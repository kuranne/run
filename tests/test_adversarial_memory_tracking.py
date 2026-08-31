"""
Adversarial Stress and Empirical Verification Test Suite for MonitoredPopen and BaseRunner Memory Tracking.

Focuses on:
- High-load rapid sequential execution with alternating large vs minimal allocations.
- Extreme memory boundaries (0-byte scripts, tiny allocations, massive allocations, GC peak retention).
- Process termination via signals (SIGTERM, SIGKILL, SIGINT, SIGABRT), timeouts, and crash exits.
- Multi-threaded process concurrency and parent-child memory isolation.
- Zero state bleed or cumulative memory inheritance across sequential runner invocations.
"""

import os
import sys
import time
import signal
import threading
import tempfile
import concurrent.futures
from pathlib import Path
from typing import List, Optional
import pytest

from runner.core import CompilerRunner
from runner.test_runner import TestcasesRunner
from util.process import MonitoredPopen, normalize_memory_bytes
from util.output import Printer
from util.errors import ExecutionError, CompilationError


# ---------------------------------------------------------------------------
# Section 1: High-Load Rapid Sequential Execution & Alternating Allocations
# ---------------------------------------------------------------------------

def test_adversarial_rapid_alternating_stress_sequence():
    """
    Stress-test high-load rapid sequential execution.
    Alternates: 100MB -> 500KB -> 120MB -> 500KB -> 80MB -> 500KB -> 150MB -> 500KB -> 200MB -> 500KB (repeated).
    Strictly asserts that every low-memory step reports isolated baseline memory (< 35MB)
    and every high-memory step reports at least the requested buffer size.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})
    
    # 20 alternating steps
    allocations = [100.0, 0.5, 120.0, 0.5, 80.0, 0.5, 150.0, 0.5, 200.0, 0.5] * 2

    for idx, alloc_mb in enumerate(allocations, start=1):
        cmd = [sys.executable, "-c", f"x = bytearray(int({alloc_mb} * 1024 * 1024))"]
        success = runner.run_command(cmd)
        assert success is True, f"Step {idx} ({alloc_mb}MB) execution failed"
        
        mem_bytes = runner.last_memory_bytes
        assert mem_bytes is not None, f"Step {idx} ({alloc_mb}MB) returned None for last_memory_bytes"
        mem_mb = mem_bytes / (1024 * 1024)

        if alloc_mb >= 10.0:
            assert mem_mb >= alloc_mb, (
                f"Step {idx} ({alloc_mb}MB high) underreported: got {mem_mb:.2f}MB"
            )
        else:
            assert mem_mb < 35.0, (
                f"Step {idx} ({alloc_mb}MB low) failed isolation! Cumulative leak detected: got {mem_mb:.2f}MB, expected < 35MB"
            )


def test_adversarial_long_sequence_30_cycles():
    """
    Stress-test 30 consecutive alternating cycles (60 process executions) in a single runner session.
    Verifies that no cumulative state buildup occurs over extended runtime.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})

    for cycle in range(1, 31):
        # High memory run (60MB)
        runner.run_command([sys.executable, "-c", "x = bytearray(60 * 1024 * 1024)"])
        m_high = runner.last_memory_bytes / (1024 * 1024)
        assert m_high >= 60.0, f"Cycle {cycle} high run failed: {m_high:.2f}MB"

        # Low memory run (200KB)
        runner.run_command([sys.executable, "-c", "x = bytearray(200 * 1024)"])
        m_low = runner.last_memory_bytes / (1024 * 1024)
        assert m_low < 35.0, f"Cycle {cycle} low run leaked memory: {m_low:.2f}MB"


# ---------------------------------------------------------------------------
# Section 2: Extreme Memory Boundaries & Churning
# ---------------------------------------------------------------------------

def test_adversarial_boundary_zero_byte_script():
    """
    Tests 0-byte script execution (empty string passed to python -c).
    Must report clean baseline memory under 35MB.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})
    runner.run_command([sys.executable, "-c", ""])
    mem_mb = runner.last_memory_bytes / (1024 * 1024)
    assert mem_mb < 35.0
    assert mem_mb > 0.0


def test_adversarial_boundary_massive_allocation_250mb():
    """
    Tests large memory allocation (250MB).
    Must report >= 250MB peak memory.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})
    runner.run_command([sys.executable, "-c", "x = bytearray(250 * 1024 * 1024)"])
    mem_mb = runner.last_memory_bytes / (1024 * 1024)
    assert mem_mb >= 250.0


