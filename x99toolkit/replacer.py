"""Contained UEFIReplace rebuilds, adapted from the canonical v0.964 logic."""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import time
from .firmware import find_ffs_by_guid, find_enclosing_fv, parse_ffs_at, sha256_file
from .tools import run_tool, validate_uefireplace
from .sessionlog import log
from .ffsverify import relocated_tbu, verify_replacement, validate_input_mapping
from .diagnostics import rebuild_evidence, event, ffs_evidence
from .fit import capture_fit_rebuild_reference, repair_fit_rebuild_damage


def _one(data: bytes, guid: str):
    hits = find_ffs_by_guid(data, guid)
    if len(hits) != 1:
        raise RuntimeError(f'expected exactly one target {guid}, found {len(hits)}')
    return hits[0]


def _contained_rebuild(input_path, output_path, guid, command, validator):
    with rebuild_evidence(input_path, output_path, guid, command):
        return _contained_rebuild_impl(input_path, output_path, guid, command, validator)


def _contained_rebuild_impl(input_path, output_path, guid, command, validator):
    if input_path.resolve() == output_path.resolve():
        raise ValueError('UEFIReplace output must differ from its input')
    data = input_path.read_bytes()
    target = _one(data, guid)
    fv = find_enclosing_fv(data, target.offset)
    if fv is None or target.offset + target.size > fv.end:
        raise RuntimeError('could not establish the complete target firmware-volume boundary')
    validate_uefireplace(Path(command[0]))
    reference = capture_fit_rebuild_reference(input_path)
    # A stale output from a previous invocation must never be accepted.
    output_path.unlink(missing_ok=True)
    started = time.monotonic()
    result = run_tool(command, cwd=input_path.parent)
    diagnostic = (f'Command: {subprocess.list2cmdline(command)}\n'
                  f'Working directory: {input_path.parent}\nElapsed: {time.monotonic()-started:.3f}s\n'
                  f'Exit code: {result.returncode}\nInput SHA-256: {sha256_file(input_path)}\n'
                  f'Output:\n{result.stdout}')
    event('External tool completed', diagnostic=diagnostic, returncode=result.returncode)
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f'UEFIReplace failed (exit {result.returncode}); see session log')
    raw = output_path.read_bytes()
    if len(raw) != len(data):
        raise RuntimeError('UEFIReplace changed image size')

    def check_boundary(buf):
        hit = _one(buf, guid)
        boundary = find_enclosing_fv(buf, hit.offset)
        if boundary is None or (boundary.offset, boundary.size, boundary.end) != (fv.offset, fv.size, fv.end):
            raise RuntimeError('UEFIReplace changed the target firmware-volume boundary')
        if hit.offset + hit.size > boundary.end:
            raise RuntimeError('rebuilt target extends outside its firmware volume')
        return hit

    event('Validate raw helper output')
    check_boundary(raw)
    validator(output_path)  # exact requested body/FFS and unchanged sibling bodies
    changes = [i for i, (a,b) in enumerate(zip(data,raw)) if a != b and not fv.offset <= i < fv.end]
    accepted = data[:fv.offset] + raw[fv.offset:fv.end] + data[fv.end:]
    output_path.write_bytes(accepted)
    check_boundary(accepted)
    validation = validator(output_path)
    if accepted[:fv.offset] != data[:fv.offset] or accepted[fv.end:] != data[fv.end:]:
        raise RuntimeError('contained rebuild changed bytes outside target FV')
    boundary = dict(offset=fv.offset, size=fv.size, end=fv.end, external_changes_restored=len(changes))
    log('UEFIREPLACE', f'{guid}: contained rebuild to target FV 0x{fv.offset:X}-0x{fv.end:X}; preserved {len(changes)} unrelated byte change(s); first raw offsets={[hex(x) for x in changes[:16]]}')
    # Separate, audited FIT-only stage; it may legitimately update the FIT outside
    # the helper's FV. It preserves any pre-existing displacement, not a cleanup.
    safeguard = repair_fit_rebuild_damage(output_path, reference)
    validator(output_path)
    log('FIT SAFEGUARD', f'{guid}: {safeguard.get("changed", False)=}')
    diagnostic += f'\nContained FV: {boundary}\nFIT rebuild safeguard: {safeguard}\n'
    return dict(diagnostic=diagnostic, validation=validation, fv_boundary=boundary, fit_safeguard=safeguard)


def replace_ffs(input_path: Path, replacement_path: Path, output_path: Path, guid: str, uefireplace: Path, validator=None, allow_tbu_relocation: bool = False) -> dict:
    if input_path.resolve() == output_path.resolve():
        raise ValueError('replacement output must differ from input')
    existing = _one(input_path.read_bytes(), guid)
    replacement_data = replacement_path.read_bytes()
    replacement = parse_ffs_at(replacement_data, 0, guid)
    if replacement.size != len(replacement_data) or existing.ffs_type != replacement.ffs_type:
        raise RuntimeError('replacement must be a complete standalone FFS of the target type')
    if existing.data == replacement_data and not allow_tbu_relocation:
        shutil.copy2(input_path, output_path)
        return dict(skipped=True, reason='complete target FFS is already byte-identical to replacement', diagnostic='UEFIReplace not invoked: target already current', old_offset=existing.offset,new_offset=existing.offset,old_size=existing.size,new_size=existing.size,subtype=f'{existing.ffs_type:02X}')
    def exact(path):
        ffs_evidence(replacement_data, None)
        image = path.read_bytes()
        ffs = _one(image, guid)
        canonical = None
        info = None
        try:
            if allow_tbu_relocation:
                validate_input_mapping(existing, input_path.stat().st_size)
                canonical, info = relocated_tbu(replacement_data, ffs.offset, len(image))
            else:
                canonical = replacement_data
                info = {'mode': 'exact-ffs'}
            ffs_evidence(replacement_data, ffs.data, canonical, info)
            result = verify_replacement(replacement_data, ffs.data, ffs.offset, len(image), allow_tbu_relocation)
            if validator:
                validator(path, replacement_path, ffs)
            event('Replacement verified', **result)
            return result
        except Exception as exc:
            ffs_evidence(replacement_data, ffs.data, canonical, {'error': str(exc), 'calculation': info})
            raise RuntimeError(str(exc) + '; rebuild evidence was recorded in the diagnostics folder') from exc
    command = [str(uefireplace),str(input_path),guid,f'{existing.ffs_type:02X}',str(replacement_path),'-o',str(output_path),'-asis']
    result = _contained_rebuild(input_path,output_path,guid,command,exact)
    final = _one(output_path.read_bytes(), guid)
    return dict(**result,skipped=False,old_offset=existing.offset,new_offset=final.offset,old_size=existing.size,new_size=final.size,subtype=f'{existing.ffs_type:02X}')


def replace_section_body(input_path: Path, body_path: Path, output_path: Path, parent_guid: str, section_type: int, uefireplace: Path, validator, failed_output_copy: Path | None = None) -> dict:
    # Every caller supplies validate_nested_body, which checks exact target body
    # and sibling bodies while allowing harmless section framing normalization.
    command=[str(uefireplace),str(input_path),parent_guid,f'{section_type:02X}',str(body_path),'-o',str(output_path)]
    try:
        result=_contained_rebuild(input_path,output_path,parent_guid,command,validator)
    except Exception:
        if failed_output_copy is not None and output_path.is_file():
            failed_output_copy.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(output_path,failed_output_copy)
        raise
    return dict(**result,section_type=f'{section_type:02X}',body_size=body_path.stat().st_size)
