"""Download disclosure; URLs come from the actual downloader specifications."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout
from . import tool_inventory as inventory
from .tools import app_directory

# Exact copyright notices verified against the pinned file metadata or upstream licence.
NOTICES = {
    'UEFIReplace.exe': ('Copyright (c) 2015, Nikolaj Schlej', 'download.license_bsd',
                       'https://github.com/LongSoft/UEFITool/blob/0.28.0/LICENSE.md'),
    'fpt': ('Copyright © 2007 - 2014, Intel Corporation. All rights reserved.', 'download.license_publisher', ''),
    'mmtool_a5.exe': ('Copyright (C) 2017 American Megatrends, Inc', 'download.license_publisher', ''),
    'ChangeLogo.exe': ('Copyright (C) 2017 American Megatrends, Inc', 'download.license_publisher', ''),
}


def disclosure(owner, name):
    group = inventory.GROUPS_BY_NAME[name]
    copyright, license_key, license_url = NOTICES[name]
    text = [owner.t('download.files'), '\n'.join(group.files), '',
            owner.t('download.sources')]
    for member in group.files:
        spec = inventory.TOOLS_BY_NAME[member]
        text.append(member + '\n' + spec.url)
        if spec.archive_member:
            text.append(owner.t('download.archive', file=member))
    text += ['', owner.t('download.official' if name == 'UEFIReplace.exe' else 'download.mirror'),
             '', owner.t('download.rights'), copyright, owner.t(license_key)]
    if license_url: text.append(license_url)
    text += ['', owner.t('download.destination', folder=str(app_directory() / 'tools')),
             owner.t('download.existing')]
    return '\n'.join(text)


class ToolDownloadConfirmation(QDialog):
    def __init__(self, owner, name):
        super().__init__(owner)
        self.setWindowTitle(owner.t('download.title'))
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        label = owner.t('tools.fpt', version=inventory.FPT_VERSION) if name == 'fpt' else inventory.GROUPS_BY_NAME[name].label
        question = QLabel(owner.t('download.question', tool=label))
        question.setTextFormat(Qt.PlainText); question.setWordWrap(True); layout.addWidget(question)
        self.details = QPlainTextEdit(disclosure(owner, name))
        self.details.setStyleSheet('background:white;color:#202a37;')
        self.details.setReadOnly(True); self.details.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.details.setAccessibleName(owner.t('download.details'))
        layout.addWidget(self.details, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        self.buttons.button(QDialogButtonBox.Yes).setText(owner.t('common.yes'))
        self.buttons.button(QDialogButtonBox.Yes).setAutoDefault(False)
        no = self.buttons.button(QDialogButtonBox.No); no.setText(owner.t('common.no'))
        no.setDefault(True); no.setFocus()
        self.buttons.button(QDialogButtonBox.Yes).clicked.connect(self.accept)
        no.clicked.connect(self.reject)
        layout.addWidget(self.buttons)
