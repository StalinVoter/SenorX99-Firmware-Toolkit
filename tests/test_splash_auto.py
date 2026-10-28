from pathlib import Path
import ast
import shutil
import tempfile
import unittest
from unittest.mock import patch

from x99toolkit import splash

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL4 = ROOT / 'tests/fixtures/xd3-4bit-original.bmp'
MASTER8 = ROOT / 'assets/SenorSpace.bmp'
ASSET4 = ROOT / 'assets/SenorSpace-XD3-4bit.bmp'


class AutoSplashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / 'assets').mkdir()
        for asset in (MASTER8, ASSET4):
            shutil.copy2(asset, self.home / 'assets' / asset.name)
        self.application = patch.object(splash, 'app_directory', return_value=self.home)
        self.application.start()

    def tearDown(self):
        self.application.stop()
        self.temp.cleanup()

    def test_automatic_selection_uses_real_4bit_header(self):
        target, info = splash.prepare_splash_replacement(None, ORIGINAL4, self.home / 'work', 'default')
        self.assertEqual(target.read_bytes(), ASSET4.read_bytes())
        self.assertEqual(info['selection_mode'], 'automatic SenorSpace')
        self.assertEqual(Path(info['selected_source']).name, ASSET4.name)

    def test_automatic_selection_preserves_original_8bit_asset(self):
        target, info = splash.prepare_splash_replacement(None, MASTER8, self.home / 'work', 'default')
        self.assertEqual(target.read_bytes(), MASTER8.read_bytes())
        self.assertEqual(Path(info['selected_source']).name, MASTER8.name)

    def test_mixed_entries_select_independent_variants(self):
        a, ai = splash.prepare_splash_replacement(None, ORIGINAL4, self.home / 'work', 'default')
        b, bi = splash.prepare_splash_replacement(None, MASTER8, self.home / 'work', 'multi-1')
        self.assertEqual(splash.inspect_bmp(a).bits_per_pixel, 4)
        self.assertEqual(splash.inspect_bmp(b).bits_per_pixel, 8)
        self.assertNotEqual(ai['selected_source'], bi['selected_source'])

    def test_explicit_custom_file_is_never_substituted(self):
        custom = self.home / 'custom.bmp'
        data = bytearray(ASSET4.read_bytes())
        data[54:58] = bytes((32, 64, 96, 0))
        custom.write_bytes(data)
        target, info = splash.prepare_splash_replacement(custom, ORIGINAL4, self.home / 'work', 'default')
        self.assertEqual(target.read_bytes(), custom.read_bytes())
        self.assertEqual(info['selection_mode'], 'custom image')

    def test_top_down_original_is_refused(self):
        unknown = self.home / 'unknown.bmp'
        data = bytearray(ORIGINAL4.read_bytes())
        data[22:26] = (-151).to_bytes(4, 'little', signed=True)
        unknown.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'bottom-up'):
            splash.select_splash_source(None, unknown)

    def test_full_replacement_uses_automatic_selection_and_verification(self):
        rom = self.home / 'input.rom'
        rom.write_bytes(b'original firmware model')
        output = self.home / 'output.rom'
        state = {}

        def extract(input_rom, target, tool, folder):
            source = state.get(str(input_rom), ORIGINAL4)
            shutil.copy2(source, target)
            return {'tool': {'diagnostic': 'modeled extract'}, 'rgb_sha256': splash._image_rgb_sha256(target)}

        def run(tool, args, folder, timeout=120):
            if '/t' in args:
                return {'stdout': 'Logo will fit into the image', 'diagnostic': 'modeled fit'}
            new_rom = Path(args[args.index('/o') + 1])
            old_rom = Path(args[args.index('/i') + 1])
            new_rom.write_bytes(b'updated!'+old_rom.read_bytes()[8:])
            state[str(new_rom)] = Path(args[args.index('/r') + 1])
            return {'diagnostic': 'modeled replace'}

        inventory = {'logos': [], 'tool': {'diagnostic': 'modeled inventory'}}
        with patch.object(splash, 'validate_changelogo_exe', return_value={}), \
             patch.object(splash, 'extract_splash', side_effect=extract), \
             patch.object(splash, 'list_multiple_splash_logos', return_value=inventory), \
             patch.object(splash, '_run_changelogo', side_effect=run):
            result = splash.replace_splash(rom, output, None, self.home/'ChangeLogo.exe', self.home/'work')
        self.assertTrue(output.is_file())
        self.assertIn(ASSET4.name, result['diagnostic'])
        self.assertEqual(result['normalization']['selection_mode'], 'automatic SenorSpace')
        self.assertEqual(result['verified']['rgb_sha256'], splash._image_rgb_sha256(ASSET4))

    def test_gui_default_and_cli_automatic_option_are_connected(self):
        # Check the wiring without requiring Qt or invoking Windows helpers.
        tree = ast.parse((ROOT/'x99toolkit/gui.py').read_text())
        window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Window')
        methods = {n.name: ast.unparse(n) for n in window.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('self.auto_splash_box.setChecked(True)', '\n'.join(methods.values()))
        self.assertIn('None if self.auto_splash_box.isChecked()', methods['start_build'])
        self.assertIn('self.auto_splash_box.setChecked(False)', methods['_browse_splash'])
        self.assertIn('LogoWorker', methods['_browse_splash'])
        self.assertIn('_logo_verified', methods['_custom_logo_finished'])
        self.assertIn('--senorspace', (ROOT/'x99toolkit/cli.py').read_text())

    def test_already_present_logos_are_verified_without_rewriting(self):
        for asset in (ASSET4, MASTER8):
            for numbered in (False, True):
                with self.subTest(asset=asset.name, numbered=numbered):
                    rom = self.home / 'already-present.rom'
                    rom.write_bytes(b'firmware model with the requested image already installed')
                    output = self.home / 'verified.rom'
                    def extract(input_rom, target, *args):
                        shutil.copy2(asset, target)
                        return {'tool': {'diagnostic': 'modeled extract'},
                                'rgb_sha256': splash._image_rgb_sha256(target)}
                    inventory = {'logos': ([{'number': 1}] if numbered else []),
                                 'tool': {'diagnostic': 'modeled inventory'}}
                    with patch.object(splash, 'validate_changelogo_exe', return_value={}), \
                         patch.object(splash, 'extract_splash', side_effect=extract), \
                         patch.object(splash, 'extract_multiple_splash', side_effect=extract), \
                         patch.object(splash, 'list_multiple_splash_logos', return_value=inventory), \
                         patch.object(splash, '_run_changelogo', side_effect=AssertionError('No rewrite needed')):
                        result = splash.replace_splash(rom, output, None, self.home/'ChangeLogo.exe', self.home/'work')
                    self.assertEqual(output.read_bytes(), rom.read_bytes())
                    self.assertTrue(result['already_present'])
                    self.assertTrue(result['default']['preexisting'])
                    self.assertEqual(len(result['final_multiple_verified']), int(numbered))

    def test_unchanged_rom_is_still_rejected_when_replacement_was_needed(self):
        rom = self.home / 'original.rom'; rom.write_bytes(b'firmware model with old logo')
        def extract(input_rom, target, *args):
            shutil.copy2(ORIGINAL4, target)
            return {'tool': {'diagnostic': 'modeled extract'},
                    'rgb_sha256': splash._image_rgb_sha256(target)}
        def run(tool, args, *rest):
            if '/t' in args:
                return {'stdout': 'Logo will fit into the image', 'diagnostic': 'modeled fit'}
            shutil.copy2(Path(args[args.index('/i')+1]), Path(args[args.index('/o')+1]))
            return {'diagnostic': 'modeled tool claiming success without a change'}
        inventory = {'logos': [], 'tool': {'diagnostic': 'modeled inventory'}}
        with patch.object(splash, 'validate_changelogo_exe', return_value={}), \
             patch.object(splash, 'extract_splash', side_effect=extract), \
             patch.object(splash, 'list_multiple_splash_logos', return_value=inventory), \
             patch.object(splash, '_run_changelogo', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'byte-identical.*replacing the default splash'):
                splash.replace_splash(rom, self.home/'output.rom', None, self.home/'ChangeLogo.exe', self.home/'work')


if __name__ == '__main__':
    unittest.main()
