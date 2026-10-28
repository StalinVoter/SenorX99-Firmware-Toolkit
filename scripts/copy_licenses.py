"""Retain the notices distributed with the locked third-party packages."""
from importlib import metadata
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1] / 'dist' / 'licenses'
root.mkdir(parents=True, exist_ok=True)
for source in (Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE'):
    if source.is_file():
        shutil.copy2(source, root / 'Python-LICENSE.txt')
        break
else:
    raise RuntimeError('The official Python license file is missing.')
shutil.copytree(Path(__file__).resolve().parents[1] / 'licenses', root / 'notices', dirs_exist_ok=True)
for package in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'pyinstaller', 'Pillow'):
    distribution = metadata.distribution(package)
    for relative in distribution.files or []:
        if any('licens' in component.casefold() or 'copying' in component.casefold() for component in relative.parts):
            path = Path(distribution.locate_file(relative))
            if path.is_file():
                destination = root / package / Path(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
    destination = root / package / 'PACKAGE-METADATA.txt'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(distribution.read_text('METADATA') or '', encoding='utf-8')
