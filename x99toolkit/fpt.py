from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable, Any
import json
import locale
import os
import re
import shutil
import subprocess
from . import nativeprocess
import tempfile
import queue
import threading
import time

from .tools import app_directory, sha256_file
from .sessionlog import log, log_block, log_json, log_exception

from .constants import FPT_VERSION, FPT_SHA256, FPT_FILE_SHA256, FPT_SOURCE_FOLDER_URL
from .fpt_elevation import needs_elevation, run_elevated
FPARTS_SHA256 = '9f815e22fdc5562f0af6ac552c27e0adccf9f3654c3d76dbc4f9cc9278bc2313'
FPT_REQUIRED_FILES = ('fptw64.exe', 'pmxdll32e.DLL', 'idrvdll32e.DLL')

EventCallback = Callable[[dict[str, Any]], None]

_PHASE_RE = re.compile(
    r'-\s*(Reading Flash|Erasing Flash Block|Programming Flash|Verifying Flash)'
    r'(?:\s*\[(0x[0-9A-Fa-f]+)\])?.*?-\s*(\d{1,3})%\s+complete',
    re.IGNORECASE,
)


@dataclass
class FptRunResult:
    command: list[str]
    returncode: int
    transcript: list[str]
    passed_marker: bool
    data_identical: bool
    destructive_started: bool
    success: bool


@dataclass
class DumpResult:
    success: bool
    output_path: str
    sha256: str | None
    size: int | None
    run: dict[str, Any]
    error: str | None


@dataclass
class BackupResult:
    success: bool
    backup_path: str | None
    sha256: str | None
    size: int | None
    first_run: dict[str, Any] | None
    second_run: dict[str, Any] | None
    error: str | None


@dataclass
class FlashResult:
    success: bool
    target_path: str
    backup_path: str | None
    backup_sha256: str | None
    flash_run: dict[str, Any] | None
    recovery_attempted: bool
    recovery_success: bool | None
    recovery_run: dict[str, Any] | None
    backup_runs: dict[str, Any] | None
    error: str | None


def _emit(callback: EventCallback | None, **event: Any) -> None:
    typ = event.get('type')
    if typ == 'status':
        log('FPT STATUS', str(event.get('text', '')))
    elif typ == 'group':
        log('FPT', f"Operation group: {event.get('name', 'FPT operation')}")
    if callback:
        try:
            callback(event)
        except Exception as exc:
            log('FPT UI', f'Callback failed: {type(exc).__name__}: {exc}')


def discover_fpt_directory() -> Path:
    return app_directory() / 'tools'


def discover_fpt() -> Path | None:
    p = discover_fpt_directory() / 'fptw64.exe'
    return p.resolve() if p.is_file() else None


def authoritative_fparts() -> Path:
    return app_directory() / 'fpt_support' / 'fparts.txt'




def _log_validation_result(result: dict[str, Any]) -> None:
    state = 'PASS' if result.get('ready') else 'FAIL'
    missing = ','.join(result.get('missing') or []) or 'none'
    log('FPT PREFLIGHT', f"FPT installation validation: {state}; reason={result.get('reason')}; missing={missing}")

