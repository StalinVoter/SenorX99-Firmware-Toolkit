"""Keep the GUI at Explorer's integrity level; elevate only BIOS access."""
from __future__ import annotations
from dataclasses import asdict
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

from .sessionlog import log, log_exception
from .tools import app_directory


class BiosHelperError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@contextmanager
def exchange_directory():
    root = Path(tempfile.mkdtemp(prefix='X99-BIOS-access-'))
    try:
        yield root
    finally:
        try:
            shutil.rmtree(root)
        except OSError as exc:
            # Cleanup must never replace a successful result or the real error.
            log_exception('BIOS HELPER CLEANUP', exc, f'Temporary files retained at {root}')


def needs_elevation() -> bool:
    if os.name != 'nt':
        return False
    import ctypes
    return not bool(ctypes.windll.shell32.IsUserAnAdmin())


def helper_command(request: Path) -> tuple[Path, list[str]]:
    if getattr(sys, 'frozen', False):
        executable = app_directory() / 'SenorX99-CLI.exe'
        if not executable.is_file():
            raise FileNotFoundError('BIOS access needs SenorX99-CLI.exe beside the GUI executable.')
        return executable, ['--fpt-helper', str(request)]
    entrypoint = Path(__file__).resolve().parents[1] / 'SenorX99-CLI.py'
    return Path(sys.executable), [str(entrypoint), '--fpt-helper', str(request)]


def _wait_elevated(executable: Path, arguments: list[str], poll) -> int:
    import ctypes
    from ctypes import wintypes
    from .nativeprocess import external_dll_search

    class ExecuteInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
                    ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                    ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                    ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
                    ('hProcess', wintypes.HANDLE)]

    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ole = ctypes.WinDLL('ole32')
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    info = ExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # Process handle, synchronous activation, no shell error UI.
    info.lpVerb = 'runas'
    info.lpFile = str(executable)
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.lpDirectory = str(app_directory())
    info.nShow = 0
    try:
        with external_dll_search():
            if not shell.ShellExecuteExW(ctypes.byref(info)):
                error = ctypes.get_last_error()
                if error == 1223:
                    raise BiosHelperError('elevation-cancelled', 'Administrator permission was cancelled. BIOS access was not started.')
                raise ctypes.WinError(error)
        if not info.hProcess:
            raise RuntimeError('Windows did not return a BIOS helper process handle.')
        try:
            while True:
                state = kernel.WaitForSingleObject(info.hProcess, 100)
                try:
                    poll()
                except Exception as exc:
                    # Never abandon the helper or clean up its files while a
                    # flash/recovery may still be running.
                    log_exception('BIOS HELPER EVENTS', exc, 'Event delivery failed; still waiting for the helper')
                if state == 0:
                    break
                if state != 258:
                    raise ctypes.WinError(ctypes.get_last_error())
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value
        finally:
            kernel.CloseHandle(info.hProcess)
    finally:
        if initialized:
            ole.CoUninitialize()


def run_elevated(action: str, path: Path | None = None, callback=None) -> dict:
    if action not in ('probe', 'dump', 'flash', 'check-transport'):
        raise ValueError('Unsupported BIOS helper action.')
    log('BIOS HELPER', f'Requesting administrator permission for {action}')
    # The helper is never killed on a timeout: a flash/recovery must finish.
    with exchange_directory() as root:
        from .fpt import backup_directory
        request = root / 'request.json'
        events = root / 'events.jsonl'
        result = root / 'result.json'
        events.touch()
        # Both output files belong to the unelevated caller. The helper opens
        # them in place, so elevation cannot give replacement files different
        # access controls or integrity labels.
        result.touch(exist_ok=False)
        request.write_text(json.dumps({'action': action, 'path': str(path.resolve()) if path else None,
                                       'backup_directory': str(backup_directory())}), encoding='utf-8')
        executable, arguments = helper_command(request)
        with events.open(encoding='utf-8') as reader:
            pending = ''
            def poll():
                nonlocal pending
                pending += reader.read()
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    if line:
                        event = json.loads(line)
                        if callback:
                            callback(event)
                        if event.get('type') == 'transcript':
                            log('FPT HELPER', event.get('text', ''))
            try:
                code = _wait_elevated(executable, arguments, poll)
            except BiosHelperError:
                raise
            except Exception as exc:
                log_exception('BIOS HELPER LAUNCH', exc)
                raise BiosHelperError('helper-launch-failed', f'Could not complete the administrator helper launch: {exc}') from exc
            try:
                poll()
            except Exception as exc:
                log_exception('BIOS HELPER EVENTS', exc, 'Final event delivery failed')
        envelope = read_result(result, code)
        log('BIOS HELPER', f'{action} helper completed')
        if envelope.get('session_log'):
            log('BIOS HELPER', f"Detailed helper session log: {envelope['session_log']}")
        return envelope['result']


