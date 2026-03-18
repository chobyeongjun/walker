/*
 * ================================================================
 *  BLE Communication Module for Walker
 *  Header File
 * ================================================================
 *
 *  Teensy 4.1 <-> Arduino Nano 33 BLE 간 UART 브릿지 통신
 *  Nordic UART Service (NUS) 프로토콜 사용
 *
 *  패킷 포맷: "SW19c<d0>n<d1>n...<d18>n"
 *  - S: 시작 문자
 *  - W: Walker 명령
 *  - 19: 데이터 개수
 *  - c: 데이터 시작
 *  - n: 구분자
 *  - 값은 정수 (실제값 * 100)
 *
 * ================================================================
 */

#ifndef BLE_COMM_H
#define BLE_COMM_H

#include <Arduino.h>

// ================================================================
// [1] BLE Serial 정의 및 핀 배치
// ================================================================

/*
 * ┌─────────────────────────────────────────────────────────────┐
 * │                    HARDWARE CONNECTION                       │
 * ├─────────────────────────────────────────────────────────────┤
 * │  Teensy 4.1                    Arduino Nano 33 BLE          │
 * │  ───────────                   ───────────────────          │
 * │  Pin 35 (TX8) ──────────────►  RX (D0 / Serial1 RX)         │
 * │  Pin 34 (RX8) ◄──────────────  TX (D1 / Serial1 TX)         │
 * │  GND         ─────────────────  GND                         │
 * │                                                              │
 * │  ⚠️  3.3V Logic Level (Teensy 4.1 및 Nano 33 BLE 모두 호환)  │
 * │  ★  기존 프로젝트와 동일한 Serial8 사용 (UARTHandler 호환)    │
 * └─────────────────────────────────────────────────────────────┘
 */

// Teensy 4.1 Serial8 핀 정의 (기존 프로젝트와 동일)
#define BLE_TX_PIN 35   // Teensy 4.1 TX8 → Nano RX (D0)
#define BLE_RX_PIN 34   // Teensy 4.1 RX8 ← Nano TX (D1)

// Serial 포트 및 통신 속도
#define BLE_SERIAL Serial8
#define BLE_BAUD_RATE 115200

// ================================================================
// [2] 데이터 전송 설정
// ================================================================

#define WALKER_DATA_COUNT 19   // 전송할 데이터 개수
#define BLE_SEND_PERIOD_MS 20  // 전송 주기 (20ms = 50Hz)
                               // ★ 9ms(111Hz) → 20ms(50Hz)로 변경
                               // 이유: 패킷 ~120bytes × 111Hz = 13,320 B/s > UART 11,520 B/s
                               //       패킷 ~120bytes × 50Hz  =  6,000 B/s < UART 11,520 B/s
#define BLE_GCP_PERIOD_MS 3    // ★ GCP 전용 전송 주기 (3ms = 333Hz)

// ================================================================
// [3] 전역 변수 선언 (extern)
// ================================================================

// Mark 기능: GUI에서 mark 명령 수신 시 증가, CSV와 BLE 데이터에 포함
extern volatile uint32_t currentMark;

// BLE 스트리밍 활성화 플래그
extern volatile bool bleStreamEnabled;

// BLE 통신 버퍼
// ★ 64→128 바이트: 긴 명령 (save<filename> 등) 수용
extern char bleRxBuffer[128];
extern uint8_t bleRxLen;

// ================================================================
// [4] 함수 선언
// ================================================================

/**
 * @brief BLE Serial 초기화
 * Serial2를 115200 baud로 초기화합니다.
 */
void setupBleComm();

/**
 * @brief Walker 데이터를 BLE로 전송
 *
 * 9ms 주기로 호출되어 19개의 데이터를 패킷 형태로 전송합니다.
 * 패킷 포맷: "SW19c<d0>n<d1>n...<d18>n"
 *
 * @param l_gcp         Left GCP (0~1)
 * @param r_gcp         Right GCP (0~1)
 * @param l_pitch       Left IMU Pitch (deg)
 * @param r_pitch       Right IMU Pitch (deg)
 * @param l_gyro_y      Left IMU Gyro Y (deg/s)
 * @param r_gyro_y      Right IMU Gyro Y (deg/s)
 * @param l_motor_pos   Left Motor Position (deg)
 * @param r_motor_pos   Right Motor Position (deg)
 * @param l_motor_vel   Left Motor Velocity (erpm)
 * @param r_motor_vel   Right Motor Velocity (erpm)
 * @param l_motor_curr  Left Motor Current (A)
 * @param r_motor_curr  Right Motor Current (A)
 * @param l_des_pos     Left Desired Position (deg)
 * @param r_des_pos     Right Desired Position (deg)
 * @param l_des_force   Left Desired Force (N)
 * @param r_des_force   Right Desired Force (N)
 * @param l_act_force   Left Actual Force (N)
 * @param r_act_force   Right Actual Force (N)
 * @param mark          Mark number (CSV 동기화용)
 */
void sendWalkerDataToBLE(
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
);

/**
 * @brief BLE Serial에서 수신된 명령 처리
 *
 * GUI에서 전송된 명령을 파싱하고 해당 동작을 수행합니다.
 * Non-blocking으로 동작합니다.
 *
 * @return true: 명령 수신 및 처리됨, false: 수신된 명령 없음
 */
bool processBleSerial();

/**
 * @brief BLE 명령 핸들러 (외부 구현 필요)
 *
 * BleComm 모듈에서 파싱된 명령을 실제로 처리하는 함수입니다.
 * 메인 펌웨어에서 구현해야 합니다.
 *
 * @param cmd 수신된 명령 문자열
 */
extern void handleBleCommand(String cmd);

/**
 * @brief GCP 전용 고속 전송 (333Hz)
 *
 * GCP 값만 빠르게 전송하여 GUI 반응성 향상
 * 패킷 포맷: "SG2c<L_GCP>n<R_GCP>n"
 *
 * @param l_gcp Left GCP (0~1+)
 * @param r_gcp Right GCP (0~1+)
 */
void sendGcpToBLE(float l_gcp, float r_gcp);

/**
 * @brief 펌웨어 → GUI 응답 전송
 *
 * GUI가 파싱할 수 있는 응답 패킷을 BLE로 전송합니다.
 * 패킷 포맷: "SR:<message>\n"
 *
 * @param msg 응답 메시지 (예: "LOG_START:TEST01.CSV")
 */
void sendBleResponse(const char* msg);

#endif // BLE_COMM_H
