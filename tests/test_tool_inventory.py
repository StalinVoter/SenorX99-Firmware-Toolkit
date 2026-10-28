from dataclasses import replace
from hashlib import sha256
import io
from pathlib import Path
from urllib.error import URLError
import zipfile

import pytest

from x99toolkit import tool_inventory as inventory, fpt, tools


@pytest.fixture
def tool(monkeypatch):
    data = b'exact required binary fixture'
    digest = sha256(data).hexdigest()
    spec = inventory.ToolSpec('pmxdll32e.DLL', 'test', digest, len(data),
                              'https://example.invalid/pinned-file', digest, len(data))
    monkeypatch.setattr(inventory, 'TOOL_SPECS', (spec,))
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec})
    return spec, data


class Response(io.BytesIO):
    def geturl(self):
        return 'https://example.invalid/pinned-file'


def serve(monkeypatch, data):
    monkeypatch.setattr(inventory, 'urlopen', lambda *a, **kw: Response(data))


def test_empty_folder_is_reported_without_creating_files(tmp_path):
    folder = tmp_path / 'absent'
    states = inventory.scan_tools(folder)
    assert len(states) == 6
    assert all(item['state'] == 'missing' for item in states)
    assert not folder.exists()


def test_exact_file_is_ready_but_same_size_wrong_bytes_are_rejected(tmp_path, tool):
    spec, data = tool
    path = tmp_path / spec.name
    path.write_bytes(data)
    assert inventory.scan_tools(tmp_path)[0]['state'] == 'ready'
    path.write_bytes(b'X' * len(data))
    assert inventory.scan_tools(tmp_path)[0]['state'] == 'wrong'


def test_directory_instead_of_tool_is_reported(tmp_path, tool):
    spec, data = tool
    (tmp_path / spec.name).mkdir()
    assert inventory.scan_tools(tmp_path)[0]['state'] == 'unreadable'


@pytest.mark.parametrize('existing', [None, b'wrong old version'])
def test_verified_download_installs_missing_or_replaces_wrong_file(monkeypatch, tmp_path, tool, existing):
    spec, data = tool
    if existing:
        (tmp_path / spec.name).write_bytes(existing)
    serve(monkeypatch, data)
    statuses = []
    path = inventory.install_tool(spec.name, tmp_path, statuses.append)
    assert path.read_bytes() == data
    assert inventory.scan_tools(tmp_path)[0]['state'] == 'ready'
    assert not list(tmp_path.glob('.x99-download-*'))
    assert statuses[-1] == 'Installed and verified'


def test_ready_file_does_not_download(monkeypatch, tmp_path, tool):
    spec, data = tool
    (tmp_path / spec.name).write_bytes(data)
    monkeypatch.setattr(inventory, 'urlopen', lambda *a, **k: pytest.fail('network used for ready file'))
    assert inventory.install_tool(spec.name, tmp_path).read_bytes() == data


@pytest.mark.parametrize('response', [b'', b'HTML error', b'X' * 28, b'X' * 1000])
def test_bad_download_keeps_existing_file(monkeypatch, tmp_path, tool, response):
    spec, data = tool
    old = b'old file'
    (tmp_path / spec.name).write_bytes(old)
    serve(monkeypatch, response)
    with pytest.raises(RuntimeError):
        inventory.install_tool(spec.name, tmp_path)
    assert (tmp_path / spec.name).read_bytes() == old
    assert not list(tmp_path.glob('.x99-download-*'))


def test_network_error_keeps_existing_file(monkeypatch, tmp_path, tool):
    spec, data = tool
    (tmp_path / spec.name).write_bytes(b'old file')
    def fail(*a, **kw):
        raise URLError('offline')
    monkeypatch.setattr(inventory, 'urlopen', fail)
    with pytest.raises(URLError):
        inventory.install_tool(spec.name, tmp_path)
    assert (tmp_path / spec.name).read_bytes() == b'old file'


def test_failed_atomic_replace_keeps_old_file_and_cleans_temporary(monkeypatch, tmp_path, tool):
    spec, data = tool
    (tmp_path / spec.name).write_bytes(b'old file')
    serve(monkeypatch, data)
    def fail(*args):
        raise PermissionError('destination is locked')
    monkeypatch.setattr(inventory.os, 'replace', fail)
    with pytest.raises(PermissionError):
        inventory.install_tool(spec.name, tmp_path)
    assert (tmp_path / spec.name).read_bytes() == b'old file'
    assert not list(tmp_path.glob('.x99-download-*'))


@pytest.mark.parametrize('member', ['UEFIReplace.exe', '../UEFIReplace.exe'])
def test_archive_installs_only_exact_named_member(monkeypatch, tmp_path, member):
    data = b'verified executable fixture'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(member, data)
    payload = buffer.getvalue()
    spec = inventory.ToolSpec('UEFIReplace.exe', 'test', sha256(data).hexdigest(), len(data),
                              'https://example.invalid/archive.zip', sha256(payload).hexdigest(),
                              len(payload), 'UEFIReplace.exe')
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec})
    serve(monkeypatch, payload)
    if member == spec.name:
        assert inventory.install_tool(spec.name, tmp_path).read_bytes() == data
    else:
        with pytest.raises(RuntimeError, match='exact required executable'):
            inventory.install_tool(spec.name, tmp_path)
        assert not (tmp_path / spec.name).exists()


def test_fpt_rejects_wrong_companion_dll_before_probe(monkeypatch, tmp_path):
    folder = tmp_path / 'tools'; folder.mkdir()
    data = {name: name.encode() for name in fpt.FPT_REQUIRED_FILES}
    for name, content in data.items():
        (folder / name).write_bytes(content)
    pins = {name: sha256(content).hexdigest() for name, content in data.items()}
    monkeypatch.setattr(fpt, 'app_directory', lambda: tmp_path)
    monkeypatch.setattr(fpt, 'FPT_SHA256', pins['fptw64.exe'])
    monkeypatch.setattr(fpt, 'FPT_FILE_SHA256', pins)
    (folder / 'pmxdll32e.DLL').write_bytes(b'wrong DLL')
    monkeypatch.setattr(fpt, 'memory_integrity_running', lambda: pytest.fail('unnecessary Windows query'))
    monkeypatch.setattr(fpt, '_run_fpt', lambda *a, **k: pytest.fail('incompatible DLL executed'))
    status = fpt.probe_fpt()
    assert status['ready'] is False
    assert status['reason'] == 'wrong-fpt-dependency'
    assert 'pmxdll32e.DLL' in status['message']


def test_uefireplace_must_be_in_tools_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, 'app_directory', lambda: tmp_path)
    (tmp_path / 'UEFIReplace.exe').write_bytes(b'wrong location')
    assert tools.discover(('UEFIReplace.exe',)) is None
    folder = tmp_path / 'tools'; folder.mkdir()
    (folder / 'UEFIReplace.exe').write_bytes(b'tool')
    assert tools.discover(('UEFIReplace.exe',)) is None  # Present but unverified bytes cannot execute.
