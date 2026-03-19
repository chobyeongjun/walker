# H-Walker GUI — Debug Log

## 2026-03-19 — Session 2: 사용자 피드백 기반 전면 개선

### Bug #1: Gait Analysis R-side 전부 "—" (CRITICAL)
**증상**: CSV 로드 후 Gait Analysis 탭에서 R측 모든 파라미터가 "—"으로 표시
**원인 분석**:
1. 실제 CSV 데이터 확인 → `R_GCP` 컬럼은 존재하지만 값이 전부 0.0 (단일 다리 실험)
2. `np.max(gcp)=0` → `if np.max(gcp) > 2` 분기 미진입 → 정규화 안됨
3. `np.diff(전부0)=전부0` → HS threshold `-0.5` 충족 건수 0
4. `n_strides=0` → 모든 mean/std가 0 → `_fmt_ms`가 `"—"` 반환

**수정**:
- `_compute_gait_params`: 동적 HS threshold (`max(0.3, gcp_range*0.4)`)
- `gcp_range < 0.5` 시 `no_data=True` 플래그로 early-continue
- `_fmt_ms`: `no_data` 플래그 확인 → "No GCP" 표시, stride=0이면 "0 strides"
- `load_file`: `df.columns.str.strip()` 으로 컬럼명 공백 제거

### Bug #2: Top Bar 텍스트 높낮이 불일치
**증상**: SD LOG, STATUS, GCP 게이지의 수직 위치가 각각 다름
**원인**: `QVBoxLayout` 내부 위젯들의 수직 정렬이 미지정
**수정**:
- SD LOG 섹션을 `QWidget(fixedWidth=230)` + `setAlignment(Qt.AlignVCenter)` 로 감싸기
- STATUS 섹션도 동일하게 `AlignVCenter`
- 섹션 간 세로 구분선(1px) 추가
- GCP 인디케이터에 `alignment=Qt.AlignVCenter` 적용
- 모든 라벨 `fixedHeight` 통일

### Feature: Legend 폰트 크기 조절
- `addLegend(labelTextSize='11pt')` 기본 크기 11pt로 키움
- toolbar에 ComboBox (8/10/11/13/15pt) 추가
- 변경 시 `_update_chart()` 재호출 → legend 재생성

### Feature: 선 굵기 조절
- `self._line_width = 2.0` 인스턴스 변수
- toolbar에 `QDoubleSpinBox(0.5~6.0)` 추가
- `_update_chart`, `_update_compare` 에서 `width=self._line_width` 사용

### Feature: MATLAB 커맨드 입력
- Chart 탭 하단에 `>> ` 프롬프트 + `QLineEdit` 추가
- 지원 명령: `ylim`, `xlim`, `grid`, `title`, `ylabel`, `xlabel`, `linewidth`, `legend`, `auto`, `help`
- `returnPressed` → `_execute_command()` 파싱 및 실행

---

## 2026-03-19 — Session 1: 초기 구현

### Architecture: PlotTabWidget TopBarWidget 이중화 제거
**증상**: PlotTabWidget 내부 TopBarWidget이 화면에 안 보이지만 30Hz GCP 렌더링 실행
**수정**: TopBarWidget 생성/연결 제거, GCP 콜백만 유지

### Style: 하드코딩 색상 제거
- `plot_widget.py`: `#1a1a1a`, `#252525` → `GlassCard` ObjectName
- `analysis_mode.py`: 이모지(🔍✋🔄🔒) → ASCII 텍스트
- `camera_mode.py`: `📷` → QPainter animated ring
- `styles.py`: 버튼 pressed 효과, GlassCard 그라디언트, 탭 호버 글로우 추가

### File Mode 전면 구현
- Download: `QInputDialog` 폴더명 → `shutil.copy2` 로컬 복사
- SD 통신: `TeensyDownloadThread(QThread)` + `LIST/GET/DEL` 프로토콜
- Delete: `QMessageBox` 확인 후 Serial 삭제
- 다운로드 완료 → "Analysis에서 열겠습니까?" 연동

### Analysis: Compare 탭 mean±SD
- stride-normalized 시 개별 stride(alpha=60) + mean±SD band(bold 3px) 동시 표시

### Analysis: Export 에러 피드백
- PNG/SVG 저장 후 `QMessageBox.information` (파일명, 크기, 경로)
- 실패 시 `QMessageBox.warning`

### Analysis: Sample Rate 자동 추정
- CSV `Time/Time_s/Timestamp` 컬럼에서 `1/median(diff)` 계산
- 없으면 기본 111Hz (펌웨어 루프 기준)

### Dead Code 제거
- `_extract_strides()` 메서드 삭제 (미사용, `_compute_gait_params`가 이미 동일 로직 포함)
