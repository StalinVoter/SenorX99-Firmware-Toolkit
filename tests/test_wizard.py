import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from hashlib import sha256
from types import SimpleNamespace
import io
import json
import shutil
import time
import pytest
from PySide6.QtWidgets import QApplication
from x99toolkit import gui, fpt_gui, cli, tool_inventory as inventory
from x99toolkit.cpu import CpuDetection, Processor
from x99toolkit.models import InspectionReport, BuildReport
from x99toolkit.optimized import OPTIMIZED_SETTINGS

ROOT=Path(__file__).resolve().parents[1]

def spin(app,done):
 end=time.monotonic()+10
 while not done() and time.monotonic()<end:app.processEvents();time.sleep(.005)
 assert done()

@pytest.fixture
def window(monkeypatch,tmp_path):
 app=QApplication.instance() or QApplication([])
 monkeypatch.setenv('X99_TOOLKIT_HOME',str(tmp_path))
 folder=tmp_path/'tools';folder.mkdir()
 specs=[]
 for old in inventory.TOOL_SPECS:
  data=('pinned fixture '+old.name).encode();spec=inventory.ToolSpec(old.name,'',sha256(data).hexdigest(),len(data),'https://example.invalid/'+old.name,sha256(data).hexdigest(),len(data))
  specs.append(spec);(folder/old.name).write_bytes(data)
 monkeypatch.setattr(inventory,'TOOL_SPECS',tuple(specs));monkeypatch.setattr(inventory,'TOOLS_BY_NAME',{s.name:s for s in specs})
 # FPT display version is data, not an installation requirement.
 w=gui.Window(runtime_check=True);w.show();app.processEvents()
 monkeypatch.setattr(w,'_message',lambda *a,**kw:True)
 assert w._all_tools_ready
 yield app,w,folder
 spin(app,lambda:not w._any_thread_running());w.close();w.deleteLater();app.processEvents()

def loaded(w,tmp_path,*,cpu='E5-2660 v3',keys=False,logo=True,oc=True):
 source=tmp_path/'source.ROM';source.write_bytes(b'firmware fixture, not real BIOS')
 r=InspectionReport(str(source),source.stat().st_size,sha256(source.read_bytes()).hexdigest(),
  tbu=dict(status='NOT_APPLIED',target_count=1,cstates={'supported':True}),
  secure_boot=dict(all_variables_valid=True,has_complete_2023_set=keys,has_ami_test_pk=False),
  fit=dict(status='PASS',supported=True),optimized=dict(supported=True,matches=False,settings=[dict(s,matches=False) for s in OPTIMIZED_SETTINGS]),
  overclock=dict(supported=oc,matches=False),splash=dict(supported=logo,source_sha256=sha256(source.read_bytes()).hexdigest(),
   tool_sha256=sha256((tmp_path/'tools/ChangeLogo.exe').read_bytes()).hexdigest(),reason='fixture'))
 w.cpu_detection=CpuDetection((Processor(cpu),)) if cpu else CpuDetection(error='fixture')
 w.rom_path=source;w._source_ready=True;w._inspection_finished(r,'');w._refresh_state()
 return r

def built(w,tmp_path):
 assert w._accept_configuration()
 output=tmp_path/'modified.ROM';output.write_bytes(w.rom_path.read_bytes())
 report=BuildReport(str(w.rom_path),str(output),output_sha256=sha256(output.read_bytes()).hexdigest(),success=True)
 w._build_plan=w._plan();w._driver_hash_at_build=sha256(Path(w.driver_combo.currentData()).read_bytes()).hexdigest() if w.tbu_box.isChecked() else None
 w._build_finished(report,'');w._refresh_state()
 return output

def test_six_clickable_tabs_and_sequential_action_gates(window):
 _,w,_=window
 assert w.windowTitle()=='SenorX99 v0.931' and w.stack.count()==6
 assert w.completed_steps()==[True,False,False,False,False,False]
 for index,b in enumerate(w.nav_buttons):
  assert b.isEnabled();b.click();assert w.stack.currentIndex()==index
  assert w.page_bodies[index].isEnabled() is (index<=1)
 assert all(not w.continue_buttons[i].isEnabled() for i in range(1,6))

@pytest.mark.parametrize('name',[s.name for s in inventory.TOOL_SPECS])
@pytest.mark.parametrize('state',['missing','wrong'])
def test_all_tools_required_before_rom_input(window,monkeypatch,tmp_path,name,state):
 _,w,folder=window
 path=folder/name
 if state=='missing':path.unlink()
 else:path.write_bytes(b'incorrect')
 monkeypatch.setattr(gui,'InspectWorker',lambda *a:pytest.fail('inspection bypassed gate'))
 source=tmp_path/'never-read.ROM';source.write_bytes(b'fixture')
 w._refresh_tools();w.load_rom(str(source));w.start_dump();w.start_build()
 assert not w.completed_steps()[0] and w.rom_path is None
 assert not w.load_rom_btn.isEnabled() and not w.drop_zone.isEnabled()