def read_result(result: Path, code: int) -> dict:
    try:
        text = result.read_text(encoding='utf-8')
    except UnicodeError as exc:
        log_exception('BIOS HELPER RESULT', exc, f'Invalid text in {result}; helper exit code {code}')
        raise BiosHelperError('helper-result-invalid', f'The BIOS helper result is not valid UTF-8 (exit code {code}). See LOGS.') from exc
    except OSError as exc:
        log_exception('BIOS HELPER RESULT', exc, f'Reading {result}; helper exit code {code}')
        reason = 'helper-result-missing' if isinstance(exc, FileNotFoundError) else 'helper-result-access'
        raise BiosHelperError(reason, f'Cannot read the BIOS helper result file: {result}. Helper exit code: {code}. {exc}') from exc
    if not text.strip():
        raise BiosHelperError('helper-result-missing', f'The BIOS helper returned no result (exit code {code}). See the CLI session log in LOGS.')
    try:
        envelope = json.loads(text)
        if not isinstance(envelope, dict) or not isinstance(envelope.get('completed'), bool):
            raise ValueError('Missing completed flag')
        if envelope['completed'] and not isinstance(envelope.get('result'), dict):
            raise ValueError('Missing result object')
    except (ValueError, TypeError) as exc:
        log_exception('BIOS HELPER RESULT', exc, f'Invalid result from {result}; helper exit code {code}')
        raise BiosHelperError('helper-result-invalid', f'The BIOS helper returned an invalid result (exit code {code}). See LOGS for the traceback.') from exc
    if not envelope['completed'] or code != 0:
        raise BiosHelperError('helper-operation-failed', str(envelope.get('error') or f'BIOS access helper failed (exit code {code}).'))
    return envelope


def transport_self_test(report: Path) -> int:
    """Exercise the production ShellExecute/runas exchange without BIOS access."""
    from . import __version__
    record = {'version': __version__, 'success': False, 'hardware_access': False,
              'caller_elevated': not needs_elevation(),
              'exe_sha256': sha256(Path(sys.executable).read_bytes()).hexdigest()}
    try:
        events = []
        record['result'] = run_elevated('check-transport', callback=events.append)
        if record['result'].get('transport_verified') is not True or record['result'].get('helper_elevated') is not True:
            raise RuntimeError('The elevated helper did not verify the exchange.')
        if not events:
            raise RuntimeError('The elevated helper produced no streamed event.')
        record['events'] = events
        record['success'] = True
    except Exception as exc:
        log_exception('BIOS HELPER SELF TEST', exc)
        record['error'] = str(exc)
        record['reason'] = getattr(exc, 'reason', 'helper-exchange-failed')
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(record, indent=2), encoding='utf-8')
    return 0 if record['success'] else 2


def helper_main(request_path: Path) -> int:
    root = request_path.resolve().parent
    result_path = root / 'result.json'
    envelope = {'completed': False}
    try:
        if os.name != 'nt' or needs_elevation():
            raise RuntimeError('The BIOS helper requires administrator permission on Windows.')
        request = json.loads(request_path.read_text(encoding='utf-8'))
        os.environ['X99_TOOLKIT_BACKUP_DIR'] = request['backup_directory']
        from . import fpt
        with (root / 'events.jsonl').open('r+', encoding='utf-8') as stream:
            stream.seek(0, 2)
            def emit(event):
                stream.write(json.dumps(event) + '\n')
                stream.flush()
            action = request['action']
            if action == 'check-transport':
                emit({'type': 'status', 'text': 'Elevated helper transport test; no BIOS access'})
                result = {'transport_verified': True, 'helper_elevated': not needs_elevation(), 'helper_pid': os.getpid(),
                          'helper_exe_sha256': sha256(Path(sys.executable).read_bytes()).hexdigest()}
            elif action == 'probe':
                result = fpt.probe_fpt(emit)
            elif action == 'dump':
                result = asdict(fpt.dump_bios(Path(request['path']), emit))
            elif action == 'flash':
                result = asdict(fpt.flash_bios_with_recovery(Path(request['path']), emit))
            else:
                raise ValueError('Unsupported BIOS helper action.')
        from .sessionlog import session_log_path
        envelope = {'completed': True, 'result': result, 'session_log': str(session_log_path())}
    except Exception as exc:
        envelope['error'] = f'{type(exc).__name__}: {exc}'
        log_exception('BIOS HELPER FAILED', exc)
    try:
        # r+ deliberately refuses to create a replacement elevated-owned file.
        # The caller waits for process exit before consuming this JSON.
        with result_path.open('r+', encoding='utf-8') as stream:
            stream.seek(0)
            json.dump(envelope, stream)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        log('BIOS HELPER RESULT', f'Wrote result in place: {result_path}')
    except OSError as exc:
        log_exception('BIOS HELPER RESULT WRITE', exc, str(result_path))
        return 3
    return 0 if envelope['completed'] else 2
