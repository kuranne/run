"""Black-box CLI C/C++ Project Manager (CPM) multi-file main detection tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_cpm_detects_main_ignoring_comments(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "helper.c").write_text(
        "// int main() { return 1; }\n"
        "/* int main(void) {} */\n"
        "int add_vals(int a, int b) { return a + b; }\n",
        encoding="utf-8",
    )
    (work_dir / "entry.c").write_text(
        "#include <stdio.h>\n"
        "int add_vals(int a, int b);\n"
        'int main(void) { printf("SUM=%d\\n", add_vals(19, 23)); return 0; }\n',
        encoding="utf-8",
    )

    res = run_cli(["-m", "helper.c", "entry.c"])
    assert res.returncode == 0, combined_output(res)
    assert "SUM=42" in res.stdout


def test_cpm_reports_missing_main(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "a.c").write_text("int f1(void) { return 1; }\n", encoding="utf-8")
    (work_dir / "b.c").write_text("int f2(void) { return 2; }\n", encoding="utf-8")

    res = run_cli(["-m", "a.c", "b.c"])
    assert res.returncode == 1
    assert "Could not find a 'main' function" in strip_ansi(combined_output(res))
