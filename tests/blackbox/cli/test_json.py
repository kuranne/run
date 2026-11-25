"""Black-box CLI --json and --json-schema machine-readable output tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from conftest import combined_output


def test_json_schema_is_valid_json(run_cli: Callable[..., object]) -> None:
    res = run_cli(["--json-schema"])
    assert res.returncode == 0, combined_output(res)
    schema = json.loads(res.stdout)
    assert schema["title"] == "RunJsonReport"
    assert "properties" in schema
    assert "tests" in schema["properties"]
    assert "bench" in schema["properties"]
    assert "stress" in schema["properties"]


def test_json_single_c_run_and_missing_file_error(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    src = work_dir / "hello.c"
    src.write_text('#include <stdio.h>\nint main(void){puts("JSON_HI"); return 0;}\n', encoding="utf-8")

    res = run_cli(["--json", str(src)])
    assert res.returncode == 0, combined_output(res)
    data = json.loads(res.stdout)
    assert data["schema_version"] == 1
    assert data["ok"] is True
    assert data["exit_code"] == 0
    assert data["mode"] == "run"
    assert data["compile"] is not None
    assert data["run"]["stdout"].replace("\r\n", "\n") == "JSON_HI\n"

    # Missing file still emits valid JSON with ok=false and non-empty errors.
    res_err = run_cli(["--json", "does_not_exist.c"])
    assert res_err.returncode == 1
    err_data = json.loads(res_err.stdout)
    assert err_data["ok"] is False
    assert err_data["exit_code"] == 1
    assert len(err_data["errors"]) >= 1


def test_json_test_dir_bench_and_stress(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    # 1. --test-dir --json
    (work_dir / "echo.py").write_text("import sys\nprint(sys.stdin.read().strip())\n", encoding="utf-8")
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("alpha\n", encoding="utf-8")
    (cases / "1.out").write_text("alpha\n", encoding="utf-8")
    (cases / "2.in").write_text("beta\n", encoding="utf-8")
    (cases / "2.out").write_text("WRONG\n", encoding="utf-8")

    res_td = run_cli(["--json", "--test-dir", "cases", "echo.py"])
    assert res_td.returncode == 1
    td_data = json.loads(res_td.stdout)
    assert td_data["mode"] == "test"
    assert td_data["tests"]["total"] == 2
    assert td_data["tests"]["passed"] == 1
    assert td_data["tests"]["failed"] == 1
    assert td_data["tests"]["cases"][1]["actual"].strip() == "beta"

    # 2. --bench --json
    res_bench = run_cli(["--json", "--bench", "3", "--warmup", "1", "--", "echo", "hi"])
    assert res_bench.returncode == 0, combined_output(res_bench)
    b_data = json.loads(res_bench.stdout)
    assert b_data["mode"] == "bench"
    assert b_data["bench"]["runs"] == 3
    assert b_data["bench"]["warmup"] == 1

    # 3. --stress --json
    gen = work_dir / "gen.py"
    gen.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
    brute = work_dir / "brute.py"
    brute.write_text("print(int(input()) * 2)\n", encoding="utf-8")
    sol = work_dir / "sol.py"
    sol.write_text("n = int(input())\nprint(999 if n == 2 else n * 2)\n", encoding="utf-8")

    res_stress = run_cli(
        ["--json", str(sol), "--stress", str(gen), str(brute), "--runs", "4", "--seed", "1"]
    )
    assert res_stress.returncode == 1
    s_data = json.loads(res_stress.stdout)
    assert s_data["mode"] == "stress"
    assert s_data["stress"]["passed"] is False
    assert s_data["stress"]["failed_seed"] == 2
