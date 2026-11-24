"""Black-box CLI debugger (--debug/--gdb/--lldb/--valgrind) and sanitizer flag tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_debugger_and_sanitizer_dry_run_flags(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    r_gdb = run_cli(["--dry-run", "--gdb", "app.c"])
    assert r_gdb.returncode == 0, combined_output(r_gdb)
    out_gdb = strip_ansi(r_gdb.stdout)
    assert "gdb --args" in out_gdb

    r_lldb = run_cli(["--dry-run", "--lldb", "app.c"])
    assert r_lldb.returncode == 0, combined_output(r_lldb)
    assert "lldb --" in strip_ansi(r_lldb.stdout)

    r_val = run_cli(["--dry-run", "--valgrind", "app.c"])
    assert r_val.returncode == 0, combined_output(r_val)
    assert "valgrind" in strip_ansi(r_val.stdout)

    r_asan = run_cli(["--dry-run", "--asan", "app.c"])
    assert r_asan.returncode == 0, combined_output(r_asan)
    assert "-fsanitize=address,undefined" in strip_ansi(r_asan.stdout)

    r_tsan = run_cli(["--dry-run", "--tsan", "app.c"])
    assert r_tsan.returncode == 0, combined_output(r_tsan)
    assert "-fsanitize=thread" in strip_ansi(r_tsan.stdout)

    r_san = run_cli(["--dry-run", "--sanitize", "undefined", "app.c"])
    assert r_san.returncode == 0, combined_output(r_san)
    assert "-fsanitize=undefined" in strip_ansi(r_san.stdout)
