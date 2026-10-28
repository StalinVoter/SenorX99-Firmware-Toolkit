import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from hashlib import sha256
import io
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
from x99toolkit import APP_SUBTITLE, gui, fpt_gui, tool_inventory as inventory


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('X99_TOOLKIT_HOME', str(tmp_path))
    probes = []
    def probe():
        probes.append(True)
        return dict(ready=False, reason='fixture', message='No hardware access in this test')
    monkeypatch.setattr(fpt_gui, 'probe_fpt', probe)
    win = gui.Window(runtime_check=True)
    win.show(); app.processEvents()
    yield app, win, probes
    wait(app, lambda: not win._operation_busy())
    win.close(); win.deleteLater(); app.processEvents()


def wait(app, condition):
    deadline = time.monotonic() + 8
    while not condition() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    assert condition(), 'The Qt operation did not finish'


def test_brand_and_four_download_groups_are_visible_on_both_pages(window):
    from PySide6.QtWidgets import QPushButton
    app, win, probes = window
    assert win.windowTitle() == 'SenorX99 v0.86'
    assert any(APP_SUBTITLE in label.text() for label in win.findChildren(QLabel))
    assert len(win.tool_rows) == len(win.missing_tool_rows) == 4
    assert not win.missing_tools_card.isHidden()
    for rows in (win.tool_rows, win.missing_tool_rows):
        assert set(rows) == {'UEFIReplace.exe', 'fpt', 'mmtool_a5.exe', 'ChangeLogo.exe'}
        for name, (label, button) in rows.items():
            assert 'Missing' in label.text()
            assert button.text() == 'Download' and button.isEnabled()
    assert not any('Import' in button.text() or 'Get Intel FPT' in button.text()
                   for button in win.findChildren(QPushButton))
    assert not probes


@pytest.mark.parametrize('name', ['fpt', 'UEFIReplace.exe', 'mmtool_a5.exe', 'ChangeLogo.exe'])
@pytest.mark.parametrize('bad', [False, True])
@pytest.mark.parametrize('page', ['overview', 'tools'])
def test_tool_worker_retry_cleanup_and_selective_fpt_reprobe(window, monkeypatch, tmp_path, name, bad, page):
    app, win, probes = window
    files = inventory.GROUPS_BY_NAME[name].files
    data = {member: ('GUI downloaded ' + member).encode() for member in files}
    specs = tuple(inventory.ToolSpec(member, 'fixture', sha256(data[member]).hexdigest(), len(data[member]),
                                   'https://example.invalid/' + member, sha256(data[member]).hexdigest(), len(data[member]))
                  for member in files)
    group = inventory.ToolGroup(name, name, files)
    monkeypatch.setattr(inventory, 'TOOL_SPECS', specs)
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec for spec in specs})
    monkeypatch.setattr(inventory, 'TOOL_GROUPS', (group,))
    monkeypatch.setattr(inventory, 'GROUPS_BY_NAME', {name: group})
    class Response(io.BytesIO):
        def geturl(self): return 'https://example.invalid/pin'
    def download(request, **kw):
        time.sleep(.02)
        member = request.full_url.rsplit('/', 1)[-1]
        return Response(b'bad' if bad else data[member])
    monkeypatch.setattr(inventory, 'urlopen', download)
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: errors.append(a[2]))
    (tmp_path / 'tools').mkdir()
    for member in files:
        (tmp_path / 'tools' / member).write_bytes(b'previous file')
    win._refresh_tools()
    win._set_nav(win.PAGE_OVERVIEW if page == 'overview' else win.PAGE_TOOLS)
    rows = win.missing_tool_rows if page == 'overview' else win.tool_rows
    label, button = rows[name]
    QTest.mouseClick(button, Qt.LeftButton)
    assert win.download_thread is not None
    assert not win.tool_rows[name][1].isEnabled()
    assert not win.missing_tool_rows[name][1].isEnabled()
    assert not win.recheck_tools_btn.isEnabled()
    app.processEvents()
    wait(app, lambda: not win._operation_busy())
    if bad:
        assert errors and button.isEnabled()
        assert all((tmp_path / 'tools' / member).read_bytes() == b'previous file' for member in files)
        assert not probes
    else:
        assert not errors and not button.isEnabled()
        assert all((tmp_path / 'tools' / member).read_bytes() == data[member] for member in files)
        assert label.text() == 'Ready'
        assert len(probes) == (1 if name == 'fpt' else 0)
        assert win.missing_tool_hosts[name].isHidden()
    assert win.download_thread is None and win.download_worker is None


def test_overview_shows_only_missing_groups_and_hides_when_all_ready(window, monkeypatch, tmp_path):
    _, win, _ = window
    specs = []
    folder = tmp_path / 'tools'; folder.mkdir()
    for spec in inventory.TOOL_SPECS:
        data = spec.name.encode()
        spec = inventory.ToolSpec(spec.name, '', sha256(data).hexdigest(), len(data),
                                  'https://example.invalid/pin', sha256(data).hexdigest(), len(data))
        specs.append(spec)
        (folder / spec.name).write_bytes(data)
    monkeypatch.setattr(inventory, 'TOOL_SPECS', tuple(specs))
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec for spec in specs})
    win._refresh_tools()
    assert win.missing_tools_card.isHidden()
    assert all(label.text() == 'Ready' and not button.isEnabled() for label, button in win.tool_rows.values())
    (folder / 'pmxdll32e.DLL').unlink()
    win._refresh_tools()
    assert not win.missing_tools_card.isHidden()
    assert not win.missing_tool_hosts['fpt'].isHidden()
    assert all(host.isHidden() for name, host in win.missing_tool_hosts.items() if name != 'fpt')
    assert 'pmxdll32e.DLL' in win.missing_tool_rows['fpt'][0].toolTip()
    (folder / 'pmxdll32e.DLL').write_bytes(b'pmxdll32e.DLL')
    win._refresh_tools()
    assert win.missing_tools_card.isHidden()


