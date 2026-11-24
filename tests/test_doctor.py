"""Black-box CLI --doctor diagnostics tests."""

from __future__ import annotations

from typing import Callable

from conftest import combined_output, strip_ansi


def test_doctor_reports_host_toolchains(run_cli: Callable[..., object]) -> None:
    res = run_cli(["--doctor", "--no-color"])
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "[ OK ]" in out
    assert "python" in out.lower() or "gcc" in out.lower()
