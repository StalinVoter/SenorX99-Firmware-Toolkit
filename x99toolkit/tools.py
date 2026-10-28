from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable
import locale
import os
import shutil
import subprocess
from . import nativeprocess
import sys
import threading
import time
import uuid
from .sessionlog import log, log_block
from .constants import UEFIREPLACE_SHA256, UEFIREPLACE_DOWNLOAD_URL
from .firmware import sha256_file

@dataclass(frozen=True)
class ToolPaths:
    mmtool: Path | None
    uefireplace: Path | None
    changelogo: Path | None

@dataclass(frozen=True)
class ExternalToolResult:
    args: list[str]
    cwd: str
    returncode: int
    stdout: str
    elapsed_seconds: float
    completion_reason: str
    process_was_terminated: bool
    staged_file_changed: bool
    staged_sha256_before: str
    staged_sha256_after: str

def app_directory() -> Path:
    override = os.environ.get('X99_TOOLKIT_HOME')
    if override:
        return Path(override).resolve()
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent

def discover(names: tuple[str, ...]) -> Path | None:
    from .tool_inventory import TOOLS_BY_NAME, tool_status
    folder = app_directory() / 'tools'
    for name in names:
        spec = TOOLS_BY_NAME.get(name)
        if spec is not None and tool_status(spec, folder)['state'] == 'ready':
            return (folder / name).resolve()
    return None

def discover_tools() -> ToolPaths:
    return ToolPaths(discover(('mmtool_a5.exe',)), discover(('UEFIReplace.exe',)), discover(('ChangeLogo.exe',)))

def require_tool(path: Path, name: str) -> None:
    from .tool_inventory import TOOLS_BY_NAME, tool_status
    folder = app_directory() / 'tools'
    status = tool_status(TOOLS_BY_NAME[name], folder)
    if path.resolve() != (folder / name).resolve() or status['state'] != 'ready':
        raise ValueError(f'{name} must be the exact verified file in {folder}. {status["detail"]}. Open Required tools to repair or import it.')

def _decode(data: bytes) -> str:
    encoding = locale.getpreferredencoding(False) or 'utf-8'
    try:
        return data.decode(encoding, errors='replace')
    except LookupError:
        return data.decode('utf-8', errors='replace')

def _hash(path: Path) -> str:
    h = sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        nativeprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), check=False)
    else:
        proc.kill()

def run_tool(args: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    if args:
        from .tool_inventory import TOOLS_BY_NAME
        name = Path(args[0]).name
        if name in TOOLS_BY_NAME:
            require_tool(Path(args[0]), name)
    log('TOOL', f'Command: {subprocess.list2cmdline(args)}')
    log('TOOL', f'Working directory: {cwd or Path.cwd()}')
    creationflags = 0
    startupinfo = None
    if os.name == 'nt':
        creationflags = getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    process = nativeprocess.popen(args, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=creationflags, startupinfo=startupinfo)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill(process)
        output, _ = process.communicate()
        log_block('TOOL', 'stdout/stderr before timeout', _decode(output or b''))
        raise subprocess.TimeoutExpired(args, timeout) from exc
    decoded = _decode(output or b'')
    log_block('TOOL', 'stdout/stderr', decoded)
    log('TOOL', f'Return code: {process.returncode}')
    return subprocess.CompletedProcess(args, process.returncode, decoded, None)

def run_mmtool(mmtool: Path, rom_path: Path, mmtool_args: list[str], operation: str, timeout: float = 45, output_markers: tuple[str, ...] = (), staged_validator: Callable[[Path], bool] | None = None) -> ExternalToolResult:
    require_tool(mmtool, 'mmtool_a5.exe')
    mmtool = mmtool.resolve(); rom_path = rom_path.resolve()
    staged = mmtool.parent / f'tbu{uuid.uuid4().hex[:8]}.rom'
    shutil.copy2(rom_path, staged)
    before = _hash(staged)
    command = [str(mmtool), staged.name, *mmtool_args]
    log('MMTOOL', f'Command: {subprocess.list2cmdline(command)}; cwd={mmtool.parent}')
    creationflags = 0; startupinfo = None
    if os.name == 'nt':
        creationflags = getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
        startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startupinfo.wShowWindow = subprocess.SW_HIDE
    process = nativeprocess.popen(command, cwd=str(mmtool.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, creationflags=creationflags, startupinfo=startupinfo)
    chunks: list[bytes] = []; lock = threading.Lock(); last_output = time.monotonic()
    def reader() -> None:
        nonlocal last_output
        assert process.stdout is not None
        while True:
            block = process.stdout.read(1)
            if not block:
                break
            with lock:
                chunks.append(block); last_output = time.monotonic()
    thread = threading.Thread(target=reader, daemon=True); thread.start()
    started = time.monotonic(); accepted = False; reason = ''; terminated = False
    try:
        while True:
            now = time.monotonic(); elapsed = now - started; returncode = process.poll()
            with lock:
                text = _decode(b''.join(chunks)); quiet_for = now - last_output
            if returncode is not None:
                reason = f'normal exit ({returncode})'; accepted = returncode == 0; break
            if output_markers and all(marker in text for marker in output_markers) and quiet_for >= 0.75:
                reason = 'complete MMTool output detected; process did not self-terminate'; accepted = True; _kill(process); terminated = True; break
            if operation == 'modify' and elapsed >= 0.75:
                changed = _hash(staged) != before
                if changed and staged_validator and staged_validator(staged) and quiet_for >= 0.75:
                    reason = 'expected staged-ROM mutation validated; process did not self-terminate'; accepted = True; _kill(process); terminated = True; break
            if elapsed >= timeout:
                reason = f'timeout after {timeout:.1f} seconds'; _kill(process); terminated = True; break
            time.sleep(0.05)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _kill(process); process.wait(timeout=3); terminated = True
        thread.join(timeout=2)
        with lock:
            text = _decode(b''.join(chunks))
        log_block('MMTOOL', 'stdout/stderr', text)
        log('MMTOOL', f'Completion: {reason}; terminated={terminated}')
        after = _hash(staged); changed = after != before; elapsed = time.monotonic() - started
        if not accepted:
            raise RuntimeError(f'MMTool {operation} failed: {reason}\nCommand: {command!r}; see session log for captured output')
        if operation == 'modify':
            if not changed or (staged_validator and not staged_validator(staged)):
                raise RuntimeError('MMTool mutation did not pass validation')
            shutil.copy2(staged, rom_path)
        return ExternalToolResult(command, str(mmtool.parent), process.returncode if process.returncode is not None else -1, text, elapsed, reason, terminated, changed, before, after)
    finally:
        try:
            staged.unlink(missing_ok=True)
        except Exception:
            pass


def validate_uefireplace(path: Path) -> None:
    require_tool(path, 'UEFIReplace.exe')
    digest = sha256_file(path)
    log('TOOLS', f'UEFIReplace candidate: {path}')
    log('TOOLS', f'UEFIReplace SHA-256: {digest}')
    if digest != UEFIREPLACE_SHA256:
        raise ValueError(
            'UEFIReplace.exe is not the required official 0.28.0 Windows binary: '
            f'{digest} != {UEFIREPLACE_SHA256}. Download the exact required build from {UEFIREPLACE_DOWNLOAD_URL}'
        )
    log('TOOLS', 'UEFIReplace validation: PASS')


