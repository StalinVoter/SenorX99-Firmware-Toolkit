"""External UTF-8 JSON catalogs with stable IDs and an English fallback."""
from __future__ import annotations
import json
from pathlib import Path
import re
from string import Formatter
import sys


def language_directory():
    # Source runs always use the source catalog; release catalogs live beside EXE.
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / 'languages'
    return Path(__file__).resolve().parent.parent / 'languages'


def _read(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('strings'), dict):
        raise ValueError('Invalid language catalog')
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in data['strings'].items()):
        raise ValueError('Language entries must be text')
    if not isinstance(data.get('name'), str) or not data['name']:
        raise ValueError('Language name missing')
    return data


def placeholders(text):
    fields = set()
    for _, name, spec, conversion in Formatter().parse(text):
        if name is not None:
            if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name) or spec or conversion:
                raise ValueError('Use simple named placeholders')
            fields.add(name)
    return fields


class Catalog:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else language_directory()
        self.english = _read(self.directory / 'en.json')['strings']
        for value in self.english.values():
            placeholders(value)
        self.strings = self.english
        self.code = 'en'

    def available(self):
        result = []
        for path in sorted(self.directory.glob('*.json')):
            if re.fullmatch(r'[a-zA-Z0-9_-]+', path.stem):
                try:
                    data = _read(path)
                    result.append((path.stem, data['name']))
                except (ValueError, OSError):
                    continue
        return result

    def select(self, code):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', code):
            raise ValueError('Invalid language code')
        data = _read(self.directory / (code + '.json'))
        for key, value in data['strings'].items():
            if key in self.english and placeholders(value) != placeholders(self.english[key]):
                raise ValueError('Language placeholder mismatch: ' + key)
        self.code = code
        self.strings = {**self.english, **data['strings']}

    def text(self, key, **values):
        # Fail visibly in development/build tests instead of shipping blank labels.
        return self.strings[key].format_map(values)
