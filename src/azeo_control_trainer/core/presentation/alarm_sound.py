"""Audible alarm sounder for critical alarm notification.

Uses QApplication.beep() as the primary mechanism, with different
repeat patterns for TRIP vs HIGH vs MEDIUM severities.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


class AlarmSounder:
    """Generates audible alarm tones for unacknowledged alarms.

    Different severities produce different beep patterns:
      - TRIP: triple beep every 2 seconds
      - HIGH: double beep every 3 seconds
      - MEDIUM: single beep every 5 seconds
    """

    def __init__(self):
        self._muted = False
        self._active_severity: str | None = None
        self._beep_count = 0
        self._beep_remaining = 0

        # Timer for individual beeps within a pattern
        self._beep_timer = QTimer()
        self._beep_timer.timeout.connect(self._do_beep)
        self._beep_timer.setSingleShot(True)

        # Timer for repeating the pattern
        self._repeat_timer = QTimer()
        self._repeat_timer.timeout.connect(self._start_pattern)

    def __del__(self):
        # At interpreter shutdown, Qt may have already torn down the
        # C++ side of the QTimers (QApplication destroys child Q-objects
        # before Python's GC runs __del__). The wrapped pointers then
        # raise RuntimeError on access; swallow that — there's nothing
        # useful to do at this point. Also handles the case where the
        # timers were never created (constructor raised mid-way).
        for attr in ("_repeat_timer", "_beep_timer"):
            t = getattr(self, attr, None)
            if t is None:
                continue
            try:
                t.stop()
            except (RuntimeError, ReferenceError):
                pass

    # Severity -> (num_beeps, repeat_interval_ms)
    _PATTERNS: dict[str, tuple[int, int]] = {
        "TRIP": (3, 2000),
        "HIGH": (2, 3000),
        "MEDIUM": (1, 5000),
    }

    def play_alarm(self, severity: str) -> None:
        """Start alarm sound for the given severity level."""
        if self._muted:
            return
        if severity not in self._PATTERNS:
            return

        # Only escalate, never downgrade while playing
        priority = ["MEDIUM", "HIGH", "TRIP"]
        if self._active_severity is not None:
            cur_idx = priority.index(self._active_severity) if self._active_severity in priority else -1
            new_idx = priority.index(severity) if severity in priority else -1
            if new_idx <= cur_idx:
                return

        self._active_severity = severity
        num_beeps, interval = self._PATTERNS[severity]
        self._beep_count = num_beeps

        self._repeat_timer.stop()
        self._repeat_timer.setInterval(interval)
        self._start_pattern()
        self._repeat_timer.start()

    def silence(self) -> None:
        """Mute the alarm horn."""
        self._muted = True
        self._repeat_timer.stop()
        self._beep_timer.stop()
        self._active_severity = None

    def unmute(self) -> None:
        """Re-enable alarm sounds (does not restart any pattern)."""
        self._muted = False

    def stop(self) -> None:
        """Stop all alarm sounds and reset state (does not affect mute)."""
        self._repeat_timer.stop()
        self._beep_timer.stop()
        self._active_severity = None

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_active(self) -> bool:
        return self._active_severity is not None

    def update_alarm_state(self, has_unack_trip: bool,
                           has_unack_high: bool,
                           has_unack_medium: bool) -> None:
        """Called on each tick to evaluate whether sound should play or stop.

        Determines the highest-severity unacknowledged alarm and starts
        the appropriate pattern if not already playing. Stops if no
        unacknowledged alarms remain.
        """
        if self._muted:
            return

        if has_unack_trip:
            target = "TRIP"
        elif has_unack_high:
            target = "HIGH"
        elif has_unack_medium:
            target = "MEDIUM"
        else:
            self.stop()
            return

        if self._active_severity != target:
            self.stop()
            self.play_alarm(target)

    # ---- internal ----

    def _start_pattern(self) -> None:
        """Begin a burst of beeps."""
        if self._muted:
            self._repeat_timer.stop()
            return
        self._beep_remaining = self._beep_count
        self._do_beep()

    def _do_beep(self) -> None:
        """Emit one beep and schedule the next if needed."""
        if self._beep_remaining <= 0 or self._muted:
            return
        QApplication.beep()
        self._beep_remaining -= 1
        if self._beep_remaining > 0:
            self._beep_timer.start(200)  # 200ms between beeps in a burst
