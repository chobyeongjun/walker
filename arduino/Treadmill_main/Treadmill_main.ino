/*
 * ================================================================
 *  AK60 + GCP Integrated Firmware
 *  ★★★ Slack Method 7: 3-Phase Admittance (HS 기준, Custom Thresholds) ★★★
 * ================================================================
 *
 *  ★ 제어 모드
 *   - MODE_FORCE_ASSIST (0): HS 기준 GCP + Onset-Peak-Release Sine
 *   - MODE_POSITION_ASSIST (1): HS 기준 GCP + 1/4 Sine Cascade PID
 *
 *  ★ GCP 동작 방식
 *   - Mode 0 (Force): HS에서 GCP 0% 시작 → HO에서 100%
 *   - Mode 1 (Position): HS에서 GCP 0% 시작 → HO에서 100%
 *
 *  ★ 보조 프로파일
 *   - Force Assist: Onset→Peak (Rising Half Sine), Peak→Release (Falling Half Sine)
 *   - Position Assist: Start→End (1/4 Sine) 위치 오프셋
 *
 *  ★ Safety
 *   - 양쪽 교대(Alternation) 체크: HO/HS 모두 좌우 번갈아 발생
 *   - 위치 제한: 초기 위치에서 ±4500도 이상 이동 시 정지
 *
 *  ★ 제어 주기
 *   - Position Loop: 9ms (111Hz) - Position PID
 *   - Velocity Loop: 3ms (333Hz) - Velocity PID
 *   - Current Command: 1ms (1kHz) - CAN 전송
 *   - Logging: 9ms
 *
 * ================================================================
 */

#if defined(ARDUINO_TEENSY36) || defined(ARDUINO_TEENSY41)

#include <Arduino.h>
#include <FlexCAN_T4.h>
#include <SD.h>
#include <SPI.h>
#include <IntervalTimer.h>
#include <math.h>
#include <string.h>
#include <algorithm>

// BLE Communication Module
#include "BleComm.h"

// ################################################################
// ##                                                            ##
// ##  SECTION A: 타입 정의 (Enums / Structs / Classes)          ##
// ##                                                            ##
// ################################################################

// ================================================================
// [A-1] 기본 Enums
// ================================================================

enum Side : uint8_t {
  SIDE_LEFT = 0,
  SIDE_RIGHT = 1,
  SIDE_COUNT = 2
};

// ★ 제어 모드 enum
enum ControlMode : uint8_t {
  MODE_FORCE_ASSIST = 0,    // HS 기준 GCP + Onset-Peak-Release Sine
  MODE_POSITION_ASSIST = 1  // HS 기준 GCP + 1/4 Sine Cascade PID
};

// ★ GCP 시작점 모드 (GaitDetector 내부용)
enum GCPStartMode : uint8_t {
  GCP_START_AT_HO = 0,  // HO 기준 (미사용)
  GCP_START_AT_HS = 1   // ★ Slack7: HS에서 GCP 0% 시작
};

// ================================================================
// [A-2] 전방 선언
// ================================================================

struct VelocityPID;
struct PositionPID;
class LowPassFilter;
struct IMUSnapshot;
class GaitDetector;
class IMU;

// ★★★ ISR에서 업데이트하는 현재 시간 (millis 대체) ★★★
// ISR 내부에서 millis()는 업데이트되지 않으므로, ISR 시작 시 이 값을 갱신
volatile uint32_t isr_now_ms = 0;

inline bool isLeftMotor(uint8_t id);
inline uint8_t sideToCanId(Side s);
inline int sideToLoadcellPin(Side s);
inline float sideToLoadcellBias(Side s);
inline float sideToLoadcellSensitive(Side s);

// ################################################################
// ##                                                            ##
// ##  SECTION B: 시스템 설정 상수                                ##
// ##                                                            ##
// ################################################################

// ================================================================
// [B-1] 통신 설정 (Serial / CAN / SD)
// ================================================================

#define IMU_SERIAL Serial1
const int SDCARD_CS_PIN = BUILTIN_SDCARD;

FlexCAN_T4<CAN1, RX_SIZE_256, TX_SIZE_16> can1;

#define MOTOR_LEFT_CAN_ID 65
#define MOTOR_RIGHT_CAN_ID 33
#define CAN_PACKET_SET_CURRENT 1
#define CAN_PACKET_SET_ORIGIN 5

// ================================================================
// [B-2] 타이밍 설정
// ================================================================

const uint32_t CONTROL_PERIOD_MS = 3;  // Velocity Loop: 3ms (333Hz)
const uint32_t CONTROL_PERIOD_US = CONTROL_PERIOD_MS * 1000UL;
const uint32_t CURRENT_PERIOD_US = 1000;  // Current Command: 1ms (1kHz)

const uint32_t POSITION_PERIOD_MS = 9;  // Position Loop: 9ms (111Hz)
const uint32_t LOG_PERIOD_MS = 9;       // Logging: 9ms
const uint32_t DEBUG_PERIOD_MS = 9;     // Debug print: 9ms

// 로깅/Position 주기 카운터 분주비
const uint8_t LOG_TICK_DIVISOR = LOG_PERIOD_MS / CONTROL_PERIOD_MS;       // 3
const uint8_t POS_TICK_DIVISOR = POSITION_PERIOD_MS / CONTROL_PERIOD_MS;  // 3

// ================================================================
// [B-3] 하드웨어 핀 설정
// ================================================================

// Analog trigger (A7 = pin21 on Teensy 4.1)
const int ANALOG_PIN = 21;
const int TRIGGER_THRESHOLD = 2000;

// Loadcell 핀
#define LEFT_LOADCELL_PIN A16
#define RIGHT_LOADCELL_PIN A6

// ================================================================
// [B-4] 물리/변환 상수 (모터/기구)
// ================================================================

#define KT_NM_PER_A 0.078f
#define POLE_PAIRS 14.0f
#define GEAR_RATIO 6.0f
#define SPOOL_RADIUS_M 0.04f
#define ETA_MECH 0.869231f

const float AI_CNT_TO_V = 3.3f / 4096.0f;

// Loadcell 보정값
const float left_knee_bias = -679.2363f;
const float left_knee_sensitive = 550.3382f;
const float right_knee_bias = -687.1577f;
const float right_knee_sensitive = 560.9190f;

// ================================================================
// [B-5] 제한값/Safety 상수
// ================================================================

#define MAX_CURRENT_A 20.0f
#define MAX_VEL_ERPM 47000.0f
#define POSITION_LIMIT_DEG 4500.0f

// ★★ Cable Tangle Safety: Payout 방향 회전 + 고힘 → 케이블 꼬임 감지
#define SAFETY_PAYOUT_FORCE_N 300.0f    // Payout 중 최대 허용 힘 [N]
#define SAFETY_PAYOUT_ERPM    500.0f   // Payout 판별 ERPM (음의 방향 기준)
#define SAFETY_TANGLE_TICKS   3        // 연속 확인 횟수 (3 ticks = 9ms @333Hz)


// ################################################################
// ##                                                            ##
// ##  SECTION C: 사용자 파라미터 (런타임 조정 가능)              ##
// ##                                                            ##
// ################################################################

// ================================================================
// [C-1] 현재 제어 모드
// ================================================================

volatile ControlMode currentMode = MODE_FORCE_ASSIST;

// ================================================================
// [C-2] Force Assist Mode 파라미터 (HS 기준 GCP)
// ================================================================

// ★ Onset-Peak-Release 3점 프로파일 (HS 기준)
volatile float GCP_FORCE_ONSET = 0.60f;    // 힘 시작점 (HS 기준 55%)
volatile float GCP_FORCE_PEAK = 0.75f;     // 최대 힘 지점 (75%)
volatile float GCP_FORCE_RELEASE = 0.85f;  // 힘 종료점 (85%)
volatile float PEAK_FORCE_N = 50.0f;       // 최대 보조력 [N]
volatile float MIN_TENSION_N = 5.0f;        // ★ 최소 케이블 장력 [N] - 항상 유지

// ★ Slack Zone Deadband - 삭제됨 (Pretension 5N이 항상 유지되므로 불필요)

// ★ Dual Admittance 파라미터
// Assist Zone - Rising (ONSET ~ PEAK): 상승 구간 추종
volatile float adm_M_assist = 1.0f;            // 가상 질량 [kg]
volatile float adm_C_assist = 4.0f;            // 가상 감쇠 [N*s/m]
volatile float MAX_ADM_VEL_ASSIST = 2.1f;      // 속도 제한 [m/s]
volatile float MAX_ADM_ACCEL_ASSIST = 30.00f;  // 가속도 제한 [m/s²]

// ★★ Falling Zone (PEAK ~ RELEASE): 하강 구간 빠른 추종
// τ = M/C = 1.0/5.0 = 200ms (하강 구간 ~250ms 내 추종 가능)
volatile float adm_M_falling = 1.0f;           // 더 낮은 질량 → 빠른 응답
volatile float adm_C_falling = 4.0f;           // 적절한 감쇠
volatile float MAX_ADM_VEL_FALLING = 2.1f;     // 더 빠른 풀림 속도 [m/s]
volatile float MAX_ADM_ACCEL_FALLING = 50.0f;  // 가속도 제한 [m/s²]

// Slack Zone (Pretension 구간): 5N 유지하며 부드럽게 다리 따라가기
volatile float adm_M_slack = 1.0f;           // 가상 질량 [kg]
volatile float adm_C_slack = 4.0f;           // 가상 감쇠 [N*s/m] - 높을수록 부드러움
volatile float MAX_ADM_VEL_SLACK = 2.1f;     // 속도 제한 [m/s]
volatile float MAX_ADM_ACCEL_SLACK = 50.0f;  // 가속도 제한 [m/s²] - 급격한 변화 방지

// ★★ Treadmill 보상: HS→(ONSET - TFF_END_OFFSET) full sine
// - treadmill belt 속도 [m/s] (예: 1.25 = 1.25m/s)
// - TFF_GAIN: BLE 'tg' 커맨드로 수동 조절 (고정값)
// - BLE 커맨드: 'tm' 벨트 속도, 'tg' TFF 게인, 'te' TFF 종료 오프셋
volatile float treadmill_speed_mps = 1.25f;  // [m/s] Treadmill belt speed (0 = off)
volatile float TFF_GAIN = 0.8f;              // [0~1] 케이블 각도 보정 게인 (cos(θ) ≈ 0.5~0.8)
volatile float TFF_END_OFFSET = 0.10f;       // [GCP %] ONSET 기준 몇 % 전에서 TFF 종료 (BLE: 'te')

// ★★ TFF_GAIN: BLE에서 수동 조절하는 고정값 (Adaptive 제거됨)

// ★★ Feedforward 파라미터: Desired Force에 비례하는 속도
volatile float FF_GAIN_F = 0.15f;  // [m/s per N] F_cmd → 속도 변환 게인

// ★★★ Motion Feedforward: Global Velocity (X,Y) norm으로 케이블 슬랙 보상 ★★★
// - EBIMU soa5 설정: acc_x, acc_y 자리에 Global Velocity (m/s) 출력
// - 다리 움직임 속도 → 케이블 길이 변화 → Feedforward로 보상
// - Force Profile 구간 (ONSET ~ RELEASE)에서만 적용
// v_motion = FF_GAIN_MOTION * sqrt(vel_x² + vel_y²)
volatile float FF_GAIN_MOTION = 0.7f;  // [무차원] velocity norm → 모터 속도 변환 게인

// Position Mode용 Admittance (Force Control 구간에서 사용)
volatile float adm_M = 1.0f;  // 가상 질량 [kg]
volatile float adm_C = 4.0f;  // 가상 감쇠 [N*s/m]
#define MAX_ADM_VELOCITY_MPS 2.10f
#define MAX_ADM_ACCEL_MPS2 50.00f


// ================================================================
// [C-3] Position Assist Mode 파라미터 (HS 기준 GCP)
// ================================================================

// ★ 1/4 Sine 위치 프로파일 구간
volatile float GCP_POS_START = 0.10f;            // 위치 보조 시작
volatile float GCP_POS_END = 0.70f;              // 위치 보조 종료
volatile float POSITION_AMPLITUDE_DEG = 600.0f;  // 최대 위치 변화량 [도]
volatile float MAX_POS_VEL_ERPM = 42000.0f;      // Position PID 출력 속도 제한

// ################################################################
// ##                                                            ##
// ##  SECTION D: 유틸리티 함수                                   ##
// ##                                                            ##
// ################################################################

// ================================================================
// [D-1] 수학 유틸리티
// ================================================================

inline float clampf(float val, float mn, float mx) {
  if (val < mn) return mn;
  if (val > mx) return mx;
  return val;
}

inline float deg_to_rad(float deg) {
  return deg * 3.14159f / 180.0f;
}

inline float rad_to_deg(float rad) {
  return rad * 180.0f / 3.14159f;
}

// ================================================================
// [D-2] 단위 변환 유틸리티
// ================================================================

inline float deg_to_cable_m(float deg) {
  return deg_to_rad(deg) * SPOOL_RADIUS_M;
}

inline float cable_m_to_deg(float m) {
  return rad_to_deg(m / SPOOL_RADIUS_M);
}

inline float erpm_to_mps(float erpm) {
  float omega_mech = (erpm / POLE_PAIRS) * (2.0f * 3.14159f / 60.0f);
  float omega_out = omega_mech / GEAR_RATIO;
  return omega_out * SPOOL_RADIUS_M;
}

inline float mps_to_erpm(float mps) {
  float omega_out = mps / SPOOL_RADIUS_M;
  float omega_mech = omega_out * GEAR_RATIO;
  float mech_rpm = omega_mech * (60.0f / (2.0f * 3.14159f));
  return mech_rpm * POLE_PAIRS;
}

// ================================================================
// [D-3] Side 헬퍼 함수
// ================================================================

inline bool isLeftMotor(uint8_t id) {
  return (id == MOTOR_LEFT_CAN_ID);
}

inline uint8_t sideToCanId(Side s) {
  return (s == SIDE_LEFT) ? MOTOR_LEFT_CAN_ID : MOTOR_RIGHT_CAN_ID;
}

inline int sideToLoadcellPin(Side s) {
  return (s == SIDE_LEFT) ? LEFT_LOADCELL_PIN : RIGHT_LOADCELL_PIN;
}

inline float sideToLoadcellBias(Side s) {
  return (s == SIDE_LEFT) ? left_knee_bias : right_knee_bias;
}

inline float sideToLoadcellSensitive(Side s) {
  return (s == SIDE_LEFT) ? left_knee_sensitive : right_knee_sensitive;
}

// ################################################################
// ##                                                            ##
// ##  SECTION E: PID 컨트롤러                                    ##
// ##                                                            ##
// ################################################################

// ================================================================
// [E-1] Velocity PID (333Hz)
// ================================================================

struct VelocityPID {
  float kp = 0.0011f;
  float ki = 0.0000005f;
  float kd = 0.000003f;

  float integral = 0;
  float prev_error = 0;

  float p_term = 0;
  float i_term = 0;
  float d_term = 0;
  float output = 0;

  const float integral_limit = 1000.0f;

  float compute(float setpoint_erpm, float measured_erpm, float dt) {
    float error = setpoint_erpm - measured_erpm;

    p_term = kp * error;

    if (fabs(error) <= 10000.0f) {
      integral += error * dt;
      integral = clampf(integral, -integral_limit, integral_limit);
    } else {
      integral = 0;
    }
    i_term = ki * integral;

    if (dt > 0) d_term = kd * (error - prev_error) / dt;

    output = p_term + i_term + d_term;
    output = clampf(output, -MAX_CURRENT_A, MAX_CURRENT_A);

    prev_error = error;
    return output;
  }

  void reset() {
    integral = 0;
    prev_error = 0;
    p_term = i_term = d_term = 0;
    output = 0;
  }
};

VelocityPID velocityPID_L;
VelocityPID velocityPID_R;

inline VelocityPID& pidOf(Side s) {
  return (s == SIDE_LEFT) ? velocityPID_L : velocityPID_R;
}

// ================================================================
// [E-2] Position PID (111Hz)
// ================================================================

struct PositionPID {
  float kp = 300.0f;
  float ki = 500.0f;
  float kd = 0.0f;

  float integral = 0;
  float prev_error = 0;

  float p_term = 0;
  float i_term = 0;
  float d_term = 0;
  float output = 0;

  const float integral_limit = 100.0f;
  float output_limit_degps = 47000.0f;

  float compute(float setpoint_deg, float measured_deg, float dt) {
    float error = setpoint_deg - measured_deg;

    p_term = kp * error;

    integral += error * dt;
    integral = clampf(integral, -integral_limit, integral_limit);
    i_term = ki * integral;

    if (dt > 0) d_term = kd * (error - prev_error) / dt;

    output = p_term + i_term + d_term;
    output = clampf(output, -output_limit_degps, output_limit_degps);

    prev_error = error;
    return output;
  }

  void reset() {
    integral = 0;
    prev_error = 0;
    p_term = i_term = d_term = 0;
    output = 0;
  }
};

PositionPID positionPID_L;
PositionPID positionPID_R;

inline PositionPID& posPidOf(Side s) {
  return (s == SIDE_LEFT) ? positionPID_L : positionPID_R;
}

// ################################################################
// ##                                                            ##
// ##  SECTION F: 필터                                            ##
// ##                                                            ##
// ################################################################

// ================================================================
// [F-1] Low Pass Filter
// ================================================================

class LowPassFilter {
  float alpha;
  float y_prev;

public:
  LowPassFilter(float cutoff, float fs) {
    float dt = 1.0f / fs;
    float rc = 1.0f / (2.0f * 3.14159f * cutoff);
    alpha = dt / (rc + dt);
    y_prev = 0;
  }

  float update(float x) {
    y_prev = y_prev + alpha * (x - y_prev);
    return y_prev;
  }

  void reconfigure(float cutoff, float fs) {
    float dt = 1.0f / fs;
    float rc = 1.0f / (2.0f * 3.14159f * cutoff);
    alpha = dt / (rc + dt);
  }
};

const float CONTROL_FS = 1000.0f / (float)CONTROL_PERIOD_MS;
LowPassFilter loadcellFilter_L(50.0f, CONTROL_FS);
LowPassFilter loadcellFilter_R(50.0f, CONTROL_FS);

inline LowPassFilter& loadcellLPFOf(Side s) {
  return (s == SIDE_LEFT) ? loadcellFilter_L : loadcellFilter_R;
}

// ################################################################
// ##                                                            ##
// ##  SECTION G: 보행 감지 (GaitDetector)                        ##
// ##                                                            ##
// ################################################################

// ================================================================
// [G-1] GaitDetector 클래스 - 통합 버전
// ================================================================

class GaitDetector {
public:
  enum GaitEvent {
    EVENT_NONE = 0,
    EVENT_HO = 1,
    EVENT_HS = 2
  };

  enum Phase {
    STANCE,
    SWING,
    LOADING
  };

