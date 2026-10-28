"""Exact-file inventory and verified, atomic downloads of external tools."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import os
from pathlib import Path
import tempfile
import time
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from .constants import (
    FPT_VERSION, FPT_FILE_SHA256, FPT_SOURCE_COMMIT,
    UEFIREPLACE_VERSION, UEFIREPLACE_SHA256, UEFIREPLACE_DOWNLOAD_URL,
    UEFIREPLACE_ARCHIVE_SHA256,
)
from .sessionlog import log
from .tools import app_directory, sha256_file


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    sha256: str
    size: int
    url: str
    download_sha256: str
    download_size: int
    archive_member: str | None = None
    byte_patches: tuple[tuple[int, int, int], ...] = ()


_FPT_BASE = (
    f'https://raw.githubusercontent.com/CE1CECL/IntelCSTools/{FPT_SOURCE_COMMIT}/'
    'ME%20System%20Tools%20v9.1%20r7/Flash%20Programming%20Tool/WIN64/'
)
TOOL_SPECS = (
    ToolSpec('UEFIReplace.exe', UEFIREPLACE_VERSION, UEFIREPLACE_SHA256, 4898304,
             UEFIREPLACE_DOWNLOAD_URL, UEFIREPLACE_ARCHIVE_SHA256, 2059168, 'UEFIReplace.exe'),
    *(ToolSpec(name, FPT_VERSION, FPT_FILE_SHA256[name], size, _FPT_BASE + name,
               FPT_FILE_SHA256[name], size)
      for name, size in (('fptw64.exe', 1325016), ('pmxdll32e.DLL', 114176), ('idrvdll32e.DLL', 61952))),
    ToolSpec('mmtool_a5.exe', '', '51a85a663b1cb819508e48fd650180e6c79afd06fb458dd1357508fd9578fc23',
             16019456,
             'https://raw.githubusercontent.com/Harvester57/UEFI-Updater-Tool/5125d0eb6e81f365b3660a29805aee751b8d5bdc/mmtool_a5.exe',
             'bbc3e75905997ddc05c523e57a72e49bbfcaf84dca64e460f10f8553b7fda9ee', 16019456,
             byte_patches=((0x1814F, 0x09, 0x00), (0x3C721, 0x0E, 0x00), (0x3C724, 0x80, 0x00))),
    ToolSpec('ChangeLogo.exe', '', 'aacf50e75f8c954047e93986315fc25cfa4619697c2f32f04889646c59fcdbd3',
             16418304,
             'https://raw.githubusercontent.com/scx567888/AMI-ChangeLogo/ef5125d6732a2d1b4f6dbe28c1439939c4aadafe/ChangeLogo.exe',
             'aacf50e75f8c954047e93986315fc25cfa4619697c2f32f04889646c59fcdbd3', 16418304),
)
TOOLS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


@dataclass(frozen=True)
class ToolGroup:
    name: str
    label: str
    files: tuple[str, ...]


FPT_FILES = ('fptw64.exe', 'pmxdll32e.DLL', 'idrvdll32e.DLL')
TOOL_GROUPS = (
    ToolGroup('UEFIReplace.exe', f'UEFIReplace {UEFIREPLACE_VERSION}', ('UEFIReplace.exe',)),
    ToolGroup('fpt', f'Intel FPT {FPT_VERSION} (3 files)', FPT_FILES),
    ToolGroup('mmtool_a5.exe', 'MMTool', ('mmtool_a5.exe',)),
    ToolGroup('ChangeLogo.exe', 'ChangeLogo', ('ChangeLogo.exe',)),
)
GROUPS_BY_NAME = {group.name: group for group in TOOL_GROUPS}


def scan_tool_groups(folder: Path | None = None) -> list[dict]:
    statuses = {item['name']: item for item in scan_tools(folder)}
    groups = []
    for group in TOOL_GROUPS:
        members = [statuses[name] for name in group.files]
        pending = [item for item in members if item['state'] != 'ready']
        if not pending:
            detail = 'Ready'
        elif len(members) == 1:
            detail = pending[0]['detail']
        else:
            ready_count = len(members) - len(pending)
            detail = f'{ready_count}/{len(members)} files ready. ' + '; '.join(
                item['name'] + ': ' + item['detail'] for item in pending)
        groups.append(dict(name=group.name, label=group.label, ready=not pending,
                           detail=detail, files=members))
    return groups


def prepare_tool_bytes(spec: ToolSpec, data: bytes) -> bytes:
    """Reproduce only the exact previously validated installed utility."""
    if spec.byte_patches:
        if len(data) != spec.download_size or sha256(data).hexdigest() != spec.download_sha256:
            raise ValueError('The utility source failed its pinned size/SHA-256 check.')
        prepared = bytearray(data)
        for offset, expected, replacement in spec.byte_patches:
            if offset >= len(prepared) or prepared[offset] != expected:
                raise ValueError('The utility does not match the required preparation bytes.')
            prepared[offset] = replacement
        data = bytes(prepared)
    if len(data) != spec.size or sha256(data).hexdigest() != spec.sha256:
        raise RuntimeError('The tool failed its exact size/SHA-256 check. No file was installed.')
    return data


def tool_status(spec: ToolSpec, folder: Path) -> dict:
    path = folder / spec.name
    try:
        if not path.exists():
            state, detail = 'missing', 'Missing'
        elif not path.is_file():
            state, detail = 'unreadable', 'Path is not a file'
        elif path.stat().st_size == spec.size and sha256_file(path) == spec.sha256:
            state, detail = 'ready', 'Exact required file verified'
        else:
            state, detail = 'wrong', 'Incorrect version or modified file'
    except OSError as exc:
        state, detail = 'unreadable', f'Cannot read file: {exc}'
    return {'name': spec.name, 'version': spec.version, 'state': state,
            'detail': detail, 'path': str(path)}


def scan_tools(folder: Path | None = None) -> list[dict]:
    folder = folder if folder is not None else app_directory() / 'tools'
    statuses = [tool_status(spec, folder) for spec in TOOL_SPECS]
    for status in statuses:
        log('TOOL INVENTORY', f"{status['path']}: {status['state']}; {status['detail']}")
    return statuses


def _download(spec: ToolSpec, progress: Callable[[str], None]) -> bytes:
    progress('Downloading…')
    log('TOOL DOWNLOAD', f'{spec.name}: {spec.url}')
    request = Request(spec.url, headers={'User-Agent': 'SenorX99', 'Accept-Encoding': 'identity'})
    deadline = time.monotonic() + 120
    with urlopen(request, timeout=20) as response:
        if urlsplit(response.geturl()).scheme.lower() != 'https':
            raise RuntimeError('The download redirected to a non-HTTPS URL.')
        chunks = []
        size = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('The download exceeded two minutes. Please try again.')
            block = response.read(65536)
            if not block:
                break
            size += len(block)
            if size > spec.download_size:
                raise RuntimeError('The download is larger than the required file.')
            chunks.append(block)
    data = b''.join(chunks)
    if len(data) != spec.download_size or sha256(data).hexdigest() != spec.download_sha256:
        raise RuntimeError('The download failed its exact size/SHA-256 check. No file was installed.')
    return data


def install_tool(name: str, folder: Path | None = None,
                 progress: Callable[[str], None] | None = None) -> Path:
    spec = TOOLS_BY_NAME[name]  # The GUI can select only these fixed downloads.
    folder = folder if folder is not None else app_directory() / 'tools'
    notify = progress if progress is not None else lambda message: None
    destination = folder / spec.name
    if tool_status(spec, folder)['state'] == 'ready':
        notify('Already installed and verified')
        return destination
    if not spec.url:
        raise ValueError(f'{name} requires verified local import. Select the original executable or an existing tools ZIP.')
    temporary = None
    try:
        data = _download(spec, notify)
        notify('Verifying…')
        if spec.archive_member is not None:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = [entry for entry in archive.infolist() if entry.filename == spec.archive_member]
                if len(entries) != 1 or entries[0].file_size != spec.size:
                    raise RuntimeError('The archive does not contain the exact required executable.')
                data = archive.read(entries[0])  # Never extract paths from an archive.
        data = prepare_tool_bytes(spec, data)
        folder.mkdir(parents=True, exist_ok=True)
        notify('Installing…')
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.x99-download-', delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if sha256_file(temporary) != spec.sha256:
            raise RuntimeError('The downloaded file could not be written correctly.')
        os.replace(temporary, destination)
        log('TOOL DOWNLOAD', f'Installed {destination}; SHA-256={spec.sha256}')
        notify('Installed and verified')
        return destination
    except Exception as exc:
        log('TOOL DOWNLOAD FAILED', f'{name}: {type(exc).__name__}: {exc}')
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install_tool_group(name: str, folder: Path | None = None,
                       progress: Callable[[str], None] | None = None) -> tuple[Path, ...]:
    group = GROUPS_BY_NAME[name]
    notify = progress or (lambda message: None)
    installed = []
    for member in group.files:
        def report(message, member=member):
            notify(member + ': ' + message if len(group.files) > 1 else message)
        installed.append(install_tool(member, folder, report))
    return tuple(installed)


def import_tool(name: str, source: Path, folder: Path | None = None,
                progress: Callable[[str], None] | None = None) -> Path:
    """Import a pinned executable, or one matching basename from a tools ZIP."""
    spec = TOOLS_BY_NAME[name]
    folder = folder if folder is not None else app_directory() / 'tools'
    notify = progress or (lambda message: None)
    notify('Verifying local file…')
    if source.suffix.casefold() == '.zip':
        with zipfile.ZipFile(source) as archive:
            entries = [e for e in archive.infolist()
                       if e.filename.replace('\\', '/').split('/')[-1].casefold() == name.casefold()]
            if len(entries) != 1 or entries[0].file_size != spec.size:
                raise ValueError('The ZIP must contain exactly one matching tool with the required size.')
            # Read only the chosen member; never write archive-controlled paths.
            data = archive.read(entries[0])
    else:
        with source.open('rb') as stream:
            data = stream.read(spec.size + 1)
    if len(data) != spec.size or sha256(data).hexdigest() != spec.sha256:
        raise ValueError(f'{name} does not match the required size and SHA-256. The installed file was preserved.')
    folder.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        notify('Installing verified file…')
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.senor-import-', delete=False) as output:
            temporary = Path(output.name)
            output.write(data); output.flush(); os.fsync(output.fileno())
        if sha256_file(temporary) != spec.sha256:
            raise OSError('The imported tool could not be written correctly.')
        os.replace(temporary, folder / name)
        if tool_status(spec, folder)['state'] != 'ready':
            raise OSError('The installed tool failed its final verification.')
        log('TOOL IMPORT', f'{name}: verified and installed from {source}')
        return folder / name
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
