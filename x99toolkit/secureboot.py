from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import lzma
import struct
import uuid

from .constants import (
    SECURE_BOOT_GUIDS,
    PATCH_TARGETS,
    LZMA_CUSTOM_DECOMPRESS_GUID,
    EFI_CERT_X509_GUID,
    EFI_CERT_SHA256_GUID,
    EFI_CERT_TYPE_PKCS7_GUID,
    CERT_STRINGS,
    BUNDLED_DONOR_SHA256,
)
from .firmware import FfsFile, find_ffs_by_guid, parse_ffs_at

@dataclass(frozen=True)
class SecureBootVar:
    name: str
    ffs: FfsFile
    decompressed: bytes
    section: dict[str, Any]
    auth: dict[str, Any]
    signature_lists: tuple[dict[str, Any], ...]
    evidence: dict[str, bool]

    @property
    def payload_sha256(self) -> str:
        return sha256(self.decompressed).hexdigest()


def _contains(blob: bytes, text: str) -> bool:
    return text.encode('ascii') in blob or text.encode('utf-16le') in blob


def _parse_esls(decompressed: bytes) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    if len(decompressed) < 4 or decompressed[3] != 0x19:
        raise ValueError('decompressed payload is not EFI_SECTION_RAW')
    raw_size = int.from_bytes(decompressed[0:3], 'little')
    if raw_size < 4 or raw_size > len(decompressed):
        raise ValueError(f'invalid EFI_SECTION_RAW size 0x{raw_size:X}')
    body = decompressed[4:raw_size]
    if len(body) < 40:
        raise ValueError('authenticated variable payload is truncated')
    cert_len = int.from_bytes(body[16:20], 'little')
    revision = int.from_bytes(body[20:22], 'little')
    cert_type = int.from_bytes(body[22:24], 'little')
    cert_guid = str(uuid.UUID(bytes_le=body[24:40])).upper()
    if cert_len < 24 or 16 + cert_len > len(body):
        raise ValueError(f'invalid WIN_CERTIFICATE length 0x{cert_len:X}')
    if cert_guid != EFI_CERT_TYPE_PKCS7_GUID:
        raise ValueError(f'unexpected authenticated-variable certificate GUID {cert_guid}')
    esl_pos = 16 + cert_len
    lists: list[dict[str, Any]] = []
    while esl_pos < len(body):
        # AMI payloads may carry alignment padding after the last list.
        if all(x in (0x00, 0xFF) for x in body[esl_pos:]):
            break
        if esl_pos + 28 > len(body):
            raise ValueError('truncated EFI_SIGNATURE_LIST header')
        sig_type = str(uuid.UUID(bytes_le=body[esl_pos:esl_pos + 16])).upper()
        list_size, header_size, sig_size = struct.unpack_from('<III', body, esl_pos + 16)
        if list_size < 28 or esl_pos + list_size > len(body):
            raise ValueError(f'invalid EFI_SIGNATURE_LIST size 0x{list_size:X}')
        if sig_size < 16:
            raise ValueError(f'invalid EFI_SIGNATURE_DATA size 0x{sig_size:X}')
        data_start = esl_pos + 28 + header_size
        if data_start > esl_pos + list_size:
            raise ValueError('signature-list header exceeds list bounds')
        sig_bytes = esl_pos + list_size - data_start
        if sig_bytes % sig_size:
            raise ValueError('signature-list payload is not a whole number of signatures')
        count = sig_bytes // sig_size
        lists.append({
            'type_guid': sig_type,
            'type': 'X509' if sig_type == EFI_CERT_X509_GUID else ('SHA256' if sig_type == EFI_CERT_SHA256_GUID else 'OTHER'),
            'list_size': list_size,
            'header_size': header_size,
            'signature_size': sig_size,
            'signature_count': count,
        })
        esl_pos += list_size
    if not lists:
        raise ValueError('authenticated variable contains no EFI_SIGNATURE_LIST')
    return {
        'raw_section_size': raw_size,
        'certificate_length': cert_len,
        'certificate_revision': revision,
        'certificate_type': cert_type,
        'certificate_guid': cert_guid,
        'signature_list_offset': 4 + esl_pos if False else 4 + 16 + cert_len,
    }, tuple(lists)


