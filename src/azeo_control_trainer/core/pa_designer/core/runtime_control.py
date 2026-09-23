from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable


class RuntimeAbortRequested(RuntimeError):
    """Raised when a supervised runtime requests an abort."""


@dataclass
class RuntimeControl:
    """Thread-safe runtime control used by the desktop UI.

    The core engine remains safe for CLI/batch use. The UI can inject this
    object as the engine sleep function so long-running delay/ramp/wait steps
    can be paused, resumed, or aborted without rewriting procedure logic.

    Pause time is tracked so the engine can compute deadlines against
    ``adjusted_monotonic()``: while paused the procedure clock freezes, so a
    wait_until/delay timeout budget is not consumed by operator pauses.
    """

    poll_interval: float = 0.1
    _pause_event: threading.Event = field(default_factory=threading.Event, init=False)
    _abort_event: threading.Event = field(default_factory=threading.Event, init=False)
    _callbacks: list[Callable[[], None]] = field(default_factory=list, init=False)
    _breakpoint_callbacks: list[Callable[[str], None]] = field(default_factory=list, init=False)
    _step_event: threading.Event = field(default_factory=threading.Event, init=False)
    _breakpoint_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _breakpoints: set[str] = field(default_factory=set, init=False)
    _active_step: str = field(default="", init=False)
    _stop_at_next_boundary: bool = field(default=False, init=False)
    _clock_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _paused_total: float = field(default=0.0, init=False)
    _paused_at: float | None = field(default=None, init=False)

    def pause(self) -> None:
        with self._clock_lock:
            if self._paused_at is None:
                self._paused_at = time.monotonic()
        self._pause_event.set()

    def resume(self) -> None:
        with self._clock_lock:
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = None
        self._pause_event.clear()
        self._step_event.set()

    def abort(self) -> None:
        self._abort_event.set()

    def reset(self) -> None:
        with self._clock_lock:
            self._paused_total = 0.0
            self._paused_at = None
        self._pause_event.clear()
        self._abort_event.clear()
        self._step_event.clear()
        self._active_step = ""
        self._stop_at_next_boundary = False

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def is_abort_requested(self) -> bool:
        return self._abort_event.is_set()

    def add_tick_callback(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    def add_breakpoint_callback(self, callback: Callable[[str], None]) -> None:
        self._breakpoint_callbacks.append(callback)

    @property
    def active_step(self) -> str:
        return self._active_step

    def set_breakpoints(self, step_ids: set[str]) -> None:
        with self._breakpoint_lock:
            self._breakpoints = {str(step_id) for step_id in step_ids if str(step_id)}

    def request_single_step(self) -> None:
        """Release the current step and stop at the next step boundary."""
        with self._breakpoint_lock:
            self._stop_at_next_boundary = True
        self.resume()

    def before_step(self, step_id: str) -> bool:
        """Apply breakpoint/single-step policy; return True when it stopped."""
        self._active_step = str(step_id)
        if self._abort_event.is_set():
            raise RuntimeAbortRequested("Operator requested procedure abort")
        with self._breakpoint_lock:
            hit = self._active_step in self._breakpoints or self._stop_at_next_boundary
            self._stop_at_next_boundary = False
        if not hit and not self._pause_event.is_set():
            return False
        self.pause()
        self._step_event.clear()
        if hit:
            for callback in list(self._breakpoint_callbacks):
                callback(self._active_step)
        while self._pause_event.is_set() and not self._step_event.wait(self.poll_interval):
            if self._abort_event.is_set():
                raise RuntimeAbortRequested("Operator requested procedure abort")
            self._emit_tick()
        if self._abort_event.is_set():
            raise RuntimeAbortRequested("Operator requested procedure abort")
        # Resume clears pause permanently. Single-step leaves it set so the
        # next boundary stops again.
        return True

    def adjusted_monotonic(self) -> float:
        """Monotonic clock that freezes while the procedure is paused."""
        with self._clock_lock:
            now = time.monotonic()
            paused = self._paused_total
            if self._paused_at is not None:
                paused += now - self._paused_at
            return now - paused

    def checkpoint(self) -> None:
        """Raise on abort; block while paused. Called between steps/nodes."""
        if self._abort_event.is_set():
            raise RuntimeAbortRequested("Operator requested procedure abort")
        while self._pause_event.is_set():
            if self._abort_event.is_set():
                raise RuntimeAbortRequested("Operator requested procedure abort")
            self._emit_tick()
            time.sleep(self.poll_interval)

    def controlled_sleep(self, seconds: float) -> None:
        """Sleep in small chunks so pause/resume/abort takes effect quickly.

        Sleeps on the adjusted clock: time spent paused extends the sleep so
        a delay step does not silently elapse while the run is paused.
        """
        end = self.adjusted_monotonic() + max(seconds, 0.0)
        while self.adjusted_monotonic() < end:
            if self._abort_event.is_set():
                raise RuntimeAbortRequested("Operator requested procedure abort")
            while self._pause_event.is_set():
                if self._abort_event.is_set():
                    raise RuntimeAbortRequested("Operator requested procedure abort")
                self._emit_tick()
                time.sleep(self.poll_interval)
            self._emit_tick()
            time.sleep(min(self.poll_interval, max(0.0, end - self.adjusted_monotonic())))

    def _emit_tick(self) -> None:
        for callback in list(self._callbacks):
            callback()
