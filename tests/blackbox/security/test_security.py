"""Black-box CLI security tests: suspicious flags, env sanitization, --expect boundary, path checks."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_security_blocks_suspicious_flags_unless_unsafe(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    blocked = run_cli(["--flags", "-Wl,-rpath,/tmp", "main.c"])
    assert blocked.returncode == 1
    assert "Suspicious flag" in strip_ansi(combined_output(blocked))

    allowed = run_cli(["--unsafe", "--dry-run", "--flags", "-Wl,-rpath,/tmp", "main.c"])
    assert allowed.returncode == 0, combined_output(allowed)


def test_security_blocks_dangerous_env_and_expect_escape(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "env_check.py").write_text(
        'import os\nprint("LD=" + os.environ.get("LD_PRELOAD", "BLOCKED"))\n',
        encoding="utf-8",
    )
    res_env = run_cli(["--env", "LD_PRELOAD=evil.so", "env_check.py"])
    assert res_env.returncode == 0, combined_output(res_env)
    out = strip_ansi(res_env.stdout)
    assert "LD=BLOCKED" in out
    assert "[ WARN ]" in out

    outside = work_dir.parent / "outside_expect.txt"
    outside.write_text("LD=BLOCKED\n", encoding="utf-8")
    res_esc = run_cli(["--expect", str(outside), "env_check.py"])
    assert res_esc.returncode == 1
    assert "[ ERROR ]" in strip_ansi(combined_output(res_esc))

    res_forced = run_cli(["-f", "--expect", str(outside), "env_check.py"])
    assert res_forced.returncode == 0, combined_output(res_forced)
    assert "[ PASS ]" in strip_ansi(res_forced.stdout)
