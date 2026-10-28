"""Exercise the actual PowerShell case setup and the frozen GUI test harness."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = os.environ.get('X99_POWERSHELL') or shutil.which('powershell.exe') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='PowerShell is not installed')


def distribution(tmp_path):
    folder = tmp_path / 'dist with spaces'; folder.mkdir()
    manifest = json.loads((ROOT/'resource-manifest.json').read_text())
    for relative in manifest['files']:
        dest = folder/relative; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/relative, dest)
    shutil.copy2(ROOT/'resource-manifest.json', folder/'resource-manifest.json')
    (folder/'tools').mkdir(); (folder/'tools/fptw64.exe').write_bytes(b'must never be copied or executed')
    return folder


def script(tmp_path, body):
    source = (ROOT/'TEST-PACKAGED-RUNTIME.ps1').read_text()
    helpers = source.split('# Windows-only packaged tests begin here.', 1)[1].split("if (-not [Environment]::Is64BitProcess)", 1)[0]
    path = tmp_path/'exercise-runtime-helpers.ps1'
    path.write_text("param([string]$Distribution,[string]$CaseDir,[string]$Reports,[string]$Output)\n$ErrorActionPreference='Stop'\n" + helpers + '\n' + body)
    return path


def stage(tmp_path, source, case):
    path = script(tmp_path, "Initialize-RuntimeTestResources -Distribution $Distribution -CaseDir $CaseDir")
    return subprocess.run([POWERSHELL,'-NoLogo','-NoProfile','-File',str(path),'-Distribution',str(source),'-CaseDir',str(case)],
                          capture_output=True,text=True,timeout=30)


def test_actual_powershell_case_setup_copies_catalog_and_resources_but_no_tools(tmp_path):
    source = distribution(tmp_path); case = tmp_path/'case'
    (source/'_runtime').mkdir(); (source/'_runtime/example.txt').write_text('private runtime fixture')
    result = stage(tmp_path, source, case)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((case/'resource-manifest.json').read_text())
    for relative, expected in manifest['files'].items():
        assert sha256((case/relative).read_bytes()).hexdigest() == expected
    assert (case/'languages/en.json').is_file()
    assert not (case/'tools').exists()
    assert (case/'_runtime/example.txt').read_text() == 'private runtime fixture'


@pytest.mark.parametrize('failure', ['missing', 'changed', 'traversal'])
def test_runtime_case_setup_rejects_incomplete_or_unsafe_resources(tmp_path, failure):
    source = distribution(tmp_path)
    if failure == 'missing': (source/'languages/en.json').unlink()
    elif failure == 'changed': (source/'languages/en.json').write_text('changed')
    else:
        p=source/'resource-manifest.json';manifest=json.loads(p.read_text());manifest['files']['../escape']='0'*64;p.write_text(json.dumps(manifest))
    result = stage(tmp_path, source, tmp_path/'case')
    assert result.returncode != 0
    assert ('missing' if failure=='missing' else 'verification' if failure=='changed' else 'Invalid') in result.stderr


def test_failure_diagnostic_is_printed_and_logs_are_retained(tmp_path):
    case=tmp_path/'case';(case/'LOGS').mkdir(parents=True)
    (case/'LOGS/Session-test.log').write_text('complete runtime details')
    reports=tmp_path/'reports';reports.mkdir()
    output=reports/'smoke-gui.json';output.write_text(json.dumps({'success':False,'error':'FileNotFoundError: languages/en.json'}))
    path=script(tmp_path,"Write-RuntimeTestDiagnostics -CaseDir $CaseDir -Reports $Reports -CaseName 'gui' -Output $Output")
    result=subprocess.run([POWERSHELL,'-NoLogo','-NoProfile','-File',str(path),'-CaseDir',str(case),'-Reports',str(reports),'-Output',str(output)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'FileNotFoundError: languages/en.json' in result.stdout
    assert (reports/'logs-gui/Session-test.log').read_text()=='complete runtime details'


@pytest.mark.parametrize('include_catalog', [False, True])
def test_frozen_gui_harness_in_an_isolated_case_directory(tmp_path, monkeypatch, include_catalog):
    # Runs the real UI constructor and drop handlers. Only the final platform-name
    # assertion is adapted for this source test; this is not Windows EXE execution.
    from PySide6.QtWidgets import QApplication
    from x99toolkit import gui, runtime_bootstrap as rt
    app=QApplication.instance() or QApplication([])
    source=distribution(tmp_path);case=tmp_path/'case';case.mkdir()
    result=stage(tmp_path,source,case)
    assert result.returncode==0,result.stderr
    if not include_catalog:(case/'languages/en.json').unlink()
    exe=case/'SenorX99.exe';exe.write_bytes(b'fixture; never executed')
    report=tmp_path/'smoke-gui.json'
    monkeypatch.setenv('X99_TOOLKIT_HOME',str(case))
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(exe))
    monkeypatch.setattr(sys,'argv',[str(exe),'--runtime-self-test','--runtime-report',str(report)])
    monkeypatch.setattr(rt,'_manifest',None);monkeypatch.setattr(rt,'_loader',None)
    original=rt.importlib.import_module
    proxy=SimpleNamespace(processEvents=app.processEvents,platformName=lambda:'windows')
    class Application:
        instance=staticmethod(lambda:proxy)
        sendEvent=staticmethod(QApplication.sendEvent)
    def importing(name,*args):
        if name=='PySide6.QtWidgets':return SimpleNamespace(QApplication=Application)
        return original(name,*args)
    monkeypatch.setattr(rt.importlib,'import_module',importing)
    code=rt.runtime_self_test('gui');data=json.loads(report.read_text())
    if include_catalog:
        assert code==0,data.get('error')
        assert data['drag_drop_verified'] is True
    else:
        assert code==2 and 'FileNotFoundError' in data['error'] and 'en.json' in data['error']
