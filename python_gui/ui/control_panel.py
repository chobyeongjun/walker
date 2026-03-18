"""
ARWalker Control Panel

모드별 파라미터 입력 및 제어 버튼 패널
QScrollArea 기반 반응형 레이아웃 - 작은 화면에서 스크롤, 큰 화면에서 정확한 배치
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QFrame, QTextEdit,
    QStackedWidget, QLineEdit, QSizePolicy, QCheckBox,
    QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont


class PlusMinusSpinBox(QWidget):
    """커스텀 SpinBox - 세로 버튼 (위: 초록, 아래: 빨강)"""

    valueChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # SpinBox (버튼 숨김)
        self.spin = QDoubleSpinBox()
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #1e1e1e;
                border: 1px solid #444444;
                border-radius: 4px 0px 0px 4px;
                padding: 4px 6px;
                color: #ffffff;
                font-size: 12px;
            }
        """)
        self.spin.valueChanged.connect(self.valueChanged.emit)
        layout.addWidget(self.spin, 1)

        # 버튼 컨테이너 (세로 배치)
        btn_container = QWidget()
        btn_layout = QVBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(0)

        # 위 버튼 (초록 - 증가)
        self.up_btn = QPushButton()
        self.up_btn.setFixedSize(20, 13)
        self.up_btn.setStyleSheet("""
            QPushButton {
                background-color: #22c55e;
                border: none;
                border-radius: 0px;
                border-top-right-radius: 4px;
            }
            QPushButton:hover { background-color: #16a34a; }
            QPushButton:pressed { background-color: #15803d; }
        """)
        self.up_btn.clicked.connect(self._increase)
        btn_layout.addWidget(self.up_btn)

        # 흰색 구분선
        separator = QFrame()
        separator.setFixedSize(20, 2)
        separator.setStyleSheet("background-color: #ffffff;")
        btn_layout.addWidget(separator)

        # 아래 버튼 (빨강 - 감소)
        self.down_btn = QPushButton()
        self.down_btn.setFixedSize(20, 13)
        self.down_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                border: none;
                border-radius: 0px;
                border-bottom-right-radius: 4px;
            }
            QPushButton:hover { background-color: #dc2626; }
            QPushButton:pressed { background-color: #b91c1c; }
        """)
        self.down_btn.clicked.connect(self._decrease)
        btn_layout.addWidget(self.down_btn)

        layout.addWidget(btn_container)

    def _increase(self):
        self.spin.setValue(self.spin.value() + self.spin.singleStep())

    def _decrease(self):
        self.spin.setValue(self.spin.value() - self.spin.singleStep())

    # QDoubleSpinBox 메서드 위임
    def setRange(self, min_val, max_val):
        self.spin.setRange(min_val, max_val)

    def setValue(self, val):
        self.spin.setValue(val)

    def value(self):
        return self.spin.value()

    def setSuffix(self, suffix):
        self.spin.setSuffix(suffix)

    def setSingleStep(self, step):
        self.spin.setSingleStep(step)

    def setFixedHeight(self, h):
        super().setFixedHeight(h)
        self.spin.setFixedHeight(h)


class ControlPanel(QWidget):
    """제어 패널 - QScrollArea 기반 반응형 레이아웃

    ★ 핵심 설계:
    - 상단 컨트롤들은 QScrollArea 안에 배치 → 작은 화면에서 겹침 방지
    - 하단 Log는 스크롤 영역 밖에서 stretch=1로 남은 공간 차지
    - 고정 너비 대신 min/max 너비로 적절한 범위 유지
    """

    # 시그널 정의
    command_requested = pyqtSignal(str)
    mode_changed = pyqtSignal(int)
    scan_requested = pyqtSignal()
    connect_requested = pyqtSignal(int)
    disconnect_requested = pyqtSignal()

    PANEL_WIDTH = 290

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.PANEL_WIDTH)
        self._init_ui()

    def _init_ui(self):
        # ★ 외부 레이아웃: 스크롤 영역(상단) + 로그(하단)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(4)

        # === 스크롤 영역 (BLE + Control + Params + FF + Camera) ===
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("""
            QScrollArea { background-color: transparent; border: none; }
            QScrollBar:vertical {
                background-color: #1e1e1e; width: 6px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #404040; border-radius: 3px; min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        # 스크롤 내부 컨텐츠 위젯
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # === BLE 연결 그룹 ===
        ble_group = self._create_ble_group()
        layout.addWidget(ble_group)

        # === 제어 그룹 ===
        control_group = self._create_control_group()
        layout.addWidget(control_group)

        # === 파라미터 그룹 (모드별 스위칭) ===
        self.param_stack = QStackedWidget()
        force_params = self._create_force_params()
        self.param_stack.addWidget(force_params)
        pos_params = self._create_position_params()
        self.param_stack.addWidget(pos_params)
        layout.addWidget(self.param_stack)

        # === Treadmill FF 파라미터 그룹 ===
        tff_group = self._create_tff_group()
        layout.addWidget(tff_group)

        # === Camera 버튼 ===
        camera_group = self._create_camera_group()
        layout.addWidget(camera_group)

        # 스크롤 내부 여백 (아래쪽 여유)
        layout.addStretch(0)

        scroll_area.setWidget(scroll_content)
        outer_layout.addWidget(scroll_area, 1)  # stretch=1: 스크롤이 가용 공간 차지

        # === 로그 출력 (스크롤 영역 밖, 하단 고정 높이) ===
        log_group = self._create_log_group()
        log_group.setFixedHeight(150)
        outer_layout.addWidget(log_group, 0)  # stretch=0: 고정 높이, 남은 공간은 스크롤이 차지

    def _create_ble_group(self) -> QGroupBox:
        """BLE 연결 그룹"""
        group = QGroupBox("BLE Connection")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)

        # 디바이스 선택
        self.device_combo = QComboBox()
        self.device_combo.setPlaceholderText("Select device...")
        self.device_combo.setFixedHeight(26)
        layout.addWidget(self.device_combo)

        # 스캔/연결 버튼
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setFixedHeight(28)
        self.scan_btn.clicked.connect(self.scan_requested.emit)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setFixedHeight(28)
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        btn_layout.addWidget(self.scan_btn)
        btn_layout.addWidget(self.connect_btn)
        layout.addLayout(btn_layout)

        # 연결 상태
        self.connection_label = QLabel("Disconnected")
        self.connection_label.setAlignment(Qt.AlignCenter)
        self.connection_label.setFixedHeight(24)
        self.connection_label.setStyleSheet("""
            QLabel {
                color: #ff6b6b;
                font-weight: bold;
                font-size: 11px;
                padding: 3px;
                background-color: #252525;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.connection_label)

        return group

    def _create_control_group(self) -> QGroupBox:
        """제어 그룹"""
        group = QGroupBox("Control")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)

        # Mode 선택
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(6)

        mode_label = QLabel("Mode:")
        mode_label.setFixedWidth(40)
        mode_layout.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Force Assist", "Position Assist"])
        self.mode_combo.setFixedHeight(26)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_combo, 1)

        self.mode_btn = QPushButton("Set")
        self.mode_btn.setFixedSize(45, 26)
        self.mode_btn.clicked.connect(self._send_mode)
        mode_layout.addWidget(self.mode_btn)

        layout.addLayout(mode_layout)

        # Enable/Disable 버튼 - 컴팩트
        enable_layout = QHBoxLayout()
        enable_layout.setSpacing(6)

        self.enable_btn = QPushButton("Enable (e)")
        self.enable_btn.setFixedHeight(34)
        self.enable_btn.setStyleSheet("""
            QPushButton {
                background-color: #22c55e;
                color: white;
                font-weight: bold;
                font-size: 12px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #16a34a; }
        """)
        self.enable_btn.clicked.connect(lambda: self.command_requested.emit("e"))

        self.disable_btn = QPushButton("Disable (d)")
        self.disable_btn.setFixedHeight(34)
        self.disable_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                font-weight: bold;
                font-size: 12px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #dc2626; }
        """)
        self.disable_btn.clicked.connect(lambda: self.command_requested.emit("d"))

        enable_layout.addWidget(self.enable_btn)
        enable_layout.addWidget(self.disable_btn)
        layout.addLayout(enable_layout)

        # 캘리브레이션 버튼 (2x2 그리드) - 컴팩트
        cal_layout = QGridLayout()
        cal_layout.setSpacing(4)

        self.imu_btn = QPushButton("IMU Cal")
        self.imu_btn.setFixedHeight(28)
        self.imu_btn.clicked.connect(lambda: self.command_requested.emit("imu"))

        self.motor_btn = QPushButton("Motor Zero")
        self.motor_btn.setFixedHeight(28)
        self.motor_btn.clicked.connect(lambda: self.command_requested.emit("motor"))

        self.mark_btn = QPushButton("Mark")
        self.mark_btn.setFixedHeight(28)
        self.mark_btn.clicked.connect(lambda: self.command_requested.emit("mark"))

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setFixedHeight(28)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                color: white;
            }
            QPushButton:hover { background-color: #4f46e5; }
        """)

        cal_layout.addWidget(self.imu_btn, 0, 0)
        cal_layout.addWidget(self.motor_btn, 0, 1)
        cal_layout.addWidget(self.mark_btn, 1, 0)
        cal_layout.addWidget(self.clear_btn, 1, 1)
        layout.addLayout(cal_layout)

        return group

    def _create_force_params(self) -> QWidget:
        """Force Assist 파라미터"""
        widget = QWidget()
        group = QGroupBox("Force Mode Parameters")
        layout = QGridLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setColumnStretch(1, 1)  # SpinBox 열 확장
        layout.setColumnMinimumWidth(0, 75)  # 라벨 열 최소 너비 (가로 줄임)
        layout.setColumnMinimumWidth(2, 40)   # 버튼 열 최소 너비

        # 각 파라미터를 그리드 레이아웃으로 구성 (정렬 보장)
        # ★ 기본값: 40, 50, 60, 70 (사용자 요청)
        params = [
            ("Onset GCP:", "onset_spin", 40, "%", "gs", 100),
            ("Peak GCP:", "peak_gcp_spin", 60, "%", "gp", 100),
            ("Release GCP:", "release_spin", 70, "%", "ge", 100),
            ("Peak Force:", "peak_force_spin", 40, " N", "pf", 1),
        ]

        for row_idx, (label_text, attr_name, default, suffix, cmd_prefix, divisor) in enumerate(params):
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(label, row_idx, 0)

            # ★ 커스텀 +/- 스핀박스 사용
            spin = PlusMinusSpinBox()
            spin.setRange(0, 200 if "Force" in label_text else 100)
            spin.setValue(default)
            spin.setSuffix(suffix)
            spin.setSingleStep(1)
            spin.setFixedHeight(28)
            setattr(self, attr_name, spin)
            layout.addWidget(spin, row_idx, 1)

            btn = QPushButton("Set")
            btn.setFixedSize(45, 28)
            if divisor == 100:
                btn.clicked.connect(lambda checked, s=spin, p=cmd_prefix: self._send_param(p, s.value() / 100))
            else:
                btn.clicked.connect(lambda checked, s=spin, p=cmd_prefix: self._send_param(p, s.value()))
            layout.addWidget(btn, row_idx, 2)

        # Set All 버튼 - 그리드 레이아웃에 3열 병합하여 추가
        set_all_btn = QPushButton("Set All Parameters")
        set_all_btn.setFixedHeight(30)
        set_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #7c3aed; }
        """)
        set_all_btn.clicked.connect(self._send_all_force_params)
        layout.addWidget(set_all_btn, len(params), 0, 1, 3)  # 마지막 행, 3열 병합

        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(group)
        return widget

    def _create_position_params(self) -> QWidget:
        """Position Assist 파라미터"""
        widget = QWidget()
        group = QGroupBox("Position Mode Parameters")
        layout = QGridLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setColumnStretch(1, 1)  # SpinBox 열 확장
        layout.setColumnMinimumWidth(0, 75)  # 라벨 열 최소 너비 (가로 줄임)
        layout.setColumnMinimumWidth(2, 40)   # 버튼 열 최소 너비

        params = [
            ("Start GCP:", "pos_start_spin", 20, "%", "ps", 100),
            ("End GCP:", "pos_end_spin", 70, "%", "pe", 100),
            ("Amplitude:", "amplitude_spin", 600, " deg", "pa", 1),
        ]

        for row_idx, (label_text, attr_name, default, suffix, cmd_prefix, divisor) in enumerate(params):
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(label, row_idx, 0)

            # ★ 커스텀 +/- 스핀박스 사용
            spin = PlusMinusSpinBox()
            spin.setRange(0, 2000 if "Amplitude" in label_text else 100)
            spin.setValue(default)
            spin.setSuffix(suffix)
            spin.setSingleStep(1 if "Amplitude" in label_text else 1)
            spin.setFixedHeight(28)
            setattr(self, attr_name, spin)
            layout.addWidget(spin, row_idx, 1)

            btn = QPushButton("Set")
            btn.setFixedSize(45, 28)
            if divisor == 100:
                btn.clicked.connect(lambda checked, s=spin, p=cmd_prefix: self._send_param(p, s.value() / 100))
            else:
                btn.clicked.connect(lambda checked, s=spin, p=cmd_prefix: self._send_param(p, s.value()))
            layout.addWidget(btn, row_idx, 2)

        set_all_btn = QPushButton("Set All Parameters")
        set_all_btn.setFixedHeight(30)
        set_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #7c3aed; }
        """)
        set_all_btn.clicked.connect(self._send_all_position_params)
        layout.addWidget(set_all_btn, len(params), 0, 1, 3)  # 마지막 행, 3열 병합

        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(group)
        return widget

    def _create_tff_group(self) -> QGroupBox:
        """Feedforward 파라미터 그룹 (Treadmill FF + Motion FF)"""
        group = QGroupBox("Feedforward")
        layout = QGridLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setColumnStretch(1, 1)
        layout.setColumnMinimumWidth(0, 75)
        layout.setColumnMinimumWidth(2, 40)

        # TM Speed (m/s) - BLE: 'tm'
        label_speed = QLabel("TM Speed:")
        label_speed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(label_speed, 0, 0)

        self.tff_speed_spin = PlusMinusSpinBox()
        self.tff_speed_spin.setRange(0, 300)
        self.tff_speed_spin.setValue(125)
        self.tff_speed_spin.setSuffix(" cm/s")
        self.tff_speed_spin.setSingleStep(5)
        self.tff_speed_spin.setFixedHeight(28)
        layout.addWidget(self.tff_speed_spin, 0, 1)

        btn_speed = QPushButton("Set")
        btn_speed.setFixedSize(45, 28)
        btn_speed.clicked.connect(lambda: self._send_param("tm", self.tff_speed_spin.value() / 100))
        layout.addWidget(btn_speed, 0, 2)

        # TFF Gain - BLE: 'tg' (TFF_GAIN, default 0.8)
        label_tff_gain = QLabel("TFF Gain:")
        label_tff_gain.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(label_tff_gain, 1, 0)

        self.tff_gain_spin = PlusMinusSpinBox()
        self.tff_gain_spin.setRange(0, 200)
        self.tff_gain_spin.setValue(80)
        self.tff_gain_spin.setSuffix(" %")
        self.tff_gain_spin.setSingleStep(5)
        self.tff_gain_spin.setFixedHeight(28)
        layout.addWidget(self.tff_gain_spin, 1, 1)

        btn_tff_gain = QPushButton("Set")
        btn_tff_gain.setFixedSize(45, 28)
        btn_tff_gain.clicked.connect(lambda: self._send_param("tg", self.tff_gain_spin.value() / 100))
        layout.addWidget(btn_tff_gain, 1, 2)

        # --- Motion FF 구분선 ---
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep, 2, 0, 1, 3)

        # Motion FF Gain - BLE: 'fm' (FF_GAIN_MOTION, default 0.7)
        label_motion = QLabel("Motion FF:")
        label_motion.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(label_motion, 3, 0)

        self.motion_ff_spin = PlusMinusSpinBox()
        self.motion_ff_spin.setRange(0, 200)       # 0.00 ~ 2.00
        self.motion_ff_spin.setValue(70)             # 0.70
        self.motion_ff_spin.setSuffix(" %")
        self.motion_ff_spin.setSingleStep(5)
        self.motion_ff_spin.setFixedHeight(28)
        layout.addWidget(self.motion_ff_spin, 3, 1)

        btn_motion = QPushButton("Set")
        btn_motion.setFixedSize(45, 28)
        btn_motion.clicked.connect(lambda: self._send_param("fm", self.motion_ff_spin.value() / 100))
        layout.addWidget(btn_motion, 3, 2)

        # Force FF Gain - BLE: 'ff' (FF_GAIN_F, default 0.15)
        label_force_ff = QLabel("Force FF:")
        label_force_ff.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(label_force_ff, 4, 0)

        self.force_ff_spin = PlusMinusSpinBox()
        self.force_ff_spin.setRange(0, 100)          # 0.00 ~ 1.00
        self.force_ff_spin.setValue(15)               # 0.15
        self.force_ff_spin.setSuffix(" %")
        self.force_ff_spin.setSingleStep(1)
        self.force_ff_spin.setFixedHeight(28)
        layout.addWidget(self.force_ff_spin, 4, 1)

        btn_force_ff = QPushButton("Set")
        btn_force_ff.setFixedSize(45, 28)
        btn_force_ff.clicked.connect(lambda: self._send_param("ff", self.force_ff_spin.value() / 100))
        layout.addWidget(btn_force_ff, 4, 2)

        return group

    def _create_camera_group(self) -> QGroupBox:
        """Camera 그룹"""
        group = QGroupBox("Camera")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)

        self.camera_btn = QPushButton("Open Camera View")
        self.camera_btn.setEnabled(False)  # Coming soon
        self.camera_btn.setFixedHeight(36)
        self.camera_btn.setStyleSheet("""
            QPushButton {
                background-color: #374151;
                color: #9ca3af;
                font-weight: bold;
                font-size: 12px;
                border-radius: 4px;
            }
            QPushButton:enabled {
                background-color: #0ea5e9;
                color: white;
            }
            QPushButton:enabled:hover { background-color: #0284c7; }
        """)
        layout.addWidget(self.camera_btn)

        return group

    def _create_log_group(self) -> QGroupBox:
        """로그 그룹 - 맨 아래까지 확장"""
        group = QGroupBox("Log")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 14, 6, 6)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # 최대 높이 제한 없음 - 맨 아래까지 확장
        self.log_text.setStyleSheet("""
            QTextEdit {
                font-family: 'Consolas', 'Menlo', monospace;
                font-size: 10px;
                background-color: #0d0d0d;
                color: #a0a0a0;
                border: 1px solid #333;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.log_text)

        return group

    # === 슬롯 ===

    def _on_connect_clicked(self):
        idx = self.device_combo.currentIndex()
        if idx >= 0:
            self.connect_requested.emit(idx)

    def _on_mode_changed(self, index: int):
        self.param_stack.setCurrentIndex(index)
        self.mode_changed.emit(index)

    def _send_mode(self):
        mode = "mode0" if self.mode_combo.currentIndex() == 0 else "mode1"
        self.command_requested.emit(mode)

    def _send_param(self, prefix: str, value: float):
        cmd = f"{prefix}{value:.2f}"
        self.command_requested.emit(cmd)

    def _send_all_force_params(self):
        self._send_param("gs", self.onset_spin.value() / 100)
        self._send_param("gp", self.peak_gcp_spin.value() / 100)
        self._send_param("ge", self.release_spin.value() / 100)
        self._send_param("pf", self.peak_force_spin.value())

    def _send_all_position_params(self):
        self._send_param("ps", self.pos_start_spin.value() / 100)
        self._send_param("pe", self.pos_end_spin.value() / 100)
        self._send_param("pa", self.amplitude_spin.value())

    # === 공개 메서드 ===

    def update_devices(self, devices: list):
        self.device_combo.clear()
        for d in devices:
            name = d.name or "Unknown"
            self.device_combo.addItem(f"{name} ({d.address})")

    def set_connected(self, connected: bool):
        if connected:
            self.connection_label.setText("Connected")
            self.connection_label.setStyleSheet("""
                QLabel {
                    color: #4ade80;
                    font-weight: bold;
                    font-size: 12px;
                    padding: 5px;
                    background-color: #1a2f1a;
                    border-radius: 4px;
                }
            """)
            self.connect_btn.setText("Disconnect")
        else:
            self.connection_label.setText("Disconnected")
            self.connection_label.setStyleSheet("""
                QLabel {
                    color: #ff6b6b;
                    font-weight: bold;
                    font-size: 12px;
                    padding: 5px;
                    background-color: #252525;
                    border-radius: 4px;
                }
            """)
            self.connect_btn.setText("Connect")

    def log(self, msg: str):
        """로그 메시지 출력 (색상 지원)
        - [ERROR]: 빨간색
        - [WARNING]: 노란색
        - 기타: 흰색
        """
        if "[ERROR]" in msg.upper():
            color = "#ff4444"  # 빨간색
        elif "[WARNING]" in msg.upper() or "[WARN]" in msg.upper():
            color = "#ffcc00"  # 노란색
        else:
            color = "#e0e0e0"  # 흰색

        html_msg = f'<span style="color: {color};">{msg}</span>'
        self.log_text.append(html_msg)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_scale_factor(self, factor: float):
        """스케일 팩터 (사용하지 않음)"""
        pass
