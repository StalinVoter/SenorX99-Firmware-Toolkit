from __future__ import annotations
from dataclasses import dataclass
from math import trunc
from pathlib import Path
from .constants import TBU_TARGET_GUID, SER8989_MARKER_ASCII, SER8989_MARKER_UTF16LE, UV_FIELD_OFFSETS
from .firmware import parse_ffs_at, find_ffs_by_guid, scan_06f2_microcode
from .cstates import inspect_all_cstates

@dataclass(frozen=True)
class DriverInfo:
    path: Path
    size: int
    undervolt_mv: dict[str, int | None]
    ffs_type: int

def decode_raw_uv(raw: int) -> int | None:
    signed = raw - 0x10000 if raw & 0x8000 else raw
    if signed == 0:
        return 0
    if signed % 32:
        return None
    return trunc((signed / 32) * 1000 / 1024)

def decode_fields(data: bytes) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for name, offset in UV_FIELD_OFFSETS.items():
        result[name] = decode_raw_uv(int.from_bytes(data[offset:offset + 2], 'little')) if offset + 2 <= len(data) else None
    return result

def inspect_driver(path: Path) -> DriverInfo:
    data = path.read_bytes()
    ffs = parse_ffs_at(data, 0, TBU_TARGET_GUID)
    if ffs.size != len(data):
        raise ValueError('FFS header size does not equal driver file size')
    if SER8989_MARKER_ASCII not in data and SER8989_MARKER_UTF16LE not in data:
        raise ValueError('ser8989 marker not found')
    undervolt = decode_fields(data)
    if any(value is None for value in undervolt.values()):
        raise ValueError('undervolt fields do not match the known encoding')
    return DriverInfo(path, len(data), undervolt, ffs.ffs_type)

def inspect_tbu(path: Path) -> dict:
    data = path.read_bytes()
    targets = find_ffs_by_guid(data, TBU_TARGET_GUID)
    microcode = scan_06f2_microcode(data)
    cstates = inspect_all_cstates(path)
    result = {'target_count': len(targets), 'microcode_06f2_count': len(microcode), 'cstates': cstates, 'applied': False, 'status': 'UNKNOWN'}
    if len(targets) != 1:
        result.update(error=f'expected one target, found {len(targets)}', status='INVALID')
        return result
    ffs = targets[0]
    marker = SER8989_MARKER_ASCII in ffs.data or SER8989_MARKER_UTF16LE in ffs.data
    undervolt = decode_fields(ffs.data) if marker else {'Core': None, 'Cache': None, 'System Agent': None}
    result.update(target_offset=ffs.offset, target_size=ffs.size, target_type=ffs.ffs_type, target_sha256=ffs.sha256, marker_present=marker, undervolt_mv=undervolt)
    clean_uv = marker and all(value is not None for value in undervolt.values())
    if clean_uv and not microcode and cstates.get('matches'):
        result.update(applied=True, status='PASS', summary=f"TBU applied: {undervolt['Core']}/{undervolt['Cache']}/{undervolt['System Agent']} mV")
    elif marker:
        problems: list[str] = []
        if microcode:
            problems.append('06F2 microcode still present')
        if not cstates.get('matches'):
            failed = [name for name in ('primary_intelsetup','nv_internal_default','amitse_setup_default') if not cstates.get(name, {}).get('matches')]
            problems.append('C-state defaults incomplete: ' + ', '.join(failed))
        if not clean_uv:
            problems.append('undervolt fields invalid')
        result.update(status='PARTIAL', summary='ser8989 payload present, but ' + '; '.join(problems))
    else:
        result.update(status='NOT_APPLIED', summary='ser8989 payload not present at target')
    return result