  // ★ GCP 시작점 모드 설정 (외부에서 currentMode에 따라 설정)
  GCPStartMode gcpStartMode = GCP_START_AT_HS;

  // 교대 제어용 플래그
  bool allowHO = true;
  bool allowHS = true;

  // Warmup 카운터
  int hsCount = 0;
  const int HS_WARMUP_COUNT = 2;

  GaitDetector() {
    reset();
  }

  void reset() {
    phase = STANCE;
    hsCount = 0;

    HO_Angle_Thresh = 2.6f;
    HO_Angvel_Thresh = 40.0f;
    HS_Ratio = 0.072f;
    Swing_Arming_Ratio = 0.25f;

    Angle_Up_Thresh = 15.0f;
    Angle_Down_Thresh = 10.0f;

    ref_angle_peak = 40.0f;
    ref_vel_peak = 100.0f;

    STABILITY_FALLBACK_VAL = 5.0f;

    curr_pushoff_max_ang = -999.0f;
    curr_swing_max_vel = 0.0f;

    angle_gate_up = false;
    angle_return_met = false;

    has_HS = false;
    ready_for_HO = true;

    stride_invalid = false;
    gcp_active = false;
    timedOut = false;  // ★ Timeout 플래그 초기화

    last_event_time = 0;
    ho_timestamp = 0;
    hs_timestamp = 0;

    avg_step_time = 1.3f;
    step_idx = 0;
    step_cnt = 0;
    for (int i = 0; i < 3; i++) step_buffer[i] = 0.0f;

    current_gcp = 0.0f;
    ho_gcp_in_cycle = -1.0f;  // ★ -1 = 아직 HO 미발생
    prev_ho_gcp = 0.4f;       // ★ 이전 HO GCP 초기값 (보행주기의 ~40%)
    hs_gcp_in_cycle = -1.0f;  // ★ -1 = 아직 HS 미발생

    const float dt = 0.003f;
    const float fc = 9.4f;
    const float rc = 1.0f / (2.0f * 3.1415926f * fc);
    lpf_alpha = dt / (rc + dt);

    lpf_initialized = false;
    lpf_prev_val = 0.0f;

    ctrl_period_ms = CONTROL_PERIOD_MS;
    last_ctrl_ms = 0;
    last_HO_time = 0;
  }

  // Getters
  // ★★★ 실시간 GCP 계산: ISR에서 업데이트한 isr_now_ms 기준 ★★★
  // (ISR 내부에서 millis()는 업데이트되지 않으므로 isr_now_ms 사용)
  float getGCP() const {
    if (!gcp_active) return 0.0f;

    unsigned long ref_timestamp = (gcpStartMode == GCP_START_AT_HS) ? hs_timestamp : ho_timestamp;
    if (ref_timestamp == 0) return 0.0f;

    // ★ isr_now_ms: ISR 시작 시 micros()/1000으로 갱신됨
    float elapsed_sec = (isr_now_ms - ref_timestamp) / 1000.0f;
    if (elapsed_sec > 4.0f) return 0.0f;  // Timeout

    return elapsed_sec / avg_step_time;
  }
  float getAvgStepTime() const {
    return avg_step_time;
  }
  float getFilteredVel() const {
    return last_filt_vel;
  }
  bool isLoading() const {
    return phase == LOADING;
  }
  bool isSwinging() const {
    return (phase == SWING);
  }
  // ★★★ GCP 활성 여부 확인 (Pretension vs Linear Ramp 구분용) ★★★
  bool isGcpActive() const {
    return gcp_active;
  }
  bool isHsInCycle() const {
    return hs_in_cycle;
  }
  Phase getPhase() const {
    return phase;
  }

  uint8_t getPhaseValue() const {
    switch (phase) {
      case STANCE: return 0;
      case SWING: return 1;
      case LOADING: return 2;
      default: return 0;
    }
  }

  // ★ HO 발생 시점의 GCP (HS-HS cycle 내부에서 HO가 언제 발생했는지)
  float getHoGcpInCycle() const { return ho_gcp_in_cycle; }

  // ★ 이전 사이클의 HO GCP (sine TFF에서 HO 발생 전 예측용)
  float getPrevHoGcp() const { return prev_ho_gcp; }

  // ★ HS 발생 시점의 GCP (HO-HO cycle 내부에서 HS가 언제 발생했는지)
  float getHsGcpInCycle() const { return hs_gcp_in_cycle; }

  // ★ Timeout 플래그 Getter/Setter
  bool hasTimedOut() const { return timedOut; }
  void clearTimedOut() { timedOut = false; }

  void setContralateral(GaitDetector* contra) {
    contralateral = contra;
  }

  bool isContralateralSwinging() const {
    if (contralateral == nullptr) return false;
    return contralateral->isSwinging();
  }

  // ★ 메인 업데이트 함수 (GCP 모드에 따라 분기)
  GaitEvent update(float angle_deg, float gyro_dps) {
    const uint32_t now_ms = (uint32_t)millis();

    const float vel = applyLPF(gyro_dps);
    last_filt_vel = vel;

    // ★★★ GCP 계산: 모드에 따라 다른 타임스탬프 사용 ★★★
    if (gcp_active) {
      unsigned long ref_timestamp = (gcpStartMode == GCP_START_AT_HS) ? hs_timestamp : ho_timestamp;

      if (ref_timestamp > 0) {
        const float elapsed_sec = (now_ms - ref_timestamp) / 1000.0f;

        // Timeout 체크 (4초 이상)
        if (elapsed_sec > 4.0f) {
          gcp_active = false;
          current_gcp = 0.0f;
          ho_timestamp = 0;
          hs_timestamp = 0;
          timedOut = true;  // ★ Timeout 플래그 설정 (main에서 firstHSDone 리셋)
          Serial.println("⚠️ GCP timeout (>4s) - reset, any foot can start");
        } else {
          current_gcp = elapsed_sec / avg_step_time;
        }
      }
    }

    // 제어 주기 체크
    if (last_ctrl_ms != 0 && (uint32_t)(now_ms - last_ctrl_ms) < ctrl_period_ms)
      return EVENT_NONE;
    last_ctrl_ms = now_ms;

    // 이벤트 최소 간격
    if (now_ms - last_event_time < 50) return EVENT_NONE;

    GaitEvent evt = EVENT_NONE;

    switch (phase) {
      case STANCE:
        // ═══════════════════════════════════════════════════════
        // STANCE → SWING (Heel Off 감지)
        // ═══════════════════════════════════════════════════════
        if (ready_for_HO && allowHO && (now_ms - last_HO_time) >= MIN_HO_INTERVAL_MS && angle_deg >= HO_Angle_Thresh && gyro_dps >= HO_Angvel_Thresh) {

          last_HO_time = now_ms;
          ho_gcp_in_cycle = current_gcp;  // ★ HO 발생 시점의 GCP 기록
          evt = EVENT_HO;
          phase = SWING;
          ready_for_HO = false;
          has_HS = false;
          stride_invalid = false;
          curr_pushoff_max_ang = angle_deg;
          curr_swing_max_vel = 0.0f;
          angle_gate_up = false;
          angle_return_met = false;
          last_event_time = now_ms;

          // ★ HO 기준 모드: HO에서 GCP 시작 (현재 미사용)
          if (gcpStartMode == GCP_START_AT_HO) {
            updateStepTime(now_ms);
            ho_timestamp = now_ms;
            current_gcp = 0.0f;
            gcp_active = true;
            hs_in_cycle = false;
            hs_gcp_in_cycle = -1.0f;
          }
        }
        break;

      case SWING:
        // ═══════════════════════════════════════════════════════
        // SWING 상태 업데이트
        // ═══════════════════════════════════════════════════════
        if (angle_deg > curr_pushoff_max_ang) curr_pushoff_max_ang = angle_deg;
        if (vel > curr_swing_max_vel) curr_swing_max_vel = vel;

        if (!angle_gate_up && angle_deg >= Angle_Up_Thresh) {
          angle_gate_up = true;
        }

        if (angle_gate_up && angle_deg <= Angle_Down_Thresh) {
          phase = LOADING;
          ref_angle_peak = 0.7f * ref_angle_peak + 0.3f * curr_pushoff_max_ang;
          angle_return_met = true;
        }
        break;

      case LOADING:
        // ═══════════════════════════════════════════════════════
        // LOADING → STANCE (Heel Strike 감지)
        // ═══════════════════════════════════════════════════════
        if (!has_HS) {
          const float threshold = ((curr_swing_max_vel * HS_Ratio));
          bool cond_vel = (vel < threshold);
          float norm = sqrtf(angle_deg * angle_deg + vel * vel);
          bool cond_stability = (norm < STABILITY_FALLBACK_VAL);
          if (cond_vel || cond_stability) {
            if (cond_stability && !cond_vel) stride_invalid = true;

            // ★ 교대 체크: allowHS가 false면 HS 무시
            if (allowHS) {
              has_HS = true;
              evt = EVENT_HS;

              // Step time 업데이트
              updateStepTime(now_ms);
              hsCount++;

              // ★ HS 기준 모드: HS에서 GCP 시작
              if (gcpStartMode == GCP_START_AT_HS) {
                hs_timestamp = now_ms;
                current_gcp = 0.0f;
                gcp_active = true;
                // ★ 이전 사이클의 HO GCP 저장 (sine TFF 예측용)
                if (ho_gcp_in_cycle > 0.0f) {
                  prev_ho_gcp = ho_gcp_in_cycle;
                }
                ho_gcp_in_cycle = -1.0f;  // ★ 새 사이클 → HO 미발생으로 리셋
              }
              // ★ HO 기준 모드 (현재 미사용): HS에서는 플래그 + GCP 기록
              else {
                hs_in_cycle = true;
                hs_gcp_in_cycle = current_gcp;
              }

              ref_vel_peak = 0.0f * ref_vel_peak + 1.0f * curr_swing_max_vel;
              last_event_time = now_ms;

              phase = STANCE;
              ready_for_HO = true;
              curr_swing_max_vel = 0.0f;
            }
          }
        }
        break;
    }

    return evt;
  }

private:
  float HO_Angle_Thresh, HO_Angvel_Thresh, HS_Ratio, Swing_Arming_Ratio;
  float Angle_Up_Thresh, Angle_Down_Thresh;
  float ref_angle_peak, ref_vel_peak;
  float STABILITY_FALLBACK_VAL;
  Phase phase;
  float curr_pushoff_max_ang, curr_swing_max_vel;
  bool angle_gate_up, angle_return_met;
  bool has_HS, ready_for_HO;
  bool stride_invalid;
  bool gcp_active;
  bool timedOut;  // ★ Timeout 발생 플래그 (main에서 firstHSDone 리셋용)
  bool hs_in_cycle;  // ★ 현재 GCP 사이클에서 HS 발생 여부 (HO기준 GCP용)

  unsigned long last_event_time;
  uint32_t ctrl_period_ms = CONTROL_PERIOD_MS;
  uint32_t last_ctrl_ms = 0;
  unsigned long ho_timestamp, hs_timestamp;

  float avg_step_time;
  float step_buffer[3];
  int step_idx, step_cnt;
  float current_gcp;
  float ho_gcp_in_cycle;  // ★ HO 발생 시점의 GCP (HS-HS 사이클 내)
  float prev_ho_gcp;      // ★ 이전 사이클의 HO GCP (sine TFF 예측용)
  float hs_gcp_in_cycle;  // ★ HS 발생 시점의 GCP (HO-HO 사이클 내)
  float lpf_alpha;
  bool lpf_initialized;
  float lpf_prev_val;
  float last_filt_vel = 0.0f;

  GaitDetector* contralateral = nullptr;
  uint32_t last_HO_time = 0;
  const uint32_t MIN_HO_INTERVAL_MS = 300;

  float applyLPF(float raw) {
    float y;
    if (!lpf_initialized) {
      lpf_prev_val = raw;
      lpf_initialized = true;
      y = raw;
    } else {
      y = lpf_alpha * raw + (1.0f - lpf_alpha) * lpf_prev_val;
      lpf_prev_val = y;
    }
    return y;
  }

  // ★ Step time 업데이트 (모드에 따라 다른 타임스탬프 사용)
  void updateStepTime(unsigned long now_ms) {
    unsigned long ref_timestamp;

    if (gcpStartMode == GCP_START_AT_HS) {
      // HS 기준 (Slack7): HS→HS 시간
      ref_timestamp = hs_timestamp;
    } else {
      // HO 기준: HO→HO 시간
      ref_timestamp = ho_timestamp;
    }

    if (ref_timestamp == 0) return;

    const float this_step_time = (now_ms - ref_timestamp) * 0.001f;

    if (this_step_time >= 0.2f && this_step_time <= 3.0f) {
      step_buffer[step_idx] = this_step_time;
      step_idx = (step_idx + 1) % 3;
      if (step_cnt < 3) step_cnt++;

      float sum = 0.0f;
      for (int i = 0; i < step_cnt; i++) sum += step_buffer[i];
      avg_step_time = sum / (float)step_cnt;
    }
  }
};

// ################################################################
// ##                                                            ##
// ##  SECTION H: IMU 클래스                                      ##
// ##                                                            ##
// ################################################################

const int PACKET_SIZE = 34;

class IMU {
public:
  IMU(uint8_t IMU_id)
    : IMU_id(IMU_id) {}

  void begin(long baudrate) {
    IMU_SERIAL.begin(baudrate);
  }

  void calibrate();
  bool read_packet(uint8_t* packet_data, size_t packet_size);

  float roll = 0, pitch = 0, yaw = 0;
  float gyro_x = 0, gyro_y = 0, gyro_z = 0;
  float acc_x = 0, acc_y = 0, acc_z = 0;
  float dist_x = 0, dist_y = 0, dist_z = 0;
  uint8_t battery = 0;
  uint16_t time_stamp = 0;
  uint8_t IMU_id;
  bool is_calibrated = false;
  unsigned long last_data_time = 0;
  float avg_data_rate = 0.0f;
  unsigned long data_intervals[10] = { 0 };
  int interval_index = 0;

private:
  int16_t roll_raw = 0, pitch_raw = 0, yaw_raw = 0;
  int16_t gyro_x_raw = 0, gyro_y_raw = 0, gyro_z_raw = 0;
  int16_t acc_x_raw = 0, acc_y_raw = 0, acc_z_raw = 0;
  int16_t dist_x_raw = 0, dist_y_raw = 0, dist_z_raw = 0;
  uint16_t battery_raw = 0;
  uint16_t time_stamp_raw = 0;
  float roll_initial = 0, pitch_initial = 0, yaw_initial = 0;
  int calibration_count = 0;
  float roll_acc = 0.0f, pitch_acc = 0.0f, yaw_acc = 0.0f;
};

void IMU::calibrate() {
  is_calibrated = false;
  calibration_count = 0;
  roll_acc = pitch_acc = yaw_acc = 0.0f;
  Serial.print("IMU ");
  Serial.print(IMU_id);
  Serial.println(" Recalibrating (Wait for 10 samples)...");
}

bool IMU::read_packet(uint8_t* packet_data, size_t packet_size) {
  if (packet_size != PACKET_SIZE) return false;
  if (packet_data[3] != IMU_id) return false;

  const int DATA_LENGTH_FOR_CHECKSUM = PACKET_SIZE - 2;
  uint16_t checksum = 0;
  for (int i = 0; i < DATA_LENGTH_FOR_CHECKSUM; i++) checksum += packet_data[i];
  uint16_t received_checksum;
  memcpy(&received_checksum, &packet_data[PACKET_SIZE - 2], 2);
  received_checksum = __builtin_bswap16(received_checksum);
  if (received_checksum != checksum) return false;

  unsigned long now = micros();
  if (last_data_time > 0) {
    data_intervals[interval_index] = now - last_data_time;
    interval_index = (interval_index + 1) % 10;
    unsigned long sum = 0;
    for (int i = 0; i < 10; i++) sum += data_intervals[i];
    avg_data_rate = 1000000.0f / (sum / 10.0f);
  }
  last_data_time = now;

  uint16_t current_offset = 4;
  memcpy(&roll_raw, &packet_data[current_offset], 2);
  memcpy(&pitch_raw, &packet_data[current_offset + 2], 2);
  memcpy(&yaw_raw, &packet_data[current_offset + 4], 2);
  current_offset += 6;
  roll_raw = __builtin_bswap16(roll_raw);
  pitch_raw = __builtin_bswap16(pitch_raw);
  yaw_raw = __builtin_bswap16(yaw_raw);

  memcpy(&gyro_x_raw, &packet_data[current_offset], 6);
  current_offset += 6;
  gyro_x_raw = __builtin_bswap16(gyro_x_raw);
  gyro_y_raw = __builtin_bswap16(gyro_y_raw);
  gyro_z_raw = __builtin_bswap16(gyro_z_raw);

  memcpy(&acc_x_raw, &packet_data[current_offset], 6);
  current_offset += 6;
  acc_x_raw = __builtin_bswap16(acc_x_raw);
  acc_y_raw = __builtin_bswap16(acc_y_raw);
  acc_z_raw = __builtin_bswap16(acc_z_raw);

  memcpy(&dist_x_raw, &packet_data[current_offset], 6);
  current_offset += 6;
  dist_x_raw = __builtin_bswap16(dist_x_raw);
  dist_y_raw = __builtin_bswap16(dist_y_raw);
  dist_z_raw = __builtin_bswap16(dist_z_raw);

  memcpy(&battery_raw, &packet_data[current_offset], 2);
  current_offset += 2;
  battery_raw = __builtin_bswap16(battery_raw);

  memcpy(&time_stamp_raw, &packet_data[current_offset], 2);
  time_stamp_raw = __builtin_bswap16(time_stamp_raw);

  if (!is_calibrated) {
    roll_acc += (float)roll_raw * 0.01f;
    pitch_acc += (float)pitch_raw * 0.01f;
    yaw_acc += (float)yaw_raw * 0.01f;
    calibration_count++;
    if (calibration_count >= 10) {
      roll_initial = roll_acc / 10.0f;
      pitch_initial = pitch_acc / 10.0f;
      yaw_initial = yaw_acc / 10.0f;
      is_calibrated = true;
      Serial.print("IMU ");
      Serial.print(IMU_id);
      Serial.println(" Zero-Set Complete (10 samples).");
    }
  }

  roll = (roll_raw * 0.01f) - roll_initial;
  pitch = (pitch_raw * 0.01f) - pitch_initial;
  yaw = (yaw_raw * 0.01f) - yaw_initial;
  gyro_x = gyro_x_raw * 0.1f;
  gyro_y = gyro_y_raw * 0.1f;
  gyro_z = gyro_z_raw * 0.1f;
  acc_x = acc_x_raw * 0.001f;
  acc_y = acc_y_raw * 0.001f;
  acc_z = acc_z_raw * 0.001f;
  dist_x = (float)dist_x_raw / 1000.0f;
  dist_y = (float)dist_y_raw / 1000.0f;
  dist_z = (float)dist_z_raw / 1000.0f;
  battery = (uint8_t)battery_raw;
  time_stamp = time_stamp_raw;

  return true;
}

