"""Inspect the finished CArchive, not just Analysis's proposed file list."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import sys
from xml.etree import ElementTree
from PyInstaller.archive.readers import CArchiveReader
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def verify(exe: Path) -> dict:
    archive = CArchiveReader(str(exe))
    manifest = json.loads(archive.extract('runtime-manifest.json'))
    if manifest.get('kind') in ('gui', 'cli'):
        import pefile
        from scripts.build_support import MSVC
        image = pefile.PE(str(exe), fast_load=True)
        try:
            if image.FILE_HEADER.Machine != 0x8664:
                raise RuntimeError('The finished executable is not Windows x64.')
            image.parse_data_directories(directories=[1, 2])
            levels = []
            for resource in getattr(getattr(image, 'DIRECTORY_ENTRY_RESOURCE', None), 'entries', []):
                if resource.id != 24:
                    continue
                for entry in resource.directory.entries:
                    for language in entry.directory.entries:
                        data = language.data.struct
                        xml = ElementTree.fromstring(image.get_data(data.OffsetToData, data.Size))
                        levels.extend(node.attrib.get('level') for node in xml.iter()
                                      if node.tag.split('}')[-1] == 'requestedExecutionLevel')
            if levels != ['asInvoker']:
                raise RuntimeError(f'The executable must run asInvoker for Explorer drag-and-drop: {levels}')
            for entry in getattr(image, 'DIRECTORY_ENTRY_IMPORT', []):
                name = entry.dll.decode('ascii')
                if MSVC.fullmatch(name) or name.casefold().startswith(('qt6', 'python3', 'pyside6', 'shiboken6')):
                    raise RuntimeError(f'Bootloader imports private runtime before startup validation: {name}')
        finally:
            image.close()
        scripts = [name for name, info in archive.toc.items() if info[-1] == 's']
        if 'runtime_first' not in scripts:
            raise RuntimeError('The first runtime validation hook is absent from the EXE.')
        custom = scripts.index('runtime_first')
        if any(name.startswith('pyi_rth_') for name in scripts[:custom]):
            raise RuntimeError('A PyInstaller runtime hook executes before private runtime validation.')
    native = {name.replace('\\', '/'): name for name in archive.toc if name.lower().endswith(('.dll', '.pyd'))}
    if set(native) != set(manifest['files']):
        raise RuntimeError(f'Actual EXE native files differ from the manifest: {exe}')
    for relative, info in manifest['files'].items():
        data = archive.extract(native[relative])
        if sha256(data).hexdigest() != info['sha256']:
            raise RuntimeError(f'Actual EXE DLL hash mismatch: {relative}')
    return manifest


if __name__ == '__main__':
    result = verify(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Actual EXE native manifest verified: {sys.argv[1]}')
