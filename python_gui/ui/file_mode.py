"""
H-Walker File Mode

로컬 폴더 + Teensy SD 카드 파일 관리
- 파일 브라우징 / 다중 선택
- 다운로드 (폴더명 다이얼로그 + 진행률)
- Teensy USB Serial SD 통신 (LIST/GET/DEL)
- Open in Analysis 연동
"""

import os
import shutil
from datetime import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QRadioButton, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QProgressBar, QFileDialog,
    QInputDialog, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread

from ui.styles import C


# === Teensy SD Download Thread ===

class TeensyDownloadThread(QThread):
    """QThread로 Teensy SD 파일 다운로드 (GUI 프리징 방지)

    프로토콜:
      GUI -> Teensy: "GET:filename.CSV\n"
      Teensy -> GUI: "SIZE:12345\n" + <binary data> + "OK\n"
    """
    progress = pyqtSignal(int, str)    # (percent, current_filename)
    file_saved = pyqtSignal(str)       # saved file path
    finished = pyqtSignal(list)        # all saved paths
    error = pyqtSignal(str)

    def __init__(self, port: str, filenames: list, dest_folder: str, parent=None):
        super().__init__(parent)
        self._port = port
        self._filenames = filenames
        self._dest_folder = dest_folder

    def run(self):
        saved_paths = []
        try:
            import serial
        except ImportError:
            self.error.emit("pyserial not installed: pip install pyserial")
            return

        try:
            with serial.Serial(self._port, 115200, timeout=10) as ser:
                total = len(self._filenames)
                for i, filename in enumerate(self._filenames):
                    self.progress.emit(int(i / total * 100), filename)

                    # 파일 요청
                    ser.write(f"GET:{filename}\n".encode())

                    # SIZE 헤더 읽기
                    header = ser.readline().decode(errors='replace').strip()
                    if not header.startswith("SIZE:"):
                        self.error.emit(f"Bad response for {filename}: {header}")
                        continue

                    file_size = int(header.split(":")[1])

                    # 바이너리 데이터 수신
                    data = b""
                    while len(data) < file_size:
                        chunk = ser.read(min(4096, file_size - len(data)))
                        if not chunk:
                            break
                        data += chunk
                        pct = int((i + len(data) / file_size) / total * 100)
                        self.progress.emit(pct, filename)

                    # OK 확인
                    ok_line = ser.readline().decode(errors='replace').strip()

                    # 저장
                    dest_path = os.path.join(self._dest_folder, filename)
                    with open(dest_path, 'wb') as f:
                        f.write(data)

                    saved_paths.append(dest_path)
                    self.file_saved.emit(dest_path)

                self.progress.emit(100, "Done")
        except Exception as e:
            self.error.emit(str(e))

        self.finished.emit(saved_paths)


