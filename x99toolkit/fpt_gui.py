from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QThread, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
    QDialog,
)

from .fpt import (
    FPT_SOURCE_FOLDER_URL,
    FPT_VERSION,
    backup_directory,
    dump_bios,
    flash_bios_with_recovery,
    probe_fpt,
    validate_fpt_installation,
)
from .sessionlog import log

SECUREBOOT_FLASH_WARNING = '''Please note: Flashing a Secure Boot-patched ROM dumped from this motherboard's own working BIOS is generally very low-risk. That being said, no BIOS flash is risk-free.

However, interrupting BIOS erasing or programming WILL . BRICK . YOUR . MOTHERBOARD.
Do NOT power off, restart, disconnect power or force-close the flashing process until the operation has finished, including any automatic recovery. An incomplete write can leave the motherboard unable to boot and may require an external programmer to recover.

If you are sure that you understand this, click "Yes" to proceed'''

INTERRUPTION_WARNING = '''However, interrupting BIOS erasing or programming WILL . BRICK . YOUR . MOTHERBOARD.
Do NOT power off, restart, disconnect power or force-close the flashing process until the operation has finished, including any automatic recovery. An incomplete write can leave the motherboard unable to boot and may require an external programmer to recover.

If you are sure that you understand this, click "Yes" to proceed'''

STYLE = r'''
QMainWindow { background: #f4f5f7; }
QWidget { font-family: "Segoe UI"; font-size: 10pt; color: #202124; }
QLabel#appTitle { font-size: 23pt; font-weight: 700; color: #111827; }
QLabel#subtitle { color: #5f6368; font-size: 10.5pt; }
QLabel#sectionTitle { font-size: 15pt; font-weight: 650; color: #111827; }
QFrame#card { background: white; border: 1px solid #dfe3e8; border-radius: 11px; }
QPushButton { background: white; border: 1px solid #c9ced6; border-radius: 7px; padding: 8px 14px; }
QPushButton:hover { background: #f5f7f9; }
QPushButton#primary { background: #1a73e8; border-color: #1a73e8; color: white; font-weight: 650; padding: 10px 18px; }
QPushButton#primary:hover { background: #1765cc; }
QPushButton#danger { background: #b3261e; border-color: #b3261e; color: white; font-weight: 650; }
QPushButton#danger:hover { background: #951f19; }
QLineEdit { background: white; border: 1px solid #cbd0d7; border-radius: 6px; padding: 7px; }
QTextEdit { background: #111827; color: #e5e7eb; border: 1px solid #2f3747; border-radius: 7px; font-family: Consolas; font-size: 9pt; }
QProgressBar { border: 1px solid #cbd0d7; border-radius: 5px; background: #f2f4f7; text-align: center; height: 18px; }
QProgressBar::chunk { background: #1a73e8; border-radius: 4px; }
'''


def _choose_firmware_file(parent: QWidget, start_dir: str = '', title: str = 'Open firmware image') -> str | None:
    dialog = QFileDialog(parent, title, start_dir)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setNameFilters(['Firmware images (*.rom *.ROM *.bin *.BIN)', 'All files (*.*)'])
    if not dialog.exec():
        return None
    files = dialog.selectedFiles()
    return files[0] if files else None


def _choose_dump_output(parent: QWidget) -> str | None:
    root = backup_directory().parent
    root.mkdir(parents=True, exist_ok=True)
    default_name = f'BIOS-Dump-{datetime.now().strftime("%Y%m%d-%H%M%S")}.ROM'
    dialog = QFileDialog(parent, 'Save BIOS dump', str(root))
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setNameFilters(['Firmware image (*.ROM *.rom *.BIN *.bin)', 'All files (*.*)'])
    dialog.selectFile(default_name)
    if not dialog.exec():
        return None
    files = dialog.selectedFiles()
    return files[0] if files else None