@pytest.mark.parametrize('model,tbu,oc',[('E5-2660 v3',True,False),('E5-1660 v3',False,True),('E5-2667 v4',False,False),('i7-6950X',False,True)])
def test_recommended_mode_locks_all_controls_and_custom_has_no_host_cpu_gate(window,tmp_path,model,tbu,oc):
 _,w,_=window;loaded(w,tmp_path,cpu=model);w._set_nav(3)
 assert w.recommended_mode.isChecked()
 assert w.tbu_box.isChecked() is tbu and w.overclock_box.isChecked() is oc
 assert w.sb_box.isChecked() and w.optimized_box.isChecked() and w.splash_box.isChecked()
 assert all(not b.isEnabled() for b in w.feature_boxes.values())
 assert not w.driver_combo.isEnabled() and not w.auto_splash_box.isEnabled() and not w.custom_splash_box.isEnabled()
 assert all(b.isEnabled() for b in w.help_buttons.values())
 w.custom_mode.click()
 assert w.tbu_box.isEnabled() and w.overclock_box.isEnabled()
 w.tbu_box.setChecked(True);w.overclock_box.setChecked(True)
 assert w.tbu_box.isChecked() and w.overclock_box.isChecked()

def test_current_secure_boot_keys_not_recommended(window,tmp_path):
 _,w,_=window;loaded(w,tmp_path,keys=True)
 assert w.analysis_rows['secureboot'].text()=='Already up to date' and not w.sb_box.isChecked()

def test_unknown_cpu_and_fpt_blocker_keep_offline_build_and_save(window,monkeypatch,tmp_path):
 app,w,_=window;loaded(w,tmp_path,cpu=None)
 w.fpt_probe=dict(ready=False,reason='memory-integrity-block');w._refresh_state()
 assert w.optimized_box.isChecked() and w.sb_box.isChecked() and w.splash_box.isChecked()
 assert not w.tbu_box.isChecked() and not w.dump_btn.isEnabled() and w.load_rom_btn.isEnabled()
 output=built(w,tmp_path);w._set_nav(5)
 assert w.save_btn.isEnabled() and not w.flash_btn.isEnabled()
 target=tmp_path/'saved.ROM';monkeypatch.setattr(w,'_file_dialog',lambda *a,**kw:str(target));w.save_rom()
 assert target.read_bytes()==output.read_bytes() and w.completed_steps()==[True]*6

def test_recommendations_do_not_overwrite_custom_choices_when_cpu_result_arrives(window,tmp_path):
 _,w,_=window;loaded(w,tmp_path);w._set_nav(3);w.custom_mode.click()
 w.splash_box.setChecked(False);w.tbu_box.setChecked(True)
 w._cpu_finished(CpuDetection((Processor('E5-2667 v4'),)))
 assert w.tbu_box.isChecked() and not w.splash_box.isChecked()

def test_declining_tbu_warning_prevents_configuration_completion(window,monkeypatch,tmp_path):
 _,w,_=window;loaded(w,tmp_path)
 monkeypatch.setattr(w,'_message',lambda *a,**kw:False)
 w._continue(3)
 assert not w.completed_steps()[3] and not w.build_btn.isEnabled()

def test_changing_plan_or_source_invalidates_later_steps(window,tmp_path):
 _,w,_=window;loaded(w,tmp_path);built(w,tmp_path)
 assert w.completed_steps()[4]
 w._set_nav(3);w.custom_mode.click()
 assert not w.completed_steps()[3] and not w.completed_steps()[4]
 assert w.report and w.completed_steps()[2]

def test_analysis_lists_actual_default_changes_and_help_stays_available_for_na(window,tmp_path):
 _,w,_=window;loaded(w,tmp_path,logo=False,oc=False);w._set_nav(3)
 assert w.na_labels['splash'].isVisible() and w.na_labels['overclock'].isVisible()
 w.help_buttons['optimized'].click()
 for setting in OPTIMIZED_SETTINGS:assert setting['name'] in w.help_text.text()
 w.help_buttons['tbu'].click()
 assert '−60/−50/−50' in w.help_text.text() and 'Core / Cache / System Agent' in w.help_text.text()

