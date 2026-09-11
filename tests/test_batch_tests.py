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


def test_batch_run_multi_c_files(tmp_path, capfd):
    """Test batch testcases runner with multiple C source files (-m)."""
    header = tmp_path / "math_lib.h"
    header.write_text("int add_numbers(int a, int b);\n")

    math_c = tmp_path / "math_lib.c"
    math_c.write_text("""#include "math_lib.h"
int add_numbers(int a, int b) {
    return a + b;
}
""")

    main_c = tmp_path / "main.c"
    main_c.write_text("""#include <stdio.h>
#include "math_lib.h"

int main(void) {
    int a, b;
    if (scanf("%d %d", &a, &b) == 2) {
        printf("%d\\n", add_numbers(a, b));
    }
    return 0;
}
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("10 20\n")
    (test_dir / "01.out").write_text("30\n")
    (test_dir / "02.in").write_text("100 250\n")
    (test_dir / "02.out").write_text("350\n")

    runner = CompilerRunner({"dry_run": False})
    try:
        success = TestcasesRunner.run_tests(runner, test_dir, [main_c, math_c, header], is_multi=True)
        assert success is True
        out, _ = capfd.readouterr()
        assert "Passed: 2/2 (100.0%)" in out
    finally:
        runner.cleanup()


def test_batch_run_multi_cpm_detection(tmp_path, capfd):
    """Test CPM locates entry point when source files are provided in reverse order."""
    header = tmp_path / "calc.h"
    header.write_text("int multiply(int a, int b);\n")

    calc_c = tmp_path / "calc.c"
    calc_c.write_text("""#include "calc.h"
int multiply(int a, int b) {
    return a * b;
}
""")

    main_c = tmp_path / "app_main.c"
    main_c.write_text("""#include <stdio.h>
#include "calc.h"

int main(void) {
    int x, y;
    if (scanf("%d %d", &x, &y) == 2) {
        printf("%d\\n", multiply(x, y));
    }
    return 0;
}
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("6 7\n")
    (test_dir / "01.out").write_text("42\n")

    runner = CompilerRunner({"dry_run": False})
    try:
        # Pass non-main file first, without explicit is_multi=True
        success = TestcasesRunner.run_tests(runner, test_dir, [calc_c, header, main_c])
        assert success is True
        out, _ = capfd.readouterr()
        assert "Passed: 1/1 (100.0%)" in out
    finally:
        runner.cleanup()


def test_batch_run_multi_compilation_failure(tmp_path, capfd):
    """Test batch test runner gracefully fails when multi-file compilation encounters errors."""
    bad_c = tmp_path / "broken.c"
    bad_c.write_text("syntax error here !!!")

    main_c = tmp_path / "main.c"
    main_c.write_text("""#include <stdio.h>
int main(void) {
    return 0;
}
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("1\n")
    (test_dir / "01.out").write_text("1\n")

    runner = CompilerRunner({"dry_run": False})
    try:
        success = TestcasesRunner.run_tests(runner, test_dir, [main_c, bad_c], is_multi=True)
        assert success is False
    finally:
        runner.cleanup()


def test_batch_run_with_auto_link_cli(tmp_path, monkeypatch, capfd):
    """Test CLI integration when combining -L and --test-dir."""
    header = tmp_path / "helper.h"
    header.write_text("int square(int x);\n")

    helper_c = tmp_path / "helper.c"
    helper_c.write_text("""#include "helper.h"
int square(int x) {
    return x * x;
}
""")

    main_c = tmp_path / "main.c"
    main_c.write_text("""#include <stdio.h>
#include "helper.h"

int main(void) {
    int n;
    if (scanf("%d", &n) == 1) {
        printf("%d\\n", square(n));
    }
    return 0;
}
""")

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "01.in").write_text("9\n")
    (test_dir / "01.out").write_text("81\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["run", "--unsafe", "-L", "--test-dir", str(test_dir)])

    from main import main
    exit_code = main()
    assert exit_code == 0
    out, _ = capfd.readouterr()
    assert "Passed: 1/1 (100.0%)" in out


