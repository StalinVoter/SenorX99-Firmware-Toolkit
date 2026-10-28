import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from hashlib import sha256
from html import unescape
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from x99toolkit import cli, gui, fpt_gui, tool_inventory as inventory
from x99toolkit.feature_text import performance_defaults_explanation, SETTING_REASONS, TBU_CPU_WARNING
from x99toolkit.models import InspectionReport
from x99toolkit.optimized import OPTIMIZED_SETTINGS, V9S_OPTIMIZED_SETTINGS


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / 'tools'; folder.mkdir()
    specs = []
    for old in inventory.TOOL_SPECS:
        data = (old.name + ' pinned fixture').encode()
        spec = inventory.ToolSpec(old.name, '', sha256(data).hexdigest(), len(data),
                                  'https://example.invalid/' + old.name, sha256(data).hexdigest(), len(data))
        specs.append(spec); (folder / old.name).write_bytes(data)
    monkeypatch.setattr(inventory, 'TOOL_SPECS', tuple(specs))
    monkeypatch.setattr(inventory, 'TOOLS_BY_NAME', {spec.name: spec for spec in specs})
    monkeypatch.setenv('X99_TOOLKIT_HOME', str(tmp_path))
    monkeypatch.setattr(fpt_gui, 'probe_fpt', lambda: dict(ready=True, reason='fixture', message='No hardware accessed'))
    win = gui.Window(runtime_check=True)
    win.show(); app.processEvents()
    assert win._all_tools_ready
    yield app, win, folder
    spin(app, lambda: not win._operation_busy())
    win.close(); win.deleteLater(); app.processEvents()


def spin(app, done):
    deadline = time.monotonic() + 8
    while not done() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    assert done()


def report_for(source, *, current_keys=True):
    return InspectionReport(str(source), source.stat().st_size, sha256(source.read_bytes()).hexdigest(),
        tbu=dict(status='NOT_APPLIED', target_count=1, cstates={'supported': True}),
        secure_boot=dict(all_variables_valid=True, has_complete_2023_set=current_keys,
                         has_ami_test_pk=False, recommended_patch_mode='none' if current_keys else 'KekVar+dbVar'),
        fit=dict(status='PASS', supported=True),
        optimized=dict(supported=True, matches=False, settings=[dict(s, matches=False) for s in OPTIMIZED_SETTINGS]),
        overclock=dict(supported=False), splash=dict(supported=False, reason='Fixture without a real logo build'))


def select_fixture(win, source):
    source.write_bytes(b'not a firmware image; UI test fixture only')
    win.rom_path = source
    win._inspection_finished(report_for(source), '')


@pytest.mark.parametrize('name', [spec.name for spec in inventory.TOOL_SPECS])
@pytest.mark.parametrize('state', ['missing', 'wrong'])
def test_each_required_file_blocks_load_dump_build_and_settings(window, monkeypatch, tmp_path, name, state):
    _, win, folder = window
    source = tmp_path / 'attempt.ROM'; source.write_bytes(b'not loaded')
    path = folder / name
    if state == 'missing': path.unlink()
    else: path.write_bytes(b'wrong file')
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: messages.append(a[2]))
    monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName', lambda *a: pytest.fail('file picker bypassed all-tools gate'))
    monkeypatch.setattr(fpt_gui, '_choose_dump_output', lambda *a: pytest.fail('dump picker bypassed all-tools gate'))
    monkeypatch.setattr(fpt_gui, 'validate_fpt_installation', lambda: pytest.fail('FPT preflight must follow all-tools gate'))
    monkeypatch.setattr(gui, 'InspectWorker', lambda *a: pytest.fail('ROM inspection started without all tools'))
    monkeypatch.setattr(gui, 'BuildWorker', lambda *a: pytest.fail('ROM build started without all tools'))
    win._refresh_tools()
    assert not win._all_tools_ready
    assert not win.load_rom_btn.isEnabled() and not win.drop_zone.isEnabled()
    assert all(not button.isEnabled() for button in win._bios_action_buttons)
    win.load_rom(str(source)); win._browse_rom(); win.drop_zone._browse(); win.start_dump(); win.start_flash(); win.start_build()
    assert win.rom_path is None and win.report is None and win.inspect_thread is None
    assert not win.create_page.isEnabled() and not win.nav_buttons[win.PAGE_CREATE].isEnabled()
    assert messages and all('All tools must be present' in message for message in messages)


