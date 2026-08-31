import pytest
from pathlib import Path
from runner.core import CompilerRunner
from runner.test_runner import TestcasesRunner
from util.output import Printer

def test_discover_test_pairs(tmp_path):
    # Setup test pairs
    (tmp_path / "01.in").write_text("1 2\n")
    (tmp_path / "01.out").write_text("3\n")

    (tmp_path / "02.in").write_text("10 20\n")
    (tmp_path / "02.ans").write_text("30\n")

    (tmp_path / "in3.txt").write_text("5 5\n")
    (tmp_path / "out3.txt").write_text("10\n")

    # Unmatched file
    (tmp_path / "unmatched.in").write_text("99\n")

    pairs = TestcasesRunner.discover_test_pairs(tmp_path)
    assert len(pairs) == 3
    pair_stems = [(p[0].name, p[1].name) for p in pairs]
    assert ("01.in", "01.out") in pair_stems
    assert ("02.in", "02.ans") in pair_stems
    assert ("in3.txt", "out3.txt") in pair_stems

def test_batch_run_testcases(tmp_path, capfd):
    # Setup solution file
    sol = tmp_path / "solution.py"
    sol.write_text("""
import sys
for line in sys.stdin:
    parts = line.strip().split()
    if parts:
        print(int(parts[0]) + int(parts[1]))
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("1 2\n")
    (test_dir / "01.out").write_text("3\n")

    (test_dir / "02.in").write_text("10 20\n")
    (test_dir / "02.out").write_text("30\n")

    runner = CompilerRunner({"dry_run": False})
    success = TestcasesRunner.run_tests(runner, test_dir, sol)
    assert success is True

    out, _ = capfd.readouterr()
    assert "Passed: 2/2 (100.0%)" in out

def test_batch_run_with_failures(tmp_path, capfd):
    # Setup solution file with intentional wrong answer on test 2
    sol = tmp_path / "solution.py"
    sol.write_text("""
import sys
for line in sys.stdin:
    parts = line.strip().split()
    if parts:
        print(int(parts[0]) + int(parts[1]))
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("1 2\n")
    (test_dir / "01.out").write_text("3\n")

    # Incorrect expected output
    (test_dir / "02.in").write_text("10 20\n")
    (test_dir / "02.out").write_text("999\n")

    runner = CompilerRunner({"dry_run": False})
    success = TestcasesRunner.run_tests(runner, test_dir, sol)
    assert success is False

    out, _ = capfd.readouterr()
    assert "Passed: 1/2 (50.0%)" in out

def test_expect_diff_matching(tmp_path, capfd, caplog):
    runner = CompilerRunner({"dry_run": False})
    
    script = tmp_path / "app.py"
    script.write_text("print('hello world')")

    expected = tmp_path / "expected.txt"
    expected.write_text("hello world\n")

    # Should match
    runner.flags["expect"] = str(expected)
    assert runner.run_command(["python3", str(script)]) is True
    out, _ = capfd.readouterr()
    assert "Output matches" in caplog.text

    # Should mismatch
    mismatch_expected = tmp_path / "mismatch.txt"
    mismatch_expected.write_text("goodbye world\n")
    runner.flags["expect"] = str(mismatch_expected)
    assert runner.run_command(["python3", str(script)]) is False
    assert "Output mismatch" in caplog.text
    err_out, _ = capfd.readouterr()
    assert "Differences" in err_out

def test_batch_run_with_memory_tracking(tmp_path, capfd):
    sol = tmp_path / "solution.py"
    sol.write_text("""
import sys
for line in sys.stdin:
    parts = line.strip().split()
    if parts:
        print(int(parts[0]) + int(parts[1]))
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("1 2\n")
    (test_dir / "01.out").write_text("3\n")
    (test_dir / "02.in").write_text("10 20\n")
    (test_dir / "02.out").write_text("30\n")

    runner = CompilerRunner({"dry_run": False, "memory": True, "no_color": True})
    success = TestcasesRunner.run_tests(runner, test_dir, sol)
    assert success is True
    out, _ = capfd.readouterr()
    assert "Passed: 2/2 (100.0%)" in out
    assert "Peak Memory:" in out


def test_discover_test_pairs_natural_sort_and_prefixes(tmp_path):
    """Test natural numerical ordering and input*.txt to *.ans/out matching."""
    (tmp_path / "1.in").write_text("1")
    (tmp_path / "1.out").write_text("1")
    (tmp_path / "2.in").write_text("2")
    (tmp_path / "2.ans").write_text("2")
    (tmp_path / "10.in").write_text("10")
    (tmp_path / "10.out").write_text("10")
    (tmp_path / "input5.txt").write_text("5")
    (tmp_path / "ans5.txt").write_text("5")
    (tmp_path / "input_6.txt").write_text("6")
    (tmp_path / "output_6.txt").write_text("6")

    pairs = TestcasesRunner.discover_test_pairs(tmp_path)
    in_names = [p[0].name for p in pairs]
    # Verify natural numerical order: 1.in, 2.in, 10.in (not 1.in, 10.in, 2.in)
    assert in_names == ["1.in", "2.in", "10.in", "input5.txt", "input_6.txt"]
    matched_stems = [(p[0].name, p[1].name) for p in pairs]
    assert ("input5.txt", "ans5.txt") in matched_stems
    assert ("input_6.txt", "output_6.txt") in matched_stems


def test_batch_run_preserves_runner_flags(tmp_path):
    """Test that running batch test suite cleans up and restores runner flags."""
    sol = tmp_path / "app.py"
    sol.write_text("import sys; print(sys.stdin.read())")

    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "01.in").write_text("hello")
    (tdir / "01.out").write_text("hello\n")

    runner = CompilerRunner({"dry_run": False, "stdin": "orig_in.txt", "expect": "orig_out.txt"})
    runner._buffered_stdin = "orig_buffered"

    success = TestcasesRunner.run_tests(runner, tdir, sol)
    assert success is True
    assert runner.flags.get("stdin") == "orig_in.txt"
    assert runner.flags.get("expect") == "orig_out.txt"
    assert runner._buffered_stdin == "orig_buffered"


