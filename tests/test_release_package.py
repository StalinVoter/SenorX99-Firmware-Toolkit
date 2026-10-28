from pathlib import Path
from hashlib import sha256
import json
import zipfile
import pytest
from scripts import package_release, verify_exe


def fixture_release(root: Path):
    (root / 'build').mkdir()
    dist = root / 'dist'; dist.mkdir()
    (root / 'tools').mkdir()
    (root / 'tools' / 'README.txt').write_text('tool instructions')
    (root / 'TEST-ELEVATED-HELPER.cmd').write_text('diagnostic fixture')
    (dist / 'README.txt').write_text('runtime guide')
    for folder in ('secureboot_donors', 'fpt_support', 'licenses', 'tools'):
        (dist / folder).mkdir()
        (dist / folder / 'fixture.txt').write_text('fixture')
    for name in ('PkVar.ffs', 'KekVar.ffs', 'dbVar.ffs'):
        (dist / 'secureboot_donors' / name).write_bytes(b'donor fixture')
    (dist / 'fpt_support' / 'fparts.txt').write_text('parts fixture')
    actual = Path(__file__).resolve().parents[1]
    import shutil
    (root / 'languages').mkdir()
    shutil.copy2(actual / 'languages/README.md', root / 'languages/README.md')
    manifest = json.loads((actual / 'resource-manifest.json').read_text())
    for relative in manifest['files']:
        target = dist / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(actual / relative, target)
    shutil.copy2(actual / 'resource-manifest.json', dist / 'resource-manifest.json')
    for name in ('START-HERE.md', 'RELEASE-NOTES-v0.91.md'):
        (dist / name).write_text('documentation fixture')
    cases = []
    for kind in ('gui', 'cli', 'conflicting-dlls'):
        name = 'SenorX99' + ('-CLI' if kind == 'cli' else '') + '.exe'
        (dist / name).write_bytes(name.encode())
        report = {'success': True, 'version': '0.91', 'platform': 'windows', 'drag_drop_verified': True,
                  'exe_sha256': sha256((dist / name).read_bytes()).hexdigest()}
        (root / 'build' / f'smoke-{kind}.json').write_text(json.dumps(report))
        cases.append({'case': kind, 'success': True})
    cases.append({'case': 'helper-transport', 'success': True})
    (root / 'build' / 'smoke-helper-transport.json').write_text(json.dumps({
        'version': '0.91', 'success': True, 'hardware_access': False, 'result': {'transport_verified': True,
            'helper_exe_sha256': sha256((dist / 'SenorX99-CLI.exe').read_bytes()).hexdigest()},
        'exe_sha256': sha256((dist / 'SenorX99.exe').read_bytes()).hexdigest()}))
    # PowerShell 5.1 reports have a UTF-8 BOM.
    (root / 'build' / 'windows-runtime-tests.json').write_text(json.dumps({'success': True, 'cases': cases}), encoding='utf-8-sig')
    for kind in ('gui', 'cli'):
        (root / 'build' / f'verified-{kind}.json').write_text('{}')
    for name in ('fptw64.exe', 'UEFIReplace.exe', 'pmxdll32e.DLL', 'idrvdll32e.DLL', 'mmtool_a5.exe', 'ChangeLogo.exe'):
        (root / 'tools' / name).write_bytes(b'private external tool')
        (dist / 'tools' / name).write_bytes(b'private external tool')


def test_public_zip_excludes_external_tools_and_keeps_documentation(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    archive = package_release.package(tmp_path)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    assert any(n.endswith('/tools/README.txt') for n in names)
    assert any(n.endswith('/README.txt') and '/tools/' not in n for n in names)
    assert any('/fpt_support/' in n for n in names)
    for name in ('fptw64.exe', 'UEFIReplace.exe', 'pmxdll32e.DLL', 'idrvdll32e.DLL', 'mmtool_a5.exe', 'ChangeLogo.exe'):
        assert not any(n.endswith('/' + name) for n in names)
        assert (tmp_path / 'tools' / name).is_file()
        assert (tmp_path / 'dist' / 'tools' / name).exists()


def test_public_zip_is_blocked_if_exe_changed_after_selftest(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    (tmp_path / 'dist' / 'SenorX99.exe').write_bytes(b'new untested build')
    with pytest.raises(RuntimeError, match='different executable'):
        package_release.package(tmp_path)


def test_public_zip_is_blocked_without_successful_conflict_test(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    (tmp_path / 'build' / 'smoke-conflicting-dlls.json').write_text(json.dumps({'success': False}))
    with pytest.raises(RuntimeError, match='conflicting-DLL test'):
        package_release.package(tmp_path)


def test_public_zip_is_blocked_without_packaged_drop_test(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    path = tmp_path / 'build' / 'smoke-gui.json'
    report = json.loads(path.read_text())
    report.pop('drag_drop_verified')
    path.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match='file-drop test'):
        package_release.package(tmp_path)


def test_public_zip_requires_helper_test_for_the_exact_cli(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    path = tmp_path / 'build' / 'smoke-helper-transport.json'
    report = json.loads(path.read_text())
    report['result']['helper_exe_sha256'] = 'different executable'
    path.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match='helper exchange'):
        package_release.package(tmp_path)


def test_public_zip_keeps_full_toolkit_assets(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    archive = package_release.package(tmp_path)
    required = json.loads((tmp_path / 'dist/resource-manifest.json').read_text())['files']
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    assert all(any(n.endswith('/' + relative) for n in names) for relative in required)


def test_packaging_rejects_missing_or_corrupt_firmware_assets(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {})
    (tmp_path / 'dist/assets/SenorSpace-XD3-4bit.bmp').write_bytes(b'changed')
    with pytest.raises(ValueError, match='corrupt bundled resource'):
        package_release.package(tmp_path)


def test_stale_finished_exe_report_cannot_be_packaged(monkeypatch, tmp_path):
    fixture_release(tmp_path)
    monkeypatch.setattr(verify_exe, 'verify', lambda p: {'new': 'manifest'})
    with pytest.raises(RuntimeError, match='finished-EXE report is stale'):
        package_release.package(tmp_path)
