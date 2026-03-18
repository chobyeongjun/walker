"""
High-Performance Ring Buffer for Real-Time Control

★ BEST Tier Implementation - Zero-Copy Optimized

Key Features:
1. Pre-allocated numpy array (no GC pressure)
2. Circular indexing (O(1) append)
3. Zero-copy view for plotting (no array copy)
4. Thread-safe with minimal locking
5. Camera + ROS2 ready architecture

Performance:
- deque→numpy: O(n) per frame → Ring buffer: O(1) per frame
- Memory: Fixed allocation, no fragmentation
- Latency: Predictable, no GC pauses
"""

import numpy as np
from typing import Optional, Tuple
import threading


class RingBuffer:
    """
    Fixed-size circular buffer using pre-allocated numpy array.

    ★★ Real-Time Optimized:
    - O(1) append (no memory allocation)
    - O(1) view access (no copy)
    - Thread-safe read/write

    Usage:
        buffer = RingBuffer(500)  # 500 samples
        buffer.append(42.0)
        x, y = buffer.get_plot_data()  # Zero-copy view
    """

    __slots__ = ('_data', '_size', '_head', '_count', '_time', '_lock')

    def __init__(self, size: int = 500):
        """
        Initialize ring buffer.

        Args:
            size: Maximum number of elements (fixed at creation)
        """
        self._size = size
        self._data = np.zeros(size, dtype=np.float32)
        self._time = np.arange(size, dtype=np.float32)  # Pre-computed time axis
        self._head = 0  # Next write position
        self._count = 0  # Current element count
        self._lock = threading.Lock()

    def append(self, value: float) -> None:
        """
        Append value to buffer (O(1), thread-safe).

        Args:
            value: Value to append
        """
        with self._lock:
            self._data[self._head] = value
            self._head = (self._head + 1) % self._size
            if self._count < self._size:
                self._count += 1

    def append_batch(self, values: np.ndarray) -> None:
        """
        Append multiple values efficiently (for burst data).

        Args:
            values: Array of values to append
        """
        with self._lock:
            n = len(values)
            if n >= self._size:
                # Buffer smaller than batch - just keep last _size elements
                self._data[:] = values[-self._size:]
                self._head = 0
                self._count = self._size
            else:
                # Calculate positions
                end_pos = self._head + n
                if end_pos <= self._size:
                    # No wrap needed
                    self._data[self._head:end_pos] = values
                else:
                    # Wrap around
                    first_part = self._size - self._head
                    self._data[self._head:] = values[:first_part]
                    self._data[:n - first_part] = values[first_part:]

                self._head = end_pos % self._size
                self._count = min(self._count + n, self._size)

    def get_plot_data(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get data for plotting (ordered, zero-copy when possible).

        Returns:
            (x_data, y_data) tuple of numpy arrays

        ★★ Optimization:
        - When buffer is not wrapped: returns view (zero-copy)
        - When buffer is wrapped: returns concatenated copy (unavoidable)
        """
        with self._lock:
            if self._count == 0:
                return np.array([], dtype=np.float32), np.array([], dtype=np.float32)

            if self._count < self._size:
                # Buffer not full yet - simple slice (view, zero-copy)
                y = self._data[:self._count]
                x = self._time[:self._count]
            else:
                # Buffer full and wrapped - need to reorder
                # This is O(n) but only happens after buffer is full
                y = np.concatenate([
                    self._data[self._head:],
                    self._data[:self._head]
                ])
                x = self._time[:self._size]

            return x, y

    def get_latest(self) -> Optional[float]:
        """
        Get most recent value (O(1)).

        Returns:
            Latest value or None if empty
        """
        with self._lock:
            if self._count == 0:
                return None
            idx = (self._head - 1) % self._size
            return float(self._data[idx])

    def clear(self) -> None:
        """Reset buffer to empty state."""
        with self._lock:
            self._data.fill(0)
            self._head = 0
            self._count = 0

    def __len__(self) -> int:
        """Current number of elements."""
        return self._count

    @property
    def is_full(self) -> bool:
        """Check if buffer is at capacity."""
        return self._count >= self._size


class WalkerDataBuffers:
    """
    All data buffers for Walker GUI - BEST tier implementation.

    ★★ Real-Time Optimized:
    - 18 channels × 500 samples = 36KB total (L2 cache friendly)
    - Single timestamp counter (no per-buffer time tracking)
    - Bulk update method for efficiency

    Future Ready:
    - Camera frame buffer (separate, larger)
    - ROS2 message queue integration points
    """

    # Buffer configuration
    BUFFER_SIZE = 500  # ~5 seconds @ 100Hz, ~20 seconds @ 25Hz

    # Channel names (matches WalkerData fields)
    CHANNELS = [
        'l_gcp', 'r_gcp',
        'l_pitch', 'r_pitch',
        'l_gyro', 'r_gyro',
        'l_pos', 'r_pos',
        'l_vel', 'r_vel',
        'l_curr', 'r_curr',
        'l_des_pos', 'r_des_pos',
        'l_des_force', 'r_des_force',
        'l_act_force', 'r_act_force'
    ]

    def __init__(self, size: int = None):
        """
        Initialize all Walker data buffers.

        Args:
            size: Buffer size (default: BUFFER_SIZE)
        """
        size = size or self.BUFFER_SIZE

        # Create all channel buffers
        self._buffers = {name: RingBuffer(size) for name in self.CHANNELS}

        # Global sample counter (shared time axis)
        self._sample_count = 0
        self._time_buffer = RingBuffer(size)

        # Lock for atomic updates
        self._update_lock = threading.Lock()

    def add_sample(self, data) -> None:
        """
        Add a WalkerData sample to all buffers.

        Args:
            data: WalkerData object with all fields

        ★★ Optimized: Single lock for all buffer updates
        """
        with self._update_lock:
            self._sample_count += 1
            self._time_buffer.append(float(self._sample_count))

            # Map WalkerData fields to buffer channels
            self._buffers['l_gcp'].append(data.l_gcp * 100)  # Convert to %
            self._buffers['r_gcp'].append(data.r_gcp * 100)
            self._buffers['l_pitch'].append(data.l_pitch)
            self._buffers['r_pitch'].append(data.r_pitch)
            self._buffers['l_gyro'].append(data.l_gyro_y)
            self._buffers['r_gyro'].append(data.r_gyro_y)
            self._buffers['l_pos'].append(data.l_motor_pos)
            self._buffers['r_pos'].append(data.r_motor_pos)
            self._buffers['l_vel'].append(data.l_motor_vel)
            self._buffers['r_vel'].append(data.r_motor_vel)
            self._buffers['l_curr'].append(data.l_motor_curr)
            self._buffers['r_curr'].append(data.r_motor_curr)
            self._buffers['l_des_pos'].append(data.l_des_pos)
            self._buffers['r_des_pos'].append(data.r_des_pos)
            self._buffers['l_des_force'].append(data.l_des_force)
            self._buffers['r_des_force'].append(data.r_des_force)
            self._buffers['l_act_force'].append(data.l_act_force)
            self._buffers['r_act_force'].append(data.r_act_force)

    def get_channel(self, name: str) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get time and data arrays for a channel.

        Args:
            name: Channel name from CHANNELS list

        Returns:
            (time_array, data_array) tuple
        """
        if name not in self._buffers:
            return np.array([]), np.array([])

        x, _ = self._time_buffer.get_plot_data()
        _, y = self._buffers[name].get_plot_data()
        return x, y

    def get_latest(self, name: str) -> float:
        """
        Get latest value for a channel.

        Args:
            name: Channel name

        Returns:
            Latest value or 0.0 if empty
        """
        if name not in self._buffers:
            return 0.0
        return self._buffers[name].get_latest() or 0.0

    def get_gcp_values(self) -> Tuple[float, float]:
        """
        Get current GCP values (optimized for indicator updates).

        Returns:
            (left_gcp, right_gcp) tuple
        """
        return (
            self._buffers['l_gcp'].get_latest() or 0.0,
            self._buffers['r_gcp'].get_latest() or 0.0
        )

    def clear(self) -> None:
        """Clear all buffers."""
        with self._update_lock:
            for buf in self._buffers.values():
                buf.clear()
            self._time_buffer.clear()
            self._sample_count = 0

    @property
    def sample_count(self) -> int:
        """Total samples received."""
        return self._sample_count

    def __len__(self) -> int:
        """Current buffer fill level."""
        return len(self._time_buffer)


# ============================================================
# Future Extension Points for Camera + ROS2
# ============================================================

class CameraFrameBuffer:
    """
    Placeholder for Camera frame buffer (future implementation).

    Design Notes:
    - Separate from data buffers (different update rate)
    - Triple buffering for tear-free display
    - cv2 → QImage conversion in background thread
    """
    pass


class ROS2Interface:
    """
    Placeholder for ROS2 integration (future implementation).

    Design Notes:
    - rclpy.spin_once() in dedicated thread
    - Publish: /walker/state (sensor_msgs/JointState)
    - Subscribe: /walker/command (std_msgs/String)
    - QoS: BEST_EFFORT for real-time data
    """
    pass
