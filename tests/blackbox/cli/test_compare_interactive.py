"""Black-box CLI tests for --cmp (tokens/float), --interactive, and --compare."""

from pathlib import Path
from typing import Callable
import subprocess


def test_cmp_tokens_and_float_with_expect(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--cmp tokens and --cmp float:<eps> validate whitespace- and epsilon-tolerant outputs."""
    prog = work_dir / "calc.py"
    prog.write_text('print("1   2\\n3.1415926")\n', encoding="utf-8")
    exp = work_dir / "expected.txt"
    exp.write_text("1 2 3.141593\n", encoding="utf-8")

    # Exact mode fails on spacing & float difference.
    res_exact = run_cli(["--expect", str(exp), str(prog)])
    assert res_exact.returncode != 0

    # Float mode passes with 1e-5 tolerance.
    res_float = run_cli(["--expect", str(exp), "--cmp", "float:1e-5", str(prog)])
    assert res_float.returncode == 0, res_float.stdout + res_float.stderr
    assert "[ PASS ]" in res_float.stdout

    # Tokens mode ignores extra whitespace when tokens match.
    exp_tok = work_dir / "expected_tok.txt"
    exp_tok.write_text("1 2 3.1415926\n", encoding="utf-8")
    res_tok = run_cli(["--expect", str(exp_tok), "--cmp", "tokens", str(prog)])
    assert res_tok.returncode == 0, res_tok.stdout + res_tok.stderr
    assert "[ PASS ]" in res_tok.stdout


def test_interactive_judge_pass_and_fail(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--interactive bridges target and interactor stdio and reports pass/fail."""
    sol = work_dir / "sol.py"
    sol.write_text(
        "import sys\n"
        "for g in [3, 7]:\n"
        "    print(g, flush=True)\n"
        "    if sys.stdin.readline().strip() == 'OK':\n"
        "        break\n",
        encoding="utf-8",
    )
    inter = work_dir / "interactor.py"
    inter.write_text(
        "import sys\n"
        "for line in sys.stdin:\n"
        "    if int(line.strip()) == 7:\n"
        "        print('OK', flush=True)\n"
        "        sys.exit(0)\n"
        "    print('NO', flush=True)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    res = run_cli(["--interactive", str(inter), "--timeout", "5", str(sol)])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[ PASS ]" in res.stdout


def test_compare_bench_two_targets(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--compare benchmarks two targets side-by-side and prints a COMPARE summary."""
    a = work_dir / "a.py"
    b = work_dir / "b.py"
    a.write_text("print(sum(range(100)))\n", encoding="utf-8")
    b.write_text("print(4950)\n", encoding="utf-8")

    res = run_cli(["--bench", "2", "--warmup", "1", "--compare", str(b), str(a)])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[ COMPARE ]" in res.stdout
