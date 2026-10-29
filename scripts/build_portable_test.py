"""Build a private Windows test ZIP offline, without a GitHub upload.

This is a separate packaging route from package_release.py: it records static
validation and does not claim that native Windows runtime/UAC tests have passed.
Requires an x64 MinGW compiler, the pinned Python embed ZIP and locked wheels.
"""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from x99toolkit import __version__
from scripts.build_support import native_info, validate_imports, preload_order, MSVC
from validate_build_inputs import validate

EMBED_SHA256 = '90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907'


def extract(archive, destination, select=lambda name: True):
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            if entry.is_dir() or not select(entry.filename): continue
            relative = Path(entry.filename)
            if relative.is_absolute() or '..' in relative.parts: raise ValueError('Unsafe archive path')
            target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(entry))


def build(embed, wheels, gcc, windres):
    if sha256(embed.read_bytes()).hexdigest() != EMBED_SHA256: raise ValueError('Pinned Python embed hash mismatch')
    locked = set(re.findall(r'--hash=sha256:([a-f0-9]{64})', (ROOT/'requirements-build.lock').read_text()))
    wanted = ('pyside6_essentials-', 'shiboken6-', 'pillow-')
    wheel_paths = []
    for prefix in wanted:
        matches = [p for p in wheels.glob('*.whl') if p.name.lower().startswith(prefix)]
        if len(matches) != 1: raise ValueError('Exactly one Windows wheel required: ' + prefix)
        path = matches[0]
        if 'win_amd64' not in path.name or sha256(path.read_bytes()).hexdigest() not in locked:
            raise ValueError('Windows wheel does not match the source lock: ' + path.name)
        wheel_paths.append(path)
    destination = ROOT/'build'/'private-windows-test'
    if destination.exists(): shutil.rmtree(destination)
    destination.mkdir(parents=True); runtime = destination/'_runtime'; runtime.mkdir()
    extract(embed, runtime, lambda name: not name.lower().endswith('.exe'))
    (runtime/'python313._pth').write_text('python313.zip\n.\napp.zip\n', encoding='ascii')
    licenses = destination/'licenses'; shutil.copytree(ROOT/'licenses', licenses)
    shutil.copy2(runtime/'LICENSE.txt', licenses/'Python-LICENSE.txt')
    for path in wheel_paths:
        extract(path, runtime, lambda name: (
            (name.startswith('PIL/') and '__pycache__' not in name) or
            (name.startswith('shiboken6/') and name.lower().endswith(('.py','.pyd','.dll'))) or
            (name.startswith('PySide6/') and (name.endswith('.py') or
             name in ('PySide6/QtCore.pyd','PySide6/QtGui.pyd','PySide6/QtWidgets.pyd','PySide6/QtTest.pyd',
                      'PySide6/Qt6Core.dll','PySide6/Qt6Gui.dll','PySide6/Qt6Widgets.dll','PySide6/Qt6Test.dll',
                      'PySide6/pyside6.abi3.dll','PySide6/plugins/platforms/qwindows.dll',
                      'PySide6/plugins/imageformats/qjpeg.dll','PySide6/plugins/imageformats/qgif.dll')))))
        extract(path, licenses/path.stem, lambda name: 'licens' in name.lower() or name.endswith('/METADATA'))
    # A single coherent MSVC runtime from the locked Shiboken wheel.
    for path in (runtime/'shiboken6').glob('*.dll'):
        if MSVC.fullmatch(path.name): shutil.move(str(path), runtime/path.name)
    # Replace the embed runtime with that same pinned source.
    with zipfile.ZipFile(wheel_paths[1]) as wheel:
        for name in wheel.namelist():
            if name.startswith('shiboken6/') and MSVC.fullmatch(Path(name).name):
                (runtime/Path(name).name).write_bytes(wheel.read(name))
    with zipfile.ZipFile(runtime/'app.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT/'x99toolkit').glob('*.py')):
            archive.write(path, 'x99toolkit/' + path.name)
        archive.write(ROOT/'validate_build_inputs.py', 'validate_build_inputs.py')
    # Compile two small native launchers with asInvoker and high-DPI manifests.
    obj = ROOT/'build'/'launcher-resource.o'
    rc = ROOT/'build'/'launcher.rc'
    manifest_path = str(ROOT/'native/launcher.manifest').replace('\\','/')
    rc.write_text('1 24 "' + manifest_path + '"\n', encoding='ascii')
    subprocess.run([str(windres), str(rc), str(obj)], check=True)
    for kind in ('gui','cli'):
        name = 'SenorX99' + ('-CLI' if kind=='cli' else '') + '.exe'
        command = [str(gcc), '-Os', '-s', '-nostdlib', '-fno-stack-protector', '-fno-builtin',
            '-Wl,--entry,entry', '-Wl,--subsystem,'+('console' if kind=='cli' else 'windows'),
            '-Wl,--dynamicbase', '-Wl,--nxcompat', '-Wl,--no-insert-timestamp',
            str(ROOT/'native/launcher.c'),str(obj),'-o',str(destination/name),'-lkernel32','-luser32','-lshell32']
        if kind=='cli':command.insert(1,'-DSENOR_CLI')
        subprocess.run(command, check=True)
        native_info(str(destination/name))
    resources = validate(ROOT)
    for relative in resources['files']:
        target = destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,target)
    shutil.copy2(ROOT/'resource-manifest.json',destination/'resource-manifest.json')
    shutil.copy2(ROOT/'languages/README.md',destination/'languages/README.md')
    shutil.copy2(ROOT/'README.md',destination/'README.txt')
    for name in ('START-HERE.md','RELEASE-NOTES-v0.93.md','BUILD-VALIDATION.md','TEST-ELEVATED-HELPER.cmd'):
        shutil.copy2(ROOT/name,destination/name)
    (destination/'tools').mkdir();shutil.copy2(ROOT/'tools/README.txt',destination/'tools/README.txt')
    # Validate all Windows private import/export relationships before packaging.
    native = {str(p.relative_to(runtime)).replace('\\','/'):p for p in runtime.rglob('*') if p.suffix.lower() in ('.dll','.pyd')}
    by_name = validate_imports(native)
    required_exports = ('Py_Initialize','Py_SetProgramName','PySys_SetArgvEx','PyRun_SimpleString',
                        'PyImport_AddModule','PyModule_GetDict','PyDict_GetItemString','PyLong_AsLong','Py_FinalizeEx')
    exports = native_info(str(runtime/'python313.dll'))['exports']
    if any(name.encode() not in exports for name in required_exports):raise ValueError('Required Python embedding API is missing')
    files = {str(p.relative_to(runtime)).replace('\\','/'):{'sha256':sha256(p.read_bytes()).hexdigest()}
             for p in sorted(runtime.rglob('*')) if p.is_file()}
    for kind in ('gui','cli'):
        roots = [relative for name,relative in by_name.items() if MSVC.fullmatch(name)]
        if kind=='gui':roots += [by_name[name] for name in ('qtcore.pyd','qtgui.pyd','qtwidgets.pyd','qwindows.dll')]
        manifest = dict(schema=1,app_version=__version__,kind=kind,versions={'Python':'3.13.14','PySide6':'6.10.3','shiboken6':'6.10.3'},
                        files=files,preload=preload_order(native,by_name,roots))
        (runtime/f'runtime-manifest-{kind}.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    validation = dict(version=__version__,packaging='offline native Windows x64 launcher + private CPython',
        embed_sha256=EMBED_SHA256,wheels={p.name:sha256(p.read_bytes()).hexdigest() for p in wheel_paths},
        native_import_exports_verified=True,native_windows_execution_verified=False,
        native_files=len(native),launchers={p.name:sha256(p.read_bytes()).hexdigest() for p in destination.glob('*.exe')})
    (destination/'build-validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    # An ordinary compatible ZIP, published only after its directory is closed.
    archive = ROOT/f'SenorX99-v{__version__}-Windows-x64.zip'
    temporary = archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=False) as output:
        for p in sorted(destination.rglob('*')):
            if p.is_file():output.write(p,str(Path(archive.stem)/p.relative_to(destination)))
    with zipfile.ZipFile(temporary) as output:
        if output.testzip() is not None:raise ValueError('ZIP CRC verification failed')
    os.replace(temporary,archive)
    archive.with_suffix('.zip.sha256').write_text(sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(archive)
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('embed','wheels','gcc','windres'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();build(args.embed,args.wheels,args.gcc,args.windres)