class FptProbeWorker(QObject):
    finished = Signal(object)

    def run(self):
        try:
            result = probe_fpt()
        except Exception as exc:
            result = dict(ready=False, reason='probe-exception', message=str(exc))
        self.finished.emit(result)


class FptWorker(QObject):
    event = Signal(object)
    finished = Signal(object)

    def __init__(self, mode: str, path: Path):
        super().__init__()
        self.mode = mode
        self.path = path

    def run(self):
        callback = lambda event: self.event.emit(event)
        try:
            if self.mode == 'dump':
                result = dump_bios(self.path, callback)
            else:
                result = flash_bios_with_recovery(self.path, callback)
        except Exception as exc:
            from .fpt import DumpResult, FlashResult
            error = f'{type(exc).__name__}: {exc}'
            log('FPT WORKER', error)
            if self.mode == 'dump':
                result = DumpResult(False, str(self.path), None, None, {}, error)
            else:
                result = FlashResult(False, str(self.path), None, None, None, False, None, None, None, error)
        self.finished.emit(result)


class FptOperationDialog(QDialog):
    def __init__(self, parent: QWidget, mode: str, path: Path):
        super().__init__(parent)
        self.mode = mode
        self.path = path
        self.result = None
        self.running = True
        self.thread: QThread | None = None
        self.worker: FptWorker | None = None
        self.phase_widgets: dict[tuple[str, str], tuple[QLabel, QProgressBar]] = {}
        self.group_layouts: dict[str, QVBoxLayout] = {}
        self.setWindowTitle('Dump BIOS' if mode == 'dump' else 'Flash BIOS')
        self.resize(820, 680)
        self.setMinimumSize(700, 560)
        self.setStyleSheet(STYLE)

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 18); lay.setSpacing(10)
        self.title = QLabel('Dumping BIOS' if mode == 'dump' else 'Flashing BIOS')
        self.title.setObjectName('sectionTitle'); lay.addWidget(self.title)
        self.status = QLabel('Starting…'); self.status.setWordWrap(True); lay.addWidget(self.status)
        self.completion_hint = QLabel('BIOS dump complete. You can close this window now.')
        self.completion_hint.setWordWrap(True)
        self.completion_hint.setStyleSheet('color:#246b38;font-weight:650;')
        lay.addWidget(self.completion_hint); self.completion_hint.hide()

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        self.phase_host = QWidget(); self.phase_layout = QVBoxLayout(self.phase_host); self.phase_layout.setContentsMargins(0, 0, 4, 0); self.phase_layout.setSpacing(9); self.phase_layout.addStretch(1)
        scroll.setWidget(self.phase_host); scroll.setMinimumHeight(230); lay.addWidget(scroll, 1)

        log_title = QLabel('FPT log'); log_title.setStyleSheet('font-weight:650;'); lay.addWidget(log_title)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(210); lay.addWidget(self.log, 1)
        self.close_btn = QPushButton('Close'); self.close_btn.setEnabled(False); self.close_btn.clicked.connect(self.accept)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(self.close_btn); lay.addLayout(row)

        self.thread = QThread(self)
        self.worker = FptWorker(mode, path)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self._event)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_stopped)
        self.thread.start()

    def _ensure_group(self, group: str) -> QVBoxLayout:
        if group in self.group_layouts:
            return self.group_layouts[group]
        # insert before the stretch
        card = QFrame(); card.setObjectName('card')
        gl = QVBoxLayout(card); gl.setContentsMargins(12, 10, 12, 10); gl.setSpacing(7)
        heading = QLabel(group); heading.setStyleSheet('font-weight:650;')
        gl.addWidget(heading)
        self.phase_layout.insertWidget(max(0, self.phase_layout.count() - 1), card)
        self.group_layouts[group] = gl
        return gl

    def _event(self, event: dict):
        typ = event.get('type')
        if typ == 'status':
            self.status.setText(event.get('text', ''))
            return
        if typ == 'transcript':
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(event.get('text', '') + '\n')
            self.log.ensureCursorVisible()
            return
        if typ == 'group':
            self._ensure_group(event.get('name', 'FPT operation'))
            return
        if typ == 'phase':
            group = event.get('group', 'FPT operation')
            key = event.get('key', 'phase')
            ident = (group, key)
            if ident not in self.phase_widgets:
                gl = self._ensure_group(group)
                label = QLabel(event.get('label', key))
                bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(0); bar.setFormat('%p%')
                gl.addWidget(label); gl.addWidget(bar)
                self.phase_widgets[ident] = (label, bar)
            label, bar = self.phase_widgets[ident]
            label.setText(event.get('label', key))
            bar.setValue(int(event.get('percent', 0)))
            return
        if typ == 'process_end':
            group = event.get('group', '')
            if not event.get('success'):
                self.status.setStyleSheet('color:#9a2929;font-weight:650;')
            return

    def _finished(self, result):
        self.result = result

        if self.mode == 'dump':
            if result.success:
                self.status.setText(f'✓ BIOS dump completed: {result.output_path}')
                self.status.setStyleSheet('color:#246b38;font-weight:650;')
            else:
                self.status.setText('✕ BIOS dump failed. ' + str(result.error or ''))
                self.status.setStyleSheet('color:#9a2929;font-weight:650;')
        else:
            if result.success:
                self.status.setText('✓ BIOS flash completed and verified successfully.')
                self.status.setStyleSheet('color:#246b38;font-weight:650;')
            elif result.recovery_attempted and result.recovery_success:
                self.status.setText('⚠ The requested flash failed, but the original BIOS backup was restored and verified successfully.')
                self.status.setStyleSheet('color:#795600;font-weight:650;')
            elif result.recovery_attempted and result.recovery_success is False:
                self.status.setText('DO NOT REBOOT OR POWER OFF — automatic recovery failed. Backup: ' + str(result.backup_path))
                self.status.setStyleSheet('color:#9a2929;font-weight:700;')
            else:
                self.status.setText('✕ BIOS flash was not completed.')
                self.status.setStyleSheet('color:#9a2929;font-weight:650;')


    def _thread_stopped(self):
        self.running = False
        self.close_btn.setEnabled(True)
        if self.mode == 'dump' and self.result and self.result.success:
            self.title.setText('BIOS dump complete')
            self.completion_hint.show()

    def accept(self):
        if not self.running:
            super().accept()

    def reject(self):
        if self.running:
            QMessageBox.information(self, 'Operation in progress', 'The FPT operation is still running. This window must remain open until it finishes.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.running:
            event.ignore()
            QMessageBox.information(self, 'Operation in progress', 'The FPT operation is still running. This window must remain open until it finishes.')
            return
        event.accept()



class BiosAccessMixin:
    def _make_bios_access_card(self) -> QFrame:
        card = QFrame(); card.setObjectName('card')
        lay = QVBoxLayout(card); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(8)
        title = QLabel('BIOS access'); title.setStyleSheet('font-weight:650;font-size:11.5pt;'); lay.addWidget(title)
        status = QLabel('Checking Intel FPT availability…'); status.setWordWrap(True); status.setStyleSheet('color:#5f6368;'); lay.addWidget(status)
        self.fpt_status_labels.append(status)
        row = QHBoxLayout()
        dump_btn = QPushButton('Dump BIOS…'); dump_btn.setCursor(Qt.CursorShape.PointingHandCursor); dump_btn.clicked.connect(self.start_dump)
        flash_btn = QPushButton('Flash BIOS…'); flash_btn.setObjectName('danger'); flash_btn.setCursor(Qt.CursorShape.PointingHandCursor); flash_btn.clicked.connect(self.start_flash)
        self._bios_action_buttons.extend((dump_btn, flash_btn))
        row.addWidget(dump_btn); row.addWidget(flash_btn); row.addStretch()
        lay.addLayout(row)
        return card

    def _start_fpt_probe(self):
        if self._operation_busy():
            return
        log('GUI', 'Starting background FPT compatibility probe')
        self.probe_thread = QThread(self)
        self.probe_worker = FptProbeWorker(); self.probe_worker.moveToThread(self.probe_thread)
        self.probe_thread.started.connect(self.probe_worker.run)
        self.probe_worker.finished.connect(self._fpt_probe_finished)
        self.probe_worker.finished.connect(self.probe_thread.quit)
        self.probe_thread.finished.connect(self.probe_worker.deleteLater)
        self.probe_thread.finished.connect(self._probe_stopped)
        self._sync_busy_controls()
        self.probe_thread.start()

    def _probe_stopped(self):
        self.probe_thread.deleteLater()
        self.probe_thread = None
        self.probe_worker = None
        self._sync_busy_controls()
        if self._pending_splash_recheck and self.rom_path and self._all_tools_ready:
            self._pending_splash_recheck = False
            self._start_inspection(self.rom_path)
            return
        self._resume_pending_open()

    def _fpt_probe_finished(self, result: dict):
        self.fpt_probe = result
        self._update_fpt_status_labels()

    def _update_fpt_status_labels(self):
        if self.fpt_probe is None:
            text, css = 'Checking Intel FPT availability…', 'color:#5f6368;'
        elif self.fpt_probe.get('ready'):
            hvci_note = ' Memory Integrity is enabled, but this FPT installation is working on the current boot.' if self.fpt_probe.get('hvci') is True else ''
            text, css = '✓ BIOS dump and flash are available.' + hvci_note, 'color:#246b38;font-weight:600;'
        elif self.fpt_probe.get('reason') == 'memory-integrity-block':
            text = ('✕ BIOS dump and flash are unavailable because Windows Memory Integrity is active and Intel FPT cannot access the flash. '
                    'Open Windows Security → Device security → Core isolation details → Memory integrity, turn it Off, then restart Windows.')
            css = 'color:#9a2929;font-weight:600;'
        elif self.fpt_probe.get('reason') == 'driver-certificate-revoked':
            text = self.fpt_probe['message']
            css = 'color:#9a2929;font-weight:600;'
        elif self.fpt_probe.get('reason') in ('missing-files', 'wrong-fpt-version', 'wrong-fpt-dependency', 'missing-authoritative-fparts', 'wrong-authoritative-fparts'):
            text = 'BIOS dump/flash setup is incomplete. ' + self.fpt_probe.get('message', '')
            css = 'color:#795600;font-weight:600;'
        else:
            text = 'BIOS dump and flash are currently unavailable. ' + self.fpt_probe.get('message', '')
            css = 'color:#9a2929;font-weight:600;'
        for label in self.fpt_status_labels:
            label.setText(text); label.setStyleSheet(css)

    def _ensure_fpt_ready(self) -> bool:
        if self._operation_busy():
            QMessageBox.information(self, 'Operation in progress', 'Wait for the current build or inspection to finish.')
            return False
        if not self._require_all_tools():
            return False
        log('GUI', 'Checking whether BIOS dump/flash action may proceed')
        local = validate_fpt_installation()
        if not local.get('ready'):
            QMessageBox.warning(
                self, 'Intel FPT files required',
                local.get('message', 'Intel FPT is not ready.') + '\n\n'
                f'Download the exact ME System Tools v9.1 r7 → Flash Programming Tool → WIN64 files and place them in the tools folder.\n\n{FPT_SOURCE_FOLDER_URL}'
            )
            log('GUI', 'BIOS access denied: FPT installation validation failed')
            return False
        if self.fpt_probe is None:
            QMessageBox.information(self, 'FPT check still running', 'The Intel FPT compatibility check is still running. Try again in a moment.')
            log('GUI', 'BIOS access denied: launch-time FPT probe still running')
            return False
        if not self.fpt_probe.get('ready'):
            QMessageBox.warning(self, 'BIOS dump/flash unavailable', self.fpt_probe.get('message', 'Intel FPT cannot access the flash on this boot.'))
            log('GUI', f"BIOS access denied by FPT probe: {self.fpt_probe.get('reason')}")
            return False
        log('GUI', 'BIOS access check: PASS')
        return True

    def start_dump(self):
        log('GUI', 'User selected Dump BIOS')
        if not self._ensure_fpt_ready():
            return
        path = _choose_dump_output(self)
        if not path:
            log('GUI', 'Dump BIOS cancelled at output-file dialog')
            return
        log('GUI', f'Dump BIOS output selected: {path}')
        if not self._require_all_tools():
            return
        self._bios_running = True
        self._sync_busy_controls()
        try:
            dlg = FptOperationDialog(self, 'dump', Path(path))
            dlg.exec()
        finally:
            self._bios_running = False
            self._sync_busy_controls()
        if dlg.result and dlg.result.success:
            self.own_dump_hashes.add(dlg.result.sha256)
            self.load_rom(dlg.result.output_path)

    def start_flash(self, checked=False, target_path=None, *, secureboot_context=False):
        log('GUI', 'User selected Flash BIOS')
        if not self._ensure_fpt_ready():
            return
        start = ''
        if self.last_built_rom and self.last_built_rom.is_file():
            start = str(self.last_built_rom.parent)
        elif self.rom_path:
            start = str(self.rom_path.parent)
        path = str(target_path) if target_path else _choose_firmware_file(self, start, 'Choose BIOS ROM to flash')
        if not path:
            log('GUI', 'Flash BIOS cancelled at ROM-selection dialog')
            return
        target = Path(path).resolve()
        log('GUI', f'Flash target selected: {target}')
        answer = QMessageBox.warning(
            self,
            'Confirm BIOS flash',
            SECUREBOOT_FLASH_WARNING if secureboot_context else (
            f'You are about to flash:\n\n{target}\n\n'
            'Before any erase/programming begins, the app will create TWO independent dumps of the currently installed BIOS and require them to be byte-identical. '
            'The verified backup is kept permanently.\n\n'
            'If the requested flash fails after erase/programming has begun, the app will automatically attempt to restore that verified backup.\n\n'
            + INTERRUPTION_WARNING),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            log('GUI', 'Flash BIOS cancelled at destructive-operation confirmation')
            return
        log('GUI', 'Flash BIOS confirmed by user')
        if not self._require_all_tools():
            return
        self._bios_running = True
        self._sync_busy_controls()
        try:
            dlg = FptOperationDialog(self, 'flash', target)
            dlg.exec()
        finally:
            self._bios_running = False
            self._sync_busy_controls()
        result = dlg.result
        if not result:
            log('GUI', 'Flash operation dialog returned no result')
            return
        if result.success:
            QMessageBox.information(self, 'BIOS flash successful', f'The ROM was flashed and verified successfully.\n\nPre-flash backup:\n{result.backup_path}')
        elif result.recovery_attempted and result.recovery_success:
            QMessageBox.warning(self, 'Flash failed — backup restored', f'The requested ROM was not successfully flashed, but the original BIOS backup was restored and verified.\n\nBackup:\n{result.backup_path}')
        elif result.recovery_attempted and result.recovery_success is False:
            QMessageBox.critical(self, 'AUTOMATIC RECOVERY FAILED', f'Do NOT reboot or power off the computer.\n\nThe requested flash failed and the automatic backup restore also failed.\n\nVerified backup:\n{result.backup_path}\n\nUse an appropriate recovery method before power is removed.')
        else:
            QMessageBox.critical(self, 'BIOS flash not completed', (result.error or 'FPT did not complete the flash.') + f'\n\nVerified backup:\n{result.backup_path or "Not created"}')

