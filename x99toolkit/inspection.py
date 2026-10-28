from __future__ import annotations
from pathlib import Path
from .sessionlog import log_json
from .firmware import sha256_file
from .ser8989 import inspect_tbu
from .secureboot import inspect_secure_boot
from .fit import inspect_fit
from .optimized import inspect_optimized_settings
from .overclock import inspect_overclock_compatibility
from .models import InspectionReport
from .reporting import public_data


def inspect_rom(path: Path) -> InspectionReport:
    path = path.resolve()
    errors: list[str] = []
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        tbu = inspect_tbu(path)
    except Exception as exc:
        tbu = {'status': 'ERROR', 'applied': False, 'error': f'{type(exc).__name__}: {exc}'}
        errors.append(tbu['error'])
    try:
        secure_boot = inspect_secure_boot(path)
    except Exception as exc:
        secure_boot = {'error': f'{type(exc).__name__}: {exc}', 'variables': {}}
        errors.append(secure_boot['error'])
    try:
        fit = inspect_fit(path)
    except Exception as exc:
        fit = {'status': 'INVALID', 'supported': False, 'repairable': False, 'error': f'{type(exc).__name__}: {exc}', 'type1_entries': []}
        errors.append(fit['error'])
    try:
        optimized = inspect_optimized_settings(path)
    except Exception as exc:
        optimized = {'supported': False, 'matches': False, 'error': f'{type(exc).__name__}: {exc}', 'settings': []}
        errors.append(optimized['error'])
    try:
        overclock = inspect_overclock_compatibility(path)
    except Exception as exc:
        overclock = {'supported': False, 'matches': False, 'error': f'{type(exc).__name__}: {exc}', 'settings': [], 'access': []}
        errors.append(overclock['error'])

    rows: list[dict[str, str]] = []
    rows.append({'category': 'TBU', 'check': 'Overall', 'result': tbu.get('status', 'UNKNOWN'), 'details': tbu.get('summary', tbu.get('error', ''))})
    rows.append({'category': 'TBU', 'check': 'Undervolt', 'result': 'FOUND' if tbu.get('marker_present') else 'N/A', 'details': str(tbu.get('undervolt_mv'))})
    rows.append({'category': 'TBU', 'check': '06F2 microcode', 'result': 'ABSENT' if tbu.get('microcode_06f2_count') == 0 else 'PRESENT', 'details': str(tbu.get('microcode_06f2_count'))})
    cstates = tbu.get('cstates', {})
    for name, label in (
        ('primary_intelsetup', 'Primary IntelSetup C-states'),
        ('nv_internal_default', 'NV-Internal C-state defaults'),
        ('amitse_setup_default', 'AMITSESetupData C-state default'),
    ):
        item = cstates.get(name, {})
        rows.append({'category': 'TBU', 'check': label, 'result': 'PASS' if item.get('matches') else 'FAIL', 'details': str(item.get('values', item.get('value', item.get('error', ''))))})

    fit_status = fit.get('status', 'INVALID')
    fit_details = ''
    if fit_status == 'STALE' and fit.get('repairable'):
        fit_details = f"{fit.get('type1_count', 0)} Type-1 entries; unique repair delta {fit.get('common_delta_hex')}"
    elif fit_status == 'PASS':
        fit_details = f"{fit.get('type1_count', 0)} Type-1 entries; all point to valid microcode headers"
    else:
        fit_details = '; '.join(fit.get('errors', [])) or fit.get('error', '')
    rows.append({'category': 'FIT', 'check': 'Overall', 'result': fit_status, 'details': fit_details})
    rows.append({
        'category': 'FIT',
        'check': 'Pointer / header',
        'result': 'PASS' if fit.get('supported') and fit.get('pointer_address') else 'FAIL',
        'details': (
            f"pointer={fit.get('pointer_address_hex', '-')} entries={fit.get('entry_count', '-')} "
            f"version=0x{fit.get('header_version', 0):04X} checksum="
            + ('PASS' if fit.get('header_checksum_valid') is True else 'not-required' if fit.get('header_checksum_valid') is None else 'FAIL')
        ) if fit.get('supported') else fit.get('error', ''),
    })
    for item in fit.get('type1_entries', []):
        result = item.get('status', 'UNKNOWN')
        if result == 'STALE' and item.get('repairable'):
            details = f"{item['address_hex']} -> {item['proposed_address_hex']} ({item.get('delta', 0):+#x}); {item.get('proposed_signature_hex')} rev {item.get('proposed_revision_hex')}"
        elif result == 'PASS':
            details = f"{item['address_hex']} -> {item.get('signature_hex')} rev {item.get('revision_hex')}"
        else:
            details = f"address={item.get('address_hex')} target={item.get('target')}"
        rows.append({'category': 'FIT', 'check': f"Type-1 entry #{item.get('index')}", 'result': result, 'details': details})


    opt_result = 'PASS' if optimized.get('matches') else 'NOT APPLIED' if optimized.get('supported') else 'UNSUPPORTED'
    opt_details = optimized.get('error', '')
    if optimized.get('supported'):
        matched = sum(1 for item in optimized.get('settings', []) if item.get('matches'))
        total = len(optimized.get('settings', []))
        opt_details = f"{matched}/{total} named defaults optimized; AMITSE layout={optimized.get('layout')}"
    rows.append({'category': 'Optimized BIOS', 'check': 'Overall', 'result': opt_result, 'details': opt_details})
    for item in optimized.get('settings', []):
        rows.append({
            'category': 'Optimized BIOS',
            'check': item['name'],
            'result': 'PASS' if item.get('matches') else 'STOCK',
            'details': f"offset={item['offset']} value={item['value']} target={item['desired']} ({item['desired_label']})",
        })

    oc_result = 'PASS' if overclock.get('matches') else 'NOT APPLIED' if overclock.get('supported') else 'UNSUPPORTED'
    rows.append({
        'category': 'Multiplier OC',
        'check': 'Compatibility defaults and access',
        'result': oc_result,
        'details': overclock.get('error', f"family={overclock.get('family')}; layout={overclock.get('layout')}"),
    })
    for item in overclock.get('settings', []):
        rows.append({
            'category': 'Multiplier OC',
            'check': item['name'],
            'result': 'PASS' if item.get('matches') else 'STOCK',
            'details': f"offset={item['offset']} value={item['value']} target={item['desired']} ({item['desired_label']})",
        })
    for item in overclock.get('access', []):
        rows.append({
            'category': 'Multiplier OC',
            'check': item['name'],
            'result': 'PASS' if item.get('matches') else 'STOCK',
            'details': f"AMITSE offset=0x{item['offset']:X} value=0x{item['value']:02X} target=0x{item['desired']:02X}",
        })

    for name, item in secure_boot.get('variables', {}).items():
        if name == 'dbxVar':
            continue
        rows.append({'category': 'Secure Boot', 'check': name, 'result': item.get('classification', 'ERROR'), 'details': f"offset={hex(item['offset']) if 'offset' in item else '-'} size={hex(item['size']) if 'size' in item else '-'}"})
    rows.append({'category': 'Secure Boot', 'check': '2011 certificates', 'result': 'PRESENT' if secure_boot.get('has_2011_certificates') else 'ABSENT', 'details': ''})
    rows.append({'category': 'Secure Boot', 'check': 'Complete 2023 set', 'result': 'PRESENT' if secure_boot.get('has_complete_2023_set') else 'ABSENT', 'details': ''})
    rows.append({'category': 'Secure Boot', 'check': 'AMI test PK', 'result': 'PRESENT' if secure_boot.get('has_ami_test_pk') else 'ABSENT', 'details': ''})
    rows.append({'category': 'Secure Boot', 'check': 'Recommended patch', 'result': secure_boot.get('recommended_patch_mode', 'unknown'), 'details': ''})
    report = InspectionReport(rom_path=str(path), rom_size=path.stat().st_size, rom_sha256=sha256_file(path), tbu=tbu, secure_boot=secure_boot, fit=fit, optimized=optimized, overclock=overclock, rows=rows, errors=errors)

    log_json("ROM", "ROM inspection result", public_data(report.to_dict()))
    return report
