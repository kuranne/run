"""Black-box CLI built-in Go language runner tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, require_optional_tool, strip_ansi


def test_go_dry_run_single_and_multi(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "main.go").write_text(
        'package main\nimport "fmt"\nfunc main() { fmt.Println("GO_OK") }\n',
        encoding="utf-8",
    )
    res = run_cli(["--dry-run", "main.go"])
    assert res.returncode == 0, combined_output(res)
    assert "go build" in strip_ansi(res.stdout)


def test_go_execution_when_installed(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    require_optional_tool("go")
    (work_dir / "main.go").write_text(
        'package main\nimport "fmt"\nfunc main() { fmt.Println("GO_LIVE_OK") }\n',
        encoding="utf-8",
    )
    res = run_cli(["main.go"])
    assert res.returncode == 0, combined_output(res)
    assert "GO_LIVE_OK" in res.stdout
