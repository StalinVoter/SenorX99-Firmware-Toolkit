"""Read-only bundled-resource validation; runtime tools are optional at build time."""
from pathlib import Path, PurePosixPath
from hashlib import sha256
import argparse
import json


def validate(home: Path):
    manifest = json.loads((home / 'resource-manifest.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['files'].items():
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or '\\' in relative or ':' in relative:
            raise ValueError('Invalid bundled resource path: ' + relative)
        local = home.joinpath(*path.parts)
        if not local.is_file() or sha256(local.read_bytes()).hexdigest() != expected:
            raise ValueError('Missing or corrupt bundled resource: ' + relative)
    from x99toolkit.secureboot import load_bundled_donors
    load_bundled_donors(home)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    validate(args.home.resolve())
    print('Bundled firmware resources verified. External utilities can be installed after extraction.')
