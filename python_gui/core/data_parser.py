"""
ARWalker Data Parser - High Performance Version

BLE로부터 수신한 패킷을 파싱하여 WalkerData 객체로 변환합니다.

★ 확장된 패킷 포맷: "SW19c<d0>n<d1>n...<d18>n"
- 19개 데이터 (펌웨어 BleComm.cpp 순서와 일치):
  [0-1]  L/R GCP (%)
  [2-3]  L/R Pitch (deg)
  [4-5]  L/R GyroY (deg/s)
  [6-7]  L/R MotorPos (deg)
  [8-9]  L/R MotorVel (eRPM/100)
  [10-11] L/R MotorCurr (A)
  [12-13] L/R DesPos (deg)
  [14-15] L/R DesForce (N)
  [16-17] L/R ActForce (N)
  [18]   Mark

★★ 성능 최적화:
- 단순 문자열 버퍼 (io.StringIO 객체 생성 오버헤드 제거)
- 패킷 제한 없이 모든 가용 패킷 즉시 처리 (지연 방지)
"""

from dataclasses import dataclass
from typing import Optional, List
from collections import deque


@dataclass
class WalkerData:
    """Walker 센서/모터 데이터 구조체 (간소화)"""

    # GCP (Gait Cycle Percentage) - 0~1
    l_gcp: float = 0.0
    r_gcp: float = 0.0

    # IMU Pitch (deg) - Angle
    l_pitch: float = 0.0
    r_pitch: float = 0.0

    # Motor Position (deg)
    l_motor_pos: float = 0.0
    r_motor_pos: float = 0.0

    # Actual Force (N)
    l_act_force: float = 0.0
    r_act_force: float = 0.0

    # ★ 추가 필드
    l_gyro_y: float = 0.0
    r_gyro_y: float = 0.0
    l_motor_vel: float = 0.0
    r_motor_vel: float = 0.0
    l_motor_curr: float = 0.0
    r_motor_curr: float = 0.0
    l_des_pos: float = 0.0
    r_des_pos: float = 0.0
    l_des_force: float = 0.0
    r_des_force: float = 0.0
    mark: int = 0

    # Timestamp (sample index)
    timestamp: int = 0


