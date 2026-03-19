"""
H-Walker Camera Mode - Coming Soon placeholder

Jetson Orin NX integration (future)
QPainter-based premium placeholder with animated ring
"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QLinearGradient

from ui.styles import C


class CameraRingWidget(QWidget):
    """QPainter 기반 animated ring placeholder"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self.setFixedSize(160, 160)

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        self._angle = (self._angle + 3) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cx, cy, r = self.width() // 2, self.height() // 2, 60

        # Outer ring (dim)
        p.setPen(QPen(QColor(40, 40, 55), 3))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Rotating arc (gradient glow)
        for glow_w, alpha in [(8, 12), (5, 25), (3, 50)]:
            glow_c = QColor(76, 158, 255, alpha)
            p.setPen(QPen(glow_c, glow_w, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(cx - r, cy - r, r * 2, r * 2,
                      self._angle * 16, 120 * 16)

        # Inner ring
        p.setPen(QPen(QColor(76, 158, 255, 80), 2))
        inner_r = 40
        p.drawEllipse(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)

        # Center dot
        p.setBrush(QColor(76, 158, 255, 60))
        p.setPen(Qt.NoPen)
        p.drawEllipse(cx - 6, cy - 6, 12, 12)

        # Center text
        p.setPen(QColor(C['muted']))
        p.setFont(QFont("Inter", 10, QFont.Bold))
        p.drawText(0, 0, self.width(), self.height(), Qt.AlignCenter, "CAM")

        p.end()


class CameraMode(QWidget):
    """Camera/Vision mode - premium placeholder for Jetson Orin NX"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        # Ring widget
        self._ring = CameraRingWidget()
        layout.addWidget(self._ring, alignment=Qt.AlignCenter)

        # Title
        title = QLabel("Camera / Vision")
        title.setStyleSheet(
            f"color:{C['text1']}; font-size:22px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Description
        desc = QLabel("Jetson Orin NX integration\nPose Estimation + Skeleton Overlay")
        desc.setStyleSheet(
            f"color:{C['muted']}; font-size:12px; line-height:1.6; "
            f"background:transparent; border:none;"
        )
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        # Feature cards (horizontal)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for feat_title, feat_desc in [
            ("Real-time", "30fps pose tracking"),
            ("Skeleton", "Joint overlay"),
            ("Analysis", "Gait kinematics"),
        ]:
            card = QFrame()
            card.setObjectName("GlassCard")
            card.setFixedSize(130, 60)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(2)
            ft = QLabel(feat_title)
            ft.setStyleSheet(
                f"color:{C['blue']}; font-size:11px; font-weight:700; "
                f"background:transparent; border:none;"
            )
            ft.setAlignment(Qt.AlignCenter)
            cl.addWidget(ft)
            fd = QLabel(feat_desc)
            fd.setStyleSheet(
                f"color:{C['muted']}; font-size:9px; "
                f"background:transparent; border:none;"
            )
            fd.setAlignment(Qt.AlignCenter)
            cl.addWidget(fd)
            cards.addWidget(card)
        layout.addLayout(cards)

        # Badge
        badge = QLabel("  COMING SOON  ")
        badge.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 rgba(76,158,255,0.12), stop:1 rgba(45,212,191,0.12));"
            f"color:{C['blue']}; border:1px solid rgba(76,158,255,0.25);"
            f"border-radius:12px; padding:6px 18px; font-size:11px; "
            f"font-weight:700; letter-spacing:2px;"
        )
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedWidth(160)
        layout.addWidget(badge, alignment=Qt.AlignCenter)
