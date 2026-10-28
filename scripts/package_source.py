"""Make a reproducible source ZIP from the checked source inventory only."""
from pathlib import Path, PurePosixPath
from hashlib import sha256
from io import BytesIO
import os
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from x99toolkit import __version__


def package_source(root=ROOT):
    entries = []
    for line in (root / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        expected, relative = line.split('  ', 1)
        name = PurePosixPath(relative)
        if name.is_absolute() or '..' in name.parts or '\\' in relative or ':' in relative:
            raise ValueError('Invalid source-inventory path')
        path = root / relative
        if path.suffix.casefold() in ('.exe', '.dll') or name.parts[0] in ('dist', 'build', '.build-venv', 'LOGS'):
            raise ValueError('External/generated file in source inventory: ' + relative)
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Source hash mismatch: ' + relative)
        entries.append((relative, path.read_bytes()))
    entries.append(('SHA256SUMS.txt', (root / 'SHA256SUMS.txt').read_bytes()))
    name = f'SenorX99-v{__version__}_source'
    destination = root / (name + '.zip')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED, allowZip64=False) as archive:
        for relative, data in sorted(entries):
            info = zipfile.ZipInfo(name + '/' + relative, date_time=(2026, 10, 28, 0, 0, 0))
            info.create_system = 0
            archive.writestr(info, data)
    # Publish only the fully closed ZIP, including its central directory.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(buffer.getvalue()); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    destination.with_suffix('.zip.sha256').write_text(
        sha256(destination.read_bytes()).hexdigest() + '  ' + destination.name + '\n', encoding='utf-8')
    return destination


if __name__ == '__main__':
    print(package_source())