def validate_fpt_installation() -> dict[str, Any]:
    folder = discover_fpt_directory()
    log('FPT PREFLIGHT', f'FPT tools directory: {folder}')
    missing = [name for name in FPT_REQUIRED_FILES if not (folder / name).is_file()]
    if missing:
        result = {
            'ready': False,
            'reason': 'missing-files',
            'missing': missing,
            'message': 'Missing required Intel FPT files: ' + ', '.join(missing),
        }
        _log_validation_result(result)
        return result
    fpt = folder / 'fptw64.exe'
    digest = sha256_file(fpt)
    log('FPT PREFLIGHT', f'fptw64.exe: {fpt}')
    log('FPT PREFLIGHT', f'fptw64.exe SHA-256: {digest}')
    for name in ('pmxdll32e.DLL', 'idrvdll32e.DLL'):
        path = folder / name
        if path.is_file():
            log('FPT PREFLIGHT', f'{name}: size={path.stat().st_size} SHA-256={sha256_file(path)}')
    if digest != FPT_SHA256:
        result = {
            'ready': False,
            'reason': 'wrong-fpt-version',
            'missing': [],
            'message': f'fptw64.exe SHA-256 is {digest}; required {FPT_SHA256} (FPT {FPT_VERSION}).',
        }
        _log_validation_result(result)
        return result
    for name in ('pmxdll32e.DLL', 'idrvdll32e.DLL'):
        digest = sha256_file(folder / name)
        if digest != FPT_FILE_SHA256[name]:
            result = {
                'ready': False, 'reason': 'wrong-fpt-dependency', 'missing': [],
                'message': f'{name} is not the required companion file for Intel FPT {FPT_VERSION}. Download/repair it in Required tools.',
            }
            _log_validation_result(result)
            return result
    source = authoritative_fparts()
    log('FPT PREFLIGHT', f'Authoritative fparts source: {source}')
    if not source.is_file():
        result = {
            'ready': False,
            'reason': 'missing-authoritative-fparts',
            'missing': [],
            'message': 'The patcher\'s authoritative fparts.txt is missing.',
        }
        _log_validation_result(result)
        return result
    digest_parts = sha256_file(source)
    log('FPT PREFLIGHT', f'Authoritative fparts SHA-256: {digest_parts}')
    if digest_parts != FPARTS_SHA256:
        result = {
            'ready': False,
            'reason': 'wrong-authoritative-fparts',
            'missing': [],
            'message': 'The patcher\'s authoritative fparts.txt failed its SHA-256 self-check.',
        }
        _log_validation_result(result)
        return result
    result = {'ready': True, 'reason': 'ok', 'missing': [], 'message': f'Intel FPT {FPT_VERSION} files are ready.'}
    _log_validation_result(result)
    return result


def install_authoritative_fparts(*, prevalidated: bool = False) -> Path:
    if not prevalidated:
        status = validate_fpt_installation()
        if not status['ready']:
            raise RuntimeError(status['message'])
    src = authoritative_fparts()
    dst = discover_fpt_directory() / 'fparts.txt'
    # This is intentionally unconditional. The app's supplied part table is
    # authoritative for every probe/dump/flash and replaces any stock copy.
    log('FPT FPARTS', f'Overwriting {dst} with authoritative {src}')
    shutil.copyfile(src, dst)
    installed_hash = sha256_file(dst)
    log('FPT FPARTS', f'Installed fparts SHA-256: {installed_hash}')
    if installed_hash != FPARTS_SHA256:
        raise RuntimeError('Could not install and verify the required fparts.txt in the tools folder.')
    log('FPT FPARTS', 'Authoritative fparts installation: PASS')
    return dst


def memory_integrity_running() -> bool | None:
    """Return True if HVCI/Memory Integrity is actually running, False if not, None if unknown."""
    if os.name != 'nt':
        log('MEMORY INTEGRITY', f'Not queried because os.name={os.name!r} (Windows required)')
        return None
    cmd = [
        'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
        "$d=Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop; "
        "if ($d.SecurityServicesRunning -contains 2) { 'ON' } else { 'OFF' }",
    ]
    shown = subprocess.list2cmdline(cmd)
    log('MEMORY INTEGRITY', f'Command: {shown}')
    try:
        cp = nativeprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=12, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = cp.stdout.decode(locale.getpreferredencoding(False) or 'utf-8', errors='replace')
        log('MEMORY INTEGRITY', f'Exit code: {cp.returncode}')
        log_block('MEMORY INTEGRITY', 'PowerShell stdout/stderr', raw.rstrip())
        text = raw.strip().upper()
        if text.endswith('ON'):
            log('MEMORY INTEGRITY', 'Detected state: ON / HVCI running')
            return True
        if text.endswith('OFF'):
            log('MEMORY INTEGRITY', 'Detected state: OFF / HVCI not running')
            return False
        log('MEMORY INTEGRITY', 'Detected state: UNKNOWN (unexpected PowerShell output)')
    except Exception as exc:
        log('MEMORY INTEGRITY', f'Query failed: {type(exc).__name__}: {exc}')
    return None


def _startupinfo():
    if os.name != 'nt':
        return None, 0
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si, getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _decode(buf: bytes) -> str:
    enc = locale.getpreferredencoding(False) or 'utf-8'
    try:
        return buf.decode(enc, errors='replace')
    except LookupError:
        return buf.decode('utf-8', errors='replace')