def test_successful_and_failed_custom_logo_trials_control_na(window,monkeypatch,tmp_path):
 app,w,_=window;loaded(w,tmp_path);w._set_nav(3);w.custom_mode.click()
 image=tmp_path/'custom.bmp';image.write_bytes(b'fixture image')
 monkeypatch.setattr(w,'_file_dialog',lambda *a,**kw:str(image))
 def trial(source,digest,selected):
  assert selected==image
  return dict(supported=True,source_sha256=digest,tool_sha256=sha256((tmp_path/'tools/ChangeLogo.exe').read_bytes()).hexdigest(),image_sha256=sha256(image.read_bytes()).hexdigest())
 monkeypatch.setattr(gui,'inspect_splash_replacement',trial)
 w.custom_splash_box.click();spin(app,lambda:w.logo_thread is None)
 assert w.splash_box.isChecked() and not w.na_labels['splash'].isVisible()
 image.write_bytes(b'changed image')
 w._refresh_state();assert w.na_labels['splash'].isVisible() and not w._configuration_valid()
 w.auto_splash_box.click();assert w._logo_verified()

def test_language_switch_preserves_rom_plan_and_completion(window,monkeypatch,tmp_path):
 _,w,_=window;loaded(w,tmp_path);output=built(w,tmp_path)
 languages=tmp_path/'languages';languages.mkdir();shutil.copy2(ROOT/'languages/en.json',languages/'en.json')
 (languages/'pt.json').write_text(json.dumps(dict(schema=1,name='Português',strings={'common.continue':'Continuar','step.1.title':'Verificar ficheiros'})))
 from x99toolkit.localization import Catalog
 w.catalog=Catalog(languages);w.language_combo.addItem('Português','pt')
 plan=w._plan();done=w.completed_steps();w.language_combo.setCurrentIndex(w.language_combo.findData('pt'))
 assert w.continue_buttons[0].text()=='Continuar'
 assert w._plan()==plan and w.completed_steps()==done and w.last_built_rom==output
 # Do not persist a test language for following source/runtime tests.
 w.settings.setValue('language','en')

def test_save_rejects_modified_build_and_never_overwrites_source(window,monkeypatch,tmp_path):
 _,w,_=window;loaded(w,tmp_path);output=built(w,tmp_path)
 original=w.rom_path.read_bytes();monkeypatch.setattr(w,'_file_dialog',lambda *a,**kw:str(w.rom_path))
 w.save_rom();assert w.rom_path.read_bytes()==original and not w._delivery_complete
 output.write_bytes(b'tampered');w.save_rom();assert not w.completed_steps()[4]

def test_inline_fpt_progress_and_dump_completion(window,monkeypatch,tmp_path):
 app,w,_=window;w.fpt_probe={'ready':True};w._refresh_state()
 target=tmp_path/'dump.ROM';monkeypatch.setattr(w,'_file_dialog',lambda *a,**kw:str(target))
 def run(worker):
  worker.event.emit(dict(type='phase',group='Dump BIOS',key='read:1',percent=100))
  worker.event.emit(dict(type='transcript',text='FPT Operation Passed'))
  target.write_bytes(b'fixture BIOS');worker.finished.emit(SimpleNamespace(success=True,output_path=str(target),sha256=sha256(target.read_bytes()).hexdigest(),error=None))
 monkeypatch.setattr(fpt_gui.FptWorker,'run',run)
 monkeypatch.setattr(gui.InspectWorker,'run',lambda worker:worker.finished.emit(None,'fixture analysis deliberately skipped'))
 w.start_dump();spin(app,lambda:w.bios_thread is None and w.inspect_thread is None)
 assert w.rom_path==target and 'FPT Operation Passed' in w.dump_view.console.toPlainText()
 assert all(bar.value()==100 for _,bar in w.dump_view.phases.values())

def test_tool_group_atomic_downloads_and_failure_preserve_existing_files(window,monkeypatch,tmp_path):
 app,w,folder=window
 original=(folder/'mmtool_a5.exe').read_bytes();(folder/'mmtool_a5.exe').write_bytes(b'wrong')
 payload=b'wrong download'
 class Response(io.BytesIO):
  def geturl(self):return 'https://example.invalid/source'
 monkeypatch.setattr(inventory,'urlopen',lambda *a,**kw:Response(payload))
 monkeypatch.setattr(w,'_confirm_tool_download',lambda name:True)
 w._refresh_tools();w._download_tool('mmtool_a5.exe');spin(app,lambda:w.download_thread is None)
 assert (folder/'mmtool_a5.exe').read_bytes()==b'wrong'
 assert 'failed' in w.tool_download_status.text().lower()
 assert len(w.tool_rows)==4 and 'fpt' in w.tool_rows

