"""Black-box CLI adversarial memory-tracking tests (empty, large alloc, non-zero exit, timeout)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_adversarial_memory_tracking_boundaries(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "empty.py").write_text("", encoding="utf-8")
    r_empty = run_cli(["-M", "empty.py"])
    assert r_empty.returncode == 0, combined_output(r_empty)
    assert "Peak Memory:" in strip_ansi(r_empty.stdout)

    (work_dir / "large.py").write_text(
        "data = bytearray(32 * 1024 * 1024)\n"
        "data[0] = 1\n"
        "data[-1] = 2\n"
        'print("ALLOC_OK")\n',
        encoding="utf-8",
    )
    r_large = run_cli(["-M", "large.py"])
    assert r_large.returncode == 0, combined_output(r_large)
    out = strip_ansi(r_large.stdout)
    assert "ALLOC_OK" in out
    assert "Peak Memory:" in out

    (work_dir / "hang.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    r_to = run_cli(["-M", "--timeout", "0.3", "hang.py"])
    assert r_to.returncode == 1
    assert "timed out" in strip_ansi(combined_output(r_to)).lower()