def decompress_var_ffs(ffs: FfsFile) -> tuple[bytes, dict[str, Any]]:
    section = ffs.header_size
    if len(ffs.data) < section + 24:
        raise ValueError('FFS is too small for GUID-defined section')
    section_size = int.from_bytes(ffs.data[section:section + 3], 'little')
    section_type = ffs.data[section + 3]
    section_guid = str(uuid.UUID(bytes_le=ffs.data[section + 4:section + 20])).upper()
    data_offset = int.from_bytes(ffs.data[section + 20:section + 22], 'little')
    attributes = int.from_bytes(ffs.data[section + 22:section + 24], 'little')
    if section_type != 0x02:
        raise ValueError(f'expected GUID-defined section type 0x02, got 0x{section_type:02X}')
    if section_guid != LZMA_CUSTOM_DECOMPRESS_GUID:
        raise ValueError(
            'Secure Boot variable uses a different section/compression layout '
            f'({section_guid}); this build refuses to guess'
        )
    if data_offset < 24 or section_size > len(ffs.data) - section or section + data_offset >= len(ffs.data):
        raise ValueError('invalid GUID-defined section bounds')
    payload = ffs.data[section + data_offset:section + section_size]
    decompressed = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
    if len(decompressed) < 4 or decompressed[3] != 0x19:
        raise ValueError('decompressed payload is not EFI_SECTION_RAW')
    return decompressed, {
        'section_size': section_size,
        'section_type': section_type,
        'section_guid': section_guid,
        'data_offset': data_offset,
        'attributes': attributes,
    }


def inspect_var(name: str, ffs: FfsFile) -> SecureBootVar:
    if ffs.ffs_type != 0x02:
        raise ValueError(f'{name} is FFS type 0x{ffs.ffs_type:02X}, expected Freeform 0x02')
    decompressed, section = decompress_var_ffs(ffs)
    auth, lists = _parse_esls(decompressed)
    evidence = {key: _contains(decompressed, text) for key, text in CERT_STRINGS.items()}
    return SecureBootVar(name, ffs, decompressed, section, auth, lists, evidence)


def _classify(name: str, evidence: dict[str, bool]) -> str:
    if name == 'PkVar':
        if evidence['ami_test_pk']:
            return 'AMI test PK'
        if evidence['asrock_pk']:
            return 'ASRock production PK'
        return 'Other / vendor PK'
    if name == 'KekVar':
        if evidence['kek_2023'] and evidence['kek_2011']:
            return '2011 + 2023'
        if evidence['kek_2023']:
            return '2023'
        if evidence['kek_2011']:
            return '2011 only'
        return 'Other / unknown KEK'
    if name == 'dbVar':
        complete_2023 = evidence['db_windows_2023'] and evidence['db_microsoft_2023'] and evidence['db_optionrom_2023']
        has_2011 = evidence['db_uefi_2011'] or evidence['db_windows_2011']
        if complete_2023 and has_2011:
            return '2011 + 2023'
        if complete_2023:
            return '2023'
        if any(evidence.get(key) for key in ('db_windows_2023', 'db_microsoft_2023', 'db_optionrom_2023')):
            return '2011 + partial 2023' if has_2011 else 'Partial 2023'
        if has_2011:
            return '2011 only'
        return 'Other / unknown db'
    return 'present'


def _inspect_one(data: bytes, name: str, guid: str, required: bool) -> dict[str, Any]:
    hits = find_ffs_by_guid(data, guid)
    item: dict[str, Any] = {'guid': guid, 'count': len(hits), 'required_for_patch': required}
    if len(hits) == 1:
        ffs = hits[0]
        item.update(offset=ffs.offset, size=ffs.size, ffs_type=ffs.ffs_type, state=ffs.state, ffs_sha256=ffs.sha256)
        try:
            variable = inspect_var(name, ffs)
            item.update(
                parse_ok=True,
                payload_sha256=variable.payload_sha256,
                evidence=variable.evidence,
                classification=_classify(name, variable.evidence),
                section=variable.section,
                auth=variable.auth,
                signature_lists=list(variable.signature_lists),
            )
        except Exception as exc:
            item.update(parse_ok=False, error=f'{type(exc).__name__}: {exc}')
    elif len(hits) == 0:
        item.update(parse_ok=False, error='not present')
    else:
        item.update(parse_ok=False, error=f'ambiguous: expected one FFS, found {len(hits)}')
    item['decompress_ok'] = item.get('parse_ok', False)
    return item


