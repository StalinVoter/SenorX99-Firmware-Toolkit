"""Launch external firmware tools without inheriting Qt's DLL search directory."""
from __future__ import annotations
import os
import subprocess
import sys
from threading import RLock
from contextlib import contextmanager

_lock = RLock()


@contextmanager
def external_dll_search():
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield
        return
    from . import runtime_bootstrap as runtime
    with _lock:
        loader = runtime._loader
        if loader is None:
            raise RuntimeError('Private runtime has not been initialized.')
        if not loader.kernel.SetDllDirectoryW(None):
            raise loader.ctypes.WinError(loader.ctypes.get_last_error())
        try:
            yield
        finally:
            if not loader.kernel.SetDllDirectoryW(str(sys._MEIPASS)):
                raise loader.ctypes.WinError(loader.ctypes.get_last_error())


def run(*args, timeout=None, **kwargs):
    check = kwargs.pop('check', False)
    proc = popen(*args, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException:
        proc.kill()
        proc.communicate()
        raise
    result = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def popen(*args, **kwargs):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return subprocess.Popen(*args, **kwargs)
    from . import runtime_bootstrap as runtime
    env = dict(kwargs.pop('env', None) or os.environ)
    env['PATH'] = runtime._original_path
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    kwargs['env'] = env
    with external_dll_search():
        return subprocess.Popen(*args, **kwargs)