def test_removing_a_tool_locks_existing_rom_settings_until_repaired(window, tmp_path):
    _, win, folder = window
    select_fixture(win, tmp_path / 'selected.ROM')
    assert win.create_page.isEnabled() and win.tbu_box.isEnabled()
    path = folder / 'ChangeLogo.exe'; original = path.read_bytes(); path.unlink()
    win._refresh_tools()
    assert not win.create_page.isEnabled() and not win.overview_content.isEnabled()
    assert not win.tbu_box.isEnabled() and not win.sb_box.isEnabled()
    assert not win.nav_buttons[win.PAGE_CREATE].isEnabled()
    path.write_bytes(original); win._refresh_tools()
    assert win.create_page.isEnabled() and win.overview_content.isEnabled()


def events_for(source):
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(source))])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    return mime, enter, drop


@pytest.mark.parametrize('target', ['panel', 'window'])
def test_drop_rechecks_tool_files_at_the_actual_drop(window, tmp_path, target):
    _, win, folder = window
    source = tmp_path / 'sample.ROM'; source.write_bytes(b'not a BIOS')
    widget = win.drop_zone if target == 'panel' else win
    mime, enter, drop = events_for(source)
    QApplication.sendEvent(widget, enter)
    assert enter.isAccepted()
    (folder / 'mmtool_a5.exe').unlink()
    QApplication.sendEvent(widget, drop)
    assert not drop.isAccepted()
    assert win.rom_path is None and win.inspect_thread is None


@pytest.mark.parametrize('answer', [QMessageBox.No, QMessageBox.Yes])
@pytest.mark.parametrize('route', ['checkbox', 'overview'])
def test_tbu_selection_shows_cpu_warning_and_requires_yes(window, monkeypatch, tmp_path, answer, route):
    _, win, _ = window
    select_fixture(win, tmp_path / 'selected.ROM')
    calls = []
    def warning(*args):
        calls.append(args)
        assert args[-1] == QMessageBox.No
        assert 'E5-26xx v3' in args[2] and 'v4' in args[2] and 'unable to POST' in args[2]
        return answer
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    if route == 'checkbox': win.tbu_box.click()
    else: win._overview_action('tbu')
    assert len(calls) == 1
    assert win.tbu_box.isChecked() is (answer == QMessageBox.Yes)
    assert win._tbu_acknowledged is (answer == QMessageBox.Yes)


def test_build_cannot_bypass_tbu_warning(window, monkeypatch, tmp_path):
    _, win, _ = window
    select_fixture(win, tmp_path / 'selected.ROM')
    win.tbu_box.blockSignals(True); win.tbu_box.setChecked(True); win.tbu_box.blockSignals(False)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.No)
    monkeypatch.setattr(gui, 'BuildWorker', lambda *a: pytest.fail('unacknowledged TBU build started'))
    win.start_build()
    assert not win.tbu_box.isChecked() and win.build_thread is None


def test_secure_boot_current_keys_use_requested_wording(window, tmp_path):
    _, win, _ = window
    select_fixture(win, tmp_path / 'selected.ROM')
    assert win.sb_overview.status.text() == 'Already up to date'
    assert win.sb_overview.action.text() == 'Already up to date'
    assert not win.sb_overview.action.isEnabled()


@pytest.mark.parametrize('profile', [OPTIMIZED_SETTINGS, V9S_OPTIMIZED_SETTINGS])
def test_help_lists_every_actual_setting_target_and_reason(profile):
    text = unescape(performance_defaults_explanation({'settings': profile}))
    assert set(SETTING_REASONS) == {setting['name'] for setting in profile}
    for setting in profile:
        assert setting['name'] in text
        assert setting['desired_label'] in text
        assert SETTING_REASONS[setting['name']] in text
    assert 'NV-Internal' not in text and 'AMITSE' not in text and '11-setting' not in text