def parse_progress_record(record: str) -> dict[str, Any] | None:
    match = _PHASE_RE.search(record)
    if not match:
        return None
    operation = match.group(1).lower()
    address = (match.group(2) or '').upper()
    percent = max(0, min(100, int(match.group(3))))
    if operation.startswith('reading'):
        kind, label = 'read', 'Reading flash'
    elif operation.startswith('erasing'):
        kind, label = 'erase', 'Erasing flash'
    elif operation.startswith('programming'):
        kind, label = 'program', 'Programming flash'
    else:
        kind, label = 'verify', 'Verifying flash'

    # The bracketed address in every FPT progress line is the *current cursor*
    # inside that operation. It changes as the percentage rises for read,
    # erase, program and verify alike. It is diagnostic information, not a
    # progress-phase identity. Phase identity is assigned statefully by
    # _sequence_progress_phase() so a continuous 0..100 pass gets one GUI bar.
    return {
        'kind': kind,
        'label': label,
        'address': address,
        'percent': percent,
        'destructive': kind in ('erase', 'program'),
    }


def _sequence_progress_phase(phase: dict[str, Any], state: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Assign a stable GUI key to one FPT progress pass.

    FPT changes the bracketed address on virtually every progress update. A new
    pass is identified instead by the percentage resetting backwards. This
    preserves one bar for a continuous erase/program run while still allowing
    a second bar if FPT later starts another erase/program pass.
    """
    kind = str(phase['kind'])
    percent = int(phase['percent'])
    previous = state.get(kind)
    if previous is None:
        index = 1
    else:
        index = int(previous['index'])
        if percent < int(previous['percent']):
            index += 1
    state[kind] = {'index': index, 'percent': percent}

    event = dict(phase)
    event['key'] = f'{kind}:{index}'
    if index > 1:
        event['label'] = f"{phase['label']} (pass {index})"
    return event


_FLASH_DEVICE_SIZE_RE = re.compile(r'\bID:0x[0-9A-Fa-f]+\s+Size:\s*(\d+)KB\b', re.IGNORECASE)


def reported_flash_size_bytes(transcript: list[str]) -> int | None:
    """Return total detected SPI flash capacity from FPT's device inventory."""
    sizes_kb: list[int] = []
    for record in transcript:
        match = _FLASH_DEVICE_SIZE_RE.search(record)
        if match:
            sizes_kb.append(int(match.group(1)))
    if not sizes_kb:
        return None
    return sum(sizes_kb) * 1024


def _run_fpt(args: list[str], group: str, callback: EventCallback | None = None, timeout: int = 1800, *, prevalidated: bool = False) -> FptRunResult:
    install_authoritative_fparts(prevalidated=prevalidated)
    fpt = discover_fpt()
    if fpt is None:
        raise FileNotFoundError('fptw64.exe is not installed in the tools folder.')
    command = [str(fpt), *args]
    _emit(callback, type='group', name=group)
    shown = subprocess.list2cmdline(command)
    log('FPT', f'[{group}] Command: {shown}')
    log('FPT', f'[{group}] Working directory: {fpt.parent}')
    _emit(callback, type='transcript', group=group, text='> ' + shown)
    transcript = ['> ' + shown]
    si, creationflags = _startupinfo()
    proc = nativeprocess.popen(
        command,
        cwd=str(fpt.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        startupinfo=si,
        creationflags=creationflags,
        bufsize=0,
    )
    current = bytearray()
    chunks = queue.Queue()
    def read_stdout():
        try:
            while True:
                ch = proc.stdout.read(1)
                chunks.put(ch)
                if not ch: break
        except Exception as exc:
            chunks.put(exc)
    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    destructive = False
    progress_state: dict[str, dict[str, int]] = {}
    try:
        assert proc.stdout is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('FPT operation exceeded its timeout')
            try:
                ch = chunks.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError('FPT operation exceeded its timeout')
            if isinstance(ch, Exception): raise ch
            if not ch:
                break
            if ch in (b'\r', b'\n'):
                if current:
                    record = _decode(bytes(current))
                    current.clear()
                    transcript.append(record)
                    log('FPT OUTPUT', f'[{group}] {record}')
                    _emit(callback, type='transcript', group=group, text=record)
                    destructive = destructive or bool(re.search(r'(Erasing Flash|Programming Flash)', record, re.I))
                    phase = parse_progress_record(record)
                    if phase:
                        phase = _sequence_progress_phase(phase, progress_state)
                        destructive = destructive or phase['destructive']
                        _emit(callback, type='phase', group=group, **phase)
                continue
            current.extend(ch)
        if current:
            record = _decode(bytes(current))
            transcript.append(record)
            log('FPT OUTPUT', f'[{group}] {record}')
            _emit(callback, type='transcript', group=group, text=record)
            destructive = destructive or bool(re.search(r'(Erasing Flash|Programming Flash)', record, re.I))
            phase = parse_progress_record(record)
            if phase:
                phase = _sequence_progress_phase(phase, progress_state)
                destructive = destructive or phase['destructive']
                _emit(callback, type='phase', group=group, **phase)
        returncode = proc.wait(timeout=max(1, timeout))
    except Exception as exc:
        if current:
            record = _decode(bytes(current))
            transcript.append(record)
            log('FPT OUTPUT', f'[{group}] {record}')
            destructive = destructive or bool(re.search(r'(Erasing Flash|Programming Flash)', record, re.I))
        proc.kill()
        proc.wait()
        transcript.append(f'FPT capture error: {type(exc).__name__}: {exc}')
        log('FPT', transcript[-1])
        return FptRunResult(command, -1, transcript, False, False, destructive, False)
    joined = '\n'.join(transcript)
    passed = 'FPT Operation Passed' in joined
    identical = 'RESULT: The data is identical.' in joined
    is_flash = '-f' in [x.lower() for x in args]
    success = returncode == 0 and passed and (identical if is_flash else True)
    log('FPT', f'[{group}] Return code={returncode}; FPT Operation Passed={passed}; data identical={identical}; destructive_started={destructive}; success={success}')
    _emit(callback, type='process_end', group=group, success=success, returncode=returncode)
    return FptRunResult(command, returncode, transcript, passed, identical, destructive, success)


def _log_probe_summary(result: dict[str, Any]) -> None:
    # The FPT command/output/return markers were already written live by
    # _run_fpt. This line records only the new preflight interpretation.
    log(
        'FPT PREFLIGHT',
        f"Result: ready={result.get('ready')}; hvci={result.get('hvci')}; reason={result.get('reason')}",
    )


def probe_fpt(callback: EventCallback | None = None) -> dict[str, Any]:
    log('FPT PREFLIGHT', 'Starting launch-time FPT compatibility probe')
    status = validate_fpt_installation()
    if not status['ready']:
        result = {'ready': False, 'hvci': None, 'probe': None, 'reason': status['reason'], 'message': status['message']}
        _log_probe_summary(result)
        return result
    if needs_elevation():
        try:
            result = run_elevated('probe', callback=callback)
        except Exception as exc:
            log_exception('FPT HELPER EXCHANGE', exc)
            result = {'ready': False, 'hvci': None, 'probe': None,
                      'reason': getattr(exc, 'reason', 'helper-exchange-failed'), 'message': str(exc)}
        _log_probe_summary(result)
        return result
    hvci = memory_integrity_running()
    try:
        # The installation was validated immediately above. Reusing that result
        # prevents the same tool/hash inventory being written twice to the session
        # log while still re-installing and verifying authoritative fparts.txt.
        run = _run_fpt(['-i'], 'FPT compatibility probe', callback, timeout=60, prevalidated=True)
    except Exception as exc:
        result = {'ready': False, 'hvci': hvci, 'probe': None, 'reason': 'probe-exception', 'message': f'{type(exc).__name__}: {exc}'}
        _log_probe_summary(result)
        return result
    if run.success:
        result = {'ready': True, 'hvci': hvci, 'probe': asdict(run), 'reason': 'ok', 'message': 'BIOS dump and flash are available.'}
        _log_probe_summary(result)
        return result
    if re.search(r'-2146762484|0x800b010c', '\n'.join(run.transcript), re.IGNORECASE):
        result = {
            'ready': False, 'hvci': hvci, 'probe': asdict(run),
            'reason': 'driver-certificate-revoked',
            'message': ('Windows rejected the Intel FPT driver with certificate-revoked error 0x800B010C. '
                        'This is a driver trust failure; administrator permission and the correct tool DLLs do not resolve it. '
                        'Use a supported firmware-update method. See LOGS for the complete driver error.'),
        }
        _log_probe_summary(result)
        return result
    if hvci is True:
        result = {
            'ready': False,
            'hvci': True,
            'probe': asdict(run),
            'reason': 'memory-integrity-block',
            'message': 'Windows Memory Integrity is running and Intel FPT could not access the flash. Disable Memory integrity in Windows Security and restart Windows to use BIOS dump/flash.',
        }
        _log_probe_summary(result)
        return result
    result = {
        'ready': False,
        'hvci': hvci,
        'probe': asdict(run),
        'reason': 'fpt-probe-failed',
        'message': 'Intel FPT is installed but could not access the firmware on this boot. Review the session log in the LOGS folder for the complete FPT output.',
    }
    _log_probe_summary(result)
    return result


def _default_documents() -> Path:
    # SHGetFolderPathW honors Windows' redirected Documents folder. Fall back
    # to the conventional location when unavailable.
    if os.name == 'nt':
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            # CSIDL_PERSONAL / Documents = 5
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / 'Documents'


def backup_directory() -> Path:
    override = os.environ.get('X99_TOOLKIT_BACKUP_DIR')
    if override:
        return Path(override).resolve()
    return _default_documents() / 'X99 Firmware Toolkit' / 'BIOS Backups'


def _log_dump_summary(result: DumpResult) -> None:
    log_json('DUMP', 'Manual BIOS dump summary', {
        'success': result.success,
        'output_path': result.output_path,
        'sha256': result.sha256,
        'size': result.size,
        'error': result.error,
    })


def _log_backup_summary(result: BackupResult) -> None:
    log_json('BACKUP', 'Verified backup summary', {
        'success': result.success,
        'backup_path': result.backup_path,
        'sha256': result.sha256,
        'size': result.size,
        'error': result.error,
    })


def _log_flash_summary(result: FlashResult) -> None:
    log_json('FLASH', 'Flash workflow summary', {
        'success': result.success,
        'target_path': result.target_path,
        'backup_path': result.backup_path,
        'backup_sha256': result.backup_sha256,
        'recovery_attempted': result.recovery_attempted,
        'recovery_success': result.recovery_success,
        'error': result.error,
    })


def _dump_once(output: Path, group: str, callback: EventCallback | None) -> FptRunResult:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    run = _run_fpt(['-d', str(output)], group, callback)
    if not run.success:
        return run
    if not output.is_file():
        log('DUMP', f'[{group}] FPT reported success but no dump file exists')
        run.success = False
        return run
    expected_size = reported_flash_size_bytes(run.transcript)
    actual_size = output.stat().st_size
    if expected_size is None:
        log('DUMP', f'[{group}] Could not determine installed SPI flash size from FPT output; dump rejected')
        run.success = False
        return run
    if actual_size != expected_size:
        log('DUMP', f'[{group}] Dump size validation failed: file={actual_size} bytes, detected flash={expected_size} bytes')
        run.success = False
        return run
    log('DUMP', f'[{group}] Dump size validation: PASS ({actual_size} bytes matches detected SPI capacity)')
    return run


def dump_bios(output: Path, callback: EventCallback | None = None) -> DumpResult:
    output = output.resolve()
    log('DUMP', f'Manual BIOS dump requested: {output}')
    try:
        if needs_elevation():
            result = DumpResult(**run_elevated('dump', output, callback))
            _log_dump_summary(result)
            return result
        _emit(callback, type='status', text='Dumping the installed BIOS…')
        run = _dump_once(output, 'Dump BIOS', callback)
        if not run.success:
            raise RuntimeError('FPT did not complete the BIOS dump successfully.')
        digest = sha256_file(output)
        output.with_suffix(output.suffix + '.fpt.log').write_text('\n'.join(run.transcript) + '\n', encoding='utf-8')
        _emit(callback, type='status', text=f'BIOS dump completed and saved as {output.name}.')
        result = DumpResult(True, str(output), digest, output.stat().st_size, asdict(run), None)
        _log_dump_summary(result)
        return result
    except Exception as exc:
        result = DumpResult(False, str(output), sha256_file(output) if output.is_file() else None, output.stat().st_size if output.is_file() else None, asdict(run) if 'run' in locals() else {}, f'{type(exc).__name__}: {exc}')
        _log_dump_summary(result)
        return result


def create_verified_backup(callback: EventCallback | None = None) -> BackupResult:
    root = backup_directory()
    log('BACKUP', f'Beginning verified pre-flash backup workflow in {root}')
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    final = root / f'Backup-{stamp}.ROM'
    first_run = second_run = None
    try:
        _emit(callback, type='status', text='Creating two independent BIOS dumps before flashing…')
        with tempfile.TemporaryDirectory(prefix='X99-Toolkit-backup-', dir=root) as td:
            td = Path(td)
            a = td / 'backup-A.ROM'
            b = td / 'backup-B.ROM'
            first_run = _dump_once(a, 'Backup dump 1 of 2', callback)
            if not first_run.success:
                raise RuntimeError('The first pre-flash BIOS backup dump failed. Flashing was not started.')
            second_run = _dump_once(b, 'Backup dump 2 of 2', callback)
            if not second_run.success:
                raise RuntimeError('The second pre-flash BIOS backup dump failed. Flashing was not started.')
            size_a, size_b = a.stat().st_size, b.stat().st_size
            log('BACKUP', f'Backup dump sizes: A={size_a} B={size_b}')
            if size_a != size_b:
                raise RuntimeError('The two BIOS backup dumps have different sizes. Flashing was not started.')
            ha, hb = sha256_file(a), sha256_file(b)
            log('BACKUP', f'Backup dump SHA-256 A: {ha}')
            log('BACKUP', f'Backup dump SHA-256 B: {hb}')
            if ha != hb:
                raise RuntimeError('The two BIOS backup dumps are not byte-identical. Flashing was not started.')
            if final.exists():
                final = root / f'Backup-{stamp}-{os.getpid()}.ROM'
            shutil.copy2(a, final)
        digest = sha256_file(final)
        if digest != ha or final.stat().st_size != size_a:
            raise RuntimeError('Permanent backup copy failed verification; flashing was not started.')
        final.with_suffix(final.suffix + '.sha256').write_text(f'{digest}  {final.name}\n', encoding='ascii')
        _emit(callback, type='status', text=f'Verified pre-flash backup saved: {final}')
        result = BackupResult(True, str(final), digest, final.stat().st_size, asdict(first_run), asdict(second_run), None)
        _log_backup_summary(result)
        return result
    except Exception as exc:
        result = BackupResult(False, None, None, None, asdict(first_run) if first_run else None, asdict(second_run) if second_run else None, f'{type(exc).__name__}: {exc}')
        _log_backup_summary(result)
        return result


def _write_flash_report_unchecked(result: FlashResult) -> tuple[Path, Path]:
    _log_flash_summary(result)
    root = backup_directory()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    json_path = root / f'Flash-{stamp}.json'
    log_path = root / f'Flash-{stamp}.fpt.log'
    json_path.write_text(json.dumps(asdict(result), indent=2), encoding='utf-8')
    lines: list[str] = []
    if result.backup_runs:
        for key in ('first', 'second'):
            run = result.backup_runs.get(key)
            if run:
                lines.extend(run.get('transcript', []))
                lines.append('')
    if result.flash_run:
        lines.extend(result.flash_run.get('transcript', [])); lines.append('')
    if result.recovery_run:
        lines.extend(result.recovery_run.get('transcript', [])); lines.append('')
    log_path.write_text('\n'.join(lines), encoding='utf-8')
    log('FLASH', f'Wrote flash JSON report: {json_path}')
    log('FLASH', f'Wrote flash FPT transcript: {log_path}')
    return json_path, log_path


def _write_flash_report(result: FlashResult):
    # A logging/storage failure after a successful flash must not cause a
    # recovery write. Hardware outcome is independent of report persistence.
    try:
        return _write_flash_report_unchecked(result)
    except Exception as exc:
        log('REPORT ERROR', f'Could not save flash report: {type(exc).__name__}: {exc}')
        return None


def _flash_result(*, success: bool, target: Path, backup: BackupResult | None, flash_run: FptRunResult | None,
                  recovery_attempted: bool, recovery_success: bool | None, recovery_run: FptRunResult | None,
                  error: str | None) -> FlashResult:
    backup_runs = None
    if backup is not None:
        backup_runs = {'first': backup.first_run, 'second': backup.second_run}
    return FlashResult(
        success=success,
        target_path=str(target),
        backup_path=backup.backup_path if backup else None,
        backup_sha256=backup.sha256 if backup else None,
        flash_run=asdict(flash_run) if flash_run else None,
        recovery_attempted=recovery_attempted,
        recovery_success=recovery_success,
        recovery_run=asdict(recovery_run) if recovery_run else None,
        backup_runs=backup_runs,
        error=error,
    )


def flash_bios_with_recovery(target: Path, callback: EventCallback | None = None) -> FlashResult:
    target = target.resolve()
    if needs_elevation():
        try:
            return FlashResult(**run_elevated('flash', target, callback))
        except Exception as exc:
            result = _flash_result(success=False, target=target, backup=None,
                                   flash_run=None, recovery_attempted=False,
                                   recovery_success=None, recovery_run=None,
                                   error=f'{type(exc).__name__}: {exc}')
            _write_flash_report(result)
            return result
    log('FLASH', f'Flash workflow requested for target: {target}')
    if target.is_file():
        log('FLASH', f'Target size={target.stat().st_size} SHA-256={sha256_file(target)}')
    backup = create_verified_backup(callback)
    if not backup.success:
        result = _flash_result(success=False, target=target, backup=backup, flash_run=None,
                               recovery_attempted=False, recovery_success=None, recovery_run=None, error=backup.error)
        _write_flash_report(result)
        return result
    backup_path = Path(backup.backup_path)
    if not target.is_file():
        result = _flash_result(success=False, target=target, backup=backup, flash_run=None,
                               recovery_attempted=False, recovery_success=None, recovery_run=None,
                               error='Target ROM does not exist.')
        _write_flash_report(result)
        return result
    if target.stat().st_size != backup.size:
        result = _flash_result(success=False, target=target, backup=backup, flash_run=None,
                               recovery_attempted=False, recovery_success=None, recovery_run=None,
                               error=f'Target ROM size ({target.stat().st_size}) does not match the installed flash image size ({backup.size}).')
        _write_flash_report(result)
        return result

    _emit(callback, type='status', text=f'Flashing {target.name}…')
    flash_run = None
    recovery_run = None
    recovery_attempted = False
    try:
        flash_run = _run_fpt(['-f', str(target)], 'Flash target ROM', callback)
        if flash_run.success:
            result = _flash_result(success=True, target=target, backup=backup, flash_run=flash_run,
                                   recovery_attempted=False, recovery_success=None, recovery_run=None, error=None)
            _emit(callback, type='status', text='BIOS flash completed and verified successfully.')
            _write_flash_report(result)
            return result

        if not flash_run.destructive_started:
            result = _flash_result(success=False, target=target, backup=backup, flash_run=flash_run,
                                   recovery_attempted=False, recovery_success=None, recovery_run=None,
                                   error='FPT failed before erase/programming began. The installed BIOS was not modified by this attempt.')
            _emit(callback, type='status', text='Flash failed before any erase/programming stage. Automatic recovery is not needed.')
            _write_flash_report(result)
            return result

        _emit(callback, type='status', text='Flash failed after erase/programming began. Attempting automatic recovery from the verified backup…')
        recovery_attempted = True
        recovery_run = _run_fpt(['-f', str(backup_path)], 'Automatic recovery: restore backup', callback)
        if recovery_run.success:
            result = _flash_result(success=False, target=target, backup=backup, flash_run=flash_run,
                                   recovery_attempted=True, recovery_success=True, recovery_run=recovery_run,
                                   error='The requested flash failed, but the original BIOS backup was restored and verified successfully.')
            _emit(callback, type='status', text='Original BIOS backup restored and verified successfully.')
        else:
            result = _flash_result(success=False, target=target, backup=backup, flash_run=flash_run,
                                   recovery_attempted=True, recovery_success=False, recovery_run=recovery_run,
                                   error='The requested flash failed and automatic recovery also failed. Do not reboot or power off the computer. Preserve the backup ROM and use an appropriate recovery method.')
            _emit(callback, type='status', text='AUTOMATIC RECOVERY FAILED — do not reboot or power off the computer.')
        _write_flash_report(result)
        return result
    except Exception as exc:
        destructive = bool(flash_run and flash_run.destructive_started)
        if destructive and not recovery_attempted:
            try:
                _emit(callback, type='status', text='A flash exception occurred after erase/programming began. Attempting automatic recovery…')
                recovery_attempted = True
                recovery_run = _run_fpt(['-f', str(backup_path)], 'Automatic recovery: restore backup', callback)
            except Exception:
                recovery_run = None
        recovery_success = recovery_run.success if recovery_run else (False if recovery_attempted else None)
        if recovery_attempted and not recovery_success:
            _emit(callback, type="status", text=f"DO NOT REBOOT OR POWER OFF — recovery failed. Backup: {backup_path}")
        result = _flash_result(success=False, target=target, backup=backup, flash_run=flash_run,
                               recovery_attempted=recovery_attempted, recovery_success=recovery_success, recovery_run=recovery_run,
                               error=f'{type(exc).__name__}: {exc}')
        _write_flash_report(result)
        return result
