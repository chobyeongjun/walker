# ARWalker Python GUI - Debug & Performance Fix Log

> 작성일: 2026-03-12
> 대상 파일: python_gui/ 전체 파이프라인

---

## 개요

BLE를 통한 실시간 데이터 스트리밍 GUI에서 심각한 **렉(지연)** 문제가 발생.
데이터 파이프라인 전체를 분석하여 5개의 병목 지점을 식별하고 수정함.
추가로 BLE 자동 재연결 로직의 잘못된 동작도 수정함.

---

## 데이터 파이프라인 구조

```
Teensy 4.1 (Serial8 @ 115200)
    ↓  UART
Arduino Nano 33 BLE
    ↓  Nordic UART Service (NUS)
Python BLE Client (bleak) ─── ble_client.py
    ↓  raw_data_queue (deque)
Main Window Timer (33ms) ─── main_window.py
    ↓  feed()
Data Parser ─── data_parser.py
    ↓  WalkerData
Plot Widget ─── plot_widget.py (pyqtgraph + GCP Indicator)
```

---

## 수정 내역

### 1. [펌웨어] BLE UART 전송 차단 제거

**파일**: `Feedforward_Slack7_3PhaseAdm_HS/BleComm.cpp`

**문제**: `availableForWrite() >= len` 조건이 UART 전송을 완전히 차단하고 있었음.
Teensy 4.1의 Serial8 하드웨어 버퍼가 채워진 상태에서 이 조건이 false를 반환하면
데이터가 영원히 전송되지 않는 상황 발생.

**원인**: UART는 하드웨어 FIFO를 가지고 있어서 `write()`가 자체적으로 버퍼링 처리를 함.
`availableForWrite()` 체크는 불필요한 게이트키퍼 역할만 수행.

**수정**:
```cpp
// Before
if (BLE_SERIAL.availableForWrite() >= len) {
    BLE_SERIAL.write(buf, len);
}

// After
BLE_SERIAL.write(buf, len);
```

---

### 2. [Parser] 패킷 처리 제한 제거 (Bottleneck #2)

**파일**: `python_gui/core/data_parser.py`

**문제**: `MAX_PACKETS_PER_CALL = 10`으로 한 번의 `feed()` 호출당 최대 10개 패킷만 파싱.
50Hz 데이터 스트림에서 버스트가 발생하면 미처리 데이터가 버퍼에 계속 쌓임.

**원인**: 원래 의도는 한 번에 너무 많은 파싱을 방지하기 위한 것이었으나,
실제로는 데이터 축적 → 지연 증가의 악순환을 만듦.

**수정**: `MAX_PACKETS_PER_CALL` 제한을 완전히 제거하고, 버퍼의 모든 패킷을 한 번에 처리.

```python
# Before
packet_count = 0
while packet_count < MAX_PACKETS_PER_CALL:
    # 패킷 파싱...
    packet_count += 1

# After
while True:
    start_idx = buf.find('S', parse_pos)
    if start_idx == -1:
        break
    # 패킷 파싱 (제한 없음)
```

---

### 3. [Parser] io.StringIO → 단순 문자열 버퍼 (Bottleneck #3)

**파일**: `python_gui/core/data_parser.py`

**문제**: `io.StringIO`를 사용한 버퍼 관리에서 매 `feed()` 호출마다
`getvalue()` → 새 StringIO 생성 → `write()` 과정이 반복되어 불필요한 객체 생성 오버헤드 발생.

**원인**: StringIO는 파일-like 객체로, 단순한 문자열 연결보다 무거운 연산.
CPython의 단일 참조 문자열 최적화를 활용하면 `+=` 연산이 O(len(data))로 동작.

**수정**:
```python
# Before
self._buffer_io = io.StringIO()

def feed(self, data):
    self._buffer_io.write(data)
    buf = self._buffer_io.getvalue()
    # ... 파싱 후
    self._buffer_io = io.StringIO()
    self._buffer_io.write(remaining)

# After
self._buffer = ''

def feed(self, data):
    self._buffer += data
    buf = self._buffer
    # ... 파싱 후
    self._buffer = self._buffer[parse_pos:]
```

---

### 4. [Main Window] 처리 제한 제거 + 조건부 렌더링 (Bottleneck #1)

**파일**: `python_gui/ui/main_window.py`

**문제**:
- `MAX_PROCESS_PER_TICK = 5`: 33ms 타이머마다 큐에서 최대 5개 청크만 처리.
  BLE에서 빠르게 데이터가 들어오면 큐가 계속 쌓임.
- Parser의 `MAX_PACKETS_PER_CALL = 10`과 **이중 스로틀링** 발생:
  tick당 5 × 10 = 50패킷이 최대 → 실제 수신량 초과 시 지연 누적.
- 새 데이터가 없어도 매 tick마다 `update_plots()` 호출.