def inspect_secure_boot(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    variables = {
        name: _inspect_one(data, name, guid, name in PATCH_TARGETS)
        for name, guid in SECURE_BOOT_GUIDS.items()
    }
    errors: list[str] = []
    warnings: list[str] = []
    for name in PATCH_TARGETS:
        item = variables[name]
        if item['count'] != 1 or not item.get('parse_ok'):
            errors.append(f'{name}: {item.get("error", "cannot parse safely")}')
    dbx = variables['dbxVar']
    if dbx['count'] != 1 or not dbx.get('parse_ok'):
        warnings.append('dbxVar is absent/ambiguous/unparseable; it will not be touched and does not block PK/KEK/db patching')

    patchable = not errors
    pk = variables['PkVar'].get('evidence', {})
    kek = variables['KekVar'].get('evidence', {})
    db = variables['dbVar'].get('evidence', {})
    complete_2023 = bool(
        kek.get('kek_2023')
        and db.get('db_windows_2023')
        and db.get('db_microsoft_2023')
        and db.get('db_optionrom_2023')
    ) if patchable else False
    test_pk = bool(pk.get('ami_test_pk')) if patchable else False
    targets: list[str] = []
    if patchable:
        if test_pk:
            targets.append('PkVar')
        # Unknown/vendor PKs are deliberately preserved. We only replace the
        # explicitly-bad AMI test PK by default.
        if not kek.get('kek_2023'):
            targets.append('KekVar')
        if not (db.get('db_windows_2023') and db.get('db_microsoft_2023') and db.get('db_optionrom_2023')):
            targets.append('dbVar')
    return {
        'variables': variables,
        'patchable': patchable,
        'all_variables_valid': patchable,
        'has_2011_certificates': any(v.get('evidence', {}).get(key) for v in variables.values() for key in ('kek_2011','db_uefi_2011','db_windows_2011')),
        'all_patch_targets_valid': patchable,
        'has_complete_2023_set': complete_2023,
        'has_ami_test_pk': test_pk,
        'has_asrock_pk': bool(pk.get('asrock_pk')),
        'recommended_targets': targets,
        'recommended_patch_mode': '+'.join(targets) if targets else ('none' if patchable else 'cannot patch safely'),
        'errors': errors,
        'warnings': warnings,
    }


def inspect_donor_ffs(path: Path, expected_name: str | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    matches: list[tuple[str, FfsFile]] = []
    for name in PATCH_TARGETS:
        guid = SECURE_BOOT_GUIDS[name]
        try:
            ffs = parse_ffs_at(data, 0, guid)
            if ffs.size == len(data):
                matches.append((name, ffs))
        except Exception:
            pass
    if len(matches) != 1:
        raise ValueError('donor file is not one recognized standalone Secure Boot variable FFS')
    name, ffs = matches[0]
    if expected_name and name != expected_name:
        raise ValueError(f'expected {expected_name}, donor contains {name}')
    variable = inspect_var(name, ffs)
    evidence = variable.evidence
    if name == 'PkVar':
        if evidence['ami_test_pk']:
            raise ValueError('donor PkVar contains AMI test PK')
        if not evidence['asrock_pk']:
            raise ValueError('donor PkVar does not contain the validated ASRock Inc. production PK')
    elif name == 'KekVar' and not evidence['kek_2023']:
        raise ValueError('donor KekVar lacks Microsoft Corporation KEK 2K CA 2023')
    elif name == 'dbVar' and not (
        evidence['db_windows_2023'] and evidence['db_microsoft_2023'] and evidence['db_optionrom_2023']
    ):
        raise ValueError('donor dbVar lacks one or more required Microsoft 2023 certificates')
    digest = ffs.sha256
    expected_digest = BUNDLED_DONOR_SHA256.get(name)
    if expected_digest and digest != expected_digest:
        raise ValueError(f'{name} donor SHA-256 mismatch: {digest} != {expected_digest}')
    return {
        'name': name,
        'path': str(path),
        'guid': ffs.guid,
        'size': ffs.size,
        'ffs_type': ffs.ffs_type,
        'ffs_sha256': digest,
        'payload_sha256': variable.payload_sha256,
        'evidence': evidence,
        'signature_lists': list(variable.signature_lists),
    }


def load_bundled_donors(root: Path) -> dict[str, Path]:
    donor_dir = root / 'secureboot_donors'
    result: dict[str, Path] = {}
    for name in PATCH_TARGETS:
        path = donor_dir / f'{name}.ffs'
        if not path.is_file():
            raise FileNotFoundError(f'missing bundled donor: {path}')
        inspect_donor_ffs(path, name)
        result[name] = path.resolve()
    return result


def load_donors(source: Path, temp_dir: Path) -> dict[str, Path]:
    source = source.resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, Path] = {}
    if source.is_dir():
        for path in source.glob('*.ffs'):
            try:
                info = inspect_donor_ffs(path)
                if info['name'] in mapping:
                    raise ValueError(f"duplicate donor for {info['name']}")
                mapping[info['name']] = path.resolve()
            except ValueError as exc:
                if 'duplicate donor' in str(exc): raise
                continue
    elif source.is_file():
        data = source.read_bytes()
        for name, guid in SECURE_BOOT_GUIDS.items():
            if name == 'dbxVar':
                continue
            hits = find_ffs_by_guid(data, guid)
            if len(hits) == 1:
                output = temp_dir / f'{name}.ffs'
                output.write_bytes(hits[0].data)
                inspect_donor_ffs(output, name)
                mapping[name] = output
    else:
        raise FileNotFoundError(source)
    return mapping

def _requested_names(mode: str, current: dict) -> list[str]:
    if mode == 'kek_db':
        return ['KekVar', 'dbVar']
    if mode == 'pk_kek_db':
        return ['PkVar', 'KekVar', 'dbVar']
    if mode != 'auto':
        raise ValueError(f'unknown Secure Boot patch mode {mode}')
    variables = current['variables']
    requested: list[str] = []
    if current.get('has_ami_test_pk'):
        requested.append('PkVar')
    if not variables['KekVar'].get('evidence', {}).get('kek_2023'):
        requested.append('KekVar')
    db_evidence = variables['dbVar'].get('evidence', {})
    if not (db_evidence.get('db_windows_2023') and db_evidence.get('db_microsoft_2023') and db_evidence.get('db_optionrom_2023')):
        requested.append('dbVar')
    return requested

def plan_secure_boot(current: dict, donors: dict[str, Path], mode: str) -> list[dict[str, Any]]:
    if not current.get('all_variables_valid'):
        raise ValueError('Secure Boot variables cannot be parsed safely')
    plan: list[dict[str, Any]] = []
    for name in _requested_names(mode, current):
        if name not in donors:
            raise ValueError(f'missing validated donor FFS: {name}')
        donor = inspect_donor_ffs(donors[name], name)
        existing = current['variables'][name]
        if existing.get('ffs_sha256') == donor['ffs_sha256']:
            action, reason = 'SKIP', 'complete FFS is already byte-identical to donor'
        elif existing.get('payload_sha256') == donor['payload_sha256']:
            action, reason = 'SKIP', 'decompressed variable payload is already identical to donor'
        else:
            action, reason = 'PATCH', 'current variable does not match the requested donor payload'
        plan.append({'name': name, 'action': action, 'reason': reason, 'donor': str(donors[name]), 'existing_ffs_sha256': existing.get('ffs_sha256'), 'existing_payload_sha256': existing.get('payload_sha256'), 'donor_ffs_sha256': donor['ffs_sha256'], 'donor_payload_sha256': donor['payload_sha256']})
    return plan