// ################################################################
// ##                                                            ##
// ##  SECTION I: 데이터 구조체 및 전역 변수                       ##
// ##                                                            ##
// ################################################################

// ================================================================
// [I-1] IMU Snapshot
// ================================================================

struct IMUSnapshot {
  float rate;
  float roll, pitch, yaw;
  float gx, gy, gz;
  float ax, ay, az;
  float dx, dy, dz;
  uint8_t batt;
  int event;
  float gcp;
  uint8_t phase;
  float avg_step_time;
};

#define COPY_SNAPSHOT(dst, src) \
  do { \
    (dst).rate = (src).rate; \
    (dst).roll = (src).roll; \
    (dst).pitch = (src).pitch; \
    (dst).yaw = (src).yaw; \
    (dst).gx = (src).gx; \
    (dst).gy = (src).gy; \
    (dst).gz = (src).gz; \
    (dst).ax = (src).ax; \
    (dst).ay = (src).ay; \
    (dst).az = (src).az; \
    (dst).dx = (src).dx; \
    (dst).dy = (src).dy; \
    (dst).dz = (src).dz; \
    (dst).batt = (src).batt; \
    (dst).event = (src).event; \
    (dst).gcp = (src).gcp; \
    (dst).phase = (src).phase; \
    (dst).avg_step_time = (src).avg_step_time; \
  } while (0)

// ================================================================
// [I-2] 전역 센서 객체
// ================================================================

IMU left_IMU(1);
IMU right_IMU(0);
GaitDetector left_Detector;
GaitDetector right_Detector;

volatile bool left_loading = false;
volatile bool right_loading = false;

volatile IMUSnapshot snapL;
volatile IMUSnapshot snapR;

inline volatile IMUSnapshot& snapOf(Side s) {
  return (s == SIDE_LEFT) ? snapL : snapR;
}

// ================================================================
// [I-3] 교대(Alternation) 상태 변수
// ================================================================

volatile Side lastHSSide = SIDE_RIGHT;      // 마지막 HS 발생 쪽
volatile Side lastHOSide = SIDE_RIGHT;      // 마지막 HO 발생 쪽
volatile bool firstHSDone = false;          // ★ 첫 HS 발생 여부 (어느 발이든)
volatile bool firstHODone = false;          // ★ 첫 HO 발생 여부 (어느 발이든)
uint32_t lastHS_ms[SIDE_COUNT] = { 0, 0 };  // ★ HS 발생 타임스탬프 (레이스 컨디션 방지)

// ★★ 첫 스텝 건너뛰기: 출발 시 각 측면별 한 스텝은 Force Profile 미적용
// (서 있다가 출발할 때 바로 보조가 작동하는 것 방지)
// ★ 첫 N스텝 건너뛰기 (true/false 대신 카운터 사용)
#define SKIP_STEP_COUNT 2
volatile int skipStepCount[SIDE_COUNT] = {SKIP_STEP_COUNT, SKIP_STEP_COUNT};
#define HS_BLOCK_DURATION_MS 200            // ★ HS 후 반대쪽 차단 시간 (ms) - 동시 HS 방지

// ★★★ Force Assist 우선권: 먼저 시작한 쪽이 끝날 때까지 독점 ★★★
// SIDE_COUNT = 아무도 Assist 안 함, SIDE_LEFT/RIGHT = 해당 쪽이 Assist 중
volatile Side activeAssistSide = SIDE_COUNT;
volatile int lastEvent_L = 0;
volatile int lastEvent_R = 0;

// ================================================================
// [I-4] 모터 상태 변수
// ================================================================

volatile float motor_position_deg[SIDE_COUNT] = { 0, 0 };
volatile float motor_velocity_erpm[SIDE_COUNT] = { 0, 0 };
volatile float motor_current_a[SIDE_COUNT] = { 0, 0 };
volatile float motor_temperature[SIDE_COUNT] = { 0, 0 };

// ================================================================
// [I-5] 컨트롤러 상태 변수
// ================================================================

// Loadcell
volatile float loadcellRaw_N[SIDE_COUNT] = { 0, 0 };

// Force/Velocity 제어
volatile float desiredForce_N[SIDE_COUNT] = { 0, 0 };
volatile float actualForce_N[SIDE_COUNT] = { 0, 0 };
volatile float desiredVelocity_mps[SIDE_COUNT] = { 0, 0 };
volatile float actualVelocity_mps[SIDE_COUNT] = { 0, 0 };
volatile float desiredCurrent_A[SIDE_COUNT] = { 0, 0 };

// Position Assist Mode용
volatile float desiredPosition_deg[SIDE_COUNT] = { 0, 0 };
volatile float positionOffset_deg[SIDE_COUNT] = { 0, 0 };
volatile float desiredVelocity_erpm[SIDE_COUNT] = { 0, 0 };

// Admittance internal
volatile float adm_velocity_mps[SIDE_COUNT] = { 0, 0 };
volatile float adm_position_m[SIDE_COUNT] = { 0, 0 };

// GCP 구간 진입 감지용
volatile float prev_gcp[SIDE_COUNT] = { 0.0f, 0.0f };

// Profile 진입 에지 감지 (velocity/PID 리셋용)
volatile bool wasInProfile[SIDE_COUNT] = { false, false };

// ================================================================
// [I-6] Safety 변수
// ================================================================

volatile float initialPosition_deg[SIDE_COUNT] = { 0, 0 };
volatile bool initialPositionSet[SIDE_COUNT] = { false, false };
volatile bool safetyTriggered[SIDE_COUNT] = { false, false };

volatile bool motorEnabled = false;

// ★★★ One-shot Pretension: Enable/GCP초기화 시 케이블 감기 ★★★
// - Enable 또는 GCP 초기화 시 한 번만 5N으로 케이블 감기
// - 완료 후 Current = 0A 유지 (GCP 시작 전까지)
volatile bool pretensionDone[SIDE_COUNT] = { false, false };
volatile uint32_t pretensionStartTime_ms[SIDE_COUNT] = { 0, 0 };
#define PRETENSION_DURATION_MS 1000  // Pretension 최대 시간 (fallback)
#define PRETENSION_SETTLE_TICKS 10   // F_meas >= 5N 연속 10 tick 확인
volatile int pretensionSettleCount[SIDE_COUNT] = { 0, 0 };

// ★★★ Slack Method 7: 3-Phase Admittance (HS 기준) ★★★
// - Profile 밖 전구간: F_cmd = 0, Falling Admittance 계수
// - Force Profile: 0N → Peak → 0N (PRE = 0)

// ★ Position Mode 구간 진입 감지용 (Enable 시 리셋 필요)
float prev_gcp_pos[SIDE_COUNT] = { 1.0f, 1.0f };  // 초기값 1.0 (GCP_POS_START보다 큼)

volatile bool safetyPrintPending = false;
volatile uint8_t safetySidePending = 255;
volatile float safetyPosDiff_deg = 0.0f;

// ★★ Cable Tangle Safety 변수
volatile int safetyTangleCount[SIDE_COUNT] = { 0, 0 };
volatile bool safetyTanglePending = false;
volatile uint8_t safetyTangleSide = 255;
volatile float safetyTangleForce = 0.0f;
volatile float safetyTangleErpm = 0.0f;

// ================================================================
// [I-7] 타이밍 변수
// ================================================================

volatile float dt_ctl = (float)CONTROL_PERIOD_MS * 0.001f;
volatile float dt_pos = (float)POSITION_PERIOD_MS * 0.001f;
volatile uint32_t lastTimeCtl_us = 0;

volatile uint8_t logTickCounter = 0;
volatile uint8_t posTickCounter = 0;

// ================================================================
// [I-8] 디버그 변수
// ================================================================

volatile float dbg_use_gcp[SIDE_COUNT] = { 0, 0 };
volatile float dbg_F_assist[SIDE_COUNT] = { 0, 0 };
volatile float dbg_F_cmd[SIDE_COUNT] = { 0, 0 };
volatile float dbg_F_err[SIDE_COUNT] = { 0, 0 };
volatile float dbg_des_erpm[SIDE_COUNT] = { 0, 0 };
volatile uint8_t dbg_contra_loading[SIDE_COUNT] = { 0, 0 };

volatile float dbg_pos_offset[SIDE_COUNT] = { 0, 0 };
volatile float dbg_des_pos[SIDE_COUNT] = { 0, 0 };
volatile float dbg_pos_err[SIDE_COUNT] = { 0, 0 };
// ★ FF Velocity 로깅용 디버그 변수
volatile float dbg_adm_vel_mps[SIDE_COUNT] = { 0, 0 };
volatile float dbg_motion_ff_mps[SIDE_COUNT] = { 0, 0 };
volatile float dbg_treadmill_ff_mps[SIDE_COUNT] = { 0, 0 };

bool show_debug = false;

// ★★★ USB Serial 스트리밍 (Treadmill_main 추가) ★★★
// BLE와 독립적으로 USB Serial로 동일한 SW19c 패킷을 전송
volatile bool serialStreamEnabled = false;
#define SERIAL_SEND_PERIOD_MS 9  // 9ms = 111Hz (USB는 BLE보다 대역폭 충분)

// ################################################################
// ##                                                            ##
// ##  SECTION J: 로깅 시스템                                     ##
// ##                                                            ##
// ################################################################

// ================================================================
// [J-1] Log Entry 구조체
// ================================================================

struct LogEntry {
  uint32_t timestamp_ms;
  float freq_hz;

  float L_des_force, L_act_force, L_err_force;
  float L_des_vel_mps, L_act_vel_mps, L_err_vel_mps;
  float L_des_pos_deg, L_act_pos_deg, L_err_pos_deg;
  float L_des_curr, L_act_curr, L_err_curr;
  float L_pos_integral, L_vel_integral;

  float R_des_force, R_act_force, R_err_force;
  float R_des_vel_mps, R_act_vel_mps, R_err_vel_mps;
  float R_des_pos_deg, R_act_pos_deg, R_err_pos_deg;
  float R_des_curr, R_act_curr, R_err_curr;
  float R_pos_integral, R_vel_integral;

  IMUSnapshot imuL;
  IMUSnapshot imuR;

  // ★ FF Velocity 로깅
  float L_adm_vel_mps, L_motion_ff_mps, L_treadmill_ff_mps;
  float R_adm_vel_mps, R_motion_ff_mps, R_treadmill_ff_mps;

  // ★ FF Gain 로깅
  float tff_gain;    // Treadmill FF gain (adaptive 시 stride마다 변함)
  float ff_gain_f;   // Motion FF gain

  uint16_t a7;
  uint8_t mode;
  uint32_t mark;  // Mark number for CSV/BLE sync
};

// ================================================================
// [J-2] 로깅 버퍼 및 상태
// ================================================================

#define RING_BUFFER_SIZE 512
DMAMEM LogEntry logBuffer[RING_BUFFER_SIZE];
volatile uint32_t logHead = 0, logTail = 0;

volatile bool isLogging = false;
File dataFile;
char filename[32] = "AK60_GCP_00.CSV";  // ★ 32바이트: 커스텀 파일명 + .CSV 수용
char customFilename[32] = {0};  // ★ GUI에서 지정한 커스텀 파일명 (없으면 auto-increment)
uint32_t logStartTime_us = 0;
volatile uint16_t syncA7 = 0;

// ################################################################
// ##                                                            ##
// ##  SECTION K: CAN 통신                                        ##
// ##                                                            ##
// ################################################################

// ================================================================
// [K-1] CAN 수신 콜백
// ================================================================

void canReceiveCallback(const CAN_message_t& msg) {
  uint8_t base_id = (uint8_t)(msg.id & 0xFF);

  Side s;
  if (base_id == MOTOR_LEFT_CAN_ID) s = SIDE_LEFT;
  else if (base_id == MOTOR_RIGHT_CAN_ID) s = SIDE_RIGHT;
  else return;

  if (msg.len != 8) return;

  int16_t pos_raw = (msg.buf[0] << 8) | msg.buf[1];
  int16_t speed_raw = (msg.buf[2] << 8) | msg.buf[3];
  int16_t current_raw = (msg.buf[4] << 8) | msg.buf[5];

  float sign = isLeftMotor(base_id) ? -1.0f : 1.0f;

  motor_position_deg[s] = sign * (pos_raw / 10.0f);
  motor_velocity_erpm[s] = sign * (speed_raw * 10.0f);
  motor_current_a[s] = sign * (current_raw / 100.0f);
  motor_temperature[s] = (int8_t)msg.buf[6];
}

// ================================================================
// [K-2] CAN 초기화
// ================================================================

void setupCAN() {
  can1.begin();
  can1.setBaudRate(1000000);
  can1.setMaxMB(16);
  can1.enableFIFO();
  can1.enableFIFOInterrupt();
  can1.setFIFOFilter(0, 0, 0, EXT);
  can1.onReceive(canReceiveCallback);
  Serial.println("✅ CAN initialized @ 1Mbps (Left+Right IDs supported)");
}

// ================================================================
// [K-3] CAN 전송
// ================================================================

inline void sendCurrentCommand(uint8_t can_id, float current_A) {
  float cmdA = current_A;
  if (isLeftMotor(can_id)) cmdA = -cmdA;

  int32_t current_int = (int32_t)(cmdA * 1000.0f);

  uint8_t buffer[8] = { 0 };
  buffer[0] = (current_int >> 24) & 0xFF;
  buffer[1] = (current_int >> 16) & 0xFF;
  buffer[2] = (current_int >> 8) & 0xFF;
  buffer[3] = (current_int)&0xFF;

  CAN_message_t msg;
  msg.id = can_id | ((uint32_t)CAN_PACKET_SET_CURRENT << 8);
  msg.len = 4;
  msg.flags.extended = 1;
  for (int i = 0; i < 4; i++) msg.buf[i] = buffer[i];

  can1.write(msg);
}

void setMotorOrigin(uint8_t can_id, uint8_t mode) {
  CAN_message_t msg;
  msg.id = can_id | ((uint32_t)CAN_PACKET_SET_ORIGIN << 8);
  msg.flags.extended = 1;
  msg.len = 1;
  msg.buf[0] = mode;

  can1.write(msg);
}

void setAllMotorsOrigin() {
  setMotorOrigin(MOTOR_LEFT_CAN_ID, 0);
  delay(10);
  setMotorOrigin(MOTOR_RIGHT_CAN_ID, 0);
  delay(10);

  motor_position_deg[SIDE_LEFT] = 0.0f;
  motor_position_deg[SIDE_RIGHT] = 0.0f;
  initialPosition_deg[SIDE_LEFT] = 0.0f;
  initialPosition_deg[SIDE_RIGHT] = 0.0f;

  Serial.println("✅ Motor origin set to 0 (L+R)");
}

// ################################################################
// ##                                                            ##
// ##  SECTION L: SD 카드 / 로깅                                  ##
// ##                                                            ##
// ################################################################

// ================================================================
// [L-1] SD 초기화
// ================================================================

void setupSD() {
  if (!SD.begin(SDCARD_CS_PIN)) Serial.println("❌ SD card initialization failed!");
  else Serial.println("✅ SD card initialized");
}

// ================================================================
// [L-2] 로그 파일 생성
// ================================================================

void createLogFile() {
  // ★ 커스텀 파일명이 지정되어 있으면 사용
  if (customFilename[0] != '\0') {
    // .CSV 확장자 확인 및 추가
    char fullName[32];
    strncpy(fullName, customFilename, sizeof(fullName) - 5);
    fullName[sizeof(fullName) - 5] = '\0';
    // 확장자가 없으면 .CSV 추가
    if (strstr(fullName, ".CSV") == NULL && strstr(fullName, ".csv") == NULL) {
      strncat(fullName, ".CSV", sizeof(fullName) - strlen(fullName) - 1);
    }
    strncpy(filename, fullName, sizeof(filename) - 1);
    filename[sizeof(filename) - 1] = '\0';
    customFilename[0] = '\0';  // 사용 후 초기화
  } else {
    // ★ 기본 패턴으로 리셋 후 auto-increment
    // (커스텀 파일명 사용 후 filename이 변경되어 있을 수 있으므로 반드시 리셋)
    strcpy(filename, "AK60_GCP_00.CSV");
    for (int i = 0; i < 100; i++) {
      filename[9] = '0' + (i / 10);
      filename[10] = '0' + (i % 10);
      if (!SD.exists(filename)) break;
    }
  }

  dataFile = SD.open(filename, FILE_WRITE);
  if (dataFile) {
    dataFile.println(
      "Time_ms,Freq_Hz,"
      "L_DesForce_N,L_ActForce_N,L_ErrForce_N,"
      "L_DesVel_mps,L_ActVel_mps,L_ErrVel_mps,"
      "L_DesPos_deg,L_ActPos_deg,L_ErrPos_deg,"
      "L_DesCurr_A,L_ActCurr_A,L_ErrCurr_A,"
      "L_PosInteg,L_VelInteg,"
      "R_DesForce_N,R_ActForce_N,R_ErrForce_N,"
      "R_DesVel_mps,R_ActVel_mps,R_ErrVel_mps,"
      "R_DesPos_deg,R_ActPos_deg,R_ErrPos_deg,"
      "R_DesCurr_A,R_ActCurr_A,R_ErrCurr_A,"
      "R_PosInteg,R_VelInteg,"
      "L_Rate,L_Roll,L_Pitch,L_Yaw,L_Gx,L_Gy,L_Gz,L_Ax,L_Ay,L_Az,L_Dx,L_Dy,L_Dz,L_Batt,L_Event,L_GCP,L_Phase,L_StepTime,L_HO_GCP,"
      "R_Rate,R_Roll,R_Pitch,R_Yaw,R_Gx,R_Gy,R_Gz,R_Ax,R_Ay,R_Az,R_Dx,R_Dy,R_Dz,R_Batt,R_Event,R_GCP,R_Phase,R_StepTime,R_HO_GCP,"
      "L_AdmVel_mps,L_MotionFF_mps,L_TreadmillFF_mps,"
      "R_AdmVel_mps,R_MotionFF_mps,R_TreadmillFF_mps,"
      "TFF_Gain,FF_Gain_F,"
      "A7,Mode,Mark");
    Serial.print("📁 Log file: ");
    Serial.println(filename);
  }
}

// ================================================================
// [L-3] 로깅 시작/중지
// ================================================================