class WalkerDataParser:
    """
    Walker 패킷 파서 - 고성능 버전

    ★★ 성능 최적화:
    1. 단순 문자열 버퍼: io.StringIO 객체 생성/관리 오버헤드 제거
    2. 패킷 제한 없음: 모든 가용 패킷 즉시 처리 (burst 시 지연 방지)
    3. 스로틀링은 main_window 타이머 레벨에서 관리
    """

    EXPECTED_COUNT = 19  # ★ 펌웨어가 19개 데이터 전송 (mark 포함)

    # 데이터 범위 제한 (유효 범위 밖 = 스파이크)
    VALID_RANGES = {
        'gcp': (-0.1, 1.5),
        'pitch': (-90, 90),
        'motor_pos': (-10000, 10000),
        'force': (-50, 350),
        'gyro': (-500, 500),
        'motor_vel': (-500, 500),
        'current': (-30, 30),
    }

    # 스파이크 감지
    MAX_CHANGE = {
        'gcp': 0.3,
        'pitch': 20,
        'motor_pos': 500,
        'force': 50,
        'gyro': 100,
        'motor_vel': 200,
        'current': 10,
    }

    def __init__(self):
        self._buffer = ''  # ★ 단순 문자열 버퍼 (io.StringIO 대비 오버헤드 감소)
        self._sample_count = 0
        self._parse_errors = 0
        self._max_buffer_size = 4096
        self._prev_data: WalkerData = None
        self._spike_count = 0

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def parse_errors(self) -> int:
        return self._parse_errors

    @property
    def spike_count(self) -> int:
        return self._spike_count

    def feed(self, data: str) -> List[WalkerData]:
        """
        데이터를 버퍼에 추가하고 완성된 패킷들을 반환합니다.

        ★★ 성능 최적화:
        - 단순 문자열 버퍼 (io.StringIO 객체 생성 오버헤드 제거)
        - 패킷 수 제한 없음 (모든 가용 패킷 즉시 처리 → 지연 방지)
        - 스로틀링은 main_window 레벨에서 관리
        """
        self._buffer += data

        if len(self._buffer) > self._max_buffer_size:
            self._compact_buffer()

        results = []
        parse_pos = 0
        buf = self._buffer

        while True:
            start_idx = buf.find('S', parse_pos)
            if start_idx == -1:
                parse_pos = len(buf)
                break

            if start_idx + 5 > len(buf):
                parse_pos = start_idx
                break

            if buf[start_idx + 1] != 'W':
                parse_pos = start_idx + 1
                continue

            c_idx = buf.find('c', start_idx + 2, start_idx + 10)
            if c_idx == -1:
                parse_pos = start_idx + 1
                continue

            try:
                count = int(buf[start_idx + 2:c_idx])
            except ValueError:
                self._parse_errors += 1
                parse_pos = start_idx + 1
                continue

            data_start = c_idx + 1

            remaining = buf[data_start:]
            n_count = remaining.count('n')
            if n_count < count:
                parse_pos = start_idx
                break

            values = []
            current_pos = data_start
            parse_success = True

            for _ in range(count):
                n_idx = buf.find('n', current_pos)
                if n_idx == -1:
                    parse_success = False
                    break
                try:
                    values.append(int(buf[current_pos:n_idx]) / 100.0)
                except ValueError:
                    parse_success = False
                    break
                current_pos = n_idx + 1

            if parse_success and len(values) == count:
                walker_data = self._create_walker_data(values, count)
                if walker_data:
                    self._sample_count += 1
                    walker_data.timestamp = self._sample_count
                    results.append(walker_data)
                parse_pos = current_pos
            else:
                self._parse_errors += 1
                parse_pos = start_idx + 1

        # ★ 처리된 부분만 제거 (단순 슬라이싱)
        if parse_pos > 0:
            self._buffer = self._buffer[parse_pos:]

        return results

    def _compact_buffer(self):
        """버퍼 정리 - 마지막 'S' 이후만 유지"""
        last_s = self._buffer.rfind('S')
        if last_s > 0:
            self._buffer = self._buffer[last_s:]
        elif len(self._buffer) > 512:
            self._buffer = self._buffer[-512:]

    def _validate_value(self, value: float, range_key: str) -> bool:
        """값이 유효 범위 내인지 확인"""
        if range_key not in self.VALID_RANGES:
            return True
        min_val, max_val = self.VALID_RANGES[range_key]
        return min_val <= value <= max_val

    def _check_spike(self, new_val: float, old_val: float, change_key: str) -> bool:
        """스파이크 여부 확인 (True = 스파이크 발생)"""
        if change_key not in self.MAX_CHANGE:
            return False
        return abs(new_val - old_val) > self.MAX_CHANGE[change_key]

    def _filter_gcp_value(self, new_val: float, old_val: float) -> float:
        """GCP 전용 필터 - Gait Cycle Reset 허용"""
        if not self._validate_value(new_val, 'gcp'):
            self._spike_count += 1
            return old_val if old_val is not None else 0.0

        if old_val is None:
            return new_val

        # GCP 리셋 감지: 이전값이 높고(>0.7) 새값이 낮으면(<0.3) 정상 리셋
        if old_val > 0.7 and new_val < 0.3:
            return new_val

        if self._check_spike(new_val, old_val, 'gcp'):
            self._spike_count += 1
            return old_val

        return new_val

    def _filter_value(self, new_val: float, old_val: float, range_key: str, change_key: str) -> float:
        """스파이크 필터링"""
        if not self._validate_value(new_val, range_key):
            self._spike_count += 1
            return old_val if old_val is not None else 0.0

        if old_val is not None and self._check_spike(new_val, old_val, change_key):
            self._spike_count += 1
            return old_val

        return new_val

    def _create_walker_data(self, values: List[float], count: int) -> Optional[WalkerData]:
        """값 리스트를 WalkerData 객체로 변환

        ★ 펌웨어 BleComm.cpp 데이터 순서 (19개):
        [0]  L_GCP         [1]  R_GCP
        [2]  L_Pitch       [3]  R_Pitch
        [4]  L_GyroY       [5]  R_GyroY
        [6]  L_MotorPos    [7]  R_MotorPos
        [8]  L_MotorVel    [9]  R_MotorVel  (eRPM/100으로 전송됨)
        [10] L_MotorCurr   [11] R_MotorCurr
        [12] L_DesPos      [13] R_DesPos
        [14] L_DesForce    [15] R_DesForce
        [16] L_ActForce    [17] R_ActForce
        [18] Mark
        """

        if count == 19:
            prev = self._prev_data
            if prev is None:
                filtered = WalkerData(
                    # [0-1] GCP
                    l_gcp=values[0] if self._validate_value(values[0], 'gcp') else 0.0,
                    r_gcp=values[1] if self._validate_value(values[1], 'gcp') else 0.0,
                    # [2-3] Pitch
                    l_pitch=values[2] if self._validate_value(values[2], 'pitch') else 0.0,
                    r_pitch=values[3] if self._validate_value(values[3], 'pitch') else 0.0,
                    # [4-5] GyroY
                    l_gyro_y=values[4] if self._validate_value(values[4], 'gyro') else 0.0,
                    r_gyro_y=values[5] if self._validate_value(values[5], 'gyro') else 0.0,
                    # [6-7] MotorPos
                    l_motor_pos=values[6] if self._validate_value(values[6], 'motor_pos') else 0.0,
                    r_motor_pos=values[7] if self._validate_value(values[7], 'motor_pos') else 0.0,
                    # [8-9] MotorVel (펌웨어에서 /100으로 전송되었으므로 *100 복원)
                    l_motor_vel=values[8] * 100.0 if self._validate_value(values[8], 'motor_vel') else 0.0,
                    r_motor_vel=values[9] * 100.0 if self._validate_value(values[9], 'motor_vel') else 0.0,
                    # [10-11] MotorCurr
                    l_motor_curr=values[10] if self._validate_value(values[10], 'current') else 0.0,
                    r_motor_curr=values[11] if self._validate_value(values[11], 'current') else 0.0,
                    # [12-13] DesPos
                    l_des_pos=values[12] if self._validate_value(values[12], 'motor_pos') else 0.0,
                    r_des_pos=values[13] if self._validate_value(values[13], 'motor_pos') else 0.0,
                    # [14-15] DesForce
                    l_des_force=values[14] if self._validate_value(values[14], 'force') else 0.0,
                    r_des_force=values[15] if self._validate_value(values[15], 'force') else 0.0,
                    # [16-17] ActForce
                    l_act_force=values[16] if self._validate_value(values[16], 'force') else 0.0,
                    r_act_force=values[17] if self._validate_value(values[17], 'force') else 0.0,
                    # [18] Mark
                    mark=int(values[18]) if len(values) > 18 else 0,
                )
            else:
                filtered = WalkerData(
                    # [0-1] GCP
                    l_gcp=self._filter_gcp_value(values[0], prev.l_gcp),
                    r_gcp=self._filter_gcp_value(values[1], prev.r_gcp),
                    # [2-3] Pitch
                    l_pitch=self._filter_value(values[2], prev.l_pitch, 'pitch', 'pitch'),
                    r_pitch=self._filter_value(values[3], prev.r_pitch, 'pitch', 'pitch'),
                    # [4-5] GyroY
                    l_gyro_y=self._filter_value(values[4], prev.l_gyro_y, 'gyro', 'gyro'),
                    r_gyro_y=self._filter_value(values[5], prev.r_gyro_y, 'gyro', 'gyro'),
                    # [6-7] MotorPos
                    l_motor_pos=self._filter_value(values[6], prev.l_motor_pos, 'motor_pos', 'motor_pos'),
                    r_motor_pos=self._filter_value(values[7], prev.r_motor_pos, 'motor_pos', 'motor_pos'),
                    # [8-9] MotorVel
                    l_motor_vel=self._filter_value(values[8] * 100.0, prev.l_motor_vel, 'motor_vel', 'motor_vel'),
                    r_motor_vel=self._filter_value(values[9] * 100.0, prev.r_motor_vel, 'motor_vel', 'motor_vel'),
                    # [10-11] MotorCurr
                    l_motor_curr=self._filter_value(values[10], prev.l_motor_curr, 'current', 'current'),
                    r_motor_curr=self._filter_value(values[11], prev.r_motor_curr, 'current', 'current'),
                    # [12-13] DesPos
                    l_des_pos=self._filter_value(values[12], prev.l_des_pos, 'motor_pos', 'motor_pos'),
                    r_des_pos=self._filter_value(values[13], prev.r_des_pos, 'motor_pos', 'motor_pos'),
                    # [14-15] DesForce
                    l_des_force=self._filter_value(values[14], prev.l_des_force, 'force', 'force'),
                    r_des_force=self._filter_value(values[15], prev.r_des_force, 'force', 'force'),
                    # [16-17] ActForce
                    l_act_force=self._filter_value(values[16], prev.l_act_force, 'force', 'force'),
                    r_act_force=self._filter_value(values[17], prev.r_act_force, 'force', 'force'),
                    # [18] Mark
                    mark=int(values[18]) if len(values) > 18 else prev.mark,
                )
            self._prev_data = filtered
            return filtered

        elif count == 10:
            prev = self._prev_data
            if prev is None:
                filtered = WalkerData(
                    l_gcp=values[0] if self._validate_value(values[0], 'gcp') else 0.0,
                    r_gcp=values[1] if self._validate_value(values[1], 'gcp') else 0.0,
                    l_pitch=values[2] if self._validate_value(values[2], 'pitch') else 0.0,
                    r_pitch=values[3] if self._validate_value(values[3], 'pitch') else 0.0,
                    l_motor_pos=values[4] if self._validate_value(values[4], 'motor_pos') else 0.0,
                    r_motor_pos=values[5] if self._validate_value(values[5], 'motor_pos') else 0.0,
                    l_des_force=values[6] if self._validate_value(values[6], 'force') else 0.0,
                    r_des_force=values[7] if self._validate_value(values[7], 'force') else 0.0,
                    l_act_force=values[8] if self._validate_value(values[8], 'force') else 0.0,
                    r_act_force=values[9] if self._validate_value(values[9], 'force') else 0.0,
                )
            else:
                filtered = WalkerData(
                    l_gcp=self._filter_gcp_value(values[0], prev.l_gcp),
                    r_gcp=self._filter_gcp_value(values[1], prev.r_gcp),
                    l_pitch=self._filter_value(values[2], prev.l_pitch, 'pitch', 'pitch'),
                    r_pitch=self._filter_value(values[3], prev.r_pitch, 'pitch', 'pitch'),
                    l_motor_pos=self._filter_value(values[4], prev.l_motor_pos, 'motor_pos', 'motor_pos'),
                    r_motor_pos=self._filter_value(values[5], prev.r_motor_pos, 'motor_pos', 'motor_pos'),
                    l_des_force=self._filter_value(values[6], prev.l_des_force, 'force', 'force'),
                    r_des_force=self._filter_value(values[7], prev.r_des_force, 'force', 'force'),
                    l_act_force=self._filter_value(values[8], prev.l_act_force, 'force', 'force'),
                    r_act_force=self._filter_value(values[9], prev.r_act_force, 'force', 'force'),
                )
            self._prev_data = filtered
            return filtered

        return None

    def reset(self):
        """파서 상태 초기화"""
        self._buffer = ''
        self._sample_count = 0
        self._parse_errors = 0
        self._prev_data = None
        self._spike_count = 0


class DataBuffer:
    """효율적인 실시간 데이터 버퍼 (deque 기반)"""

    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._data: deque = deque(maxlen=max_size)

    def append(self, value: float):
        self._data.append(value)

    def get_array(self):
        """numpy 배열로 반환 (플로팅용)"""
        import numpy as np
        return np.asarray(self._data, dtype=np.float32)

    def __len__(self):
        return len(self._data)

    def clear(self):
        self._data.clear()

    @property
    def last(self) -> Optional[float]:
        return self._data[-1] if self._data else None