class FileMode(QWidget):
    """File management mode - local folders and Teensy SD card"""

    open_in_analysis = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self._teensy_port = None
        self._download_thread = None
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # === Left Sidebar ===
        left = QWidget()
        left.setFixedWidth(255)
        ll = QVBoxLayout(left)
        ll.setSpacing(6)
        ll.setContentsMargins(0, 0, 0, 0)

        # SOURCE card
        src_card = QFrame()
        src_card.setObjectName("GlassCard")
        sl = QVBoxLayout(src_card)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(6)

        st = QLabel("SOURCE")
        st.setStyleSheet(
            f"color:{C['muted']}; font-size:9px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        sl.addWidget(st)

        self._local_radio = QRadioButton("Local Folder")
        self._local_radio.setChecked(True)
        sl.addWidget(self._local_radio)

        self._sd_radio = QRadioButton("Teensy SD Card")
        sl.addWidget(self._sd_radio)

        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setObjectName("AccentBtn")
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        sl.addWidget(self._browse_btn)

        self._local_radio.toggled.connect(self._on_source_changed)

        self._path_label = QLabel("No folder selected")
        self._path_label.setWordWrap(True)
        self._path_label.setStyleSheet(
            f"color:{C['muted']}; font-size:10px; background:transparent; border:none;"
        )
        sl.addWidget(self._path_label)
        ll.addWidget(src_card)

        # TEENSY USB card
        usb_card = QFrame()
        usb_card.setObjectName("GlassCard")
        tl = QVBoxLayout(usb_card)
        tl.setContentsMargins(10, 10, 10, 10)
        tl.setSpacing(4)

        tt = QLabel("TEENSY USB")
        tt.setStyleSheet(
            f"color:{C['muted']}; font-size:9px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        tl.addWidget(tt)

        status_row = QHBoxLayout()
        self._usb_dot = QLabel("\u25cf")
        self._usb_dot.setStyleSheet(
            f"color:{C['muted']}; font-size:10px; background:transparent; border:none;"
        )
        status_row.addWidget(self._usb_dot)
        self._usb_label = QLabel("Not detected")
        self._usb_label.setStyleSheet(
            f"color:{C['muted']}; font-size:11px; background:transparent; border:none;"
        )
        status_row.addWidget(self._usb_label)
        status_row.addStretch()
        tl.addLayout(status_row)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("SecondaryBtn")
        refresh_btn.clicked.connect(self._detect_teensy)
        tl.addWidget(refresh_btn)
        ll.addWidget(usb_card)

        # HOW IT WORKS card
        info_card = QFrame()
        info_card.setObjectName("GlassCard")
        il = QVBoxLayout(info_card)
        il.setContentsMargins(10, 10, 10, 10)

        it = QLabel("HOW IT WORKS")
        it.setStyleSheet(
            f"color:{C['muted']}; font-size:9px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        il.addWidget(it)

        for step in [
            "1. Select source (local or SD)",
            "2. Select files from list",
            "3. Download \u2192 name folder \u2192 save",
            "4. Open in Analysis \u2192 chart"
        ]:
            s = QLabel(step)
            s.setStyleSheet(
                f"color:{C['text2']}; font-size:10px; background:transparent; border:none;"
            )
            il.addWidget(s)
        ll.addWidget(info_card)

        ll.addStretch()
        layout.addWidget(left)

        # === Right Side ===
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setSpacing(6)
        rl.setContentsMargins(0, 0, 0, 0)

        # File table
        self._table = QTableWidget(0, 3)
        self._table.setObjectName("FileTable")
        self._table.setHorizontalHeaderLabels(["File", "Size", "Date"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.MultiSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 280)
        self._table.setColumnWidth(1, 80)
        rl.addWidget(self._table, 1)

        # Progress bar
        progress_card = QFrame()
        progress_card.setObjectName("GlassCard")
        progress_card.setFixedHeight(36)
        pl = QHBoxLayout(progress_card)
        pl.setContentsMargins(10, 0, 10, 0)

        pl.addWidget(QLabel("Transfer"))
        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setFormat("Ready")
        self._progress.setFixedHeight(16)
        pl.addWidget(self._progress, 1)
        rl.addWidget(progress_card)

        # Action buttons
        actions = QHBoxLayout()
        actions.setSpacing(6)

        dl_btn = QPushButton("Download")
        dl_btn.setObjectName("AccentBtn")
        dl_btn.clicked.connect(self._on_download_clicked)
        actions.addWidget(dl_btn)

        open_btn = QPushButton("Open in Analysis")
        open_btn.setObjectName("SecondaryBtn")
        open_btn.clicked.connect(self._on_open_in_analysis)
        actions.addWidget(open_btn)

        del_btn = QPushButton("Delete from SD")
        del_btn.setObjectName("RedBtn")
        del_btn.clicked.connect(self._on_delete_clicked)
        actions.addWidget(del_btn)

        actions.addStretch()
        rl.addLayout(actions)

        layout.addWidget(right, 1)

    # =========================================================
    # Source Selection
    # =========================================================

    def _on_source_changed(self, checked: bool):
        if self._sd_radio.isChecked():
            self._browse_btn.setText("List SD Files")
            self._path_label.setText("SD Card \u2014 connect via USB")
        else:
            self._browse_btn.setText("Browse...")
            if self._current_path:
                self._path_label.setText(self._current_path)
            else:
                self._path_label.setText("No folder selected")

    def _on_browse_clicked(self):
        if self._sd_radio.isChecked():
            self._scan_sd_card()
        else:
            self._browse_folder()

    # =========================================================
    # Local Folder
    # =========================================================

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if path:
            self._current_path = path
            self._path_label.setText(path)
            self._scan_local_folder(path)

    def _scan_local_folder(self, path: str):
        self._table.setRowCount(0)
        try:
            files = sorted(os.listdir(path))
        except OSError:
            return

        csv_files = [f for f in files if f.upper().endswith('.CSV')]

        for f in csv_files:
            filepath = os.path.join(path, f)
            try:
                stat = os.stat(filepath)
                size = self._format_size(stat.st_size)
                date = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
            except OSError:
                size = "?"
                date = "?"

            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(f))
            self._table.setItem(row, 1, QTableWidgetItem(size))
            self._table.setItem(row, 2, QTableWidgetItem(date))

    # =========================================================
    # Teensy USB Detection
    # =========================================================

    def _detect_teensy(self):
        try:
            import serial.tools.list_ports
            ports = list(serial.tools.list_ports.comports())
            teensy_ports = [p for p in ports if 'teensy' in p.description.lower()
                          or 'usbmodem' in p.device.lower()]
            if teensy_ports:
                port = teensy_ports[0]
                self._teensy_port = port.device
                self._usb_dot.setStyleSheet(
                    f"color:{C['green']}; font-size:10px; background:transparent; border:none;"
                )
                self._usb_label.setText(port.device)
                self._usb_label.setStyleSheet(
                    f"color:{C['green']}; font-size:11px; background:transparent; border:none;"
                )
            else:
                self._teensy_port = None
                self._usb_dot.setStyleSheet(
                    f"color:{C['muted']}; font-size:10px; background:transparent; border:none;"
                )
                self._usb_label.setText("Not detected")
                self._usb_label.setStyleSheet(
                    f"color:{C['muted']}; font-size:11px; background:transparent; border:none;"
                )
        except ImportError:
            self._usb_label.setText("pyserial not installed")

    # =========================================================
    # SD Card File Listing
    # =========================================================

    def _scan_sd_card(self):
        """List files from Teensy SD card via USB Serial

        Protocol:
          GUI -> Teensy: "LIST\n"
          Teensy -> GUI: "FILE:name.CSV:12345\n" (name:size_bytes)
                         ...
                         "END\n"
        """
        if not self._teensy_port:
            self._detect_teensy()
        if not self._teensy_port:
            QMessageBox.warning(self, "Teensy Not Found",
                "Teensy USB not detected.\n"
                "Connect Teensy via USB and click Refresh first.")
            return

        self._table.setRowCount(0)
        self._progress.setFormat("Scanning SD card...")
        self._progress.setValue(0)

        try:
            import serial
            with serial.Serial(self._teensy_port, 115200, timeout=5) as ser:
                ser.write(b"LIST\n")
                while True:
                    line = ser.readline().decode(errors='replace').strip()
                    if not line or line == "END":
                        break
                    if line.startswith("FILE:"):
                        # FILE:name.CSV:12345
                        parts = line[5:].rsplit(":", 1)
                        if len(parts) == 2:
                            fname, fsize = parts[0], parts[1]
                            row = self._table.rowCount()
                            self._table.insertRow(row)
                            self._table.setItem(row, 0, QTableWidgetItem(fname))
                            self._table.setItem(row, 1, QTableWidgetItem(
                                self._format_size(int(fsize))))
                            self._table.setItem(row, 2, QTableWidgetItem("SD"))

            count = self._table.rowCount()
            self._progress.setFormat(f"Found {count} files on SD")
            self._progress.setValue(100)

        except Exception as e:
            QMessageBox.warning(self, "SD Scan Error",
                f"Failed to read SD card:\n{e}")
            self._progress.setFormat("Scan failed")

    # =========================================================
    # Download
    # =========================================================

    def _on_download_clicked(self):
        """Download selected files with folder name dialog"""
        filenames = self._get_selected_filenames()
        if not filenames:
            QMessageBox.information(self, "No Selection",
                "Select files to download first.")
            return

        # 폴더명 입력 다이얼로그
        today = datetime.now().strftime("%y%m%d")
        default_name = f"{today}_Treadmill"
        folder_name, ok = QInputDialog.getText(
            self, "Download Folder Name",
            f"Enter folder name for {len(filenames)} file(s):",
            text=default_name)

        if not ok or not folder_name.strip():
            return
        folder_name = folder_name.strip()

        # 저장 경로 선택
        base_path = QFileDialog.getExistingDirectory(
            self, "Select Save Location",
            os.path.expanduser("~/Desktop"))
        if not base_path:
            return

        dest_folder = os.path.join(base_path, folder_name)
        os.makedirs(dest_folder, exist_ok=True)

        if self._sd_radio.isChecked():
            self._download_from_sd(filenames, dest_folder)
        else:
            self._copy_local_files(filenames, dest_folder)

    def _copy_local_files(self, filenames: list, dest_folder: str):
        """Copy selected local files to destination folder"""
        saved_paths = []
        total = len(filenames)

        for i, fname in enumerate(filenames):
            src = os.path.join(self._current_path, fname)
            dst = os.path.join(dest_folder, fname)
            pct = int((i + 1) / total * 100)
            self._progress.setValue(pct)
            self._progress.setFormat(f"Copying {fname}... {pct}%")

            try:
                shutil.copy2(src, dst)
                saved_paths.append(dst)
            except OSError as e:
                QMessageBox.warning(self, "Copy Error",
                    f"Failed to copy {fname}:\n{e}")

        self._progress.setValue(100)
        self._progress.setFormat(f"Copied {len(saved_paths)} files")
        self._current_path = dest_folder
        self._path_label.setText(dest_folder)
        self._ask_open_in_analysis(saved_paths)

    def _download_from_sd(self, filenames: list, dest_folder: str):
        """Download files from Teensy SD via USB Serial (background thread)"""
        if not self._teensy_port:
            QMessageBox.warning(self, "No Teensy",
                "Teensy USB not detected. Click Refresh first.")
            return

        self._progress.setValue(0)
        self._progress.setFormat("Starting download...")

        self._download_thread = TeensyDownloadThread(
            self._teensy_port, filenames, dest_folder)

        self._download_thread.progress.connect(
            lambda pct, fname: (
                self._progress.setValue(pct),
                self._progress.setFormat(f"Downloading {fname}... {pct}%")
            ))
        self._download_thread.error.connect(
            lambda msg: QMessageBox.warning(self, "Download Error", msg))
        self._download_thread.finished.connect(
            lambda paths: self._on_sd_download_finished(paths, dest_folder))

        self._download_thread.start()

    def _on_sd_download_finished(self, paths: list, dest_folder: str):
        """SD download complete callback"""
        self._progress.setValue(100)
        self._progress.setFormat(f"Downloaded {len(paths)} files")
        self._current_path = dest_folder
        self._path_label.setText(dest_folder)
        self._download_thread = None
        self._ask_open_in_analysis(paths)

    def _ask_open_in_analysis(self, paths: list):
        """Ask user to open downloaded files in Analysis mode"""
        if not paths:
            return
        reply = QMessageBox.question(
            self, "Download Complete",
            f"{len(paths)} file(s) saved.\n"
            f"Open in Analysis mode?",
            QMessageBox.Yes | QMessageBox.No)

        if reply == QMessageBox.Yes:
            for p in paths:
                self.open_in_analysis.emit(p)

    # =========================================================
    # Delete from SD
    # =========================================================

    def _on_delete_clicked(self):
        """Delete selected files from Teensy SD card"""
        if not self._sd_radio.isChecked():
            QMessageBox.information(self, "SD Only",
                "Delete is only available for Teensy SD Card mode.")
            return

        filenames = self._get_selected_filenames()
        if not filenames:
            return

        if not self._teensy_port:
            self._detect_teensy()
        if not self._teensy_port:
            QMessageBox.warning(self, "No Teensy",
                "Teensy USB not detected.")
            return

        reply = QMessageBox.warning(
            self, "Delete Files",
            f"Delete {len(filenames)} file(s) from SD card?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No)

        if reply != QMessageBox.Yes:
            return

        try:
            import serial
            deleted = 0
            with serial.Serial(self._teensy_port, 115200, timeout=5) as ser:
                for fname in filenames:
                    ser.write(f"DEL:{fname}\n".encode())
                    resp = ser.readline().decode(errors='replace').strip()
                    if resp == "OK":
                        deleted += 1

            QMessageBox.information(self, "Deleted",
                f"Deleted {deleted}/{len(filenames)} files from SD.")
            # Refresh file list
            self._scan_sd_card()

        except Exception as e:
            QMessageBox.warning(self, "Delete Error", str(e))

    # =========================================================
    # Open in Analysis
    # =========================================================

    def _on_open_in_analysis(self):
        """Send selected files to Analysis mode"""
        selected_rows = set()
        for item in self._table.selectedItems():
            selected_rows.add(item.row())

        for row in sorted(selected_rows):
            filename = self._table.item(row, 0).text()
            filepath = os.path.join(self._current_path, filename)
            if os.path.exists(filepath):
                self.open_in_analysis.emit(filepath)

    # =========================================================
    # Helpers
    # =========================================================

    def _get_selected_filenames(self) -> list:
        """Get list of selected filenames from table"""
        selected_rows = set()
        for item in self._table.selectedItems():
            selected_rows.add(item.row())
        filenames = []
        for row in sorted(selected_rows):
            item = self._table.item(row, 0)
            if item:
                filenames.append(item.text())
        return filenames

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"
