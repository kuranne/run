"""Black-box CLI -M/--memory and -t/--time metrics formatting tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_memory_and_time_metrics_output(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "alloc.py").write_text(
        'buf = bytearray(8 * 1024 * 1024)\nprint("LEN", len(buf))\n',
        encoding="utf-8",
    )

    r_mem = run_cli(["-M", "alloc.py"])
    assert r_mem.returncode == 0, combined_output(r_mem)
    assert "Peak Memory:" in strip_ansi(r_mem.stdout)

    r_both = run_cli(["-t", "-M", "alloc.py"])
    assert r_both.returncode == 0, combined_output(r_both)
    out = strip_ansi(r_both.stdout)
    assert "Took" in out
    assert "Peak Memory:" in out
