"""
Process execution and resource monitoring utilities.

This module provides MonitoredPopen, a subprocess.Popen subclass that captures
isolated, per-process peak memory usage (RSS) upon child process termination,
preventing cumulative memory accumulation across multiple sequential executions.
"""

import os
import sys
import subprocess as spc
from typing import Optional, Tuple, Any


class MonitoredPopen(spc.Popen):
    """
    Subprocess Popen subclass that accurately tracks per-process peak memory usage.

    On POSIX systems (Linux, macOS, BSD), it leverages os.wait4 during process
    reaping to harvest the exact struct_rusage belonging to the specific child PID.
    On Windows systems, it queries PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize via
    GetProcessMemoryInfo from the child process handle before closure.
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
        super().__init__(*args, **kwargs)

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
                # Process was already waited on or reaped
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
            return res
        except Exception:
            if os.name == "nt":
                self._query_windows_memory()
            raise

    def poll(self) -> Optional[int]:
        """
        Check if child process has terminated and harvest resource metrics.

        Returns:
            Optional[int]: Process return code if terminated, else None.
        """
        res = super().poll()
        if res is not None and os.name == "nt":
            self._query_windows_memory()
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
            return res
        except Exception:
            if os.name == "nt":
                self._query_windows_memory()
            raise

    def get_memory_bytes(self) -> Optional[int]:
        """
        Get the peak memory usage of this process in normalized bytes.

        Normalizes across operating systems:
        - macOS (darwin): ru_maxrss is reported in bytes.
        - Linux & other POSIX: ru_maxrss is reported in kilobytes (converted to bytes).
        - Windows (NT): PeakWorkingSetSize is reported in bytes.

        Returns:
            Optional[int]: Peak memory consumption in bytes, or None if unavailable.
        """
        if self.rusage is not None:
            raw_rss = getattr(self.rusage, "ru_maxrss", 0)
            if sys.platform == "darwin":
                return int(raw_rss)
            else:
                return int(raw_rss * 1024)

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
