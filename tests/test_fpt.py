from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import tempfile

import pytest

from x99toolkit import fpt

ROOT = Path(__file__).resolve().parents[1]


def test_v0961_pins_exact_fpt_and_authoritative_fparts():
    assert fpt.FPT_VERSION == '9.1.10.1000'
    assert fpt.FPT_SHA256 == 'b7e942e903f5f6bba84c3e9294edc8cc097c1173ca249a41a4cca1ab9e15a697'
    assert fpt.FPARTS_SHA256 == '9f815e22fdc5562f0af6ac552c27e0adccf9f3654c3d76dbc4f9cc9278bc2313'
    assert (ROOT / 'fpt_support' / 'fparts.txt').is_file()
    assert fpt.sha256_file(ROOT / 'fpt_support' / 'fparts.txt') == fpt.FPARTS_SHA256


def test_external_fpt_files_are_excluded_from_source_control():
    # A developer may keep verified local utilities without breaking tests.
    exclusions = (ROOT / '.gitignore').read_text()
    for name in ('fptw64.exe', 'pmxdll32e.DLL', 'idrvdll32e.DLL'):
        assert 'tools/' + name in exclusions
    text = (ROOT / 'tools' / 'README.txt').read_text(encoding='utf-8')
    assert fpt.FPT_SOURCE_FOLDER_URL in text
    assert fpt.FPT_SHA256 in text


@pytest.mark.parametrize(
    'line,kind,percent,destructive',
    [
        ('- Reading Flash [0x1000000] 8192KB of 16384KB - 50% complete.', 'read', 50, False),
        ('- Erasing Flash Block [0x83C000] - 37% complete.', 'erase', 37, True),
        ('- Programming Flash [0x83C000] 120KB of 240KB - 50% complete.', 'program', 50, True),
        ('- Verifying Flash [0x1000000] 16384KB of 16384KB - 100% complete.', 'verify', 100, False),
    ],
)
def test_fpt_progress_records_map_to_independent_phases(line, kind, percent, destructive):
    event = fpt.parse_progress_record(line)
    assert event is not None
    assert event['kind'] == kind
    assert event['percent'] == percent
    assert event['destructive'] is destructive



def test_progress_parser_treats_addresses_as_cursor_not_identity():
    records = [
        ('- Reading Flash [0xA147C0] 10321KB of 16384KB - 63% complete.', 'read', 'Reading flash'),
        ('- Erasing Flash Block [0xB7D000] - 2% complete.', 'erase', 'Erasing flash'),
        ('- Programming Flash [0xB7BE00] 3KB of 344KB - 1% complete.', 'program', 'Programming flash'),
        ('- Verifying Flash [0xFDC5C0] 16244KB of 16384KB - 99% complete.', 'verify', 'Verifying flash'),
    ]
    for line, kind, label in records:
        event = fpt.parse_progress_record(line)
        assert event is not None
        assert event['kind'] == kind
        assert event['label'] == label
        assert 'key' not in event
        assert event['address'].startswith('0X')


def test_one_continuous_erase_pass_uses_one_stable_phase_key():
    state = {}
    events = []
    for line in [
        '- Erasing Flash Block [0xB7C000] - 1% complete.',
        '- Erasing Flash Block [0xB7D000] - 2% complete.',
        '- Erasing Flash Block [0xBA6000] - 50% complete.',
        '- Erasing Flash Block [0xBD1000] - 100% complete.',
    ]:
        events.append(fpt._sequence_progress_phase(fpt.parse_progress_record(line), state))
    assert {e['key'] for e in events} == {'erase:1'}
    assert [e['percent'] for e in events] == [1, 2, 50, 100]
    assert all(e['label'] == 'Erasing flash' for e in events)


def test_one_continuous_program_pass_uses_one_stable_phase_key():
    state = {}
    first = fpt._sequence_progress_phase(
        fpt.parse_progress_record('- Programming Flash [0xB7B040] 0KB of 344KB - 0% complete.'), state)
    last = fpt._sequence_progress_phase(
        fpt.parse_progress_record('- Programming Flash [0xBD1000] 344KB of 344KB - 100% complete.'), state)
    assert first['key'] == last['key'] == 'program:1'
    assert first['label'] == last['label'] == 'Programming flash'