void startLogging() {
  if (isLogging) return;

  noInterrupts();
  logHead = 0;
  logTail = 0;
  logTickCounter = 0;
  posTickCounter = 0;
  interrupts();

  createLogFile();
  if (dataFile) {
    isLogging = true;
    logStartTime_us = micros();
    left_Detector.reset();
    right_Detector.reset();

    // ★★ 첫 스텝 건너뛰기 활성화: 양쪽 모두 첫 스텝은 Force Assist 미적용
    skipStepCount[SIDE_LEFT] = SKIP_STEP_COUNT;
    skipStepCount[SIDE_RIGHT] = SKIP_STEP_COUNT;

    // ★★ 동시 보조 방지 플래그 리셋
    activeAssistSide = SIDE_COUNT;

    // ★ GCP 모드 설정 (HS 기준)
    left_Detector.gcpStartMode = GCP_START_AT_HS;
    right_Detector.gcpStartMode = GCP_START_AT_HS;

    Serial.print("📝 Logging STARTED: ");
    Serial.println(filename);
  }
}

void stopLogging() {
  if (!isLogging) return;
  isLogging = false;
  Serial.print("💾 Logging STOP requested: ");
  Serial.println(filename);
}

/**
 * @brief 즉시 로깅 중지 (버퍼 flush + 파일 닫기)
 *
 * stopLogging()은 플래그만 설정하고 processLogBuffer()에서 비동기 닫기.
 * 이 함수는 save 명령에서 새 파일 시작 전에 이전 파일을 확실히 닫기 위해 사용.
 * ★ 블로킹 호출이므로 제어 루프에 영향 (ISR는 독립 실행이라 안전)
 */
void stopLoggingImmediate() {
  if (!isLogging) return;
  isLogging = false;

  // ★ 링 버퍼의 남은 데이터 모두 기록
  uint32_t flushStart = millis();
  while (logTail != logHead && (millis() - flushStart) < 500) {
    processLogBuffer();
  }

  // ★ 파일 flush + close
  if (dataFile) {
    dataFile.flush();
    dataFile.close();
  }

  // ★ GUI에 이전 파일 저장 완료 피드백
  char resp[48];
  snprintf(resp, sizeof(resp), "LOG_STOP:%s", filename);
  sendBleResponse(resp);

  Serial.print("💾 Logging STOPPED (immediate): ");
  Serial.println(filename);
}

// ================================================================
// [L-4] 로그 버퍼 처리
// ================================================================

void processLogBuffer() {
  const int MAX_PROCESS = 30;
  int processed = 0;
  static uint32_t flush_cnt = 0;

  while (processed < MAX_PROCESS && logTail != logHead) {
    LogEntry e = logBuffer[logTail];
    logTail = (logTail + 1) % RING_BUFFER_SIZE;

    if (dataFile) {
      dataFile.print(e.timestamp_ms);
      dataFile.print(",");
      dataFile.print(e.freq_hz, 1);
      dataFile.print(",");

      // Left
      dataFile.print(e.L_des_force, 3);
      dataFile.print(",");
      dataFile.print(e.L_act_force, 3);
      dataFile.print(",");
      dataFile.print(e.L_err_force, 3);
      dataFile.print(",");
      dataFile.print(e.L_des_vel_mps, 6);
      dataFile.print(",");
      dataFile.print(e.L_act_vel_mps, 6);
      dataFile.print(",");
      dataFile.print(e.L_err_vel_mps, 6);
      dataFile.print(",");
      dataFile.print(e.L_des_pos_deg, 2);
      dataFile.print(",");
      dataFile.print(e.L_act_pos_deg, 2);
      dataFile.print(",");
      dataFile.print(e.L_err_pos_deg, 2);
      dataFile.print(",");
      dataFile.print(e.L_des_curr, 3);
      dataFile.print(",");
      dataFile.print(e.L_act_curr, 3);
      dataFile.print(",");
      dataFile.print(e.L_err_curr, 3);
      dataFile.print(",");
      dataFile.print(e.L_pos_integral, 4);
      dataFile.print(",");
      dataFile.print(e.L_vel_integral, 4);
      dataFile.print(",");

      // Right
      dataFile.print(e.R_des_force, 3);
      dataFile.print(",");
      dataFile.print(e.R_act_force, 3);
      dataFile.print(",");
      dataFile.print(e.R_err_force, 3);
      dataFile.print(",");
      dataFile.print(e.R_des_vel_mps, 6);
      dataFile.print(",");
      dataFile.print(e.R_act_vel_mps, 6);
      dataFile.print(",");
      dataFile.print(e.R_err_vel_mps, 6);
      dataFile.print(",");
      dataFile.print(e.R_des_pos_deg, 2);
      dataFile.print(",");
      dataFile.print(e.R_act_pos_deg, 2);
      dataFile.print(",");
      dataFile.print(e.R_err_pos_deg, 2);
      dataFile.print(",");
      dataFile.print(e.R_des_curr, 3);
      dataFile.print(",");
      dataFile.print(e.R_act_curr, 3);
      dataFile.print(",");
      dataFile.print(e.R_err_curr, 3);
      dataFile.print(",");
      dataFile.print(e.R_pos_integral, 4);
      dataFile.print(",");
      dataFile.print(e.R_vel_integral, 4);
      dataFile.print(",");

      // Left IMU
      dataFile.print(e.imuL.rate, 1);
      dataFile.print(",");
      dataFile.print(e.imuL.roll, 2);
      dataFile.print(",");
      dataFile.print(e.imuL.pitch, 2);
      dataFile.print(",");
      dataFile.print(e.imuL.yaw, 2);
      dataFile.print(",");
      dataFile.print(e.imuL.gx, 1);
      dataFile.print(",");
      dataFile.print(e.imuL.gy, 1);
      dataFile.print(",");
      dataFile.print(e.imuL.gz, 1);
      dataFile.print(",");
      dataFile.print(e.imuL.ax, 2);
      dataFile.print(",");
      dataFile.print(e.imuL.ay, 2);
      dataFile.print(",");
      dataFile.print(e.imuL.az, 2);
      dataFile.print(",");
      dataFile.print(e.imuL.dx, 3);
      dataFile.print(",");
      dataFile.print(e.imuL.dy, 3);
      dataFile.print(",");
      dataFile.print(e.imuL.dz, 3);
      dataFile.print(",");
      dataFile.print((int)e.imuL.batt);
      dataFile.print(",");
      dataFile.print(e.imuL.event);
      dataFile.print(",");
      dataFile.print(e.imuL.gcp, 4);
      dataFile.print(",");
      dataFile.print(e.imuL.phase);
      dataFile.print(",");
      dataFile.print(e.imuL.avg_step_time, 3);
      dataFile.print(",");
      dataFile.print(left_Detector.getHoGcpInCycle(), 4);
      dataFile.print(",");

      // Right IMU
      dataFile.print(e.imuR.rate, 1);
      dataFile.print(",");
      dataFile.print(e.imuR.roll, 2);
      dataFile.print(",");
      dataFile.print(e.imuR.pitch, 2);
      dataFile.print(",");
      dataFile.print(e.imuR.yaw, 2);
      dataFile.print(",");
      dataFile.print(e.imuR.gx, 1);
      dataFile.print(",");
      dataFile.print(e.imuR.gy, 1);
      dataFile.print(",");
      dataFile.print(e.imuR.gz, 1);
      dataFile.print(",");
      dataFile.print(e.imuR.ax, 2);
      dataFile.print(",");
      dataFile.print(e.imuR.ay, 2);
      dataFile.print(",");
      dataFile.print(e.imuR.az, 2);
      dataFile.print(",");
      dataFile.print(e.imuR.dx, 3);
      dataFile.print(",");
      dataFile.print(e.imuR.dy, 3);
      dataFile.print(",");
      dataFile.print(e.imuR.dz, 3);
      dataFile.print(",");
      dataFile.print((int)e.imuR.batt);
      dataFile.print(",");
      dataFile.print(e.imuR.event);
      dataFile.print(",");
      dataFile.print(e.imuR.gcp, 4);
      dataFile.print(",");
      dataFile.print(e.imuR.phase);
      dataFile.print(",");
      dataFile.print(e.imuR.avg_step_time, 3);
      dataFile.print(",");
      dataFile.print(right_Detector.getHoGcpInCycle(), 4);
      dataFile.print(",");

      // ★ FF Velocity components
      dataFile.print(e.L_adm_vel_mps, 6); dataFile.print(",");
      dataFile.print(e.L_motion_ff_mps, 6); dataFile.print(",");
      dataFile.print(e.L_treadmill_ff_mps, 6); dataFile.print(",");
      dataFile.print(e.R_adm_vel_mps, 6); dataFile.print(",");
      dataFile.print(e.R_motion_ff_mps, 6); dataFile.print(",");
      dataFile.print(e.R_treadmill_ff_mps, 6); dataFile.print(",");

      // ★ FF Gains
      dataFile.print(e.tff_gain, 4); dataFile.print(",");
      dataFile.print(e.ff_gain_f, 4); dataFile.print(",");

      dataFile.print(e.a7);
      dataFile.print(",");
      dataFile.print(e.mode);
      dataFile.print(",");
      dataFile.println(e.mark);

      flush_cnt++;
    }
    processed++;
  }

  if (dataFile && flush_cnt >= 100) {
    dataFile.flush();
    flush_cnt = 0;
  }

  if (!isLogging && dataFile && (logTail == logHead)) {
    dataFile.flush();
    dataFile.close();
    Serial.print("💾 Logging STOPPED: ");
    Serial.println(filename);
  }
}

// ################################################################
// ##                                                            ##
// ##  SECTION M: IMU 스트림 처리                                 ##
// ##                                                            ##
// ################################################################

uint8_t receive_buffer[PACKET_SIZE * 4];
size_t buffer_index = 0;

void updateIMUStream() {
  int available_bytes = IMU_SERIAL.available();
  if (available_bytes <= 0) return;

  int bytes_to_read = min(available_bytes, (int)(sizeof(receive_buffer) - buffer_index));
  IMU_SERIAL.readBytes(&receive_buffer[buffer_index], bytes_to_read);
  buffer_index += bytes_to_read;

  while (buffer_index >= PACKET_SIZE) {
    int sop_pos = -1;
    for (int i = 0; i <= (int)buffer_index - 2; i++) {
      if (receive_buffer[i] == 0x55 && receive_buffer[i + 1] == 0x55) {
        sop_pos = i;
        break;
      }
    }
    if (sop_pos == -1) {
      buffer_index = 0;
      break;
    }
    if (sop_pos > 0) {
      memmove(receive_buffer, &receive_buffer[sop_pos], buffer_index - sop_pos);
      buffer_index -= sop_pos;
    }
    if (buffer_index < PACKET_SIZE) break;

    uint8_t imu_id = receive_buffer[3];
    bool packet_valid = false;  // ★ 패킷 처리 성공 여부

    // ===================== LEFT IMU =====================
    if (imu_id == left_IMU.IMU_id) {
      if (left_IMU.read_packet(receive_buffer, PACKET_SIZE)) {
        packet_valid = true;  // ★ Checksum 성공

        // ★★★ 교대 제한 제거 - 양쪽 독립적으로 HO/HS 감지 가능 ★★★
        // Timeout 처리
        if (left_Detector.hasTimedOut()) {
          left_Detector.clearTimedOut();
          Serial.println("🔄 LEFT timeout - GCP reset");
        }

        // ★ 항상 HO/HS 허용 (교대 제한 없음)
        left_Detector.allowHS = true;
        left_Detector.allowHO = true;

        int evt = (int)left_Detector.update(left_IMU.pitch, -left_IMU.gyro_y);

        if (evt == GaitDetector::EVENT_HO) {
          Serial.println("🦵 LEFT HO!");
        }

        if (evt == GaitDetector::EVENT_HS) {
          Serial.println("🦶 LEFT HS!");
        }

        if (evt != 0) {
          lastEvent_L = evt;
        }

        IMUSnapshot tmp;
        tmp.rate = left_IMU.avg_data_rate;
        tmp.roll = left_IMU.roll;
        tmp.pitch = left_IMU.pitch;
        tmp.yaw = left_IMU.yaw;
        tmp.gx = left_IMU.gyro_x;
        tmp.gy = -left_IMU.gyro_y;
        tmp.gz = left_IMU.gyro_z;
        tmp.ax = left_IMU.acc_x;
        tmp.ay = left_IMU.acc_y;
        tmp.az = left_IMU.acc_z;
        tmp.dx = left_IMU.dist_x;
        tmp.dy = left_IMU.dist_y;
        tmp.dz = left_IMU.dist_z;
        tmp.batt = left_IMU.battery;
        tmp.event = lastEvent_L;
        tmp.gcp = left_Detector.getGCP();
        tmp.phase = left_Detector.getPhaseValue();
        tmp.avg_step_time = left_Detector.getAvgStepTime();

        noInterrupts();
        COPY_SNAPSHOT(snapL, tmp);
        left_loading = left_Detector.isLoading();
        interrupts();
      }
    }
    // ===================== RIGHT IMU =====================
    else if (imu_id == right_IMU.IMU_id) {
      if (right_IMU.read_packet(receive_buffer, PACKET_SIZE)) {
        packet_valid = true;  // ★ Checksum 성공

        // ★★★ 교대 제한 제거 - 양쪽 독립적으로 HO/HS 감지 가능 ★★★
        // Timeout 처리
        if (right_Detector.hasTimedOut()) {
          right_Detector.clearTimedOut();
          Serial.println("🔄 RIGHT timeout - GCP reset");
        }

        // ★ 항상 HO/HS 허용 (교대 제한 없음)
        right_Detector.allowHS = true;
        right_Detector.allowHO = true;

        int evt = (int)right_Detector.update(right_IMU.pitch, -right_IMU.gyro_y);

        if (evt == GaitDetector::EVENT_HO) {
          Serial.println("🦵 RIGHT HO!");
        }

        if (evt == GaitDetector::EVENT_HS) {
          Serial.println("🦶 RIGHT HS!");
        }

        if (evt != 0) {
          lastEvent_R = evt;
        }

        IMUSnapshot tmp;
        tmp.rate = right_IMU.avg_data_rate;
        tmp.roll = right_IMU.roll;
        tmp.pitch = right_IMU.pitch;
        tmp.yaw = right_IMU.yaw;
        tmp.gx = right_IMU.gyro_x;
        tmp.gy = -right_IMU.gyro_y;
        tmp.gz = right_IMU.gyro_z;
        tmp.ax = right_IMU.acc_x;
        tmp.ay = right_IMU.acc_y;
        tmp.az = right_IMU.acc_z;
        tmp.dx = right_IMU.dist_x;
        tmp.dy = right_IMU.dist_y;
        tmp.dz = right_IMU.dist_z;
        tmp.batt = right_IMU.battery;
        tmp.event = lastEvent_R;
        tmp.gcp = right_Detector.getGCP();
        tmp.phase = right_Detector.getPhaseValue();
        tmp.avg_step_time = right_Detector.getAvgStepTime();

        noInterrupts();
        COPY_SNAPSHOT(snapR, tmp);
        right_loading = right_Detector.isLoading();
        interrupts();
      }
    }

    // ★ 패킷 처리 결과에 따른 버퍼 이동
    if (packet_valid) {
      // 정상 패킷: PACKET_SIZE만큼 스킵
      memmove(receive_buffer, &receive_buffer[PACKET_SIZE], buffer_index - PACKET_SIZE);
      buffer_index -= PACKET_SIZE;
    } else {
      // ★ 실패 (Checksum 오류 or Unknown ID): 1바이트만 스킵하고 다시 헤더 찾기
      // → 0x55 0x55가 데이터 중간에 나타난 경우 복구 가능
      memmove(receive_buffer, &receive_buffer[1], buffer_index - 1);
      buffer_index -= 1;
    }
  }
}

// ################################################################
// ##                                                            ##
// ##  SECTION N: 보조 프로파일 함수                              ##
// ##                                                            ##
// ################################################################

// ================================================================
// [N-1] Force Assist: 4-Point Profile (Onset-Inter-Peak-Release)
// ================================================================

/**
 * ★★★ 4점 Force 프로파일 (Onset-Inter-Peak-Release + Ramp Pretension) ★★★
 *
 *  Force
 *    │               Peak (50N)
 *    │               ╱╲
 *    │             ╱    ╲   (sin: 부드러운 연결)
 *    │           ╱        ╲
 *    │         ╱            ╲
 * PRE│───────╱                ╲
 * (5N)                          ╲──────────────
 *  0N│
 *    └──┬────────┬──────────┬──────────────→ GCP
 *      0%      ONSET      PEAK   RELEASE
 *
 *  구간별 동작:
 *  1) [0%, ONSET): PRE (Zone 3에서 처리)
 *  2) [ONSET, PEAK]: Rising Half-Sine  - 5N → 50N
 *  3) [PEAK, RELEASE]: Falling Half-Sine - 50N → 0N
 *  4) [RELEASE, 100%]: 0N
 *
 *  ★★ INTER 제거: ONSET→PEAK 하나의 sin(π/2×t) 곡선
 *  피크에서 기울기 = 0 → Rising/Falling 매끄럽게 연결
 */
inline float computeForceProfile_4Point(float gcp, float peakF) {
  const float PRE = MIN_TENSION_N;  // 5N (Zone 3b ramp과 연속)

  // Phase 0: [0%, ONSET) - PRE (Zone 3에서 처리되므로 미도달)
  if (gcp < GCP_FORCE_ONSET) {
    return PRE;
  }

  // Phase 1: [ONSET, PEAK] - Rising Half-Sine
  // sin(π/2 × t): 0→1, 피크에서 기울기=0 (매끄러운 연결)
  if (gcp <= GCP_FORCE_PEAK) {
    float t = (gcp - GCP_FORCE_ONSET) / (GCP_FORCE_PEAK - GCP_FORCE_ONSET);
    t = clampf(t, 0.0f, 1.0f);
    float ratio = sinf(1.5707963f * t);         // 0 → 1
    return PRE + (peakF - PRE) * ratio;          // 5N → 50N
  }

  // Phase 2: [PEAK, RELEASE] - Falling Half-Sine (peakF → 0N)
  // cos(π/2 × t): 1→0, 피크에서 기울기=0 (매끄러운 연결)
  if (gcp <= GCP_FORCE_RELEASE) {
    float t = (gcp - GCP_FORCE_PEAK) / (GCP_FORCE_RELEASE - GCP_FORCE_PEAK);
    t = clampf(t, 0.0f, 1.0f);
    float ratio = cosf(1.5707963f * t);  // 1 → 0
    return peakF * ratio;                // 50N → 0N
  }

  // Phase 3: [RELEASE, 100%] - Zero Force Control
  return 0.0f;
}

/**
 * ★ Assist Zone 판별 함수
 * GCP가 ONSET ~ RELEASE 구간인지 확인 (Dual Admittance 전환용)
 */
inline bool isInAssistZone(float gcp) {
  return (gcp >= GCP_FORCE_ONSET && gcp <= GCP_FORCE_RELEASE);
}

/**
 * ★★ Falling Zone 판별 함수
 * GCP가 PEAK ~ RELEASE 구간인지 확인 (하강 구간 빠른 추종용)
 * Peak(70%) 초과 ~ Release(80%) 이하
 */
