from dataclasses import replace
from hashlib import sha256
import io
from pathlib import Path
import pytest
from x99toolkit import tool_inventory as inventory


@pytest.fixture
def fpt_group(monkeypatch):
    data = {name: (name + ' fixture bytes').encode() for name in inventory.FPT_FILES}
    specs = tuple(inventory.ToolSpec(name, 'fixture', sha256(content).hexdigest(), len(content),
                                    'https://example.invalid/' + name, sha256(content).hexdigest(), len(content))
                  for name, content in data.items())
    group = inventory.ToolGroup('fpt', 'Intel FPT', tuple(data))
    monkeypatch.setattr(inventory, 'TOOL_SPECS', specs)
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec for spec in specs})
    monkeypatch.setattr(inventory, 'TOOL_GROUPS', (group,))
    monkeypatch.setattr(inventory, 'GROUPS_BY_NAME', {'fpt': group})
    return data


def serve(monkeypatch, data, calls, fail=None):
    class Response(io.BytesIO):
        def geturl(self): return 'https://example.invalid/pinned'
    def open_file(request, **kwargs):
        name = request.full_url.rsplit('/', 1)[-1]
        calls.append(name)
        return Response(b'bad' if name == fail else data[name])
    monkeypatch.setattr(inventory, 'urlopen', open_file)


def test_one_fpt_action_installs_all_three_verified_files(monkeypatch, tmp_path, fpt_group):
    calls = []
    serve(monkeypatch, fpt_group, calls)
    progress = []
    paths = inventory.install_tool_group('fpt', tmp_path, progress.append)
    assert len(paths) == 3
    assert set(calls) == set(inventory.FPT_FILES)
    assert all(path.read_bytes() == fpt_group[path.name] for path in paths)
    assert inventory.scan_tool_groups(tmp_path)[0]['ready'] is True
    assert all(any(name in message for message in progress) for name in inventory.FPT_FILES)


def test_fpt_action_repairs_only_incorrect_or_missing_members(monkeypatch, tmp_path, fpt_group):
    first = inventory.FPT_FILES[0]
    (tmp_path / first).write_bytes(fpt_group[first])
    (tmp_path / inventory.FPT_FILES[1]).write_bytes(b'wrong')
    calls = []; serve(monkeypatch, fpt_group, calls)
    inventory.install_tool_group('fpt', tmp_path)
    assert calls == list(inventory.FPT_FILES[1:])
    inventory.install_tool_group('fpt', tmp_path)
    assert calls == list(inventory.FPT_FILES[1:])  # Ready group makes no network requests.


def test_partial_fpt_download_failure_can_retry_without_redownloading_valid_members(monkeypatch, tmp_path, fpt_group):
    last = inventory.FPT_FILES[-1]
    (tmp_path / last).write_bytes(b'previous file')
    calls = []; serve(monkeypatch, fpt_group, calls, fail=last)
    with pytest.raises(RuntimeError, match='exact size/SHA-256'):
        inventory.install_tool_group('fpt', tmp_path)
    assert (tmp_path / last).read_bytes() == b'previous file'
    assert inventory.scan_tool_groups(tmp_path)[0]['ready'] is False
    assert all((tmp_path / name).read_bytes() == fpt_group[name] for name in inventory.FPT_FILES[:-1])
    serve(monkeypatch, fpt_group, calls)
    inventory.install_tool_group('fpt', tmp_path)
    assert calls == list(inventory.FPT_FILES) + [last]
    assert inventory.scan_tool_groups(tmp_path)[0]['ready'] is True


def test_every_required_file_has_an_automatic_download():
    assert len(inventory.TOOL_GROUPS) == 4
    assert set(inventory.GROUPS_BY_NAME['fpt'].files) == set(inventory.FPT_FILES)
    assert all(spec.url.startswith('https://') and spec.download_sha256 and spec.download_size > 0
               for spec in inventory.TOOL_SPECS)


def prepared_spec():
    source = b'0123456789'
    installed = b'0x234567y9'
    spec = inventory.ToolSpec('mmtool_a5.exe', '', sha256(installed).hexdigest(), len(installed),
                              'https://example.invalid/source', sha256(source).hexdigest(), len(source),
                              byte_patches=((1, ord('1'), ord('x')), (8, ord('8'), ord('y'))))
    return spec, source, installed


def test_prepared_download_must_match_both_source_and_installed_pins(monkeypatch, tmp_path):
    spec, source, expected = prepared_spec()
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec})
    monkeypatch.setattr(inventory, '_download', lambda *a: source)
    result = inventory.install_tool(spec.name, tmp_path)
    assert result.read_bytes() == expected


@pytest.mark.parametrize('failure', ['changed-source', 'wrong-original-byte', 'wrong-final-pin'])
def test_prepared_download_failure_preserves_previous_file(monkeypatch, tmp_path, failure):
    spec, source, expected = prepared_spec()
    if failure == 'changed-source':
        source = b'changed data'
    elif failure == 'wrong-original-byte':
        spec = replace(spec, byte_patches=((1, ord('!'), ord('x')),))
    else:
        spec = replace(spec, sha256='0' * 64)
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec})
    monkeypatch.setattr(inventory, '_download', lambda *a: source)
    destination = tmp_path / spec.name; destination.write_bytes(b'previous file')
    with pytest.raises((ValueError, RuntimeError)):
        inventory.install_tool(spec.name, tmp_path)
    assert destination.read_bytes() == b'previous file'
