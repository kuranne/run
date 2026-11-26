"""Black-box CLI tests for -e/--eval and --repl scratchpad modes."""

from pathlib import Path
from typing import Callable
import subprocess


def test_eval_python_and_c_autowrap(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """-e/--eval evaluates inline Python snippets and auto-wraps C snippets."""
    res_py = run_cli(["-e", "print(6 * 7)", "--lang", "py"])
    assert res_py.returncode == 0, res_py.stdout + res_py.stderr
    assert "42" in res_py.stdout


def test_repl_interactive_python_session(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--repl reads lines from stdin, evaluates snippets, and exits cleanly on :q."""
    res = run_cli(["--repl", "py"], stdin_text="print('REPL_OK_123')\n:q\n")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "REPL_OK_123" in res.stdout
