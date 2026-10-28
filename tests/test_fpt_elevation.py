from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from x99toolkit import fpt, fpt_elevation as elevation


def test_source_helper_command_uses_cli_entrypoint(tmp_path):
    request = tmp_path / 'request with spaces.json'
    executable, args = elevation.helper_command(request)
    assert executable.is_file()
    assert args[0].endswith('SenorX99-CLI.py')
    assert args[1:] == ['--fpt-helper', str(request)]


def test_frozen_helper_command_requires_companion_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(elevation.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(elevation, 'app_directory', lambda: tmp_path)
    with pytest.raises(FileNotFoundError, match='CLI.exe'):
        elevation.helper_command(tmp_path / 'request.json')
    cli = tmp_path / 'SenorX99-CLI.exe'; cli.touch()
    executable, args = elevation.helper_command(tmp_path / 'request.json')
    assert executable == cli
    assert args[0] == '--fpt-helper'


def test_helper_transport_reassembles_events_and_preserves_backup_location(monkeypatch, tmp_path):
    backup = tmp_path / 'original user Documents' / 'BIOS Backups'
    monkeypatch.setattr(fpt, 'backup_directory', lambda: backup)
    received = []
    def fake_wait(executable, args, poll):
        request = Path(args[-1]); body = json.loads(request.read_text())
        assert body['backup_directory'] == str(backup)
        events = request.parent / 'events.jsonl'
        assert (request.parent / 'result.json').is_file()
        events.write_text('{"type":"transcript","text":"first')
        poll()
        assert not received
        with events.open('a') as stream:
            stream.write(' line"}\n{"type":"status","text":"done"}\n')
        poll()
        (request.parent / 'result.json').write_text(json.dumps({'completed': True, 'result': {'ready': True}}))
        return 0
    monkeypatch.setattr(elevation, '_wait_elevated', fake_wait)
    assert elevation.run_elevated('probe', callback=received.append) == {'ready': True}
    assert [event['text'] for event in received] == ['first line', 'done']


def test_cancelled_elevation_is_reported_without_local_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(fpt, 'validate_fpt_installation', lambda: {'ready': True})
    monkeypatch.setattr(fpt, 'needs_elevation', lambda: True)
    def cancel(*a, **k):
        raise elevation.BiosHelperError('elevation-cancelled', 'Administrator permission was cancelled')
    monkeypatch.setattr(fpt, 'run_elevated', cancel)
    monkeypatch.setattr(fpt, '_run_fpt', lambda *a, **k: pytest.fail('local hardware access'))
    assert fpt.probe_fpt()['reason'] == 'elevation-cancelled'
    assert not fpt.dump_bios(tmp_path / 'dump.ROM').success
    monkeypatch.setattr(fpt, '_write_flash_report', lambda result: None)
    result = fpt.flash_bios_with_recovery(tmp_path / 'target.ROM')
    assert not result.success and not result.recovery_attempted


def test_helper_dispatches_complete_flash_workflow_and_streams_events(monkeypatch, tmp_path):
    monkeypatch.setattr(elevation, 'os', SimpleNamespace(name='nt', environ={}, fsync=lambda fd: None))
    monkeypatch.setattr(elevation, 'needs_elevation', lambda: False)
    target = tmp_path / 'target.ROM'
    backup = tmp_path / 'user backups'
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'action': 'flash', 'path': str(target), 'backup_directory': str(backup)}))
    (tmp_path / 'result.json').touch()
    (tmp_path / 'events.jsonl').touch()
    expected = fpt.FlashResult(True, str(target), str(backup / 'original.ROM'), 'hash', {}, False, None, None, {}, None)
    def flash(path, callback):
        assert path == target
        assert elevation.os.environ['X99_TOOLKIT_BACKUP_DIR'] == str(backup)
        callback({'type': 'status', 'text': 'fixture flash workflow'})
        return expected
    monkeypatch.setattr(fpt, 'flash_bios_with_recovery', flash)
    assert elevation.helper_main(request) == 0
    result = json.loads((tmp_path / 'result.json').read_text())
    assert result['result'] == asdict(expected)
    assert json.loads((tmp_path / 'events.jsonl').read_text())['text'] == 'fixture flash workflow'


def test_cleanup_failure_preserves_success_and_original_error(monkeypatch, tmp_path):
    def denied(path):
        raise PermissionError('cleanup denied fixture')
    monkeypatch.setattr(elevation.shutil, 'rmtree', denied)
    messages = []
    monkeypatch.setattr(elevation, 'log_exception', lambda category, exc, *args: messages.append((category, str(exc))))
    with elevation.exchange_directory():
        pass
    with pytest.raises(ValueError, match='original operation error'):
        with elevation.exchange_directory():
            raise ValueError('original operation error')
    assert len(messages) == 2
    assert all(category == 'BIOS HELPER CLEANUP' for category, _ in messages)


