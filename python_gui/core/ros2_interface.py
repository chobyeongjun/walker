"""
ROS2 Interface for ARWalker GUI - Future Integration Ready

★ This module provides the architecture for ROS2 integration.
  Currently a skeleton - activate when ROS2 environment is ready.

Design:
- rclpy executor in separate thread (doesn't block GUI)
- Publisher: /walker/state (sensor_msgs/JointState)
- Subscriber: /walker/command (std_msgs/String)
- Signal-based communication with Qt main thread

Requirements:
- ROS2 (Humble/Iron recommended)
- rclpy, sensor_msgs, std_msgs packages

Usage (when ready):
    from core.ros2_interface import ROS2Interface

    ros2 = ROS2Interface()
    ros2.command_received.connect(self._on_ros2_command)
    ros2.start()

    # Publish state
    ros2.publish_state(walker_data)
"""

import threading
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass
from PyQt5.QtCore import QThread, pyqtSignal, QObject

# ============================================================
# ROS2 Node Thread (Skeleton - Activate when ROS2 ready)
# ============================================================

class ROS2Signals(QObject):
    """ROS2 interface signals for Qt communication."""
    command_received = pyqtSignal(str)    # Command from ROS2 subscriber
    status_changed = pyqtSignal(str)      # ROS2 connection status
    error = pyqtSignal(str)               # Error messages


class ROS2Interface(QThread):
    """
    ROS2 interface thread for Walker GUI.

    ★★ Design Principles:
    1. Non-blocking: rclpy spin in separate thread
    2. Qt-compatible: Signal-based communication
    3. Configurable: Topic names, QoS settings
    4. Graceful: Clean shutdown, reconnection support

    Topics:
    - /walker/state (pub): JointState with motor positions, velocities, efforts
    - /walker/gcp (pub): Float32MultiArray with GCP values
    - /walker/command (sub): String commands from ROS2 network

    QoS Profile:
    - Reliability: BEST_EFFORT (for real-time data)
    - Durability: VOLATILE
    - History: KEEP_LAST (depth=1 for latest data)
    """

    # Topic configuration
    TOPIC_STATE = '/walker/state'
    TOPIC_GCP = '/walker/gcp'
    TOPIC_COMMAND = '/walker/command'

    def __init__(self, node_name: str = 'walker_gui', parent=None):
        super().__init__(parent)
        self.signals = ROS2Signals()
        self._node_name = node_name
        self._running = False
        self._node = None
        self._state_pub = None
        self._gcp_pub = None
        self._command_sub = None
        self._pending_state = None
        self._state_lock = threading.Lock()

    @property
    def command_received(self):
        """Convenience property for signal access."""
        return self.signals.command_received

    def run(self):
        """ROS2 node execution loop."""
        try:
            import rclpy
            from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
            from sensor_msgs.msg import JointState
            from std_msgs.msg import String, Float32MultiArray
        except ImportError as e:
            self.signals.error.emit(f"ROS2 not available: {e}. "
                                   "Ensure ROS2 is installed and sourced.")
            return

        # Initialize ROS2
        rclpy.init()
        self._node = rclpy.create_node(self._node_name)

        # QoS for real-time data
        qos_realtime = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Publishers
        self._state_pub = self._node.create_publisher(
            JointState, self.TOPIC_STATE, qos_realtime
        )
        self._gcp_pub = self._node.create_publisher(
            Float32MultiArray, self.TOPIC_GCP, qos_realtime
        )

        # Subscriber
        self._command_sub = self._node.create_subscription(
            String, self.TOPIC_COMMAND, self._command_callback, 10
        )

        self.signals.status_changed.emit("ROS2 Connected")
        self._running = True

        # Spin loop
        while self._running:
            rclpy.spin_once(self._node, timeout_sec=0.01)

            # Publish pending state
            with self._state_lock:
                if self._pending_state is not None:
                    self._publish_state_internal(self._pending_state)
                    self._pending_state = None

        # Cleanup
        self._node.destroy_node()
        rclpy.shutdown()
        self.signals.status_changed.emit("ROS2 Disconnected")

    def _command_callback(self, msg):
        """Handle incoming command from ROS2."""
        self.signals.command_received.emit(msg.data)

    def _publish_state_internal(self, data: Dict[str, Any]):
        """Internal state publishing (called from ROS2 thread)."""
        try:
            from sensor_msgs.msg import JointState
            from std_msgs.msg import Float32MultiArray
            from builtin_interfaces.msg import Time

            # JointState message
            state_msg = JointState()
            state_msg.header.stamp = self._node.get_clock().now().to_msg()
            state_msg.name = ['left_motor', 'right_motor']
            state_msg.position = [data.get('l_pos', 0.0), data.get('r_pos', 0.0)]
            state_msg.velocity = [data.get('l_vel', 0.0), data.get('r_vel', 0.0)]
            state_msg.effort = [data.get('l_curr', 0.0), data.get('r_curr', 0.0)]
            self._state_pub.publish(state_msg)

            # GCP message
            gcp_msg = Float32MultiArray()
            gcp_msg.data = [data.get('l_gcp', 0.0), data.get('r_gcp', 0.0)]
            self._gcp_pub.publish(gcp_msg)

        except Exception as e:
            self.signals.error.emit(f"ROS2 publish error: {e}")

    def publish_state(self, walker_data):
        """
        Queue state for publishing (thread-safe, called from main thread).

        Args:
            walker_data: WalkerData object with sensor values
        """
        state = {
            'l_pos': walker_data.l_motor_pos,
            'r_pos': walker_data.r_motor_pos,
            'l_vel': walker_data.l_motor_vel,
            'r_vel': walker_data.r_motor_vel,
            'l_curr': walker_data.l_motor_curr,
            'r_curr': walker_data.r_motor_curr,
            'l_gcp': walker_data.l_gcp,
            'r_gcp': walker_data.r_gcp,
        }
        with self._state_lock:
            self._pending_state = state

    def stop(self):
        """Stop ROS2 interface."""
        self._running = False
        self.wait(2000)


# ============================================================
# ROS2 Configuration Helpers
# ============================================================

class ROS2Config:
    """
    ROS2 configuration and utilities.

    Provides:
    - Environment checking
    - Topic discovery
    - Parameter management
    """

    @staticmethod
    def is_ros2_available() -> bool:
        """Check if ROS2 is available."""
        try:
            import rclpy
            return True
        except ImportError:
            return False

    @staticmethod
    def get_ros2_version() -> Optional[str]:
        """Get ROS2 distribution name."""
        import os
        return os.environ.get('ROS_DISTRO', None)


# ============================================================
# Usage Example (for reference)
# ============================================================
"""
# In MainWindow.__init__():

    # Initialize ROS2 (optional, enable when ready)
    self._ros2_enabled = False
    if self._ros2_enabled and ROS2Config.is_ros2_available():
        from core.ros2_interface import ROS2Interface
        self._ros2 = ROS2Interface()
        self._ros2.command_received.connect(self._on_ros2_command)
        self._ros2.signals.error.connect(self._on_ros2_error)
        self._ros2.start()

# In _on_data_received():
    # Publish to ROS2
    if hasattr(self, '_ros2') and self._ros2:
        for walker_data in results:
            self._ros2.publish_state(walker_data)

# In MainWindow.closeEvent():
    if hasattr(self, '_ros2') and self._ros2:
        self._ros2.stop()
"""
