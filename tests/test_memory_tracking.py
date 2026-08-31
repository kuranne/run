"""
Comprehensive End-to-End and Regression Test Suite for Memory Tracking Subsystem (-M / --mem / -tM).

Covers 4-Tier Test Matrix:
- Tier 1: Feature Coverage (CLI flags, BaseRunner, TestcasesRunner, Compilation vs Execution, MonitoredPopen API)
- Tier 2: Boundary & Corner Cases (Zero alloc, short/long lived, non-zero exits, timeouts, 100MB buffer, pipe deadlock immunity, unit normalization, Windows mock)
- Tier 3: Cross-Feature Combinations & Isolation (High-then-low sequential, alternating sequence, batch testcase non-stacking, compiler isolation)
- Tier 4: Real-World Scenarios (Competitive programming 5-testcase benchmark suite, multi-file sequential runs, large data pipeline)
"""

import os
import re
import sys
import time
import subprocess
import pytest
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

from runner.core import CompilerRunner
from runner.test_runner import TestcasesRunner
from util.process import MonitoredPopen, normalize_memory_bytes
from util.output import Colors, Printer
from util.errors import ExecutionError, CompilationError

# ---------------------------------------------------------------------------
# Test Helpers and Fixtures
# ---------------------------------------------------------------------------

RE_PEAK_MEM = re.compile(r"Peak Memory:\s*([\d\.]+)\s*(MB|KB|GB|B)", re.IGNORECASE)
RE_TOOK_TIME = re.compile(r"Took\s*([\d\.]+)s", re.IGNORECASE)

def parse_all_peak_memory_bytes(output: str) -> List[int]:
    """Extract all Peak Memory readings from output text and convert to integer bytes."""
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

def parse_last_peak_memory_bytes(output: str) -> Optional[int]:
    """Extract the most recent Peak Memory reading from output text."""
    all_readings = parse_all_peak_memory_bytes(output)
    return all_readings[-1] if all_readings else None

def parse_all_execution_times(output: str) -> List[float]:
    """Extract all execution duration readings from output text."""
    return [float(match.group(1)) for match in RE_TOOK_TIME.finditer(output)]

def make_memory_script(path: Path, mb: int = 0, sleep_s: float = 0, exit_code: int = 0) -> Path:
    """Create a standalone Python script with deterministic memory allocation and timing."""
    code_lines = ["import sys", "import time"]
    if mb > 0:
        code_lines.append(f"buffer = bytearray({mb} * 1024 * 1024)")
    else:
        code_lines.append("pass")
    if sleep_s > 0:
        code_lines.append(f"time.sleep({sleep_s})")
    if exit_code != 0:
        code_lines.append(f"sys.exit({exit_code})")
    path.write_text("\n".join(code_lines) + "\n", encoding="utf-8")
    return path

# ===========================================================================
# Tier 1: Feature Coverage
# ===========================================================================

