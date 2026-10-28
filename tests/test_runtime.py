from __future__ import annotations
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile

import pytest

from x99toolkit import __version__, runtime_bootstrap as rt, sessionlog
from scripts import build_support as bs

ROOT = Path(__file__).resolve().parents[1]


def manifest(root, files=None, preload=None):
    files = files or {'vcruntime140.dll': b'fixture-runtime'}
    for relative, data in files.items():
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return {'schema': 1, 'app_version': __version__, 'kind': 'cli', 'versions': {},
            'files': {p: {'sha256': sha256(b).hexdigest()} for p, b in files.items()},
            'preload': preload if preload is not None else ['vcruntime140.dll']}


@pytest.fixture(autouse=True)
def reset_runtime(monkeypatch, tmp_path):
    sessionlog.finish_session_log('reset')
    monkeypatch.setenv('X99_TOOLKIT_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(rt, '_prepared', False)
    monkeypatch.setattr(rt, '_manifest', None)
    monkeypatch.setattr(rt, '_loader', None)
    monkeypatch.setattr(rt, '_directory_handles', [])
    monkeypatch.setattr(rt, '_library_handles', [])
    yield
    sessionlog.finish_session_log('test completed')


@pytest.mark.parametrize('relative', ['../x.dll', '/x.dll', 'C:/x.dll', 'a\\b.dll', ''])
def test_manifest_paths_cannot_escape_private_root(tmp_path, relative):
    with pytest.raises(RuntimeError):
        rt.checked_path(tmp_path, relative)


def test_missing_or_changed_runtime_fails_before_load(tmp_path):
    m = manifest(tmp_path)
    rt.verify_files(tmp_path, m)
    (tmp_path / 'vcruntime140.dll').write_bytes(b'older-runtime')
    with pytest.raises(RuntimeError, match='SHA-256 mismatch'):
        rt.verify_files(tmp_path, m)
    (tmp_path / 'vcruntime140.dll').unlink()
    with pytest.raises(FileNotFoundError):
        rt.verify_files(tmp_path, m)


def test_unlisted_preload_is_rejected(tmp_path):
    m = manifest(tmp_path, preload=['foreign.dll'])
    with pytest.raises(RuntimeError, match='preload'):
        rt.verify_files(tmp_path, m)


def test_loaded_foreign_copy_is_rejected_even_if_same_basename(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    m = manifest(private)
    rt.verify_loaded(private, m, [private / 'vcruntime140.dll'])
    with pytest.raises(RuntimeError, match='foreign runtime'):
        rt.verify_loaded(private, m, [tmp_path / 'System32' / 'vcruntime140.dll'])


def test_absent_preloaded_module_and_unexpected_qt_version_are_rejected(tmp_path):
    m = manifest(tmp_path)
    with pytest.raises(RuntimeError, match='not present'):
        rt.verify_loaded(tmp_path, m, [])
    with pytest.raises(RuntimeError, match='Unexpected native'):
        rt.verify_loaded(tmp_path, m, [tmp_path / 'Qt6Unexpected.dll'])


def test_private_loads_and_search_restrictions_precede_qt_import(monkeypatch, tmp_path):
    m = manifest(tmp_path)
    (tmp_path / 'runtime-manifest.json').write_text(json.dumps(m))
    events = []
    class Loader:
        def restrict(self, root): events.append('restrict')
        def preload(self, path): events.append('preload')
        def modules(self): return [tmp_path / 'vcruntime140.dll']
    monkeypatch.setattr(rt.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(rt.sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(rt.sys, 'platform', 'win32')
    monkeypatch.setattr(rt, 'WindowsLoader', Loader)
    monkeypatch.setattr(rt.os, 'add_dll_directory', lambda path: object(), raising=False)
    monkeypatch.setattr(rt, 'configure_qt', lambda: events.append('configure-qt'))
    def load_module(name, package):
        events.append('import')
        assert rt._prepared
        return SimpleNamespace(run_gui=lambda: 0)
    monkeypatch.setattr(rt.importlib, 'import_module', load_module)
    assert rt.run('gui') == 0
    assert events == ['restrict', 'preload', 'import', 'configure-qt']


def test_import_failure_is_in_the_session_log(monkeypatch, tmp_path):
    def broken_import(*args): raise ImportError('test missing Qt procedure')
    monkeypatch.setattr(rt.importlib, 'import_module', broken_import)
    monkeypatch.setattr(rt.sys, 'platform', 'linux')
    assert rt.run('gui') == 2
    logs = list((tmp_path / 'home' / 'LOGS').glob('Session-*.log'))
    assert len(logs) == 1
    text = logs[0].read_text()
    assert text.index('Starting before importing Qt') < text.index('test missing Qt procedure')
    assert 'Traceback' in text


def test_cli_selftest_never_imports_qt_or_firmware_tools(monkeypatch, tmp_path):
    def forbidden(*args): pytest.fail('Self-test must not import the GUI or launch firmware tools')
    monkeypatch.setattr(rt.importlib, 'import_module', forbidden)
    output = tmp_path / 'selftest.json'
    monkeypatch.setattr(rt.sys, 'argv', ['patcher', '--runtime-self-test', '--runtime-report', str(output)])
    assert rt.run('cli') == 0
    assert json.loads(output.read_text())['success'] is True


def test_native_preload_order_respects_dependency_graph(monkeypatch, tmp_path):
    files = {'Qt6Core.dll': tmp_path / 'qt', 'vcruntime140.dll': tmp_path / 'vc'}
    lookup = {'qt6core.dll': 'Qt6Core.dll', 'vcruntime140.dll': 'vcruntime140.dll'}
    monkeypatch.setattr(bs, 'native_info', lambda path: {'imports': {'vcruntime140.dll': []} if path.endswith('qt') else {}, 'exports': set()})
    assert bs.preload_order(files, lookup, ['Qt6Core.dll']) == ['vcruntime140.dll', 'Qt6Core.dll']


def test_binary_pair_missing_export_is_build_error(monkeypatch, tmp_path):
    consumer = tmp_path / 'qt.dll'; provider = tmp_path / 'vc.dll'
    consumer.write_bytes(b'consumer'); provider.write_bytes(b'provider')
    def info(path):
        return {'imports': {'vcruntime140.dll': [b'new_function']}, 'exports': set()} if path == str(consumer) else {'imports': {}, 'exports': {b'old_function'}}
    monkeypatch.setattr(bs, 'native_info', info)
    with pytest.raises(RuntimeError, match='missing exports'):
        bs.validate_imports({'Qt6Core.dll': consumer, 'vcruntime140.dll': provider})


def test_unbundled_microsoft_runtime_is_build_error(monkeypatch, tmp_path):
    consumer = tmp_path / 'qt.dll'; consumer.write_bytes(b'consumer')
    monkeypatch.setattr(bs, 'native_info', lambda path: {'imports': {'msvcp140_2.dll': []}, 'exports': set()})
    with pytest.raises(RuntimeError, match='unbundled private DLL'):
        bs.validate_imports({'Qt6Core.dll': consumer})


def test_different_private_copies_of_same_dll_are_build_error(tmp_path):
    a = tmp_path / 'a'; a.write_bytes(b'old')
    b = tmp_path / 'b'; b.write_bytes(b'new')
    with pytest.raises(RuntimeError, match='Conflicting private DLL'):
        bs.validate_imports({'x/Qt6Core.dll': a, 'y/Qt6Core.dll': b})


def test_os_ucrt_is_not_mistaken_for_private_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll'):
        (runtime / name).write_bytes(b'pinned runtime fixture')
    os_names = ('ucrtbase.dll', 'msvcp_win.dll', 'api-ms-win-crt-runtime-l1-1-0.dll', 'ext-ms-win-ntuser-window-l1-1-0.dll')
    entries = [('PySide6\\' + name, str(tmp_path / name), 'BINARY') for name in os_names]
    monkeypatch.setattr(bs, 'native_info', lambda path: {'imports': {'ucrtbase.dll': []}, 'exports': set()})
    sealed, info = bs.seal_collection(entries, runtime, 'cli', {})
    assert all(name not in relative for relative in info['files'] for name in os_names)
    assert set(info['files']) == {p.name for p in runtime.iterdir()}
    root = tmp_path / 'private'
    root.mkdir()
    for relative in info['files']:
        (root / relative).write_bytes(b'pinned runtime fixture')
    rt.verify_loaded(root, info, [root / relative for relative in info['files']] + [tmp_path / 'Windows' / 'System32' / 'ucrtbase.dll'])


def test_external_tool_search_directory_is_restored_on_spawn_failure(monkeypatch):
    from x99toolkit import nativeprocess
    events = []
    class Kernel:
        def SetDllDirectoryW(self, path): events.append(path); return True
    monkeypatch.setattr(rt, '_loader', SimpleNamespace(kernel=Kernel()))
    monkeypatch.setattr(rt, '_original_path', 'original-windows-path')
    monkeypatch.setattr(nativeprocess, 'os', SimpleNamespace(name='nt', environ={'PATH': 'qt-path', 'QT_PLUGIN_PATH': 'qt'}))
    monkeypatch.setattr(nativeprocess.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(nativeprocess.sys, '_MEIPASS', 'private-runtime', raising=False)
    def fail(*args, **kwargs):
        assert kwargs['env'] == {'PATH': 'original-windows-path'}
        raise OSError('spawn failed')
    monkeypatch.setattr(nativeprocess.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='spawn failed'):
        nativeprocess.popen(['fptw64.exe', '-i'])
    assert events == [None, 'private-runtime']


def test_real_windows_qt_and_python_dependency_exports():
    corpus = Path(os.environ.get('X99_RUNTIME_NATIVE_CORPUS', '/nonexistent'))
    if not corpus.is_dir(): pytest.skip('optional downloaded Windows native corpus not present')
    names = {'qtcore.pyd', 'qtgui.pyd', 'qtwidgets.pyd', 'qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll', 'qwindows.dll', 'pyside6.abi3.dll', 'shiboken6.abi3.dll'}
    binaries = [(str(p.relative_to(corpus)), str(p), 'BINARY') for p in corpus.rglob('*') if p.is_file() and (p.name.lower() in names or p.parent == corpus)]
    sealed, m = bs.seal_collection(binaries, corpus / 'shiboken6', 'gui', {})
    assert m['preload'].index('vcruntime140.dll') < m['preload'].index('PySide6/Qt6Core.dll')
    assert 'PySide6/plugins/platforms/qwindows.dll' in m['preload']
    assert all('/msvcp140' not in dest.lower() and '/vcruntime140' not in dest.lower() for dest, _, _ in sealed)
    assert len(bs.native_info(str(corpus / 'PySide6' / 'Qt6Gui.dll'))['exports']) > 8192


def test_finished_exe_audit_detects_tampered_archive_bytes(tmp_path):
    from PyInstaller.archive.writers import CArchiveWriter
    from scripts.verify_exe import verify
    native = tmp_path / 'core.dll'; native.write_bytes(b'original')
    info = {'files': {'core.dll': {'sha256': sha256(b'original').hexdigest()}}}
    data = tmp_path / 'manifest.json'; data.write_text(json.dumps(info))
    output = tmp_path / 'test.pkg'
    CArchiveWriter(str(output), [('core.dll', str(native), True, 'b'), ('runtime-manifest.json', str(data), True, 'x')], 'python313.dll')
    assert verify(output) == info
    native.write_bytes(b'different')
    CArchiveWriter(str(output), [('core.dll', str(native), True, 'b'), ('runtime-manifest.json', str(data), True, 'x')], 'python313.dll')
    with pytest.raises(RuntimeError, match='DLL hash mismatch'):
        verify(output)


@pytest.mark.parametrize('component', ['qt', 'pyside', 'shiboken'])
def test_mismatched_qt_binding_or_runtime_version_fails(monkeypatch, tmp_path, component):
    expected = '6.10.3'
    modules = {
        'PySide6.QtCore': SimpleNamespace(qVersion=lambda: 'wrong' if component == 'qt' else expected),
        'PySide6': SimpleNamespace(__version__='wrong' if component == 'pyside' else expected),
        'shiboken6': SimpleNamespace(__version__='wrong' if component == 'shiboken' else expected),
    }
    monkeypatch.setattr(rt, '_manifest', {'versions': {'PySide6': expected, 'shiboken6': expected}})
    monkeypatch.setattr(rt.importlib, 'import_module', lambda name: modules[name])
    with pytest.raises(RuntimeError, match='version'):
        rt.configure_qt()


def test_non_x64_private_image_is_rejected_and_closed(monkeypatch):
    closed = []
    image = SimpleNamespace(FILE_HEADER=SimpleNamespace(Machine=0x14c), close=lambda: closed.append(True))
    monkeypatch.setattr(bs.pefile, 'PE', lambda *a, **kw: image)
    with pytest.raises(RuntimeError, match='Non-x64'):
        bs.native_info('wrong-architecture-fixture.dll')
    assert closed == [True]
