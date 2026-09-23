"""Read Windows benchmark CPU/memory counters without a monitoring dependency.

GetSystemTimes includes idle in kernel time; subtract idle before computing
utilization. Process CPU is expressed as percent of ONE logical processor,
so a multithreaded process can exceed 100%. No other process is inspected.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import time
import threading
from collections import deque


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in (
            "total_physical", "available_physical", "total_pagefile", "available_pagefile",
            "total_virtual", "available_virtual", "available_extended_virtual")]


class _ProcessMemory(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("page_faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
            "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile", "private")]


class HostSampler:
    def __init__(self):
        self.previous = None
        self.processors = os.cpu_count()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self.kernel.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
        self.kernel.GetSystemTimes.restype = wintypes.BOOL
        self.kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
        self.kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
        self.kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetProcessHandleCount.restype = wintypes.BOOL
        self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemory), wintypes.DWORD]
        self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def sample(self):
        start = time.perf_counter()
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        memory, process_memory = _MemoryStatus(), _ProcessMemory()
        memory.length = ctypes.sizeof(memory)
        process_memory.size = ctypes.sizeof(process_memory)
        if not self.kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.psapi.GetProcessMemoryInfo(wintypes.HANDLE(-1), ctypes.byref(process_memory), process_memory.size):
            raise ctypes.WinError(ctypes.get_last_error())
        handles = wintypes.DWORD()
        available = self.kernel.GetProcessHandleCount(wintypes.HANDLE(-1), ctypes.byref(handles))
        handle_count = handles.value if available else None

        def ticks(value):
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        total, idle_time, cpu = ticks(kernel) + ticks(user), ticks(idle), time.process_time()
        row = {"time": time.time(), "logical_processors": self.processors,
               "host_cpu_percent": None, "process_cpu_percent_one_core": None,
               "interval_s": None, "working_set_bytes": process_memory.working_set,
               "private_bytes": process_memory.private,
               "handle_count": handle_count,
               "free_physical_bytes": memory.available_physical,
               "total_physical_bytes": memory.total_physical}
        if self.previous is not None:
            before, total_before, idle_before, cpu_before = self.previous
            elapsed, total_delta = start - before, total - total_before
            row["interval_s"] = elapsed
            if total_delta > 0:
                row["host_cpu_percent"] = 100 * (1 - (idle_time - idle_before) / total_delta)
            if elapsed > 0:
                row["process_cpu_percent_one_core"] = 100 * (cpu - cpu_before) / elapsed
        self.previous = start, total, idle_time, cpu
        row["sampling_ms"] = (time.perf_counter() - start) * 1000
        return row


class BackgroundHostSampler:
    """Keep OS counter calls out of the event loop being measured."""

    def __init__(self, interval=1.0, capacity=10000, sampler_factory=HostSampler):
        self.interval = interval
        self._sampler_factory = sampler_factory
        self._rows = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="ui-host-sampling", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            sampler = self._sampler_factory()
            while not self._stop.is_set():
                row = sampler.sample()
                with self._lock:
                    self._rows.append(row)
                self._stop.wait(self.interval)
        except (AttributeError, OSError) as error:
            self.error = str(error)

    def drain(self):
        with self._lock:
            rows = list(self._rows)
            self._rows.clear()
        return rows

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            self.error = "Host counter read did not stop within five seconds"
