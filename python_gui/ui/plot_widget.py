"""
ARWalker Plot Widget - Optimized Version (No Lock Contention)

★ 최적화 버전:
- deque 기반 (단순, 안정적)
- numpy 변환 최소화
- 현재 탭만 업데이트
"""

from collections import deque
from typing import Optional, Dict
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QFrame, QDoubleSpinBox, QPushButton, QLineEdit, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QPainter, QColor, QPen, QPixmap
import pyqtgraph as pg

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.data_parser import WalkerData


# PyQtGraph 설정 (단순화)
pg.setConfigOptions(antialias=False, useOpenGL=False)
pg.setConfigOption('background', '#0d0d0d')
pg.setConfigOption('foreground', '#e0e0e0')


# Constants
BASE_WINDOW_WIDTH = 1100
BASE_WINDOW_HEIGHT = 680
MIN_SCALE_FACTOR = 0.7
MAX_SCALE_FACTOR = 1.4


class GCPIndicator(QWidget):
    """GCP 원형 인디케이터"""

    def __init__(self, label: str, color: str = "#00aaff", parent=None):
        super().__init__(parent)
        self._label = label
        self._color = QColor(color)
        self._value = 0.0
        self._bg_cache: Optional[QPixmap] = None
        self.setFixedSize(80, 100)

    def set_value(self, value: float):
        if value > 1:
            value = value / 100.0
        new_value = max(0.0, min(1.0, value))
        # ★ 0.5% 미만 변화는 repaint 스킵 (QPainter 오버헤드 감소)
        if abs(new_value - self._value) < 0.005:
            return
        self._value = new_value
        self.update()

    def _ensure_bg_cache(self, size: int):
        if self._bg_cache is not None and self._bg_cache.width() == size:
            return
        self._bg_cache = QPixmap(size, size)
        self._bg_cache.fill(Qt.transparent)
        painter = QPainter(self._bg_cache)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = 4
        rect = (margin, margin, size - margin * 2, size - margin * 2)
        painter.setPen(QPen(QColor("#3a3a3a"), 4))
        painter.drawArc(*rect, 0, 360 * 16)
        painter.end()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        label_height = 22
        circle_size = min(self.width(), self.height() - label_height) - 8
        x = (self.width() - circle_size) // 2
        y = 4

        self._ensure_bg_cache(circle_size)
        if self._bg_cache:
            painter.drawPixmap(x, y, self._bg_cache)

        margin = 4
        rect = (x + margin, y + margin, circle_size - margin * 2, circle_size - margin * 2)

        pen = QPen(self._color, 6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        span_angle = int(-self._value * 360 * 16)
        painter.drawArc(*rect, 90 * 16, span_angle)

        painter.setPen(QColor("#ffffff"))
        font = QFont("Arial", 14, QFont.Bold)
        painter.setFont(font)
        text = f"{self._value * 100:.0f}%"
        painter.drawText(x, y, circle_size, circle_size, Qt.AlignCenter, text)

        painter.setPen(QColor("#aaaaaa"))
        font = QFont("Arial", 10)
        painter.setFont(font)
        painter.drawText(0, circle_size + 4, self.width(), label_height, Qt.AlignCenter, self._label)


class TopBarWidget(QWidget):
    """상단 바 - File + Image + GCP"""

    save_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        # File Logging - 고정 최소 너비로 안정적 배치
        file_frame = QFrame()
        file_frame.setStyleSheet("QFrame { background-color: #1a1a1a; border-radius: 6px; border: 1px solid #333; }")
        file_layout = QHBoxLayout(file_frame)
        file_layout.setContentsMargins(8, 6, 8, 6)
        file_layout.setSpacing(6)

        file_icon = QLabel("\U0001F4BE")
        file_icon.setStyleSheet("border: none; font-size: 14px;")
        file_icon.setFixedWidth(18)
        file_layout.addWidget(file_icon)

        file_label = QLabel("File:")
        file_label.setStyleSheet("color: #b0b0b0; font-weight: bold; font-size: 11px; border: none;")
        file_label.setFixedWidth(28)
        file_layout.addWidget(file_label)

        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText("(auto if empty)")
        self.filename_input.setMaxLength(20)  # ★ 펌웨어 customFilename[32] - ".CSV"(4) - 여유(8) = 20자
        self.filename_input.setMinimumWidth(100)
        self.filename_input.setMaximumWidth(160)
        self.filename_input.setFixedHeight(26)
        self.filename_input.setStyleSheet("""
            QLineEdit {
                background-color: #252525; border: 1px solid #404040;
                border-radius: 4px; padding: 3px 6px;
                color: #e0e0e0; font-size: 11px;
            }
        """)
        file_layout.addWidget(self.filename_input, 1)

        self.save_btn = QPushButton("Save")
        self.save_btn.setFixedSize(50, 26)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #0ea5e9; color: white; font-weight: bold;
                font-size: 11px; border-radius: 4px; border: none;
            }
            QPushButton:hover { background-color: #0284c7; }
        """)
        self.save_btn.clicked.connect(self._on_save_clicked)
        file_layout.addWidget(self.save_btn)

        file_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        layout.addWidget(file_frame)

        # Image - stretch=1로 가용 공간 채움
        self.image_frame = QFrame()
        self.image_frame.setStyleSheet("QFrame { background-color: #1a1a1a; border-radius: 6px; border: 1px solid #333; }")
        image_layout = QVBoxLayout(self.image_frame)
        image_layout.setContentsMargins(6, 4, 6, 4)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("color: #666; font-size: 11px; border: none;")
        self.image_label.setMinimumSize(100, 50)
        image_layout.addWidget(self.image_label)

        self.image_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.image_frame, 1)

        # GCP Indicators - 고정 너비
        gcp_frame = QFrame()
        gcp_frame.setStyleSheet("QFrame { background-color: #1a1a1a; border-radius: 6px; border: 1px solid #333; }")
        gcp_layout = QHBoxLayout(gcp_frame)
        gcp_layout.setContentsMargins(8, 4, 8, 4)
        gcp_layout.setSpacing(8)

        gcp_label = QLabel("GCP")
        gcp_label.setStyleSheet("color: #b0b0b0; font-weight: bold; font-size: 11px; border: none;")
        gcp_layout.addWidget(gcp_label)

        self.gcp_left = GCPIndicator("Left", "#22d3ee")
        self.gcp_right = GCPIndicator("Right", "#f472b6")
        gcp_layout.addWidget(self.gcp_left)
        gcp_layout.addWidget(self.gcp_right)

        gcp_frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        layout.addWidget(gcp_frame)

        self.setFixedHeight(140)
        self._original_pixmap = None

    def _on_save_clicked(self):
        self.save_requested.emit(self.filename_input.text().strip())
        self.filename_input.clear()

    def set_left_gcp(self, value: float):
        self.gcp_left.set_value(value)

    def set_right_gcp(self, value: float):
        self.gcp_right.set_value(value)

    def set_image(self, image_path: str):
        from PyQt5.QtGui import QPixmap
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self._original_pixmap = pixmap
            QTimer.singleShot(100, self._update_image)

    def _update_image(self):
        if self._original_pixmap:
            w = self.image_frame.width() - 20
            h = self.image_frame.height() - 20
            if w > 0 and h > 0:
                scaled = self._original_pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.image_label.setPixmap(scaled)
                self.image_label.setText("")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_image()


class SinglePlot(QWidget):
    """단일 플롯"""

    COLOR_LEFT = '#22d3ee'
    COLOR_RIGHT = '#f472b6'

    def __init__(self, title: str, y_range: tuple = None, parent=None):
        super().__init__(parent)
        self._title = title
        self._curves: Dict[str, pg.PlotDataItem] = {}
        self._y_range = y_range
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        # Y축 범위 컨트롤
        range_frame = QFrame()
        range_frame.setStyleSheet("QFrame { background-color: #1a1a1a; border-radius: 4px; }")
        range_bar = QHBoxLayout(range_frame)
        range_bar.setContentsMargins(8, 4, 8, 4)
        range_bar.setSpacing(6)

        range_bar.addWidget(QLabel("Y:"))

        self.y_min_spin = QDoubleSpinBox()
        self.y_min_spin.setRange(-10000, 10000)
        self.y_min_spin.setValue(self._y_range[0] if self._y_range else -100)
        self.y_min_spin.setFixedWidth(85)
        range_bar.addWidget(self.y_min_spin)

        range_bar.addWidget(QLabel("~"))

        self.y_max_spin = QDoubleSpinBox()
        self.y_max_spin.setRange(-10000, 10000)
        self.y_max_spin.setValue(self._y_range[1] if self._y_range else 100)
        self.y_max_spin.setFixedWidth(85)
        range_bar.addWidget(self.y_max_spin)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setFixedWidth(60)
        self.apply_btn.setStyleSheet("background-color: #3b82f6; color: white;")
        self.apply_btn.clicked.connect(self._apply_y_range)
        range_bar.addWidget(self.apply_btn)

        self.auto_btn = QPushButton("Auto")
        self.auto_btn.setFixedWidth(55)
        self.auto_btn.setStyleSheet("background-color: #8b5cf6; color: white;")
        self.auto_btn.clicked.connect(self._auto_y_range)
        range_bar.addWidget(self.auto_btn)

        range_bar.addStretch()
        layout.addWidget(range_frame)

        # Plot
        self.plot = pg.PlotWidget(title=self._title)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self._legend = self.plot.addLegend(offset=(70, 10))
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.getPlotItem().setMenuEnabled(False)
        self.plot.setClipToView(True)
        self.plot.setDownsampling(auto=True, mode='peak')

        # ★ X축 auto-range 활성화 (deque maxlen으로 자연 스크롤)
        self.plot.enableAutoRange(axis='x', enable=True)

        if self._y_range:
            self.plot.setYRange(*self._y_range)

        layout.addWidget(self.plot)

    def _apply_y_range(self):
        y_min, y_max = self.y_min_spin.value(), self.y_max_spin.value()
        if y_min < y_max:
            self.plot.setYRange(y_min, y_max)
            self.plot.enableAutoRange(axis='y', enable=False)

    def _auto_y_range(self):
        self.plot.enableAutoRange(axis='y', enable=True)

    def add_curve(self, name: str, color: str, style=Qt.SolidLine):
        pen = pg.mkPen(color, width=2, style=style)
        curve = pg.PlotDataItem(pen=pen, name=name)
        self.plot.addItem(curve)
        self._curves[name] = curve
        return curve

    def update_curve(self, name: str, x_data, y_data):
        if name in self._curves:
            self._curves[name].setData(x_data, y_data)

    def batch_update(self, updates: list):
        """★ 배치 업데이트: N번 autoRange 재계산 → 1번으로 감소

        setData() 호출 시마다 ViewBox.updateAutoRange()가 발동되어
        모든 커브의 dataBounds()를 순회. 4개 커브면 4×4=16번 순회.
        배치 처리하면 1×4=4번으로 감소 (4배 빠름).

        Args:
            updates: [(curve_name, x_data, y_data), ...]
        """
        self.plot.enableAutoRange(axis='x', enable=False)
        for name, x_data, y_data in updates:
            if name in self._curves:
                self._curves[name].setData(x_data, y_data)
        self.plot.enableAutoRange(axis='x', enable=True)


class PlotTabWidget(QWidget):
    """탭 기반 플롯 위젯 - 단순화된 버전"""

    BUFFER_SIZE = 150  # ★ 50Hz × 3초 = 150샘플 (실시간 모니터링에 최적)

    gcp_updated = pyqtSignal(float, float)
    save_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = 0
        self._sample_count = 0

        # ★ 단순한 deque 버퍼 (lock 없음)
        self._buffers = self._init_buffers()

        self._init_ui()

    def _init_buffers(self) -> dict:
        keys = [
            'time', 'l_gcp', 'r_gcp', 'l_pitch', 'r_pitch',
            'l_gyro', 'r_gyro', 'l_pos', 'r_pos', 'l_vel', 'r_vel',
            'l_curr', 'r_curr', 'l_des_pos', 'r_des_pos',
            'l_des_force', 'r_des_force', 'l_act_force', 'r_act_force'
        ]
        return {k: deque(maxlen=self.BUFFER_SIZE) for k in keys}

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.top_bar = TopBarWidget()
        self.top_bar.save_requested.connect(self.save_requested.emit)
        layout.addWidget(self.top_bar)

        self.tab_widget = QTabWidget()
        self._update_tab_style()
        layout.addWidget(self.tab_widget, 1)

        self._create_tabs()

    def _update_tab_style(self):
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #333; background-color: #0d0d0d; }
            QTabBar::tab { background-color: #252525; color: #aaa; padding: 8px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background-color: #404040; color: #fff; }
            QTabBar::tab:hover { background-color: #353535; }
        """)

    def _create_tabs(self):
        self.tab_widget.clear()

        if self._mode == 0:
            self.force_plot = SinglePlot("Force (N)", (-10, 120))
            self.force_plot.add_curve("L Desired", "#34d399", Qt.DashLine)
            self.force_plot.add_curve("L Actual", "#22d3ee")
            self.force_plot.add_curve("R Desired", "#a78bfa", Qt.DashLine)
            self.force_plot.add_curve("R Actual", "#f472b6")
        else:
            self.force_plot = SinglePlot("Force (N)", (-10, 120))
            self.force_plot.add_curve("L Force", SinglePlot.COLOR_LEFT)
            self.force_plot.add_curve("R Force", SinglePlot.COLOR_RIGHT)
        self.tab_widget.addTab(self.force_plot, "Force")

        self.imu_plot = SinglePlot("IMU Pitch (deg)", (-45, 45))
        self.imu_plot.add_curve("L Pitch", SinglePlot.COLOR_LEFT)
        self.imu_plot.add_curve("R Pitch", SinglePlot.COLOR_RIGHT)
        self.tab_widget.addTab(self.imu_plot, "IMU Pitch")

        self.gyro_plot = SinglePlot("IMU Gyro Y (deg/s)", (-200, 200))
        self.gyro_plot.add_curve("L Gyro", SinglePlot.COLOR_LEFT)
        self.gyro_plot.add_curve("R Gyro", SinglePlot.COLOR_RIGHT)
        self.tab_widget.addTab(self.gyro_plot, "Gyro")

        self.pos_plot = SinglePlot("Motor Position (deg)", (-500, 500))
        self.pos_plot.add_curve("L Actual", SinglePlot.COLOR_LEFT)
        self.pos_plot.add_curve("R Actual", SinglePlot.COLOR_RIGHT)
        if self._mode == 1:
            self.pos_plot.add_curve("L Desired", "#34d399", Qt.DashLine)
            self.pos_plot.add_curve("R Desired", "#a78bfa", Qt.DashLine)
        self.tab_widget.addTab(self.pos_plot, "Position")

        self.vel_plot = SinglePlot("Motor Velocity (eRPM)", (-20000, 20000))
        self.vel_plot.add_curve("L Velocity", SinglePlot.COLOR_LEFT)
        self.vel_plot.add_curve("R Velocity", SinglePlot.COLOR_RIGHT)
        self.tab_widget.addTab(self.vel_plot, "Velocity")

        self.curr_plot = SinglePlot("Motor Current (A)", (-25, 25))
        self.curr_plot.add_curve("L Current", SinglePlot.COLOR_LEFT)
        self.curr_plot.add_curve("R Current", SinglePlot.COLOR_RIGHT)
        self.tab_widget.addTab(self.curr_plot, "Current")

    def set_mode(self, mode: int):
        if mode != self._mode:
            self._mode = mode
            current = self.tab_widget.currentIndex()
            self._create_tabs()
            if current < self.tab_widget.count():
                self.tab_widget.setCurrentIndex(current)

    def add_data(self, data: WalkerData):
        """데이터 추가 - 단순 deque append"""
        self._sample_count += 1
        b = self._buffers

        b['time'].append(self._sample_count)
        b['l_gcp'].append(data.l_gcp * 100)
        b['r_gcp'].append(data.r_gcp * 100)
        b['l_pitch'].append(data.l_pitch)
        b['r_pitch'].append(data.r_pitch)
        b['l_gyro'].append(data.l_gyro_y)
        b['r_gyro'].append(data.r_gyro_y)
        b['l_pos'].append(data.l_motor_pos)
        b['r_pos'].append(data.r_motor_pos)
        b['l_vel'].append(data.l_motor_vel)
        b['r_vel'].append(data.r_motor_vel)
        b['l_curr'].append(data.l_motor_curr)
        b['r_curr'].append(data.r_motor_curr)
        b['l_des_pos'].append(data.l_des_pos)
        b['r_des_pos'].append(data.r_des_pos)
        b['l_des_force'].append(data.l_des_force)
        b['r_des_force'].append(data.r_des_force)
        b['l_act_force'].append(data.l_act_force)
        b['r_act_force'].append(data.r_act_force)

    def _to_array(self, d: deque) -> np.ndarray:
        """deque → numpy (캐시 재사용)"""
        n = len(d)
        if n == 0:
            return np.array([], dtype=np.float32)
        # 직접 복사 (list() 중간 단계 없음)
        arr = np.fromiter(d, dtype=np.float32, count=n)
        return arr

    def update_plots(self):
        """현재 탭만 업데이트 - batch_update로 autoRange 중복 재계산 방지"""
        b = self._buffers
        if len(b['time']) < 2:
            return

        # GCP 업데이트 (set_value 내부에서 변화량 threshold 체크)
        if b['l_gcp'] and b['r_gcp']:
            self.top_bar.set_left_gcp(b['l_gcp'][-1])
            self.top_bar.set_right_gcp(b['r_gcp'][-1])

        # 현재 탭만 업데이트
        current = self.tab_widget.currentIndex()
        tab_text = self.tab_widget.tabText(current)

        time_arr = self._to_array(b['time'])

        if "IMU" in tab_text and "Gyro" not in tab_text:
            self.imu_plot.batch_update([
                ("L Pitch", time_arr, self._to_array(b['l_pitch'])),
                ("R Pitch", time_arr, self._to_array(b['r_pitch'])),
            ])

        elif "Position" in tab_text:
            updates = [
                ("L Actual", time_arr, self._to_array(b['l_pos'])),
                ("R Actual", time_arr, self._to_array(b['r_pos'])),
            ]
            if self._mode == 1:
                updates.extend([
                    ("L Desired", time_arr, self._to_array(b['l_des_pos'])),
                    ("R Desired", time_arr, self._to_array(b['r_des_pos'])),
                ])
            self.pos_plot.batch_update(updates)

        elif "Velocity" in tab_text:
            self.vel_plot.batch_update([
                ("L Velocity", time_arr, self._to_array(b['l_vel'])),
                ("R Velocity", time_arr, self._to_array(b['r_vel'])),
            ])

        elif "Current" in tab_text:
            self.curr_plot.batch_update([
                ("L Current", time_arr, self._to_array(b['l_curr'])),
                ("R Current", time_arr, self._to_array(b['r_curr'])),
            ])

        elif "Force" in tab_text:
            if self._mode == 0:
                self.force_plot.batch_update([
                    ("L Desired", time_arr, self._to_array(b['l_des_force'])),
                    ("L Actual", time_arr, self._to_array(b['l_act_force'])),
                    ("R Desired", time_arr, self._to_array(b['r_des_force'])),
                    ("R Actual", time_arr, self._to_array(b['r_act_force'])),
                ])
            else:
                self.force_plot.batch_update([
                    ("L Force", time_arr, self._to_array(b['l_act_force'])),
                    ("R Force", time_arr, self._to_array(b['r_act_force'])),
                ])

        elif "Gyro" in tab_text:
            self.gyro_plot.batch_update([
                ("L Gyro", time_arr, self._to_array(b['l_gyro'])),
                ("R Gyro", time_arr, self._to_array(b['r_gyro'])),
            ])

    def clear_data(self):
        self._buffers = self._init_buffers()
        self._sample_count = 0

        empty = np.array([], dtype=np.float32)
        for plot in [self.force_plot, self.imu_plot, self.gyro_plot,
                     self.pos_plot, self.vel_plot, self.curr_plot]:
            if hasattr(plot, '_curves'):
                for curve in plot._curves.values():
                    curve.setData(empty, empty)

        self.top_bar.set_left_gcp(0.0)
        self.top_bar.set_right_gcp(0.0)

    def get_latest_values(self) -> dict:
        b = self._buffers
        return {
            'l_gcp': b['l_gcp'][-1] if b['l_gcp'] else 0,
            'r_gcp': b['r_gcp'][-1] if b['r_gcp'] else 0,
            'l_force': b['l_act_force'][-1] if b['l_act_force'] else 0,
            'r_force': b['r_act_force'][-1] if b['r_act_force'] else 0,
            'samples': self._sample_count
        }

    def set_scale_factor(self, factor: float):
        pass