def test_second_destructive_pass_gets_a_new_bar_when_percentage_resets():
    state = {}
    first = fpt._sequence_progress_phase(
        fpt.parse_progress_record('- Erasing Flash Block [0x83C000] - 100% complete.'), state)
    second = fpt._sequence_progress_phase(
        fpt.parse_progress_record('- Erasing Flash Block [0x842000] - 1% complete.'), state)
    assert first['key'] == 'erase:1'
    assert second['key'] == 'erase:2'
    assert second['label'] == 'Erasing flash (pass 2)'


def test_read_and_verify_each_use_one_stable_bar():
    state = {}
    read1 = fpt._sequence_progress_phase(fpt.parse_progress_record('- Reading Flash [0x000040] 0KB of 16384KB - 0% complete.'), state)
    read2 = fpt._sequence_progress_phase(fpt.parse_progress_record('- Reading Flash [0x1000000] 16384KB of 16384KB - 100% complete.'), state)
    verify1 = fpt._sequence_progress_phase(fpt.parse_progress_record('- Verifying Flash [0x000040] 0KB of 16384KB - 0% complete.'), state)
    verify2 = fpt._sequence_progress_phase(fpt.parse_progress_record('- Verifying Flash [0x1000000] 16384KB of 16384KB - 100% complete.'), state)
    assert read1['key'] == read2['key'] == 'read:1'
    assert verify1['key'] == verify2['key'] == 'verify:1'

def test_non_progress_fpt_line_is_not_fake_progress():
    assert fpt.parse_progress_record('FPT Operation Passed') is None
    assert fpt.parse_progress_record('RESULT: The data is identical.') is None


def test_authoritative_fparts_unconditionally_overwrites_tools_copy(monkeypatch, tmp_path):
    source = tmp_path / 'authoritative.txt'
    source.write_bytes((ROOT / 'fpt_support' / 'fparts.txt').read_bytes())
    tools = tmp_path / 'tools'; tools.mkdir()
    target = tools / 'fparts.txt'; target.write_text('stock file that must be replaced', encoding='utf-8')
    monkeypatch.setattr(fpt, 'validate_fpt_installation', lambda: {'ready': True})
    monkeypatch.setattr(fpt, 'authoritative_fparts', lambda: source)
    monkeypatch.setattr(fpt, 'discover_fpt_directory', lambda: tools)
    out = fpt.install_authoritative_fparts()
    assert out == target
    assert target.read_bytes() == source.read_bytes()
    assert fpt.sha256_file(target) == fpt.FPARTS_SHA256


def _run(success: bool, destructive: bool) -> fpt.FptRunResult:
    return fpt.FptRunResult(
        command=['fptw64.exe'],
        returncode=0 if success else 1,
        transcript=['FPT Operation Passed'] if success else ['Error 999: synthetic'],
        passed_marker=success,
        data_identical=success,
        destructive_started=destructive,
        success=success,
    )


def _backup(tmp_path: Path) -> fpt.BackupResult:
    p = tmp_path / 'backup.ROM'; p.write_bytes(b'A' * 4096)
    return fpt.BackupResult(True, str(p), fpt.sha256_file(p), p.stat().st_size, None, None, None)


def test_flash_failure_before_destructive_stage_does_not_reflash_backup(monkeypatch, tmp_path):
    target = tmp_path / 'target.ROM'; target.write_bytes(b'B' * 4096)
    monkeypatch.setattr(fpt, 'create_verified_backup', lambda cb=None: _backup(tmp_path))
    monkeypatch.setattr(fpt, 'backup_directory', lambda: tmp_path)
    calls = []
    def fake_run(args, group, callback=None, timeout=1800):
        calls.append((args, group))
        return _run(False, False)
    monkeypatch.setattr(fpt, '_run_fpt', fake_run)
    result = fpt.flash_bios_with_recovery(target)
    assert result.success is False
    assert result.recovery_attempted is False
    assert len(calls) == 1