**수정**:
```python
# Before
def _process_and_update(self):
    processed = 0
    while self._raw_data_queue and processed < MAX_PROCESS_PER_TICK:
        data = self._raw_data_queue.popleft()
        results = self._data_parser.feed(data)
        for walker_data in results:
            self.plot_widget.add_data(walker_data)
        processed += 1
    self.plot_widget.update_plots()

# After
def _process_and_update(self):
    has_new_data = False
    while self._raw_data_queue:
        data = self._raw_data_queue.popleft()
        results = self._data_parser.feed(data)
        for walker_data in results:
            self.plot_widget.add_data(walker_data)
            has_new_data = True
    if has_new_data:
        self.plot_widget.update_plots()
```

**효과**: 큐가 쌓이지 않고, 데이터 없을 때 불필요한 렌더링 방지.

---

### 5. [Plot Widget] pyqtgraph autoRange 배치 최적화 (Bottleneck #4)

**파일**: `python_gui/ui/plot_widget.py`

**문제**: 각 `setData()` 호출이 `ViewBox.updateAutoRange()`를 트리거.
4개 커브가 있는 플롯에서 커브 하나 갱신 시 4개 커브 전체의 `dataBounds()` 재계산.
4개 커브 × 4번 재계산 = 16번의 `dataBounds()` 호출.

**수정**: `batch_update()` 메서드 추가. autoRange를 일시 비활성화한 후
모든 커브를 갱신하고, 마지막에 한 번만 autoRange 재활성화.

```python
# SinglePlot에 추가
def batch_update(self, updates: list):
    """updates: [(curve_name, x_data, y_data), ...]"""
    self.plot.enableAutoRange(axis='x', enable=False)
    for name, x_data, y_data in updates:
        if name in self._curves:
            self._curves[name].setData(x_data, y_data)
    self.plot.enableAutoRange(axis='x', enable=True)
```

**효과**: 16번 → 4번으로 `dataBounds()` 호출 75% 감소.

---

### 6. [Plot Widget] GCP Indicator 리페인트 최소화 (Bottleneck #5)

**파일**: `python_gui/ui/plot_widget.py`

**문제**: GCP 값이 매 프레임(~30fps)마다 `set_value()` → `update()` → `paintEvent()` 호출.
QPainter로 그래디언트 바를 매번 다시 그리는 것은 비용이 큼.

**수정**: 값 변화가 0.5% 미만이면 리페인트를 건너뛰는 임계값 적용.

```python
def set_value(self, value: float):
    if value > 1:
        value = value / 100.0
    new_value = max(0.0, min(1.0, value))
    if abs(new_value - self._value) < 0.005:  # 0.5% 임계값
        return  # 리페인트 건너뛰기
    self._value = new_value
    self.update()
```

**효과**: ~30 QPaint/sec → 의미있는 변화가 있을 때만 → ~10 QPaint/sec.

---

### 7. [Plot Widget] X축 Auto-Range 활성화

**파일**: `python_gui/ui/plot_widget.py`

**문제**: X축이 고정 윈도우 방식으로 설정되어 있어 실시간 데이터 위치를 볼 수 없었음.

**수정**: `enableAutoRange(axis='x', enable=True)`로 변경하여 실시간 스크롤 구현.
수동 `setXRange()` 로직과 `active_plot` 추적 코드 제거.

---

### 8. [BLE Client] 자동 재연결 로직 수정

**파일**: `python_gui/core/ble_client.py`

**문제**: BLE 스캔만 하고 연결하지 않은 상태에서도 자동 재연결이 계속 시도됨.
`_on_disconnect_callback`과 `_check_connection_health`가 한 번도 연결 성공한 적 없는
디바이스에 대해서도 재연결을 시도.

**수정**: `_ever_connected` 플래그 추가.

```python
# __init__에 추가
self._ever_connected = False

# _connect() 성공 시
self._ever_connected = True

# _disconnect() (수동 해제) 시
self._ever_connected = False

# _on_disconnect_callback에서 체크
if (not self._user_disconnected
        and self._ever_connected  # ← 추가
        and self._last_device
        and self._running):
    asyncio.ensure_future(self._attempt_reconnect())

# _check_connection_health에서 체크
if not self._reconnecting and self._ever_connected and self._last_device:
    await self._attempt_reconnect()
```

**효과**: 수동으로 CONNECT 버튼을 눌러 한 번 성공적으로 연결된 이후에만
자동 재연결이 활성화됨.

---

## 성능 개선 요약

| 항목 | Before | After |
|------|--------|-------|
| 파서 패킷 제한 | 10개/call | 무제한 |
| 메인 루프 처리 제한 | 5 청크/tick | 무제한 |
| 버퍼 방식 | io.StringIO (객체 생성) | 단순 문자열 (in-place) |
| autoRange 계산 | 16 dataBounds/update | 4 dataBounds/update |
| GCP 리페인트 | ~30 QPaint/sec | ~10 QPaint/sec |
| 빈 프레임 렌더링 | 매 tick | 데이터 있을 때만 |
| 자동 재연결 | 스캔만 해도 시도 | 연결 성공 후에만 |

---

## 참고: 이전 세션에서 변경된 설정값

| 설정 | 원래 값 | 변경 값 | 파일 |
|------|---------|---------|------|
| BUFFER_SIZE | 500 | 150 | plot_widget.py |
| PLOT_UPDATE_INTERVAL_MS | 40 | 33 | main_window.py |
| MAX_PROCESS_PER_TICK | 3→5 | 제거 | main_window.py |
