"""
H-Walker Main Window

TopNav + QStackedWidget (4 modes) + BLE orchestration

BLE 핵심 설계 (절대 변경 금지):
1. 반응성 - GUI 블로킹 없음 (BLE는 별도 스레드)
2. 성능 - 33ms 플롯 업데이트 (30Hz), 현재 탭만 렌더링
3. 정확성 - 데이터 파싱 에러 처리, 명령 확인
4. 큐 기반 비동기 처리 + QueuedConnection 스레드 안전성
"""

import sys
import os
from collections import deque
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QStackedWidget, QFrame, QStatusBar, QApplication
)
from PyQt5.QtCore import Qt, QTimer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ble_client import BleClientThread
from core.data_parser import WalkerDataParser
from ui.top_nav import TopNav
from ui.control_panel import ControlPanel
from ui.plot_widget import PlotTabWidget
from ui.realtime_mode import RealtimeMode
from ui.analysis_mode import AnalysisMode
from ui.file_mode import FileMode
from ui.camera_mode import CameraMode
from ui.styles import get_stylesheet


class MainWindow(QMainWindow):
    """H-Walker GUI Main Window

    BLE 프리징 방지 핵심:
    - 데이터 수신: BLE 스레드 -> _raw_data_queue에 쌓기만 함 (빠름)
    - 데이터 처리: 타이머 콜백에서 큐 비우면서 처리 (throttled)
    """

    PLOT_UPDATE_INTERVAL_MS = 33   # 33ms = 30Hz GUI 업데이트

    def __init__(self):
        super().__init__()

        self.setWindowTitle("H-Walker Control")
        self.resize(1360, 840)
        self.setMinimumSize(1000, 650)

        # 데이터 큐 - BLE 스레드에서 쌓고, 타이머에서 처리
        self._raw_data_queue: deque = deque(maxlen=1000)

        # 디버그용 변수
        self._last_sample_count = 0
        self._data_rate = 0.0

        # 컴포넌트 초기화
        self._ble_client = BleClientThread()
        self._data_parser = WalkerDataParser()
        self._devices = []

        # UI 초기화
        self._init_ui()

        # 시그널 연결
        self._connect_signals()

        # BLE 스레드 시작
        self._ble_client.start()

        # 통합 업데이트 타이머 (데이터 처리 + 플롯 업데이트)
        self._plot_timer = QTimer()
        self._plot_timer.timeout.connect(self._process_and_update)
        self._plot_timer.start(self.PLOT_UPDATE_INTERVAL_MS)

        # 상태 업데이트 타이머 (500ms)
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

    def _init_ui(self):
        """UI: TopNav + QStackedWidget (4 modes)"""
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top navigation bar
        self.nav = TopNav()
        main_layout.addWidget(self.nav)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background:rgba(255,255,255,0.04);")
        main_layout.addWidget(divider)

        # Stacked widget (4 modes)
        self.stack = QStackedWidget()

        # Mode 0: Realtime - reuse existing ControlPanel + PlotTabWidget
        self.control_panel = ControlPanel()
        self.plot_widget = PlotTabWidget()
        self.realtime_mode = RealtimeMode(self.control_panel, self.plot_widget)

        # Mode 1: Analysis
        self.analysis_mode = AnalysisMode()

        # Mode 2: Files
        self.file_mode = FileMode()

        # Mode 3: Camera
        self.camera_mode = CameraMode()

        self.stack.addWidget(self.realtime_mode)   # index 0
        self.stack.addWidget(self.analysis_mode)   # index 1
        self.stack.addWidget(self.file_mode)       # index 2
        self.stack.addWidget(self.camera_mode)     # index 3

        main_layout.addWidget(self.stack, 1)

        # Nav -> Stack connection
        self.nav.mode_changed.connect(self.stack.setCurrentIndex)

        # File mode -> Analysis mode connection
        self.file_mode.open_in_analysis.connect(self._open_in_analysis)

        # Plot save signal
        self.plot_widget.save_requested.connect(self._on_save_requested)

        # Status bar
        sb = self.statusBar()
        sb.showMessage("H-Walker Ready                                                                                    Designed by CBJ")

    def _open_in_analysis(self, path: str):
        """FileMode -> AnalysisMode 전환"""
        self.analysis_mode.load_file(path)
        self.stack.setCurrentIndex(1)
        self.nav.set_mode(1)

    def _on_save_requested(self, filename: str):
        """File Save 요청 핸들러 - 펌웨어 로깅 토글"""
        if filename:
            cmd = f"save{filename}"
            self.control_panel.log(f"Toggle logging (filename: {filename})")
        else:
            cmd = "save"
            self.control_panel.log("Toggle logging (auto filename)")
        self._on_send_command(cmd)

    # =========================================================
    # BLE Signal Connections (preserved exactly)
    # =========================================================

    def _connect_signals(self):
        """시그널 연결 - QueuedConnection으로 스레드 안전성 보장"""
        # BLE 클라이언트 시그널
        self._ble_client.signals.connected.connect(self._on_connected, Qt.QueuedConnection)
        self._ble_client.signals.disconnected.connect(self._on_disconnected, Qt.QueuedConnection)
        self._ble_client.signals.reconnecting.connect(self._on_reconnecting, Qt.QueuedConnection)
        self._ble_client.signals.data_received.connect(self._on_data_received, Qt.QueuedConnection)
        self._ble_client.signals.error.connect(self._on_error, Qt.QueuedConnection)
        self._ble_client.signals.devices_found.connect(self._on_devices_found, Qt.QueuedConnection)
        self._ble_client.signals.command_sent.connect(self._on_command_sent, Qt.QueuedConnection)

        # 제어 패널 시그널
        self.control_panel.scan_requested.connect(self._on_scan)
        self.control_panel.connect_requested.connect(self._on_connect)
        self.control_panel.disconnect_requested.connect(self._on_disconnect)
        self.control_panel.command_requested.connect(self._on_send_command)
        self.control_panel.mode_changed.connect(self._on_mode_changed)
        self.control_panel.clear_btn.clicked.connect(self._on_clear_data)

    # =========================================================
    # BLE Event Handlers (preserved exactly)
    # =========================================================

    def _on_scan(self):
        self.control_panel.device_combo.clear()
        self.control_panel.log("Scanning for BLE devices...")
        self.statusBar().showMessage("Scanning...")
        self._ble_client.scan()

    def _on_devices_found(self, devices):
        self._devices = devices
        self.control_panel.update_devices(devices)
        self.control_panel.log(f"Found {len(devices)} devices")
        self.statusBar().showMessage(f"Found {len(devices)} devices")

    def _on_connect(self, device_index: int):
        if self._ble_client.is_connected:
            self._ble_client.disconnect_device()
        elif device_index < len(self._devices):
            device = self._devices[device_index]
            self.control_panel.log(f"Connecting to {device.name}...")
            self.statusBar().showMessage(f"Connecting to {device.name}...")
            self._ble_client.connect_device(device)

    def _on_disconnect(self):
        self._ble_client.disconnect_device()

    def _on_connected(self):
        self.control_panel.set_connected(True)
        self.control_panel.log("Connected!")
        self.statusBar().showMessage("Connected - Receiving data")
        self._data_parser.reset()
        self._raw_data_queue.clear()

    def _on_disconnected(self):
        self.control_panel.set_connected(False)
        self.control_panel.log("Disconnected")
        self.statusBar().showMessage("Disconnected")

    def _on_reconnecting(self, attempt: int):
        self.statusBar().showMessage(f"Reconnecting... (attempt {attempt})")
        self.control_panel.log(f"Reconnecting... attempt {attempt}")

    def _on_data_received(self, data: str):
        """데이터 수신 - 큐에 쌓기만 함 (처리는 타이머에서)"""
        if 'SR:' in data:
            self._handle_response_packet(data)
        if 'SE' in data:
            self._handle_error_packet(data)
        self._raw_data_queue.append(data)

    def _handle_error_packet(self, data: str):
        import re
        matches = re.findall(r'SE([A-Z_]+)', data)
        for error_code in matches:
            if error_code == 'SENSORS_OK':
                self.control_panel.log('[OK] All sensors ready')
                self.statusBar().showMessage('All sensors ready - OK to enable')
                continue
            error_messages = {
                'SD_NOT_CONNECTED': '[ERROR] NO SD CARD',
                'MOTOR_CAN_ERROR': '[ERROR] MOTOR CAN - No feedback',
                'MOTOR_CAN_LEFT_ERROR': '[ERROR] MOTOR CAN LEFT - No feedback',
                'MOTOR_CAN_RIGHT_ERROR': '[ERROR] MOTOR CAN RIGHT - No feedback',
                'IMU_ERROR': '[ERROR] IMU - No data received',
                'IMU_LEFT_ERROR': '[ERROR] IMU LEFT - No data',
                'IMU_RIGHT_ERROR': '[ERROR] IMU RIGHT - No data',
                'LOADCELL_ERROR': '[ERROR] LOADCELL - Abnormal value',
                'LOADCELL_LEFT_ERROR': '[ERROR] LOADCELL LEFT - Abnormal',
                'LOADCELL_RIGHT_ERROR': '[ERROR] LOADCELL RIGHT - Abnormal',
                'POSITION_LIMIT': '[ERROR] POSITION - Limit exceeded',
                'FORCE_LIMIT': '[ERROR] FORCE - Limit exceeded',
                'WATCHDOG': '[ERROR] WATCHDOG - Timeout',
                'SAFETY_TRIGGERED': '[ERROR] SAFETY - Reset required',
                'UNKNOWN': '[ERROR] UNKNOWN',
            }
            msg = error_messages.get(error_code, f'[ERROR] {error_code}')
            self.control_panel.log(msg)
            self.statusBar().showMessage(msg)

    def _handle_response_packet(self, data: str):
        import re
        matches = re.findall(r'SR:([^\n]+)', data)
        for resp in matches:
            resp = resp.strip()
            if resp.startswith('LOG_START:'):
                fname = resp[10:]
                self.control_panel.log(f'[SD] Logging started: {fname}')
                self.statusBar().showMessage(f'Logging: {fname}')
            elif resp.startswith('LOG_STOP:'):
                fname = resp[9:]
                self.control_panel.log(f'[SD] Saved: {fname}')
                self.statusBar().showMessage(f'Saved: {fname}')
            elif resp.startswith('LOG_FAIL:'):
                reason = resp[9:]
                self.control_panel.log(f'[SD] Save FAILED: {reason}')
                self.statusBar().showMessage(f'Save failed: {reason}')
            elif resp == 'MOTORS_ON':
                self.control_panel.log('[FW] Motors ENABLED')
            elif resp == 'MOTORS_OFF':
                self.control_panel.log('[FW] Motors DISABLED')
            else:
                self.control_panel.log(f'[FW] {resp}')

    def _on_error(self, msg: str):
        self.control_panel.log(f"ERROR: {msg}")
        self.statusBar().showMessage(f"Error: {msg}")

    def _on_command_sent(self, cmd: str):
        self.control_panel.log(f"Sent: {cmd}")

    def _on_send_command(self, cmd: str):
        if self._ble_client.is_connected:
            self._ble_client.send_command(cmd)
        else:
            self.control_panel.log("Not connected!")

    def _on_mode_changed(self, mode: int):
        """Control mode change (Force/Position)"""
        self.plot_widget.set_mode(mode)
        mode_name = "Force Assist" if mode == 0 else "Position Assist"
        self.control_panel.log(f"Mode changed to: {mode_name}")

    def _on_clear_data(self):
        self._raw_data_queue.clear()
        self.plot_widget.clear_data()
        self._data_parser.reset()
        self.control_panel.log("Data cleared")

    # =========================================================
    # Periodic Updates (preserved exactly - 7 optimizations)
    # =========================================================

    def _process_and_update(self):
        """통합 업데이트 (30Hz) - 큐 전체 처리 + 조건부 렌더링"""
        has_new_data = False

        while self._raw_data_queue:
            data = self._raw_data_queue.popleft()
            results = self._data_parser.feed(data)
            for walker_data in results:
                self.plot_widget.add_data(walker_data)
                has_new_data = True

        if has_new_data:
            self.plot_widget.update_plots()

    def _on_mode_changed_status(self, mode: int):
        """Update realtime_mode status when control mode changes"""
        mode_name = "Force" if mode == 0 else "Position"
        self.realtime_mode.update_status(mode=mode_name)

    def _update_status(self):
        """상태바 업데이트 (2Hz)"""
        current_samples = self._data_parser.sample_count
        delta = current_samples - self._last_sample_count
        if delta < 0:
            delta = 0
        self._data_rate = delta * 2
        self._last_sample_count = current_samples

        # Update realtime mode status bar
        self.realtime_mode.update_status(rate_hz=self._data_rate)

        if self._ble_client.is_connected:
            vals = self.plot_widget.get_latest_values()
            self.statusBar().showMessage(
                f"L_GCP: {vals['l_gcp']:.1f}% | R_GCP: {vals['r_gcp']:.1f}% | "
                f"L_Force: {vals['l_force']:.1f}N | R_Force: {vals['r_force']:.1f}N | "
                f"Samples: {vals['samples']} | "
                f"Parse Errors: {self._data_parser.parse_errors}"
            )

    def closeEvent(self, event):
        self._plot_timer.stop()
        self._status_timer.stop()
        self._ble_client.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(get_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
