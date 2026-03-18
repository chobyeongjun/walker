/*
 * ================================================================
 *  BLE Communication Module for Walker
 *  Implementation File
 * ================================================================
 */

#include "BleComm.h"

// ================================================================
// [1] 전역 변수 정의
// ================================================================

volatile uint32_t currentMark = 0;
volatile bool bleStreamEnabled = false;

char bleRxBuffer[128];
uint8_t bleRxLen = 0;

// ================================================================
// [2] BLE Serial 초기화
// ================================================================

void setupBleComm() {
    BLE_SERIAL.begin(BLE_BAUD_RATE);

    // 버퍼 초기화
    bleRxLen = 0;
    memset(bleRxBuffer, 0, sizeof(bleRxBuffer));

    Serial.println("✅ BLE Serial initialized (Serial2 @ 115200 baud)");
    Serial.println("┌─────────────────────────────────────────┐");
    Serial.println("│  BLE UART Bridge Connection             │");
    Serial.println("├─────────────────────────────────────────┤");
    Serial.print("│  Teensy Pin ");
    Serial.print(BLE_TX_PIN);
    Serial.println(" (TX2) → Nano RX (D0)     │");
    Serial.print("│  Teensy Pin ");
    Serial.print(BLE_RX_PIN);
    Serial.println(" (RX2) ← Nano TX (D1)     │");
    Serial.println("│  GND ─────────────── GND                │");
    Serial.println("└─────────────────────────────────────────┘");
}

// ================================================================
// [3] Walker 데이터 전송
// ================================================================

// ★ 최적화: 단일 버퍼에 모든 데이터를 포맷팅 후 한 번에 전송
static char bleTxBuffer[256];

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
) {
    // BLE 스트리밍이 비활성화되어 있으면 전송하지 않음
    if (!bleStreamEnabled) return;

    // ★ 최적화: 모든 데이터를 버퍼에 한 번에 포맷팅
    // 38개의 개별 print() 호출 → 1개의 snprintf + write
    int len = snprintf(bleTxBuffer, sizeof(bleTxBuffer),
        "SW%dc%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn%dn",
        WALKER_DATA_COUNT,
        (int)(l_gcp * 100.0f),        // 0: L_GCP
        (int)(r_gcp * 100.0f),        // 1: R_GCP
        (int)(l_pitch * 100.0f),      // 2: L_Pitch
        (int)(r_pitch * 100.0f),      // 3: R_Pitch
        (int)(l_gyro_y * 100.0f),     // 4: L_GyroY
        (int)(r_gyro_y * 100.0f),     // 5: R_GyroY
        (int)(l_motor_pos * 100.0f),  // 6: L_MotorPos
        (int)(r_motor_pos * 100.0f),  // 7: R_MotorPos
        (int)(l_motor_vel / 100.0f),  // 8: L_MotorVel (erpm/100)
        (int)(r_motor_vel / 100.0f),  // 9: R_MotorVel (erpm/100)
        (int)(l_motor_curr * 100.0f), // 10: L_MotorCurr
        (int)(r_motor_curr * 100.0f), // 11: R_MotorCurr
        (int)(l_des_pos * 100.0f),    // 12: L_DesPos
        (int)(r_des_pos * 100.0f),    // 13: R_DesPos
        (int)(l_des_force * 100.0f),  // 14: L_DesForce
        (int)(r_des_force * 100.0f),  // 15: R_DesForce
        (int)(l_act_force * 100.0f),  // 16: L_ActForce
        (int)(r_act_force * 100.0f),  // 17: R_ActForce
        (int)(mark * 100)             // 18: Mark
    );

    // ★ 단일 write 호출로 전송 (38개 print → 1개 write)
    // Teensy Serial.write()는 내부적으로 TX 버퍼를 관리하며,
    // 패킷이 버퍼보다 커도 자동 분할 전송됨 (blocking write)
    if (len > 0 && len < (int)sizeof(bleTxBuffer)) {
        BLE_SERIAL.write(bleTxBuffer, len);
    }
}

// ================================================================
// [3-2] GCP 전용 고속 전송 (333Hz)
// ================================================================

static char bleGcpBuffer[32];

void sendGcpToBLE(float l_gcp, float r_gcp) {
    // BLE 스트리밍이 비활성화되어 있으면 전송하지 않음
    if (!bleStreamEnabled) return;

    // ★ GCP 전용 패킷: "SG2c<L>n<R>n"
    // GUI에서 "SG" 패킷을 받으면 GCP만 빠르게 업데이트
    int len = snprintf(bleGcpBuffer, sizeof(bleGcpBuffer),
        "SG2c%dn%dn",
        (int)(l_gcp * 100.0f),   // L_GCP (0~100+)
        (int)(r_gcp * 100.0f)    // R_GCP (0~100+)
    );

    if (len > 0 && len < (int)sizeof(bleGcpBuffer)) {
        BLE_SERIAL.write(bleGcpBuffer, len);
    }
}

// ================================================================
// [3-3] 펌웨어 → GUI 응답 전송
// ================================================================

static char bleRespBuffer[64];

void sendBleResponse(const char* msg) {
    // 패킷 포맷: "SR:<message>\n"
    // GUI에서 "SR:" 프리픽스로 응답 패킷 식별
    int len = snprintf(bleRespBuffer, sizeof(bleRespBuffer), "SR:%s\n", msg);
    if (len > 0 && len < (int)sizeof(bleRespBuffer)) {
        BLE_SERIAL.write(bleRespBuffer, len);
    }
}

// ================================================================
// [4] BLE Serial 수신 처리
// ================================================================

bool processBleSerial() {
    static uint32_t lastRxMs = 0;
    bool commandProcessed = false;

    while (BLE_SERIAL.available()) {
        char ch = (char)BLE_SERIAL.read();
        lastRxMs = millis();

        if (ch == '\r' || ch == '\n') {
            if (bleRxLen > 0) {
                bleRxBuffer[bleRxLen] = '\0';

                // 명령 핸들러 호출 (외부 구현)
                handleBleCommand(String(bleRxBuffer));

                bleRxLen = 0;
                commandProcessed = true;
            }
        } else {
            if (bleRxLen < sizeof(bleRxBuffer) - 1) {
                bleRxBuffer[bleRxLen++] = ch;
            } else {
                // 버퍼 오버플로 방지
                bleRxLen = 0;
            }
        }
    }

    // ★ 타임아웃: 50ms (BLE 전송 지연 고려, newline 누락 시 안전망)
    if (bleRxLen > 0 && (millis() - lastRxMs) > 50) {
        bleRxBuffer[bleRxLen] = '\0';
        handleBleCommand(String(bleRxBuffer));
        bleRxLen = 0;
        commandProcessed = true;
    }

    return commandProcessed;
}
