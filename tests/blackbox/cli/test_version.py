"""Black-box CLI version tests verifying Cargo.toml metadata version reporting."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Callable

from conftest import REPO_ROOT, combined_output


def _cargo_version() -> str:
    data = tomllib.loads((REPO_ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    return str(data["package"]["version"])


def test_version_long_and_short_flags(run_cli: Callable[..., object]) -> None:
    expected = _cargo_version()
    for flag in ("--version", "-V"):
        res = run_cli([flag])
        assert res.returncode == 0, combined_output(res)
        assert expected in combined_output(res)
