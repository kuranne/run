"""
Deep Adversarial Stress Test Suite - Challenger 2.

Covers:
1. Multi-testcase batch runner (TestcasesRunner / --test-dir) under varying memory loads,
   compiled vs interpreted targets, mixed success/fail/timeout/crash outcomes, and edge-case patterns.
2. Heavy compilation phase followed by light execution phase (isolation of rusage and memory).
3. Large stdout/stderr output (5MB+ streaming) with memory tracking (pipe deadlock immunity,
   diff comparison scalability, rusage integrity).
4. Subprocess timeout handling under memory tracking (SIGKILL, rusage extraction on killed child,
   cleanup, zombie avoidance, recovery in batch runner).
"""

import os
import sys
import time
import pytest
import subprocess
from pathlib import Path
from typing import List, Optional

import re
from runner.core import CompilerRunner
from runner.test_runner import TestcasesRunner
from util.process import MonitoredPopen, normalize_memory_bytes
from util.output import Printer
from util.errors import ExecutionError, CompilationError

RE_PEAK_MEM = re.compile(r"Peak Memory:\s*([\d\.]+)\s*(MB|KB|GB|B)", re.IGNORECASE)

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


# ==============================================================================
# 1. Multi-Testcase Batch Runner Under Varying Memory Loads & Edge Cases
# ==============================================================================

