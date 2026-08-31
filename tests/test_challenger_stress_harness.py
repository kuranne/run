"""
High-Stress and Edge-Case Test Harness - Challenger 2.

Covers:
1. Batch Testcase Runner with 50 testcases, empty files, huge stdin payloads, and unconsumed stdin (SIGPIPE test).
2. Repetitive compile-and-run cycles (10 cycles) with varying compiler and runtime memory profiles.
3. Burst streaming I/O (10MB) without newlines, mixed binary streams, and huge stdin buffering.
4. Process timeout race conditions and sub-thread/process survival tests under MonitoredPopen.
"""

import os
import sys
import time
import pytest
import subprocess
from pathlib import Path
from typing import List, Optional

from runner.core import CompilerRunner
from runner.test_runner import TestcasesRunner
from util.process import MonitoredPopen, normalize_memory_bytes
from util.output import Printer
from util.errors import ExecutionError, CompilationError

import re
RE_PEAK_MEM = re.compile(r"Peak Memory:\s*([\d\.]+)\s*(MB|KB|GB|B)", re.IGNORECASE)

def parse_all_peak_memory_bytes(output: str) -> List[int]:
    results = []
    for match in RE_PEAK_MEM.finditer(output):
        val = float(match.group(1))
        unit = match.group(2).upper()
        if unit == "MB":
            bytes_val = int(val * 1024 * 1024)
        elif unit == "KB":
            bytes_val = int(val * 1024)
        elif unit == "GB":
            bytes_val = int(val * 1024 * 1024 * 1024)
        else:
            bytes_val = int(val)
        results.append(bytes_val)
    return results


# ==============================================================================
# 1. 50-Testcase Batch Runner & Stdin Edge Cases
# ==============================================================================

