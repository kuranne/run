"""Black-box CLI [projects] manifest detection, two-step build+run, and .git boundary tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_project_two_step_and_single_step(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[projects.twostep]\n"
        'file = "demo.proj"\n'
        'build = "python3 -c \\"print(\'BUILD_OK\')\\""\n'
        'run = "python3 -c \\"import sys; print(\'RUN_OK:\' + \'|\'.join(sys.argv[1:]))\\""\n',
        encoding="utf-8",
    )
    (work_dir / "demo.proj").write_text("ok\n", encoding="utf-8")

    res = run_cli(["--", "p1", "p2"])
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "Detected project 'twostep' via demo.proj" in out
    assert "BUILD_OK" in out
    assert "RUN_OK:p1|p2" in out


def test_project_detection_stops_at_git_boundary(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Makefile").write_text("all:\n\t@echo ROOT_MAKE\n", encoding="utf-8")
    nested = work_dir / "child_repo"
    nested.mkdir()
    (nested / ".git").mkdir()

    res = run_cli([], cwd=nested, stdin_text="")
    assert res.returncode == 0, combined_output(res)
    assert "ROOT_MAKE" not in res.stdout
    assert "[ INPUT ] No file given" in strip_ansi(res.stdout)
