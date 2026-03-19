"""
H-Walker TopNav - Horizontal tab navigation bar

[H-Walker] [Realtime] [Analysis] [Files] [Camera]  ...  [ARLAB Logo]
"""

import os
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QButtonGroup
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QPixmap

from ui.styles import C


class TopNav(QWidget):
    """Top navigation bar with mode tabs"""

    mode_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setObjectName("TopNav")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        # H-Walker title
        title = QLabel("H-Walker")
        title.setStyleSheet(
            f"color:{C['blue']}; font-size:17px; font-weight:800; "
            f"background:transparent; border:none;"
        )
        layout.addWidget(title)

        sep = QLabel("  ")
        sep.setStyleSheet("background:transparent; border:none;")
        layout.addWidget(sep)

        # Mode buttons
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        for text, idx in [("Realtime", 0), ("Analysis", 1), ("Files", 2), ("Camera", 3)]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedHeight(48)
            btn.setMinimumWidth(85)
            if idx == 0:
                btn.setChecked(True)
            self._btn_group.addButton(btn, idx)
            layout.addWidget(btn)

        layout.addStretch()

        # ARLAB Logo (image or text fallback)
        self._logo_label = QLabel()
        self._logo_label.setStyleSheet("background:transparent; border:none;")
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logo.png"
        )
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            scaled = pixmap.scaledToHeight(32, Qt.SmoothTransformation)
            self._logo_label.setPixmap(scaled)
        else:
            self._logo_label.setText("ARLAB")
            self._logo_label.setStyleSheet(
                f"color:{C['teal']}; font-size:13px; font-weight:700; "
                f"letter-spacing:2px; background:transparent; border:none;"
            )
        layout.addWidget(self._logo_label)

        self._btn_group.idClicked.connect(self.mode_changed.emit)

    def set_mode(self, index: int):
        """Programmatically switch active tab"""
        btn = self._btn_group.button(index)
        if btn:
            btn.setChecked(True)
