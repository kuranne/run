"""
Process execution and resource monitoring utilities.

This module provides MonitoredPopen, a subprocess.Popen subclass that captures
isolated, per-process peak memory usage (RSS) upon child process termination,
preventing cumulative memory accumulation across multiple sequential executions.
"""

import os
import sys
import time
import threading
import subprocess as spc
from typing import Optional, Tuple, Any


class ProcfsSampler:
    """
    Lightweight daemon thread sampler monitoring /proc/[pid]/status on Linux.
    Captures the true peak resident memory (VmHWM) of the child process without
    parent fork Copy-On-Write (COW) memory contamination.
    """

    def __init__(self, pid: int, interval: float = 0.001) -> None:
        """
        Initialize ProcfsSampler for a given process ID.

        Args:
            pid (int): Process ID to monitor.
            interval (float): Polling interval in seconds (default: 1ms).
        """
        self.pid = pid
        self.interval = interval
        self.peak_bytes: Optional[int] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background sampling thread if on Linux."""
        if sys.platform.startswith("linux") and os.path.exists(f"/proc/{self.pid}"):
            self._thread = threading.Thread(target=self._sample_loop, daemon=True)
            self._thread.start()

    def _sample_loop(self) -> None:
        """Continuously read /proc/[pid]/status to capture VmHWM."""
        proc_status = f"/proc/{self.pid}/status"
        while not self._stop_event.is_set():
            try:
                with open(proc_status, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("VmHWM:"):
                            parts = line.split()
                            if len(parts) >= 2 and parts[1].isdigit():
                                bytes_val = int(parts[1]) * 1024
                                if self.peak_bytes is None or bytes_val > self.peak_bytes:
                                    self.peak_bytes = bytes_val
                            break
            except (OSError, IOError, FileNotFoundError, ProcessLookupError):
                break
            time.sleep(self.interval)

    def stop(self) -> Optional[int]:
        """
        Stop sampling and attempt a final reading before returning peak memory.

        Returns:
            Optional[int]: Peak memory in bytes, or None if unavailable.
        """
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.05)

        proc_status = f"/proc/{self.pid}/status"
        try:
            if os.path.exists(proc_status):
                with open(proc_status, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("VmHWM:"):
                            parts = line.split()
                            if len(parts) >= 2 and parts[1].isdigit():
                                bytes_val = int(parts[1]) * 1024
                                if self.peak_bytes is None or bytes_val > self.peak_bytes:
                                    self.peak_bytes = bytes_val
                            break
        except Exception:
            pass

        return self.peak_bytes


class MonitoredPopen(spc.Popen):
    """
    Subprocess Popen subclass that accurately tracks per-process peak memory usage.

    - On Linux: Leverages ProcfsSampler (/proc/[pid]/status VmHWM) with os.wait4 fallback.
    - On macOS/BSD: Leverages os.wait4 during process reaping to harvest struct_rusage.
    - On Windows: Queries PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize via GetProcessMemoryInfo.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize MonitoredPopen and prepare resource tracking structures.

        Args:
            *args: Variable length argument list passed to subprocess.Popen.
            **kwargs: Arbitrary keyword arguments passed to subprocess.Popen.
        """
        self.rusage: Optional[Any] = None
        self.peak_working_set_size: Optional[int] = None
        self.procfs_sampler: Optional[ProcfsSampler] = None

        # Isolate child process group/session if no preexec_fn is provided
        if "start_new_session" not in kwargs and os.name != "nt":
            if kwargs.get("preexec_fn") is None:
                kwargs["start_new_session"] = True

        super().__init__(*args, **kwargs)

        if sys.platform.startswith("linux"):
            self.procfs_sampler = ProcfsSampler(self.pid)
            self.procfs_sampler.start()

    def _stop_sampler(self) -> None:
        """Stop background sampler if active."""
        if self.procfs_sampler is not None:
            self.procfs_sampler.stop()

    def _try_wait(self, wait_flags: int) -> Tuple[int, int]:
        """
        Reap the child process on POSIX using os.wait4 to capture per-process rusage.

        Args:
            wait_flags (int): Wait flags passed to os.wait4 / os.waitpid.

        Returns:
            Tuple[int, int]: A tuple of (pid, status).
        """
        if os.name != "nt" and hasattr(os, "wait4"):
            try:
                (pid, sts, ru) = os.wait4(self.pid, wait_flags)
                if pid != 0:
                    self.rusage = ru
                return (pid, sts)
            except ChildProcessError:
                return (self.pid, self.returncode if self.returncode is not None else 0)
        return super()._try_wait(wait_flags)

    def _internal_poll(self, _deadstate: Optional[int] = None, _del_safe: Any = None) -> Optional[int]:
        """
        Non-blocking poll for process exit on POSIX using os.wait4 with WNOHANG.

        Args:
            _deadstate (Optional[int]): State to set if child is already dead.
            _del_safe (Any): Internal CPython del-safe reference.

        Returns:
            Optional[int]: Return code if process terminated, else None.
        """
        if os.name != "nt" and hasattr(os, "wait4"):
            if self.returncode is None:
                if hasattr(self, "_waitpid_lock") and not self._waitpid_lock.acquire(False):
                    return None
                try:
                    if self.returncode is not None:
                        return self.returncode
                    try:
                        pid, sts, ru = os.wait4(self.pid, os.WNOHANG)
                        if pid == self.pid:
                            self.rusage = ru
                            self._handle_exitstatus(sts)
                    except (ChildProcessError, OSError):
                        if _deadstate is not None:
                            self.returncode = _deadstate
                        else:
                            self.returncode = 0
                finally:
                    if hasattr(self, "_waitpid_lock"):
                        self._waitpid_lock.release()
            return self.returncode
        return super()._internal_poll(_deadstate=_deadstate)

    def _query_windows_memory(self) -> None:
        """
        Query process peak working set size on Windows before handle is closed.
        """
        if os.name != "nt":
            return
        if self.peak_working_set_size is not None:
            return
        if hasattr(self, "_handle") and self._handle is not None:
            try:
                import ctypes
                from ctypes import wintypes

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                handle = int(self._handle)
                if handle and hasattr(ctypes, "windll") and hasattr(ctypes.windll, "psapi"):
                    if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                        self.peak_working_set_size = int(counters.PeakWorkingSetSize)
            except Exception:
                pass

    def wait(self, timeout: Optional[float] = None) -> int:
        """
        Wait for child process to terminate and capture resource usage.

        Args:
            timeout (Optional[float]): Timeout in seconds.

        Returns:
            int: Process exit return code.
        """
        try:
            res = super().wait(timeout=timeout)
            if os.name == "nt":
                self._query_windows_memory()
            self._stop_sampler()
            return res
        except Exception:
            if os.name == "nt":
                self._query_windows_memory()
            self._stop_sampler()
            raise

    def poll(self) -> Optional[int]:
        """
        Check if child process has terminated and harvest resource metrics.

        Returns:
            Optional[int]: Process return code if terminated, else None.
        """
        res = super().poll()
        if res is not None:
            if os.name == "nt":
                self._query_windows_memory()
            self._stop_sampler()
        return res

    def communicate(self, input: Optional[Any] = None, timeout: Optional[float] = None) -> Tuple[Optional[Any], Optional[Any]]:
        """
        Interact with process, wait for termination, and capture memory metrics.

        Args:
            input (Optional[Any]): Standard input data.
            timeout (Optional[float]): Timeout in seconds.

        Returns:
            Tuple[Optional[Any], Optional[Any]]: (stdout_data, stderr_data) tuple.
        """
        try:
            res = super().communicate(input=input, timeout=timeout)
            if os.name == "nt":
                self._query_windows_memory()
            self._stop_sampler()
            return res
        except Exception:
            if os.name == "nt":
                self._query_windows_memory()
            self._stop_sampler()
            raise

    def get_memory_bytes(self) -> Optional[int]:
        """
        Get the peak memory usage of this process in normalized bytes.

        Normalizes across operating systems:
        - Linux: Prioritizes /proc/[pid]/status VmHWM with wait4 fallback.
        - macOS (darwin): ru_maxrss is reported in bytes.
        - Windows (NT): PeakWorkingSetSize is reported in bytes.

        Returns:
            Optional[int]: Peak memory consumption in bytes, or None if unavailable.
        """
        if self.poll() is None:
            return None

        self._stop_sampler()

        # 1. Linux Procfs Sampler (most accurate on Linux)
        if self.procfs_sampler is not None and self.procfs_sampler.peak_bytes is not None:
            return self.procfs_sampler.peak_bytes

        # 2. POSIX wait4 rusage
        if self.rusage is not None:
            raw_rss = getattr(self.rusage, "ru_maxrss", 0)
            if sys.platform == "darwin":
                return int(raw_rss)
            else:
                return int(raw_rss * 1024)

        # 3. Windows GetProcessMemoryInfo
        if os.name == "nt":
            self._query_windows_memory()
            if self.peak_working_set_size is not None:
                return int(self.peak_working_set_size)

        return None

    @property
    def peak_memory_bytes(self) -> Optional[int]:
        """
        Property alias for get_memory_bytes().

        Returns:
            Optional[int]: Peak memory consumption in bytes, or None if unavailable.
        """
        return self.get_memory_bytes()


def normalize_memory_bytes(raw_rss: int, platform: Optional[str] = None) -> int:
    """
    Normalize raw rusage ru_maxrss into bytes based on the target OS platform.

    Args:
        raw_rss (int): Raw ru_maxrss value from struct_rusage.
        platform (Optional[str]): Platform identifier (defaults to sys.platform).

    Returns:
        int: Normalized memory usage in bytes.
    """
    target_platform = platform if platform is not None else sys.platform
    if target_platform == "darwin":
        return int(raw_rss)
    return int(raw_rss * 1024)

