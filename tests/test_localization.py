from pathlib import Path
import ast
import json
import pytest
from x99toolkit.localization import Catalog

ROOT=Path(__file__).resolve().parents[1]

def test_bundled_languages_and_all_literal_gui_ids_resolve():
 c=Catalog();assert c.available()==[('en','English'),('pt-BR','Português (Brasil)')]
 tree=ast.parse((ROOT/'x99toolkit/gui.py').read_text())
 for n in ast.walk(tree):
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('t','label','button','_message') and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str):
   assert n.args[0].value in c.english,n.args[0].value

def test_partial_translation_has_english_fallback_and_named_placeholders(tmp_path):
 import shutil
 shutil.copy2(ROOT/'languages/en.json',tmp_path/'en.json')
 (tmp_path/'pt.json').write_text(json.dumps({'schema':1,'name':'Português','strings':{'common.continue':'Continuar','common.source':'BIOS: {path}'}}))
 c=Catalog(tmp_path);c.select('pt')
 assert c.text('common.continue')=='Continuar'
 assert c.text('common.finish')=='Finish'
 assert c.text('common.source',path='C:/board.ROM')=='BIOS: C:/board.ROM'

def test_broken_catalog_keeps_previous_language(tmp_path):
 import shutil
 shutil.copy2(ROOT/'languages/en.json',tmp_path/'en.json');c=Catalog(tmp_path)
 (tmp_path/'bad.json').write_text(json.dumps({'schema':1,'name':'Broken','strings':{'common.source':'{wrong}'}}))
 with pytest.raises(ValueError,match='placeholder'):c.select('bad')
 assert c.code=='en' and c.text('common.continue')=='Continue'
 with pytest.raises(ValueError):c.select('../outside')


def test_brazilian_catalog_is_complete_and_preserves_all_placeholders():
 from x99toolkit.localization import placeholders
 c=Catalog();data=json.loads((ROOT/'languages/pt-BR.json').read_text())['strings']
 assert set(data)==set(c.english)
 for key in c.english:assert placeholders(data[key])==placeholders(c.english[key]),key
 c.select('pt-BR');assert c.text('common.continue')=='Continuar'