inline bool isInFallingZone(float gcp) {
  return (gcp > GCP_FORCE_PEAK && gcp <= GCP_FORCE_RELEASE);
}

// ================================================================
// [N-2] Position Assist: 1/4 Sine Profile
// ================================================================

/**
 * ★ Position 프로파일: 1/4 Sine ★
 *
 *  Position
 *    │           ╭────────
 *    │          ╱
 *    │         ╱
 *    │        ╱
 *    │       ╱
 *    └──────┴────────────── GCP
 *         Start        End
 *
 *  Start → End: Quarter Sine (0 → 1)
 */
inline float computePositionOffset_QuarterSine(float gcp, float amplitude, Side s) {
  // 구간 외: 오프셋 없음
  if (gcp < GCP_POS_START || gcp > GCP_POS_END) {
    return 0.0f;
  }

  // 0~1로 정규화
  float t = (gcp - GCP_POS_START) / (GCP_POS_END - GCP_POS_START);
  t = clampf(t, 0.0f, 1.0f);

  // 1/4 sine: sin(π/2 * t) → 0에서 시작, 1에서 최대
  float ratio = sinf(1.5707963f * t);

  return amplitude * ratio;
}

// ================================================================
// [N-3] Loadcell 읽기
// ================================================================

inline float readLoadcellForceN(Side s) {
  const int pin = sideToLoadcellPin(s);
  const float bias = sideToLoadcellBias(s);
  const float sens = sideToLoadcellSensitive(s);
  int raw_adc = analogRead(pin);
  float voltage = raw_adc * AI_CNT_TO_V;
  float F = (voltage * sens) + bias;
  F = loadcellLPFOf(s).update(F);
  if (F < 0) F = 0;
  return F;
}

// ================================================================
// [N-4] 헬퍼 함수
// ================================================================

inline bool contraLoadingOf(Side s) {
  return (s == SIDE_LEFT) ? right_loading : left_loading;
}

// ★★★ 실시간 GCP: 캐시된 snapL.gcp 대신 getGCP() 직접 호출 ★★★
inline float gcpOf(Side s) {
  return (s == SIDE_LEFT) ? left_Detector.getGCP() : right_Detector.getGCP();
}

// ★★★ GCP 활성 여부 확인 (Pretension vs Linear Ramp 구분용) ★★★
inline bool gcpActiveOf(Side s) {
  return (s == SIDE_LEFT) ? left_Detector.isGcpActive() : right_Detector.isGcpActive();
}

inline bool hsInCycleOf(Side s) {
  return (s == SIDE_LEFT) ? left_Detector.isHsInCycle() : right_Detector.isHsInCycle();
}

// ★ HO GCP 위치 (현재 사이클 또는 이전 사이클) - sine TFF용
inline float hoGcpOf(Side s) {
  float ho = (s == SIDE_LEFT) ? left_Detector.getHoGcpInCycle() : right_Detector.getHoGcpInCycle();
  if (ho < 0.0f) {
    ho = (s == SIDE_LEFT) ? left_Detector.getPrevHoGcp() : right_Detector.getPrevHoGcp();
  }
  return ho;
}

// ################################################################
// ##                                                            ##
// ##  SECTION O: 컨트롤러                                        ##
// ##                                                            ##
// ################################################################

// ================================================================
// [O-1] Position Loop (111Hz) - Position PID
// ================================================================

inline void positionLoopStep(Side s, float dt) {
  float gcp = gcpOf(s);

  // ★ GCP 0% 진입 감지 (HO 발생) → 초기 위치 저장
  // prev_gcp_pos는 전역 변수 (Enable 시 리셋됨)
  if (prev_gcp_pos[s] <= 0.0f && gcp > 0.0f) {
    initialPosition_deg[s] = motor_position_deg[s];  // ★ HO 순간 위치 저장!
    posPidOf(s).reset();
    pidOf(s).reset();
    desiredVelocity_erpm[s] = 0.0f;
    Serial.print(s == SIDE_LEFT ? "📍 L" : "📍 R");
    Serial.print(" InitPos @ HO: ");
    Serial.println(initialPosition_deg[s], 1);
  }
  prev_gcp_pos[s] = gcp;

  // GCP 70% 이후는 스킵 (위치 제어 종료)
  if (gcp > GCP_POS_END) {
    return;
  }

  // ★ 0% ~ 10%: 위치 유지 (offset = 0)
  // ★ 10% ~ 70%: 1/4 sine으로 당김
  float offset = 0.0f;
  if (gcp >= GCP_POS_START) {
    offset = computePositionOffset_QuarterSine(gcp, POSITION_AMPLITUDE_DEG, s);
  }
  positionOffset_deg[s] = offset;

  // 목표 위치 = HO 시점 위치 + 오프셋
  float des_pos = initialPosition_deg[s] + offset;
  desiredPosition_deg[s] = des_pos;

  // 현재 위치
  float act_pos = motor_position_deg[s];

  // Position PID → ERPM 출력
  float vel_erpm = posPidOf(s).compute(des_pos, act_pos, dt);
  desiredVelocity_erpm[s] = clampf(vel_erpm, -MAX_POS_VEL_ERPM, MAX_POS_VEL_ERPM);

  // 디버그용
  dbg_pos_offset[s] = offset;
  dbg_des_pos[s] = des_pos;
  dbg_pos_err[s] = des_pos - act_pos;
}

// ================================================================
// [O-2] Velocity Loop (333Hz) - 메인 컨트롤러
// ================================================================

