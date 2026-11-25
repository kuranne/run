"""Black-box CLI [tasks] execution, compound operators, and argument forwarding tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_tasks_simple_compound_and_quoted_operators(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        'echo_args = "python3 -c \\"import sys; print(\'ARGS:\' + \'|\'.join(sys.argv[1:]))\\""\n'
        'chain_ok = "python3 -c \\"print(\'STEP1\')\\" && python3 -c \\"print(\'STEP2\')\\""\n'
        'chain_fail = "python3 -c \\"import sys; sys.exit(1)\\" && python3 -c \\"print(\'SKIPPED_STEP\')\\""\n'
        'semi_ok = "python3 -c \\"print(\'SEMI1\')\\" ; python3 -c \\"print(\'SEMI2\')\\""\n'
        "quoted = \"python3 -c \\\"import sys; print('Q:' + sys.argv[1])\\\" 'a && b ; c'\"\n",
        encoding="utf-8",
    )

    r_args = run_cli(["echo_args", "one", "two"])
    assert r_args.returncode == 0, combined_output(r_args)
    assert "ARGS:one|two" in r_args.stdout

    r_ok = run_cli(["chain_ok"])
    assert r_ok.returncode == 0, combined_output(r_ok)
    assert r_ok.stdout.index("STEP1") < r_ok.stdout.index("STEP2")

    r_fail = run_cli(["chain_fail"])
    assert r_fail.returncode == 1
    non_task = [l for l in strip_ansi(combined_output(r_fail)).splitlines() if "[ TASK ]" not in l]
    assert all("SKIPPED_STEP" not in l for l in non_task)

    r_semi = run_cli(["semi_ok"])
    assert r_semi.returncode == 0, combined_output(r_semi)
    assert r_semi.stdout.index("SEMI1") < r_semi.stdout.index("SEMI2")

    r_q = run_cli(["quoted"])
    assert r_q.returncode == 0, combined_output(r_q)
    assert "Q:a && b ; c" in r_q.stdout
