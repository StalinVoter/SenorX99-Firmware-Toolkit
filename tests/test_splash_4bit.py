from pathlib import Path

import unittest
import tempfile
from PIL import Image

from x99toolkit.splash import (
    _same_compatibility_spec,
    inspect_bmp,
    normalize_replacement_bmp,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'tests' / 'fixtures' / 'xd3-4bit-original.bmp'
REPLACEMENT = ROOT / 'assets' / 'SenorSpace-XD3-4bit.bmp'


def test_real_xd3_4bit_replacement_preserves_full_resolution_and_pixels(tmp_path):
    target = tmp_path / 'accepted.bmp'
    result = normalize_replacement_bmp(REPLACEMENT, ORIGINAL, target)
    assert target.read_bytes() == REPLACEMENT.read_bytes()
    spec = inspect_bmp(target)
    assert (spec.width, spec.height, spec.bits_per_pixel, spec.actual_size) == (800, 600, 4, 240118)
    assert not _same_compatibility_spec(spec, inspect_bmp(ORIGINAL))
    assert result['operation'].startswith('copied unchanged')
    with Image.open(target) as image:
        assert len(image.getcolors()) <= 16
        assert image.convert('RGB').getpixel((0, 0)) == (0, 0, 0)


def test_4bit_original_still_rejects_mismatched_8bit_source(tmp_path):
    target = tmp_path / 'refused.bmp'
    with unittest.TestCase().assertRaisesRegex(ValueError, '4-bit replacement must'):
        normalize_replacement_bmp(ROOT / 'assets' / 'SenorSpace.bmp', ORIGINAL, target)
    assert not target.exists()


def test_4bit_original_rejects_incorrect_file_size(tmp_path):
    source = tmp_path / 'different-header.bmp'
    data = bytearray(REPLACEMENT.read_bytes())
    data[2:6] = (42).to_bytes(4, 'little')
    source.write_bytes(data)
    target = tmp_path / 'refused.bmp'
    with unittest.TestCase().assertRaisesRegex(ValueError, '4-bit replacement must'):
        normalize_replacement_bmp(source, ORIGINAL, target)
    assert not target.exists()


def test_original_8bit_source_still_passes_unchanged(tmp_path):
    source = ROOT / 'assets' / 'SenorSpace.bmp'
    target = tmp_path / 'original8.bmp'
    normalize_replacement_bmp(source, source, target)
    assert target.read_bytes() == source.read_bytes()


def test_8bit_normalization_still_matches_original_header(tmp_path):
    original = ROOT / 'assets' / 'SenorSpace.bmp'
    target = tmp_path / 'normalized8.bmp'
    normalize_replacement_bmp(REPLACEMENT, original, target)
    assert _same_compatibility_spec(inspect_bmp(target), inspect_bmp(original))
    with Image.open(target) as image:
        assert image.size == (800, 600)


class SplashRegressionTests(unittest.TestCase):
    pass


def _as_case(function):
    def run(self):
        with tempfile.TemporaryDirectory() as folder:
            function(Path(folder))
    return run


for _name, _function in list(globals().items()):
    if _name.startswith('test_') and callable(_function):
        setattr(SplashRegressionTests, _name, _as_case(_function))
        del globals()[_name]

if __name__ == '__main__':
    unittest.main()
