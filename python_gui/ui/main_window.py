"""
ARWalker Main Window

메인 윈도우 - 모든 컴포넌트 통합

핵심 설계:
1. 반응성 - GUI 블로킹 없음 (BLE는 별도 스레드)
2. 성능 - 50ms 플롯 업데이트 (20Hz), 현재 탭만 렌더링
3. 정확성 - 데이터 파싱 에러 처리, 명령 확인
4. 반응형 레이아웃 - 창 크기에 따라 폰트/위젯 자동 스케일링

★★★ GUI 프리징 방지 핵심 설계:
- 데이터 수신과 처리 완전 분리
- 큐 기반 비동기 처리
- QueuedConnection으로 스레드 안전성 보장
"""

import sys
from collections import deque
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStatusBar, QApplication
)
from PyQt5.QtCore import Qt, QTimer

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ble_client import BleClientThread
from core.data_parser import WalkerDataParser
from ui.plot_widget import PlotTabWidget, BASE_WINDOW_WIDTH, BASE_WINDOW_HEIGHT, MIN_SCALE_FACTOR, MAX_SCALE_FACTOR
from ui.control_panel import ControlPanel


class MainWindow(QMainWindow):
    """ARWalker GUI 메인 윈도우 - 반응형 스케일링 지원

    ★★★ GUI 프리징 방지 핵심:
    - 데이터 수신: BLE 스레드 → _raw_data_queue에 쌓기만 함 (빠름)
    - 데이터 처리: 타이머 콜백에서 큐 비우면서 처리 (throttled)
    """

    # ★ 설정 상수 - 펌웨어 50Hz에 맞춤
    PLOT_UPDATE_INTERVAL_MS = 33   # 33ms = 30Hz GUI 업데이트

    def __init__(self):
        super().__init__()

        self.setWindowTitle("ARWalker Control - No Jetson")
        # ★ 반응형: 작은 화면도 허용, 스크롤로 대응
        self.resize(1300, 800)
        self.setMinimumSize(800, 500)  # 최소 크기 축소 - 스크롤 패널이 대응

        # 스케일링 관련
        self._current_scale = 1.0
        self._base_stylesheet = ""

        # ★★★ 데이터 큐 - BLE 스레드에서 쌓고, 타이머에서 처리
        self._raw_data_queue: deque = deque(maxlen=1000)

        # ★ 디버그용 변수 (항상 출력)
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

        # ★★★ 통합 업데이트 타이머 (데이터 처리 + 플롯 업데이트)
        self._plot_timer = QTimer()
        self._plot_timer.timeout.connect(self._process_and_update)
        self._plot_timer.start(self.PLOT_UPDATE_INTERVAL_MS)

        # 상태 업데이트 타이머 (500ms)
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

        # 초기 스케일 적용
        self._apply_scale(self._calculate_scale_factor())

        # 이미지 설정 - 스크립트 위치 기준 상대 경로
        logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logo.png")
        self.plot_widget.top_bar.set_image(logo_path)

    def _init_ui(self):
        """UI 초기화 - 레이아웃: [ControlPanel | PlotWidget (상단에 File+Image+GCP)]"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(8)

        # 왼쪽: 제어 패널
        self.control_panel = ControlPanel()
        main_layout.addWidget(self.control_panel)

        # 오른쪽: 플롯 위젯 (상단에 File Logging + Image + GCP 포함, stretch로 확장)
        self.plot_widget = PlotTabWidget()
        main_layout.addWidget(self.plot_widget, 1)  # stretch=1로 확장

        # PlotWidget의 File Save 시그널 연결
        self.plot_widget.save_requested.connect(self._on_save_requested)

        # 상태바
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready - Click 'Scan' to find devices")

    def _on_save_requested(self, filename: str):
        """File Save 요청 핸들러 - 펌웨어 로깅 토글

        펌웨어 프로토콜:
        - "save"         → 자동 파일명으로 로깅 시작/중지
        - "save<name>"   → 지정 파일명으로 로깅 시작 (예: "saveTEST01")
        """
        if filename:
            cmd = f"save{filename}"
            self.control_panel.log(f"Toggle logging (filename: {filename})")
        else:
            cmd = "save"
            self.control_panel.log("Toggle logging (auto filename)")
        self._on_send_command(cmd)

    def _calculate_scale_factor(self) -> float:
        """현재 윈도우 크기에 기반한 스케일 팩터 계산"""
        width_scale = self.width() / BASE_WINDOW_WIDTH
        height_scale = self.height() / BASE_WINDOW_HEIGHT
        # 너비와 높이 중 작은 쪽에 맞춤 (종횡비 유지)
        scale = min(width_scale, height_scale)
        # 범위 제한
        return max(MIN_SCALE_FACTOR, min(MAX_SCALE_FACTOR, scale))

    def _apply_scale(self, scale: float):
        """스케일 팩터 적용 (현재 사용하지 않음)"""
        if abs(scale - self._current_scale) < 0.05:
            return  # 변화가 미미하면 무시
        self._current_scale = scale
        # 레이아웃 안정성을 위해 스케일링 비활성화

    def resizeEvent(self, event):
        """윈도우 리사이즈 이벤트"""
        super().resizeEvent(event)
        new_scale = self._calculate_scale_factor()
        self._apply_scale(new_scale)

    def _connect_signals(self):
        """시그널 연결

        ★★★ QueuedConnection 사용:
        - BLE 스레드에서 emit된 시그널을 메인 스레드의 이벤트 큐에 추가
        - 즉시 실행하지 않고, Qt 이벤트 루프가 처리할 때 실행
        - 크로스-스레드 안전성 보장
        """
        # BLE 클라이언트 시그널 - ★★★ QueuedConnection으로 스레드 안전성 보장
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

    # === BLE 이벤트 핸들러 ===

    def _on_scan(self):
        """스캔 요청"""
        self.control_panel.device_combo.clear()
        self.control_panel.log("Scanning for BLE devices...")
        self.status_bar.showMessage("Scanning...")
        self._ble_client.scan()

    def _on_devices_found(self, devices):
        """디바이스 발견"""
        self._devices = devices
        self.control_panel.update_devices(devices)
        self.control_panel.log(f"Found {len(devices)} devices")
        self.status_bar.showMessage(f"Found {len(devices)} devices")

    def _on_connect(self, device_index: int):
        """연결 요청"""
        if self._ble_client.is_connected:
            self._ble_client.disconnect_device()
        elif device_index < len(self._devices):
            device = self._devices[device_index]
            self.control_panel.log(f"Connecting to {device.name}...")
            self.status_bar.showMessage(f"Connecting to {device.name}...")
            self._ble_client.connect_device(device)

    def _on_disconnect(self):
        """연결 해제 요청"""
        self._ble_client.disconnect_device()

    def _on_connected(self):
        """연결 성공"""
        self.control_panel.set_connected(True)
        self.control_panel.log("Connected!")
        self.status_bar.showMessage("Connected - Receiving data")
        # ★ 재연결 시 파서 상태 초기화 (잔여 버퍼로 인한 파싱 에러 방지)
        self._data_parser.reset()
        self._raw_data_queue.clear()

    def _on_disconnected(self):
        """연결 해제"""
        self.control_panel.set_connected(False)
        self.control_panel.log("Disconnected")
        self.status_bar.showMessage("Disconnected")

    def _on_reconnecting(self, attempt: int):
        """자동 재연결 시도 중"""
        self.status_bar.showMessage(f"Reconnecting... (attempt {attempt})")
        self.control_panel.log(f"Reconnecting... attempt {attempt}")

    def _on_data_received(self, data: str):
        """데이터 수신 - ★★★ 큐에 쌓기만 함 (처리는 타이머에서)

        핵심: 이 함수는 최대한 빠르게 리턴해야 함
        - 데이터 파싱 안 함
        - 플롯 업데이트 안 함
        - 그냥 큐에 추가만
        """
        # ★ 펌웨어 응답 패킷 처리 (SR:...)
        if 'SR:' in data:
            self._handle_response_packet(data)

        # ★ 에러 메시지 체크 (SE로 시작하는 패킷)
        if 'SE' in data:
            self._handle_error_packet(data)

        # ★★★ 항상 큐에 추가 (파서가 SW/SG 패킷만 추출, SE/SR은 자연 스킵)
        # 이전 버그: 'SE' 발견 시 return → 센서 데이터 손실
        self._raw_data_queue.append(data)

    def _handle_error_packet(self, data: str):
        """펌웨어에서 보낸 에러 메시지 처리"""
        import re
        matches = re.findall(r'SE([A-Z_]+)', data)
        for error_code in matches:
            # ★ SENSORS_OK is a success message, not an error
            if error_code == 'SENSORS_OK':
                self.control_panel.log('[OK] All sensors ready')
                self.status_bar.showMessage('All sensors ready - OK to enable')
                continue

            error_messages = {
                # SD Card errors
                'SD_NOT_CONNECTED': '[ERROR] NO SD CARD',
                # Motor CAN errors (runtime - per side)
                'MOTOR_CAN_ERROR': '[ERROR] MOTOR CAN - No feedback',
                'MOTOR_CAN_LEFT_ERROR': '[ERROR] MOTOR CAN LEFT - No feedback',
                'MOTOR_CAN_RIGHT_ERROR': '[ERROR] MOTOR CAN RIGHT - No feedback',
                # IMU errors (per side)
                'IMU_ERROR': '[ERROR] IMU - No data received',
                'IMU_LEFT_ERROR': '[ERROR] IMU LEFT - No data',
                'IMU_RIGHT_ERROR': '[ERROR] IMU RIGHT - No data',
                # Loadcell errors (per side)
                'LOADCELL_ERROR': '[ERROR] LOADCELL - Abnormal value',
                'LOADCELL_LEFT_ERROR': '[ERROR] LOADCELL LEFT - Abnormal',
                'LOADCELL_RIGHT_ERROR': '[ERROR] LOADCELL RIGHT - Abnormal',
                # Position/Force limits
                'POSITION_LIMIT': '[ERROR] POSITION - Limit exceeded',
                'FORCE_LIMIT': '[ERROR] FORCE - Limit exceeded',
                # Watchdog
                'WATCHDOG': '[ERROR] WATCHDOG - Timeout',
                # Safety already triggered
                'SAFETY_TRIGGERED': '[ERROR] SAFETY - Reset required',
                # Unknown
                'UNKNOWN': '[ERROR] UNKNOWN',
            }
            msg = error_messages.get(error_code, f'[ERROR] {error_code}')
            self.control_panel.log(msg)
            self.status_bar.showMessage(msg)

    def _handle_response_packet(self, data: str):
        """펌웨어에서 보낸 응답 메시지 처리 (SR:... 패킷)"""
        import re
        matches = re.findall(r'SR:([^\n]+)', data)
        for resp in matches:
            resp = resp.strip()
            if resp.startswith('LOG_START:'):
                fname = resp[10:]
                self.control_panel.log(f'[SD] Logging started: {fname}')
                self.status_bar.showMessage(f'Logging: {fname}')
            elif resp.startswith('LOG_STOP:'):
                fname = resp[9:]
                self.control_panel.log(f'[SD] Saved: {fname}')
                self.status_bar.showMessage(f'Saved: {fname}')
            elif resp.startswith('LOG_FAIL:'):
                reason = resp[9:]
                self.control_panel.log(f'[SD] Save FAILED: {reason}')
                self.status_bar.showMessage(f'Save failed: {reason}')
            elif resp == 'MOTORS_ON':
                self.control_panel.log('[FW] Motors ENABLED')
            elif resp == 'MOTORS_OFF':
                self.control_panel.log('[FW] Motors DISABLED')
            else:
                self.control_panel.log(f'[FW] {resp}')

    def _on_error(self, msg: str):
        """에러 발생"""
        self.control_panel.log(f"ERROR: {msg}")
        self.status_bar.showMessage(f"Error: {msg}")

    def _on_command_sent(self, cmd: str):
        """명령 전송 확인"""
        self.control_panel.log(f"Sent: {cmd}")

    def _on_send_command(self, cmd: str):
        """명령 전송 요청"""
        if self._ble_client.is_connected:
            self._ble_client.send_command(cmd)
        else:
            self.control_panel.log("Not connected!")

    def _on_mode_changed(self, mode: int):
        """모드 변경"""
        self.plot_widget.set_mode(mode)
        mode_name = "Force Assist" if mode == 0 else "Position Assist"
        self.control_panel.log(f"Mode changed to: {mode_name}")

    def _on_clear_data(self):
        """데이터 초기화"""
        self._raw_data_queue.clear()  # ★ 큐도 클리어
        self.plot_widget.clear_data()
        self._data_parser.reset()
        self.control_panel.log("Data cleared")

    # === 주기적 업데이트 ===

    def _process_and_update(self):
        """★★★ 통합 업데이트 함수 (30Hz)

        핵심 설계:
        - 큐의 모든 데이터를 즉시 처리 (파싱은 빠름, ~100μs)
        - 렌더링은 새 데이터가 있을 때만 1회 수행
        - 이전의 MAX_PROCESS_PER_TICK 제한은 burst 시 지연 누적의 원인이었음
        """
        has_new_data = False

        # ★ 큐의 모든 데이터 즉시 처리 (파싱은 문자열 연산이라 빠름)
        while self._raw_data_queue:
            data = self._raw_data_queue.popleft()
            results = self._data_parser.feed(data)
            for walker_data in results:
                self.plot_widget.add_data(walker_data)
                has_new_data = True

        # ★ 새 데이터가 있을 때만 플롯 업데이트 (불필요한 렌더링 방지)
        if has_new_data:
            self.plot_widget.update_plots()

    def _update_status(self):
        """상태바 업데이트 (2Hz)"""
        # ★ 데이터 레이트 계산 (samples per second)
        current_samples = self._data_parser.sample_count
        delta = current_samples - self._last_sample_count

        # ★ 카운터 리셋 시 음수 방지 (Rate: -7794.0 Hz 버그 수정)
        if delta < 0:
            delta = 0  # 카운터가 리셋된 경우

        self._data_rate = delta * 2  # 2Hz 업데이트이므로 ×2
        self._last_sample_count = current_samples

        if self._ble_client.is_connected:
            vals = self.plot_widget.get_latest_values()
            self.status_bar.showMessage(
                f"L_GCP: {vals['l_gcp']:.1f}% | R_GCP: {vals['r_gcp']:.1f}% | "
                f"L_Force: {vals['l_force']:.1f}N | R_Force: {vals['r_force']:.1f}N | "
                f"Samples: {vals['samples']} | "
                f"Parse Errors: {self._data_parser.parse_errors}"
            )


    def closeEvent(self, event):
        """윈도우 닫기"""
        self._plot_timer.stop()
        self._status_timer.stop()
        self._ble_client.stop()
        event.accept()


def get_fixed_stylesheet() -> str:
    """고정 스타일시트 - 레이아웃 안정성 보장"""
    return """
        QMainWindow, QWidget {
            background-color: #121212;
            color: #e0e0e0;
            font-size: 12px;
        }
        QGroupBox {
            border: 1px solid #333333;
            border-radius: 6px;
            margin-top: 14px;
            padding-top: 10px;
            font-weight: bold;
            font-size: 13px;
            color: #b0b0b0;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
        }
        QPushButton {
            background-color: #1e1e1e;
            border: 1px solid #333333;
            border-radius: 4px;
            padding: 6px 12px;
            color: #e0e0e0;
            font-size: 12px;
        }
        QPushButton:hover {
            background-color: #2a2a2a;
        }
        QPushButton:pressed {
            background-color: #3a3a3a;
        }
        QPushButton:disabled {
            background-color: #1a1a1a;
            color: #555555;
        }
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            background-color: #1e1e1e;
            border: 1px solid #333333;
            border-radius: 4px;
            padding: 5px;
            color: #e0e0e0;
            font-size: 12px;
            min-height: 20px;
        }
        QComboBox::drop-down {
            border: none;
            width: 22px;
        }
        QComboBox QAbstractItemView {
            background-color: #1e1e1e;
            color: #e0e0e0;
            selection-background-color: #404040;
            font-size: 12px;
        }
        QTextEdit {
            background-color: #0d0d0d;
            border: 1px solid #333333;
            border-radius: 4px;
            color: #a0a0a0;
            font-size: 11px;
        }
        QLabel {
            color: #c0c0c0;
            font-size: 12px;
        }
        QStatusBar {
            background-color: #0d0d0d;
            color: #808080;
            border-top: 1px solid #1e1e1e;
            font-size: 11px;
        }
        QTabWidget::pane {
            border: 1px solid #333333;
            background-color: #0d0d0d;
        }
        QTabBar::tab {
            background-color: #1e1e1e;
            color: #9e9e9e;
            padding: 8px 16px;
            margin-right: 2px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            font-size: 12px;
        }
        QTabBar::tab:selected {
            background-color: #404040;
            color: #ffffff;
        }
        QTabBar::tab:hover {
            background-color: #2a2a2a;
        }
        QScrollBar:vertical {
            background-color: #1e1e1e;
            width: 10px;
        }
        QScrollBar::handle:vertical {
            background-color: #404040;
            border-radius: 5px;
        }
    """


def main():
    """메인 함수"""
    app = QApplication(sys.argv)

    # ★ Fusion 스타일 적용 (SpinBox 화살표가 모든 플랫폼에서 제대로 보임)
    app.setStyle('Fusion')

    # ★ 고정 다크 테마 스타일시트
    app.setStyleSheet(get_fixed_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
