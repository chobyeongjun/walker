# H-Walker

Hip Walker GUI + Firmware for gait rehabilitation research.

## Structure

```
walker/
├── python_gui/          # PyQt5 + pyqtgraph GUI
│   ├── main.py          # Entry point
│   ├── core/
│   │   ├── ble_client.py    # BLE communication (Nordic NUS)
│   │   ├── data_parser.py   # Packet parser (WalkerData)
│   │   └── ring_buffer.py   # Ring buffer utilities
│   └── ui/
│       ├── main_window.py   # MainWindow (4 modes)
│       ├── top_nav.py       # Tab navigation
│       ├── realtime_mode.py # BLE realtime monitoring
│       ├── control_panel.py # Parameter controls
│       ├── plot_widget.py   # GCP gauge + 6-tab plots
│       ├── analysis_mode.py # CSV analysis + Gait + Compare
│       ├── file_mode.py     # SD card file management
│       ├── camera_mode.py   # Coming Soon placeholder
│       └── styles.py        # Glassmorphism design system
├── arduino/             # Teensy 4.1 firmware
└── docs/                # Documentation
```

## GUI Modes

| Mode | Description |
|------|-------------|
| **Realtime** | BLE monitoring, GCP gauges, 6-channel plots (Force/IMU/Gyro/Pos/Vel/Curr) |
| **Analysis** | CSV viewer, Gait Analysis (HS/HO/Cadence/Symmetry), multi-file Compare |
| **Files** | Local + Teensy SD card file management, download with progress |
| **Camera** | Jetson Orin NX integration (coming soon) |

## Hardware

- **MCU**: Teensy 4.1
- **Motors**: AK60-6 x2 (CAN bus)
- **IMU**: EBIMU x2
- **Loadcell**: x2
- **Communication**: BLE (Nordic NUS) + USB Serial

## Quick Start

```bash
cd python_gui
pip install -r requirements.txt
python main.py
```

## Requirements

- Python 3.8+
- PyQt5, pyqtgraph, numpy, pandas
- bleak (BLE)
- pyserial (SD card access)

---

Designed by CBJ | ARLAB
