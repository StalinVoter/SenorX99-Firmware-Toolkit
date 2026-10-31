"""Prepare all Toolkit resources beside the finished executables."""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def stage(root=ROOT):
    from validate_build_inputs import validate
    manifest = validate(root)
    dist = root / 'dist'; dist.mkdir(exist_ok=True)
    for relative in manifest['files']:
        source = root / relative
        target = dist / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(root / 'resource-manifest.json', dist / 'resource-manifest.json')
    shutil.copy2(root / 'README.md', dist / 'README.txt')
    shutil.copy2(root / 'languages/README.md', dist / 'languages/README.md')
    for name in ('START-HERE.md', 'RELEASE-NOTES-v0.931.md', 'TEST-ELEVATED-HELPER.cmd'):
        shutil.copy2(root / name, dist / name)
    (dist / 'tools').mkdir(exist_ok=True)
    shutil.copy2(root / 'tools/README.txt', dist / 'tools/README.txt')
    # Developer-only copies are allowed locally. Public staging excludes them.
    from x99toolkit.tool_inventory import TOOL_SPECS
    for spec in TOOL_SPECS:
        path = root / 'tools' / spec.name
        if path.is_file():
            shutil.copy2(path, dist / 'tools' / spec.name)
    shutil.copy2(root / 'fpt_support/fparts.txt', dist / 'tools/fparts.txt')


if __name__ == '__main__':
    stage()