def test_plain_language_help_is_available_on_overview_and_create_page(window, tmp_path):
    _, win, _ = window
    select_fixture(win, tmp_path / 'selected.ROM')
    assert win.opt_overview.description.text() == 'Applies performance-oriented default settings in BIOS.'
    assert win.opt_overview.status.text() == 'Not applied'
    for setting in OPTIMIZED_SETTINGS:
        assert setting['name'] in win.opt_overview.detail.text()
        assert setting['name'] in win.opt_create_detail.text()
    win._set_nav(win.PAGE_CREATE)
    win.opt_create_more.click()
    assert not win.opt_create_detail.isHidden()


def test_load_dump_tab_survives_loading_and_can_load_another_rom(window, monkeypatch, tmp_path):
    app, win, _ = window
    monkeypatch.setattr(gui, 'inspect_rom', report_for)
    monkeypatch.setattr(gui, 'inspect_splash_replacement', lambda *a: dict(supported=False, state='unverified', reason='Fixture only'))
    monkeypatch.setattr(gui, 'write_inspection', lambda *a: None)
    for name in ('first.ROM', 'second.ROM'):
        source = tmp_path / name; source.write_bytes(name.encode())
        win._set_nav(win.PAGE_ROM)
        assert win.load_rom_btn.isEnabled()
        win.load_rom(str(source))
        spin(app, lambda: win.inspect_thread is None)
        assert win.rom_path == source and win.report is not None
        assert win.nav_buttons[win.PAGE_ROM].isEnabled()
        win._set_nav(win.PAGE_ROM)
        assert win.stack.currentWidget() is win.rom_page and win.load_rom_btn.isEnabled()


def test_cli_refuses_to_read_rom_without_all_tools(window, monkeypatch, tmp_path, capsys):
    _, _, folder = window
    (folder / 'ChangeLogo.exe').unlink()
    monkeypatch.setattr(cli.sys, 'argv', ['SenorX99-CLI.py', 'inspect', str(tmp_path / 'never-read.ROM')])
    monkeypatch.setattr(cli, 'inspect_rom', lambda *a: pytest.fail('CLI read a ROM before tools were ready'))
    with pytest.raises(SystemExit) as error: cli.main()
    assert error.value.code == 2
    assert 'Install all required tools' in capsys.readouterr().err


def test_successful_dump_loads_its_rom_and_keeps_the_source_tab(window, monkeypatch, tmp_path):
    app, win, _ = window
    output = tmp_path / 'dumped.ROM'
    payload = b'UI dump fixture; no hardware access'
    monkeypatch.setattr(fpt_gui, 'validate_fpt_installation', lambda: dict(ready=True))
    monkeypatch.setattr(fpt_gui, '_choose_dump_output', lambda *a: str(output))
    win.fpt_probe = dict(ready=True)
    class DumpDialog:
        def __init__(self, parent, operation, target):
            assert operation == 'dump' and target == output
            self.result = SimpleNamespace(success=True, output_path=str(output), sha256=sha256(payload).hexdigest())
        def exec(self):
            output.write_bytes(payload)
    monkeypatch.setattr(fpt_gui, 'FptOperationDialog', DumpDialog)
    monkeypatch.setattr(gui, 'inspect_rom', report_for)
    monkeypatch.setattr(gui, 'inspect_splash_replacement', lambda *a: dict(supported=False, state='unverified', reason='Fixture only'))
    monkeypatch.setattr(gui, 'write_inspection', lambda *a: None)
    win._set_nav(win.PAGE_ROM)
    win.start_dump()
    spin(app, lambda: win.inspect_thread is None)
    assert win.rom_path == output and win.report is not None
    assert sha256(payload).hexdigest() in win.own_dump_hashes
    win._set_nav(win.PAGE_ROM)
    assert win.stack.currentWidget() is win.rom_page and win.load_rom_btn.isEnabled()