def test_destructive_flash_failure_triggers_one_verified_backup_restore(monkeypatch, tmp_path):
    target = tmp_path / 'target.ROM'; target.write_bytes(b'B' * 4096)
    backup = _backup(tmp_path)
    monkeypatch.setattr(fpt, 'create_verified_backup', lambda cb=None: backup)
    monkeypatch.setattr(fpt, 'backup_directory', lambda: tmp_path)
    calls = []
    def fake_run(args, group, callback=None, timeout=1800):
        calls.append((list(args), group))
        return _run(False, True) if len(calls) == 1 else _run(True, True)
    monkeypatch.setattr(fpt, '_run_fpt', fake_run)
    result = fpt.flash_bios_with_recovery(target)
    assert result.success is False
    assert result.recovery_attempted is True
    assert result.recovery_success is True
    assert len(calls) == 2
    assert calls[1][0][0] == '-f'
    assert Path(calls[1][0][1]).resolve() == Path(backup.backup_path).resolve()


def test_gui_contains_dump_flash_progress_and_no_fit_wording():
    text = (ROOT / 'x99toolkit' / 'fpt_gui.py').read_text(encoding='utf-8')
    assert 'Dump BIOS…' in text
    assert 'Flash BIOS…' in text
    assert 'QProgressBar' in text
    assert 'FPT log' in text
    assert 'AUTOMATIC RECOVERY FAILED' in text
    assert 'FIT' not in text


def test_both_executable_builds_keep_normal_privileges_and_use_helper():
    spec = (ROOT / 'SenorX99.spec').read_text()
    assert 'uac_admin=False' in spec
    assert '--uac-admin' not in (ROOT / 'build_exe.bat').read_text()
    from x99toolkit.fpt_elevation import helper_command
    _, arguments = helper_command(ROOT / 'request.json')
    assert '--fpt-helper' in arguments


def test_detected_flash_capacity_is_parsed_from_fpt_device_inventory():
    transcript = [
        '    --- Flash Devices Found ---',
        '    FM25W128    ID:0xA12818    Size: 16384KB (131072Kb)',
    ]
    assert fpt.reported_flash_size_bytes(transcript) == 16 * 1024 * 1024


def test_detected_flash_capacity_sums_multiple_spi_components():
    transcript = [
        '    CHIP-A    ID:0x111111    Size: 8192KB (65536Kb)',
        '    CHIP-B    ID:0x222222    Size: 8192KB (65536Kb)',
    ]
    assert fpt.reported_flash_size_bytes(transcript) == 16 * 1024 * 1024


def test_dump_once_accepts_only_exact_detected_spi_capacity(monkeypatch, tmp_path):
    output = tmp_path / 'dump.ROM'
    def fake_run(args, group, callback=None, timeout=1800, prevalidated=False):
        output.write_bytes(b'A' * 4096)
        return fpt.FptRunResult(
            command=['fptw64.exe', *args], returncode=0,
            transcript=['CHIP ID:0x123456 Size: 4KB (32Kb)', 'FPT Operation Passed'],
            passed_marker=True, data_identical=False, destructive_started=False, success=True,
        )
    monkeypatch.setattr(fpt, '_run_fpt', fake_run)
    run = fpt._dump_once(output, 'Synthetic dump', None)
    assert run.success is True
    assert output.stat().st_size == 4096


def test_dump_once_rejects_truncated_file_even_when_fpt_reports_pass(monkeypatch, tmp_path):
    output = tmp_path / 'dump.ROM'
    def fake_run(args, group, callback=None, timeout=1800, prevalidated=False):
        output.write_bytes(b'A' * 2048)
        return fpt.FptRunResult(
            command=['fptw64.exe', *args], returncode=0,
            transcript=['CHIP ID:0x123456 Size: 4KB (32Kb)', 'FPT Operation Passed'],
            passed_marker=True, data_identical=False, destructive_started=False, success=True,
        )
    monkeypatch.setattr(fpt, '_run_fpt', fake_run)
    run = fpt._dump_once(output, 'Synthetic dump', None)
    assert run.success is False
