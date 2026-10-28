import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path

import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from x99toolkit.gui import Window


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('X99_TOOLKIT_HOME', str(tmp_path))
    loaded = []
    class RecordedWindow(Window):
        def _rom_input_allowed(self):
            return True  # Route a dummy file without opening/inspecting firmware.
        def _require_all_tools(self, **kwargs):
            return True
        def load_rom(self, path):
            loaded.append(Path(path).resolve())
    win = RecordedWindow(runtime_check=True); win.show(); app.processEvents()
    yield app, win, loaded
    win.close(); app.processEvents()


def send_drop(target, urls):
    mime = QMimeData(); mime.setUrls(urls)
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    QApplication.sendEvent(target, drop)
    return enter.isAccepted(), drop.isAccepted()


@pytest.mark.parametrize('target', ['panel', 'main'])
def test_file_drop_routes_to_rom_loader(window, tmp_path, target):
    app, win, loaded = window
    rom = tmp_path / 'firmware åäö with spaces.ROM'; rom.write_bytes(b'fixture')
    widget = win.drop_zone if target == 'panel' else win
    assert send_drop(widget, [QUrl.fromLocalFile(str(rom))]) == (True, True)
    assert loaded == [rom.resolve()]


@pytest.mark.parametrize('case', ['remote', 'directory', 'missing', 'multiple'])
def test_invalid_drops_are_rejected(window, tmp_path, case):
    app, win, loaded = window
    rom = tmp_path / 'firmware.ROM'; rom.write_bytes(b'fixture')
    urls = {
        'remote': [QUrl('https://example.invalid/firmware.ROM')],
        'directory': [QUrl.fromLocalFile(str(tmp_path))],
        'missing': [QUrl.fromLocalFile(str(tmp_path / 'missing.ROM'))],
        'multiple': [QUrl.fromLocalFile(str(rom)), QUrl.fromLocalFile(str(rom))],
    }[case]
    for widget in (win.drop_zone, win):
        assert send_drop(widget, urls) == (False, False)
    assert not loaded


def test_gui_manifest_does_not_request_admin():
    root = Path(__file__).resolve().parents[1]
    assert 'uac_admin=False' in (root / 'SenorX99.spec').read_text()
    from PyInstaller.utils.win32.winmanifest import create_application_manifest
    from xml.etree import ElementTree
    manifest = ElementTree.fromstring(create_application_manifest(uac_admin=False))
    levels = [node.attrib['level'] for node in manifest.iter()
              if node.tag.split('}')[-1] == 'requestedExecutionLevel']
    assert levels == ['asInvoker']
