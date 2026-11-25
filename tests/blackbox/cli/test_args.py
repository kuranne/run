"""Black-box CLI argument parsing and preprocessing tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_args_flags_and_argument_dash_values(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "macro.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("M=%d\\n", M); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["-F", "-DM=9", "macro.c"])
    assert res.returncode == 0, combined_output(res)
    assert "M=9" in res.stdout

    (work_dir / "echo.py").write_text(
        'import sys\nprint("|".join(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    res_a = run_cli(["-a", "--help", "echo.py"])
    assert res_a.returncode == 0, combined_output(res_a)
    assert "--help" in res_a.stdout


def test_args_double_dash_merge_order(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "show.py").write_text(
        'import sys\nprint("A:" + "|".join(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    res = run_cli(["--argument", "first second", "show.py", "--", "--third", "4"])
    assert res.returncode == 0, combined_output(res)
    assert "A:first|second|--third|4" in res.stdout


def test_args_aliases_and_verbose(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    sub = work_dir / "sub"
    sub.mkdir()
    (sub / "ok.py").write_text('print("OK_ALIAS")\n', encoding="utf-8")

    res = run_cli(["--cwd", str(sub), "-v", "--mem", "-t", "ok.py"])
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "[ DEBUG ] Debug logging enabled" in out
    assert "OK_ALIAS" in out
    assert "Took" in out
    assert "Peak Memory:" in out

    res_clean = run_cli(["--clean"])
    assert res_clean.returncode == 0, combined_output(res_clean)
    assert "[ CLEAN ] Build cache cleared successfully." in strip_ansi(res_clean.stdout)


def test_args_stdin_and_optional_flags(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "read_in.py").write_text(
        'import sys\nprint("IN:" + sys.stdin.read().strip())\n',
        encoding="utf-8",
    )
    (work_dir / "data.txt").write_text("from_file\n", encoding="utf-8")

    res_file = run_cli(["-i", "data.txt", "read_in.py"])
    assert res_file.returncode == 0, combined_output(res_file)
    assert "IN:from_file" in res_file.stdout

    res_pipe = run_cli(["-i", "-q", "read_in.py"], stdin_text="from_pipe\n")
    assert res_pipe.returncode == 0, combined_output(res_pipe)
    assert "IN:from_pipe" in res_pipe.stdout

    # Explicit --stdin-text with \n escape
    res_text = run_cli(["--stdin-text", "5 10\\n", "read_in.py"])
    assert res_text.returncode == 0, combined_output(res_text)
    assert "IN:5 10" in res_text.stdout

    # Explicit --stdin-file
    res_sfile = run_cli(["--stdin-file", "data.txt", "read_in.py"])
    assert res_sfile.returncode == 0, combined_output(res_sfile)
    assert "IN:from_file" in res_sfile.stdout

    # Dynamic -i / --stdin: non-existent path treated as inline text
    res_dyn = run_cli(["-i", "hello_inline\\n", "read_in.py"])
    assert res_dyn.returncode == 0, combined_output(res_dyn)
    assert "IN:hello_inline" in res_dyn.stdout


def test_args_plain_and_tui_conflict(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "hello.py").write_text('print("PLAIN_OK")\n', encoding="utf-8")

    res_plain = run_cli(["--plain", "hello.py"])
    assert res_plain.returncode == 0, combined_output(res_plain)
    out = strip_ansi(res_plain.stdout)
    assert "[ RUN ]" in out
    assert "PLAIN_OK" in out

    res_conflict = run_cli(["--plain", "--tui", "hello.py"])
    assert res_conflict.returncode != 0