@pytest.mark.parametrize('active', ['inspect_thread', 'build_thread', 'probe_thread', 'download_thread', '_bios_running'])
def test_busy_load_keeps_source_output_and_report_unchanged(window, tmp_path, active):
    app, win, _ = window
    source = tmp_path / 'replacement åäö.ROM'; source.write_bytes(b'not inspected')
    old = tmp_path / 'original.ROM'
    win.rom_path = old
    win.report = {'previous': True}
    win.output_edit.setText('unchanged-output.ROM')
    setattr(win, active, object())
    try:
        win.load_rom(str(source))
        assert win.rom_path == old
        assert win.report == {'previous': True}
        assert win.output_edit.text() == 'unchanged-output.ROM'
    finally:
        setattr(win, active, None)
        win.report = None


@pytest.mark.parametrize('answers,expected_calls', [((False, False), 0), ((True, False), 0), ((True, True), 1)])
@pytest.mark.parametrize('secureboot_context', [False, True])
def test_postbuild_two_confirmations_default_no_and_keep_target(window, monkeypatch, tmp_path, answers, expected_calls, secureboot_context):
    app, win, _ = window
    target = tmp_path / 'fresh build.ROM'; target.write_bytes(b'fixture')
    first, second = answers
    questions, warnings, flashed = [], [], []
    def question(*args):
        questions.append(args)
        assert args[-1] == QMessageBox.No
        return QMessageBox.Yes if first else QMessageBox.No
    def warning(*args):
        warnings.append(args)
        assert args[-1] == QMessageBox.No
        if secureboot_context:
            assert args[2] == fpt_gui.SECUREBOOT_FLASH_WARNING
        else:
            assert 'generally very low-risk' not in args[2]
            assert str(target) in args[2]
            assert 'WILL . BRICK . YOUR . MOTHERBOARD.' in args[2]
        return QMessageBox.Yes if second else QMessageBox.No
    class Dialog:
        result = None
        def __init__(self, parent, mode, path):
            assert mode == 'flash'; flashed.append(path)
        def exec(self): pass
    monkeypatch.setattr(QMessageBox, 'question', question)
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    monkeypatch.setattr(win, '_ensure_fpt_ready', lambda: True)
    monkeypatch.setattr(win, '_require_all_tools', lambda **kwargs: True)
    monkeypatch.setattr(fpt_gui, '_choose_firmware_file', lambda *a: pytest.fail('fresh target must not be browsed again'))
    monkeypatch.setattr(fpt_gui, 'FptOperationDialog', Dialog)
    win._secureboot_flash_context = secureboot_context
    win._offer_flash(target)
    assert len(questions) == 1
    assert len(warnings) == int(first)
    assert flashed == ([target] if expected_calls else [])


def test_revoked_certificate_is_displayed_without_admin_advice(window):
    _, win, _ = window
    win.fpt_probe = dict(ready=False, reason='driver-certificate-revoked', message='Windows rejected the Intel FPT driver: 0x800B010C')
    win._update_fpt_status_labels()
    assert all('0x800B010C' in label.text() for label in win.fpt_status_labels)


def test_failed_download_blocks_conflicting_actions(window, monkeypatch):
    _, win, _ = window
    win.download_thread = object()
    try:
        monkeypatch.setattr(gui, 'BuildWorker', lambda *a: pytest.fail('build started during download'))
        monkeypatch.setattr(fpt_gui, 'FptProbeWorker', lambda *a: pytest.fail('probe started during download'))
        win.start_build(); win._start_fpt_probe(); win._recheck_tools()
    finally:
        win.download_thread = None


@pytest.mark.parametrize('verified,tool_available,expected', [(True, True, True), (False, True, False), (True, False, False)])
def test_splash_is_opt_out_only_after_verified_rom_trial(window, monkeypatch, tmp_path, verified, tool_available, expected):
    from types import SimpleNamespace
    from x99toolkit.models import InspectionReport
    _, win, _ = window
    tool = tmp_path / 'ChangeLogo.exe'; tool.write_bytes(b'fixture tool')
    monkeypatch.setattr(gui, 'discover_tools', lambda: SimpleNamespace(changelogo=tool if tool_available else None))
    source = tmp_path / 'board.ROM'; source.write_bytes(b'fixture ROM')
    digest = sha256(source.read_bytes()).hexdigest()
    win.rom_path = source
    win.report = InspectionReport(str(source), source.stat().st_size, digest, {}, {}, fit={'status': 'PASS'},
        splash=dict(supported=verified, source_sha256=digest, tool_sha256=sha256(tool.read_bytes()).hexdigest(), reason='fixture result'))
    monkeypatch.setattr(win, '_rom_input_allowed', lambda: True)
    win.create_page.setEnabled(True)
    win._configure_splash_option()
    assert win.splash_box.isChecked() is expected
    assert win.splash_box.isEnabled() is tool_available
    assert win.auto_splash_box.isChecked()
    if expected:
        assert 'LOGO' in win.output_edit.text()
        win.splash_box.click()  # A user opts out.
        assert win._splash_user_choice is False
        win._configure_splash_option()  # Tool refresh must not undo that choice.
        assert not win.splash_box.isChecked()
        assert 'LOGO' not in win.output_edit.text()
