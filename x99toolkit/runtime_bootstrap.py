"""Qt-free startup and verification of the native files inside a frozen EXE.

This module is also invoked by the first, custom PyInstaller runtime hook.
No Qt import is allowed before prepare_runtime() finishes.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import traceback
from hashlib import sha256

from . import __version__
from .sessionlog import start_session_log, install_exception_hook, log, finish_session_log

_prepared = False
_manifest: dict | None = None
_loader = None
_directory_handles: list = []
_library_handles: list = []
_original_path = os.environ.get('PATH', '')


def checked_path(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if not relative or rel.is_absolute() or '..' in rel.parts or '\\' in relative or ':' in relative:
        raise RuntimeError(f'Invalid runtime manifest path: {relative!r}')
    path = root.joinpath(*rel.parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError(f'Runtime file escapes the private directory: {relative}')
    return path


def verify_files(root: Path, manifest: dict) -> None:
    if manifest.get('schema') != 1 or manifest.get('app_version') != __version__:
        raise RuntimeError('The bundled runtime manifest does not match this application.')
    if not manifest.get('files') or not isinstance(manifest.get('preload'), list):
        raise RuntimeError('The bundled runtime manifest is incomplete.')
    for relative, expected in manifest['files'].items():
        path = checked_path(root, relative)
        actual = sha256(path.read_bytes()).hexdigest()
        if actual != expected['sha256']:
            raise RuntimeError(f'Bundled runtime SHA-256 mismatch: {relative}')
        log('RUNTIME FILE', f'{relative}; SHA-256={actual}')
    for relative in manifest['preload']:
        if relative not in manifest['files'] or not relative.lower().endswith('.dll'):
            raise RuntimeError(f'Invalid runtime preload entry: {relative}')


class WindowsLoader:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.SetDefaultDllDirectories.argtypes = [wintypes.DWORD]
        self.kernel.SetDefaultDllDirectories.restype = wintypes.BOOL
        self.kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        self.kernel.SetDllDirectoryW.restype = wintypes.BOOL
        self.kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
        self.kernel.LoadLibraryExW.restype = wintypes.HMODULE
        self.kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        self.kernel.GetModuleFileNameW.restype = wintypes.DWORD
        self.kernel.K32EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.K32EnumProcessModules.restype = wintypes.BOOL
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE

    def restrict(self, root: Path) -> None:
        # USER_DIRS + SYSTEM32: exclude current directory, PATH and EXE directory.
        if not self.kernel.SetDefaultDllDirectories(0xC00):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        if not self.kernel.SetDllDirectoryW(str(root)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def preload(self, path: Path) -> None:
        # DLL_LOAD_DIR + USER_DIRS + SYSTEM32; never the launch directory/PATH.
        handle = self.kernel.LoadLibraryExW(str(path), None, 0xD00)
        if not handle:
            code = self.ctypes.get_last_error()
            raise OSError(code, f'Cannot load private runtime {path}: {self.ctypes.FormatError(code)}')
        _library_handles.append(handle)

    def modules(self) -> list[Path]:
        from ctypes import wintypes
        ctypes = self.ctypes
        count = 256
        while True:
            modules = (wintypes.HMODULE * count)()
            needed = wintypes.DWORD()
            if not self.kernel.K32EnumProcessModules(self.kernel.GetCurrentProcess(), modules, ctypes.sizeof(modules), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            if needed.value <= ctypes.sizeof(modules):
                break
            count = needed.value // ctypes.sizeof(wintypes.HMODULE) + 32
        paths = []
        for handle in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            name = ctypes.create_unicode_buffer(32768)
            if not self.kernel.GetModuleFileNameW(handle, name, len(name)):
                raise ctypes.WinError(ctypes.get_last_error())
            paths.append(Path(name.value).resolve())
        return paths


def verify_loaded(root: Path, manifest: dict, paths: list[Path]) -> None:
    expected: dict[str, set[Path]] = {}
    for relative in manifest['files']:
        path = checked_path(root, relative)
        expected.setdefault(path.name.casefold(), set()).add(path)
    observed = set()
    for path in paths:
        key = path.name.casefold()
        if key not in expected:
            # Also reject unexpected versions of protected DLL families.
            if key.startswith(('qt6', 'shiboken6', 'pyside6', 'msvcp140', 'vcruntime140', 'concrt140', 'vcamp140', 'vccorlib140', 'vcomp140')) and key.endswith(('.dll', '.pyd')):
                raise RuntimeError(f'Unexpected native runtime module loaded: {path}')
            continue
        if path.resolve() not in expected[key]:
            raise RuntimeError(f'A foreign runtime DLL was loaded: {path}')
        observed.add(path.resolve())
        log('RUNTIME LOADED', str(path))
    for relative in manifest['preload']:
        if checked_path(root, relative) not in observed:
            raise RuntimeError(f'Preloaded runtime module is not present: {relative}')


def audit_loaded() -> None:
    if _manifest is not None and _loader is not None:
        verify_loaded(Path(sys._MEIPASS), _manifest, _loader.modules())


def configure_qt() -> None:
    if _manifest is None:
        return
    core = importlib.import_module('PySide6.QtCore')
    pyside = importlib.import_module('PySide6')
    shiboken = importlib.import_module('shiboken6')
    for actual, expected in ((core.qVersion(), _manifest['versions']['PySide6']),
                             (pyside.__version__, _manifest['versions']['PySide6']),
                             (shiboken.__version__, _manifest['versions']['shiboken6'])):
        if actual != expected:
            raise RuntimeError(f'Loaded Qt binding/runtime version {actual}, expected {expected}')
    core.QCoreApplication.setLibraryPaths([str(Path(sys._MEIPASS) / 'PySide6' / 'plugins')])


def prepare_runtime() -> None:
    global _prepared, _manifest, _loader
    if _prepared:
        return
    start_session_log(__version__)
    install_exception_hook()
    log('STARTUP', 'Starting before importing Qt')
    if not getattr(sys, 'frozen', False):
        log('RUNTIME', 'Source execution: frozen-bundle enforcement does not apply')
        _prepared = True
        return
    if sys.platform != 'win32':
        raise RuntimeError('This distribution requires 64-bit Windows.')
    root = Path(sys._MEIPASS).resolve()
    manifest = json.loads((root / 'runtime-manifest.json').read_text(encoding='utf-8'))
    verify_files(root, manifest)
    log('RUNTIME', json.dumps(manifest['versions'], sort_keys=True))
    loader = WindowsLoader()
    loader.restrict(root)
    # All directories are private and verified. Duplicate private DLL basenames
    # with different bytes are rejected by the build, before an EXE is produced.
    directories = {root} | {checked_path(root, p).parent for p in manifest['files']}
    for folder in sorted(directories):
        _directory_handles.append(os.add_dll_directory(str(folder)))
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM', 'QT_QPA_PLATFORMTHEME'):
        os.environ.pop(key, None)
    if manifest['kind'] == 'gui':
        plugins = root / 'PySide6' / 'plugins'
        os.environ['QT_PLUGIN_PATH'] = str(plugins)
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(plugins / 'platforms')
    # Set restrictions before Qt/PyInstaller's Qt runtime hooks execute.
    for relative in manifest['preload']:
        loader.preload(checked_path(root, relative))
    verify_loaded(root, manifest, loader.modules())
    _manifest, _loader = manifest, loader
    _prepared = True
    log('RUNTIME', 'Private runtime files and loaded paths verified')


def fail_startup(exc: BaseException) -> int:
    from .sessionlog import session_log_path
    detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log('STARTUP FAILURE', detail)
    message = f'The application could not start.\n\n{exc}\n\nDiagnostic log:\n{session_log_path()}'
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    # Automated self-tests must never block on a modal error dialog.
    if sys.platform == 'win32' and not any(flag in sys.argv for flag in ('--runtime-self-test', '--bios-helper-self-test', '--fpt-helper')):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'SenorX99 startup problem', 0x10)
        except Exception as dialog_error:
            log('STARTUP FAILURE', f'Native error dialog unavailable: {dialog_error}')
    finish_session_log('startup failed')
    return 2


def runtime_self_test(kind: str) -> int:
    result = {'version': __version__, 'kind': kind, 'success': False}
    if getattr(sys, 'frozen', False):
        result['exe_sha256'] = sha256(Path(sys.executable).read_bytes()).hexdigest()
    try:
        if kind == 'gui':
            core = importlib.import_module('PySide6.QtCore')
            configure_qt()
            widgets = importlib.import_module('PySide6.QtWidgets')
            importlib.import_module('PySide6.QtGui')
            app = widgets.QApplication.instance() or widgets.QApplication([])
            gui = importlib.import_module('.gui', __package__)
            dropped = []
            class CheckWindow(gui.Window):
                def _rom_input_allowed(self):
                    return True  # Dummy routing test only; load_rom never opens firmware.
                def _require_all_tools(self, **kwargs):
                    return True
                def load_rom(self, path):
                    dropped.append(Path(path).resolve())
            window = CheckWindow(runtime_check=True)
            window.setWindowTitle('Runtime self-test')
            window.show()
            app.processEvents()
            # Exercise the packaged GUI's real drop handlers without firmware
            # inspection, tool execution, network requests or BIOS access.
            import tempfile
            qtgui = importlib.import_module('PySide6.QtGui')
            with tempfile.TemporaryDirectory(prefix='X99-drop-test-') as temporary:
                sample = Path(temporary) / 'firmware åäö with spaces.ROM'
                sample.write_bytes(b'Drop routing test only; not a firmware image')
                mime = core.QMimeData(); mime.setUrls([core.QUrl.fromLocalFile(str(sample))])
                for target in (window.drop_zone, window):
                    enter = qtgui.QDragEnterEvent(core.QPoint(10, 10), core.Qt.CopyAction, mime, core.Qt.LeftButton, core.Qt.NoModifier)
                    drop = qtgui.QDropEvent(core.QPointF(10, 10), core.Qt.CopyAction, mime, core.Qt.LeftButton, core.Qt.NoModifier)
                    widgets.QApplication.sendEvent(target, enter)
                    widgets.QApplication.sendEvent(target, drop)
                    if not enter.isAccepted() or not drop.isAccepted():
                        raise RuntimeError('Packaged GUI did not accept the file drop.')
                if dropped != [sample.resolve(), sample.resolve()]:
                    raise RuntimeError('Packaged GUI file drops did not reach ROM loading.')
                result['drag_drop_verified'] = True
            audit_loaded()
            result['qt_version'] = core.qVersion()
            result['platform'] = app.platformName()
            if result['platform'] != 'windows':
                raise RuntimeError(f'Expected the Windows Qt platform, got {result["platform"]}')
            window.close()
            app.processEvents()
        audit_loaded()
        result['success'] = True
        log('SELF TEST', json.dumps(result))
    except BaseException as exc:
        result['error'] = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log('SELF TEST FAILURE', result['error'])
    if '--runtime-report' in sys.argv:
        index = sys.argv.index('--runtime-report')
        Path(sys.argv[index + 1]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    finish_session_log('runtime self-test completed')
    return 0 if result['success'] else 2


def run(kind: str) -> int:
    try:
        prepare_runtime()
        if '--runtime-self-test' in sys.argv:
            return runtime_self_test(kind)
        if '--bios-helper-self-test' in sys.argv:
            from .fpt_elevation import transport_self_test
            report = Path(sys.argv[sys.argv.index('--runtime-report') + 1])
            return transport_self_test(report)
        if '--fpt-helper' in sys.argv:
            if kind != 'cli':
                raise RuntimeError('BIOS access helper must use the CLI executable.')
            from .fpt_elevation import helper_main
            return helper_main(Path(sys.argv[sys.argv.index('--fpt-helper') + 1]))
        module = importlib.import_module('.gui' if kind == 'gui' else '.cli', __package__)
        if kind == 'gui':
            configure_qt()
        audit_loaded()
        return module.run_gui() if kind == "gui" else module.main()
    except SystemExit:
        raise
    except BaseException as exc:
        return fail_startup(exc)