def test_cli_still_requires_all_tools(window,monkeypatch,tmp_path,capsys):
 _,w,folder=window;(folder/'ChangeLogo.exe').unlink()
 monkeypatch.setattr(cli.sys,'argv',['SenorX99-CLI.py','inspect',str(tmp_path/'unread.ROM')])
 monkeypatch.setattr(cli,'inspect_rom',lambda *a:pytest.fail('CLI bypassed all-tools requirement'))
 with pytest.raises(SystemExit) as exc:cli.main()
 assert exc.value.code==2


def test_flags_on_every_page_switch_all_pages_and_preserve_built_rom(window,tmp_path):
 app,w,_=window
 loaded(w,tmp_path);output=built(w,tmp_path)
 plan=w._plan();done=w.completed_steps();rom=w.rom_path;digest=w._build_digest
 try:
  assert len(w.language_flag_buttons)==6
  for page,buttons in enumerate(w.language_flag_buttons):
   w._set_nav(page);app.processEvents()
   assert w.pages[page][0].layout().itemAt(0).layout().indexOf(buttons['pt-BR'])>=0
   for language in ('pt-BR','en'):
    assert buttons[language].isEnabled() and buttons[language].isVisible()
    assert not buttons[language].icon().isNull()
    buttons[language].click();app.processEvents()
    assert w.catalog.code==language and w.language_combo.currentData()==language
    assert w.stack.currentIndex()==page
    assert w.rom_path==rom and w.last_built_rom==output and w._build_digest==digest
    assert w._plan()==plan and w.completed_steps()==done
    for i,flags in enumerate(w.language_flag_buttons):
     assert flags[language].isChecked() and not flags['en' if language=='pt-BR' else 'pt-BR'].isChecked()
     assert w.nav_labels[i].text()==w.t(f'step.{i+1}.title')
     assert w.continue_buttons[i].text()==w.t('common.finish' if i==5 else 'common.continue')
  # Restarting also restores the saved preference.
  w.language_flag_buttons[0]['pt-BR'].click()
  other=gui.Window(runtime_check=True)
  try:
   assert other.catalog.code=='pt-BR'
   assert all(flags['pt-BR'].isChecked() for flags in other.language_flag_buttons)
  finally:other.close();other.deleteLater();app.processEvents()
 finally:w._select_language('en')


def test_language_flags_remain_available_on_locked_pages(window):
 app,w,_=window
 try:
  w._set_nav(5);app.processEvents();assert not w.page_bodies[5].isEnabled()
  w.language_flag_buttons[5]['pt-BR'].click()
  assert w.catalog.code=='pt-BR' and not w.page_bodies[5].isEnabled()
 finally:w._select_language('en')


@pytest.mark.parametrize('name',[g.name for g in inventory.TOOL_GROUPS])
@pytest.mark.parametrize('approve',[False,True])
@pytest.mark.parametrize('language',['en','pt-BR'])
def test_tool_download_dialog_requires_yes_before_starting(window,monkeypatch,name,approve,language):
 from PySide6.QtCore import QTimer
 from PySide6.QtWidgets import QDialogButtonBox
 from x99toolkit.tool_confirmation import ToolDownloadConfirmation, NOTICES
 app,w,folder=window;started=[];seen=[]
 for member in inventory.GROUPS_BY_NAME[name].files:(folder/member).unlink()
 w._refresh_tools();w._select_language(language)
 monkeypatch.setattr(w,'_launch',lambda task,worker,callback:started.append((task,worker.name)))
 def answer():
  box=app.activeModalWidget()
  if not isinstance(box,ToolDownloadConfirmation):
   seen.append(False)
   if box:box.reject()
   return
  details=box.details.toPlainText()
  seen.append(all(inventory.TOOLS_BY_NAME[f].url in details and f in details for f in inventory.GROUPS_BY_NAME[name].files)
              and str(folder) in details and NOTICES[name][0] in details
              and box.buttons.button(QDialogButtonBox.No).isDefault())
  box.buttons.button(QDialogButtonBox.Yes if approve else QDialogButtonBox.No).click()
 try:
  QTimer.singleShot(0,answer)
  w.tool_rows[name][1].click()
  assert seen==[True]
  assert started==([('download',name)] if approve else [])
  if not approve:assert w._download_name=='' and w._download_result is None
 finally:w._select_language('en')


def test_tool_download_window_close_cancels(window,monkeypatch):
 from PySide6.QtCore import QTimer
 app,w,_=window
 monkeypatch.setattr(w,'_launch',lambda *args:pytest.fail('Closing confirmation started a download'))
 QTimer.singleShot(0,lambda:app.activeModalWidget().close())
 w._download_tool('fpt')
 assert w._download_name=='' and w._download_result is None
