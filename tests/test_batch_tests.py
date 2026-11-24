"""Black-box CLI --test-dir batch testcase discovery, natural sort, and summary tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_batch_test_dir_c_single_compile_and_natural_order(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "double.c").write_text(
        "#include <stdio.h>\n"
        'int main(void) { int x; if (scanf("%d", &x) == 1) printf("%d\\n", x * 2); return 0; }\n',
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    for n in (1, 2, 10):
        (cases / f"in{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        (cases / f"out{n}.txt").write_text(f"{n * 2}\n", encoding="utf-8")

    res = run_cli(["--test-dir", "cases", "double.c"])
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "Discovered 3 test case(s)" in out
    assert "Passed: 3/3 (100.0%)" in out
    assert out.index("out2.txt") < out.index("out10.txt")
    # Single-compile check: gcc invoked once for 3 test cases.
    assert out.count("[ COMPILE ]") == 1


def test_batch_test_dir_failure_exit_code_1(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "echo.py").write_text("import sys\nprint(sys.stdin.read().strip())\n", encoding="utf-8")
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("hello\n", encoding="utf-8")
    (cases / "1.out").write_text("different\n", encoding="utf-8")

    res = run_cli(["--test-dir", "cases", "echo.py"])
    assert res.returncode == 1
    out = strip_ansi(combined_output(res))
    assert "[ FAIL ]" in out
    assert "Passed: 0/1 (0.0%)" in out
