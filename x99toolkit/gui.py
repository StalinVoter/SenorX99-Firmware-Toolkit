from __future__ import annotations

from pathlib import Path
import os
import sys
import traceback

from PySide6.QtCore import QObject, QThread, Signal, Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .inspection import inspect_rom
from .reporting import write_inspection, report_paths
from .sessionlog import start_session_log, finish_session_log, install_exception_hook, log
from . import __version__, APP_SUBTITLE
from .tool_gui import ToolsMixin
from .firmware import sha256_file
from .buildprogress import BuildProgressTracker
from .fpt_gui import BiosAccessMixin
from .pipeline import build_rom, default_output
from .tools import discover_tools, app_directory
from .ser8989 import inspect_driver
from .splash_preflight import inspect_splash_replacement
from .feature_text import PERFORMANCE_DEFAULTS_DESCRIPTION, TBU_CPU_WARNING, performance_defaults_explanation, logo_check_failure_reason


APP_TITLE = f"SenorX99 v{__version__}"


STYLE = r"""
QMainWindow, QWidget {
    background: #f4f5f7;
    color: #1f2328;
    font-family: "Segoe UI", "Arial";
    font-size: 10pt;
}
QFrame#nav {
    background: #eef0f3;
    border-right: 1px solid #d5d8dc;
}
QLabel#brand {
    font-size: 18pt;
    font-weight: 650;
    padding: 18px 18px 4px 18px;
}
QLabel#brandSub {
    color: #61666d;
    padding: 0 18px 18px 18px;
}
QPushButton.navButton {
    text-align: left;
    padding: 11px 16px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    font-size: 10.5pt;
}
QPushButton.navButton:hover { background: #e1e4e8; }
QPushButton.navButton:checked {
    background: #dce8f6;
    color: #0f4c81;
    font-weight: 600;
}
QLabel#pageTitle {
    font-size: 23pt;
    font-weight: 650;
}
QLabel#pageSub {
    color: #5f6368;
    font-size: 10.5pt;
}
QFrame.card {
    background: white;
    border: 1px solid #dfe2e6;
    border-radius: 10px;
}
QLabel.cardTitle {
    font-size: 12.5pt;
    font-weight: 650;
}
QLabel.cardStatus {
    font-weight: 650;
}
QLabel.muted { color: #646a73; }
QLabel.good { color: #1d6b39; font-weight: 650; }
QLabel.warn { color: #8a5b00; font-weight: 650; }
QLabel.bad { color: #a12a2a; font-weight: 650; }
QLabel.info { color: #0b5ea8; font-weight: 650; }
QLabel#infoBanner {
    background: #e9f2fb;
    border: 1px solid #c8def2;
    border-radius: 8px;
    padding: 10px 12px;
    color: #174f7c;
}
QLabel#warningBanner {
    background: #fff7df;
    border: 1px solid #ead79c;
    border-radius: 8px;
    padding: 10px 12px;
    color: #6e5200;
}
QPushButton {
    padding: 8px 14px;
    border: 1px solid #c8ccd1;
    border-radius: 7px;
    background: #ffffff;
}
QPushButton:hover { background: #f2f4f7; }
QPushButton:disabled { color: #9da2a9; background: #f3f4f5; }
QPushButton.primary {
    background: #0f6cbd;
    color: white;
    border-color: #0f6cbd;
    font-weight: 600;
}
QPushButton.primary:hover { background: #115ea3; }
QLineEdit, QComboBox, QPlainTextEdit, QTableWidget, QListWidget {
    background: white;
    border: 1px solid #cfd3d8;
    border-radius: 6px;
}
QLineEdit, QComboBox { padding: 6px 8px; min-height: 24px; }
QCheckBox { spacing: 8px; }
QHeaderView::section {
    background: #f0f2f4;
    padding: 7px;
    border: 0;
    border-bottom: 1px solid #d6d9dd;
    font-weight: 600;
}
QProgressBar {
    border: 1px solid #cfd3d8;
    border-radius: 6px;
    background: white;
    text-align: center;
    min-height: 20px;
}
QProgressBar::chunk { background: #0f6cbd; border-radius: 5px; }
QToolButton {
    border: 0;
    background: transparent;
    color: #0f5f9f;
    font-weight: 600;
    padding: 4px 0;
}
"""


def _dropped_file(event) -> str | None:
    mime = event.mimeData()
    if not mime.hasUrls():
        return None
    urls = mime.urls()
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    path = Path(urls[0].toLocalFile())
    return str(path) if path.is_file() and path.suffix.casefold() in ('.rom', '.bin') else None


