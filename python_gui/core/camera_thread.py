"""
Camera Thread for ARWalker GUI - Future Integration Ready

★ This module provides the architecture for camera integration.
  Currently a skeleton - activate when hardware is ready.

Design:
- Separate thread for camera capture (doesn't block GUI)
- cv2.VideoCapture → QImage conversion
- Signal-based frame delivery to main thread
- Triple buffering for tear-free display

Usage (when ready):
    from core.camera_thread import CameraThread

    camera = CameraThread(camera_index=0)
    camera.frame_ready.connect(plot_widget.set_camera_frame)
    camera.start()
"""

import threading
import time
from typing import Optional
from PyQt5.QtCore import QThread, pyqtSignal, QObject
from PyQt5.QtGui import QImage

# ============================================================
# Camera Thread (Skeleton - Activate when hardware ready)
# ============================================================

class CameraSignals(QObject):
    """Camera thread signals."""
    frame_ready = pyqtSignal(object)  # QImage
    error = pyqtSignal(str)
    fps_updated = pyqtSignal(float)


class CameraThread(QThread):
    """
    Camera capture thread with QImage output.

    ★★ Design Principles:
    1. Non-blocking: Camera I/O in separate thread
    2. Efficient: cv2 → QImage conversion with minimal copying
    3. Throttled: Configurable FPS (default 30Hz)
    4. Graceful: Clean shutdown, error recovery

    Future Features:
    - Multiple camera support
    - Resolution configuration
    - Recording capability
    """

    # Configuration
    DEFAULT_FPS = 30
    DEFAULT_RESOLUTION = (640, 480)

    def __init__(self, camera_index: int = 0, target_fps: int = None, parent=None):
        super().__init__(parent)
        self.signals = CameraSignals()
        self._camera_index = camera_index
        self._target_fps = target_fps or self.DEFAULT_FPS
        self._running = False
        self._capture = None

        # Frame timing
        self._frame_interval = 1.0 / self._target_fps
        self._last_frame_time = 0
        self._fps_counter = 0
        self._fps_timer = 0

    @property
    def frame_ready(self):
        """Convenience property for signal access."""
        return self.signals.frame_ready

    def run(self):
        """Camera capture loop."""
        try:
            import cv2
        except ImportError:
            self.signals.error.emit("OpenCV (cv2) not installed. Run: pip install opencv-python")
            return

        self._running = True
        self._capture = cv2.VideoCapture(self._camera_index)

        if not self._capture.isOpened():
            self.signals.error.emit(f"Failed to open camera {self._camera_index}")
            return

        # Set resolution
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.DEFAULT_RESOLUTION[0])
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.DEFAULT_RESOLUTION[1])

        self._fps_timer = time.time()

        while self._running:
            current_time = time.time()

            # Frame rate limiting
            if current_time - self._last_frame_time < self._frame_interval:
                time.sleep(0.001)  # 1ms sleep to prevent busy-wait
                continue

            ret, frame = self._capture.read()
            if not ret:
                self.signals.error.emit("Failed to read frame")
                time.sleep(0.1)
                continue

            # Convert BGR → RGB → QImage
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            bytes_per_line = ch * w

            # Create QImage (shares memory with numpy array)
            qimage = QImage(
                rgb_frame.data,
                w, h,
                bytes_per_line,
                QImage.Format_RGB888
            ).copy()  # .copy() ensures data independence

            self.signals.frame_ready.emit(qimage)
            self._last_frame_time = current_time

            # FPS calculation
            self._fps_counter += 1
            if current_time - self._fps_timer >= 1.0:
                self.signals.fps_updated.emit(self._fps_counter)
                self._fps_counter = 0
                self._fps_timer = current_time

        # Cleanup
        if self._capture:
            self._capture.release()

    def stop(self):
        """Stop camera capture."""
        self._running = False
        self.wait(1000)

    def set_resolution(self, width: int, height: int):
        """Set camera resolution (requires restart)."""
        # For future use
        pass


# ============================================================
# Camera Manager (Multi-camera support - Future)
# ============================================================

class CameraManager:
    """
    Manager for multiple cameras (future implementation).

    Design Notes:
    - Enumerate available cameras
    - Manage multiple CameraThread instances
    - Provide unified interface for GUI
    """

    @staticmethod
    def list_cameras() -> list:
        """List available camera indices."""
        try:
            import cv2
        except ImportError:
            return []

        cameras = []
        for i in range(5):  # Check first 5 indices
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                cameras.append(i)
                cap.release()
        return cameras


# ============================================================
# Usage Example (for reference)
# ============================================================
"""
# In MainWindow.__init__():

    # Initialize camera (optional, enable when ready)
    self._camera_enabled = False
    if self._camera_enabled:
        from core.camera_thread import CameraThread
        self._camera = CameraThread(camera_index=0)
        self._camera.frame_ready.connect(self.plot_widget.set_camera_frame)
        self._camera.signals.error.connect(self._on_camera_error)
        self._camera.start()

# In MainWindow.closeEvent():

    if hasattr(self, '_camera') and self._camera:
        self._camera.stop()
"""
