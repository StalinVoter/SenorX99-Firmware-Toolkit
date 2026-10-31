"""Create a tool-free release ZIP only after both real EXE self-tests pass."""
from pathlib import Path
from hashlib import sha256
import json
import shutil
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]


def package(root: Path = ROOT) -> Path:
    sys.path.insert(0, str(ROOT))
    from scripts.verify_exe import verify
    from validate_build_inputs import validate
    from x99toolkit import __version__
    overall = json.loads((root / 'build' / 'windows-runtime-tests.json').read_text(encoding='utf-8-sig'))
    if any(c.get('success') is not True for c in overall.get('cases', [])) or overall.get('success') is not True or {c['case'] for c in overall.get('cases', [])} != {'gui', 'cli', 'conflicting-dlls', 'helper-transport'}:
        raise RuntimeError('The actual EXE and conflicting-DLL tests have not all passed.')
    for kind in ('gui', 'cli'):
        report = json.loads((root / 'build' / f'smoke-{kind}.json').read_text(encoding='utf-8'))
        if report.get('success') is not True or report.get('version') != __version__:
            raise RuntimeError(f'{kind} packaged self-test has not passed.')
        name = 'SenorX99' + ('-CLI' if kind == 'cli' else '') + '.exe'
        if report.get('exe_sha256') != sha256((root / 'dist' / name).read_bytes()).hexdigest():
            raise RuntimeError(f'{kind} self-test is for a different executable.')
        if kind == 'gui' and report.get('platform') != 'windows':
            raise RuntimeError('The actual Windows Qt platform has not passed.')
        if kind == 'gui' and report.get('drag_drop_verified') is not True:
            raise RuntimeError('The packaged GUI file-drop test has not passed.')
        verified = verify(root / 'dist' / name)
        recorded = json.loads((root / 'build' / f'verified-{kind}.json').read_text(encoding='utf-8-sig'))
        if recorded != verified:
            raise RuntimeError(f'{kind} finished-EXE report is stale or does not match the actual executable.')
    poison = json.loads((root / 'build' / 'smoke-conflicting-dlls.json').read_text(encoding='utf-8'))
    if poison.get('version') != __version__ or poison.get('platform') != 'windows' or poison.get('success') is not True or poison.get('drag_drop_verified') is not True or poison.get('exe_sha256') != sha256((root / 'dist' / 'SenorX99.exe').read_bytes()).hexdigest():
        raise RuntimeError('The conflicting-DLL test did not pass for this GUI executable.')
    helper = json.loads((root / 'build' / 'smoke-helper-transport.json').read_text(encoding='utf-8'))
    if (helper.get('version') != __version__ or helper.get('success') is not True or helper.get('hardware_access') is not False
            or helper.get('result', {}).get('transport_verified') is not True
            or helper.get('result', {}).get('helper_exe_sha256') != sha256((root / 'dist' / 'SenorX99-CLI.exe').read_bytes()).hexdigest()
            or helper.get('exe_sha256') != sha256((root / 'dist' / 'SenorX99.exe').read_bytes()).hexdigest()):
        raise RuntimeError('The packaged helper exchange has not passed for this GUI executable.')
    resources = validate(root / 'dist')
    stage = root / 'build' / 'release-staging'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in ('SenorX99.exe', 'SenorX99-CLI.exe', 'README.txt'):
        shutil.copy2(root / 'dist' / name, stage / name)
    shutil.copy2(root / 'TEST-ELEVATED-HELPER.cmd', stage / 'TEST-ELEVATED-HELPER.cmd')
    (stage / 'languages').mkdir(exist_ok=True)
    shutil.copy2(root / 'languages/README.md', stage / 'languages/README.md')
    for relative in resources['files']:
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / 'dist' / relative, target)
    for name in ('resource-manifest.json', 'START-HERE.md', 'RELEASE-NOTES-v0.931.md'):
        shutil.copy2(root / 'dist' / name, stage / name)
    (stage / 'tools').mkdir()
    shutil.copy2(root / 'tools' / 'README.txt', stage / 'tools' / 'README.txt')
    shutil.copytree(root / 'dist' / 'licenses', stage / 'licenses')
    (stage / 'runtime-info').mkdir()
    for kind in ('gui', 'cli'):
        shutil.copy2(root / 'build' / f'verified-{kind}.json', stage / 'runtime-info' / f'{kind}.json')
    archive = root / f'SenorX99-v{__version__}-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                output.write(path, str(Path(f'SenorX99-v{__version__}-Windows-x64') / path.relative_to(stage)))
    archive.with_suffix('.zip.sha256').write_text(sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n', encoding='utf-8')
    return archive


if __name__ == '__main__':
    print(package())