@pytest.mark.parametrize('text,code,reason', [
    ('', 3, 'helper-result-missing'),
    ('{broken', 0, 'helper-result-invalid'),
    ('[]', 0, 'helper-result-invalid'),
    ('{"completed": true}', 0, 'helper-result-invalid'),
    ('{"completed": false, "error": "fixture operation failed"}', 2, 'helper-operation-failed'),
    ('{"completed": true, "result": {}}', 2, 'helper-operation-failed'),
])
def test_result_failures_are_classified(tmp_path, text, code, reason):
    path = tmp_path / 'result.json'; path.write_text(text)
    with pytest.raises(elevation.BiosHelperError) as error:
        elevation.read_result(path, code)
    assert error.value.reason == reason


def test_inaccessible_result_has_traceback_and_distinct_reason(monkeypatch, tmp_path):
    def denied(*args, **kwargs):
        raise PermissionError('result read denied fixture')
    monkeypatch.setattr(Path, 'read_text', denied)
    messages = []
    monkeypatch.setattr(elevation, 'log_exception', lambda *args: messages.append(args))
    with pytest.raises(elevation.BiosHelperError) as error:
        elevation.read_result(tmp_path / 'result.json', 0)
    assert error.value.reason == 'helper-result-access'
    assert isinstance(error.value.__cause__, PermissionError)
    assert messages[0][0] == 'BIOS HELPER RESULT'


def test_missing_reply_is_distinct_from_denied_access(tmp_path):
    with pytest.raises(elevation.BiosHelperError) as error:
        elevation.read_result(tmp_path / 'missing.json', 3)
    assert error.value.reason == 'helper-result-missing'


def test_invalid_reply_encoding_is_reported_as_invalid_result(tmp_path):
    path = tmp_path / 'result.json'; path.write_bytes(b'\xff')
    with pytest.raises(elevation.BiosHelperError) as error:
        elevation.read_result(path, 0)
    assert error.value.reason == 'helper-result-invalid'


def test_probe_preserves_helper_exchange_error(monkeypatch):
    monkeypatch.setattr(fpt, 'validate_fpt_installation', lambda: {'ready': True})
    monkeypatch.setattr(fpt, 'needs_elevation', lambda: True)
    def fail(*args, **kwargs):
        raise elevation.BiosHelperError('helper-result-access', 'Cannot read result.json')
    monkeypatch.setattr(fpt, 'run_elevated', fail)
    result = fpt.probe_fpt()
    assert result['reason'] == 'helper-result-access'
    assert 'result.json' in result['message']


def test_helper_writes_existing_result_without_replacement(monkeypatch, tmp_path):
    monkeypatch.setattr(elevation, 'needs_elevation', lambda: False)
    monkeypatch.setattr(elevation, 'os', SimpleNamespace(name='nt', environ={}, fsync=lambda fd: None, getpid=lambda: 123))
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'action': 'check-transport', 'backup_directory': str(tmp_path)}))
    (tmp_path / 'events.jsonl').touch()
    reply = tmp_path / 'result.json'; reply.write_text('old contents' * 1000)
    identity = reply.stat().st_ino
    monkeypatch.setattr(Path, 'replace', lambda *args: pytest.fail('must preserve reply file identity'))
    assert elevation.helper_main(request) == 0
    assert reply.stat().st_ino == identity
    assert json.loads(reply.read_text())['result']['transport_verified'] is True
    assert not (tmp_path / 'result.tmp').exists()


@pytest.mark.parametrize('driver_error', ['-2146762484', '0x800B010C'])
def test_revoked_driver_is_not_reported_as_missing_admin_rights(monkeypatch, driver_error):
    monkeypatch.setattr(fpt, 'needs_elevation', lambda: False)
    monkeypatch.setattr(fpt, 'validate_fpt_installation', lambda: {'ready': True})
    monkeypatch.setattr(fpt, 'memory_integrity_running', lambda: False)
    run = fpt.FptRunResult([], 1, [f'Failed to start loaded service err: {driver_error}', 'Error 284'], False, False, False, False)
    monkeypatch.setattr(fpt, '_run_fpt', lambda *args, **kwargs: run)
    result = fpt.probe_fpt()
    assert result['reason'] == 'driver-certificate-revoked'
    assert result['ready'] is False
    assert '0x800B010C' in result['message']
