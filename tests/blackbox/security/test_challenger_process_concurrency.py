"""Black-box CLI concurrent invocations and parallel multi-file compilation tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from conftest import combined_output


def test_concurrent_cli_invocations_isolated(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    for idx in range(4):
        (work_dir / f"worker_{idx}.py").write_text(
            f'print("WORKER_{idx}_OK")\n',
            encoding="utf-8",
        )

    def _run_one(i: int) -> tuple[int, str]:
        res = run_cli([f"worker_{i}.py"])
        return res.returncode, res.stdout

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_run_one, range(4)))

    for idx, (rc, stdout) in enumerate(results):
        assert rc == 0
        assert f"WORKER_{idx}_OK" in stdout


def test_parallel_c_multifile_jobs_flag(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    for idx in range(3):
        (work_dir / f"m{idx}.c").write_text(
            f"int f{idx}(void) {{ return {idx + 1}; }}\n",
            encoding="utf-8",
        )
    (work_dir / "main.c").write_text(
        "#include <stdio.h>\n"
        "int f0(void); int f1(void); int f2(void);\n"
        'int main(void) { printf("PAR=%d\\n", f0() + f1() + f2()); return 0; }\n',
        encoding="utf-8",
    )

    res = run_cli(["-m", "-j", "2", "m0.c", "m1.c", "m2.c", "main.c"])
    assert res.returncode == 0, combined_output(res)
    assert "PAR=6" in res.stdout
