from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any
import atexit
import json
import os
import platform
import sys
import traceback

_LOCK = RLock()
_SESSION_PATH: Path | None = None
_SESSION_FILE = None
_FINISHED = False


def _app_directory() -> Path:
    override = os.environ.get('X99_TOOLKIT_HOME')
    if override:
        return Path(override).resolve()
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def logs_directory() -> Path:
    import tempfile
    candidates = [_app_directory() / 'LOGS']
    if os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA']) / 'SenorX99' / 'LOGS')
    candidates.append(Path(tempfile.gettempdir()) / 'SenorX99' / 'LOGS')
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
            return path
        except OSError:
            continue
    raise OSError('No writable SenorX99 log directory is available.')


def session_log_path() -> Path | None:
    return _SESSION_PATH


def start_session_log(version: str, app_name: str = 'SenorX99') -> Path:
    global _SESSION_PATH, _SESSION_FILE, _FINISHED
    with _LOCK:
        if _SESSION_FILE is not None:
            return _SESSION_PATH  # type: ignore[return-value]
        root = logs_directory()
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
        path = root / f'Session-{stamp}-PID{os.getpid()}.log'
        _SESSION_FILE = path.open('a', encoding='utf-8', buffering=1)
        _SESSION_PATH = path
        _FINISHED = False
        _write_raw('SESSION', f'{app_name} v{version} session started')
        _write_raw('SESSION', f'Session log: {path}')
        _write_raw('ENV', f'Executable: {Path(sys.executable).resolve()}')
        _write_raw('ENV', f'Application directory: {_app_directory()}')
        _write_raw('ENV', f'Working directory: {Path.cwd()}')
        _write_raw('ENV', f'Frozen executable: {bool(getattr(sys, "frozen", False))}')
        _write_raw('ENV', f'Python: {sys.version.replace(chr(10), " ")}')
        _write_raw('ENV', f'Platform: {platform.platform()}')
        _write_raw('ENV', f'OS name: {os.name}')
        return path


def _write_raw(category: str, message: str) -> None:
    if _SESSION_FILE is None:
        return
    try:
        timestamp = datetime.now().astimezone().isoformat(timespec='milliseconds')
        lines = str(message).splitlines() or ['']
        for line in lines:
            _SESSION_FILE.write(f'{timestamp} [{category}] {line}\n')
        _SESSION_FILE.flush()
    except OSError:
        pass  # Never turn a log-disk error into an interrupted flash.


def log(category: str, message: str) -> None:
    with _LOCK:
        _write_raw(category, message)


def log_block(category: str, title: str, text: str | None) -> None:
    with _LOCK:
        _write_raw(category, f'--- {title} ---')
        if text:
            for line in str(text).splitlines():
                _write_raw(category, line)
        else:
            _write_raw(category, '(no output)')
        _write_raw(category, f'--- end {title} ---')


def log_json(category: str, title: str, value: Any) -> None:
    try:
        rendered = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        rendered = f'<could not serialize JSON: {type(exc).__name__}: {exc}>\n{value!r}'
    log_block(category, title, rendered)


def log_exception(category: str, exc: BaseException, context: str | None = None) -> None:
    prefix = f'{context}: ' if context else ''
    log(category, f'{prefix}{type(exc).__name__}: {exc}')
    log_block(category, 'traceback', ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())


def install_exception_hook() -> None:
    old_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            log('UNHANDLED', ''.join(traceback.format_exception(exc_type, exc, tb)).rstrip())
        finally:
            old_hook(exc_type, exc, tb)

    sys.excepthook = hook


def finish_session_log(reason: str = 'normal application exit') -> None:
    global _SESSION_FILE, _FINISHED
    with _LOCK:
        if _SESSION_FILE is None or _FINISHED:
            return
        _write_raw('SESSION', f'Session ended: {reason}')
        try:
            _SESSION_FILE.flush()
            _SESSION_FILE.close()
        finally:
            _SESSION_FILE = None
            _FINISHED = True


atexit.register(finish_session_log)