inline void controllerStep(Side s, float dt) {
  float gcp = gcpOf(s);
  bool contra_loading = contraLoadingOf(s);

  dbg_use_gcp[s] = gcp;
  dbg_contra_loading[s] = contra_loading ? 1 : 0;

  // ★ FF Velocity 디버그 초기화 (모든 경로에서 기본값 0)
  dbg_adm_vel_mps[s] = 0.0f;
  dbg_motion_ff_mps[s] = 0.0f;
  dbg_treadmill_ff_mps[s] = 0.0f;

  if (!motorEnabled) {
    desiredCurrent_A[s] = 0.0f;
    dbg_F_err[s] = 0.0f;
    dbg_des_erpm[s] = 0.0f;
    return;
  }

  actualVelocity_mps[s] = erpm_to_mps(motor_velocity_erpm[s]);

  // ═══════════════════════════════════════════════════════════════
  // MODE 0: Force Assist (4-Point Profile + Dual Admittance)
  // ═══════════════════════════════════════════════════════════════
  if (currentMode == MODE_FORCE_ASSIST) {
    // ═══════════════════════════════════════════════════════════════
    // ★★ Slack Method 7: 3-Phase Admittance (HS 기준, Slack/Rising/Falling) ★★
    //
    // Slack Zone (HS ~ ONSET): Sine TFF (HS→ONSET peak) + Ramp 0→5N
    // Assist Zone (ONSET ~ RELEASE): Force Profile + Admittance
    // Post-Release: Zero Current (모터 프리휠)
    // ═══════════════════════════════════════════════════════════════

    float F_meas = loadcellRaw_N[s];
    actualForce_N[s] = F_meas;

    // ───────────────────────────────────────────────────────────────
    // 구간 판별
    // ───────────────────────────────────────────────────────────────
    bool gcpInProfileZone = (gcp >= GCP_FORCE_ONSET && gcp <= GCP_FORCE_RELEASE);

    bool otherSideActive = (activeAssistSide != SIDE_COUNT && activeAssistSide != s);

    bool inProfileZone = gcpInProfileZone && (skipStepCount[s] <= 0) && !otherSideActive;

    // ★★ activeAssistSide 업데이트
    if (inProfileZone && activeAssistSide == SIDE_COUNT) {
      activeAssistSide = s;
      Serial.print(s == SIDE_LEFT ? "🔵 LEFT" : "🔴 RIGHT");
      Serial.println(" Force Assist STARTED (exclusive)");
    }
    if (!gcpInProfileZone && activeAssistSide == s) {
      activeAssistSide = SIDE_COUNT;
      Serial.print(s == SIDE_LEFT ? "🔵 LEFT" : "🔴 RIGHT");
      Serial.println(" Force Assist ENDED");
    }

    // ★★ 스텝 카운터 감소
    if (skipStepCount[s] > 0 && gcp > GCP_FORCE_RELEASE && gcp < 1.0f) {
      skipStepCount[s]--;
      Serial.print(s == SIDE_LEFT ? "🦵 LEFT" : "🦵 RIGHT");
      Serial.print(" step skipped (");
      Serial.print(skipStepCount[s]);
      Serial.println(" remaining) - Force Assist after warmup");
    }

    float F_cmd;
    float M, C, max_vel, max_accel;

    // ═══════════════════════════════════════════════════════════════
    // ZONE 1: Assist Zone (ONSET ~ RELEASE) - Force Profile
    // ★ 2단계 Admittance 계수:
    //   ONSET → PEAK  : Assist 계수 (상승 추종)
    //   PEAK  → RELEASE: Falling 계수 (빠른 하강 추종)
    // ═══════════════════════════════════════════════════════════════
    if (inProfileZone) {
      F_cmd = computeForceProfile_4Point(gcp, PEAK_FORCE_N);

      if (isInFallingZone(gcp)) {
        // ── PEAK → RELEASE: Falling (빠른 payout) ──
        M = adm_M_falling;
        C = adm_C_falling;
        max_vel = MAX_ADM_VEL_FALLING;
        max_accel = MAX_ADM_ACCEL_FALLING;
      } else {
        // ── ONSET → PEAK: Assist (상승 추종) ──
        M = adm_M_assist;
        C = adm_C_assist;
        max_vel = MAX_ADM_VEL_ASSIST;
        max_accel = MAX_ADM_ACCEL_ASSIST;
      }
    }
    // ═══════════════════════════════════════════════════════════════
    // ZONE 2: GCP 비활성 (첫 스텝 전) - One-shot Pretension
    // ═══════════════════════════════════════════════════════════════
    else if (!gcpActiveOf(s)) {
      if (!pretensionDone[s]) {
        F_cmd = MIN_TENSION_N;
        M = adm_M_slack;
        C = adm_C_slack;
        max_vel = MAX_ADM_VEL_SLACK;
        max_accel = MAX_ADM_ACCEL_SLACK;

        // ★★★ Force-based Pretension 완료 판정 ★★★
        if (F_meas >= MIN_TENSION_N) {
          pretensionSettleCount[s]++;
          if (pretensionSettleCount[s] >= PRETENSION_SETTLE_TICKS) {
            pretensionDone[s] = true;
            pretensionSettleCount[s] = 0;
          }
        } else {
          pretensionSettleCount[s] = 0;
        }
      } else {
        desiredForce_N[s] = 0.0f;
        desiredCurrent_A[s] = 0.0f;
        desiredVelocity_mps[s] = 0.0f;
        adm_velocity_mps[s] = 0.0f;
        dbg_F_assist[s] = 0.0f;
        dbg_F_cmd[s] = 0.0f;
        dbg_F_err[s] = 0.0f;
        dbg_des_erpm[s] = 0.0f;
        return;
      }
    }
    // ═══════════════════════════════════════════════════════════════
    // ZONE 3: Pre-Onset (HS 0% ~ ONSET) — 2단계
    //
    //   ramp_start = ONSET - 0.10 (ONSET 10% 전)
    //
    //   Zone 3a [0% ~ ramp_start): F=0 + TFF sine (additive)
    //     TFF: HS→ONSET sine (0→peak at ONSET)
    //   Zone 3b [ramp_start ~ ONSET): TFF sine 계속 + F_cmd 선형 증가
    //     F_cmd: 0N → 5N (ramp_start에서 ONSET까지)
    //
    //   ONSET 도달 시: F=5N, TFF=0 → Profile에 장력 잡힌 채 진입
    // ═══════════════════════════════════════════════════════════════
    else if (gcp < GCP_FORCE_ONSET) {
      if (pretensionDone[s]) {
        pretensionDone[s] = false;
        pretensionStartTime_ms[s] = millis();
      }

      const float FORCE_RAMP_RANGE = 0.10f;
      float ramp_start = GCP_FORCE_ONSET - FORCE_RAMP_RANGE;

      if (gcp < ramp_start) {
        // Zone 3a: TFF 선형 감소, F=0
        F_cmd = 0.0f;
      } else {
        // Zone 3b: Force 선형 증가 0→5N
        float progress = (gcp - ramp_start) / FORCE_RAMP_RANGE;  // 0→1
        F_cmd = MIN_TENSION_N * progress;
      }
      M = adm_M_slack;
      C = adm_C_slack;
      max_vel = MAX_ADM_VEL_SLACK;
      max_accel = MAX_ADM_ACCEL_SLACK;
    }
    // ═══════════════════════════════════════════════════════════════
    // ZONE 4: Post-Release (85%→100%) - Zero Current
    // ★ Release 이후 HS 전까지 전류 0 (모터 프리휠)
    // ═══════════════════════════════════════════════════════════════
    else {
      desiredForce_N[s] = 0.0f;
      desiredCurrent_A[s] = 0.0f;
      desiredVelocity_mps[s] = 0.0f;
      adm_velocity_mps[s] = 0.0f;
      dbg_F_assist[s] = 0.0f;
      dbg_F_cmd[s] = 0.0f;
      dbg_F_err[s] = 0.0f;
      dbg_des_erpm[s] = 0.0f;
      return;
    }

    // ═══════════════════════════════════════════════════════════════
    // COMMON: 단일 Admittance 경로 + Feedforward (모든 Zone 공통)
    //   Zone 1 (Profile):    F_cmd=profile + I_ff + Motion FF, TFF=0
    //   Zone 2 (Pretension): F_cmd=5N, TFF=0
    //   Zone 3a (TFF):       F_cmd=0 + TFF sine (HS→ONSET peak, additive)
    //   Zone 3b (Ramp):      F_cmd 0→5N 선형 증가 + TFF sine (ONSET-10% → ONSET)
    //   Zone 4 (Post-Release): F_cmd=0N, TFF=0 (ZFC)
    // ═══════════════════════════════════════════════════════════════
    bool inStance = ((s == SIDE_LEFT) ? left_Detector.getPhase() : right_Detector.getPhase()) == GaitDetector::STANCE;

    // ★★★ Profile 진입 리셋 ★★★
    if (inProfileZone && !wasInProfile[s]) {
      adm_velocity_mps[s] = 0.0f;
      pidOf(s).reset();
    }

    wasInProfile[s] = inProfileZone;

    // ─────────────────────────────────────────────────────────────
    // UNIFIED: Admittance + Feedforward (PATH A/B 통합)
    // ─────────────────────────────────────────────────────────────
    {
      desiredForce_N[s] = F_cmd;
      dbg_F_assist[s] = inProfileZone ? F_cmd : 0.0f;
      dbg_F_cmd[s] = F_cmd;

      if (M < 1e-2f) M = 1e-2f;
      float F_err = F_cmd - F_meas;

      // ★★ I_ff: Profile 구간 F_cmd 비례 전류 보상
      float I_ff = 0.0f;
      if (inProfileZone && FF_GAIN_F > 0.0f) {
        I_ff = FF_GAIN_F * F_cmd;
      }

      // ★★ Motion FF: Profile 구간에서만 IMU velocity 기반 보상
      float v_motion_ff = 0.0f;
      if (inProfileZone && FF_GAIN_MOTION > 0.0f) {
        float vel_x = snapOf(s).ax;
        float vel_y = snapOf(s).ay;
        float vel_norm = sqrtf(vel_x * vel_x + vel_y * vel_y);
        v_motion_ff = FF_GAIN_MOTION * vel_norm;
      }

      // ★★ Treadmill FF: HS → ONSET-10% half sine
      //
      //  v_tff
      //    0 ──╮                     ╭── 0     → GCP
      //        ╲                   ╱
      //         ╲  sin(π×t)      ╱
      //          ╲             ╱
      //           ╰───────────╯ peak (at 25%)
      //    HS(0%)          ramp_start(50%)  ONSET(60%)
      //
      //  tff_end = ONSET - 10% = ramp_start
      //  [0, tff_end]: -1.0 × v_belt × gain × sin(π × gcp/tff_end)
      //  > tff_end:    0
      //  peak at gcp = tff_end/2 (25%), 양 끝에서 매끄럽게 0
      //
      const float tff_end = GCP_FORCE_ONSET - TFF_END_OFFSET;  // ONSET에서 TFF_END_OFFSET% 전
      float v_treadmill_ff = 0.0f;
      if (gcpActiveOf(s) && skipStepCount[s] <= 0) {
        if (gcp <= tff_end) {
          float t = gcp / tff_end;  // 0→1 (HS→tff_end)
          v_treadmill_ff = -1.0f * treadmill_speed_mps * TFF_GAIN * sinf(3.1415927f * t);
        }
      }

      // Admittance Dynamics
      float dv_dt = (F_err - C * adm_velocity_mps[s]) / M;
      dv_dt = clampf(dv_dt, -max_accel, max_accel);

      adm_velocity_mps[s] += dv_dt * dt;

      float v_total = adm_velocity_mps[s] + v_motion_ff + v_treadmill_ff;

      // ★ Rising (ONSET→PEAK): 풀림 방지 — 양수(당김) 방향만 허용
      if (inProfileZone && !isInFallingZone(gcp)) {
        if (v_total < 0.0f) v_total = 0.0f;
        if (adm_velocity_mps[s] < 0.0f) adm_velocity_mps[s] = 0.0f;
      }

      // ★ Slew Rate Limiter: 출력 속도의 급격한 변화 방지
      // Admittance가 F_err에 자유롭게 반응하되, 한 tick당 max_dv까지만 변화 허용
      float max_dv = max_accel * dt;
      float prev_v = desiredVelocity_mps[s];
      v_total = clampf(v_total, prev_v - max_dv, prev_v + max_dv);

      v_total = clampf(v_total, -max_vel, max_vel);

      adm_position_m[s] += v_total * dt;
      desiredVelocity_mps[s] = v_total;
    // ★ FF Velocity 로깅 캡처
    dbg_adm_vel_mps[s] = adm_velocity_mps[s];
    dbg_motion_ff_mps[s] = v_motion_ff;
    dbg_treadmill_ff_mps[s] = v_treadmill_ff;

      float desired_erpm = mps_to_erpm(desiredVelocity_mps[s]);
      desired_erpm = clampf(desired_erpm, -MAX_VEL_ERPM, MAX_VEL_ERPM);
      float I_pid = pidOf(s).compute(desired_erpm, motor_velocity_erpm[s], dt);

      desiredCurrent_A[s] = clampf(I_pid + I_ff, -MAX_CURRENT_A, MAX_CURRENT_A);

      dbg_F_err[s] = F_err;
      dbg_des_erpm[s] = desired_erpm;
    }
  }
  // ═══════════════════════════════════════════════════════════════
  // MODE 1: Position Assist (HS 기준 GCP + 1/4 Sine Cascade PID)
  // ═══════════════════════════════════════════════════════════════
  else {
    // ★ 초기 위치 업데이트는 positionLoopStep()에서 수행됨
    // ★ GCP 0%: HO 순간 위치 저장
    // ★ GCP 0~10%: 위치 유지 (offset = 0)
    // ★ GCP 10~70%: 1/4 sine으로 당김
    // ★ GCP 70~100%: Pretension 제어

    // -------- Position Control 구간 (GCP > 0 ~ GCP_POS_END) --------
    // HO 발생 후 ~ 70%까지 위치 제어
    if (gcp > 0.0f && gcp <= GCP_POS_END) {

      // desiredVelocity_erpm[s]는 positionLoopStep에서 계산됨 (111Hz)
      float desired_erpm = desiredVelocity_erpm[s];
      desired_erpm = clampf(desired_erpm, -MAX_VEL_ERPM, MAX_VEL_ERPM);

      desiredCurrent_A[s] = pidOf(s).compute(desired_erpm, motor_velocity_erpm[s], dt);
      desiredVelocity_mps[s] = erpm_to_mps(desired_erpm);

      dbg_F_assist[s] = 0.0f;
      dbg_F_cmd[s] = 0.0f;
      dbg_F_err[s] = 0.0f;
      dbg_des_erpm[s] = desired_erpm;

      float F_meas = loadcellRaw_N[s];
      actualForce_N[s] = F_meas;
      desiredForce_N[s] = 0.0f;

    }
    // -------- Pretension 구간 (GCP = 0 또는 > 70%) --------
    else {
      // GCP 사이클 전 또는 Release 구간
      positionOffset_deg[s] = 0.0f;
      dbg_pos_offset[s] = 0.0f;
      dbg_pos_err[s] = 0.0f;

      float F_cmd = MIN_TENSION_N;

      dbg_F_assist[s] = 0.0f;
      dbg_F_cmd[s] = F_cmd;

      float F_meas = loadcellRaw_N[s];
      actualForce_N[s] = F_meas;
      desiredForce_N[s] = F_cmd;

      // Admittance Control for pretension
      float M = adm_M;
      if (M < 1e-4f) M = 1e-4f;
      float C = adm_C;
      float F_err = desiredForce_N[s] - actualForce_N[s];

      float dv_dt = (F_err - C * adm_velocity_mps[s]) / M;
      dv_dt = clampf(dv_dt, -MAX_ADM_ACCEL_MPS2, MAX_ADM_ACCEL_MPS2);

      adm_velocity_mps[s] += dv_dt * dt;
      adm_velocity_mps[s] = clampf(adm_velocity_mps[s], -MAX_ADM_VELOCITY_MPS, MAX_ADM_VELOCITY_MPS);
      adm_position_m[s] += adm_velocity_mps[s] * dt;

      desiredVelocity_mps[s] = adm_velocity_mps[s];

      float desired_erpm = mps_to_erpm(desiredVelocity_mps[s]);
      desired_erpm = clampf(desired_erpm, -MAX_VEL_ERPM, MAX_VEL_ERPM);
      desiredCurrent_A[s] = pidOf(s).compute(desired_erpm, motor_velocity_erpm[s], dt);

      dbg_F_err[s] = F_err;
      dbg_des_erpm[s] = desired_erpm;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // Safety Check 1: Position Limit (공통)
  // ═══════════════════════════════════════════════════════════════
  if (initialPositionSet[s]) {
    float posDiff = fabs(motor_position_deg[s] - initialPosition_deg[s]);
    if (posDiff > POSITION_LIMIT_DEG) {
      motorEnabled = false;
      safetyTriggered[s] = true;
      desiredCurrent_A[0] = 0.0f;
      desiredCurrent_A[1] = 0.0f;
      safetyPosDiff_deg = posDiff;
      safetySidePending = (uint8_t)s;
      safetyPrintPending = true;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // Safety Check 2: Cable Tangle Detection
  // ★ Payout 방향(음의 ERPM) 회전 중 Force > 50N → 케이블 꼬임
  //   정상: Payout → Force 감소 | 비정상: Payout → Force 증가 (꼬임)
  //   3 consecutive ticks (9ms) 확인 후 Safety 정지
  // ═══════════════════════════════════════════════════════════════
  if (motorEnabled &&
      motor_velocity_erpm[s] < -SAFETY_PAYOUT_ERPM &&
      loadcellRaw_N[s] > SAFETY_PAYOUT_FORCE_N) {
    safetyTangleCount[s]++;
    if (safetyTangleCount[s] >= SAFETY_TANGLE_TICKS) {
      motorEnabled = false;
      safetyTriggered[s] = true;
      desiredCurrent_A[0] = 0.0f;
      desiredCurrent_A[1] = 0.0f;
      safetyTanglePending = true;
      safetyTangleSide = (uint8_t)s;
      safetyTangleForce = loadcellRaw_N[s];
      safetyTangleErpm = motor_velocity_erpm[s];
    }
  } else {
    safetyTangleCount[s] = 0;
  }
}

// ################################################################
// ##                                                            ##
// ##  SECTION P: 인터럽트 서비스 루틴 (ISR)                      ##
// ##                                                            ##
// ################################################################

IntervalTimer timerCtrl;
IntervalTimer timer1k;

// ================================================================
// [P-1] Control ISR (333Hz)
// ================================================================

void ISR_Control() {
  uint32_t now_us = micros();
  isr_now_ms = now_us / 1000;  // ★ getGCP()가 사용할 현재 시간 (ms)

  // dt 계산
  if (lastTimeCtl_us > 0) {
    uint32_t elapsed = now_us - lastTimeCtl_us;
    dt_ctl = elapsed / 1000000.0f;
    if (dt_ctl <= 0.0f || dt_ctl > 0.05f) dt_ctl = (float)CONTROL_PERIOD_MS * 0.001f;
  } else {
    dt_ctl = (float)CONTROL_PERIOD_MS * 0.001f;
  }
  lastTimeCtl_us = now_us;

  // ★ Position Loop: 9ms마다 (3번에 1번) - 111Hz
  posTickCounter++;
  if (posTickCounter >= POS_TICK_DIVISOR) {
    posTickCounter = 0;

    if (motorEnabled && currentMode == MODE_POSITION_ASSIST) {
      positionLoopStep(SIDE_LEFT, dt_pos);
      positionLoopStep(SIDE_RIGHT, dt_pos);
    }
  }

  // Velocity Loop: 3ms마다 - 333Hz
  controllerStep(SIDE_LEFT, dt_ctl);
  controllerStep(SIDE_RIGHT, dt_ctl);

  // ★ Logging: 9ms마다 (3번에 1번)
  logTickCounter++;
  if (logTickCounter >= LOG_TICK_DIVISOR) {
    logTickCounter = 0;

    if (isLogging) {
      LogEntry e;
      e.timestamp_ms = (now_us - logStartTime_us) / 1000;
      e.freq_hz = (dt_ctl > 0.0f) ? (1000.0f / LOG_PERIOD_MS) : 0.0f;

      // Left
      e.L_des_force = desiredForce_N[SIDE_LEFT];
      e.L_act_force = actualForce_N[SIDE_LEFT];
      e.L_err_force = desiredForce_N[SIDE_LEFT] - actualForce_N[SIDE_LEFT];
      e.L_des_vel_mps = desiredVelocity_mps[SIDE_LEFT];
      e.L_act_vel_mps = actualVelocity_mps[SIDE_LEFT];
      e.L_err_vel_mps = desiredVelocity_mps[SIDE_LEFT] - actualVelocity_mps[SIDE_LEFT];
      e.L_des_pos_deg = (currentMode == MODE_POSITION_ASSIST) ? desiredPosition_deg[SIDE_LEFT] : cable_m_to_deg(adm_position_m[SIDE_LEFT]);
      e.L_act_pos_deg = motor_position_deg[SIDE_LEFT];
      e.L_err_pos_deg = e.L_des_pos_deg - e.L_act_pos_deg;
      e.L_des_curr = desiredCurrent_A[SIDE_LEFT];
      e.L_act_curr = motor_current_a[SIDE_LEFT];
      e.L_err_curr = desiredCurrent_A[SIDE_LEFT] - motor_current_a[SIDE_LEFT];
      e.L_pos_integral = positionPID_L.integral;
      e.L_vel_integral = velocityPID_L.integral;

      // Right
      e.R_des_force = desiredForce_N[SIDE_RIGHT];
      e.R_act_force = actualForce_N[SIDE_RIGHT];
      e.R_err_force = desiredForce_N[SIDE_RIGHT] - actualForce_N[SIDE_RIGHT];
      e.R_des_vel_mps = desiredVelocity_mps[SIDE_RIGHT];
      e.R_act_vel_mps = actualVelocity_mps[SIDE_RIGHT];
      e.R_err_vel_mps = desiredVelocity_mps[SIDE_RIGHT] - actualVelocity_mps[SIDE_RIGHT];
      e.R_des_pos_deg = (currentMode == MODE_POSITION_ASSIST) ? desiredPosition_deg[SIDE_RIGHT] : cable_m_to_deg(adm_position_m[SIDE_RIGHT]);
      e.R_act_pos_deg = motor_position_deg[SIDE_RIGHT];
      e.R_err_pos_deg = e.R_des_pos_deg - e.R_act_pos_deg;
      e.R_des_curr = desiredCurrent_A[SIDE_RIGHT];
      e.R_act_curr = motor_current_a[SIDE_RIGHT];
      e.R_err_curr = desiredCurrent_A[SIDE_RIGHT] - motor_current_a[SIDE_RIGHT];
      e.R_pos_integral = positionPID_R.integral;
      e.R_vel_integral = velocityPID_R.integral;

      COPY_SNAPSHOT(e.imuL, snapL);
      COPY_SNAPSHOT(e.imuR, snapR);

      // ★ FF Velocity 로깅
      e.L_adm_vel_mps = dbg_adm_vel_mps[SIDE_LEFT];
      e.L_motion_ff_mps = dbg_motion_ff_mps[SIDE_LEFT];
      e.L_treadmill_ff_mps = dbg_treadmill_ff_mps[SIDE_LEFT];
      e.R_adm_vel_mps = dbg_adm_vel_mps[SIDE_RIGHT];
      e.R_motion_ff_mps = dbg_motion_ff_mps[SIDE_RIGHT];
      e.R_treadmill_ff_mps = dbg_treadmill_ff_mps[SIDE_RIGHT];

      // ★ FF Gains
      e.tff_gain = TFF_GAIN;
      e.ff_gain_f = FF_GAIN_F;

      e.a7 = syncA7;
      e.mode = (uint8_t)currentMode;
      e.mark = currentMark;

      uint32_t nextHead = (logHead + 1) % RING_BUFFER_SIZE;
      if (nextHead != logTail) {
        logBuffer[logHead] = e;
        logHead = nextHead;
      }
    }
  }
}

// ================================================================
// [P-2] Current Command ISR (1kHz)
// ================================================================

void ISR_Current1kHz() {
  float I_L = motorEnabled ? desiredCurrent_A[SIDE_LEFT] : 0.0f;
  float I_R = motorEnabled ? desiredCurrent_A[SIDE_RIGHT] : 0.0f;
  sendCurrentCommand(MOTOR_LEFT_CAN_ID, I_L);
  sendCurrentCommand(MOTOR_RIGHT_CAN_ID, I_R);
}

// ################################################################
// ##                                                            ##
// ##  SECTION P-3: 모터 Enable/Disable 공통 함수                 ##
// ##                                                            ##
// ################################################################

/**
 * @brief 모터 Enable 시 공통 초기화
 * Serial 'e' 명령과 BLE 'e' 명령 모두에서 호출됨.
 * motorEnabled = true 설정 후 호출할 것.
 */
void initMotorEnable() {
  for (int i = 0; i < SIDE_COUNT; i++) {
    adm_velocity_mps[i] = 0.0f;
    adm_position_m[i] = deg_to_cable_m(motor_position_deg[i]);
    desiredVelocity_erpm[i] = 0.0f;
    positionOffset_deg[i] = 0.0f;
    wasInProfile[i] = false;

    // ★★★ One-shot Pretension 시작 ★★★
    pretensionDone[i] = false;
    pretensionStartTime_ms[i] = millis();
  }

  velocityPID_L.reset();
  velocityPID_R.reset();
  positionPID_L.reset();
  positionPID_R.reset();

  left_Detector.reset();
  right_Detector.reset();

  activeAssistSide = SIDE_COUNT;

  // ★ GCP Start Mode 설정 (HS 기준)
  left_Detector.gcpStartMode = GCP_START_AT_HS;
  right_Detector.gcpStartMode = GCP_START_AT_HS;

  prev_gcp[SIDE_LEFT] = 0.0f;
  prev_gcp[SIDE_RIGHT] = 0.0f;

  prev_gcp_pos[SIDE_LEFT] = 0.0f;
  prev_gcp_pos[SIDE_RIGHT] = 0.0f;

  firstHSDone = false;
  firstHODone = false;

  skipStepCount[SIDE_LEFT] = SKIP_STEP_COUNT;
  skipStepCount[SIDE_RIGHT] = SKIP_STEP_COUNT;

  lastHSSide = SIDE_RIGHT;
  lastHOSide = SIDE_RIGHT;

  initialPosition_deg[SIDE_LEFT] = motor_position_deg[SIDE_LEFT];
  initialPosition_deg[SIDE_RIGHT] = motor_position_deg[SIDE_RIGHT];
  initialPositionSet[SIDE_LEFT] = true;
  initialPositionSet[SIDE_RIGHT] = true;
}

/**
 * @brief 모터 Disable 시 공통 처리
 */
void disableMotors() {
  desiredCurrent_A[SIDE_LEFT] = 0.0f;
  desiredCurrent_A[SIDE_RIGHT] = 0.0f;
}

// ################################################################
// ##                                                            ##
// ##  SECTION Q: Serial 명령어 처리                              ##
// ##                                                            ##
// ################################################################

// ================================================================
// [Q-1] Help 출력
// ================================================================

void printHelp() {
  Serial.println("\n═══════════════════════════════════════════════════════════");
  Serial.println("              INTEGRATED GCP FIRMWARE COMMANDS              ");
  Serial.println("═══════════════════════════════════════════════════════════");

  Serial.println("\n=== 기본 명령어 ===");
  Serial.println("e            : Enable/Disable motors (L+R)");
  Serial.println("s            : Start/Stop logging (SD)");
  Serial.println("d            : Toggle debug prints");
  Serial.println("c            : IMU recalibration");
  Serial.println("r            : Safety reset (L+R)");
  Serial.println("z            : Zero motor position (set origin)");
  Serial.println("g            : Print all parameters");
  Serial.println("h            : Help");
  Serial.println("stream       : Toggle USB Serial data streaming (111Hz)");

  Serial.println("\n=== 모드 선택 ===");
  Serial.println("mode0        : Force Assist (HS 기준 GCP, Onset-Peak-Release)");
  Serial.println("mode1        : Position Assist (HS 기준 GCP, 1/4 Sine)");

  Serial.println("\n=== Force Assist 파라미터 (Mode 0) ===");
  Serial.println("fo<val>      : Force Onset GCP (default 0.10)");
  Serial.println("fp<val>      : Force Peak GCP (default 0.35)");
  Serial.println("fr<val>      : Force Release GCP (default 0.60)");
  Serial.println("pf<val>      : Peak force [N] (default 60)");
  Serial.println("pt<val>      : Pretension force [N] (default 10)");
  Serial.println("m<val>       : Admittance M [kg]");
  Serial.println("ad<val>      : Admittance C [N*s/m]");

  Serial.println("\n=== Position Assist 파라미터 (Mode 1) ===");
  Serial.println("ps<val>      : Position Start GCP (default 0.10)");
  Serial.println("pe<val>      : Position End GCP (default 0.70)");
  Serial.println("pa<val>      : Position amplitude [deg] (default 600)");
  Serial.println("pp<val>      : Position PID Kp");
  Serial.println("pi<val>      : Position PID Ki");
  Serial.println("pd<val>      : Position PID Kd");

  Serial.println("\n=== Velocity PID (공통) ===");
  Serial.println("vp<val>      : Velocity PID Kp");
  Serial.println("vi<val>      : Velocity PID Ki");
  Serial.println("vd<val>      : Velocity PID Kd");

  Serial.println("\n═══════════════════════════════════════════════════════════\n");
}

// ================================================================
// [Q-2] 파라미터 출력
// ================================================================

void printGains() {
  Serial.println("\n═══════════════════════════════════════════════════════════");
  Serial.println("                    CURRENT PARAMETERS                      ");
  Serial.println("═══════════════════════════════════════════════════════════");

  Serial.println("\n=== 현재 모드 ===");
  Serial.print("Mode: ");
  if (currentMode == MODE_FORCE_ASSIST) {
    Serial.println("0 (Force Assist - HS 기준 GCP, Onset-Peak-Release)");
  } else {
    Serial.println("1 (Position Assist - HS 기준 GCP, 1/4 Sine)");
  }

  Serial.println("\n=== 교대(Alternation) 상태 ===");
  Serial.print("lastHSSide: ");
  Serial.println(lastHSSide == SIDE_LEFT ? "LEFT" : "RIGHT");
  Serial.print("lastHOSide: ");
  Serial.println(lastHOSide == SIDE_LEFT ? "LEFT" : "RIGHT");

  Serial.println("\n=== Step Time ===");
  Serial.print("L avg_step_time: ");
  Serial.print(left_Detector.getAvgStepTime(), 3);
  Serial.println(" sec");
  Serial.print("R avg_step_time: ");
  Serial.print(right_Detector.getAvgStepTime(), 3);
  Serial.println(" sec");

  Serial.println("\n=== 타이밍 ===");
  Serial.print("Velocity Loop: ");
  Serial.print(CONTROL_PERIOD_MS);
  Serial.println("ms (333Hz)");
  Serial.print("Position Loop: ");
  Serial.print(POSITION_PERIOD_MS);
  Serial.println("ms (111Hz)");
  Serial.println("Current Cmd:   1ms (1kHz)");

  Serial.println("\n=== Force Assist 파라미터 ===");
  Serial.print("GCP_FORCE_ONSET: ");
  Serial.println(GCP_FORCE_ONSET, 2);
  Serial.print("GCP_FORCE_PEAK: ");
  Serial.println(GCP_FORCE_PEAK, 2);
  Serial.print("GCP_FORCE_RELEASE: ");
  Serial.println(GCP_FORCE_RELEASE, 2);
  Serial.print("PEAK_FORCE_N: ");
  Serial.println(PEAK_FORCE_N, 2);
  Serial.print("MIN_TENSION_N: ");
  Serial.println(MIN_TENSION_N, 2);
  Serial.print("adm_M: ");
  Serial.println(adm_M, 4);
  Serial.print("adm_C: ");
  Serial.println(adm_C, 4);

  Serial.println("\n=== Position Assist 파라미터 ===");
  Serial.print("GCP_POS_START: ");
  Serial.println(GCP_POS_START, 2);
  Serial.print("GCP_POS_END: ");
  Serial.println(GCP_POS_END, 2);
  Serial.print("POSITION_AMPLITUDE_DEG: ");
  Serial.println(POSITION_AMPLITUDE_DEG, 2);
  Serial.print("PositionPID(L): Kp=");
  Serial.print(positionPID_L.kp, 4);
  Serial.print(" Ki=");
  Serial.print(positionPID_L.ki, 6);
  Serial.print(" Kd=");
  Serial.println(positionPID_L.kd, 6);
  Serial.print("PositionPID(R): Kp=");
  Serial.print(positionPID_R.kp, 4);
  Serial.print(" Ki=");
  Serial.print(positionPID_R.ki, 6);
  Serial.print(" Kd=");
  Serial.println(positionPID_R.kd, 6);

  Serial.println("\n=== Velocity PID ===");
  Serial.print("VelocityPID(L): Kp=");
  Serial.print(velocityPID_L.kp, 10);
  Serial.print(" Ki=");
  Serial.print(velocityPID_L.ki, 10);
  Serial.print(" Kd=");
  Serial.println(velocityPID_L.kd, 10);
  Serial.print("VelocityPID(R): Kp=");
  Serial.print(velocityPID_R.kp, 10);
  Serial.print(" Ki=");
  Serial.print(velocityPID_R.ki, 10);
  Serial.print(" Kd=");
  Serial.println(velocityPID_R.kd, 10);

  Serial.println("\n=== Safety ===");
  Serial.print("SafetyTriggered L/R: ");
  Serial.print(safetyTriggered[SIDE_LEFT] ? "1" : "0");
  Serial.print("/");
  Serial.println(safetyTriggered[SIDE_RIGHT] ? "1" : "0");

  Serial.println("\n═══════════════════════════════════════════════════════════\n");
}

// ================================================================
// [Q-3] 명령어 처리
// ================================================================

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  // 기본 명령어
  if (cmd == "h") {
    printHelp();
    return;
  }
  if (cmd == "g") {
    printGains();
    return;
  }
  if (cmd == "d") {
    show_debug = !show_debug;
    Serial.print("Debug: ");
    Serial.println(show_debug ? "ON" : "OFF");
    return;
  }
  if (cmd == "c") {
    left_IMU.calibrate();
    right_IMU.calibrate();
    return;
  }
  if (cmd == "r") {
    safetyTriggered[SIDE_LEFT] = false;
    safetyTriggered[SIDE_RIGHT] = false;
    initialPositionSet[SIDE_LEFT] = false;
    initialPositionSet[SIDE_RIGHT] = false;
    safetyTangleCount[SIDE_LEFT] = 0;
    safetyTangleCount[SIDE_RIGHT] = 0;
    Serial.println("✅ Safety reset (L+R)");
    return;
  }
  if (cmd == "z") {
    if (motorEnabled) {
      Serial.println("⚠️ Disable motors first before zeroing!");
      return;
    }
    setAllMotorsOrigin();
    return;
  }

  // 모드 변경
  if (cmd == "mode0") {
    if (motorEnabled) {
      Serial.println("⚠️ Disable motors first before changing mode!");
      return;
    }
    currentMode = MODE_FORCE_ASSIST;
    left_Detector.gcpStartMode = GCP_START_AT_HS;
    right_Detector.gcpStartMode = GCP_START_AT_HS;
    Serial.println("✅ Mode: 0 (Force Assist - HS 기준 GCP, Onset-Peak-Release)");
    return;
  }
  if (cmd == "mode1") {
    if (motorEnabled) {
      Serial.println("⚠️ Disable motors first before changing mode!");
      return;
    }
    currentMode = MODE_POSITION_ASSIST;
    left_Detector.gcpStartMode = GCP_START_AT_HS;
    right_Detector.gcpStartMode = GCP_START_AT_HS;
    Serial.println("✅ Mode: 1 (Position Assist - HS 기준 GCP, 1/4 Sine)");
    return;
  }

  // 모터 Enable/Disable
  if (cmd == "e") {
    if (safetyTriggered[SIDE_LEFT] || safetyTriggered[SIDE_RIGHT]) {
      Serial.println("⚠️ Safety triggered! Reset with 'r' first");
      return;
    }

    motorEnabled = !motorEnabled;

    if (motorEnabled) {
      initMotorEnable();
      Serial.println("✅ Motors ENABLED (L+R)");
      Serial.print("📍 InitPos L/R: ");
      Serial.print(initialPosition_deg[SIDE_LEFT], 2);
      Serial.print(" / ");
      Serial.println(initialPosition_deg[SIDE_RIGHT], 2);
      Serial.print("🔧 Mode: ");
      if (currentMode == MODE_FORCE_ASSIST) {
        Serial.println("Force Assist (HS GCP)");
        Serial.print("📊 Force Range: ");
        Serial.print(GCP_FORCE_ONSET, 2);
        Serial.print(" → ");
        Serial.print(GCP_FORCE_PEAK, 2);
        Serial.print(" → ");
        Serial.println(GCP_FORCE_RELEASE, 2);
      } else {
        Serial.println("Position Assist (HS GCP)");
        Serial.print("📊 Position Range: ");
        Serial.print(GCP_POS_START, 2);
        Serial.print(" ~ ");
        Serial.println(GCP_POS_END, 2);
      }
    } else {
      disableMotors();
      Serial.println("❌ Motors DISABLED (L+R)");
    }
    return;
  }

  // 로깅
  if (cmd == "s") {
    if (!isLogging) startLogging();
    else stopLogging();
    return;
  }

  // Force Assist 구간 설정
  if (cmd.startsWith("fo")) {
    noInterrupts();
    GCP_FORCE_ONSET = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("GCP_FORCE_ONSET = " + String(GCP_FORCE_ONSET, 2));
    return;
  }
  if (cmd.startsWith("fp") && cmd.length() > 2 && !cmd.startsWith("pf")) {
    noInterrupts();
    GCP_FORCE_PEAK = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("GCP_FORCE_PEAK = " + String(GCP_FORCE_PEAK, 2));
    return;
  }
  if (cmd.startsWith("fr")) {
    noInterrupts();
    GCP_FORCE_RELEASE = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("GCP_FORCE_RELEASE = " + String(GCP_FORCE_RELEASE, 2));
    return;
  }

  // Position Assist 구간 설정
  if (cmd.startsWith("ps") && cmd.length() > 2) {
    noInterrupts();
    GCP_POS_START = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("GCP_POS_START = " + String(GCP_POS_START, 2));
    return;
  }
  if (cmd.startsWith("pe") && cmd.length() > 2) {
    noInterrupts();
    GCP_POS_END = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("GCP_POS_END = " + String(GCP_POS_END, 2));
    return;
  }

  // Velocity PID
  if (cmd.startsWith("vp")) {
    float v = cmd.substring(2).toFloat();
    noInterrupts();
    velocityPID_L.kp = v;
    velocityPID_R.kp = v;
    interrupts();
    Serial.println("Velocity Kp = " + String(v, 10));
    return;
  }
  if (cmd.startsWith("vi")) {
    float v = cmd.substring(2).toFloat();
    noInterrupts();
    velocityPID_L.ki = v;
    velocityPID_R.ki = v;
    interrupts();
    Serial.println("Velocity Ki = " + String(v, 10));
    return;
  }
  if (cmd.startsWith("vd")) {
    float v = cmd.substring(2).toFloat();
    noInterrupts();
    velocityPID_L.kd = v;
    velocityPID_R.kd = v;
    interrupts();
    Serial.println("Velocity Kd = " + String(v, 10));
    return;
  }

  // Position PID
  if (cmd.startsWith("pp")) {
    float v = cmd.substring(2).toFloat();
    noInterrupts();
    positionPID_L.kp = v;
    positionPID_R.kp = v;
    interrupts();
    Serial.println("Position Kp = " + String(v, 6));
    return;
  }
  if (cmd.startsWith("pi")) {
    float v = cmd.substring(2).toFloat();
    noInterrupts();
    positionPID_L.ki = v;
    positionPID_R.ki = v;
    interrupts();
    Serial.println("Position Ki = " + String(v, 6));
    return;
  }
  if (cmd.startsWith("pd")) {
    float v = cmd.substring(2).toFloat();
    noInterrupts();
    positionPID_L.kd = v;
    positionPID_R.kd = v;
    interrupts();
    Serial.println("Position Kd = " + String(v, 6));
    return;
  }

  // Position amplitude
  if (cmd.startsWith("pa")) {
    noInterrupts();
    POSITION_AMPLITUDE_DEG = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("POSITION_AMPLITUDE_DEG = " + String(POSITION_AMPLITUDE_DEG, 2));
    return;
  }

  // Force params
  if (cmd.startsWith("pf")) {
    noInterrupts();
    PEAK_FORCE_N = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("PEAK_FORCE_N = " + String(PEAK_FORCE_N, 2));
    return;
  }
  // ★ ft: 최소 장력 설정 (Force minimum Tension)
  if (cmd.startsWith("ft")) {
    noInterrupts();
    MIN_TENSION_N = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("MIN_TENSION_N = " + String(MIN_TENSION_N, 2) + " N");
    return;
  }
  // ★ ff: Feedforward 게인 조절 (F_cmd 비례)
  if (cmd.startsWith("ff")) {
    noInterrupts();
    FF_GAIN_F = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("FF_GAIN_F = " + String(FF_GAIN_F, 4));
    return;
  }
  // ★ fm: Motion Feedforward 게인 (Global Velocity norm 비례)
  if (cmd.startsWith("fm")) {
    noInterrupts();
    FF_GAIN_MOTION = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("FF_GAIN_MOTION = " + String(FF_GAIN_MOTION, 4));
    return;
  }

  // Admittance params
  if (cmd.startsWith("m") && cmd.length() > 1 && !cmd.startsWith("mo")) {
    noInterrupts();
    adm_M = cmd.substring(1).toFloat();
    interrupts();
    Serial.println("M = " + String(adm_M, 4) + " kg");
    return;
  }
  if (cmd.startsWith("ad") && cmd.length() > 2) {
    noInterrupts();
    adm_C = cmd.substring(2).toFloat();
    interrupts();
    Serial.println("C = " + String(adm_C, 4) + " N*s/m");
    return;
  }

  // === TFF 파라미터 (Serial에서도 접근 가능) ===
  if (cmd.startsWith("tm") && cmd.length() > 2) {
    noInterrupts(); treadmill_speed_mps = cmd.substring(2).toFloat(); interrupts();
    Serial.print("Treadmill speed = "); Serial.print(treadmill_speed_mps, 2); Serial.println(" m/s");
    return;
  }
  if (cmd.startsWith("tg") && cmd.length() > 2) {
    noInterrupts(); TFF_GAIN = cmd.substring(2).toFloat(); interrupts();
    Serial.print("TFF Gain = "); Serial.println(TFF_GAIN, 2);
    return;
  }
  if (cmd.startsWith("te") && cmd.length() > 2) {
    noInterrupts(); TFF_END_OFFSET = cmd.substring(2).toFloat(); interrupts();
    Serial.print("TFF End Offset = "); Serial.println(TFF_END_OFFSET, 2);
    return;
  }
  // ★★★ USB Serial 스트리밍 토글 (Treadmill_main 추가) ★★★
  if (cmd == "stream") {
    serialStreamEnabled = !serialStreamEnabled;
    Serial.print("USB Serial Stream: ");
    Serial.println(serialStreamEnabled ? "ON (111Hz)" : "OFF");
    return;
  }

  Serial.println("Unknown command. Type 'h' for help");
}

// ================================================================
// [Q-3.5] USB Serial 데이터 스트리밍 (Treadmill_main 추가)
// ================================================================
// ★ BLE 코드와 완전히 독립. 동일한 SW19c 패킷을 USB Serial로 전송
// ★ GUI에서 pyserial로 수신하여 기존 data_parser.py로 바로 파싱 가능

static char serialTxBuffer[256];

void sendWalkerDataToSerial(
    float l_gcp, float r_gcp,
    float l_pitch, float r_pitch,
    float l_gyro_y, float r_gyro_y,
    float l_motor_pos, float r_motor_pos,
    float l_motor_vel, float r_motor_vel,
    float l_motor_curr, float r_motor_curr,
    float l_des_pos, float r_des_pos,
    float l_des_force, float r_des_force,
    float l_act_force, float r_act_force,
    uint32_t mark
) {
    if (!serialStreamEnabled) return;

    int len = snprintf(serialTxBuffer, sizeof(serialTxBuffer),
        "SW%dc%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn",
        WALKER_DATA_COUNT,
        (int)(l_gcp * 100.0f),
        (int)(r_gcp * 100.0f),
        (int)(l_pitch * 100.0f),
        (int)(r_pitch * 100.0f),
        (int)(l_gyro_y * 100.0f),
        (int)(r_gyro_y * 100.0f),
        (int)(l_motor_pos * 100.0f),
        (int)(r_motor_pos * 100.0f),
        (int)(l_motor_vel / 100.0f),
        (int)(r_motor_vel / 100.0f),
        (int)(l_motor_curr * 100.0f),
        (int)(r_motor_curr * 100.0f),
        (int)(l_des_pos * 100.0f),
        (int)(r_des_pos * 100.0f),
        (int)(l_des_force * 100.0f),
        (int)(r_des_force * 100.0f),
        (int)(l_act_force * 100.0f),
        (int)(r_act_force * 100.0f),
        (int)(mark * 100)
    );

    if (len > 0 && len < (int)sizeof(serialTxBuffer)) {
        Serial.write(serialTxBuffer, len);
        Serial.write('\n');
    }
}

// ================================================================
// [Q-4] Serial 스트림 처리
// ================================================================

void processSerialStreamNonBlocking() {
  static char buf[64];
  static uint8_t len = 0;
  static uint32_t last_rx_ms = 0;

  while (Serial.available()) {
    char ch = (char)Serial.read();
    last_rx_ms = millis();

    if (ch == '\r' || ch == '\n') {
      if (len > 0) {
        buf[len] = '\0';
        handleCommand(String(buf));
        len = 0;
      }
    } else {
      if (len < sizeof(buf) - 1) buf[len++] = ch;
      else len = 0;
    }
  }

  if (len > 0 && (millis() - last_rx_ms) > 50) {
    buf[len] = '\0';
    handleCommand(String(buf));
    len = 0;
  }
}

// ################################################################
// ##                                                            ##
// ##  SECTION R-0: BLE 명령어 처리                               ##
// ##                                                            ##
// ################################################################

/**
 * @brief BLE 명령 핸들러
 *
 * GUI에서 수신된 명령을 처리합니다.
 * 기존 Serial 명령과 동일한 형식을 사용합니다.
 *
 * 추가 명령:
 *   - mark: 마커 번호 증가 (CSV/BLE 동기화)
 *   - imu: IMU 캘리브레이션
 */
void handleBleCommand(String cmd) {
  cmd.trim();
  cmd.toLowerCase();  // ★ 대소문자 통일
  if (cmd.length() == 0) return;

  // ★ 디버그: 수신 명령 출력
  Serial.print("[BLE RX] '");
  Serial.print(cmd);
  Serial.println("'");

  // === Mark 명령 (BLE 전용) ===
  if (cmd == "mark") {
    currentMark++;
    Serial.print("📍 MARK: ");
    Serial.println(currentMark);
    return;
  }

  // === Enable/Disable (BLE에서 'e' 수신) ===
  if (cmd == "e") {
    if (safetyTriggered[SIDE_LEFT] || safetyTriggered[SIDE_RIGHT]) {
      Serial.println("⚠️ [BLE] Safety triggered! Reset with 'r' first");
      return;
    }

    motorEnabled = !motorEnabled;
    bleStreamEnabled = motorEnabled;  // 모터 Enable과 함께 BLE 스트리밍 시작

    if (motorEnabled) {
      initMotorEnable();
      Serial.println("✅ [BLE] Motors ENABLED + BLE Stream ON");
      sendBleResponse("MOTORS_ON");
    } else {
      disableMotors();
      bleStreamEnabled = false;
      Serial.println("❌ [BLE] Motors DISABLED + BLE Stream OFF");
      sendBleResponse("MOTORS_OFF");
    }
    return;
  }

  // === Disable Only (BLE에서 'd' 수신) ===
  if (cmd == "d") {
    motorEnabled = false;
    bleStreamEnabled = false;
    desiredCurrent_A[SIDE_LEFT] = 0.0f;
    desiredCurrent_A[SIDE_RIGHT] = 0.0f;
    Serial.println("❌ [BLE] Motors DISABLED");
    return;
  }

  // === 모드 변경 ===
  if (cmd == "mode0") {
    if (motorEnabled) {
      Serial.println("⚠️ [BLE] Disable motors first before changing mode!");
      return;
    }
    currentMode = MODE_FORCE_ASSIST;
    left_Detector.gcpStartMode = GCP_START_AT_HS;
    right_Detector.gcpStartMode = GCP_START_AT_HS;
    Serial.println("✅ [BLE] Mode: 0 (Force Assist)");
    return;
  }
  if (cmd == "mode1") {
    if (motorEnabled) {
      Serial.println("⚠️ [BLE] Disable motors first before changing mode!");
      return;
    }
    currentMode = MODE_POSITION_ASSIST;
    left_Detector.gcpStartMode = GCP_START_AT_HS;
    right_Detector.gcpStartMode = GCP_START_AT_HS;
    Serial.println("✅ [BLE] Mode: 1 (Position Assist)");
    return;
  }

  // === IMU 캘리브레이션 ===
  if (cmd == "imu") {
    left_IMU.calibrate();
    right_IMU.calibrate();
    Serial.println("✅ [BLE] IMU Calibration started");
    return;
  }

  // === Force Assist 파라미터 (★ 바운드 체크 적용) ===
  if (cmd.startsWith("gs")) {
    noInterrupts();
    GCP_FORCE_ONSET = clampf(cmd.substring(2).toFloat(), 0.0f, 1.0f);
    interrupts();
    Serial.print("[BLE] GCP_FORCE_ONSET = ");
    Serial.println(GCP_FORCE_ONSET, 2);
    return;
  }
  if (cmd.startsWith("gp")) {
    noInterrupts();
    GCP_FORCE_PEAK = clampf(cmd.substring(2).toFloat(), 0.0f, 1.0f);
    interrupts();
    Serial.print("[BLE] GCP_FORCE_PEAK = ");
    Serial.println(GCP_FORCE_PEAK, 2);
    return;
  }
  if (cmd.startsWith("ge")) {
    noInterrupts();
    GCP_FORCE_RELEASE = clampf(cmd.substring(2).toFloat(), 0.0f, 1.0f);
    interrupts();
    Serial.print("[BLE] GCP_FORCE_RELEASE = ");
    Serial.println(GCP_FORCE_RELEASE, 2);
    return;
  }
  if (cmd.startsWith("pf")) {
    noInterrupts();
    PEAK_FORCE_N = clampf(cmd.substring(2).toFloat(), 0.0f, 150.0f);
    interrupts();
    Serial.print("[BLE] PEAK_FORCE_N = ");
    Serial.println(PEAK_FORCE_N, 2);
    return;
  }
  // ★ ft: 최소 장력 설정
  if (cmd.startsWith("ft")) {
    noInterrupts();
    MIN_TENSION_N = clampf(cmd.substring(2).toFloat(), 0.0f, 30.0f);
    interrupts();
    Serial.print("[BLE] MIN_TENSION_N = ");
    Serial.println(MIN_TENSION_N, 2);
    return;
  }
  // ★ ff: Feedforward 게인 (F_cmd 비례)
  if (cmd.startsWith("ff")) {
    noInterrupts();
    FF_GAIN_F = clampf(cmd.substring(2).toFloat(), 0.0f, 3.0f);
    interrupts();
    Serial.print("[BLE] FF_GAIN_F = ");
    Serial.println(FF_GAIN_F, 4);
    return;
  }
  // ★ fm: Motion Feedforward 게인 (Global Velocity norm 비례)
  if (cmd.startsWith("fm")) {
    noInterrupts();
    FF_GAIN_MOTION = clampf(cmd.substring(2).toFloat(), 0.0f, 3.0f);
    interrupts();
    Serial.print("[BLE] FF_GAIN_MOTION = ");
    Serial.println(FF_GAIN_MOTION, 4);
    return;
  }

  // ★★ Admittance 파라미터 (바운드 체크 적용)
  // 포맷: aa<M>,<C> 예: "aa2.00,10.00"
  if (cmd.startsWith("aa")) {
    int commaIdx = cmd.indexOf(',');
    if (commaIdx > 2) {
      noInterrupts();
      adm_M_assist = clampf(cmd.substring(2, commaIdx).toFloat(), 0.1f, 10.0f);
      adm_C_assist = clampf(cmd.substring(commaIdx + 1).toFloat(), 0.5f, 50.0f);
      interrupts();
      Serial.print("[BLE] Assist: M=");
      Serial.print(adm_M_assist, 2);
      Serial.print(", C=");
      Serial.println(adm_C_assist, 2);
    }
    return;
  }
  if (cmd.startsWith("af")) {
    int commaIdx = cmd.indexOf(',');
    if (commaIdx > 2) {
      noInterrupts();
      adm_M_falling = clampf(cmd.substring(2, commaIdx).toFloat(), 0.1f, 10.0f);
      adm_C_falling = clampf(cmd.substring(commaIdx + 1).toFloat(), 0.5f, 50.0f);
      interrupts();
      Serial.print("[BLE] Falling: M=");
      Serial.print(adm_M_falling, 2);
      Serial.print(", C=");
      Serial.println(adm_C_falling, 2);
    }
    return;
  }
  if (cmd.startsWith("ak")) {
    int commaIdx = cmd.indexOf(',');
    if (commaIdx > 2) {
      noInterrupts();
      adm_M_slack = clampf(cmd.substring(2, commaIdx).toFloat(), 0.1f, 10.0f);
      adm_C_slack = clampf(cmd.substring(commaIdx + 1).toFloat(), 0.5f, 50.0f);
      interrupts();
      Serial.print("[BLE] Slack: M=");
      Serial.print(adm_M_slack, 2);
      Serial.print(", C=");
      Serial.println(adm_C_slack, 2);
    }
    return;
  }

  // === Position Assist 파라미터 (바운드 체크 적용) ===
  if (cmd.startsWith("pa") && cmd.length() > 2) {
    noInterrupts();
    POSITION_AMPLITUDE_DEG = clampf(cmd.substring(2).toFloat(), 0.0f, 360.0f);
    interrupts();
    Serial.print("[BLE] POSITION_AMPLITUDE_DEG = ");
    Serial.println(POSITION_AMPLITUDE_DEG, 2);
    return;
  }
  if (cmd.startsWith("ps") && cmd.length() > 2) {
    noInterrupts();
    GCP_POS_START = clampf(cmd.substring(2).toFloat(), 0.0f, 1.0f);
    interrupts();
    Serial.print("[BLE] GCP_POS_START = ");
    Serial.println(GCP_POS_START, 2);
    return;
  }
  if (cmd.startsWith("pe") && cmd.length() > 2) {
    noInterrupts();
    GCP_POS_END = clampf(cmd.substring(2).toFloat(), 0.0f, 1.0f);
    interrupts();
    Serial.print("[BLE] GCP_POS_END = ");
    Serial.println(GCP_POS_END, 2);
    return;
  }

  // === Treadmill Speed (BLE에서 'tm' + 값 수신) ===
  // 예: "tm1.0" → treadmill belt speed = 1.0 m/s
  // "tm0" → treadmill 보상 끄기 (overground mode)
  if (cmd.startsWith("tm") && cmd.length() > 2) {
    noInterrupts();
    treadmill_speed_mps = clampf(cmd.substring(2).toFloat(), 0.0f, 3.0f);
    interrupts();
    Serial.print("[BLE] Treadmill speed = ");
    Serial.print(treadmill_speed_mps, 2);
    Serial.println(" m/s");
    return;
  }

  // === TFF Gain (BLE에서 'tg' + 값 수신) ===
  if (cmd.startsWith("tg") && cmd.length() > 2) {
    noInterrupts();
    TFF_GAIN = clampf(cmd.substring(2).toFloat(), 0.0f, 5.0f);
    interrupts();
    Serial.print("[BLE] TFF Gain = ");
    Serial.println(TFF_GAIN, 2);
    return;
  }

  // === TFF End Offset (BLE에서 'te' + 값 수신) ===
  // 예: "te0.10" → ONSET 10% 전에서 TFF 종료, peak는 그 절반
  // "te0.15" → ONSET 15% 전에서 TFF 종료
  if (cmd.startsWith("te") && cmd.length() > 2) {
    noInterrupts();
    TFF_END_OFFSET = clampf(cmd.substring(2).toFloat(), 0.0f, 0.5f);
    interrupts();
    Serial.print("[BLE] TFF End Offset = ");
    Serial.print(TFF_END_OFFSET, 2);
    Serial.print(" → tff_end = ");
    Serial.print(GCP_FORCE_ONSET - TFF_END_OFFSET, 2);
    Serial.print(", peak at ");
    Serial.println((GCP_FORCE_ONSET - TFF_END_OFFSET) / 2.0f, 2);
    return;
  }

  // === Save / Logging 명령 ===
  // "save"         → 자동 파일명으로 새 파일 시작 (이미 로깅 중이면 이전 파일 닫고 재시작)
  // "save<name>"   → 지정 파일명으로 새 파일 시작 (예: "saveTEST01")
  // "s"            → 호환용: 동일 동작
  if (cmd == "s" || cmd.startsWith("save")) {
    if (cmd.startsWith("save") && cmd.length() > 4) {
      // 커스텀 파일명 지정
      String fname = cmd.substring(4);
      fname.trim();
      if (fname.length() > 0 && fname.length() < sizeof(customFilename) - 1) {
        fname.toCharArray(customFilename, sizeof(customFilename));
        Serial.print("[BLE] Custom filename: ");
        Serial.println(customFilename);
      }
    }
    // ★ 항상 새 파일 시작: 이미 로깅 중이면 현재 파일 닫고 새 파일로
    if (isLogging) {
      stopLoggingImmediate();  // 버퍼 flush + 파일 닫기
    }
    startLogging();
    // ★ GUI에 로깅 상태 피드백 전송
    if (isLogging) {
      char resp[48];
      snprintf(resp, sizeof(resp), "LOG_START:%s", filename);
      sendBleResponse(resp);
    } else {
      sendBleResponse("LOG_FAIL:SD_ERROR");
    }
    return;
  }

  // === Motor Zero Position (BLE에서 'motor' 수신) ===
  if (cmd == "motor") {
    if (motorEnabled) {
      Serial.println("⚠️ [BLE] Disable motors first before zeroing!");
      return;
    }
    setAllMotorsOrigin();
    Serial.println("✅ [BLE] Motor position zeroed");
    return;
  }

  Serial.print("[BLE] Unknown command: ");
  Serial.println(cmd);
}

// ################################################################
// ##                                                            ##
// ##  SECTION R: Setup / Loop                                    ##
// ##                                                            ##
// ################################################################

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000)
    ;

  Serial.println("\n═══════════════════════════════════════════════════════════");
  Serial.println("       AK60 + GCP Integrated Firmware (Unified Version)     ");
  Serial.println("═══════════════════════════════════════════════════════════");
  Serial.println("  ★ Mode 0: Force Assist (HS 기준 GCP, Onset-Peak-Release)");
  Serial.println("  ★ Mode 1: Position Assist (HS 기준 GCP, 1/4 Sine)");
  Serial.println("  ★ Position Loop: 9ms (111Hz)");
  Serial.println("  ★ Velocity Loop: 3ms (333Hz)");
  Serial.println("  ★ Current Cmd:   1ms (1kHz)");
  Serial.println("  ★ Logging:       9ms (111Hz)");
  Serial.println("═══════════════════════════════════════════════════════════\n");

  // Bilateral gait detection 설정
  left_Detector.setContralateral(&right_Detector);
  right_Detector.setContralateral(&left_Detector);
  Serial.println("✅ Bilateral gait detection enabled");

  // 초기 GCP 모드 설정 (기본: Force Assist = HS 기준)
  left_Detector.gcpStartMode = GCP_START_AT_HS;
  right_Detector.gcpStartMode = GCP_START_AT_HS;
  Serial.println("✅ GCP mode: HS-based (Force Assist default)");

  // 핀 설정
  pinMode(LEFT_LOADCELL_PIN, INPUT);
  pinMode(RIGHT_LOADCELL_PIN, INPUT);
  pinMode(ANALOG_PIN, INPUT);
  analogReadResolution(12);

  // IMU Serial 초기화
  IMU_SERIAL.begin(921600);

  // CAN 및 SD 초기화
  setupCAN();
  setupSD();

  // BLE Serial 초기화
  setupBleComm();

  // 모터 원점 설정
  delay(100);
  setAllMotorsOrigin();

  // 타이머 시작
  if (!timerCtrl.begin(ISR_Control, CONTROL_PERIOD_US))
    Serial.println("❌ Failed to start CONTROL timer!");
  else {
    Serial.print("✅ CONTROL timer started @ ");
    Serial.print(CONTROL_PERIOD_MS);
    Serial.println("ms");
  }

  if (!timer1k.begin(ISR_Current1kHz, CURRENT_PERIOD_US))
    Serial.println("❌ Failed to start 1kHz timer!");
  else
    Serial.println("✅ 1kHz command timer started");

  printHelp();
}

void loop() {
  // ★★★ 안전: 메인루프 타임아웃 감지 ★★★
  // SD 카드 flush 등으로 루프가 50ms 이상 지연되면 경고
  static uint32_t lastLoopMs = 0;
  uint32_t loopNow = millis();
  if (lastLoopMs > 0 && (loopNow - lastLoopMs) > 50 && motorEnabled) {
    Serial.print("⚠️ LOOP SLOW: ");
    Serial.print(loopNow - lastLoopMs);
    Serial.println("ms (>50ms)");
  }
  lastLoopMs = loopNow;

  // Serial 명령 처리
  processSerialStreamNonBlocking();

  // BLE 명령 처리
  processBleSerial();

  // BLE 전체 데이터 전송 (9ms 주기)
  static uint32_t lastBleSend = 0;
  if (bleStreamEnabled && (millis() - lastBleSend >= BLE_SEND_PERIOD_MS)) {
    lastBleSend = millis();

    // 현재 데이터 스냅샷
    // ★★★ 실시간 GCP 사용 ★★★
    float l_gcp, r_gcp, l_pitch, r_pitch, l_gy, r_gy;
    noInterrupts();
    l_gcp = left_Detector.getGCP();
    r_gcp = right_Detector.getGCP();
    l_pitch = snapL.pitch;
    r_pitch = snapR.pitch;
    l_gy = snapL.gy;
    r_gy = snapR.gy;
    interrupts();

    sendWalkerDataToBLE(
      l_gcp, r_gcp,
      l_pitch, r_pitch,
      l_gy, r_gy,
      motor_position_deg[SIDE_LEFT], motor_position_deg[SIDE_RIGHT],
      motor_velocity_erpm[SIDE_LEFT], motor_velocity_erpm[SIDE_RIGHT],
      motor_current_a[SIDE_LEFT], motor_current_a[SIDE_RIGHT],
      desiredPosition_deg[SIDE_LEFT], desiredPosition_deg[SIDE_RIGHT],
      desiredForce_N[SIDE_LEFT], desiredForce_N[SIDE_RIGHT],
      actualForce_N[SIDE_LEFT], actualForce_N[SIDE_RIGHT],
      currentMark);
  }

  // ★★★ USB Serial 데이터 스트리밍 (Treadmill_main 추가) ★★★
  // BLE와 완전 독립 — 동일 데이터를 USB Serial로 동시 전송
  static uint32_t lastSerialSend = 0;
  if (serialStreamEnabled && (millis() - lastSerialSend >= SERIAL_SEND_PERIOD_MS)) {
    lastSerialSend = millis();

    float l_gcp_s, r_gcp_s, l_pitch_s, r_pitch_s, l_gy_s, r_gy_s;
    noInterrupts();
    l_gcp_s = left_Detector.getGCP();
    r_gcp_s = right_Detector.getGCP();
    l_pitch_s = snapL.pitch;
    r_pitch_s = snapR.pitch;
    l_gy_s = snapL.gy;
    r_gy_s = snapR.gy;
    interrupts();

    sendWalkerDataToSerial(
      l_gcp_s, r_gcp_s,
      l_pitch_s, r_pitch_s,
      l_gy_s, r_gy_s,
      motor_position_deg[SIDE_LEFT], motor_position_deg[SIDE_RIGHT],
      motor_velocity_erpm[SIDE_LEFT], motor_velocity_erpm[SIDE_RIGHT],
      motor_current_a[SIDE_LEFT], motor_current_a[SIDE_RIGHT],
      desiredPosition_deg[SIDE_LEFT], desiredPosition_deg[SIDE_RIGHT],
      desiredForce_N[SIDE_LEFT], desiredForce_N[SIDE_RIGHT],
      actualForce_N[SIDE_LEFT], actualForce_N[SIDE_RIGHT],
      currentMark);
  }

  // Analog 트리거 (A7)
  int a7 = analogRead(ANALOG_PIN);
  syncA7 = (uint16_t)a7;

  if (!isLogging && a7 > TRIGGER_THRESHOLD) {
    Serial.println("NO SD Card!");
    startLogging();
  }

  // Loadcell 읽기
  loadcellRaw_N[SIDE_LEFT] = readLoadcellForceN(SIDE_LEFT);
  loadcellRaw_N[SIDE_RIGHT] = readLoadcellForceN(SIDE_RIGHT);

  // IMU 스트림 처리
  updateIMUStream();

  // 로그 버퍼 처리
  if (isLogging || dataFile) processLogBuffer();

  // Safety 메시지 출력: Position Limit
  if (safetyPrintPending) {
    safetyPrintPending = false;
    Serial.println("🚨 SAFETY TRIGGERED! Position limit exceeded!");
    Serial.print("side=");
    Serial.println((safetySidePending == SIDE_LEFT) ? "LEFT" : "RIGHT");
    Serial.print("posDiff_deg=");
    Serial.println(safetyPosDiff_deg, 2);
  }

  // Safety 메시지 출력: Cable Tangle
  if (safetyTanglePending) {
    safetyTanglePending = false;
    Serial.println("🚨 SAFETY: Cable Tangle Detected!");
    Serial.print("  Side: ");
    Serial.println((safetyTangleSide == SIDE_LEFT) ? "LEFT" : "RIGHT");
    Serial.print("  Force: ");
    Serial.print(safetyTangleForce, 1);
    Serial.print(" N (> ");
    Serial.print(SAFETY_PAYOUT_FORCE_N, 0);
    Serial.println(" N threshold)");
    Serial.print("  Motor ERPM: ");
    Serial.print(safetyTangleErpm, 0);
    Serial.println(" (payout direction)");
    Serial.println("  → Motor disabled. Reset with 'r' command.");
  }

  // 디버그 출력
  static uint32_t lastDbg = 0;
  if (show_debug && (millis() - lastDbg >= DEBUG_PERIOD_MS)) {
    lastDbg = millis();

    // ★★★ 실시간 GCP 사용 ★★★
    float Lgcp, Rgcp;
    noInterrupts();
    Lgcp = left_Detector.getGCP();
    Rgcp = right_Detector.getGCP();
    interrupts();

    Serial.print("t:");
    Serial.print(millis());
    Serial.print(",Mode:");
    Serial.print(currentMode);
    Serial.print(",Lgcp:");
    Serial.print(Lgcp, 2);
    Serial.print(",Rgcp:");
    Serial.print(Rgcp, 2);

    if (currentMode == MODE_POSITION_ASSIST) {
      Serial.print(",L_offset:");
      Serial.print(dbg_pos_offset[SIDE_LEFT], 1);
      Serial.print(",L_des_pos:");
      Serial.print(dbg_des_pos[SIDE_LEFT], 1);
      Serial.print(",L_act_pos:");
      Serial.print(motor_position_deg[SIDE_LEFT], 1);
      Serial.print(",L_I_cmd:");
      Serial.print(desiredCurrent_A[SIDE_LEFT], 3);

      Serial.print(",R_offset:");
      Serial.print(dbg_pos_offset[SIDE_RIGHT], 1);
      Serial.print(",R_des_pos:");
      Serial.print(dbg_des_pos[SIDE_RIGHT], 1);
      Serial.print(",R_act_pos:");
      Serial.print(motor_position_deg[SIDE_RIGHT], 1);
      Serial.print(",R_I_cmd:");
      Serial.print(desiredCurrent_A[SIDE_RIGHT], 3);
    } else {
      Serial.print(",L_F_cmd:");
      Serial.print(dbg_F_cmd[SIDE_LEFT], 1);
      Serial.print(",L_F_act:");
      Serial.print(actualForce_N[SIDE_LEFT], 1);
      Serial.print(",L_I_cmd:");
      Serial.print(desiredCurrent_A[SIDE_LEFT], 3);

      Serial.print(",R_F_cmd:");
      Serial.print(dbg_F_cmd[SIDE_RIGHT], 1);
      Serial.print(",R_F_act:");
      Serial.print(actualForce_N[SIDE_RIGHT], 1);
      Serial.print(",R_I_cmd:");
      Serial.print(desiredCurrent_A[SIDE_RIGHT], 3);
    }

    Serial.print(",MEn:");
    Serial.print(motorEnabled ? 1 : 0);
    Serial.print(",Log:");
    Serial.println(isLogging ? 1 : 0);
  }

  yield();
}

#else
#error "This code requires Teensy 3.6 or 4.1"
#endif
