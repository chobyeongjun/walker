"""
ARWalker BLE Client - Robust Auto-Reconnect Version

Arduino Nano 33 BLE와 통신하는 BLE 클라이언트입니다.
Nordic UART Service (NUS)를 사용합니다.

핵심 설계 원칙:
1. GUI 블로킹 없음 - 모든 BLE 작업은 별도 스레드에서 실행
2. 절대 끊기지 않는 연결 - 무한 자동 재연결 (exponential backoff)
3. Watchdog - 조용한 연결 끊김도 감지
4. 데이터 버퍼링 - 시그널 폭주 방지 (GUI 프리징 해결)
5. Proper asyncio - busy-wait 없음 (asyncio.gather 기반)
"""

import asyncio
import logging
import threading
import time
from typing import Optional
from bleak import BleakClient, BleakScanner, BleakError
from bleak.backends.device import BLEDevice
from PyQt5.QtCore import QThread, pyqtSignal, QObject
from collections import deque

logger = logging.getLogger(__name__)

# Nordic UART Service UUIDs
NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # Notify (데이터 수신)
NUS_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # Write (명령 전송)


class BleClientSignals(QObject):
    """BLE 클라이언트 시그널 - Qt 이벤트 시스템과 연결"""
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    reconnecting = pyqtSignal(int)   # 재연결 시도 횟수
    data_received = pyqtSignal(str)
    error = pyqtSignal(str)
    devices_found = pyqtSignal(list)  # List[BLEDevice]
    command_sent = pyqtSignal(str)    # 명령 전송 확인


