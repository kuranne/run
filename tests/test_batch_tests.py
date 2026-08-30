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
