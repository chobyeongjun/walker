"""
H-Walker Realtime Mode

BLE 실시간 모니터링 모드
좌측: ControlPanel (sidebar)
우측: SD Log + Status + GCP gauges + 6 plot tabs
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QLineEdit, QPushButton, QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal

from ui.styles import C
from ui.plot_widget import GCPIndicator


class RealtimeMode(QWidget):
    """Realtime monitoring mode - wraps ControlPanel + PlotTabWidget"""

    def __init__(self, control_panel, plot_widget, parent=None):
        super().__init__(parent)
        self._control_panel = control_panel
        self._plot_widget = plot_widget
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Left: ControlPanel wrapped in sidebar scroll
        sidebar = QScrollArea()
        sidebar.setFixedWidth(255)
        sidebar.setWidgetResizable(True)
        sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar.setObjectName("Sidebar")
        sidebar.setWidget(self._control_panel)
        layout.addWidget(sidebar)

        # Right: top bar + plot tabs
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Top bar: SD Log + Status + GCP circles (vertically centered)
        top = QFrame()
        top.setObjectName("GlassCard")
        top.setFixedHeight(96)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(14, 8, 14, 8)
        top_layout.setSpacing(0)

        # SD Log section (fixed width, centered)
        sd_frame = QWidget()
        sd_frame.setFixedWidth(230)
        sd_section = QVBoxLayout(sd_frame)
        sd_section.setSpacing(4)
        sd_section.setContentsMargins(0, 0, 0, 0)
        sd_section.setAlignment(Qt.AlignVCenter)

        sd_label = QLabel("SD LOG")
        sd_label.setStyleSheet(
            f"color:{C['muted']}; font-size:9px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        sd_section.addWidget(sd_label)

        file_row = QHBoxLayout()
        file_row.setSpacing(4)
        self._filename_input = QLineEdit()
        self._filename_input.setPlaceholderText("AK60_GCP_00")
        self._filename_input.setFixedWidth(150)
        self._filename_input.setFixedHeight(28)
        self._filename_input.setMaxLength(20)
        file_row.addWidget(self._filename_input)

        self._save_btn = QPushButton("Start")
        self._save_btn.setObjectName("AccentBtn")
        self._save_btn.setFixedWidth(60)
        self._save_btn.setFixedHeight(28)
        self._save_btn.clicked.connect(self._on_save_clicked)
        file_row.addWidget(self._save_btn)
        file_row.addStretch()

        sd_section.addLayout(file_row)
        top_layout.addWidget(sd_frame)

        # Separator
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setFixedHeight(50)
        sep.setStyleSheet("background:rgba(255,255,255,0.06);")
        top_layout.addWidget(sep)
        top_layout.addSpacing(14)

        # STATUS section (centered)
        status_frame = QWidget()
        status_section = QVBoxLayout(status_frame)
        status_section.setSpacing(4)
        status_section.setContentsMargins(0, 0, 0, 0)
        status_section.setAlignment(Qt.AlignVCenter)

        status_title = QLabel("STATUS")
        status_title.setStyleSheet(
            f"color:{C['muted']}; font-size:9px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        status_section.addWidget(status_title)

        status_row = QHBoxLayout()
        status_row.setSpacing(20)

        self._mode_label = QLabel("Mode: —")
        self._motor_label = QLabel("Motor: OFF")
        self._rate_label = QLabel("Rate: —Hz")

        for lbl in [self._mode_label, self._motor_label, self._rate_label]:
            lbl.setStyleSheet(
                f"color:{C['text2']}; font-size:12px; font-weight:600; "
                f"background:transparent; border:none;"
            )
            lbl.setFixedHeight(20)
            status_row.addWidget(lbl)

        status_section.addLayout(status_row)
        top_layout.addWidget(status_frame)

        top_layout.addStretch()

        # GCP Circles (vertically centered)
        self._gcp_left = GCPIndicator("L_GCP", C['blue'])
        self._gcp_right = GCPIndicator("R_GCP", C['orange'])
        top_layout.addWidget(self._gcp_left, alignment=Qt.AlignVCenter)
        top_layout.addSpacing(4)
        top_layout.addWidget(self._gcp_right, alignment=Qt.AlignVCenter)

        right_layout.addWidget(top)

        # Plot tabs (reuse existing PlotTabWidget's tab_widget)
        right_layout.addWidget(self._plot_widget.tab_widget, 1)

        layout.addWidget(right, 1)

        # Connect GCP updates from plot_widget
        self._plot_widget.set_gcp_callback(self._update_gcp)

    def _on_save_clicked(self):
        """Save button -> emit through plot_widget's save_requested signal"""
        filename = self._filename_input.text().strip()
        self._plot_widget.save_requested.emit(filename)
        self._filename_input.clear()

    def _update_gcp(self, l_gcp: float, r_gcp: float):
        """Update GCP circular gauges"""
        self._gcp_left.set_value(l_gcp / 100.0 if l_gcp > 1 else l_gcp)
        self._gcp_right.set_value(r_gcp / 100.0 if r_gcp > 1 else r_gcp)

    def update_status(self, mode: str = None, motor_on: bool = None, rate_hz: float = None):
        """Update real-time status labels in top bar"""
        if mode is not None:
            self._mode_label.setText(f"Mode: {mode}")
        if motor_on is not None:
            color = C['green'] if motor_on else C['red']
            state = "ON" if motor_on else "OFF"
            self._motor_label.setText(f"Motor: {state}")
            self._motor_label.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:600; "
                f"background:transparent; border:none;"
            )
        if rate_hz is not None:
            self._rate_label.setText(f"Rate: {rate_hz:.0f}Hz")
