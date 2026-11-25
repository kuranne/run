"""Black-box CLI sandbox config validation and --sandbox-stop tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_sandbox_invalid_config_rejected(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "a.py").write_text("print(1)\n", encoding="utf-8")

    (work_dir / "Run.toml").write_text('[sandbox]\nimage = "-bad-image"\n', encoding="utf-8")
    r1 = run_cli(["a.py"])
    assert r1.returncode == 1
    assert "[ ERROR ]" in strip_ansi(combined_output(r1))

    (work_dir / "Run.toml").write_text('[sandbox]\ndockerfile = "missing.Dockerfile"\n', encoding="utf-8")
    r2 = run_cli(["a.py"])
    assert r2.returncode == 1
    assert "[ ERROR ]" in strip_ansi(combined_output(r2))

    (work_dir / "Run.toml").write_text('[sandbox]\ncompose = "missing-compose.yml"\n', encoding="utf-8")
    r3 = run_cli(["a.py"])
    assert r3.returncode == 1
    assert "[ ERROR ]" in strip_ansi(combined_output(r3))


def test_sandbox_stop_exits_cleanly(run_cli: Callable[..., object]) -> None:
    res = run_cli(["--sandbox-stop"])
    assert res.returncode == 0, combined_output(res)
