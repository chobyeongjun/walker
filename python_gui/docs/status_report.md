# H-Walker GUI — Status Report

**날짜**: 2026-03-19
**작성**: CBJ
**레포**: https://github.com/chobyeongjun/walker.git

---

## 프로젝트 개요

H-Walker는 보행 보조 로봇(Exoskeleton)을 위한 제어 및 분석 GUI입니다.
BLE 실시간 모니터링, CSV 데이터 분석, Teensy SD 카드 파일 관리를 통합합니다.

- **프레임워크**: PyQt5 + pyqtgraph
- **하드웨어**: Teensy 4.1 + AK60-6 모터 x2 + EBIMU IMU x2 + Loadcell x2
- **통신**: BLE (Nordic NUS) + USB Serial (SD 파일 관리)

---

## 완성된 기능

### Realtime Mode (Mode 0)
- [x] BLE 자동 연결/재연결 (exponential backoff)
- [x] 6탭 실시간 플롯 (Force, IMU Pitch, Gyro, Position, Velocity, Current)
- [x] GCP 원형 게이지 (QPainter, 3겹 글로우)
- [x] Force/Position 모드 전환
- [x] 파라미터 실시간 전송 (Onset/Peak/Release GCP, Peak Force, FF)
- [x] SD 로깅 시작/정지
- [x] 30Hz 최적화 (현재 탭만 렌더링, batch update)

### Analysis Mode (Mode 1)
- [x] 다중 CSV 로드 (최대 10개, 자동 색+선스타일 배정)
- [x] Chart 탭: 67개 컬럼 선택 + Zoom/Pan/Reset
- [x] Gait Analysis 탭: HS/HO 감지, Stride/Step Time, Cadence, Stance/Swing %, Symmetry Index
- [x] Compare 탭: stride-normalized mean±SD band + 개별 stride 오버레이
- [x] Export PNG/SVG (파일 저장 확인 메시지 포함)
- [x] MATLAB 커맨드 입력 (ylim, xlim, grid, title, xlabel, ylabel, linewidth, legend)
- [x] Legend 폰트 크기 조절 (8~15pt)
- [x] 선 굵기 조절 (0.5~6.0)
- [x] R-side Gait Analysis 버그 수정 (동적 HS threshold + No GCP 표시)

### File Mode (Mode 2)
- [x] 로컬 폴더 CSV 브라우징
- [x] Teensy USB 감지
- [x] Download (폴더명 다이얼로그 + 로컬 복사)
- [x] SD 카드 파일 리스트 (LIST 프로토콜)
- [x] SD 파일 다운로드 (GET 프로토콜, QThread)
- [x] Delete from SD (DEL 프로토콜)
- [x] Open in Analysis 연동

### Camera Mode (Mode 3)
- [x] Coming Soon placeholder (QPainter animated ring)

### Design System
- [x] Glassmorphism 다크 테마 (전역 QSS)
- [x] 그라디언트 버튼 (Accent/Green/Red + pressed 효과)
- [x] GlassCard 호버 효과
- [x] TopNav 탭 글로우

---

## 미해결 이슈

| # | 이슈 | 심각도 | 상태 |
|---|------|--------|------|
| 1 | Dual Y-axis (좌우 독립 Y축) | Medium | 미구현 |
| 2 | Subplot 기능 | Medium | 미구현 |
| 3 | Teensy SD 프로토콜 펌웨어 측 구현 | High | GUI 준비됨, FW 미구현 |
| 4 | Google Drive 자동 복사 | Low | 미구현 |
| 5 | Camera Mode (Jetson Orin NX) | Low | placeholder만 |
| 6 | Cloud/GitHub 동기화 | Medium | 조사 필요 |

---

## 파일 구조

```
python_gui/
├── main.py                  # 엔트리포인트
├── requirements.txt
├── logo.png
├── core/
│   ├── ble_client.py        # BLE QThread (수정 금지)
│   ├── data_parser.py       # WalkerData 파싱
│   ├── ring_buffer.py
│   ├── camera_thread.py
│   └── ros2_interface.py
├── ui/
│   ├── main_window.py       # MainWindow + BLE 오케스트레이션
│   ├── top_nav.py           # 수평 탭 네비게이션
│   ├── realtime_mode.py     # 실시간 모드
│   ├── control_panel.py     # BLE 제어 패널
│   ├── plot_widget.py       # GCPIndicator + SinglePlot + PlotTabWidget
│   ├── analysis_mode.py     # CSV 분석 (Chart/Gait/Compare)
│   ├── file_mode.py         # 파일 관리 + Teensy SD
│   ├── camera_mode.py       # Coming Soon
│   └── styles.py            # 색상 팔레트 + QSS
└── docs/
    ├── gait_parameters.md   # 보행 파라미터 계산 방법
    ├── status_report.md     # 이 문서
    └── debug_log.md         # 디버그 이력
```
