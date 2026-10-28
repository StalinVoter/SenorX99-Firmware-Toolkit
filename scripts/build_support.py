"""Validate and seal PyInstaller's actual Windows native dependency collection."""
from __future__ import annotations
from functools import lru_cache
from hashlib import sha256
from importlib import metadata, util
import json
from pathlib import Path, PurePosixPath
import re
import struct
import sys

import pefile
from x99toolkit import __version__

ROOT = Path(__file__).resolve().parents[1]
MSVC = re.compile(r'^(?:msvcp140(?:_[a-z0-9_]+)?|vcruntime140(?:_[a-z0-9_]+)?|concrt140|vcamp140|vccorlib140|vcomp140)\.dll$', re.I)
PYTHON_VERSION = (3, 13, 14)
PINS = {
    'PySide6': '6.10.3', 'PySide6_Essentials': '6.10.3',
    'PySide6_Addons': '6.10.3', 'shiboken6': '6.10.3',
    'pyinstaller': '6.22.3', 'pyinstaller-hooks-contrib': '2026.9',
    'pefile': '2024.8.26', 'Pillow': '12.0.0',
}


def check_environment() -> None:
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('Build using 64-bit Windows Python.')
    if sys.version_info[:3] != PYTHON_VERSION:
        raise RuntimeError(f'Build requires Python {PYTHON_VERSION}, got {sys.version_info[:3]}')
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Build must run inside the isolated build virtual environment.')
    for package, version in PINS.items():
        if metadata.version(package) != version:
            raise RuntimeError(f'{package} must be exactly {version}')


@lru_cache(maxsize=None)
def native_info(source: str) -> dict:
    # QtGui legitimately exports over 8,192 named functions. pefile's default
    # cap otherwise truncates names and produces false missing-export reports.
    image = pefile.PE(source, fast_load=True, max_symbol_exports=1000000)
    try:
        if image.FILE_HEADER.Machine != 0x8664:
            raise RuntimeError(f'Non-x64 native file in the Windows x64 build: {source}')
        image.parse_data_directories(directories=[1, 0, 13])
        imports: dict[str, list[bytes | int]] = {}
        for directory in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT'):
            for entry in getattr(image, directory, []):
                dll = entry.dll.decode('ascii').casefold()
                imports.setdefault(dll, []).extend(i.name if i.name is not None else i.ordinal for i in entry.imports)
        exports = set()
        for symbol in getattr(getattr(image, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', []):
            exports.add(symbol.ordinal)
            if symbol.name:
                exports.add(symbol.name)
        directory = getattr(image, 'DIRECTORY_ENTRY_EXPORT', None)
        if directory is not None and sum(isinstance(s, bytes) for s in exports) != directory.struct.NumberOfNames:
            raise RuntimeError(f'Incomplete export-table inspection: {source}')
        return {'imports': imports, 'exports': exports}
    finally:
        image.close()


def validate_imports(files: dict[str, Path]) -> dict[str, str]:
    by_name: dict[str, str] = {}
    digests: dict[str, str] = {}
    for relative, source in files.items():
        name = PurePosixPath(relative).name.casefold()
        digest = sha256(source.read_bytes()).hexdigest()
        if name in by_name and digests[name] != digest:
            raise RuntimeError(f'Conflicting private DLL basename: {name}')
        by_name.setdefault(name, relative)
        digests[name] = digest
    for relative, source in files.items():
        for dll, imports in native_info(str(source))['imports'].items():
            provider = by_name.get(dll)
            if provider is None:
                if MSVC.fullmatch(dll) or dll.startswith(('qt6', 'shiboken6', 'pyside6', 'python3')):
                    raise RuntimeError(f'{relative} requires an unbundled private DLL: {dll}')
                continue  # OS DLLs are validated by the real Windows smoke test.
            exports = native_info(str(files[provider]))['exports']
            missing = [symbol for symbol in imports if symbol not in exports]
            if missing:
                raise RuntimeError(f'{relative} requires missing exports from {provider}: {missing!r}')
    return by_name


def preload_order(files: dict[str, Path], by_name: dict[str, str], roots: list[str]) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()
    seen: set[str] = set()
    def visit(relative: str):
        if relative in seen or relative in visiting:
            return  # Windows resolves genuine native import cycles itself.
        visiting.add(relative)
        for dll in native_info(str(files[relative]))['imports']:
            if dll in by_name:
                visit(by_name[dll])
        visiting.remove(relative)
        seen.add(relative)
        if relative.lower().endswith('.dll'):
            ordered.append(relative)
    for root in roots:
        visit(root)
    return ordered


def seal_collection(binaries: list, runtime_source: Path, kind: str, versions: dict) -> tuple[list, dict]:
    # The pinned official Shiboken wheel carries a coherent Microsoft runtime
    # set. Use that single source, not whichever System32/PATH copy Analysis saw.
    canonical = {p.name.casefold(): p for p in runtime_source.glob('*.dll') if MSVC.fullmatch(p.name)}
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll'):
        if name not in canonical:
            raise RuntimeError(f'Pinned runtime wheel is missing {name}')
    accepted = []
    for destination, source, typ in binaries:
        destination = destination.replace('\\', '/')
        name = PurePosixPath(destination).name.casefold()
        # On supported Windows 10+ systems the UCRT and API-set contracts are
        # OS components. Windows can load them before Python's startup hook;
        # they must not be inventoried as private app-local runtime copies.
        if name in ('ucrtbase.dll', 'msvcp_win.dll') or name.startswith(('api-ms-win-', 'ext-ms-win-')):
            continue
        if MSVC.fullmatch(name):
            if name not in canonical:
                raise RuntimeError(f'No pinned source for runtime DLL: {destination}')
            continue
        accepted.append((destination, source, typ))
    accepted.extend((name, str(path), 'BINARY') for name, path in sorted(canonical.items()))
    files = {dest: Path(src) for dest, src, _ in accepted if dest.lower().endswith(('.dll', '.pyd'))}
    by_name = validate_imports(files)
    roots = [relative for name, relative in by_name.items() if MSVC.fullmatch(name)]
    if kind == 'gui':
        for name in ('qtcore.pyd', 'qtgui.pyd', 'qtwidgets.pyd', 'qwindows.dll'):
            if name not in by_name:
                raise RuntimeError(f'GUI native dependency was not collected: {name}')
            roots.append(by_name[name])
    manifest = {
        'schema': 1, 'app_version': __version__, 'kind': kind, 'versions': versions,
        'files': {relative: {'sha256': sha256(path.read_bytes()).hexdigest()} for relative, path in sorted(files.items())},
        'preload': preload_order(files, by_name, roots),
    }
    return accepted, manifest


def seal_analysis(analysis, kind: str) -> Path:
    spec = util.find_spec('shiboken6')
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError('Pinned Shiboken runtime package is missing.')
    versions = {'Python': '.'.join(map(str, PYTHON_VERSION)), **PINS}
    binaries, manifest = seal_collection(analysis.binaries, Path(next(iter(spec.submodule_search_locations))), kind, versions)
    analysis.binaries = binaries
    destination = ROOT / 'build' / f'runtime-manifest-{kind}.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
    analysis.datas.append(('runtime-manifest.json', str(destination), 'DATA'))
    return destination
