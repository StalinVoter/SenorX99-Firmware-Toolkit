import ast
from pathlib import Path
import pytest
from x99toolkit.flash_warning import confirmation_key
from x99toolkit.localization import Catalog

ROOT=Path(__file__).resolve().parents[1]

def test_english_flash_warnings_preserve_exact_legacy_wording():
 values={}
 for node in ast.parse((ROOT/'x99toolkit/fpt_gui.py').read_text()).body:
  if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('SECUREBOOT_FLASH_WARNING','INTERRUPTION_WARNING'):
   values[node.targets[0].id]=ast.literal_eval(node.value)
 c=Catalog()
 assert c.text('fpt.confirm_secureboot')==values['SECUREBOOT_FLASH_WARNING']
 assert c.text('fpt.confirm',path='board.ROM').endswith(values['INTERRUPTION_WARNING'])

@pytest.mark.parametrize('other',['tbu','optimized','overclock','splash'])
def test_other_modifications_use_general_warning_even_for_own_dump(other):
 assert confirmation_key({'secureboot':True,other:True},'own',{'own'})=='fpt.confirm'

def test_low_risk_wording_requires_secureboot_only_and_this_boards_own_dump():
 assert confirmation_key({'secureboot':True},'own',{'own'})=='fpt.confirm_secureboot'
 assert confirmation_key({'secureboot':True},'other',{'own'})=='fpt.confirm'
 assert confirmation_key({},'own',{'own'})=='fpt.confirm'
 assert confirmation_key(None,'own',{'own'})=='fpt.confirm'