def test_batch_runner_50_testcases_stability(tmp_path, capfd):
    """
    Stress Test: 50 sequential testcases in batch runner.
    Ensures no memory leaking in Python runner itself, no file descriptor leaks,
    and 100% testcase passing rate.
    """
    sol = tmp_path / "calc.py"
    sol.write_text("""
import sys
for line in sys.stdin:
    line = line.strip()
    if line:
        print(f"RES:{int(line)*2}")
""")

    tdir = tmp_path / "tests_50"
    tdir.mkdir()

    for i in range(1, 51):
        (tdir / f"{i:03d}.in").write_text(f"{i}\n")
        (tdir / f"{i:03d}.out").write_text(f"RES:{i*2}\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True

    out, _ = capfd.readouterr()
    assert "Passed: 50/50 (100.0%)" in out
    readings = parse_all_peak_memory_bytes(out)
    assert len(readings) == 50
    # Every reading should be independent and reasonable (< 35MB)
    for idx, r in enumerate(readings, start=1):
        assert r < 35 * 1024 * 1024, f"Testcase #{idx} exceeded expected memory baseline: {r / (1024*1024):.2f}MB"


def test_batch_runner_empty_inputs_and_outputs(tmp_path, capfd):
    """
    Edge Case: Empty input file (.in) and empty output file (.out).
    Program reads nothing and prints nothing.
    """
    sol = tmp_path / "noop.py"
    sol.write_text("""
import sys
# Does nothing
""")

    tdir = tmp_path / "empty_tests"
    tdir.mkdir()
    (tdir / "01.in").write_text("")
    (tdir / "01.out").write_text("")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True
    out, _ = capfd.readouterr()
    assert "Passed: 1/1 (100.0%)" in out


def test_batch_runner_unconsumed_stdin_no_broken_pipe(tmp_path, capfd):
    """
    Edge Case: Testcase input file is 1MB, but the target program exits immediately
    without reading all stdin. Ensures no BrokenPipeError or uncaught exception crashes runner.
    """
    sol = tmp_path / "fast_exit.py"
    sol.write_text("""
import sys
# Exit immediately without consuming stdin
print("OK")
sys.exit(0)
""")

    tdir = tmp_path / "large_in_tests"
    tdir.mkdir()
    # 1MB input file
    (tdir / "01.in").write_text("DATA\n" * 200000)
    (tdir / "01.out").write_text("OK\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True
    out, _ = capfd.readouterr()
    assert "Passed: 1/1 (100.0%)" in out


# ==============================================================================
# 2. 10 Consecutive Compile-and-Run Cycles with Alternating Memory
# ==============================================================================

def test_10_consecutive_compile_and_run_cycles(tmp_path, capfd):
    """
    Stress Test: 10 back-to-back compilation and execution cycles in the same CompilerRunner
    instance, alternating heavy compiler workloads with lightweight execution.
    """
    from shutil import which
    compiler = which("gcc") or which("clang")
    if not compiler:
        pytest.skip("No C compiler available")

    runner = CompilerRunner({"memory": True, "no_color": True})

    for cycle in range(1, 11):
        c_src = tmp_path / f"cycle_{cycle:02d}.c"
        alloc_mb = 50 if (cycle % 2 == 1) else 1
        c_src.write_text(f"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main() {{
    int mb = {alloc_mb};
    size_t sz = (size_t)mb * 1024 * 1024;
    char *buf = (char *)malloc(sz);
    if (buf) memset(buf, 7, sz);
    printf("CYCLE_{cycle:02d}:%d\\n", mb);
    return 0;
}}
""")
        bin_path = c_src.with_name(c_src.stem + (".out" if os.name != "nt" else ".exe"))

        # Compile
        comp_ok = runner.run_command([compiler, str(c_src), "-o", str(bin_path)], compiling=True)
        assert comp_ok is True

        # Execute
        exec_ok = runner.run_command([str(bin_path)], compiling=False)
        assert exec_ok is True

        out, _ = capfd.readouterr()
        assert f"CYCLE_{cycle:02d}:{alloc_mb}" in out
        mem = parse_all_peak_memory_bytes(out)[-1]

        if alloc_mb == 50:
            assert mem >= 50 * 1024 * 1024, f"Cycle {cycle} expected >= 50MB, got {mem / (1024*1024):.2f}MB"
        else:
            assert mem < 15 * 1024 * 1024, f"Cycle {cycle} failed isolation! Inherited peak: {mem / (1024*1024):.2f}MB"


# ==============================================================================
# 3. 10MB Burst Output and Large Stdin Buffering
# ==============================================================================

def test_large_10mb_stdout_continuous_stream_with_memory_tracking(tmp_path, capfd):
    """
    Stress Test: 10MB of continuous streamed data without newlines under -M.
    Verifies that stream buffering in MonitoredPopen and BaseRunner does not freeze or overflow.
    """
    payload_size = 10 * 1024 * 1024
    script = tmp_path / "stream_10mb.py"
    script.write_text(f"""
import sys
chunk = b"Z" * 65536
total = {payload_size}
written = 0
while written < total:
    sys.stdout.buffer.write(chunk)
    written += len(chunk)
sys.stdout.buffer.flush()
""")

    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})
    t0 = time.perf_counter()
    success = runner.run_command([sys.executable, str(script)])
    elapsed = time.perf_counter() - t0

    assert success is True
    assert elapsed < 15.0
    assert runner.last_memory_bytes is not None


def test_huge_5mb_stdin_buffered_mode(tmp_path, monkeypatch, capfd):
    """
    Stress Test: Buffered stdin (-i -) with a 5MB payload passed into BaseRunner.
    Ensures tempfile buffering and redirection handle 5MB smoothly.
    """
    import io
    payload = "A" * (5 * 1024 * 1024) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))

    runner = CompilerRunner({"stdin": "-", "memory": True, "no_color": True, "quiet": True})
    assert len(runner._buffered_stdin) == len(payload)

    # Script reading all stdin and printing length
    script = tmp_path / "read_all.py"
    script.write_text("""
import sys
data = sys.stdin.read()
print(f"RECEIVED_LEN:{len(data)}")
""")

    success = runner.run_command([sys.executable, str(script)])
    assert success is True
    out, _ = capfd.readouterr()
    assert f"RECEIVED_LEN:{len(payload)}" in out


# ==============================================================================
# 4. Process Timeout Edge Cases & Boundary Conditions
# ==============================================================================

def test_ultra_tight_timeout_boundary():
    """
    Edge Case: Extremely short timeout (0.05s) on a 2-second sleep.
    Verifies immediate termination without hang.
    """
    runner = CompilerRunner({"timeout": 0.05, "memory": True, "no_color": True, "quiet": True})
    cmd = [sys.executable, "-c", "import time; time.sleep(2)"]

    t0 = time.perf_counter()
    with pytest.raises(ExecutionError, match="timed out"):
        runner.run_command(cmd)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"Ultra tight timeout took too long: {elapsed:.2f}s"


def test_timeout_with_multithreaded_target():
    """
    Stress Test: Target process running multiple background threads that do not stop.
    Ensures MonitoredPopen.kill() terminates the whole process cleanly.
    """
    runner = CompilerRunner({"timeout": 0.3, "memory": True, "no_color": True, "quiet": True})
    script_code = """
import threading
import time

def busy_worker():
    while True:
        time.sleep(0.01)

for _ in range(10):
    t = threading.Thread(target=busy_worker, daemon=False)
    t.start()

time.sleep(10)
"""
    t0 = time.perf_counter()
    with pytest.raises(ExecutionError, match="timed out"):
        runner.run_command([sys.executable, "-c", script_code])
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0
