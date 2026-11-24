"""Black-box CLI repeated execution stability stress harness."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output


def test_stress_repeated_executions_clean_exit(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "ping.py").write_text('import sys\nprint("PONG:" + sys.argv[1])\n', encoding="utf-8")
    for idx in range(10):
        res = run_cli(["-q", "ping.py", "--", str(idx)])
        assert res.returncode == 0, combined_output(res)
        assert f"PONG:{idx}" in res.stdout