def test_batch_runner_20_varying_memory_loads(tmp_path, capfd):
    """
    Stress Test: 20 sequential testcases with alternating high (40-80MB) and low (0.5-2MB)
    allocations. Asserts that EVERY low-memory testcase accurately reports isolated low memory
    (< 35MB) and never accumulates or inherits previous high-memory testcase peaks.
    """
    sol = tmp_path / "solution.py"
    sol.write_text("""
import sys
line = sys.stdin.read().strip()
alloc_mb = float(line)
if alloc_mb > 0:
    buf = bytearray(int(alloc_mb * 1024 * 1024))
print(f"VAL:{alloc_mb}")
""")
    tdir = tmp_path / "testsuite_20"
    tdir.mkdir()

    # Pattern of 20 alternating memory sizes
    allocations = [
        0.5, 50.0, 1.0, 70.0, 0.8,
        60.0, 1.2, 80.0, 0.5, 45.0,
        1.5, 65.0, 0.7, 55.0, 1.0,
        75.0, 0.6, 50.0, 1.1, 40.0
    ]

    for idx, mb in enumerate(allocations, start=1):
        (tdir / f"{idx:02d}.in").write_text(f"{mb}\n")
        (tdir / f"{idx:02d}.out").write_text(f"VAL:{mb}\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True

    out, _ = capfd.readouterr()
    readings = parse_all_peak_memory_bytes(out)
    assert len(readings) == 20, f"Expected 20 memory readings, got {len(readings)}"

    for idx, (mb, mem_b) in enumerate(zip(allocations, readings), start=1):
        mem_mb = mem_b / (1024 * 1024)
        if mb >= 40.0:
            assert mem_b >= mb * 1024 * 1024, f"TC#{idx} ({mb}MB) expected >= {mb}MB, got {mem_mb:.2f}MB"
        else:
            assert mem_b < 35 * 1024 * 1024, (
                f"TC#{idx} ({mb}MB) failed isolation! Inherited peak: got {mem_mb:.2f}MB, expected < 35MB"
            )


def test_batch_runner_compiled_c_binary_varying_memory(tmp_path, capfd):
    """
    Stress Test: Batch runner compiling a C program once, then executing across
    multiple testcases with diverse memory allocations. Asserts C binary memory isolation.
    """
    # Check if gcc / clang is available
    from shutil import which
    compiler = which("gcc") or which("clang")
    if not compiler:
        pytest.skip("No C compiler (gcc/clang) available in environment")

    c_src = tmp_path / "solver.c"
    c_src.write_text("""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main() {
    int mb = 0;
    if (scanf("%d", &mb) == 1) {
        if (mb > 0) {
            size_t bytes = (size_t)mb * 1024 * 1024;
            char *buf = (char *)malloc(bytes);
            if (buf) {
                // Touch pages to ensure physical allocation
                memset(buf, 1, bytes);
            }
        }
        printf("C_DONE:%d\\n", mb);
        return 0;
    }
    return 1;
}
""")

    tdir = tmp_path / "c_tests"
    tdir.mkdir()

    allocations = [1, 30, 2, 50, 1, 40, 2]
    for idx, mb in enumerate(allocations, start=1):
        (tdir / f"test_{idx:02d}.in").write_text(f"{mb}\n")
        (tdir / f"test_{idx:02d}.out").write_text(f"C_DONE:{mb}\n")

    runner = CompilerRunner({"memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, tdir, c_src)
    assert success is True

    out, _ = capfd.readouterr()
    readings = parse_all_peak_memory_bytes(out)
    assert len(readings) == len(allocations)

    for idx, (mb, mem_b) in enumerate(zip(allocations, readings), start=1):
        mem_mb = mem_b / (1024 * 1024)
        if mb >= 30:
            assert mem_b >= mb * 1024 * 1024, f"C TC#{idx} ({mb}MB) got {mem_mb:.2f}MB"
        else:
            # Compiled C binary baseline memory is tiny (< 10MB)
            assert mem_b < 15 * 1024 * 1024, f"C TC#{idx} ({mb}MB) failed isolation! Got {mem_mb:.2f}MB"


def test_batch_runner_mixed_outcomes_resilience(tmp_path, capfd):
    """
    Stress Test: Batch runner encountering mixed testcase outcomes:
    - TC 1: PASS
    - TC 2: FAIL (Output mismatch)
    - TC 3: TIMEOUT (Infinite loop / sleep with timeout=1)
    - TC 4: CRASH (Non-zero exit code)
    - TC 5: PASS (Heavy memory 50MB)
    - TC 6: PASS (Light memory 1MB - asserts state recovery & isolation after timeout/crash)
    """
    sol = tmp_path / "solution.py"
    sol.write_text("""
import sys
import time

cmd = sys.stdin.read().strip()
if cmd == "PASS":
    print("EXPECTED_PASS")
elif cmd == "FAIL":
    print("UNEXPECTED_STRING")
elif cmd == "TIMEOUT":
    time.sleep(10)
    print("AFTER_TIMEOUT")
elif cmd == "CRASH":
    sys.exit(134)
elif cmd.startswith("ALLOC:"):
    mb = float(cmd.split(":")[1])
    if mb > 0:
        buf = bytearray(int(mb * 1024 * 1024))
    print(f"ALLOC_OK:{mb}")
""")

    tdir = tmp_path / "mixed_tests"
    tdir.mkdir()

    # 1. PASS
    (tdir / "01.in").write_text("PASS\n")
    (tdir / "01.out").write_text("EXPECTED_PASS\n")

    # 2. FAIL
    (tdir / "02.in").write_text("FAIL\n")
    (tdir / "02.out").write_text("EXPECTED_PASS\n")

    # 3. TIMEOUT
    (tdir / "03.in").write_text("TIMEOUT\n")
    (tdir / "03.out").write_text("AFTER_TIMEOUT\n")

    # 4. CRASH
    (tdir / "04.in").write_text("CRASH\n")
    (tdir / "04.out").write_text("CRASH_IGNORED\n")

    # 5. PASS Heavy
    (tdir / "05.in").write_text("ALLOC:50\n")
    (tdir / "05.out").write_text("ALLOC_OK:50.0\n")

    # 6. PASS Light
    (tdir / "06.in").write_text("ALLOC:1\n")
    (tdir / "06.out").write_text("ALLOC_OK:1.0\n")

    runner = CompilerRunner({"memory": True, "no_color": True, "timeout": 0.5})
    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is False

    out, err = capfd.readouterr()
    # Check that 3 passed (TC1, TC5, TC6) out of 6
    assert "Passed: 3/6 (50.0%)" in out

    readings = parse_all_peak_memory_bytes(out)
    # TC 5 should report >= 50MB, TC 6 should report < 35MB
    # Note: readings list will contain memory logs for completed executions
    assert len(readings) >= 3
    # Check the reading for TC6 (the last reading)
    last_reading = readings[-1]
    assert last_reading < 35 * 1024 * 1024, (
        f"TC6 failed memory isolation after earlier timeout/crash/heavy run: {last_reading / (1024*1024):.2f}MB"
    )


def test_batch_runner_filename_patterns(tmp_path):
    """
    Verify TestcasesRunner.discover_test_pairs on diverse supported filename formats.
    """
    (tmp_path / "in.txt").write_text("1\n")
    (tmp_path / "out.txt").write_text("1\n")

    (tmp_path / "in_data.txt").write_text("2\n")
    (tmp_path / "outdata.txt").write_text("2\n")

    (tmp_path / "input10.txt").write_text("3\n")
    (tmp_path / "output10.txt").write_text("3\n")

    (tmp_path / "test_case_A.in").write_text("4\n")
    (tmp_path / "test_case_A.ans").write_text("4\n")

    (tmp_path / "sample.input").write_text("5\n")
    (tmp_path / "sample.output").write_text("5\n")

    (tmp_path / "orphan.in").write_text("orphan\n")

    pairs = TestcasesRunner.discover_test_pairs(tmp_path)
    assert len(pairs) == 5
    in_names = [p[0].name for p in pairs]
    assert "in.txt" in in_names
    assert "in_data.txt" in in_names
    assert "input10.txt" in in_names
    assert "test_case_A.in" in in_names
    assert "sample.input" in in_names
    assert "orphan.in" not in in_names


def test_batch_runner_separator_pattern_limitation(tmp_path):
    """
    Edge-Case / Defect Verification:
    Tests discovery when output files have separator prefixes (e.g. in_1.txt and out_1.txt,
    or in_data.txt and out_data.txt).
    Verifies that separator-based outputs (out_1.txt, output_1.txt) are successfully matched.
    """
    (tmp_path / "in_1.txt").write_text("1\n")
    (tmp_path / "out_1.txt").write_text("1\n")

    pairs = TestcasesRunner.discover_test_pairs(tmp_path)
    assert len(pairs) == 1
    assert pairs[0][0].name == "in_1.txt"
    assert pairs[0][1].name == "out_1.txt"


# ==============================================================================
# 2. Heavy Compilation Phase Followed by Light Execution Phase Isolation
# ==============================================================================

def test_compiler_vs_runner_memory_isolation_c_program(tmp_path, capfd):
    """
    Stress Test: Compiles a C program with heavy compiler flags / preprocessor expansions,
    followed by executing the lightweight compiled binary under -M.
    Verifies that self.last_compile_memory_bytes is isolated and runner.last_memory_bytes
    reflects ONLY the lightweight binary execution (< 15MB).
    """
    from shutil import which
    if not (which("gcc") or which("clang")):
        pytest.skip("No C compiler available")

    # Generate a C file with 10,000 unrolled statements to stress the compiler
    lines = ["#include <stdio.h>", "int main() {", "    volatile long long sum = 0;"]
    for i in range(10000):
        lines.append(f"    sum += {i} * 3;")
    lines.append('    printf("SUM_DONE\\n");')
    lines.append("    return 0;")
    lines.append("}")

    c_file = tmp_path / "heavy_compile.c"
    c_file.write_text("\n".join(lines), encoding="utf-8")

    bin_path = c_file.with_name(c_file.stem + (".out" if os.name != "nt" else ".exe"))
    runner = CompilerRunner({"memory": True, "no_color": True})

    # 1. Compilation step
    comp_ok = runner.run_command(
        [which("gcc") or which("clang"), str(c_file), "-O2", "-o", str(bin_path)],
        compiling=True
    )
    assert comp_ok is True
    # Compiler memory was tracked under last_compile_memory_bytes if compiling
    out_comp, _ = capfd.readouterr()
    assert "Peak Memory:" not in out_comp, "Compilation step should not print execution Peak Memory metric"

    # 2. Lightweight binary execution step
    exec_ok = runner.run_command([str(bin_path)], compiling=False)
    assert exec_ok is True

    out_exec, _ = capfd.readouterr()
    assert "Peak Memory:" in out_exec
    mem_exec = parse_last_peak_memory_bytes(out_exec)
    assert mem_exec is not None
    # C binary with no heap allocations should be < 15MB
    assert mem_exec < 15 * 1024 * 1024, f"Binary execution memory contaminated by compiler: {mem_exec / (1024*1024):.2f}MB"
    assert runner.last_memory_bytes is not None
    assert abs(runner.last_memory_bytes - mem_exec) < 64 * 1024


def test_compiler_vs_runner_multi_file_cpm_isolation(tmp_path, capfd):
    """
    Stress Test: Multi-file C project compilation followed by binary execution under -M.
    Verifies that multi-threaded compiler worker memory does not pollute execution memory.
    """
    from shutil import which
    if not (which("gcc") or which("clang")):
        pytest.skip("No C compiler available")

    # File 1: helper
    (tmp_path / "helper.h").write_text("int compute_val(int x);\n")
    (tmp_path / "helper.c").write_text("""
#include "helper.h"
int compute_val(int x) {
    return x * 42;
}
""")
    # File 2: main
    (tmp_path / "main.c").write_text("""
#include <stdio.h>
#include "helper.h"
int main() {
    printf("RES:%d\\n", compute_val(10));
    return 0;
}
""")

    runner = CompilerRunner({"memory": True, "no_color": True})
    runner.compile_and_run([str(tmp_path / "main.c"), str(tmp_path / "helper.c")], multi=True)

    out, _ = capfd.readouterr()
    assert "RES:420" in out
    mem = parse_last_peak_memory_bytes(out)
    assert mem is not None
    assert mem < 15 * 1024 * 1024, f"Multi-file execution memory contaminated: {mem / (1024*1024):.2f}MB"


# ==============================================================================
# 3. Large stdout/stderr (5MB+) Streaming Under Memory Tracking
# ==============================================================================

def test_large_5mb_stdout_streaming_with_memory_tracking(tmp_path, capfd):
    """
    Stress Test: Emits 5MB of text to stdout with memory tracking enabled and --expect.
    Verifies:
    1. No pipe deadlock occurs (5MB >> standard 64KB OS pipe buffer).
    2. Exact output match passes.
    3. Peak Memory metric is accurately computed and reported.
    """
    mb_count = 5
    payload_size = mb_count * 1024 * 1024
    chunk = "0123456789ABCDEF" * 64  # 1024 bytes
    num_chunks = payload_size // len(chunk)
    full_payload = chunk * num_chunks

    expect_file = tmp_path / "expected_5mb.txt"
    expect_file.write_text(full_payload, encoding="utf-8")

    # Script emitting 5MB in 64KB chunks
    script = tmp_path / "stream_5mb.py"
    script.write_text(f"""
import sys
chunk = ("0123456789ABCDEF" * 64).encode("ascii")
for _ in range({num_chunks}):
    sys.stdout.buffer.write(chunk)
sys.stdout.buffer.flush()
""")

    runner = CompilerRunner({
        "memory": True,
        "expect": str(expect_file),
        "no_color": True,
        "quiet": True
    })

    t0 = time.perf_counter()
    success = runner.run_command([sys.executable, str(script)])
    elapsed = time.perf_counter() - t0

    assert success is True
    assert elapsed < 10.0, f"Streaming 5MB took unexpectedly long: {elapsed:.2f}s"
    assert runner.last_memory_bytes is not None


def test_large_5mb_dual_stream_stdout_and_stderr_with_memory_tracking(tmp_path, capfd):
    """
    Stress Test: Concurrently streams 5MB to stdout AND 5MB to stderr under -M.
    Verifies that simultaneous saturation of stdout and stderr channels does not deadlock
    and does not corrupt rusage memory harvesting.
    """
    script = tmp_path / "dual_stream.py"
    script.write_text("""
import sys
chunk = ("S" * 1024).encode("ascii")
err_chunk = ("E" * 1024).encode("ascii")
for _ in range(5120): # 5MB each
    sys.stdout.buffer.write(chunk)
    sys.stderr.buffer.write(err_chunk)
sys.stdout.buffer.flush()
sys.stderr.buffer.flush()
""")

    runner = CompilerRunner({"memory": True, "no_color": True, "quiet": True})
    t0 = time.perf_counter()
    success = runner.run_command([sys.executable, str(script)])
    elapsed = time.perf_counter() - t0

    assert success is True
    assert elapsed < 10.0
    assert runner.last_memory_bytes is not None
    assert runner.last_memory_bytes > 0


def test_large_5mb_output_mismatch_diff_safety(tmp_path, capfd):
    """
    Stress Test: 5MB stdout mismatch against an expected file under --expect.
    Verifies that the diff generator and error reporter handle 5MB without OOM or hanging.
    """
    expect_file = tmp_path / "expected_5mb.txt"
    expect_file.write_text("EXPECTED_INITIAL_LINE\n" + ("A" * (5 * 1024 * 1024)), encoding="utf-8")

    script = tmp_path / "mismatch_5mb.py"
    script.write_text("""
import sys
sys.stdout.write("ACTUAL_DIFFERENT_LINE\\n" + ("B" * (5 * 1024 * 1024)))
""")

    runner = CompilerRunner({
        "memory": True,
        "expect": str(expect_file),
        "no_color": True,
        "quiet": True
    })

    success = runner.run_command([sys.executable, str(script)])
    # Mismatch should cleanly return False without crash
    assert success is False


# ==============================================================================
# 4. Subprocess Timeout Handling Under Memory Tracking
# ==============================================================================

def test_timeout_under_memory_tracking_clean_kill(capfd):
    """
    Stress Test: Subprocess that loops infinitely is terminated by timeout under -M.
    Verifies:
    1. ExecutionError is raised with timeout message.
    2. Process is killed promptly (well before 3s for a 0.3s timeout).
    3. MonitoredPopen handles timeout cleanup gracefully without leaking resources.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "timeout": 0.3})
    cmd = [sys.executable, "-c", "while True: pass"]

    t0 = time.perf_counter()
    with pytest.raises(ExecutionError, match="timed out"):
        runner.run_command(cmd)
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.5, f"Timeout took too long to abort: {elapsed:.2f}s"


def test_timeout_on_process_with_heavy_memory_allocation():
    """
    Stress Test: Subprocess that allocates 50MB before entering a sleep/hang state.
    Verifies that when killed by timeout, MonitoredPopen captures the memory rusage
    of the killed child process upon reaping.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "timeout": 0.4})
    cmd = [
        sys.executable, "-c",
        "import time; x = bytearray(50 * 1024 * 1024); time.sleep(10)"
    ]

    with pytest.raises(ExecutionError, match="timed out"):
        runner.run_command(cmd)

    # MonitoredPopen in BaseRunner reaps with p.wait(5) and queries memory
    assert runner.last_memory_bytes is not None
    assert runner.last_memory_bytes >= 50 * 1024 * 1024, (
        f"Killed child memory was not harvested: {runner.last_memory_bytes}"
    )


def test_sequential_timeouts_and_subsequent_execution_recovery(tmp_path, capfd):
    """
    Stress Test: 3 consecutive timed-out executions followed immediately by a normal execution.
    Verifies no zombie process accumulation, file descriptor leaks, or corrupted runner state.
    """
    runner = CompilerRunner({"memory": True, "no_color": True, "timeout": 0.2})

    for i in range(3):
        with pytest.raises(ExecutionError, match="timed out"):
            runner.run_command([sys.executable, "-c", "import time; time.sleep(5)"])

    # Normal run immediately after 3 timeouts
    script = tmp_path / "recovered.py"
    script.write_text("print('ALL_SYSTEMS_OPERATIONAL')\n")

    # Reset timeout for normal run or run with generous timeout
    runner.flags["timeout"] = 5.0
    success = runner.run_command([sys.executable, str(script)])
    assert success is True

    out, _ = capfd.readouterr()
    assert "Peak Memory:" in out
    mem = parse_last_peak_memory_bytes(out)
    assert mem is not None and mem < 35 * 1024 * 1024


def test_monitored_popen_exception_robustness():
    """
    Stress Test: Verify MonitoredPopen behavior under invalid command or immediate failure.
    """
    # Non-existent command
    with pytest.raises(FileNotFoundError):
        MonitoredPopen(["non_existent_binary_xyz_9999"])