class DropZone(QFrame):
    dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setStyleSheet(
            "QFrame#dropZone {background:white;border:2px dashed #b9bec5;border-radius:12px;}"
            "QFrame#dropZone:hover {border-color:#0f6cbd;}"
        )
        self.setMinimumHeight(210)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(10)
        title = QLabel("Open a BIOS / ROM file")
        title.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(17)
        font.setBold(True)
        title.setFont(font)
        layout.addStretch(1)
        layout.addWidget(title)
        sub = QLabel("Drag a .ROM or .BIN file here, or choose one from disk.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("class", "muted")
        sub.setStyleSheet("color:#646a73;")
        layout.addWidget(sub)
        choose = QPushButton("Choose firmware file…")
        choose.setProperty("class", "primary")
        choose.setStyleSheet("QPushButton {background:#0f6cbd;color:white;border-color:#0f6cbd;font-weight:600;padding:9px 18px;} QPushButton:hover{background:#115ea3;}")
        choose.setMaximumWidth(210)
        choose.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(choose)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

    def _browse(self) -> None:
        owner = self.window()
        if hasattr(owner, '_require_all_tools') and not owner._require_all_tools():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open firmware image", "", "Firmware images (*.rom *.ROM *.bin *.BIN)")
        if path:
            self.dropped.emit(path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        owner = self.window()
        if self.isEnabled() and not (hasattr(owner, '_rom_input_allowed') and not owner._rom_input_allowed()) and _dropped_file(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        owner = self.window()
        path = _dropped_file(event)
        if (path and self.isEnabled() and not (hasattr(owner, '_rom_input_allowed') and not owner._rom_input_allowed())
                and (not hasattr(owner, '_require_all_tools') or owner._require_all_tools(notify=False))):
            self.dropped.emit(path)
            event.acceptProposedAction()
        else:
            event.ignore()


class FeatureCard(QFrame):
    action_requested = Signal(str)

    def __init__(self, key: str, title: str, description: str) -> None:
        super().__init__()
        self.key = key
        self.setProperty("class", "card")
        self.setStyleSheet("QFrame {background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        layout = QGridLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(7)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size:12.5pt;font-weight:650;border:0;")
        layout.addWidget(self.title, 0, 0)
        self.status = QLabel("Not checked")
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status.setStyleSheet("font-weight:650;border:0;color:#646a73;")
        layout.addWidget(self.status, 0, 1)
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color:#5f6368;border:0;")
        layout.addWidget(self.description, 1, 0, 1, 2)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color:#5f6368;border:0;")
        self.detail.hide()
        layout.addWidget(self.detail, 2, 0, 1, 2)
        self.more = QToolButton()
        self.more.setText("What does this mean? ▾")
        self.more.clicked.connect(self._toggle_detail)
        layout.addWidget(self.more, 3, 0, alignment=Qt.AlignLeft)
        self.action = QPushButton("Add to build")
        self.action.clicked.connect(lambda: self.action_requested.emit(self.key))
        layout.addWidget(self.action, 3, 1, alignment=Qt.AlignRight)

    def _toggle_detail(self) -> None:
        visible = not self.detail.isVisible()
        self.detail.setVisible(visible)
        self.more.setText("Hide explanation ▴" if visible else "What does this mean? ▾")

    def set_state(self, text: str, tone: str = "muted", action_text: str | None = None, action_enabled: bool = True, detail: str = "") -> None:
        colors = {
            "good": "#1d6b39",
            "warn": "#8a5b00",
            "bad": "#a12a2a",
            "info": "#0b5ea8",
            "muted": "#646a73",
        }
        self.status.setText(text)
        self.status.setStyleSheet(f"font-weight:650;border:0;color:{colors.get(tone, colors['muted'])};")
        if action_text is None:
            self.action.hide()
        else:
            self.action.show()
            self.action.setText(action_text)
            self.action.setEnabled(action_enabled)
        self.detail.setText(detail)
        self.more.setVisible(bool(detail))


class InspectWorker(QObject):
    finished = Signal(object, str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        try:
            report = inspect_rom(self.path)
            report.splash = inspect_splash_replacement(self.path, report.rom_sha256)
            report.rows.append(dict(category='Boot logo', check='Automatic replacement trial',
                                    result=report.splash['state'].upper(), details=report.splash['reason']))
            write_inspection(report, self.path)
            self.finished.emit(report, "")
        except Exception:
            self.finished.emit(None, traceback.format_exc())


class BuildWorker(QObject):
    progress = Signal(str, str, str)
    finished = Signal(object, str)

    def __init__(self, kwargs: dict) -> None:
        super().__init__()
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            report = build_rom(
                progress=lambda name, status, details: self.progress.emit(name, status, details),
                **self.kwargs,
            )
            self.finished.emit(report, "")
        except Exception:
            self.finished.emit(None, traceback.format_exc())


class Window(ToolsMixin, BiosAccessMixin, QMainWindow):
    PAGE_OVERVIEW, PAGE_ROM, PAGE_CREATE, PAGE_BUILDS, PAGE_TECHNICAL, PAGE_TOOLS = range(6)

    def __init__(self, *, runtime_check: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1220, 840)
        self.setMinimumSize(980, 700)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)
        self.download_thread = self.download_worker = None
        self._tool_views = []
        self._download_result = None
        self._download_name = ''
        self._bios_running = False
        self._bios_action_buttons = []
        self._all_tools_ready = False
        self._tbu_acknowledged = False
        self._tbu_rom_sha256 = None
        self._pending_open_path = None
        self.own_dump_hashes = set()
        self._secureboot_flash_context = False
        self._splash_user_choice = None
        self._splash_choice_rom_hash = None
        self._pending_splash_recheck = False

        self.probe_thread = None
        self.probe_worker = None
        self.fpt_probe = None
        self.fpt_status_labels = []
        self.last_built_rom = None
        self.pending_flash = None
        self.progress_tracker = None
        self.rom_path: Path | None = None
        self.report = None
        self.inspect_thread: QThread | None = None
        self.inspect_worker: InspectWorker | None = None
        self.build_thread: QThread | None = None
        self.build_worker: BuildWorker | None = None
        self.last_build_report = None
        self.technical_log_lines: list[str] = []
        self.output_manual = False

        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._make_nav())
        self.stack = QStackedWidget()
        shell.addWidget(self.stack, 1)

        self.overview_page = self._make_overview_page()
        self.rom_page = self._make_rom_page()
        self.create_page = self._make_create_page()
        self.builds_page = self._make_builds_page()
        self.technical_page = self._make_technical_page()
        self.tools_page, tools_layout = self._page_header('Required tools', 'Install or repair the exact utilities used by each firmware feature.')
        tools_scroll = QScrollArea(); tools_scroll.setWidgetResizable(True); tools_scroll.setFrameShape(QFrame.NoFrame)
        tools_scroll.setWidget(self._make_tools_card()); tools_layout.addWidget(tools_scroll)
        for page in (self.overview_page, self.rom_page, self.create_page, self.builds_page, self.technical_page, self.tools_page):
            self.stack.addWidget(page)

        self.refresh_drivers()
        self._use_bundled_donor()
        self._use_bundled_splash()
        self._set_nav(self.PAGE_OVERVIEW)
        self._set_loaded_state(False)
        for edit in self.findChildren(QLineEdit):
            edit.setAcceptDrops(False)
        self._refresh_tools()
        if not runtime_check:
            self._start_fpt_probe()

    def dragEnterEvent(self, event):
        if self._rom_input_allowed() and _dropped_file(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = _dropped_file(event)
        if path and self._rom_input_allowed() and self._require_all_tools(notify=False):
            self.load_rom(path)
            event.acceptProposedAction()
        else:
            event.ignore()

    # ---------- shell / navigation ----------
    def _make_nav(self) -> QWidget:
        nav = QFrame()
        nav.setObjectName("nav")
        nav.setFixedWidth(225)
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(10, 8, 10, 12)
        layout.setSpacing(5)
        brand = QLabel("SenorX99")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        sub = QLabel(APP_SUBTITLE + "\n\nv" + __version__)
        sub.setWordWrap(True)
        sub.setObjectName("brandSub")
        layout.addWidget(sub)
        self.nav_buttons: list[QPushButton] = []
        for i, text in enumerate(("Overview", "Load / Dump ROM", "Create ROM", "Builds", "Technical details", "Required tools")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("class", "navButton")
            button.setStyleSheet(
                "QPushButton{text-align:left;padding:11px 16px;border:0;border-radius:7px;background:transparent;}"
                "QPushButton:hover{background:#e1e4e8;}"
                "QPushButton:checked{background:#dce8f6;color:#0f4c81;font-weight:600;}"
            )
            button.clicked.connect(lambda checked=False, index=i: self._set_nav(index))
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        footer = QLabel("Build, inspect, dump and flash.\nVerified backups before flashing.")
        footer.setWordWrap(True)
        footer.setStyleSheet("color:#646a73;padding:10px 12px;")
        layout.addWidget(footer)
        return nav

    def _set_nav(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def _page_header(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 26, 32, 28)
        layout.setSpacing(14)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)
        sub = QLabel(subtitle)
        sub.setObjectName("pageSub")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        return page, layout

    # ---------- overview ----------
    def closeEvent(self, event):
        if self._operation_busy():
            QMessageBox.information(self, "Operation in progress", "Wait for the active operation to finish before closing the toolkit.")
            event.ignore()
        else:
            event.accept()

    def _make_overview_page(self) -> QWidget:
        page, outer = self._page_header(
            "Firmware overview",
            "Open a firmware image and the toolkit will explain what it contains, what is supported, and what can be changed safely.",
        )
        overview_scroll = QScrollArea()
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setFrameShape(QFrame.NoFrame)
        overview_body = QWidget()
        body_layout = QVBoxLayout(overview_body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(14)
        overview_scroll.setWidget(overview_body)
        outer.addWidget(overview_scroll, 1)
        outer = body_layout
        self.overview_banner = QLabel("Create a modified ROM, dump the installed BIOS, or flash with verified pre-flash backups.")
        self.overview_banner.setObjectName("infoBanner")
        self.overview_banner.setWordWrap(True)
        outer.addWidget(self.overview_banner)

        self.missing_tools_card = self._make_tools_card(missing_only=True)
        outer.addWidget(self.missing_tools_card)
        outer.addWidget(self._make_bios_access_card())
        self.drop_zone = DropZone()
        self.drop_zone.dropped.connect(self.load_rom)
        outer.addWidget(self.drop_zone)

        self.inspecting_card = QFrame()
        self.inspecting_card.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        inspect_layout = QVBoxLayout(self.inspecting_card)
        inspect_layout.setContentsMargins(20, 18, 20, 18)
        self.inspecting_title = QLabel("Analyzing firmware…")
        self.inspecting_title.setStyleSheet("font-size:13pt;font-weight:650;")
        inspect_layout.addWidget(self.inspecting_title)
        self.inspecting_text = QLabel("Reading the image and checking supported firmware structures.")
        self.inspecting_text.setWordWrap(True)
        inspect_layout.addWidget(self.inspecting_text)
        self.inspecting_progress = QProgressBar()
        self.inspecting_progress.setRange(0, 0)
        inspect_layout.addWidget(self.inspecting_progress)
        self.inspecting_card.hide()
        outer.addWidget(self.inspecting_card)

        self.overview_content = QWidget()
        content = QVBoxLayout(self.overview_content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)

        identity = QFrame()
        identity.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        il = QGridLayout(identity)
        il.setContentsMargins(18, 15, 18, 15)
        self.rom_name_label = QLabel("No firmware loaded")
        self.rom_name_label.setStyleSheet("font-size:15pt;font-weight:650;")
        il.addWidget(self.rom_name_label, 0, 0)
        self.recognition_label = QLabel("")
        self.recognition_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        il.addWidget(self.recognition_label, 0, 1)
        self.rom_meta_label = QLabel("")
        self.rom_meta_label.setStyleSheet("color:#646a73;")
        self.rom_meta_label.setWordWrap(True)
        il.addWidget(self.rom_meta_label, 1, 0, 1, 2)
        open_other = QPushButton("Open another firmware…")
        open_other.clicked.connect(self._browse_rom)
        il.addWidget(open_other, 2, 0, alignment=Qt.AlignLeft)
        content.addWidget(identity)

        self.tbu_overview = FeatureCard(
            "tbu",
            "Turbo Boost Unlock",
            "Lets supported Xeon E5 v3 processors use the firmware changes required for unlocked turbo behavior.",
        )
        self.sb_overview = FeatureCard(
            "secureboot",
            "Secure Boot factory keys",
            "Checks the factory key set stored in the ROM and can update outdated/test defaults to the validated 2023-capable set.",
        )
        self.opt_overview = FeatureCard(
            "optimized",
            "Performance-oriented BIOS defaults",
            PERFORMANCE_DEFAULTS_DESCRIPTION,
        )
        self.oc_overview = FeatureCard(
            "overclock",
            "Unlocked-multiplier overclocking",
            "Applies the verified XD4 setup defaults and menu-access changes required for compatible unlocked CPUs.",
        )
        self.logo_overview = FeatureCard(
            "splash",
            "Boot logo",
            "Replaces the firmware splash image and verifies the image embedded in the completed ROM.",
        )
        for card in (self.tbu_overview, self.sb_overview, self.opt_overview, self.oc_overview, self.logo_overview):
            card.action_requested.connect(self._overview_action)
            content.addWidget(card)

        integrity = QFrame()
        integrity.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        gl = QGridLayout(integrity)
        gl.setContentsMargins(18, 15, 18, 15)
        title = QLabel("BIOS startup information")
        title.setStyleSheet("font-size:12.5pt;font-weight:650;")
        gl.addWidget(title, 0, 0)
        self.fit_status_label = QLabel("Not checked")
        self.fit_status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        gl.addWidget(self.fit_status_label, 0, 1)
        desc = QLabel("The BIOS uses an internal list to find the CPU updates it needs at startup. The toolkit checks that this list points to the right files. If it finds a problem it knows how to fix, it corrects the list automatically when creating your modified BIOS. You do not need to repair it separately.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#5f6368;")
        gl.addWidget(desc, 1, 0, 1, 2)
        content.addWidget(integrity)

        create = QPushButton("Create modified ROM →")
        create.setStyleSheet("QPushButton{background:#0f6cbd;color:white;border-color:#0f6cbd;font-weight:600;padding:10px 18px;} QPushButton:hover{background:#115ea3;}")
        create.clicked.connect(lambda: self._set_nav(self.PAGE_CREATE))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(create)
        content.addLayout(row)
        self.overview_content.hide()

        outer.addWidget(self.overview_content)
        outer.addStretch(1)
        return page

    def _make_rom_page(self) -> QWidget:
        page, layout = self._page_header(
            "Load or dump a BIOS ROM",
            "Open an existing ROM file, or save the BIOS installed on this computer as a ROM file.",
        )
        self.rom_tools_notice = QLabel("Install all required tools before loading or dumping a ROM. Use the Download buttons on Overview.")
        self.rom_tools_notice.setObjectName('warningBanner')
        self.rom_tools_notice.setWordWrap(True)
        layout.addWidget(self.rom_tools_notice)
        self.loaded_rom_label = QLabel('No ROM loaded yet.')
        self.loaded_rom_label.setWordWrap(True)
        layout.addWidget(self.loaded_rom_label)
        self.load_rom_btn = QPushButton('Load ROM from file…')
        self.load_rom_btn.clicked.connect(self._browse_rom)
        layout.addWidget(self.load_rom_btn, alignment=Qt.AlignLeft)
        layout.addWidget(self._make_bios_access_card())
        tools_button = QPushButton('View required tools')
        tools_button.clicked.connect(lambda: self._set_nav(self.PAGE_TOOLS))
        layout.addWidget(tools_button, alignment=Qt.AlignLeft)
        layout.addStretch(1)
        return page

    # ---------- create ROM ----------
    def _make_create_page(self) -> QWidget:
        page, outer = self._page_header(
            "Create a modified ROM",
            "Choose outcomes you want. The toolkit handles the firmware-specific implementation and performs integrity validation automatically.",
        )
        self.create_no_rom = QLabel("Install all required tools, then load or dump a ROM from Load / Dump ROM.")
        self.create_no_rom.setObjectName("warningBanner")
        outer.addWidget(self.create_no_rom)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 8, 0)
        bl.setSpacing(12)

        source_card = QFrame()
        source_card.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        sl = QGridLayout(source_card)
        sl.setContentsMargins(18, 15, 18, 15)
        sl.addWidget(QLabel("Source firmware"), 0, 0)
        self.create_source_label = QLabel("None")
        self.create_source_label.setStyleSheet("font-weight:600;")
        sl.addWidget(self.create_source_label, 0, 1)
        sl.addWidget(QLabel("Output file"), 1, 0)
        self.output_edit = QLineEdit()
        self.output_edit.textEdited.connect(lambda _text: setattr(self, "output_manual", True))
        sl.addWidget(self.output_edit, 1, 1)
        out_btn = QPushButton("Choose…")
        out_btn.clicked.connect(self._browse_output)
        sl.addWidget(out_btn, 1, 2)
        bl.addWidget(source_card)

        self.tbu_build_card = self._make_build_feature_card(
            "Turbo Boost Unlock",
            "Apply the complete Turbo Boost Unlock firmware changes as one operation.",
        )
        tbu_layout = self.tbu_build_card.layout()
        self.tbu_box = QCheckBox("Add Turbo Boost Unlock")
        self.tbu_box.toggled.connect(self._tbu_selection_changed)
        tbu_layout.addWidget(self.tbu_box)
        tbu_row = QHBoxLayout()
        tbu_row.addWidget(QLabel("Undervolt profile:"))
        self.driver_combo = QComboBox()
        self.driver_combo.currentIndexChanged.connect(self._plan_changed)
        tbu_row.addWidget(self.driver_combo, 1)
        tbu_layout.addLayout(tbu_row)
        self.tbu_help = QLabel("The toolkit removes the required 06F2 microcode, updates every validated C-state default copy, installs the selected S3TurboHack payload, and verifies the result.")
        self.tbu_help.setWordWrap(True)
        self.tbu_help.setStyleSheet("color:#646a73;")
        tbu_layout.addWidget(self.tbu_help)
        bl.addWidget(self.tbu_build_card)

        self.sb_build_card = self._make_build_feature_card(
            "Secure Boot factory keys",
            "Update outdated/test factory defaults using the bundled validated 2023-capable key set.",
        )
        sb_layout = self.sb_build_card.layout()
        self.sb_box = QCheckBox("Update factory Secure Boot keys")
        self.sb_box.stateChanged.connect(self._plan_changed)
        sb_layout.addWidget(self.sb_box)
        self.sb_plan_label = QLabel("The toolkit evaluates the factory key databases independently and changes only what needs updating.")
        self.sb_plan_label.setWordWrap(True)
        self.sb_plan_label.setStyleSheet("color:#646a73;")
        sb_layout.addWidget(self.sb_plan_label)
        bl.addWidget(self.sb_build_card)

        self.opt_build_card = self._make_build_feature_card(
            "Performance-oriented BIOS defaults",
            PERFORMANCE_DEFAULTS_DESCRIPTION,
        )
        opt_layout = self.opt_build_card.layout()
        self.optimized_box = QCheckBox("Apply performance-oriented defaults")
        self.optimized_box.stateChanged.connect(self._plan_changed)
        opt_layout.addWidget(self.optimized_box)
        self.opt_create_more = QToolButton()
        self.opt_create_more.setText('What does this mean? ▾')
        self.opt_create_more.clicked.connect(self._toggle_optimized_explanation)
        opt_layout.addWidget(self.opt_create_more)
        self.opt_create_detail = QLabel(performance_defaults_explanation())
        self.opt_create_detail.setWordWrap(True)
        self.opt_create_detail.hide()
        opt_layout.addWidget(self.opt_create_detail)
        bl.addWidget(self.opt_build_card)

        self.oc_build_card = self._make_build_feature_card(
            "Unlocked-multiplier overclocking",
            "Apply the verified XD4 defaults and AMIBCP access changes for compatible unlocked Haswell/Broadwell CPUs.",
        )
        oc_layout = self.oc_build_card.layout()
        self.overclock_box = QCheckBox("Add unlocked-multiplier OC compatibility")
        self.overclock_box.stateChanged.connect(self._plan_changed)
        oc_layout.addWidget(self.overclock_box)
        oc_note = QLabel(
            "Disables Overclocking Lock, enables the PP0 current override, selects the HEDT unlimited-current value, and exposes OverClocking Feature, XE Ratio Limit and PP0 current-control menus."
        )
        oc_note.setWordWrap(True)
        oc_note.setStyleSheet("color:#646a73;")
        oc_layout.addWidget(oc_note)
        bl.addWidget(self.oc_build_card)

        self.logo_build_card = self._make_build_feature_card(
            "Boot logo",
            "Use SenorSpace automatically matched to the ROM, or choose a custom BMP. The finished image is re-extracted and verified.",
        )
        logo_layout = self.logo_build_card.layout()
        self.splash_box = QCheckBox("Replace the firmware boot logo")
        self.splash_box.stateChanged.connect(self._plan_changed)
        self.splash_box.clicked.connect(self._remember_splash_choice)
        logo_layout.addWidget(self.splash_box)
        self.splash_check_label = QLabel('Logo compatibility is checked when a ROM is opened. Verified automatic replacement is selected by default; uncheck it to keep the original logo.')
        self.splash_check_label.setWordWrap(True)
        logo_layout.addWidget(self.splash_check_label)
        self.auto_splash_box = QCheckBox("Automatically match SenorSpace to this ROM")
        self.auto_splash_box.setChecked(True)
        self.auto_splash_box.toggled.connect(self._splash_mode_changed)
        logo_layout.addWidget(self.auto_splash_box)
        logo_row = QHBoxLayout()
        self.splash_edit = QLineEdit()
        self.splash_edit.setReadOnly(True)
        self.splash_edit.textChanged.connect(self._update_logo_preview)
        logo_row.addWidget(self.splash_edit, 1)
        splash_btn = QPushButton("Choose custom image…")
        splash_btn.clicked.connect(self._browse_splash)
        logo_row.addWidget(splash_btn)
        logo_layout.addLayout(logo_row)
        self.logo_preview = QLabel("No preview")
        self.logo_preview.setFixedHeight(120)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setStyleSheet("background:#f0f2f4;border:1px solid #d8dce1;border-radius:6px;color:#6d7279;")
        logo_layout.addWidget(self.logo_preview)
        bl.addWidget(self.logo_build_card)

        integrity = QFrame()
        integrity.setStyleSheet("QFrame{background:#eef7ef;border:1px solid #c9dfcc;border-radius:10px;}")
        il = QVBoxLayout(integrity)
        il.setContentsMargins(18, 14, 18, 14)
        title = QLabel("✓ Firmware integrity checks are automatic")
        title.setStyleSheet("font-size:11.5pt;font-weight:650;color:#1d6b39;")
        il.addWidget(title)
        text = QLabel("The toolkit validates the completed image, preserves the input size, repairs FIT microcode pointers when required, and refuses to produce a successful build if the final integrity checks do not pass.")
        text.setWordWrap(True)
        il.addWidget(text)
        bl.addWidget(integrity)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced options ▾")
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        bl.addWidget(self.advanced_toggle, alignment=Qt.AlignLeft)
        self.advanced_panel = QFrame()
        self.advanced_panel.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        al = QGridLayout(self.advanced_panel)
        al.setContentsMargins(18, 14, 18, 14)
        self.preserve = QCheckBox("Keep intermediate stage ROMs for troubleshooting")
        al.addWidget(self.preserve, 0, 0, 1, 3)
        al.addWidget(QLabel("Secure Boot donor source:"), 1, 0)
        self.donor_edit = QLineEdit()
        al.addWidget(self.donor_edit, 1, 1)
        donor_btn = QPushButton("Choose…")
        donor_btn.clicked.connect(self._browse_donor)
        al.addWidget(donor_btn, 1, 2)
        donor_reset = QPushButton("Use bundled validated donors")
        donor_reset.clicked.connect(self._use_bundled_donor)
        al.addWidget(donor_reset, 2, 1, alignment=Qt.AlignLeft)
        adv_note = QLabel("Custom donors are intended for expert use. They are still parsed and validated before use.")
        adv_note.setStyleSheet("color:#646a73;")
        adv_note.setWordWrap(True)
        al.addWidget(adv_note, 3, 0, 1, 3)
        self.advanced_panel.hide()
        bl.addWidget(self.advanced_panel)

        review = QFrame()
        review.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        rl = QVBoxLayout(review)
        rl.setContentsMargins(18, 15, 18, 15)
        review_title = QLabel("Review")
        review_title.setStyleSheet("font-size:12.5pt;font-weight:650;")
        rl.addWidget(review_title)
        self.review_text = QLabel("Choose at least one change.")
        self.review_text.setWordWrap(True)
        rl.addWidget(self.review_text)
        self.build_btn = QPushButton("Build and verify ROM")
        self.build_btn.setStyleSheet("QPushButton{background:#0f6cbd;color:white;border-color:#0f6cbd;font-weight:600;padding:10px 18px;} QPushButton:hover{background:#115ea3;}")
        self.build_btn.clicked.connect(self.start_build)
        rl.addWidget(self.build_btn, alignment=Qt.AlignRight)
        bl.addWidget(review)
        bl.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page

    def _make_build_feature_card(self, title: str, description: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet("QFrame{background:white;border:1px solid #dfe2e6;border-radius:10px;}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setStyleSheet("font-size:12.5pt;font-weight:650;")
        layout.addWidget(label)
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#5f6368;")
        layout.addWidget(desc)
        return card

    # ---------- builds ----------
    def _make_builds_page(self) -> QWidget:
        page, outer = self._page_header(
            "Builds",
            "Build progress is shown in plain language. The full external-tool transcript remains available below for troubleshooting.",
        )
        self.build_state_banner = QLabel("No build has been started in this session.")
        self.build_state_banner.setObjectName("infoBanner")
        self.build_state_banner.setWordWrap(True)
        outer.addWidget(self.build_state_banner)
        self.build_progress = QProgressBar()
        self.build_progress.setRange(0, 100)
        self.build_progress.setValue(0)
        outer.addWidget(self.build_progress)
        self.build_steps = QTableWidget(0, 3)
        self.build_steps.setHorizontalHeaderLabels(["Task", "Status", "Explanation"])
        self.build_steps.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.build_steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.build_steps.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.build_steps.verticalHeader().setVisible(False)
        outer.addWidget(self.build_steps, 1)
        row = QHBoxLayout()
        self.open_output_btn = QPushButton("Open output folder")
        self.open_output_btn.clicked.connect(self._open_output_folder)
        self.open_output_btn.setEnabled(False)
        row.addWidget(self.open_output_btn)
        self.open_report_btn = QPushButton("Open detailed report")
        self.open_report_btn.clicked.connect(self._open_report)
        self.open_report_btn.setEnabled(False)
        self.open_diagnostics_btn = QPushButton("Open diagnostics")
        self.open_diagnostics_btn.setEnabled(False)
        self.open_diagnostics_btn.clicked.connect(self._open_diagnostics)
        row.addWidget(self.open_report_btn)
        row.addWidget(self.open_diagnostics_btn)
        row.addStretch(1)
        outer.addLayout(row)
        self.log_toggle = QToolButton()
        self.log_toggle.setText("Show technical log ▾")
        self.log_toggle.clicked.connect(self._toggle_log)
        outer.addWidget(self.log_toggle, alignment=Qt.AlignLeft)
        self.build_log = QPlainTextEdit()
        self.build_log.setReadOnly(True)
        self.build_log.hide()
        self.build_log.setMaximumHeight(220)
        outer.addWidget(self.build_log)
        return page

    # ---------- technical ----------
    def _make_technical_page(self) -> QWidget:
        page, outer = self._page_header(
            "Technical details",
            "Byte-level and firmware-structure information is kept here so experts can audit what the toolkit detected without forcing beginners to interpret it.",
        )
        self.tech_meta = QPlainTextEdit()
        self.tech_meta.setReadOnly(True)
        self.tech_meta.setMaximumHeight(150)
        self.tech_meta.setPlainText("No firmware loaded.")
        outer.addWidget(self.tech_meta)
        self.tech_table = QTableWidget(0, 4)
        self.tech_table.setHorizontalHeaderLabels(["Category", "Check", "Result", "Details"])
        self.tech_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tech_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tech_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tech_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tech_table.verticalHeader().setVisible(False)
        outer.addWidget(self.tech_table, 1)
        return page

    # ---------- ROM loading / inspection ----------
    def _browse_rom(self) -> None:
        if self._operation_busy() or not self._require_all_tools():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open firmware image", "", "Firmware images (*.rom *.ROM *.bin *.BIN)")
        if path:
            self.load_rom(path)

    def load_rom(self, path_string: str) -> None:
        if self._operation_busy():
            log('GUI', 'Source change rejected while another operation is active')
            return
        if not self._require_all_tools():
            return
        path = Path(path_string).resolve()
        if not path.is_file():
            QMessageBox.warning(self, "Firmware file not found", "The selected file no longer exists.")
            return
        if path.suffix.lower() not in (".rom", ".bin"):
            QMessageBox.warning(self, "Unsupported file type", "Choose a .ROM or .BIN firmware image.")
            return
        self.rom_path = path
        self.report = None
        self.loaded_rom_label.setText('Selected ROM: ' + str(path))
        self._set_nav(self.PAGE_OVERVIEW)
        self.output_manual = False
        self._set_loaded_state(False, inspecting=True)
        self.drop_zone.hide()
        self.inspecting_card.show()
        self.inspecting_title.setText("Analyzing firmware…")
        self.inspecting_text.setText(f"Reading {path.name} and checking Turbo Boost Unlock, Secure Boot defaults, BIOS defaults, firmware integrity and boot logo compatibility on a temporary copy.")
        self._start_inspection(path)

    def _start_inspection(self, path: Path) -> None:
        if self.inspect_thread is not None or not self._require_all_tools():
            return
        self.inspect_thread = QThread()
        self.inspect_worker = InspectWorker(path)
        self.inspect_worker.moveToThread(self.inspect_thread)
        self.inspect_thread.started.connect(self.inspect_worker.run)
        self.inspect_worker.finished.connect(self._inspection_finished)
        self.inspect_worker.finished.connect(self.inspect_thread.quit)
        self.inspect_thread.finished.connect(self.inspect_thread.deleteLater)
        self.inspect_thread.finished.connect(self._clear_inspection_thread)
        self.inspect_worker.finished.connect(self.inspect_worker.deleteLater)
        self._sync_busy_controls()
        self.inspect_thread.start()

    def _clear_inspection_thread(self) -> None:
        self.inspect_thread = None
        self.inspect_worker = None
        self._sync_busy_controls()
        self._resume_pending_open()

    def _inspection_finished(self, report, error: str) -> None:
        self.inspecting_card.hide()
        if report is None:
            self.drop_zone.show()
            self._set_loaded_state(False)
            msg = self._friendly_exception(error)
            QMessageBox.critical(self, "Firmware could not be analyzed", msg)
            return
        if self._splash_choice_rom_hash != report.rom_sha256:
            self._splash_user_choice = None
        self._splash_choice_rom_hash = report.rom_sha256
        self.report = report
        if self._tbu_rom_sha256 != report.rom_sha256:
            self._tbu_acknowledged = False
            self.tbu_box.setChecked(False)
        self._tbu_rom_sha256 = report.rom_sha256
        self._set_loaded_state(True)
        self._populate_overview()
        self._populate_technical()
        self._configure_create_options()
        self._plan_changed()

    def _set_loaded_state(self, loaded: bool, inspecting: bool = False) -> None:
        self.overview_content.setVisible(loaded)
        self.create_no_rom.setVisible(not loaded)
        self.nav_buttons[self.PAGE_CREATE].setEnabled(loaded)
        self.nav_buttons[self.PAGE_TECHNICAL].setEnabled(loaded)
        if not loaded and not inspecting:
            self.drop_zone.show()
        if loaded:
            self.drop_zone.hide()
        self._apply_rom_tool_gate()

    def _populate_overview(self) -> None:
        assert self.report is not None and self.rom_path is not None
        r = self.report
        self.rom_name_label.setText(self.rom_path.name)
        layout = r.tbu.get("cstates", {}).get("amitse_setup_default", {}).get("layout") or "unknown"
        fully = bool(
            r.tbu.get("cstates", {}).get("supported")
            and r.secure_boot.get("all_variables_valid")
            and r.fit.get("supported")
        )
        self.recognition_label.setText("✓ Supported firmware structure" if fully else "⚠ Partially recognized")
        self.recognition_label.setStyleSheet("color:#1d6b39;font-weight:650;" if fully else "color:#8a5b00;font-weight:650;")
        self.rom_meta_label.setText(
            f"{r.rom_size / (1024*1024):.1f} MiB  •  Firmware layout: {layout}  •  SHA-256: {r.rom_sha256}"
        )

        tbu_status = r.tbu.get("status")
        tbu_supported = bool(r.tbu.get("cstates", {}).get("supported") and r.tbu.get("target_count") == 1)
        if tbu_status == "PASS":
            uv = r.tbu.get("undervolt_mv") or {}
            self.tbu_overview.set_state(
                f"Installed ({uv.get('Core')}/{uv.get('Cache')}/{uv.get('System Agent')} mV)",
                "good",
                "Change profile",
                tbu_supported,
                "Turbo Boost Unlock is complete only when the S3TurboHack payload, the required microcode state, and all validated C-state default copies agree. This ROM passes those checks.",
            )
        elif tbu_status in ("NOT_APPLIED", "PARTIAL"):
            self.tbu_overview.set_state(
                "Not installed" if tbu_status == "NOT_APPLIED" else "Incomplete",
                "info" if tbu_status == "NOT_APPLIED" else "warn",
                "Add to build" if tbu_status == "NOT_APPLIED" else "Repair in build",
                tbu_supported,
                "The toolkit treats Turbo Boost Unlock as one complete operation: remove the required 06F2 microcode, update all validated C-state default copies, install the selected ser8989 payload, then verify the combined result.",
            )
        else:
            self.tbu_overview.set_state("Cannot determine", "bad", "Unavailable", False, str(r.tbu.get("error", "The expected firmware structure was not recognized.")))

        sb = r.secure_boot
        if not sb.get("all_variables_valid"):
            self.sb_overview.set_state("Cannot update safely", "bad", "Unavailable", False, "The required Secure Boot variable files could not all be parsed unambiguously, so the toolkit will not guess.")
        elif sb.get("has_complete_2023_set") and not sb.get("has_ami_test_pk"):
            self.sb_overview.set_state("Already up to date", "good", "Already up to date", False, "This ROM already contains the required 2023 Secure Boot factory keys and has no AMI test Platform Key. No update is needed.")
        else:
            reason = []
            if sb.get("has_ami_test_pk"):
                reason.append("AMI test Platform Key detected")
            if not sb.get("has_complete_2023_set"):
                reason.append("2023 KEK/db set incomplete")
            self.sb_overview.set_state("Update recommended", "warn", "Add update", True, "; ".join(reason) + ". The toolkit changes only factory-key databases that require updating.")

        opt = r.optimized
        explanation = performance_defaults_explanation(opt)
        if opt.get("supported") and opt.get("matches"):
            self.opt_overview.set_state("Already applied", "good", "Already applied", False, explanation)
        elif opt.get("supported"):
            self.opt_overview.set_state("Not applied", "info", "Add to build", True, explanation)
        else:
            self.opt_overview.set_state("Not available for this firmware", "muted", "Unavailable", False,
                '<p>This ROM uses a BIOS layout that is not supported for these default changes.</p>' + explanation)

        oc = r.overclock
        if oc.get("supported") and oc.get("matches"):
            self.oc_overview.set_state(
                "Applied", "good", "Already applied", False,
                "The verified defaults, mirrored IntelSetup copy and eight AMIBCP default/access bytes all match the XD4 OC reference.",
            )
        elif oc.get("supported"):
            self.oc_overview.set_state(
                "Not applied", "info", "Add to build", True,
                "This XD3/XD4 firmware layout has a verified mapping for OC defaults and menu access.",
            )
        else:
            self.oc_overview.set_state(
                "Not available for this firmware", "muted", "Unavailable", False,
                oc.get("error", "No validated XD4 overclocking mapping exists for this layout."),
            )

        tools = discover_tools()
        self.logo_overview.description.setText('Choose SenorSpace or a custom BMP as the picture shown when the computer starts.')
        if tools.changelogo and r.splash.get('supported'):
            logo_status = 'SenorSpace already installed' if r.splash.get('already_present') else 'Logo replacement supported'
            self.logo_overview.set_state(logo_status, "good", "Review logo option", True, r.splash['reason'] + ' Selected by default; uncheck Replace the firmware boot logo to keep the existing logo.')
        elif tools.changelogo:
            reason = logo_check_failure_reason(r.splash.get('reason', ''))
            self.logo_overview.description.setText('Logo check failed: ' + reason)
            self.logo_overview.set_state("Logo check failed", "warn", "Review logo option", True,
                reason + ' Logo replacement is unchecked until you choose it. Any build must still pass the final logo checks.')
        else:
            self.logo_overview.set_state("Required tool missing", "warn", "Unavailable", False, "Install ChangeLogo in Required tools before continuing with this ROM.")

        fit = r.fit
        if fit.get("status") == "PASS":
            self.fit_status_label.setText("✓ No correction needed")
            self.fit_status_label.setStyleSheet("color:#1d6b39;font-weight:650;")
        elif fit.get("repairable"):
            self.fit_status_label.setText("⚠ Will be corrected in the new BIOS")
            self.fit_status_label.setStyleSheet("color:#8a5b00;font-weight:650;")
        else:
            self.fit_status_label.setText("✕ Cannot correct automatically")
            self.fit_status_label.setStyleSheet("color:#a12a2a;font-weight:650;")

    def _configure_create_options(self) -> None:
        assert self.report is not None and self.rom_path is not None
        self.create_source_label.setText(self.rom_path.name)
        r = self.report
        tbu_supported = bool(r.tbu.get("cstates", {}).get("supported") and r.tbu.get("target_count") == 1 and self.driver_combo.count())
        self.tbu_box.setEnabled(tbu_supported)
        self.driver_combo.setEnabled(tbu_supported)
        if not tbu_supported:
            self.tbu_box.setChecked(False)
            self.tbu_help.setText("Turbo Boost Unlock is unavailable because this firmware structure is not fully recognized or no validated payloads were found.")
        else:
            self.tbu_help.setText("The toolkit removes the required 06F2 microcode, updates every validated C-state default copy, installs the selected S3TurboHack payload, and verifies the result.")

        sb_supported = bool(r.secure_boot.get("all_variables_valid") and Path(self.donor_edit.text()).exists())
        self.sb_box.setEnabled(sb_supported)
        if not sb_supported:
            self.sb_box.setChecked(False)
        rec = r.secure_boot.get("recommended_patch_mode", "unknown")
        if rec == "none":
            self.sb_plan_label.setText("The ROM already contains the validated factory key set. No Secure Boot update is required.")
        else:
            self.sb_plan_label.setText(f"Recommended update: {rec}. Factory-key databases are evaluated independently and only outdated/test defaults are replaced.")

        self.opt_create_detail.setText(performance_defaults_explanation(r.optimized))
        self.optimized_box.setEnabled(bool(r.optimized.get("supported")))
        if not r.optimized.get("supported"):
            self.optimized_box.setChecked(False)

        self.overclock_box.setEnabled(bool(r.overclock.get("supported")))
        if not r.overclock.get("supported"):
            self.overclock_box.setChecked(False)

        self._configure_splash_option()

        self._update_default_output()

    def _remember_splash_choice(self, checked):
        self._splash_user_choice = bool(checked)

    def _configure_splash_option(self):
        tool = discover_tools().changelogo
        available = tool is not None
        trial = self.report.splash
        try:
            eligible = bool(available and trial.get('supported')
                            and trial.get('source_sha256') == self.report.rom_sha256
                            and trial.get('tool_sha256') == sha256_file(tool))
        except OSError:
            eligible = False
        self.splash_box.setEnabled(available)
        checked = self._splash_user_choice if self._splash_user_choice is not None else eligible
        self.splash_box.setChecked(bool(available and checked))
        if eligible:
            self.splash_check_label.setText(('SenorSpace is already installed. ' if trial.get('already_present') else 'Logo replacement passed its compatibility check. ')
                                           + 'Uncheck this option to keep the original logo.')
        else:
            self.splash_check_label.setText('Logo check failed: ' + logo_check_failure_reason(trial.get('reason', ''))
                                           + ' Replacement is unchecked by default.')

    def _populate_technical(self) -> None:
        assert self.report is not None and self.rom_path is not None
        r = self.report
        self.tech_meta.setPlainText(
            f"ROM: {r.rom_path}\n"
            f"Size: {r.rom_size} bytes\n"
            f"SHA-256: {r.rom_sha256}\n"
            f"Inspection reports: {self.rom_path.with_suffix('.inspection.txt')} / .inspection.json"
        )
        self.tech_table.setRowCount(0)
        for item in r.rows:
            row = self.tech_table.rowCount()
            self.tech_table.insertRow(row)
            for column, key in enumerate(("category", "check", "result", "details")):
                self.tech_table.setItem(row, column, QTableWidgetItem(str(item.get(key, ""))))

    def _toggle_optimized_explanation(self):
        visible = not self.opt_create_detail.isVisible()
        self.opt_create_detail.setVisible(visible)
        self.opt_create_more.setText('Hide explanation ▴' if visible else 'What does this mean? ▾')

    def _clear_tbu_selection(self):
        was_blocked = self.tbu_box.blockSignals(True)
        try:
            self.tbu_box.setChecked(False)
        finally:
            self.tbu_box.blockSignals(was_blocked)
        self._tbu_acknowledged = False

    def _confirm_tbu_selection(self):
        answer = QMessageBox.warning(self, 'Turbo Boost Unlock CPU compatibility', TBU_CPU_WARNING,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        self._tbu_acknowledged = answer == QMessageBox.Yes
        if not self._tbu_acknowledged:
            self._clear_tbu_selection()
        return self._tbu_acknowledged

    def _tbu_selection_changed(self, checked):
        if checked:
            if self._require_all_tools():
                self._confirm_tbu_selection()
            else:
                self._clear_tbu_selection()
        else:
            self._tbu_acknowledged = False
        self._plan_changed()

    # ---------- plan ----------
    def _overview_action(self, key: str) -> None:
        if self._operation_busy() or not self._require_all_tools():
            return
        self._set_nav(self.PAGE_CREATE)
        if key == "tbu" and self.tbu_box.isEnabled():
            self.tbu_box.setChecked(True)
        elif key == "secureboot" and self.sb_box.isEnabled():
            self.sb_box.setChecked(True)
        elif key == "optimized" and self.optimized_box.isEnabled():
            self.optimized_box.setChecked(True)
        elif key == "overclock" and self.overclock_box.isEnabled():
            self.overclock_box.setChecked(True)
        elif key == "splash" and self.splash_box.isEnabled():
            self._remember_splash_choice(True)
            self.splash_box.setChecked(True)

    def _plan_changed(self, *args) -> None:
        if not self.report or not self.rom_path:
            self.review_text.setText("Open a firmware image first.")
            self.build_btn.setEnabled(False)
            return
        self._update_default_output()
        changes: list[str] = []
        if self.tbu_box.isChecked():
            profile = self.driver_combo.currentText() or "selected profile"
            changes.append(f"• Turbo Boost Unlock — {profile}")
        if self.sb_box.isChecked():
            changes.append("• Update outdated/test factory Secure Boot keys")
        if self.optimized_box.isChecked():
            changes.append("• Apply performance-oriented default settings in BIOS")
        if self.overclock_box.isChecked():
            changes.append("• Add verified XD4 unlocked-multiplier OC defaults and menu access")
        if self.splash_box.isChecked():
            image = "SenorSpace (automatically matched to the ROM)" if self.auto_splash_box.isChecked() else (Path(self.splash_edit.text()).name if self.splash_edit.text().strip() else "no image selected")
            changes.append(f"• Replace the firmware boot logo — {image}")
        fit_needed = self.report.fit.get("status") != "PASS"
        if fit_needed:
            changes.append("• Check and correct the BIOS's CPU-update list")
        if not changes:
            self.review_text.setText("Choose at least one firmware change. Integrity validation will run automatically during every build.")
            self.build_btn.setEnabled(False)
        else:
            self.review_text.setText(
                "The original ROM will not be modified. A new image will be created with:\n\n"
                + "\n".join(changes)
                + "\n\nThe completed ROM must preserve the input size and pass the toolkit's final inspection before the build is reported as successful."
            )
            valid = True
            if self.tbu_box.isChecked() and not self.driver_combo.currentData():
                valid = False
            if self.sb_box.isChecked() and not Path(self.donor_edit.text()).exists():
                valid = False
            if self.splash_box.isChecked() and not self.auto_splash_box.isChecked() and not Path(self.splash_edit.text()).is_file():
                valid = False
            self.build_btn.setEnabled(valid and self._rom_input_allowed())

    def _update_default_output(self) -> None:
        if not self.rom_path:
            return
        if not self.output_manual:
            self.output_edit.setText(
                str(
                    default_output(
                        self.rom_path,
                        apply_tbu=self.tbu_box.isChecked(),
                        apply_secureboot=self.sb_box.isChecked(),
                        apply_splash=self.splash_box.isChecked(),
                        repair_fit=True,
                        apply_optimized=self.optimized_box.isChecked(),
                        apply_overclock=self.overclock_box.isChecked(),
                    )
                )
            )

    # ---------- paths/assets ----------
    def driver_paths(self) -> list[Path]:
        base = app_directory()
        source = Path(__file__).resolve().parent.parent
        found: dict[str, Path] = {}
        for folder in (base, base / "drivers", source, source / "drivers"):
            if folder.is_dir():
                for path in folder.glob("*.ffs"):
                    try:
                        inspect_driver(path)
                        found[str(path.resolve()).lower()] = path.resolve()
                    except Exception:
                        pass
        return sorted(found.values(), key=lambda p: p.name.lower())

    def refresh_drivers(self) -> None:
        self.driver_combo.clear()
        for path in self.driver_paths():
            info = inspect_driver(path)
            uv = info.undervolt_mv
            label = f"Core {uv['Core']} mV / Cache {uv['Cache']} mV / System Agent {uv['System Agent']} mV"
            self.driver_combo.addItem(label, str(path))
        # The project's commonly used validated profile is -60/-50/-50 when present.
        for i in range(self.driver_combo.count()):
            if "Core -60 mV / Cache -50 mV / System Agent -50 mV" in self.driver_combo.itemText(i):
                self.driver_combo.setCurrentIndex(i)
                break

    def _use_bundled_donor(self) -> None:
        candidates = [app_directory() / "secureboot_donors", Path(__file__).resolve().parent.parent / "secureboot_donors"]
        for path in candidates:
            if path.is_dir():
                self.donor_edit.setText(str(path))
                self._plan_changed()
                return

    def _use_bundled_splash(self) -> None:
        candidates = [app_directory() / "assets" / "SenorSpace.bmp", Path(__file__).resolve().parent.parent / "assets" / "SenorSpace.bmp"]
        for path in candidates:
            if path.is_file():
                self.splash_edit.setText(str(path))
                return

    def _splash_mode_changed(self, automatic: bool) -> None:
        self.splash_edit.setReadOnly(automatic)
        if automatic:
            self._use_bundled_splash()
        self._plan_changed()

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save modified ROM", self.output_edit.text(), "Firmware images (*.rom *.ROM *.bin *.BIN)")
        if path:
            self.output_manual = True
            self.output_edit.setText(path)

    def _browse_donor(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Secure Boot donor folder", self.donor_edit.text())
        if path:
            self.donor_edit.setText(path)
            self._plan_changed()

    def _browse_splash(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose boot logo", self.splash_edit.text(), "Bitmap images (*.bmp *.BMP)")
        if path:
            self.auto_splash_box.setChecked(False)
            self.splash_edit.setText(path)
            self.splash_box.setChecked(True)
            self._plan_changed()

    def _update_logo_preview(self) -> None:
        path = Path(self.splash_edit.text())
        if path.is_file():
            pix = QPixmap(str(path))
            if not pix.isNull():
                self.logo_preview.setPixmap(pix.scaled(self.logo_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self._plan_changed()
                return
        self.logo_preview.setPixmap(QPixmap())
        self.logo_preview.setText("No preview")
        self._plan_changed()

    # ---------- build ----------
    def start_build(self) -> None:
        if self._operation_busy() or not self._require_all_tools():
            return
        if not self.report or not self.rom_path:
            return
        if self.tbu_box.isChecked() and not self._tbu_acknowledged and not self._confirm_tbu_selection():
            return
        output = Path(self.output_edit.text()).resolve()
        if output == self.rom_path:
            QMessageBox.warning(self, "Choose a different output file", "The toolkit never overwrites the input ROM. Choose a different output filename.")
            return
        driver = Path(self.driver_combo.currentData()) if self.driver_combo.currentData() else None
        donor = Path(self.donor_edit.text()).resolve() if self.donor_edit.text().strip() else None
        splash = None if self.auto_splash_box.isChecked() else (Path(self.splash_edit.text()).resolve() if self.splash_edit.text().strip() else None)
        if self.tbu_box.isChecked() and not driver:
            QMessageBox.warning(self, "Turbo Boost Unlock profile missing", "Choose a validated undervolt profile.")
            return
        if self.sb_box.isChecked() and (not donor or not donor.exists()):
            QMessageBox.warning(self, "Secure Boot donor missing", "Use the bundled validated donor set or choose a valid donor folder.")
            return
        if self.splash_box.isChecked() and not self.auto_splash_box.isChecked() and (not splash or not splash.is_file()):
            QMessageBox.warning(self, "Boot logo missing", "Choose a BMP image for the boot logo.")
            return
        kwargs = dict(
            source=self.rom_path,
            output=output,
            tools=discover_tools(),
            apply_tbu=self.tbu_box.isChecked(),
            driver=driver,
            apply_secureboot=self.sb_box.isChecked(),
            donor_source=donor,
            secureboot_mode="auto",
            apply_optimized=self.optimized_box.isChecked(),
            apply_overclock=self.overclock_box.isChecked(),
            apply_splash=self.splash_box.isChecked(),
            splash_image=splash,
            repair_fit=True,
            preserve_stages=self.preserve.isChecked(),
        )
        self._secureboot_flash_context = (
            kwargs['apply_secureboot'] and not any(kwargs[k] for k in
            ('apply_tbu', 'apply_optimized', 'apply_overclock', 'apply_splash'))
            and sha256_file(self.rom_path) in self.own_dump_hashes
        )
        self.technical_log_lines.clear()
        self.build_log.clear()
        self.build_steps.setRowCount(0)
        self.progress_tracker = BuildProgressTracker(kwargs)
        self.build_progress.setValue(0)
        self.build_state_banner.setText("Creating firmware… The original ROM is untouched.")
        self.build_state_banner.setStyleSheet("background:#e9f2fb;border:1px solid #c8def2;border-radius:8px;padding:10px 12px;color:#174f7c;")
        self.build_btn.setEnabled(False)
        self.open_output_btn.setEnabled(False)
        self.open_report_btn.setEnabled(False)
        self.open_diagnostics_btn.setEnabled(False)
        self._set_nav(self.PAGE_BUILDS)

        self.build_thread = QThread()
        self.build_worker = BuildWorker(kwargs)
        self.build_worker.moveToThread(self.build_thread)
        self.build_thread.started.connect(self.build_worker.run)
        self.build_worker.progress.connect(self._on_build_progress)
        self.build_worker.finished.connect(self._build_finished)
        self.build_worker.finished.connect(self.build_thread.quit)
        self.build_thread.finished.connect(self.build_thread.deleteLater)
        self.build_thread.finished.connect(self._clear_build_thread)
        self.build_worker.finished.connect(self.build_worker.deleteLater)
        self._sync_busy_controls()
        self.build_thread.start()

    def _clear_build_thread(self) -> None:
        self.build_thread = None
        self.build_worker = None
        self._sync_busy_controls()
        if self.pending_flash:
            target = self.pending_flash
            self.pending_flash = None
            QTimer.singleShot(0, lambda: self._offer_flash(target))

    def _offer_flash(self, target):
        if QMessageBox.question(self, "ROM created", "Flash this ROM now?\n\n" + str(target), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.start_flash(target_path=target, secureboot_context=self._secureboot_flash_context)

    def _friendly_step(self, name: str, status: str, details: str) -> tuple[str, str]:
        mapping = {
            "Input preflight": ("Validate source firmware", "The input file size and SHA-256 were recorded."),
            "Initial inspection": ("Inspect current firmware", "The current feature state and firmware structure were identified."),
            "Remove 06F2 microcode": ("Prepare Turbo Boost Unlock", "The required 06F2 microcode state was verified."),
            "Capture FIT microcode identities": ("Record firmware integrity references", "Microcode identities were captured so FIT can be repaired by identity after rebuilds."),
            "Set primary IntelSetup C-states": ("Apply Turbo Boost Unlock defaults", "The primary CPU power-state defaults were updated and verified."),
            "Set NV-Internal C-state defaults": ("Apply Turbo Boost Unlock defaults", "The internal mirrored CPU defaults were updated and verified."),
            "Set AMITSESetupData C-state default": ("Apply Turbo Boost Unlock defaults", "The AMI default/template copy was updated and verified."),
            "Validate complete MiyConst C-states": ("Verify Turbo Boost Unlock defaults", "All required C-state default copies agree."),
            "Apply TBU payload": ("Install Turbo Boost Unlock payload", "The selected undervolt payload was inserted and independently verified."),
            "Apply Optimized BIOS Settings": ("Apply performance-oriented defaults", "The validated BIOS default profile is being applied and checked."),
            "Validate Optimized BIOS Settings": ("Verify performance-oriented defaults", "All validated settings and mirrored copies must match."),
            "Apply multiplier OC: primary IntelSetup": ("Apply unlocked-multiplier defaults", "The primary overclocking and current-limit defaults are being updated and verified."),
            "Apply multiplier OC: NV-Internal": ("Mirror unlocked-multiplier defaults", "The internal setup-default copy is being updated and verified."),
            "Apply multiplier OC: AMITSESetupData": ("Expose and configure OC controls", "The verified AMIBCP menu-access flags and default-table values are being reproduced."),
            "Validate multiplier OC compatibility": ("Verify unlocked-multiplier compatibility", "All three setup values, their mirror and the eight AMIBCP bytes must match."),
            "Replace PkVar": ("Update Secure Boot factory keys", "The Platform Key default is being updated only if required."),
            "Replace KekVar": ("Update Secure Boot factory keys", "The KEK factory default is being updated only if required."),
            "Replace dbVar": ("Update Secure Boot factory keys", "The allowed-signature factory database is being updated only if required."),
            "Apply Secure Boot 2023": ("Verify Secure Boot factory keys", "The completed 2023-capable factory key set is being checked."),
            "Replace splash logo": ("Replace and verify boot logo", "The selected image is being embedded, re-extracted and pixel-verified."),
            "Repair / validate FIT": ("Check firmware integrity", "FIT microcode pointers are being repaired if needed and then validated."),
            "Final validation": ("Verify completed ROM", "The final image must preserve the source size and pass the combined inspection."),
            "Reports": ("Write build reports", "Detailed text, JSON and live-log reports are written beside the ROM."),
            "Build": ("Build firmware", "The build stopped safely before reporting success." if status == "FAIL" else details),
        }
        return mapping.get(name, (name, details))

    def _on_build_progress(self, name: str, status: str, details: str) -> None:
        self.technical_log_lines.append(f"[{status}] {name}: {details}")
        self.build_log.appendPlainText(self.technical_log_lines[-1])
        friendly_name, explanation = self._friendly_step(name, status, details)
        row = None
        for i in range(self.build_steps.rowCount()):
            if self.build_steps.item(i, 0) and self.build_steps.item(i, 0).text() == friendly_name:
                row = i
                break
        if row is None:
            row = self.build_steps.rowCount()
            self.build_steps.insertRow(row)
            self.build_steps.setItem(row, 0, QTableWidgetItem(friendly_name))
        visible_status = {"PASS": "✓ Complete", "RUNNING": "In progress…", "SKIP": "✓ Already current", "FAIL": "✕ Failed"}.get(status, status)
        self.build_steps.setItem(row, 1, QTableWidgetItem(visible_status))
        self.build_steps.setItem(row, 2, QTableWidgetItem(explanation))
        if self.progress_tracker:
            self.build_progress.setValue(self.progress_tracker.update(name, status, details))

    def _build_finished(self, report, error: str) -> None:
        self.last_build_report = report
        self.build_btn.setEnabled(True)
        self.open_output_btn.setEnabled(bool(report))
        self.open_report_btn.setEnabled(bool(report and report_paths(Path(report.output_path))[1].is_file()))
        self.open_diagnostics_btn.setEnabled(bool(report and report.tool_output.get("Diagnostic evidence")))
        if report and report.success:
            self._secureboot_flash_context = bool(self._secureboot_flash_context and report.input_sha256 in self.own_dump_hashes)
            self.last_built_rom = Path(report.output_path)
            self.pending_flash = self.last_built_rom
            self.build_state_banner.setText(
                f"✓ ROM created and verified\n{Path(report.output_path).name}\nAll requested modifications and final integrity checks passed."
            )
            self.build_state_banner.setStyleSheet("background:#eef7ef;border:1px solid #c9dfcc;border-radius:8px;padding:10px 12px;color:#1d6b39;font-weight:600;")
            self.open_output_btn.setEnabled(True)
            self.open_report_btn.setEnabled(True)
        else:
            message = report.error if report else self._friendly_exception(error)
            self.build_state_banner.setText(
                "✕ The ROM was not accepted as a successful build\n"
                + message
                + "\nNo firmware was flashed. Review the technical log for the exact failure."
            )
            self.build_state_banner.setStyleSheet("background:#fdeeee;border:1px solid #ebc6c6;border-radius:8px;padding:10px 12px;color:#8f2727;font-weight:600;")

    # ---------- misc UI ----------
    def _toggle_advanced(self) -> None:
        visible = not self.advanced_panel.isVisible()
        self.advanced_panel.setVisible(visible)
        self.advanced_toggle.setText("Advanced options ▴" if visible else "Advanced options ▾")

    def _toggle_log(self) -> None:
        visible = not self.build_log.isVisible()
        self.build_log.setVisible(visible)
        self.log_toggle.setText("Hide technical log ▴" if visible else "Show technical log ▾")

    def _open_output_folder(self) -> None:
        if not self.last_build_report or not self.last_build_report.output_path:
            return
        path = Path(self.last_build_report.output_path).resolve().parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_diagnostics(self) -> None:
        if self.last_build_report:
            directory = self.last_build_report.tool_output.get("Diagnostic evidence")
            if directory and Path(directory).is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def _open_report(self) -> None:
        if not self.last_build_report or not self.last_build_report.output_path:
            return
        path = report_paths(Path(self.last_build_report.output_path))[1]
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _friendly_exception(self, raw: str) -> str:
        if "Expected one validated" in raw or "unsupported" in raw.lower() or "not recognized" in raw.lower():
            return "The toolkit does not recognize the firmware structure required for this operation. No changes were made. Technical details are available in the traceback/log."
        if "FileNotFoundError" in raw:
            return "A required file or external firmware tool is missing. The operation was stopped before a successful ROM could be produced."
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return lines[-1] if lines else "An unexpected error occurred while analyzing the firmware."


def run_gui() -> int:
    start_session_log(__version__, APP_TITLE)
    install_exception_hook()
    app = QApplication.instance() or QApplication(sys.argv)
    from .runtime_bootstrap import audit_loaded
    audit_loaded()
    app.setApplicationName(APP_TITLE)
    app.aboutToQuit.connect(lambda: finish_session_log("GUI exit"))
    window = Window()
    window.show()
    if len(sys.argv) == 2:
        window._pending_open_path = sys.argv[1]
        QTimer.singleShot(0, window._resume_pending_open)
    return app.exec()
