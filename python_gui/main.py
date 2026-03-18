#!/usr/bin/env python3
"""
ARWalker Control GUI - No Jetson Version

고성능 실시간 모니터링 및 제어 GUI

Features:
- BLE 연결 (Nordic UART Service)
- 탭 기반 실시간 플롯 (렉 방지)
- GCP 인디케이터 항상 표시
- 모드별 파라미터 (Force Assist / Position Assist)
- Y축 범위 조절 가능
- Camera 확장 지원 (미래)

Usage:
    # 의존성 설치
    pip install -r requirements.txt

    # 실행
    python main.py

Requirements:
    - Python 3.9+
    - PyQt5
    - pyqtgraph
    - bleak (BLE)
    - numpy
"""

import sys
import os

# 패키지 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import main

if __name__ == "__main__":
    main()
