# H-Walker Gait Parameter Calculation Methods

## Overview

H-Walker GUI의 Gait Analysis 탭에서 계산되는 모든 보행 파라미터의 알고리즘과 수식을 설명합니다.
코드 위치: `ui/analysis_mode.py` → `_compute_gait_params()`

---

## 1. Heel Strike (HS) Detection

**알고리즘**: GCP(Gait Cycle Percentage) 신호의 급하강(reset) 감지

```
GCP: 0 → 0.1 → 0.3 → 0.6 → 0.9 → 1.0 → [0.0] ← HS 감지 지점
```

**수식**:
```python
diffs = np.diff(gcp)                          # 연속 샘플 간 차이
gcp_range = np.ptp(gcp)                        # peak-to-peak range
hs_threshold = -max(0.3, gcp_range * 0.4)      # 동적 threshold
hs_indices = np.where(diffs < hs_threshold)     # threshold 이하 급락 위치
```

**동적 threshold 설계 이유**:
- 고정값 `-0.5`는 GCP 진폭이 낮은 경우 감지 실패
- `gcp_range * 0.4` = 신호 전체 범위의 40%를 threshold로 사용
- 최소 `0.3` 보장하여 노이즈에 의한 오탐 방지
- GCP가 0-100 범위인 경우 자동으로 0-1로 정규화 후 적용

---

## 2. Heel Off (HO) Detection

**알고리즘**: 각 stride 내에서 GCP가 60%를 초과하는 구간 존재 여부

```python
for each stride (HS_i to HS_{i+1}):
    stride_gcp = gcp[HS_i : HS_{i+1}]
    if np.any(stride_gcp > 0.6):
        ho_count += 1
```

**해석**: GCP > 60%는 swing phase 진입을 의미 → HO 발생

---

## 3. Stride Time

**정의**: 동일 측 연속 HS 간 시간 간격

```
Stride Time = (HS_{i+1} - HS_i) / sample_rate [seconds]
```

**Sample Rate 추정**:
1. CSV에 `Time`, `Time_s`, `Timestamp` 컬럼이 있으면: `1 / median(diff(time))`
2. 없으면: 기본값 111Hz (펌웨어 9ms loop 기준)

**통계**: Mean ± SD (모든 stride에 대해)

---

## 4. Step Time

**정의**: Stride의 절반 (단일 측 분석 시 근사값)

```
Step Time ≈ Stride Time / 2 [seconds]
```

**참고**: 정확한 Step Time은 반대측 HS도 필요하지만, 단일 IMU 사용 시 stride/2로 근사

---

## 5. Cadence

**정의**: 분당 걸음 수

```
Cadence = (60 / Stride_Time_mean) × 2 [steps/min]
```

**×2 이유**: 1 stride = 2 steps (좌+우 한 쌍)

**정상 범위**: 건강한 성인 약 100-120 steps/min

---

## 6. Stance Phase (%)

**정의**: Stride 중 발이 지면에 닿아있는 비율

```python
n_stance = np.sum(stride_gcp < 0.6)     # GCP < 60% = stance
stance_ratio = n_stance / n_total * 100  # 백분율
```

**해석**:
- GCP < 60%: 발이 지면 접촉 (weight bearing) → Stance Phase
- GCP ≥ 60%: 발이 공중 (swing) → Swing Phase
- 정상 비율: Stance ~60%, Swing ~40%

---

## 7. Swing Phase (%)

```
Swing Phase = 100% - Stance Phase
```

---

## 8. Peak Force / Mean Force

**정의**: 각 stride 내 `L_ActForce_N` 또는 `R_ActForce_N` 컬럼의 통계

```python
for each stride:
    peak_force = np.max(force[HS_i : HS_{i+1}])
    mean_force = np.mean(force[HS_i : HS_{i+1}])
```

**통계**: 모든 stride에 대한 Mean ± SD

---

## 9. Symmetry Index (SI)

**정의**: 좌우 stride time의 비대칭 정도

```
SI = |L_stride_time - R_stride_time| / ((L + R) / 2) × 100 [%]
```

**해석**:
- SI = 0%: 완전 대칭 보행
- SI < 10%: 정상 범위
- SI > 10%: 비대칭 보행 (편마비 등)

**조건**: 좌우 모두 stride 데이터가 있어야 계산 가능. 한쪽이 "No GCP"이면 SI = 0 (N/A)

---

## 10. GCP-Normalized Force Profile

**알고리즘**: 각 stride의 Force를 0-100% GCP로 정규화 후 중첩

```python
for each stride:
    x_orig = np.linspace(0, 100, len(stride_force))
    y_interp = np.interp(x_101_points, x_orig, stride_force)
    # → 101개 점으로 리샘플링 (0%, 1%, ..., 100%)

mean_profile = np.mean(all_strides, axis=0)
std_profile = np.std(all_strides, axis=0)
# → Mean ± SD band로 표시
```

---

## No Data 처리

| 상태 | 표시 | 조건 |
|------|------|------|
| GCP 컬럼 없음 | `No GCP` | `gcp_col not in df.columns` |
| GCP 변동 없음 (all-zero) | `No GCP` | `np.ptp(gcp) < 0.5` |
| GCP 있으나 stride 0건 | `0 strides` | `len(hs_idx) < 2` |
| 정상 | `mean ± std` | stride ≥ 1건 |

---

## 참고문헌

- Perry, J. (2010). *Gait Analysis: Normal and Pathological Function*
- Winter, D.A. (2009). *Biomechanics and Motor Control of Human Movement*
- GCP(Gait Cycle Percentage) 기반 분석은 H-Walker 자체 IMU 알고리즘 기반
