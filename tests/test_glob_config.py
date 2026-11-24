"""Black-box CLI glob exclusions and glob manifest matching tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_glob_core_exclusions_in_autolink(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[core]\n"
        'exclude_files = ["*_skip.c"]\n'
        'exclude_dirs = ["ignored_*"]\n',
        encoding="utf-8",
    )
    (work_dir / "main.c").write_text(
        '#include <stdio.h>\nint ok_fn(void);\nint main(void) { printf("OK=%d\\n", ok_fn()); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "helper.c").write_text("int ok_fn(void) { return 11; }\n", encoding="utf-8")
    (work_dir / "broken_skip.c").write_text("THIS IS INVALID C;\n", encoding="utf-8")
    ign = work_dir / "ignored_sub"
    ign.mkdir()
    (ign / "bad.c").write_text("ALSO INVALID C;\n", encoding="utf-8")

    res = run_cli(["-L"])
    assert res.returncode == 0, combined_output(res)
    assert "OK=11" in res.stdout


def test_glob_project_manifest_detection(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[projects.sln]\n"
        'file = "*.sln"\n'
        'command = "python3 -c \\"import sys; print(\'MATCH:\' + sys.argv[1])\\" {filename}"\n',
        encoding="utf-8",
    )
    (work_dir / "AppB.sln").write_text("", encoding="utf-8")
    (work_dir / "AppA.sln").write_text("", encoding="utf-8")

    res = run_cli([])
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "Multiple manifest files matched '*.sln'" in out
    assert "MATCH:AppA.sln" in out
