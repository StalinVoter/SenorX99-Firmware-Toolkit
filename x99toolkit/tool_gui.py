"""Tool downloads shared by Overview and Required tools."""
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)
from . import tool_inventory as inventory
from .tools import app_directory
from .sessionlog import log


class ToolDownloadWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    def run(self):
        try:
            paths = inventory.install_tool_group(self.name, progress=self.progress.emit)
            result = dict(success=True, name=self.name, files=[str(path) for path in paths])
        except Exception as exc:
            log('TOOL DOWNLOAD FAILED', f'{self.name}: {type(exc).__name__}: {exc}')
            result = dict(success=False, name=self.name, error=str(exc))
        self.finished.emit(result)


class ToolsMixin:
    def _operation_busy(self):
        return any(getattr(self, name, None) for name in (
            'inspect_thread', 'build_thread', 'probe_thread', 'download_thread', '_bios_running'))

    def _make_tools_card(self, *, missing_only=False):
        card = QFrame(); card.setObjectName('toolsCard')
        card.setStyleSheet('QFrame#toolsCard{background:white;border:1px solid #dfe2e6;border-radius:10px;} QLabel{background:transparent;}')
        layout = QVBoxLayout(card); layout.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        title = QLabel('Missing tools' if missing_only else 'Required tools')
        title.setStyleSheet('font-size:13pt;font-weight:650;')
        top.addWidget(title); top.addStretch()
        if not missing_only:
            self.recheck_tools_btn = QPushButton('Recheck files')
            self.recheck_tools_btn.clicked.connect(self._recheck_tools)
            top.addWidget(self.recheck_tools_btn)
        layout.addLayout(top)
        if not missing_only:
            folder = QLabel('Tools folder: ' + str(app_directory() / 'tools'))
            folder.setWordWrap(True); layout.addWidget(folder)
        rows, hosts = {}, {}
        name_width = max(245, max(QFontMetrics(card.font()).horizontalAdvance(group.label)
                                 for group in inventory.TOOL_GROUPS) + 28)
        for group in inventory.TOOL_GROUPS:
            host = QWidget(); host.setObjectName('toolRow')
            host.setStyleSheet('QWidget#toolRow{background:transparent;}')
            row = QGridLayout(host); row.setContentsMargins(0, 0, 0, 0)
            row.setHorizontalSpacing(14)
            name = QLabel(group.label); name.setFixedWidth(name_width); name.setWordWrap(True)
            status = QLabel('Checking…'); status.setWordWrap(True)
            status.setMinimumWidth(140)
            button = QPushButton('Download')
            button.setMinimumWidth(120)
            button.clicked.connect(lambda checked=False, name=group.name: self._download_tool(name))
            row.addWidget(name, 0, 0, alignment=Qt.AlignVCenter)
            row.addWidget(status, 0, 1)
            row.addWidget(button, 0, 2, alignment=Qt.AlignVCenter)
            row.setColumnStretch(1, 1)
            layout.addWidget(host)
            rows[group.name] = status, button
            hosts[group.name] = host
        status = QLabel()
        status.setWordWrap(True); layout.addWidget(status); status.hide()
        self._tool_views.append(dict(card=card, rows=rows, hosts=hosts, missing_only=missing_only, progress=status))
        if missing_only:
            self.missing_tool_rows = rows
            self.missing_tool_hosts = hosts
            self.overview_download_status = status
        else:
            self.tool_rows = rows
            self.tool_download_status = status
            layout.addStretch(1)
        return card

    def _refresh_tools(self):
        busy = self._operation_busy()
        groups = inventory.scan_tool_groups()
        self._all_tools_ready = bool(groups) and all(item['ready'] for item in groups)
        for view in self._tool_views:
            missing = False
            for item in groups:
                key = item['name']
                if key not in view['rows']:
                    continue
                label, button = view['rows'][key]
                ready = item['ready']
                missing = missing or not ready
                members = item['files']
                if len(members) > 1 and not ready:
                    ready_count = sum(member['state'] == 'ready' for member in members)
                    text = (f'{ready_count}/{len(members)} files ready' if ready_count else
                            'Missing' if all(member['state'] == 'missing' for member in members) else
                            'Incorrect or unreadable files')
                else:
                    text = item['detail']
                label.setText(text)
                label.setToolTip(item['detail'])
                label.setStyleSheet('color:#246b38;' if ready else 'color:#9a2929;')
                button.setText('Installed' if ready else 'Download')
                button.setEnabled(not ready and not busy)
                if view['missing_only']:
                    view['hosts'][key].setVisible(not ready)
            if view['missing_only']:
                view['card'].setVisible(missing)
        if hasattr(self, 'recheck_tools_btn'):
            self.recheck_tools_btn.setEnabled(not busy)
        self._apply_rom_tool_gate()

    def _rom_input_allowed(self):
        return self._all_tools_ready and not self._operation_busy()

    def _require_all_tools(self, *, notify=True):
        # Recheck exact files at the operation boundary; a previous green row
        # does not authorize a file that has since disappeared or changed.
        self._refresh_tools()
        if self._all_tools_ready:
            return True
        if notify:
            QMessageBox.warning(self, 'Install all required tools first',
                'Install every missing or incorrect tool using the Download buttons on Overview or Required tools. '
                'All tools must be present and verified before you can load, dump, build or flash a ROM.')
        return False

    def _apply_rom_tool_gate(self):
        allowed = self._rom_input_allowed()
        self.create_page.setEnabled(allowed and self.report is not None)
        self.overview_content.setEnabled(allowed)
        self.drop_zone.setEnabled(allowed)
        self.load_rom_btn.setEnabled(allowed)
        for button in self._bios_action_buttons:
            button.setEnabled(allowed)
        self.rom_tools_notice.setVisible(not self._all_tools_ready)
        self.nav_buttons[self.PAGE_CREATE].setEnabled(self.report is not None and self._all_tools_ready)
        self._plan_changed()

    def _sync_busy_controls(self):
        self._refresh_tools()

    def _recheck_tools(self):
        if self._operation_busy():
            return
        self._refresh_tools()
        if self.report and self._all_tools_ready:
            self._populate_overview(); self._configure_create_options()
            self._pending_splash_recheck = True
        self._start_fpt_probe()

    def _download_tool(self, name: str):
        if self._operation_busy():
            return
        group = inventory.GROUPS_BY_NAME[name]
        self._download_name = name
        self._download_result = None
        self._tool_download_progress('Starting download…')
        self.download_thread = QThread(self)
        self.download_worker = ToolDownloadWorker(name)
        self.download_worker.moveToThread(self.download_thread)
        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.progress.connect(self._tool_download_progress)
        self.download_worker.finished.connect(self._tool_download_finished)
        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_worker.finished.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self._tool_download_stopped)
        self.download_thread.finished.connect(self.download_thread.deleteLater)
        self._sync_busy_controls()
        self.download_thread.start()

    def _tool_download_progress(self, message):
        group = inventory.GROUPS_BY_NAME[self._download_name]
        for view in self._tool_views:
            view['progress'].setText(f'{group.label}: {message}')
            view['progress'].show()

    def _tool_download_finished(self, result):
        self._download_result = result

    def _tool_download_stopped(self):
        self.download_thread = self.download_worker = None
        result = self._download_result or dict(success=False, error='Tool download returned no result.')
        self._sync_busy_controls()
        if result['success']:
            self._tool_download_progress('Installed and verified.')
            if self.report and self._all_tools_ready:
                self._populate_overview(); self._configure_create_options()
                if result['name'] != 'fpt':
                    self._start_inspection(self.rom_path)
                    return
                self._pending_splash_recheck = True
            if result['name'] == 'fpt':
                self.fpt_probe = None
                self._update_fpt_status_labels()
                self._start_fpt_probe()
        else:
            self._tool_download_progress('Download failed. You can retry.')
            QMessageBox.warning(self, 'Tool download failed', result['error'])
        self._resume_pending_open()

    def _resume_pending_open(self):
        if self._rom_input_allowed() and self._pending_open_path:
            path, self._pending_open_path = self._pending_open_path, None
            self.load_rom(path)