def test_adversarial_memory_peak_retention_after_gc():
    """
    Tests that MonitoredPopen captures TRUE PEAK memory (high-water mark),
    even if the process allocates memory and then frees/garbage-collects it before exit.
    """
    code = """
import gc, time
# Step 1: Allocate 80MB
x = bytearray(80 * 1024 * 1024)
time.sleep(0.02)
# Step 2: Delete and force full GC
del x
gc.collect()
time.sleep(0.02)
# Step 3: Exit with minimal live memory
"""
    p = MonitoredPopen([sys.executable, "-c", code])
    p.wait()
    assert p.returncode == 0
    peak_mb = p.get_memory_bytes() / (1024 * 1024)
    assert peak_mb >= 80.0, (
        f"Peak memory tracking failed to capture high-water mark: reported {peak_mb:.2f}MB, expected >= 80MB"
    )


# ---------------------------------------------------------------------------
# Section 3: Process Termination via Signals, Timeouts & Crashes
# ---------------------------------------------------------------------------

def test_adversarial_termination_timeout_harvest():
    """
    Tests that a process timing out under BaseRunner triggers timeout kill,
    raises ExecutionError, and successfully harvests the memory consumed up to the timeout.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True, "timeout": 0.2})
    with pytest.raises(ExecutionError) as exc_info:
        runner.run_command([sys.executable, "-c", "x = bytearray(50 * 1024 * 1024); import time; time.sleep(5)"])
    
    assert "timed out" in str(exc_info.value)
    assert runner.last_memory_bytes is not None
    assert runner.last_memory_bytes >= 50 * 1024 * 1024


def test_adversarial_post_timeout_clean_isolation():
    """
    Verifies that after a timed-out execution, a subsequent low-memory execution
    in the same runner instance reports isolated baseline memory (< 35MB).
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True, "timeout": 0.2})
    try:
        runner.run_command([sys.executable, "-c", "x = bytearray(75 * 1024 * 1024); import time; time.sleep(5)"])
    except ExecutionError:
        pass

    # Clear timeout and run lightweight script
    runner.flags["timeout"] = None
    runner.run_command([sys.executable, "-c", "x = bytearray(500 * 1024)"])
    mem_mb = runner.last_memory_bytes / (1024 * 1024)
    assert mem_mb < 35.0, f"Post-timeout execution inherited peak memory: {mem_mb:.2f}MB"


def test_adversarial_termination_sigterm_handling():
    """
    Tests external SIGTERM signal sent to MonitoredPopen child process.
    The process should terminate with returncode -15 (or signal.SIGTERM)
    and still yield its rusage peak memory.
    """
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(40 * 1024 * 1024); import time; time.sleep(10)"])
    time.sleep(0.15)
    os.kill(p.pid, signal.SIGTERM)
    p.wait()
    
    assert p.returncode in (-signal.SIGTERM, signal.SIGTERM)
    mem_bytes = p.get_memory_bytes()
    assert mem_bytes is not None
    assert mem_bytes >= 40 * 1024 * 1024


def test_adversarial_termination_sigkill_handling():
    """
    Tests external SIGKILL signal sent to MonitoredPopen child process.
    The process should terminate with returncode -9 (or signal.SIGKILL)
    and still yield its rusage peak memory.
    """
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(45 * 1024 * 1024); import time; time.sleep(10)"])
    time.sleep(0.15)
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    
    assert p.returncode in (-signal.SIGKILL, signal.SIGKILL)
    mem_bytes = p.get_memory_bytes()
    assert mem_bytes is not None
    assert mem_bytes >= 45 * 1024 * 1024


def test_adversarial_termination_sigint_handling():
    """
    Tests SIGINT signal (simulating Ctrl+C / KeyboardInterrupt).
    Process terminates and captures peak memory.
    """
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(35 * 1024 * 1024); import time; time.sleep(10)"])
    time.sleep(0.15)
    os.kill(p.pid, signal.SIGINT)
    p.wait()
    
    assert p.returncode in (-signal.SIGINT, 1)
    mem_bytes = p.get_memory_bytes()
    assert mem_bytes is not None
    assert mem_bytes >= 35 * 1024 * 1024


@pytest.mark.skipif(sys.platform == "darwin", reason="SIGABRT triggers macOS CrashReporter GUI dialog")
def test_adversarial_termination_sigabrt_handling():
    """
    Tests child self-abort via SIGABRT.
    MonitoredPopen handles abort returncode cleanly and captures peak memory.
    """
    p = MonitoredPopen([sys.executable, "-c", "import os, signal; x = bytearray(30 * 1024 * 1024); os.kill(os.getpid(), signal.SIGABRT)"])
    p.wait()
    
    assert p.returncode in (-signal.SIGABRT, signal.SIGABRT)
    mem_bytes = p.get_memory_bytes()
    assert mem_bytes is not None
    assert mem_bytes >= 30 * 1024 * 1024