class BleClientThread(QThread):
    """
    BLE 통신 전용 스레드 - 자동 재연결 + Watchdog

    아키텍처:
    - asyncio.gather로 3개 코루틴 동시 실행:
      1. command_processor: 명령 큐 처리 (500ms 타임아웃, busy-wait 아님)
      2. buffer_flusher: 40ms 간격으로 데이터 버퍼 플러시
      3. watchdog: 3초 간격으로 연결 상태 확인

    자동 재연결:
    - 연결이 끊기면 1초 → 5초 exponential backoff로 무한 재시도
    - 사용자가 수동으로 끊을 때만 재연결 중지
    """

    # ★★★ 안전-critical 재연결 설정 (모터 제어 불능 방지)
    RECONNECT_DELAY_INIT = 0.3    # 0.3초 후 즉시 재시도 (빠른 복구)
    RECONNECT_DELAY_MAX = 2.0     # 최대 2초 간격 (안전 우선)
    RECONNECT_BACKOFF = 1.5       # 백오프 배수

    # Watchdog 설정
    WATCHDOG_INTERVAL = 2.0       # 2초마다 연결 상태 확인 (빠른 감지)
    DATA_TIMEOUT = 5.0            # 5초 데이터 없으면 연결 끊김으로 판단

    # Heartbeat 설정 (펌웨어 watchdog 피드 + 연결 유지)
    HEARTBEAT_INTERVAL = 2.0      # 2초마다 heartbeat 전송

    # 데이터 버퍼링 설정
    DATA_BUFFER_INTERVAL_MS = 40  # 40ms = 25Hz (펌웨어 50Hz보다 낮게 유지해 배치 처리)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = BleClientSignals()
        self._client: Optional[BleakClient] = None
        self._last_device: Optional[BLEDevice] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._command_queue: Optional[asyncio.Queue] = None
        self._is_connected = False

        # 재연결 상태
        self._reconnecting = False
        self._user_disconnected = False  # 사용자가 수동으로 끊었을 때 True
        self._ever_connected = False     # ★ 한 번이라도 연결 성공해야 자동 재연결 허용

        # 데이터 버퍼링 (GUI 프리징 방지)
        self._data_buffer: deque = deque(maxlen=1000)
        self._thread_lock = threading.Lock()
        self._last_data_time = 0.0

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def run(self):
        """QThread 메인 루프"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._command_queue = asyncio.Queue()
        self._running = True

        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            logger.error(f"BLE thread fatal error: {e}")
        finally:
            self._loop.run_until_complete(self._cleanup())
            self._loop.close()

    async def _main_loop(self):
        """비동기 메인 루프 - 3개 코루틴 동시 실행 (busy-wait 없음)"""
        try:
            await asyncio.gather(
                self._command_processor(),
                self._buffer_flusher(),
                self._watchdog(),
            )
        except asyncio.CancelledError:
            pass

    async def _command_processor(self):
        """명령 큐 처리 - 500ms 타임아웃으로 적절한 블로킹"""
        while self._running:
            try:
                cmd = await asyncio.wait_for(
                    self._command_queue.get(), timeout=0.5
                )
                if cmd is None:  # 종료 센티널
                    break
                await self._process_command(cmd)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Command processor error: {e}")

    async def _buffer_flusher(self):
        """데이터 버퍼 주기적 플러시 (40ms 간격)"""
        while self._running:
            await asyncio.sleep(self.DATA_BUFFER_INTERVAL_MS / 1000.0)
            if not self._running:
                break
            self._flush_data_buffer()

    async def _watchdog(self):
        """연결 상태 감시 - 조용한 연결 끊김 감지"""
        while self._running:
            await asyncio.sleep(self.WATCHDOG_INTERVAL)
            if not self._running:
                break
            try:
                await self._check_connection_health()
            except Exception as e:
                logger.debug(f"Watchdog check error: {e}")

    async def _check_connection_health(self):
        """BLE 연결 상태 확인"""
        if not self._client or self._user_disconnected:
            return

        # is_connected == False 감지
        if self._is_connected and not self._client.is_connected:
            logger.warning("Watchdog: Connection lost (is_connected=False)")
            self._is_connected = False
            self.signals.disconnected.emit()
            if not self._reconnecting and self._ever_connected and self._last_device:
                await self._attempt_reconnect()
            return

        # 데이터 흐름 확인 (연결 상태인데 데이터가 안 오면 경고)
        if self._is_connected and self._last_data_time > 0:
            elapsed = time.monotonic() - self._last_data_time
            if elapsed > self.DATA_TIMEOUT:
                logger.warning(
                    f"Watchdog: No data for {elapsed:.1f}s - forcing reconnect"
                )
                self._is_connected = False
                try:
                    await self._force_disconnect()
                except Exception:
                    pass
                self.signals.disconnected.emit()
                if not self._reconnecting and self._ever_connected and self._last_device:
                    await self._attempt_reconnect()

    async def _cleanup(self):
        """종료 시 연결 정리"""
        if self._client:
            try:
                if self._client.is_connected:
                    await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def _process_command(self, cmd: tuple):
        """명령 처리"""
        action = cmd[0]
        try:
            if action == "scan":
                await self._scan_devices()
            elif action == "connect":
                self._user_disconnected = False
                await self._connect(cmd[1])
            elif action == "disconnect":
                self._user_disconnected = True
                self._reconnecting = False
                await self._disconnect()
            elif action == "send":
                await self._send_data(cmd[1])
        except Exception as e:
            self.signals.error.emit(f"{action} failed: {str(e)}")

    async def _scan_devices(self):
        """BLE 디바이스 스캔"""
        try:
            devices = await BleakScanner.discover(timeout=4.0)
            filtered = [d for d in devices if d.name and any(
                keyword in d.name for keyword in
                ["Nano", "Walker", "ExoBLE", "Arduino", "BLE"]
            )]
            result = filtered if filtered else [d for d in devices if d.name]
            self.signals.devices_found.emit(result)
        except Exception as e:
            self.signals.error.emit(f"Scan error: {str(e)}")
            self.signals.devices_found.emit([])

    async def _connect(self, device: BLEDevice):
        """BLE 디바이스에 연결"""
        try:
            self._last_device = device

            # 기존 클라이언트 정리
            if self._client:
                old_client = self._client
                self._client = None
                try:
                    if old_client.is_connected:
                        await old_client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(0.2)

            self._client = BleakClient(
                device.address,
                disconnected_callback=self._on_disconnect_callback
            )

            await asyncio.wait_for(self._client.connect(), timeout=10.0)

            if self._client.is_connected:
                await self._client.start_notify(NUS_TX_UUID, self._on_notify)
                self._is_connected = True
                self._ever_connected = True  # ★ 연결 성공 → 자동 재연결 허용
                self._last_data_time = time.monotonic()
                self._reconnecting = False
                self.signals.connected.emit()
                logger.info(f"Connected to {device.name}")
            else:
                raise BleakError("Connection not established")

        except Exception as e:
            if self._client:
                try:
                    if self._client.is_connected:
                        await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
            logger.error(f"Connection error: {e}")
            raise  # 재연결 루프에서 catch

    async def _disconnect(self):
        """사용자 요청에 의한 연결 해제"""
        self._is_connected = False
        self._ever_connected = False  # ★ 수동 해제 → 자동 재연결 비활성화
        try:
            if self._client:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(NUS_TX_UUID)
                    except Exception:
                        pass
                    await self._client.disconnect()
                self._client = None
        except Exception as e:
            logger.error(f"Disconnect error: {e}")
            self._client = None
        finally:
            self.signals.disconnected.emit()

    async def _force_disconnect(self):
        """강제 연결 해제 (watchdog에서 사용 - 시그널 emit 안함)"""
        try:
            if self._client:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(NUS_TX_UUID)
                    except Exception:
                        pass
                    await self._client.disconnect()
                self._client = None
        except Exception:
            self._client = None

    async def _send_data(self, data: str):
        """BLE로 명령 전송"""
        if not self._client or not self._is_connected:
            # ★ 침묵 반환 → 에러 알림 (사용자가 명령 실패를 인지해야 함)
            self.signals.error.emit("Not connected - command not sent")
            return

        try:
            await self._client.write_gatt_char(
                NUS_RX_UUID,
                data.encode('utf-8'),
                response=False
            )
            self.signals.command_sent.emit(data.strip())
        except BleakError as e:
            self.signals.error.emit(f"Send failed: {str(e)}")
            if self._client and not self._client.is_connected:
                self._is_connected = False
                self.signals.disconnected.emit()
        except Exception as e:
            self.signals.error.emit(f"Send error: {str(e)}")

    def _flush_data_buffer(self):
        """버퍼된 데이터를 한 번에 emit (GUI 프리징 방지)

        threading.Lock으로 bleak 콜백 스레드와 안전하게 동기화
        """
        with self._thread_lock:
            if self._data_buffer:
                combined_data = ''.join(self._data_buffer)
                self._data_buffer.clear()
                self.signals.data_received.emit(combined_data)

    def _on_notify(self, sender, data: bytearray):
        """Notify 콜백 - 데이터 수신 (bleak의 콜백 스레드에서 호출됨!)"""
        try:
            self._last_data_time = time.monotonic()
            text = data.decode('utf-8', errors='ignore')
            with self._thread_lock:
                self._data_buffer.append(text)
        except Exception as e:
            logger.error(f"Notify decode error: {e}")

    def _on_disconnect_callback(self, client):
        """연결 해제 콜백 - BLE 스택에서 호출됨

        이전 클라이언트의 콜백이 현재 클라이언트와 다르면 무시합니다.
        (재연결 중 이전 연결의 콜백 방지)
        """
        if client is not self._client:
            return

        logger.warning("BLE disconnected via callback")
        self._is_connected = False
        self.signals.disconnected.emit()

        # ★ 자동 재연결 조건: 사용자가 수동 해제 안 했고, 이전에 성공적으로 연결된 적 있을 때만
        if (not self._user_disconnected
                and self._ever_connected
                and self._last_device
                and self._running):
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._attempt_reconnect(),
                    self._loop
                )

    async def _attempt_reconnect(self):
        """자동 재연결 시도 (exponential backoff) - 무한 재시도

        1초부터 시작하여 최대 5초 간격으로 재시도합니다.
        사용자가 수동으로 끊거나 스레드가 종료될 때까지 계속합니다.
        """
        if self._reconnecting:
            return
        self._reconnecting = True

        delay = self.RECONNECT_DELAY_INIT
        attempt = 0

        while self._running and not self._user_disconnected:
            attempt += 1
            logger.info(f"Reconnect attempt {attempt}...")
            self.signals.reconnecting.emit(attempt)

            try:
                await self._connect(self._last_device)
                if self._client and self._client.is_connected:
                    logger.info("Reconnected successfully!")
                    self._reconnecting = False
                    return
            except Exception as e:
                logger.debug(f"Reconnect attempt {attempt} failed: {e}")

            # 대기 (0.1초 단위로 확인하여 빠른 종료 지원)
            for _ in range(int(delay * 10)):
                if not self._running or self._user_disconnected:
                    self._reconnecting = False
                    return
                await asyncio.sleep(0.1)

            delay = min(delay * self.RECONNECT_BACKOFF, self.RECONNECT_DELAY_MAX)

        self._reconnecting = False

    # === 외부 인터페이스 (메인 스레드에서 호출) ===

    def _enqueue_command(self, cmd: tuple):
        """명령 큐에 추가 (thread-safe)"""
        if self._loop and self._command_queue:
            asyncio.run_coroutine_threadsafe(
                self._command_queue.put(cmd),
                self._loop
            )

    def scan(self):
        """디바이스 스캔 요청"""
        self._enqueue_command(("scan",))

    def connect_device(self, device: BLEDevice):
        """디바이스 연결 요청"""
        self._enqueue_command(("connect", device))

    def disconnect_device(self):
        """연결 해제 요청"""
        self._enqueue_command(("disconnect",))

    def send_command(self, command: str):
        """명령 전송 - 줄바꿈 자동 추가"""
        if not command.endswith('\n'):
            command += '\n'
        self._enqueue_command(("send", command))

    def stop(self):
        """스레드 안전하게 종료"""
        self._running = False
        self._user_disconnected = True
        # 센티널 전송으로 command processor 종료
        if self._loop and self._loop.is_running() and self._command_queue:
            asyncio.run_coroutine_threadsafe(
                self._command_queue.put(None),
                self._loop
            )
        self.wait(3000)  # 최대 3초 대기
