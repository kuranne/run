"""Black-box CLI -M/--memory and -t/--time metrics formatting tests."""

from __future__ import annotations

import json
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


def test_mem_limit_enforcement_and_json_report(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "heavy.py").write_text(
        "import time\n"
        "buf = bytearray(40 * 1024 * 1024)\n"
        "for i in range(0, len(buf), 4096):\n"
        "    buf[i] = 1\n"
        "time.sleep(0.5)\n"
        'print("SHOULD_NOT_FINISH")\n',
        encoding="utf-8",
    )

    res_fail = run_cli(["--mem-limit", "16M", "--json", "heavy.py"])
    assert res_fail.returncode == 1
    data = json.loads(res_fail.stdout)
    assert data["ok"] is False
    assert data["run"]["memory_limit_exceeded"] is True
    assert "SHOULD_NOT_FINISH" not in data["run"]["stdout"]

    (work_dir / "light.py").write_text('print("LIGHT_OK")\n', encoding="utf-8")
    res_ok = run_cli(["--mem-limit", "128M", "--json", "light.py"])
    assert res_ok.returncode == 0, combined_output(res_ok)
    ok_data = json.loads(res_ok.stdout)
    assert ok_data["ok"] is True
    assert ok_data["run"]["memory_limit_exceeded"] is False
