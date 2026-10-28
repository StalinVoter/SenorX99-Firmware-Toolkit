from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import io
import json
import shutil
import zipfile
import pytest

from x99toolkit import tool_inventory as inventory, nativeprocess, runtime_bootstrap as runtime
from x99toolkit.constants import CERT_STRINGS
from x99toolkit.secureboot import _classify, inspect_secure_boot
from validate_build_inputs import validate

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('schema,version', [(2, '0.85'), (1, 'old')])
def test_wrong_runtime_identity_rejected(tmp_path, schema, version):
    with pytest.raises(RuntimeError, match='does not match'):
        runtime.verify_files(tmp_path, dict(schema=schema, app_version=version))


def test_explicit_child_environment_is_also_sanitized(monkeypatch):
    monkeypatch.setattr(nativeprocess, 'os', SimpleNamespace(name='nt', environ={'PATH': 'private'}))
    monkeypatch.setattr(nativeprocess.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, '_original_path', 'original')
    from contextlib import nullcontext
    monkeypatch.setattr(nativeprocess, 'external_dll_search', nullcontext)
    captured = []
    monkeypatch.setattr(nativeprocess.subprocess, 'Popen', lambda *a, **kw: captured.append(kw))
    original = dict(PATH='foreign', QT_PLUGIN_PATH='foreign', QT_QPA_PLATFORM='offscreen', CUSTOM='keep')
    nativeprocess.popen(['fixture'], env=original)
    assert captured[0]['env'] == dict(PATH='original', CUSTOM='keep')
    assert original['QT_PLUGIN_PATH'] == 'foreign'


def test_partial_2023_label_does_not_claim_2011_only():
    evidence = dict.fromkeys(CERT_STRINGS, False)
    evidence.update(db_windows_2011=True, db_windows_2023=True)
    assert _classify('dbVar', evidence) == '2011 + partial 2023'


def test_partial_2023_display_preserves_full_set_update_policy(tmp_path):
    # Model the real inspected-variable evidence, keeping update planning intact.
    from x99toolkit import secureboot
    from unittest.mock import patch
    evidence = dict.fromkeys(CERT_STRINGS, False)
    evidence.update(db_windows_2011=True, db_windows_2023=True)
    def inspected(data, name, guid, required):
        return dict(count=1, parse_ok=True, evidence=(evidence if name=='dbVar' else
                    dict(ami_test_pk=False, kek_2023=True)), classification='fixture')
    rom = tmp_path / 'fixture.ROM'; rom.write_bytes(b'fixture')
    with patch.object(secureboot, '_inspect_one', inspected):
        report = inspect_secure_boot(rom)
    assert report['has_complete_2023_set'] is False
    assert 'dbVar' in report['recommended_targets']


def test_bundled_resource_checks_allow_empty_external_tools(tmp_path):
    manifest = json.loads((ROOT / 'resource-manifest.json').read_text())
    for relative in manifest['files']:
        dest = tmp_path / relative; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, dest)
    shutil.copy2(ROOT / 'resource-manifest.json', tmp_path / 'resource-manifest.json')
    assert validate(tmp_path) == manifest
    assert not (tmp_path / 'tools').exists()
    (tmp_path / 'drivers/ser8989_S3TurboHack-40-20-20.ffs').write_bytes(b'changed')
    with pytest.raises(ValueError, match='corrupt bundled resource'):
        validate(tmp_path)


@pytest.fixture
def local_tool(monkeypatch):
    data = b'local binary fixture'
    spec = inventory.ToolSpec('mmtool_a5.exe', '', sha256(data).hexdigest(), len(data), '', '', 0)
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec})
    return spec, data


@pytest.mark.parametrize('zipped', [False, True])
def test_verified_local_import_of_executable_or_tools_zip(tmp_path, local_tool, zipped):
    spec, data = local_tool
    source = tmp_path / ('tools.zip' if zipped else 'original.exe')
    if zipped:
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('old tools/' + spec.name, data)
    else:
        source.write_bytes(data)
    result = inventory.import_tool(spec.name, source, tmp_path / 'dest')
    assert result.read_bytes() == data


def test_wrong_local_import_preserves_previous_file(tmp_path, local_tool):
    spec, data = local_tool
    folder = tmp_path / 'tools'; folder.mkdir()
    destination = folder / spec.name; destination.write_bytes(b'old')
    source = tmp_path / 'wrong.exe'; source.write_bytes(b'bad')
    with pytest.raises(ValueError, match='preserved'):
        inventory.import_tool(spec.name, source, folder)
    assert destination.read_bytes() == b'old'


@pytest.mark.parametrize('members', [[], ['other.exe'], ['UEFIReplace.exe', 'UEFIReplace.exe']])
def test_missing_or_duplicate_download_archive_member_rejected(monkeypatch, tmp_path, members):
    data = b'verified tool'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name in members:
            archive.writestr(name, data)
    payload = buffer.getvalue()
    spec = inventory.ToolSpec('UEFIReplace.exe', 'fixture', sha256(data).hexdigest(), len(data),
                              'https://example.invalid/tool.zip', sha256(payload).hexdigest(), len(payload), 'UEFIReplace.exe')
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec})
    monkeypatch.setattr(inventory, '_download', lambda *a: payload)
    with pytest.raises(RuntimeError, match='exact required executable'):
        inventory.install_tool(spec.name, tmp_path / 'dest')


def test_tool_read_error_is_visible(monkeypatch, tmp_path, local_tool):
    spec, data = local_tool
    (tmp_path / spec.name).write_bytes(data)
    def denied(*a): raise PermissionError('fixture denied')
    monkeypatch.setattr(inventory, 'sha256_file', denied)
    result = inventory.tool_status(spec, tmp_path)
    assert result['state'] == 'unreadable' and 'denied' in result['detail']