def test_adversarial_termination_raw_exit_bypass_atexit():
    """
    Tests child process exiting abruptly via os._exit(77) (bypassing normal atexit handlers).
    MonitoredPopen accurately harvests peak memory upon OS reaping.
    """
    p = MonitoredPopen([sys.executable, "-c", "import os; x = bytearray(25 * 1024 * 1024); os._exit(77)"])
    p.wait()
    
    assert p.returncode == 77
    mem_bytes = p.get_memory_bytes()
    assert mem_bytes is not None
    assert mem_bytes >= 25 * 1024 * 1024


def test_adversarial_termination_uncaught_exception():
    """
    Tests child process throwing an uncaught exception.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})
    with pytest.raises(ExecutionError):
        runner.run_command([sys.executable, "-c", "x = bytearray(20 * 1024 * 1024); raise RuntimeError('Adversarial Test')"])
    
    assert runner.last_memory_bytes is not None
    assert runner.last_memory_bytes >= 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# Section 4: Multi-Threaded Process Concurrency & Parent-Child Isolation
# ---------------------------------------------------------------------------

def test_adversarial_parent_heavy_memory_isolation():
    """
    Tests that parent process allocating heavy memory (150MB) does NOT contaminate
    or inflate the child process memory metric.
    """
    # Parent holds 150MB buffer
    parent_buffer = bytearray(150 * 1024 * 1024)
    
    # Child runs lightweight process (500KB)
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(500 * 1024)"])
    p.wait()
    
    child_mem = p.get_memory_bytes() / (1024 * 1024)
    assert child_mem < 35.0, f"Parent memory contaminated child report! Got {child_mem:.2f}MB"
    # Keep parent buffer reference alive
    assert len(parent_buffer) > 0


def test_adversarial_concurrent_monitored_popens_in_threads():
    """
    Tests multiple MonitoredPopen instances running concurrently across worker threads
    with completely distinct allocation sizes.
    Asserts no PID collision, race conditions, or memory metric cross-talk.
    """
    def run_worker(alloc_mb: int):
        cmd = [sys.executable, "-c", f"x = bytearray({alloc_mb} * 1024 * 1024); import time; time.sleep(0.08)"]
        proc = MonitoredPopen(cmd)
        proc.wait()
        return alloc_mb, proc.returncode, proc.get_memory_bytes()

    task_sizes = [5, 40, 10, 80, 2, 60, 1, 90, 3, 50]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run_worker, task_sizes))

    for req_mb, retcode, rep_bytes in results:
        assert retcode == 0
        assert rep_bytes is not None
        rep_mb = rep_bytes / (1024 * 1024)
        if req_mb >= 10:
            assert rep_mb >= req_mb, f"Thread worker ({req_mb}MB) underreported: {rep_mb:.2f}MB"
        else:
            assert rep_mb < 35.0, f"Thread worker ({req_mb}MB) contaminated: {rep_mb:.2f}MB"


# ---------------------------------------------------------------------------
# Section 5: API Invariants & Method Combinations
# ---------------------------------------------------------------------------

def test_adversarial_api_memory_query_while_running():
    """
    Tests calling get_memory_bytes() and peak_memory_bytes property
    while child process is still running. Must safely return None without throwing.
    """
    p = MonitoredPopen([sys.executable, "-c", "import time; time.sleep(0.4)"])
    assert p.get_memory_bytes() is None
    assert p.peak_memory_bytes is None
    p.wait()
    assert p.get_memory_bytes() is not None
    assert p.peak_memory_bytes == p.get_memory_bytes()


def test_adversarial_api_polling_then_wait():
    """
    Tests calling poll() multiple times until termination, followed by wait(),
    ensuring rusage is safely preserved through all transitions.
    """
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(20 * 1024 * 1024); import time; time.sleep(0.08)"])
    
    # Poll loop
    while p.poll() is None:
        time.sleep(0.01)
    
    # wait() after poll has already reaped
    ret = p.wait()
    assert ret == 0
    assert p.returncode == 0
    
    mem_bytes = p.get_memory_bytes()
    assert mem_bytes is not None
    assert mem_bytes >= 20 * 1024 * 1024


def test_adversarial_api_large_io_stream_with_memory_tracking():
    """
    Tests process generating 2MB of stdout text while consuming 15MB buffer,
    communicating via MonitoredPopen with stdout pipe.
    """
    code = """
import sys
buf = bytearray(15 * 1024 * 1024)
sys.stdout.write("M" * (2 * 1024 * 1024))
"""
    p = MonitoredPopen([sys.executable, "-c", code], stdout=sys.subprocess.PIPE if hasattr(sys, 'subprocess') else -1)
    stdout, stderr = p.communicate()
    assert p.returncode == 0
    assert len(stdout) == 2 * 1024 * 1024
    mem = p.get_memory_bytes()
    assert mem is not None
    assert mem >= 15 * 1024 * 1024