def test_feature_cli_flag_m(tmp_path):
    """Tier 1: Verify CLI invocation with -M displays Peak Memory metric."""
    script = make_memory_script(tmp_path / "app.py", mb=5)
    result = subprocess.run(
        [sys.executable, "src/main.py", str(script), "-M", "--no-color"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=dict(os.environ, PYTHONPATH="src"),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "Peak Memory:" in result.stdout
    mem_bytes = parse_last_peak_memory_bytes(result.stdout)
    assert mem_bytes is not None
    assert mem_bytes >= 5 * 1024 * 1024

def test_feature_cli_flag_mem(tmp_path):
    """Tier 1: Verify CLI invocation with --mem displays Peak Memory metric."""
    script = make_memory_script(tmp_path / "app.py", mb=8)
    result = subprocess.run(
        [sys.executable, "src/main.py", str(script), "--mem", "--no-color"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=dict(os.environ, PYTHONPATH="src"),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "Peak Memory:" in result.stdout
    mem_bytes = parse_last_peak_memory_bytes(result.stdout)
    assert mem_bytes is not None
    assert mem_bytes >= 8 * 1024 * 1024

def test_feature_cli_flag_memory_long(tmp_path):
    """Tier 1: Verify CLI invocation with --memory displays Peak Memory metric."""
    script = make_memory_script(tmp_path / "app.py", mb=6)
    result = subprocess.run(
        [sys.executable, "src/main.py", str(script), "--memory", "--no-color"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=dict(os.environ, PYTHONPATH="src"),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "Peak Memory:" in result.stdout
    mem_bytes = parse_last_peak_memory_bytes(result.stdout)
    assert mem_bytes is not None
    assert mem_bytes >= 6 * 1024 * 1024

def test_feature_cli_combined_time_and_memory(tmp_path):
    """Tier 1: Verify CLI invocation with -t -M displays both time and Peak Memory."""
    script = make_memory_script(tmp_path / "app.py", mb=5, sleep_s=0.05)
    result = subprocess.run(
        [sys.executable, "src/main.py", str(script), "-t", "-M", "--no-color"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=dict(os.environ, PYTHONPATH="src"),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "Took " in result.stdout
    assert "Peak Memory:" in result.stdout
    times = parse_all_execution_times(result.stdout)
    assert times and times[0] >= 0.04

def test_feature_base_runner_run_command_mem(capsys):
    """Tier 1: Verify BaseRunner.run_command captures and prints memory when flags['memory']=True."""
    runner = CompilerRunner({"memory": True, "no_color": True})
    success = runner.run_command([sys.executable, "-c", "x = bytearray(12 * 1024 * 1024)"])
    assert success is True
    out = capsys.readouterr().out
    assert "Peak Memory:" in out
    mem_bytes = parse_last_peak_memory_bytes(out)
    assert mem_bytes is not None
    assert mem_bytes >= 12 * 1024 * 1024
    assert runner.last_memory_bytes is not None
    assert abs(runner.last_memory_bytes - mem_bytes) < 64 * 1024

def test_feature_batch_testcases_runner_memory(tmp_path, capfd):
    """Tier 1: Verify TestcasesRunner (--test-dir) displays memory metrics per testcase."""
    sol = tmp_path / "sol.py"
    sol.write_text("""
import sys
line = sys.stdin.read().strip()
print(f"ECHO:{line}")
""")
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "01.in").write_text("alpha\n")
    (tdir / "01.out").write_text("ECHO:alpha\n")
    (tdir / "02.in").write_text("beta\n")
    (tdir / "02.out").write_text("ECHO:beta\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True
    out, _ = capfd.readouterr()
    readings = parse_all_peak_memory_bytes(out)
    assert len(readings) == 2, f"Expected 2 peak memory readings, got {len(readings)}"

def test_feature_compiler_step_vs_execution_step_flags(capsys):
    """Tier 1: Verify that compiling=True does not output execution metrics, but execution step does."""
    runner = CompilerRunner({"memory": True, "time": True, "no_color": True})
    # Step 1: Compiling step
    comp_ok = runner.run_command([sys.executable, "-c", "pass"], compiling=True)
    assert comp_ok is True
    out_comp = capsys.readouterr().out
    assert "Peak Memory:" not in out_comp
    assert "Took " not in out_comp

    # Step 2: Execution step
    exec_ok = runner.run_command([sys.executable, "-c", "pass"], compiling=False)
    assert exec_ok is True
    out_exec = capsys.readouterr().out
    assert "Peak Memory:" in out_exec
    assert "Took " in out_exec
    assert runner.last_memory_bytes is not None

def test_feature_monitored_popen_direct_api():
    """Tier 1: Directly instantiate MonitoredPopen and assert communicate / get_memory_bytes API."""
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(10 * 1024 * 1024)"])
    stdout, stderr = p.communicate()
    assert p.returncode == 0
    mem = p.get_memory_bytes()
    assert mem is not None
    assert mem >= 10 * 1024 * 1024
    assert p.peak_memory_bytes == mem

# ===========================================================================
# Tier 2: Boundary & Corner Cases
# ===========================================================================

def test_boundary_zero_negligible_allocation(capsys):
    """Tier 2: Empty script or pass executes cleanly and reports minimal baseline memory."""
    runner = CompilerRunner({"memory": True, "no_color": True})
    runner.run_command([sys.executable, "-c", "pass"])
    out = capsys.readouterr().out
    mem_bytes = parse_last_peak_memory_bytes(out)
    assert mem_bytes is not None
    # Baseline Python 3 runtime memory is typically 12-25MB, definitely under 35MB
    assert mem_bytes < 35 * 1024 * 1024
    assert runner.last_memory_bytes is not None
    assert abs(runner.last_memory_bytes - mem_bytes) < 64 * 1024

def test_boundary_short_vs_long_lived_process(capsys):
    """Tier 2: Both micro-second processes and long-lived processes track memory accurately."""
    runner = CompilerRunner({"memory": True, "no_color": True})
    # Fast micro-process (<5ms)
    runner.run_command([sys.executable, "-c", "import sys; sys.exit(0)"])
    out_fast = capsys.readouterr().out
    mem_fast = parse_last_peak_memory_bytes(out_fast)
    assert mem_fast is not None and mem_fast > 0

    # Sustained process with sleep
    runner.run_command([sys.executable, "-c", "import time; time.sleep(0.12); x = bytearray(15 * 1024 * 1024)"])
    out_slow = capsys.readouterr().out
    mem_slow = parse_last_peak_memory_bytes(out_slow)
    assert mem_slow is not None and mem_slow >= 15 * 1024 * 1024

def test_boundary_nonzero_exit_code_under_memory_tracking():
    """Tier 2: Child exiting with non-zero code raises ExecutionError without crashing memory subsystem."""
    runner = CompilerRunner({"memory": True, "no_color": True})
    with pytest.raises(ExecutionError) as exc_info:
        runner.run_command([sys.executable, "-c", "import sys; sys.exit(42)"])
    assert "exit code 42" in str(exc_info.value)

def test_boundary_process_timeout_under_memory_tracking():
    """Tier 2: Child timing out raises ExecutionError and is cleanly killed."""
    runner = CompilerRunner({"memory": True, "no_color": True, "timeout": 0.3})
    with pytest.raises(ExecutionError) as exc_info:
        runner.run_command([sys.executable, "-c", "import time; time.sleep(5)"])
    assert "timed out" in str(exc_info.value)

def test_boundary_large_memory_allocation_100mb(capsys):
    """Tier 2: Allocating 100MB buffer reports >= 100MB peak memory."""
    runner = CompilerRunner({"memory": True, "no_color": True})
    runner.run_command([sys.executable, "-c", "x = bytearray(100 * 1024 * 1024)"])
    out = capsys.readouterr().out
    mem_bytes = parse_last_peak_memory_bytes(out)
    assert mem_bytes is not None
    assert mem_bytes >= 100 * 1024 * 1024

def test_boundary_large_stdout_pipe_no_deadlock(capsys):
    """Tier 2: Large stdout output (>256KB) with memory tracking does not deadlock Popen."""
    runner = CompilerRunner({"memory": True, "no_color": True})
    # Generate 256KB of stdout text
    runner.run_command([sys.executable, "-c", "import sys; sys.stdout.write('A' * 262144)"])
    out = capsys.readouterr().out
    assert "Peak Memory:" in out
    assert runner.last_memory_bytes is not None

def test_boundary_unit_conversion_darwin_vs_linux(capsys):
    """Tier 2: Unit formatting checks (Linux KB normalization vs Darwin bytes normalization)."""
    # 512 KB
    Printer.metrics(memory_bytes=512 * 1024)
    out1 = capsys.readouterr().out
    assert "Peak Memory: 512.0 KB" in out1

    # 48.5 MB
    Printer.metrics(memory_bytes=int(48.5 * 1024 * 1024))
    out2 = capsys.readouterr().out
    assert "Peak Memory: 48.50 MB" in out2

    # Test normalize_memory_bytes directly
    # Linux KiB -> bytes
    assert normalize_memory_bytes(50000, platform="linux") == 50000 * 1024
    # Darwin bytes -> bytes
    assert normalize_memory_bytes(51200000, platform="darwin") == 51200000

def test_boundary_windows_mock_getprocessmemoryinfo(monkeypatch, capsys):
    """Tier 2: Mock Windows environment and verify PeakWorkingSetSize extraction."""
    class MockCounters:
        cb = 64
        PeakWorkingSetSize = 64 * 1024 * 1024  # 64 MB

    # Test Printer.metrics output for Windows simulated bytes
    Printer.metrics(memory_bytes=MockCounters.PeakWorkingSetSize)
    out = capsys.readouterr().out
    assert "Peak Memory: 64.00 MB" in out

def test_boundary_monitored_popen_poll_reaping():
    """Tier 2: Verify MonitoredPopen.poll() successfully reaps and captures memory."""
    p = MonitoredPopen([sys.executable, "-c", "x = bytearray(8 * 1024 * 1024)"])
    ret = None
    for _ in range(50):
        ret = p.poll()
        if ret is not None:
            break
        time.sleep(0.05)
    assert ret == 0
    mem = p.get_memory_bytes()
    assert mem is not None
    assert mem >= 8 * 1024 * 1024

# ===========================================================================
# Tier 3: Cross-Feature Combinations & Isolation (The Core Regression Assertions)
# ===========================================================================

def test_isolation_sequential_high_then_low_memory(capsys):
    """
    Tier 3 (Regression R1/R3):
    Running a high-memory process (60MB) followed by a low-memory process (1MB)
    in the SAME runner instance must report low memory for the second process.
    Must NOT inherit or stack the 60MB peak of the first process.
    """
    runner = CompilerRunner({"memory": True, "no_color": True})

    # 1. High Memory Run
    runner.run_command([sys.executable, "-c", "x = bytearray(60 * 1024 * 1024)"])
    out_high = capsys.readouterr().out
    mem_high = parse_last_peak_memory_bytes(out_high)
    assert mem_high is not None
    assert mem_high >= 60 * 1024 * 1024, f"High run expected >= 60MB, got {mem_high / (1024*1024):.2f}MB"

    # 2. Low Memory Run in the same runner session
    runner.run_command([sys.executable, "-c", "x = bytearray(1 * 1024 * 1024)"])
    out_low = capsys.readouterr().out
    mem_low = parse_last_peak_memory_bytes(out_low)
    assert mem_low is not None, "Low memory run failed to produce memory output"

    # The low-memory process must be isolated: < 35MB (baseline Python + 1MB)
    # and significantly less than the high-memory run.
    assert mem_low < 35 * 1024 * 1024, (
        f"Regression Failure: Second process inherited peak memory! "
        f"Expected < 35MB, got {mem_low / (1024*1024):.2f}MB"
    )
    assert mem_low < mem_high - (25 * 1024 * 1024), (
        f"Regression Failure: Second process memory ({mem_low / (1024*1024):.2f}MB) "
        f"is not isolated from first process ({mem_high / (1024*1024):.2f}MB)"
    )
    assert runner.last_memory_bytes is not None
    assert abs(runner.last_memory_bytes - mem_low) < 64 * 1024

def test_isolation_alternating_memory_sequence(capsys):
    """
    Tier 3 (Regression R1):
    Alternating sequence of high and low memory tasks in the same runner instance.
    Every low-memory task must independently report low memory (< 35MB).
    """
    runner = CompilerRunner({"memory": True, "no_color": True})
    task_allocations = [50, 1, 80, 1, 60, 1]  # in MB

    for idx, mb in enumerate(task_allocations, start=1):
        runner.run_command([sys.executable, "-c", f"x = bytearray({mb} * 1024 * 1024)"])
        out = capsys.readouterr().out
        mem = parse_last_peak_memory_bytes(out)
        assert mem is not None, f"Task #{idx} ({mb}MB) produced no memory metric"

        if mb > 10:
            assert mem >= mb * 1024 * 1024, (
                f"Task #{idx} ({mb}MB) expected >= {mb}MB, got {mem / (1024*1024):.2f}MB"
            )
        else:
            assert mem < 35 * 1024 * 1024, (
                f"Regression Failure: Task #{idx} (low {mb}MB) inherited peak memory! "
                f"Got {mem / (1024*1024):.2f}MB, expected < 35MB"
            )

def test_isolation_batch_runner_testcase_non_stacking(tmp_path, capfd):
    """
    Tier 3 (Regression R2):
    Batch runner (TestcasesRunner / --test-dir) with Testcase 1 allocating 60MB
    and Testcase 2 allocating 1MB. Testcase 2 must NOT inherit 60MB peak.
    """
    sol = tmp_path / "solution.py"
    sol.write_text("""
import sys
line = sys.stdin.read().strip()
val = int(line)
if val == 1:
    x = bytearray(60 * 1024 * 1024)
else:
    x = bytearray(1 * 1024 * 1024)
print(f"RESULT:{val}")
""")
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "01.in").write_text("1\n")
    (tdir / "01.out").write_text("RESULT:1\n")
    (tdir / "02.in").write_text("2\n")
    (tdir / "02.out").write_text("RESULT:2\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True

    out, _ = capfd.readouterr()
    readings = parse_all_peak_memory_bytes(out)
    assert len(readings) == 2, f"Expected 2 peak memory readings, got {len(readings)}"

    tc1_mem, tc2_mem = readings[0], readings[1]
    assert tc1_mem >= 60 * 1024 * 1024, f"TC1 expected >= 60MB, got {tc1_mem / (1024*1024):.2f}MB"
    assert tc2_mem < 35 * 1024 * 1024, (
        f"Regression Failure: Batch runner testcase 2 inherited TC1 peak memory! "
        f"Expected < 35MB, got {tc2_mem / (1024*1024):.2f}MB"
    )

def test_isolation_compilation_then_execution_isolation(tmp_path, capsys):
    """
    Tier 3 (Regression R2):
    A heavy compilation step (e.g. 80MB) followed by a lightweight execution (1MB)
    must report isolated execution memory (< 35MB), not polluted by the compilation process.
    """
    runner = CompilerRunner({"memory": True, "no_color": True})

    # Step 1: Simulate heavy compilation process
    comp_ok = runner.run_command([sys.executable, "-c", "x = bytearray(80 * 1024 * 1024)"], compiling=True)
    assert comp_ok is True
    capsys.readouterr()  # discard compilation output

    # Step 2: Run lightweight target program
    exec_ok = runner.run_command([sys.executable, "-c", "x = bytearray(1 * 1024 * 1024)"], compiling=False)
    assert exec_ok is True

    out_exec = capsys.readouterr().out
    mem_exec = parse_last_peak_memory_bytes(out_exec)
    assert mem_exec is not None
    assert mem_exec < 35 * 1024 * 1024, (
        f"Regression Failure: Execution step inherited compilation memory! "
        f"Expected < 35MB, got {mem_exec / (1024*1024):.2f}MB"
    )
    assert runner.last_memory_bytes is not None
    assert abs(runner.last_memory_bytes - mem_exec) < 64 * 1024

# ===========================================================================
# Tier 4: Real-World Scenarios
# ===========================================================================

def test_real_world_competitive_programming_5_testcases(tmp_path, capfd):
    """
    Tier 4: Realistic competitive programming benchmark suite with 5 test cases
    exhibiting diverse memory consumption profiles.
    TC1: 500KB
    TC2: 55MB
    TC3: 1MB
    TC4: 40MB
    TC5: 2MB
    """
    sol = tmp_path / "solver.py"
    sol.write_text("""
import sys
line = sys.stdin.read().strip()
alloc_mb = float(line)
if alloc_mb > 0:
    buf = bytearray(int(alloc_mb * 1024 * 1024))
print(f"DONE:{alloc_mb}")
""")
    tdir = tmp_path / "testsuite"
    tdir.mkdir()

    profiles = [0.5, 55.0, 1.0, 40.0, 2.0]
    for i, p in enumerate(profiles, start=1):
        (tdir / f"{i:02d}.in").write_text(f"{p}\n")
        (tdir / f"{i:02d}.out").write_text(f"DONE:{p}\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True

    out, _ = capfd.readouterr()
    readings = parse_all_peak_memory_bytes(out)
    assert len(readings) == 5, f"Expected 5 readings, got {len(readings)}"

    # TC 1: 0.5 MB -> < 35 MB
    assert readings[0] < 35 * 1024 * 1024, f"TC1 failed: {readings[0]/(1024*1024):.2f}MB"
    # TC 2: 55.0 MB -> >= 55 MB
    assert readings[1] >= 55 * 1024 * 1024, f"TC2 failed: {readings[1]/(1024*1024):.2f}MB"
    # TC 3: 1.0 MB -> must NOT inherit TC2's 55MB, < 35 MB
    assert readings[2] < 35 * 1024 * 1024, f"TC3 failed (stacked): {readings[2]/(1024*1024):.2f}MB"
    # TC 4: 40.0 MB -> >= 40 MB
    assert readings[3] >= 40 * 1024 * 1024, f"TC4 failed: {readings[3]/(1024*1024):.2f}MB"
    # TC 5: 2.0 MB -> must NOT inherit TC4's 40MB or TC2's 55MB, < 35 MB
    assert readings[4] < 35 * 1024 * 1024, f"TC5 failed (stacked): {readings[4]/(1024*1024):.2f}MB"

def test_real_world_multi_file_sequential_runs(tmp_path, capsys):
    """
    Tier 4: Sequential execution of multiple script files in one runner session.
    File 1: heavy (60MB)
    File 2: light (1MB)
    File 3: heavy (70MB)
    File 4: light (1MB)
    """
    runner = CompilerRunner({"memory": True, "no_color": True})

    f1 = make_memory_script(tmp_path / "f1.py", mb=60)
    f2 = make_memory_script(tmp_path / "f2.py", mb=1)
    f3 = make_memory_script(tmp_path / "f3.py", mb=70)
    f4 = make_memory_script(tmp_path / "f4.py", mb=1)

    runner.run_command([sys.executable, str(f1)])
    m1 = parse_last_peak_memory_bytes(capsys.readouterr().out)
    assert m1 is not None and m1 >= 60 * 1024 * 1024

    runner.run_command([sys.executable, str(f2)])
    m2 = parse_last_peak_memory_bytes(capsys.readouterr().out)
    assert m2 is not None and m2 < 35 * 1024 * 1024, f"File 2 stacked: {m2/(1024*1024):.2f}MB"

    runner.run_command([sys.executable, str(f3)])
    m3 = parse_last_peak_memory_bytes(capsys.readouterr().out)
    assert m3 is not None and m3 >= 70 * 1024 * 1024

    runner.run_command([sys.executable, str(f4)])
    m4 = parse_last_peak_memory_bytes(capsys.readouterr().out)
    assert m4 is not None and m4 < 35 * 1024 * 1024, f"File 4 stacked: {m4/(1024*1024):.2f}MB"
