import os,time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from x99toolkit import fpt_gui
from x99toolkit.fpt import FlashResult
from x99toolkit.gui import Window
from x99toolkit.inspection import inspect_rom


def spin(app,predicate):
    end=time.monotonic()+5
    while not predicate() and time.monotonic()<end:
        app.processEvents();time.sleep(.005)
    assert predicate()


@pytest.mark.parametrize('name,opt',[('qiyida-x99-k9s-stock.rom',True),('Jginue ARGB backup.rom',False)])
def test_gui_feature_gates(name,opt,monkeypatch,tmp_path):
    p=Path(os.environ.get('X99_TEST_DATA','/nonexistent'))/name
    if not p.exists():pytest.skip('reference ROM not supplied')
    monkeypatch.setenv('X99_TOOLKIT_HOME',str(tmp_path))
    app=QApplication.instance() or QApplication([])
    from x99toolkit import tool_inventory
    monkeypatch.setattr(tool_inventory, 'scan_tool_groups', lambda: [
        dict(name=g.name, label=g.label, ready=True, detail='Ready', files=[]) for g in tool_inventory.TOOL_GROUPS])
    w=Window()
    spin(app,lambda:w.probe_thread is None)
    w.load_rom(str(p))
    spin(app,lambda:w.inspect_thread is None and w.probe_thread is None)
    assert w.report is not None
    assert w.tbu_box.isEnabled() and w.sb_box.isEnabled()
    assert w.optimized_box.isEnabled() is opt
    assert not w.overclock_box.isEnabled()
    assert w.fpt_status_labels
    w.close();w.deleteLater();app.processEvents()


def test_fpt_dialog_continuous_pass_bars_and_thread_completion(monkeypatch,tmp_path):
    app=QApplication.instance() or QApplication([])
    def fake_run(self):
        for kind in ['read','erase','program','verify']:
            for pct in [0,50,100]:
                self.event.emit(dict(type='phase',group='Flash target ROM',key=kind+':1',label=kind,percent=pct))
        self.finished.emit(FlashResult(True,'target','backup','abc',{},False,None,None,{},None))
    monkeypatch.setattr(fpt_gui.FptWorker,'run',fake_run)
    dialog=fpt_gui.FptOperationDialog(None,'flash',tmp_path/'target')
    assert dialog.running and not dialog.close_btn.isEnabled()
    dialog.accept();assert dialog.running
    spin(app,lambda:not dialog.running)
    assert dialog.close_btn.isEnabled() and dialog.result.success
    assert len(dialog.phase_widgets)==4
    assert all(bar.value()==100 for label,bar in dialog.phase_widgets.values())
    dialog.close();dialog.deleteLater();app.processEvents()
