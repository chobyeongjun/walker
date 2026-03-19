#!/usr/bin/env python3
"""
H-Walker Control GUI

Realtime BLE monitoring, CSV analysis, file management
Designed by CBJ

Usage:
    pip install -r requirements.txt
    python main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import main

if __name__ == "__main__":
    main()