def test_pending_startup_file_waits_for_complete_tool_installation(window, monkeypatch, tmp_path):
    _, win, folder = window
    source = tmp_path / 'startup.ROM'
    path = folder / 'UEFIReplace.exe'; data = path.read_bytes(); path.unlink()
    opened = []
    monkeypatch.setattr(win, 'load_rom', opened.append)
    win._pending_open_path = str(source)
    win._refresh_tools(); win._resume_pending_open()
    assert not opened and win._pending_open_path == str(source)
    path.write_bytes(data)
    win._refresh_tools(); win._resume_pending_open()
    assert opened == [str(source)] and win._pending_open_path is None


def test_tbu_acknowledgement_is_reset_for_a_different_rom(window, monkeypatch, tmp_path):
    _, win, _ = window
    first = tmp_path / 'first.ROM'; select_fixture(win, first)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.Yes)
    win.tbu_box.click()
    assert win._tbu_acknowledged
    win._inspection_finished(report_for(first), '')
    assert win._tbu_acknowledged and win.tbu_box.isChecked()
    second = tmp_path / 'second.ROM'; second.write_bytes(b'another UI fixture')
    win.rom_path = second
    win._inspection_finished(report_for(second), '')
    assert not win._tbu_acknowledged and not win.tbu_box.isChecked()


def test_download_footer_is_empty_until_download_progress_exists(window):
    _, win, _ = window
    assert all(view['progress'].text() == '' and view['progress'].isHidden() for view in win._tool_views)
    win._download_name = 'UEFIReplace.exe'
    win._tool_download_progress('Downloading…')
    assert all(not view['progress'].isHidden() and 'Downloading' in view['progress'].text() for view in win._tool_views)


@pytest.mark.parametrize('success', [False, True])
def test_dump_completion_hint_only_appears_after_worker_stops(window, monkeypatch, tmp_path, success):
    app, win, _ = window
    result = SimpleNamespace(success=success, output_path=str(tmp_path/'dump.ROM'), error='fixture failure')
    monkeypatch.setattr(fpt_gui.FptWorker, 'run', lambda worker: worker.finished.emit(result))
    dialog = fpt_gui.FptOperationDialog(win, 'dump', tmp_path/'dump.ROM')
    dialog.show()
    assert dialog.completion_hint.isHidden() and not dialog.close_btn.isEnabled()
    spin(app, lambda: not dialog.running)
    assert dialog.close_btn.isEnabled()
    assert dialog.completion_hint.isHidden() is not success
    if success:
        assert dialog.title.text() == 'BIOS dump complete'
        assert dialog.completion_hint.text() == 'BIOS dump complete. You can close this window now.'
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_logo_failure_reason_is_visible_without_expanding_help(window, tmp_path):
    _, win, _ = window
    select_fixture(win, tmp_path/'selected.ROM')
    win.report.splash = dict(supported=False, reason='ValueError: RuntimeError: ChangeLogo reported success but the ROM is byte-identical while replacing the default splash')
    win._populate_overview(); win._configure_splash_option()
    assert win.logo_overview.status.text() == 'Logo check failed'
    assert 'ChangeLogo did not change the BIOS file' in win.logo_overview.description.text()
    assert 'ValueError' not in win.logo_overview.description.text()
    assert 'different logo was needed' in win.splash_check_label.text()


def test_existing_senorspace_and_fit_correction_have_plain_statuses(window, tmp_path):
    _, win, _ = window
    select_fixture(win, tmp_path/'selected.ROM')
    win.report.splash = dict(supported=True, already_present=True, reason='SenorSpace is already installed.')
    win.report.fit = dict(status='STALE', repairable=True)
    win._populate_overview()
    assert win.logo_overview.status.text() == 'SenorSpace already installed'
    assert win.fit_status_label.text() == '⚠ Will be corrected in the new BIOS'
