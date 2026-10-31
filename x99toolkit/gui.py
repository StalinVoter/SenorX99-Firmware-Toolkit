"""Six-step firmware workflow. Firmware mutations remain in the validated engine."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import sys
import tempfile
import traceback

from PySide6.QtCore import QObject, QSettings, QThread, Signal, Qt, QUrl, QTimer, QSize
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from . import __version__
from .cpu import CpuDetection, detect_cpu
from .localization import Catalog
from .flash_warning import confirmation_key
from .language_flags import language_flag
from .recommendations import FEATURES, firmware_availability, recommended_modifications
from .inspection import inspect_rom
from .splash_preflight import inspect_splash_replacement
from .pipeline import build_rom, default_output
from .reporting import write_inspection, report_paths
from .firmware import sha256_file
from .ser8989 import inspect_driver
from .tools import app_directory, discover_tools
from . import tool_inventory as inventory
from . import fpt_gui
from .fpt import backup_directory
from .tool_gui import ToolDownloadWorker
from .tool_confirmation import ToolDownloadConfirmation
from .buildprogress import BuildProgressTracker
from .sessionlog import start_session_log, finish_session_log, install_exception_hook, log

APP_TITLE = f'SenorX99 v{__version__}'
STYLE = '''
QWidget {font-family:"Segoe UI","Arial";font-size:10pt;color:#202a37;}
QMainWindow {background:#f3f5f8;}
QFrame#sidebar {background:#eaf0f6;border-right:1px solid #d4dce6;}
QPushButton {background:white;border:1px solid #bdc8d5;border-radius:7px;padding:9px 14px;}
QPushButton:hover {background:#eaf3fc;}
QPushButton:disabled {color:#8d99a8;background:#edf0f4;border-color:#d8dee6;}
QPushButton#primary {background:#096cc2;color:white;border-color:#096cc2;font-weight:600;}
QPushButton#primary:disabled {background:#cad5e0;border-color:#cad5e0;color:#738295;}
QPushButton#nav {background:transparent;border:0;text-align:left;padding:0;}
QPushButton#nav:checked {background:#d7e7f8;}
QPushButton#languageFlag {padding:4px;}
QPushButton#languageFlag:checked {background:#d7e7f8;border:2px solid #096cc2;}
QPushButton#help {color:#0868b4;font-weight:700;border-radius:15px;padding:3px;}
QFrame#card {background:white;border:1px solid #dce3eb;border-radius:10px;}
QLabel {background:transparent;}
QLabel#title {font-size:22pt;font-weight:650;}
QLabel#muted {color:#5a6b7d;}
QLabel#notice {background:#e8f2fc;border:1px solid #cbdef2;border-radius:7px;padding:10px;}
QLabel#warning {color:#9e2222;}
QComboBox {background:white;border:1px solid #bdc8d5;border-radius:5px;padding:6px;}
QComboBox:disabled {background:#edf0f4;color:#8d99a8;border-color:#d8dee6;}
QRadioButton:disabled {color:#8d99a8;}
QPlainTextEdit {background:#111b2c;color:#dde7f3;border:1px solid #c9d4e1;border-radius:7px;font-family:Consolas;font-size:9pt;}
QProgressBar {border:1px solid #c5d1df;border-radius:5px;background:#e8edf3;text-align:center;min-height:22px;}
QProgressBar::chunk {background:#1675ce;border-radius:4px;}
QCheckBox {spacing:8px;}
QRadioButton {spacing:7px;}
QScrollArea {background:transparent;border:0;}
'''


def _dropped_file(event):
    mime = event.mimeData()
    if not mime.hasUrls():
        return None
    urls = mime.urls()
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    path = Path(urls[0].toLocalFile())
    return str(path) if path.is_file() and path.suffix.lower() in ('.rom', '.bin') else None


class DropZone(QFrame):
    dropped = Signal(str)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)
        self.setMinimumSize(240, 110)
        self.setStyleSheet('QFrame{background:#fbfdff;border:2px dashed #b5c7da;border-radius:10px;} QLabel{border:0;}')
        layout = QVBoxLayout(self)
        for key in ('rom.drop', 'rom.drop_detail'):
            text = owner.label(key); text.setAlignment(Qt.AlignCenter)
            text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            layout.addWidget(text)

    def dragEnterEvent(self, event):
        if self.owner._rom_input_allowed() and _dropped_file(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = _dropped_file(event)
        if path and self.owner._rom_input_allowed() and self.owner._require_all_tools(notify=False):
            self.dropped.emit(path)
            event.acceptProposedAction()
        else:
            event.ignore()


class CpuWorker(QObject):
    finished = Signal(object)

    def run(self):
        try:
            result = detect_cpu()
        except Exception:
            result = CpuDetection(error='query-failed')
        self.finished.emit(result)


class InspectWorker(QObject):
    finished = Signal(object, str)

    def __init__(self, path):
        super().__init__(); self.path = path

    def run(self):
        try:
            report = inspect_rom(self.path)
            report.splash = inspect_splash_replacement(self.path, report.rom_sha256)
            try:
                write_inspection(report, self.path)
            except OSError as exc:
                log('INSPECTION REPORT', str(exc))
            self.finished.emit(report, '')
        except Exception:
            self.finished.emit(None, traceback.format_exc())


class LogoWorker(QObject):
    finished = Signal(object)

    def __init__(self, source, digest, image):
        super().__init__(); self.source, self.digest, self.image = source, digest, image

    def run(self):
        try:
            result = inspect_splash_replacement(self.source, self.digest, self.image)
        except Exception as exc:
            result = dict(supported=False, reason=str(exc))
        self.finished.emit(result)


class BuildWorker(QObject):
    progress = Signal(str, str, str)
    finished = Signal(object, str)

    def __init__(self, kwargs):
        super().__init__(); self.kwargs = kwargs

    def run(self):
        try:
            result = build_rom(progress=self.progress.emit, **self.kwargs)
            self.finished.emit(result, '')
        except Exception:
            self.finished.emit(None, traceback.format_exc())


class OperationView(QFrame):
    """Inline FPT progress on the left and unmodified transcript on the right."""
    def __init__(self, owner):
        super().__init__(); self.owner = owner
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        left = QWidget(); self.progress_layout = QVBoxLayout(left)
        self.progress_layout.setContentsMargins(0, 0, 12, 0)
        self.progress_layout.addWidget(owner.label('fpt.progress'))
        self.phase_host = QWidget(); self.phases_layout = QVBoxLayout(self.phase_host)
        self.phases_layout.setContentsMargins(0, 0, 0, 0); self.phases_layout.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.phase_host)
        self.progress_layout.addWidget(scroll, 1)
        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(owner.label('fpt.output'))
        self.console = QPlainTextEdit(); self.console.setReadOnly(True)
        right_layout.addWidget(self.console)
        split.addWidget(left); split.addWidget(right); split.setSizes([310, 600])
        split.setChildrenCollapsible(False); layout.addWidget(split, 1)
        self.setMinimumHeight(270)
        self.phases = {}; self.mode = None

    def reset(self, mode):
        self.mode = mode; self.console.clear(); self.phases.clear()
        while self.phases_layout.count() > 1:
            item = self.phases_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.status.setStyleSheet('')
        self.status.setText(self.owner.t('fpt.dumping' if mode == 'dump' else 'fpt.flashing'))

    def event_received(self, event):
        typ = event.get('type')
        if typ == 'transcript':
            self.console.appendPlainText(event.get('text', ''))
        elif typ == 'status':
            raw = event.get('text', '')
            # Backend status diagnostics stay intact; friendly summaries use IDs.
            log('FPT STATUS', raw)
            if 'recovery' in raw.lower() or 'restor' in raw.lower():
                self.status.setText(self.owner.t('fpt.recovering'))
        elif typ == 'phase':
            ident = (event.get('group', ''), event.get('key', ''))
            if ident not in self.phases:
                frame = QWidget(); row = QVBoxLayout(frame); row.setContentsMargins(0, 2, 0, 7)
                label = QLabel(); label.setWordWrap(True)
                bar = QProgressBar(); bar.setRange(0, 100)
                row.addWidget(label); row.addWidget(bar)
                self.phases_layout.insertWidget(self.phases_layout.count() - 1, frame)
                self.phases[ident] = (label, bar)
            label, bar = self.phases[ident]
            group = event.get('group', '').lower()
            group_key = ('fpt.backup1' if 'backup dump 1' in group else 'fpt.backup2' if 'backup dump 2' in group else
                         'fpt.recovery' if 'recovery' in group or 'restore' in group else
                         'fpt.target' if self.mode == 'flash' else 'fpt.operation')
            phase = str(event.get('key', '')).split(':')[0].lower()
            phase_key = {'read': 'fpt.read', 'erase': 'fpt.erase', 'program': 'fpt.program', 'verify': 'fpt.verify'}.get(phase, 'fpt.operation')
            label.setText(self.owner.t(group_key) + '\n' + self.owner.t(phase_key))
            label.setProperty('groupKey', group_key); label.setProperty('phaseKey', phase_key)
            bar.setValue(max(0, min(100, int(event.get('percent', 0)))))

    def retranslate(self):
        for label, _ in self.phases.values():
            label.setText(self.owner.t(label.property('groupKey')) + '\n' + self.owner.t(label.property('phaseKey')))
        if hasattr(self, '_result_translation'):
            key, values = self._result_translation
            self.status.setText(self.owner.t(key, **values))


class Window(QMainWindow):
    PAGE_TOOLS, PAGE_ROM, PAGE_ANALYSIS, PAGE_CREATE, PAGE_BUILDS, PAGE_FINISH = range(6)
    PAGE_OVERVIEW = PAGE_ANALYSIS

    def __init__(self, *, runtime_check=False):
        super().__init__()
        self.catalog = Catalog(); self.settings = QSettings('SenorX99', 'FirmwareToolkit')
        try:
            self.catalog.select(str(self.settings.value('language', 'en')))
        except (OSError, ValueError):
            pass
        self._bindings = []; self._initializing = True; self._updating = False; self._closing = False
        self._threads = {}; self._pending_open_path = None
        for name in ('cpu', 'inspect', 'logo', 'build', 'probe', 'download', 'bios'):
            setattr(self, name + '_thread', None); setattr(self, name + '_worker', None)
        self.rom_path = None; self.report = None; self.cpu_detection = None; self.fpt_probe = None
        self._all_tools_ready = False; self._resources_ready = False; self._resource_error = ''
        self._source_ready = False; self._config_complete = False; self._delivery_complete = False
        self._tbu_acknowledged = False; self._custom_logo = None; self._custom_logo_result = None
        self.last_built_rom = None; self.last_build_report = None; self._build_digest = None; self._build_plan = None
        self._bios_running = False; self._download_name = ''; self._download_result = None
        self._splash_user_choice = None; self.own_dump_hashes = set(); self._help_key = None
        self._build_progress_records = []; self._last_build_status = 'build.idle'; self._fpt_result = None
        self.setWindowTitle(self.t('app.title', version=__version__))
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        self.setMinimumSize(min(1000, area.width() - 30) if area else 1000,
                            min(700, area.height() - 40) if area else 700)
        self.resize(min(1380, area.width() - 40) if area else 1380,
                    min(930, area.height() - 50) if area else 930)
        self.setStyleSheet(STYLE); self.setAcceptDrops(True)
        shell = QWidget(); self.setCentralWidget(shell); outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(self._make_sidebar())
        self.stack = QStackedWidget(); outer.addWidget(self.stack, 1)
        self.language_flag_buttons = []; self.pages = []; self.page_bodies = []; self.continue_buttons = []; self.gate_labels = []
        for i in range(6): self._make_page(i)
        self._make_tools_page(); self._make_rom_page(); self._make_analysis_page()
        self._make_config_page(); self._make_build_page(); self._make_finish_page()
        self.refresh_drivers(); self._refresh_tools()
        self._initializing = False; self._refresh_state(); self._set_nav(0)
        if not runtime_check:
            self._launch('cpu', CpuWorker(), self._cpu_finished)
            if self._all_tools_ready: QTimer.singleShot(0, self._start_fpt_probe)

    def t(self, key, **values):
        return self.catalog.text(key, **values)

    def label(self, key, *, name=None):
        widget = QLabel(self.t(key)); widget.setWordWrap(True); widget.setTextFormat(Qt.PlainText)
        if name: widget.setObjectName(name)
        self._bindings.append((widget, key))
        return widget

    def button(self, key, callback=None, *, primary=False):
        widget = QPushButton(self.t(key))
        if primary: widget.setObjectName('primary')
        if callback: widget.clicked.connect(callback)
        self._bindings.append((widget, key))
        return widget

    def _make_sidebar(self):
        frame = QFrame(); frame.setObjectName('sidebar'); frame.setFixedWidth(285)
        layout = QVBoxLayout(frame); layout.setContentsMargins(15, 22, 15, 18); layout.setSpacing(8)
        brand = QLabel('SenorX99'); brand.setStyleSheet('font-size:22pt;font-weight:700;'); layout.addWidget(brand)
        layout.addWidget(self.label('app.subtitle', name='muted'))
        version = QLabel('v' + __version__); version.setObjectName('muted'); layout.addWidget(version)
        layout.addSpacing(18)
        self.nav_buttons = []; self.nav_marks = []; self.nav_labels = []
        for i in range(6):
            button = QPushButton(); button.setObjectName('nav'); button.setCheckable(True)
            button.setMinimumHeight(70)
            row = QHBoxLayout(button); row.setContentsMargins(8, 10, 8, 10)
            mark = QLabel(str(i + 1) + '.'); mark.setFixedWidth(43)
            text = QLabel(self.t(f'step.{i+1}.title')); text.setWordWrap(True)
            for label in (mark, text): label.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(mark); row.addWidget(text, 1)
            button.clicked.connect(lambda _checked=False, index=i: self._set_nav(index))
            layout.addWidget(button); self.nav_buttons.append(button); self.nav_marks.append(mark); self.nav_labels.append(text)
        layout.addStretch()
        layout.addWidget(self.label('app.language'))
        self.language_combo = QComboBox()
        for code, name in self.catalog.available(): self.language_combo.addItem(name, code)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(self.catalog.code)))
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        layout.addWidget(self.language_combo)
        return frame

    def _make_page(self, index):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(28, 25, 28, 24); layout.setSpacing(14)
        languages = QHBoxLayout(); languages.setSpacing(8)
        group = QButtonGroup(page); group.setExclusive(True)
        buttons = {}
        for code, name in (('en', 'English'), ('pt-BR', 'Português (Brasil)')):
            flag = QPushButton(); flag.setObjectName('languageFlag'); flag.setCheckable(True)
            flag.setIcon(language_flag(code)); flag.setIconSize(QSize(48, 32)); flag.setFixedSize(68, 46)
            flag.setToolTip(name); flag.setAccessibleName(name)
            flag.setChecked(self.catalog.code == code)
            flag.clicked.connect(lambda _checked=False, language=code: self._select_language(language))
            group.addButton(flag); languages.addWidget(flag); buttons[code] = flag
        languages.addStretch(); layout.addLayout(languages); self.language_flag_buttons.append(buttons)
        title = self.label(f'step.{index+1}.title', name='title'); layout.addWidget(title)
        row = QHBoxLayout(); row.setSpacing(20)
        row.addWidget(self.label(f'step.{index+1}.description', name='muted'), 1)
        button = self.button('common.finish' if index == 5 else 'common.continue', lambda: self._continue(index), primary=True)
        row.addWidget(button, alignment=Qt.AlignTop); layout.addLayout(row)
        gate = QLabel(); gate.setWordWrap(True); gate.setObjectName('notice'); layout.addWidget(gate)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); content = QVBoxLayout(body); content.setContentsMargins(0, 0, 7, 0); content.setSpacing(14)
        scroll.setWidget(body); layout.addWidget(scroll, 1)
        self.stack.addWidget(page); self.pages.append((page, content)); self.page_bodies.append(body)
        self.continue_buttons.append(button); self.gate_labels.append(gate)

    def _card(self, layout):
        frame = QFrame(); frame.setObjectName('card'); box = QVBoxLayout(frame); box.setContentsMargins(17, 15, 17, 15); box.setSpacing(11)
        layout.addWidget(frame); return box

    def _make_tools_page(self):
        layout = self.pages[0][1]; card = self._card(layout)
        self.tool_summary = QLabel(); self.tool_summary.setWordWrap(True); card.addWidget(self.tool_summary)
        self.tool_rows = {}; self.tool_hosts = {}
        for group in inventory.TOOL_GROUPS:
            host = QWidget(); row = QHBoxLayout(host); row.setContentsMargins(0, 5, 0, 5)
            name = QLabel(self.t('tools.fpt', version=inventory.TOOLS_BY_NAME['fptw64.exe'].version) if group.name == 'fpt' else group.label)
            name.setMinimumWidth(180); name.setWordWrap(True)
            status = QLabel(); status.setWordWrap(True)
            button = self.button('tools.download', lambda _checked=False, key=group.name: self._download_tool(key))
            row.addWidget(name, 2); row.addWidget(status, 2); row.addWidget(button)
            card.addWidget(host); self.tool_rows[group.name] = (status, button); self.tool_hosts[group.name] = host
        self.recheck_tools_btn = self.button('tools.recheck', self._recheck_tools); card.addWidget(self.recheck_tools_btn, alignment=Qt.AlignLeft)
        self.tool_folder_label = QLabel(); self.tool_folder_label.setWordWrap(True); card.addWidget(self.tool_folder_label)
        self.tool_download_status = QLabel(); self.tool_download_status.setWordWrap(True); card.addWidget(self.tool_download_status)
        self.tool_diagnostics = QPlainTextEdit(); self.tool_diagnostics.setReadOnly(True); self.tool_diagnostics.setMaximumHeight(170)
        self.tool_diagnostics.hide(); card.addWidget(self.tool_diagnostics); layout.addStretch()

    def _make_rom_page(self):
        layout = self.pages[1][1]
        self.fpt_status_label = QLabel(); self.fpt_status_label.setWordWrap(True); layout.addWidget(self.fpt_status_label)
        self.fpt_status_labels = [self.fpt_status_label]
        self.probe_button = self.button('fpt.check', self._start_fpt_probe); layout.addWidget(self.probe_button, alignment=Qt.AlignLeft)
        source = self._card(layout); top = QHBoxLayout(); left = QVBoxLayout()
        self.load_rom_btn = self.button('rom.browse', self._browse_rom, primary=True)
        self.dump_btn = self.button('rom.dump', self.start_dump)
        left.addWidget(self.load_rom_btn); left.addWidget(self.dump_btn); left.addStretch()
        top.addLayout(left, 1); self.drop_zone = DropZone(self); self.drop_zone.dropped.connect(self.load_rom)
        top.addWidget(self.drop_zone, 2); source.addLayout(top)
        self.loaded_rom_label = QLabel(); self.loaded_rom_label.setWordWrap(True); source.addWidget(self.loaded_rom_label)
        self.dump_view = OperationView(self); layout.addWidget(self.dump_view, 1)

    def _make_analysis_page(self):
        layout = self.pages[2][1]
        self.analysis_status = QLabel(); self.analysis_status.setWordWrap(True); layout.addWidget(self.analysis_status)
        self.analysis_meta = QLabel(); self.analysis_meta.setWordWrap(True); self.analysis_meta.setTextInteractionFlags(Qt.TextSelectableByMouse); layout.addWidget(self.analysis_meta)
        self.analysis_rows = {}
        card = self._card(layout)
        for key in FEATURES:
            row = QHBoxLayout(); name = self.label('feature.' + key); status = QLabel(); status.setWordWrap(True)
            row.addWidget(name, 3); row.addWidget(status, 2); card.addLayout(row); self.analysis_rows[key] = status
        row = QHBoxLayout(); row.addWidget(self.label('analysis.startup'), 3)
        self.fit_status_label = QLabel(); self.fit_status_label.setWordWrap(True); row.addWidget(self.fit_status_label, 2); card.addLayout(row)
        card.addWidget(self.label('analysis.startup_help', name='muted'))
        self.analysis_logo_reason = QLabel(); self.analysis_logo_reason.setWordWrap(True); card.addWidget(self.analysis_logo_reason)
        layout.addWidget(self.label('common.details'))
        self.analysis_diagnostics = QPlainTextEdit(); self.analysis_diagnostics.setReadOnly(True); self.analysis_diagnostics.setMaximumHeight(180)
        layout.addWidget(self.analysis_diagnostics); layout.addStretch()

    def _make_config_page(self):
        layout = self.pages[3][1]; mode_card = self._card(layout)
        self.recommended_mode = QRadioButton(); self.custom_mode = QRadioButton()
        self.mode_group = QButtonGroup(self); self.mode_group.addButton(self.recommended_mode); self.mode_group.addButton(self.custom_mode)
        mode_card.addWidget(self.recommended_mode); mode_card.addWidget(self.custom_mode)
        mode_card.addWidget(self.label('config.other_system', name='muted'))
        self.config_note = QLabel(); self.config_note.setWordWrap(True); mode_card.addWidget(self.config_note)
        self.recommended_mode.setChecked(True); self.recommended_mode.toggled.connect(self._mode_changed)
        split = QSplitter(Qt.Horizontal); split.setChildrenCollapsible(False)
        table = QFrame(); table.setObjectName('card'); rows = QVBoxLayout(table); rows.setContentsMargins(15, 12, 15, 12)
        self.feature_boxes = {}; self.na_labels = {}; self.help_buttons = {}
        for key in FEATURES:
            host = QWidget(); grid = QGridLayout(host); grid.setContentsMargins(0, 7, 0, 7)
            box = QCheckBox(); box.toggled.connect(lambda checked, feature=key: self._feature_changed(feature, checked))
            na = self.label('common.na'); na.setStyleSheet('color:#7f8996;font-weight:600;')
            check_host = QWidget(); checks = QVBoxLayout(check_host); checks.setContentsMargins(0, 0, 0, 0); checks.addWidget(box); checks.addWidget(na)
            grid.addWidget(check_host, 0, 0); grid.addWidget(self.label('feature.' + key), 0, 1, 1, 1 if key in ('tbu', 'splash') else 2)
            help_button = QPushButton('?'); help_button.setObjectName('help'); help_button.setFixedSize(29, 29)
            help_button.clicked.connect(lambda _checked=False, feature=key: self._show_help(feature)); grid.addWidget(help_button, 0, 3)
            grid.setColumnStretch(1, 1)
            if key == 'tbu':
                self.driver_combo = QComboBox(); self.driver_combo.currentIndexChanged.connect(self._plan_changed)
                self.driver_combo.setMinimumContentsLength(13)
                self.driver_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                grid.addWidget(self.driver_combo, 0, 2)
            if key == 'splash':
                radios = QWidget(); radio_layout = QHBoxLayout(radios); radio_layout.setContentsMargins(0, 0, 0, 0)
                self.auto_splash_box = QRadioButton(); self.custom_splash_box = QRadioButton()
                self.logo_group = QButtonGroup(self); self.logo_group.addButton(self.auto_splash_box); self.logo_group.addButton(self.custom_splash_box)
                self.auto_splash_box.setChecked(True)
                radio_layout.addWidget(self.auto_splash_box); radio_layout.addWidget(self.custom_splash_box)
                self.custom_splash_box.clicked.connect(self._browse_splash)
                self.auto_splash_box.toggled.connect(self._logo_mode_changed)
                grid.addWidget(radios, 0, 2)
                self.custom_image_label = QLabel(); self.custom_image_label.setWordWrap(True); grid.addWidget(self.custom_image_label, 2, 1, 1, 2)
            rows.addWidget(host); self.feature_boxes[key] = box; self.na_labels[key] = na; self.help_buttons[key] = help_button
        rows.addStretch()
        self.tbu_box = self.feature_boxes['tbu']; self.sb_box = self.feature_boxes['secureboot']
        self.optimized_box = self.feature_boxes['optimized']; self.overclock_box = self.feature_boxes['overclock']; self.splash_box = self.feature_boxes['splash']
        right = QFrame(); right.setObjectName('card'); help_layout = QVBoxLayout(right); help_layout.setContentsMargins(18, 15, 18, 15)
        self.help_title = self.label('help.title'); self.help_title.setStyleSheet('font-size:13pt;font-weight:650;'); help_layout.addWidget(self.help_title)
        self.help_text = self.label('help.prompt'); self.help_text.setTextInteractionFlags(Qt.TextSelectableByMouse); help_layout.addWidget(self.help_text)
        self.logo_preview = QLabel(); self.logo_preview.setAlignment(Qt.AlignCenter); self.logo_preview.setMaximumHeight(150); help_layout.addWidget(self.logo_preview)
        help_layout.addStretch(); split.addWidget(table)
        help_scroll = QScrollArea(); help_scroll.setWidgetResizable(True); help_scroll.setWidget(right)
        split.addWidget(help_scroll); split.setSizes([550, 400]); layout.addWidget(split, 1)
        row = QHBoxLayout(); self.driver_folder_label = QLabel(); self.driver_folder_label.setWordWrap(True)
        row.addWidget(self.driver_folder_label, 1); self.refresh_profiles_btn = self.button('config.refresh_profiles', self._refresh_profiles_clicked); row.addWidget(self.refresh_profiles_btn)
        layout.addLayout(row)

    def _make_build_page(self):
        layout = self.pages[4][1]; card = self._card(layout)
        self.build_review = QLabel(); self.build_review.setWordWrap(True); card.addWidget(self.build_review)
        card.addWidget(self.label('build.startup', name='muted'))
        self.output_label = QLabel(); self.output_label.setWordWrap(True); card.addWidget(self.output_label)
        self.output_btn = self.button('build.choose_output', self._browse_output); card.addWidget(self.output_btn, alignment=Qt.AlignLeft)
        self.build_btn = self.button('build.start', self.start_build, primary=True); card.addWidget(self.build_btn, alignment=Qt.AlignLeft)
        self.build_state_banner = QLabel(); self.build_state_banner.setWordWrap(True); layout.addWidget(self.build_state_banner)
        self.build_progress = QProgressBar(); self.build_progress.setRange(0, 100); layout.addWidget(self.build_progress)
        self.build_phase_label = QLabel(); self.build_phase_label.setWordWrap(True); layout.addWidget(self.build_phase_label)
        self.build_log = QPlainTextEdit(); self.build_log.setReadOnly(True); layout.addWidget(self.build_log, 1)
        self.open_report_btn = self.button('common.open_report', self._open_report); layout.addWidget(self.open_report_btn, alignment=Qt.AlignLeft)
        self.output_path = None; self.output_manual = False; self.progress_tracker = None

    def _make_finish_page(self):
        layout = self.pages[5][1]; self.finish_status = QLabel(); self.finish_status.setWordWrap(True); layout.addWidget(self.finish_status)
        row = QHBoxLayout(); self.save_btn = self.button('save.button', self.save_rom, primary=True)
        self.flash_btn = self.button('save.flash', self.start_flash)
        self.open_output_btn = self.button('common.open_folder', self._open_output_folder)
        row.addWidget(self.save_btn); row.addWidget(self.flash_btn); row.addWidget(self.open_output_btn); row.addStretch(); layout.addLayout(row)
        self.finish_fpt_status = QLabel(); self.finish_fpt_status.setWordWrap(True); layout.addWidget(self.finish_fpt_status)
        self.fpt_status_labels.append(self.finish_fpt_status)
        self.flash_view = OperationView(self); layout.addWidget(self.flash_view, 1)
        self.saved_path = None

    # ---- task lifetimes and language changes ----
    def _launch(self, name, worker, callback):
        if getattr(self, name + '_thread') is not None: return False
        thread = QThread(self); setattr(self, name + '_thread', thread); setattr(self, name + '_worker', worker)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.finished.connect(callback); worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._stopped(name))
        thread.finished.connect(thread.deleteLater); thread.start(); self._refresh_state()
        return True

    def _stopped(self, name):
        setattr(self, name + '_thread', None); setattr(self, name + '_worker', None)
        if name == 'bios':
            self._bios_running = False
            result = self._fpt_result
            if result and self._bios_mode == 'dump' and result.success:
                self.own_dump_hashes.add(result.sha256); self.load_rom(result.output_path)
            elif result and self._bios_mode == 'flash' and result.success:
                self._delivery_complete = True
        if name == 'download':
            self._refresh_tools()
            if self._download_result and self._download_result['success'] and self._download_name == 'fpt':
                self._start_fpt_probe()
        self._refresh_state(); self._resume_pending_open()
        if self._closing and not self._any_thread_running(): self.close()

    def _operation_busy(self):
        return any(getattr(self, name + '_thread') is not None for name in ('inspect', 'logo', 'build', 'download', 'bios'))

    def _any_thread_running(self):
        return any(getattr(self, name + '_thread') is not None for name in ('cpu', 'inspect', 'logo', 'build', 'download', 'bios', 'probe'))

    def closeEvent(self, event):
        if self._operation_busy():
            self._message('common.busy'); event.ignore()
        elif self._any_thread_running():
            self._closing = True; event.ignore()
        else:
            event.accept()

    def _language_changed(self):
        if self._initializing: return
        self._select_language(self.language_combo.currentData())

    def _select_language(self, code):
        if self._initializing: return
        try:
            self.catalog.select(code)
        except (OSError, ValueError):
            self._message('error.language'); self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(self.language_combo.findData(self.catalog.code)); self.language_combo.blockSignals(False)
            self._sync_language_flags(); return
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(self.language_combo.findData(code))
        self.language_combo.blockSignals(False)
        self._sync_language_flags()
        self.settings.setValue('language', code)
        for widget, key in self._bindings: widget.setText(self.t(key))
        self.setWindowTitle(self.t('app.title', version=__version__))
        self._update_driver_labels(); self._refresh_tools(); self._populate_analysis()
        self.dump_view.retranslate(); self.flash_view.retranslate()
        if self._help_key: self._show_help(self._help_key)
        self._refresh_state()

    def _sync_language_flags(self):
        for buttons in self.language_flag_buttons:
            for code, button in buttons.items():
                button.setChecked(code == self.catalog.code)

    # ---- state, gates and navigation ----
    def completed_steps(self):
        first = self._all_tools_ready
        second = first and self._source_ready
        third = second and self.report is not None
        fourth = third and self._config_complete
        fifth = fourth and self.last_built_rom is not None and self._build_digest is not None
        return [first, second, third, fourth, fifth, fifth and self._delivery_complete]

    def _set_nav(self, index):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons): button.setChecked(i == index)
        self._refresh_state()

    def _continue(self, index):
        if self._operation_busy(): return
        if index == 3:
            if not self._accept_configuration(): return
        if not self.completed_steps()[index]: return
        if index == 5: self.close()
        else: self._set_nav(index + 1)

    def _refresh_state(self):
        if self._initializing or self._updating: return
        self._updating = True
        try:
            done = self.completed_steps(); busy = self._operation_busy()
            for i in range(6):
                enabled = (i == 0 or done[i - 1]) and not busy
                # Analysis remains readable while its inspection is running.
                self.page_bodies[i].setEnabled(enabled or (i == 2 and self.inspect_thread is not None))
                self.nav_marks[i].setText(str(i + 1) + ('. ✓' if done[i] else '.'))
                self.nav_marks[i].setStyleSheet('color:#198046;font-weight:700;' if done[i] else 'color:#64778b;font-weight:600;')
                self.nav_labels[i].setText(self.t(f'step.{i+1}.title'))
                gate = self.gate_labels[i]
                locked = i > 0 and not done[i - 1]
                gate.setVisible(locked or busy)
                gate.setText(self.t('common.locked', number=i) if locked else self.t('common.busy'))
                can_continue = done[i]
                if i == 3: can_continue = done[2] and self._configuration_valid()
                self.continue_buttons[i].setEnabled(bool(can_continue and not busy))
            ready = self._all_tools_ready and not busy
            self.drop_zone.setEnabled(ready); self.load_rom_btn.setEnabled(ready)
            fpt_ready = bool(self.fpt_probe and self.fpt_probe.get('ready') and self.probe_thread is None)
            self.dump_btn.setEnabled(ready and fpt_ready)
            self.probe_button.setEnabled(ready and self.probe_thread is None)
            self.recheck_tools_btn.setEnabled(not busy)
            for key, (label, button) in self.tool_rows.items():
                button.setEnabled(not busy and not self._tool_ready.get(key, False))
            self.loaded_rom_label.setText(self.t('rom.selected', path=self.rom_path) if self.rom_path else self.t('common.none'))
            self._update_fpt_status_labels(); self._refresh_configuration()
            self.build_btn.setEnabled(done[3] and ready)
            self.output_btn.setEnabled(done[3] and not busy)
            self.build_state_banner.setText(self.t(self._last_build_status))
            self.build_review.setText(self._review_text())
            self.output_label.setText(self.t('build.destination', path=self.output_path) if self.output_path else '')
            built = done[4]
            self.save_btn.setEnabled(built and not busy)
            self.flash_btn.setEnabled(built and ready and fpt_ready)
            self.open_output_btn.setEnabled(built and not busy)
            self.open_report_btn.setEnabled(bool(self.last_build_report and not busy))
            if self.saved_path and self._delivery_complete:
                self.finish_status.setText(self.t('save.success', path=self.saved_path))
            else:
                self.finish_status.setText(self.t('save.ready', path=self.last_built_rom) if built else self.t('status.not_built'))
        finally:
            self._updating = False

    def _invalidate_build(self):
        self._config_complete = False; self.last_built_rom = None; self._build_digest = None
        self._delivery_complete = False; self.saved_path = None; self._last_build_status = 'build.idle'

    # ---- required files ----
    def _refresh_tools(self):
        previous = self._all_tools_ready
        groups = inventory.scan_tool_groups(); self._tool_ready = {}
        for item in groups:
            key = item['name']; self._tool_ready[key] = item['ready']
            if key not in self.tool_rows: continue
            label, button = self.tool_rows[key]
            members = item['files']; count = sum(member['state'] == 'ready' for member in members)
            state_key = 'common.ready' if item['ready'] else 'common.missing' if all(m['state'] == 'missing' for m in members) else 'common.incorrect'
            text = self.t(state_key)
            if len(members) > 1 and not item['ready'] and count: text = self.t('tools.partial', ready=count, total=len(members))
            label.setText(text); label.setToolTip('\n'.join(m['name'] for m in members if m['state'] != 'ready'))
            label.setStyleSheet('color:#188044;' if item['ready'] else 'color:#a52222;')
            button.setText(self.t('tools.installed' if item['ready'] else 'tools.download'))
        try:
            from validate_build_inputs import validate
            validate(app_directory() if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent)
            self._resources_ready = True; self._resource_error = ''
        except Exception as exc:
            self._resources_ready = False; self._resource_error = str(exc)
        self._all_tools_ready = bool(groups) and all(g['ready'] for g in groups) and self._resources_ready
        self.tool_summary.setText(self.t('tools.resources_missing' if not self._resources_ready else 'tools.all_ready' if self._all_tools_ready else 'tools.required'))
        self.tool_folder_label.setText(self.t('tools.folder', path=app_directory() / 'tools'))
        if self._resource_error: self.tool_diagnostics.setPlainText(self._resource_error); self.tool_diagnostics.show()
        if previous and not self._all_tools_ready: self._invalidate_build()
        self._refresh_state()

    def _require_all_tools(self, *, notify=True):
        self._refresh_tools()
        if not self._all_tools_ready and notify: self._message('tools.required')
        return self._all_tools_ready

    def _rom_input_allowed(self):
        return self._all_tools_ready and not self._operation_busy()

    def _recheck_tools(self):
        if self._operation_busy(): return
        self._refresh_tools(); self.refresh_drivers()
        if self._all_tools_ready: self._start_fpt_probe()

    def _confirm_tool_download(self, name):
        dialog = ToolDownloadConfirmation(self, name)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _download_tool(self, name):
        if self._operation_busy(): return
        if not self._confirm_tool_download(name): return
        self._download_name = name; self._download_result = None
        self.tool_download_status.setText(self.t('tools.downloading', tool=inventory.GROUPS_BY_NAME[name].label))
        worker = ToolDownloadWorker(name)
        worker.progress.connect(lambda message: log('DOWNLOAD PROGRESS', message))
        self._launch('download', worker, self._download_finished)

    def _download_finished(self, result):
        self._download_result = result
        if result['success']:
            self.tool_download_status.setText(self.t('tools.success', tool=inventory.GROUPS_BY_NAME[self._download_name].label))
        else:
            self.tool_download_status.setText(self.t('tools.failure'))
            self.tool_diagnostics.setPlainText(result.get('error', '')); self.tool_diagnostics.show()
        # _stopped refreshes files and may probe FPT after the worker has ended.

    # ---- CPU and recommendations ----
    def _cpu_finished(self, result):
        self.cpu_detection = result
        log('CPU', ' / '.join(p.name for p in result.processors) or result.error)
        if self.recommended_mode.isChecked() and self.report: self._apply_recommended()
        self._refresh_state()

    def driver_paths(self):
        folder = app_directory() / 'drivers'
        if not getattr(sys, 'frozen', False): folder = Path(__file__).resolve().parent.parent / 'drivers'
        self.driver_folder = folder
        result = []
        for path in sorted(folder.glob('*.ffs')):
            try: inspect_driver(path); result.append(path)
            except Exception: pass
        return result

    def refresh_drivers(self):
        old = self.driver_combo.currentData(); self.driver_combo.blockSignals(True); self.driver_combo.clear()
        self._driver_info = []
        for path in self.driver_paths():
            uv = inspect_driver(path).undervolt_mv; self._driver_info.append((path, uv)); self.driver_combo.addItem('', str(path))
        self._update_driver_labels()
        self._default_driver_index = next((i for i, (_, uv) in enumerate(self._driver_info)
            if (uv['Core'], uv['Cache'], uv['System Agent']) == (-60, -50, -50)), -1)
        index = self.driver_combo.findData(old) if old else -1
        self.driver_combo.setCurrentIndex(index if index >= 0 else self._default_driver_index)
        self.driver_combo.blockSignals(False)
        if not self._initializing: self._plan_changed()

    def _update_driver_labels(self):
        for i, (_, uv) in enumerate(getattr(self, '_driver_info', [])):
            self.driver_combo.setItemText(i, self.t('config.uv_profile', core=uv['Core'], cache=uv['Cache'], agent=uv['System Agent']))

    def _refresh_profiles_clicked(self):
        if self._operation_busy(): return
        self.refresh_drivers()
        if self.recommended_mode.isChecked(): self._apply_recommended()

    def _available(self):
        result = firmware_availability(self.report)
        result['tbu'] = result['tbu'] and bool(self.driver_combo.count())
        result['splash'] = self._logo_verified()
        return result

    def _apply_recommended(self):
        if not self.report: return
        self._updating = True
        try:
            self.auto_splash_box.setChecked(True)
            self.driver_combo.setCurrentIndex(self._default_driver_index)
            selected = set(recommended_modifications(self.cpu_detection or CpuDetection(), self.report))
            if self._default_driver_index < 0: selected.discard('tbu')
            available = self._available()
            for key, box in self.feature_boxes.items():
                box.setChecked(key in selected and available[key])
        finally:
            self._updating = False
        self._invalidate_build(); self._update_default_output(); self._refresh_state()

    def _mode_changed(self, recommended):
        if self._initializing or self._updating: return
        if recommended: self._apply_recommended()
        else: self._invalidate_build(); self._refresh_state()

    def _refresh_configuration(self):
        cpu_name = self.cpu_detection.display_name if self.cpu_detection and self.cpu_detection.processors else self.t('config.cpu_unknown')
        self.recommended_mode.setText(self.t('config.recommended', cpu=cpu_name))
        self.custom_mode.setText(self.t('config.custom'))
        recommended = self.recommended_mode.isChecked(); available = self._available()
        editable = not recommended and not self._operation_busy()
        for key, box in self.feature_boxes.items():
            na = not available[key] or (recommended and key == 'tbu' and self._default_driver_index < 0)
            box.setVisible(not na); self.na_labels[key].setVisible(na)
            box.setEnabled(editable and available[key])
            self.help_buttons[key].setToolTip(self.t('feature.' + key))
        self.driver_combo.setEnabled(editable and available['tbu'] and self.tbu_box.isChecked())
        self.auto_splash_box.setText(self.t('config.logo_recommended')); self.custom_splash_box.setText(self.t('config.logo_custom'))
        self.auto_splash_box.setEnabled(editable); self.custom_splash_box.setEnabled(editable)
        self.refresh_profiles_btn.setEnabled(editable)
        self.driver_folder_label.setText(self.t('config.drivers_folder', path=self.driver_folder))
        note = self.t('config.recommended_lock') if recommended else self.t('config.other_system')
        if self.cpu_detection is None: note = self.t('config.cpu_checking') + '\n' + note
        elif not self.cpu_detection.processors: note = self.t('config.cpu_unknown_detail') + '\n' + note
        if recommended and self.cpu_detection and self.cpu_detection.recommend_tbu and self._default_driver_index < 0:
            note += '\n' + self.t('config.no_default_driver')
        if self.report and not any(box.isChecked() for box in self.feature_boxes.values()): note += '\n' + self.t('config.no_changes')
        self.config_note.setText(note)
        if self.logo_thread is not None: self.custom_image_label.setText(self.t('config.logo_checking'))
        elif self.custom_splash_box.isChecked() and self._custom_logo:
            self.custom_image_label.setText(str(self._custom_logo) + '\n' + (self.t('config.logo_ok') if self._logo_verified() else self.t('config.logo_failed', reason=self._logo_reason(self._custom_logo_result or {}))))
        else: self.custom_image_label.setText('')

    def _feature_changed(self, key, checked):
        if self._initializing or self._updating: return
        if key == 'splash': self._splash_user_choice = bool(checked)
        if key == 'tbu': self._tbu_acknowledged = False
        if key == 'tbu' and checked and not self.recommended_mode.isChecked():
            if not self._confirm_tbu():
                self.tbu_box.blockSignals(True); self.tbu_box.setChecked(False); self.tbu_box.blockSignals(False)
        self._plan_changed()

    def _plan_changed(self, *args):
        if self._initializing or self._updating: return
        self._invalidate_build(); self._update_default_output(); self._refresh_state()

    def _configuration_valid(self):
        if not self.report or not self._all_tools_ready or self.logo_thread is not None: return False
        if self.recommended_mode.isChecked() and self.cpu_detection is None: return False
        available = self._available()
        if any(box.isChecked() and not available[key] for key, box in self.feature_boxes.items()): return False
        if self.tbu_box.isChecked() and not self.driver_combo.currentData(): return False
        return True

    def _confirm_tbu(self):
        if self._tbu_acknowledged: return True
        self._tbu_acknowledged = self._message('config.tbu_warning', title='config.tbu_warning_title', confirm=True)
        return self._tbu_acknowledged

    def _accept_configuration(self):
        if not self._require_all_tools() or not self._configuration_valid(): return False
        if self.tbu_box.isChecked() and not self._confirm_tbu(): return False
        self._config_complete = True; self._update_default_output(); self._refresh_state(); return True

    def _show_help(self, key):
        self._help_key = key; self.help_title.setText(self.t('feature.' + key)); self.logo_preview.clear()
        if key == 'optimized':
            from .optimized import OPTIMIZED_SETTINGS
            settings = self.report.optimized.get('settings') if self.report else None
            paragraphs = [self.t('help.optimized_intro')]
            for setting in settings or OPTIMIZED_SETTINGS:
                target = {'Disable': 'setting.disabled', 'Enable': 'setting.enabled', 'Performance': 'setting.performance', 'BIOS': 'setting.bios'}.get(setting['desired_label'])
                value = self.t(target) if target else setting['desired_label']
                paragraphs.append(self.t('help.setting', name=setting['name'], value=value, reason=self.t('reason.' + setting['name'])))
            paragraphs.append(self.t('help.optimized_end')); self.help_text.setText('\n\n'.join(paragraphs))
        else:
            self.help_text.setText(self.t('help.' + key))
        if key == 'splash':
            image = self._custom_logo if self.custom_splash_box.isChecked() else self._asset('assets/SenorSpace.bmp')
            if image:
                pix = QPixmap(str(image))
                if not pix.isNull(): self.logo_preview.setPixmap(pix.scaled(320, 145, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ---- ROM input and analysis ----
    def dragEnterEvent(self, event):
        if self._rom_input_allowed() and _dropped_file(event): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event): self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = _dropped_file(event)
        if path and self._rom_input_allowed() and self._require_all_tools(notify=False):
            self.load_rom(path); event.acceptProposedAction()
        else: event.ignore()

    def _browse_rom(self):
        if self._operation_busy() or not self._require_all_tools(): return
        path = self._file_dialog('rom.choose', 'rom.filter')
        if path: self.load_rom(path)

    def load_rom(self, path_string):
        if self._operation_busy() or not self._require_all_tools(): return
        path = Path(path_string).resolve()
        if not path.is_file() or path.suffix.lower() not in ('.rom', '.bin'):
            self._message('rom.load_invalid'); return
        self._invalidate_build(); self.rom_path = path; self.report = None; self._source_ready = True
        self._updating = True
        self.recommended_mode.setChecked(True); self.auto_splash_box.setChecked(True)
        self._updating = False
        self._tbu_acknowledged = False; self._splash_user_choice = None; self._custom_logo = None; self._custom_logo_result = None
        self.output_manual = False; self.output_path = None
        self.analysis_status.setText(self.t('analysis.start')); self.analysis_diagnostics.clear(); self._set_nav(self.PAGE_ANALYSIS)
        self._launch('inspect', InspectWorker(path), self._inspection_finished)

    def _inspection_finished(self, report, error):
        self.report = report
        if not report:
            self.analysis_status.setText(self.t('analysis.failed')); self.analysis_diagnostics.setPlainText(error)
            self._refresh_state(); return
        self.analysis_status.setText(self.t('analysis.finished'))
        self._populate_analysis()
        if self.recommended_mode.isChecked(): self._apply_recommended()
        else:
            self._updating = True
            try:
                available = self._available()
                for key, box in self.feature_boxes.items(): box.setChecked(key == 'splash' and available[key])
            finally: self._updating = False
        self._update_default_output(); self._refresh_state()

    def _populate_analysis(self):
        if not self.report: return
        r = self.report; available = firmware_availability(r)
        for key in FEATURES:
            status = 'common.not_applied'
            if not available[key]: status = 'common.unavailable'
            elif key == 'tbu': status = 'common.already' if r.tbu.get('status') == 'PASS' else 'analysis.tbu_partial' if r.tbu.get('status') == 'PARTIAL' else status
            elif key == 'secureboot': status = 'analysis.sb_current' if r.secure_boot.get('has_complete_2023_set') and not r.secure_boot.get('has_ami_test_pk') else 'analysis.sb_update'
            elif key in ('optimized', 'overclock'): status = 'common.already' if getattr(r, key).get('matches') else status
            elif key == 'splash': status = 'analysis.logo_current' if r.splash.get('already_present') else 'analysis.logo_ready'
            self.analysis_rows[key].setText(self.t(status))
            self.analysis_rows[key].setStyleSheet('color:#1d7c43;' if status in ('common.already', 'analysis.sb_current', 'analysis.logo_current', 'analysis.logo_ready') else 'color:#5b7087;')
        self.analysis_meta.setText(self.t('analysis.hash', size=f'{r.rom_size/(1024*1024):.1f}', sha=r.rom_sha256))
        fit_key = 'analysis.startup_ok' if r.fit.get('status') == 'PASS' else 'analysis.startup_repair' if r.fit.get('repairable') else 'analysis.startup_bad'
        self.fit_status_label.setText(self.t(fit_key))
        self.analysis_logo_reason.setText('' if r.splash.get('supported') else self.t('config.logo_failed', reason=self._logo_reason(r.splash)))
        self.analysis_diagnostics.setPlainText('\n'.join(str(row) for row in r.rows) + '\n' + '\n'.join(r.errors) + '\n' + str(r.splash.get('reason', '')))

    def _asset(self, relative):
        root = app_directory() if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        return root / relative

    def _logo_verified(self):
        if not self.report: return False
        trial = self.report.splash if self.auto_splash_box.isChecked() else self._custom_logo_result or {}
        tool = discover_tools().changelogo
        try:
            valid = bool(tool and trial.get('supported') and trial.get('source_sha256') == self.report.rom_sha256 and trial.get('tool_sha256') == sha256_file(tool))
            if self.custom_splash_box.isChecked():
                valid = valid and bool(self._custom_logo and trial.get('image_sha256') == sha256_file(self._custom_logo))
            return valid
        except OSError: return False

    def _logo_reason(self, trial):
        raw = trial.get('reason', '')
        if 'ROM is byte-identical' in raw: return self.t('config.logo_no_change')
        if 'test-fit command' in raw: return self.t('config.logo_no_fit')
        return self.t('config.logo_generic')

    def _logo_mode_changed(self, automatic):
        if self._initializing or self._updating: return
        self._updating = True
        try: self.splash_box.setChecked(automatic and self._logo_verified())
        finally: self._updating = False
        self._plan_changed()

    def _browse_splash(self):
        if self.recommended_mode.isChecked() or self._operation_busy() or not self.report: return
        path = self._file_dialog('config.logo_choose', 'config.logo_filter')
        if not path:
            self.auto_splash_box.setChecked(True); return
        self.custom_splash_box.setChecked(True)
        self.auto_splash_box.setChecked(False)
        self._custom_logo = Path(path).resolve(); self._custom_logo_result = None
        self.splash_box.setChecked(False); self._invalidate_build()
        self._launch('logo', LogoWorker(self.rom_path, self.report.rom_sha256, self._custom_logo), self._custom_logo_finished)

    def _custom_logo_finished(self, result):
        self._custom_logo_result = result
        self.splash_box.setChecked(self._logo_verified())
        if not result.get('supported'): self.analysis_diagnostics.appendPlainText(str(result.get('reason', '')))
        self._refresh_state()

    # ---- build and save ----
    def _plan(self):
        return dict(**{key: box.isChecked() for key, box in self.feature_boxes.items()},
            driver=self.driver_combo.currentData() if self.tbu_box.isChecked() else None,
            image=str(self._custom_logo) if self.splash_box.isChecked() and self.custom_splash_box.isChecked() else None)

    def _review_text(self):
        choices = [self.t('feature.' + key) for key in FEATURES if self.feature_boxes[key].isChecked()]
        return self.t('build.review', changes='\n'.join('• ' + value for value in choices)) if choices else self.t('build.copy')

    def _update_default_output(self):
        if not self.rom_path or self.output_manual: return
        self.output_path = default_output(self.rom_path, apply_tbu=self.tbu_box.isChecked(), apply_secureboot=self.sb_box.isChecked(),
            apply_optimized=self.optimized_box.isChecked(), apply_overclock=self.overclock_box.isChecked(), apply_splash=self.splash_box.isChecked(), repair_fit=True)

    def _browse_output(self):
        if self._operation_busy(): return
        path = self._file_dialog('build.choose_output', 'rom.filter', save=True, start=str(self.output_path or ''))
        if path:
            self.output_path = Path(path); self.output_manual = True; self._refresh_state()

    def start_build(self):
        if self._operation_busy() or not self._require_all_tools() or not self.completed_steps()[3]: return
        if not self._configuration_valid(): self._invalidate_build(); self._refresh_state(); return
        if self.tbu_box.isChecked() and not self._confirm_tbu(): return
        if sha256_file(self.rom_path) != self.report.rom_sha256:
            self._invalidate_build(); self._message('error.changed'); return
        self._update_default_output()
        output = self.output_path
        if output.resolve() == self.rom_path.resolve() or (output.exists() and self.rom_path.samefile(output)):
            self._message('error.source_output'); return
        if output.exists() and not self._message('save.overwrite', confirm=True, path=output): return
        self._delivery_complete = False; self.saved_path = None; self.last_built_rom = None; self._build_digest = None
        self._build_plan = self._plan(); self._last_build_status = 'build.running'; self.build_log.clear(); self.build_progress.setValue(0)
        self._driver_hash_at_build = sha256_file(Path(self.driver_combo.currentData())) if self.tbu_box.isChecked() else None
        kwargs = dict(source=self.rom_path, output=output, tools=discover_tools(),
            apply_tbu=self.tbu_box.isChecked(), driver=Path(self.driver_combo.currentData()) if self.tbu_box.isChecked() else None,
            apply_secureboot=self.sb_box.isChecked(), donor_source=self._asset('secureboot_donors'),
            apply_optimized=self.optimized_box.isChecked(), apply_overclock=self.overclock_box.isChecked(),
            apply_splash=self.splash_box.isChecked(), splash_image=None if self.auto_splash_box.isChecked() else self._custom_logo, repair_fit=True)
        self.progress_tracker = BuildProgressTracker(kwargs); worker = BuildWorker(kwargs)
        worker.progress.connect(self._on_build_progress); self._launch('build', worker, self._build_finished)

    def _on_build_progress(self, name, status, details):
        self.build_log.appendPlainText(f'[{status}] {name}: {details}')
        lowered = name.lower()
        key = ('tbu' if any(word in lowered for word in ('06f2', 'c-state', 's3turbo', 'tbu')) else
            'secureboot' if any(word in lowered for word in ('secure boot', 'pkvar', 'kekvar', 'dbvar')) else
            'optimized' if 'optimized' in lowered else 'overclock' if 'multiplier' in lowered else
            'splash' if 'splash' in lowered else 'fit' if 'fit' in lowered else
            'final' if 'final' in lowered or 'report' in lowered else 'preflight')
        self._build_phase_key = 'build.phase.' + key
        self.build_phase_label.setText(self.t(self._build_phase_key))
        if self.progress_tracker: self.build_progress.setValue(self.progress_tracker.update(name, status, details))

    def _build_finished(self, report, error):
        self.last_build_report = report
        if report and report.success and self._plan() == self._build_plan:
            path = Path(report.output_path)
            try:
                valid = path.is_file() and path.stat().st_size == self.report.rom_size and sha256_file(path) == report.output_sha256
            except OSError: valid = False
            if valid:
                self.last_built_rom = path; self._build_digest = report.output_sha256; self._last_build_status = 'build.success'
                self.build_progress.setValue(100)
            else: self._last_build_status = 'build.failed'
        else: self._last_build_status = 'build.failed'
        if self._last_build_status == 'build.failed': self.build_log.appendPlainText(report.error if report else error)
        self._refresh_state()

    def _verified_build(self):
        try:
            valid = bool(self.completed_steps()[4] and self.last_built_rom.is_file() and sha256_file(self.last_built_rom) == self._build_digest
                and self._plan() == self._build_plan and sha256_file(self.rom_path) == self.report.rom_sha256)
            if self.splash_box.isChecked(): valid = valid and self._logo_verified()
            if self.tbu_box.isChecked():
                valid = valid and sha256_file(Path(self.driver_combo.currentData())) == self._driver_hash_at_build
            return valid
        except (OSError, TypeError, AttributeError): return False

    def save_rom(self):
        if self._operation_busy() or not self.completed_steps()[4]: return
        if not self._verified_build(): self._message('error.changed'); self._invalidate_build(); self._refresh_state(); return
        path = self._file_dialog('save.choose', 'rom.filter', save=True, start=str(self.last_built_rom))
        if not path: return
        destination = Path(path).resolve()
        if destination == self.rom_path.resolve() or (destination.exists() and destination.samefile(self.rom_path)):
            self._message('error.source_output'); return
        temporary = None
        try:
            if destination != self.last_built_rom.resolve() and not (destination.exists() and destination.samefile(self.last_built_rom)):
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix='.tmp') as handle:
                    temporary = Path(handle.name)
                    with self.last_built_rom.open('rb') as source: shutil.copyfileobj(source, handle)
                    handle.flush(); os.fsync(handle.fileno())
                if sha256_file(temporary) != self._build_digest: raise ValueError('Saved copy hash mismatch')
                os.replace(temporary, destination)
            if sha256_file(destination) != self._build_digest: raise ValueError('Saved file changed')
            self.saved_path = destination; self._delivery_complete = True
        except Exception as exc:
            self.flash_view.console.appendPlainText(str(exc)); self._message('save.failed')
        finally:
            if temporary: temporary.unlink(missing_ok=True)
        self._refresh_state()

    # ---- inline hardware operations ----
    def _start_fpt_probe(self):
        if self._operation_busy() or self.probe_thread is not None or not self._require_all_tools(notify=False): return
        self.fpt_probe = None; self._launch('probe', fpt_gui.FptProbeWorker(), self._fpt_probe_finished)

    def _fpt_probe_finished(self, result):
        self.fpt_probe = result
        probe = result.get('probe') or {}
        for line in probe.get('transcript', []): self.dump_view.console.appendPlainText(line)
        self._refresh_state()

    def _update_fpt_status_labels(self):
        result = self.fpt_probe
        if self.probe_thread is not None: key = 'fpt.checking'
        elif result is None: key = 'fpt.unknown'
        elif result.get('ready'): key = 'fpt.ready'
        elif result.get('reason') == 'memory-integrity-block': key = 'fpt.memory_integrity'
        elif result.get('reason') == 'driver-certificate-revoked': key = 'fpt.revoked'
        else: key = 'fpt.blocked'
        for label in self.fpt_status_labels:
            label.setText(self.t(key)); label.setStyleSheet('color:#218044;' if key == 'fpt.ready' else 'color:#a52222;' if key in ('fpt.memory_integrity', 'fpt.revoked', 'fpt.blocked') else 'color:#5a6b7d;')

    def _ensure_fpt_ready(self):
        return bool(not self._operation_busy() and self.probe_thread is None and self._require_all_tools()
            and self.fpt_probe and self.fpt_probe.get('ready'))

    def start_dump(self):
        if not self._ensure_fpt_ready(): return
        root = backup_directory().parent; root.mkdir(parents=True, exist_ok=True)
        path = self._file_dialog('rom.dump_save', 'rom.filter', save=True, start=str(root / ('BIOS-Dump-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.ROM')))
        if not path: return
        self._bios_mode = 'dump'; self._fpt_result = None; self._bios_running = True; self.dump_view.reset('dump')
        worker = fpt_gui.FptWorker('dump', Path(path)); worker.event.connect(self.dump_view.event_received)
        self._launch('bios', worker, self._bios_finished)

    def start_flash(self):
        if not self.completed_steps()[4] or not self._ensure_fpt_ready(): return
        if not self._verified_build(): self._message('error.changed'); self._invalidate_build(); self._refresh_state(); return
        key = confirmation_key(self._build_plan, self.report.rom_sha256, self.own_dump_hashes)
        values = {} if key == 'fpt.confirm_secureboot' else dict(path=self.last_built_rom)
        if not self._message(key, title='fpt.confirm_title', confirm=True, **values): return
        # Check again after a potentially long confirmation dialog.
        if not self._verified_build() or not self._require_all_tools(): self._message('error.changed'); return
        self._bios_mode = 'flash'; self._fpt_result = None; self._bios_running = True; self.flash_view.reset('flash')
        worker = fpt_gui.FptWorker('flash', self.last_built_rom); worker.event.connect(self.flash_view.event_received)
        self._launch('bios', worker, self._bios_finished)

    def _bios_finished(self, result):
        self._fpt_result = result; view = self.dump_view if self._bios_mode == 'dump' else self.flash_view
        if self._bios_mode == 'dump': key, values = ('rom.dump_complete' if result.success else 'rom.dump_failed'), {}
        elif result.success: key, values = 'fpt.flash_complete', dict(path=result.backup_path)
        elif result.recovery_attempted and result.recovery_success: key, values = 'fpt.recovered', dict(path=result.backup_path)
        elif result.recovery_attempted and result.recovery_success is False: key, values = 'fpt.recovery_failed', dict(path=result.backup_path)
        else: key, values = 'fpt.flash_failed', dict(path=result.backup_path or self.t('common.none'))
        view.status.setText(self.t(key, **values)); view.status.setStyleSheet('color:#1b7b40;' if result.success else 'color:#a51e24;font-weight:650;')
        view._result_translation = key, values
        if getattr(result, 'error', None): view.console.appendPlainText(str(result.error))

    # ---- dialogs and output ----
    def _file_dialog(self, title, filter_key, *, save=False, start=''):
        dialog = QFileDialog(self, self.t(title), start)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilter(self.t(filter_key)); dialog.setAcceptMode(QFileDialog.AcceptSave if save else QFileDialog.AcceptOpen)
        dialog.setFileMode(QFileDialog.AnyFile if save else QFileDialog.ExistingFile)
        if save: dialog.setDefaultSuffix('ROM')
        dialog.setLabelText(QFileDialog.Accept, self.t('common.save' if save else 'common.open'))
        dialog.setLabelText(QFileDialog.Reject, self.t('common.cancel'))
        return dialog.selectedFiles()[0] if dialog.exec() and dialog.selectedFiles() else None

    def _message(self, key, *, title='error.title', confirm=False, **values):
        box = QMessageBox(self); box.setWindowTitle(self.t(title)); box.setText(self.t(key, **values))
        box.setTextFormat(Qt.PlainText); box.setIcon(QMessageBox.Warning if confirm else QMessageBox.Information)
        if confirm:
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No); box.setDefaultButton(QMessageBox.No)
            box.button(QMessageBox.Yes).setText(self.t('common.yes')); box.button(QMessageBox.No).setText(self.t('common.no'))
            return box.exec() == QMessageBox.Yes
        box.setStandardButtons(QMessageBox.Ok); box.button(QMessageBox.Ok).setText(self.t('common.ok')); box.exec(); return False

    def _open_report(self):
        if self.last_build_report:
            path = report_paths(Path(self.last_build_report.output_path))[1]
            if path.is_file(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_output_folder(self):
        if self.last_built_rom: QDesktopServices.openUrl(QUrl.fromLocalFile(str((self.saved_path or self.last_built_rom).parent)))

    def _resume_pending_open(self):
        if self._pending_open_path and self._rom_input_allowed():
            path, self._pending_open_path = self._pending_open_path, None; self.load_rom(path)


def run_gui():
    start_session_log(__version__, APP_TITLE); install_exception_hook()
    app = QApplication.instance() or QApplication(sys.argv)
    from .runtime_bootstrap import audit_loaded
    audit_loaded(); app.setApplicationName(APP_TITLE)
    app.aboutToQuit.connect(lambda: finish_session_log('GUI exit'))
    window = Window(); window.show()
    if len(sys.argv) == 2:
        window._pending_open_path = sys.argv[1]; QTimer.singleShot(0, window._resume_pending_open)
    return app.exec()
