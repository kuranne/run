"""Black-box CLI core execution, --keep, --build-only, --out-dir, --expect, and --timeout tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_core_c_python_bash_and_cleanup(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "hello.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("C_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res_c = run_cli(["hello.c"])
    assert res_c.returncode == 0, combined_output(res_c)
    assert "C_OK" in res_c.stdout
    bin_name = "hello.exe" if os.name == "nt" else "hello.out"
    assert not (work_dir / bin_name).exists()

    res_keep = run_cli(["--keep", "--out-dir", "dist", "hello.c"])
    assert res_keep.returncode == 0, combined_output(res_keep)
    assert (work_dir / "dist" / bin_name).is_file()

    res_build = run_cli(["--build-only", "--out-dir", "build", "hello.c"])
    assert res_build.returncode == 0, combined_output(res_build)
    assert (work_dir / "build" / bin_name).is_file()
    assert "C_OK" not in res_build.stdout

    (work_dir / "hello.py").write_text('print("PY_OK")\n', encoding="utf-8")
    res_py = run_cli(["hello.py"])
    assert res_py.returncode == 0, combined_output(res_py)
    assert "PY_OK" in res_py.stdout

    (work_dir / "hello.sh").write_text('echo "SH_OK"\n', encoding="utf-8")
    res_sh = run_cli(["hello.sh"])
    assert res_sh.returncode == 0, combined_output(res_sh)
    assert "SH_OK" in res_sh.stdout


def test_core_expect_pass_and_diff_fail(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "out.py").write_text('print("expected_line")\n', encoding="utf-8")
    (work_dir / "good.txt").write_text("expected_line\n", encoding="utf-8")
    (work_dir / "bad.txt").write_text("wrong_line\n", encoding="utf-8")

    r_pass = run_cli(["--expect", "good.txt", "out.py"])
    assert r_pass.returncode == 0, combined_output(r_pass)
    assert "[ PASS ]" in strip_ansi(r_pass.stdout)

    r_fail = run_cli(["--expect", "bad.txt", "out.py"])
    assert r_fail.returncode == 1
    out = strip_ansi(combined_output(r_fail))
    assert "[ FAIL ]" in out
    assert "-wrong_line" in out
    assert "+expected_line" in out


def test_core_timeout_kills_process(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "slow.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    res = run_cli(["--timeout", "0.3", "slow.py"])
    assert res.returncode == 1
    assert "timed out" in strip_ansi(combined_output(res)).lower()


def test_core_sigint_cleans_up_compiled_binary(
    work_dir: Path, run_binary: Path, isolated_env: dict[str, str]
) -> None:
    import signal
    import subprocess
    import time

    src = work_dir / "hang.c"
    src.write_text(
        "#include <stdio.h>\n#include <unistd.h>\n"
        "int main(void) { puts(\"READY\"); fflush(stdout); sleep(10); return 0; }\n",
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [str(run_binary), "--no-cache", "--out-dir", str(work_dir), str(src)],
        cwd=str(work_dir),
        env=isolated_env["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if "READY" in line:
            break
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=5)

    assert proc.returncode != 0
    assert not (work_dir / "hang.out").exists()


def test_core_direct_binary_and_path_command_execution(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    # 1. Build a precompiled binary with -B --out-dir, then execute it directly via `run`.
    src = work_dir / "hello_bin.c"
    src.write_text(
        '#include <stdio.h>\n'
        'int main(int argc, char **argv) {\n'
        '    printf("BIN_OK:%s\\n", argc > 1 ? argv[1] : "none");\n'
        '    return 0;\n'
        '}\n',
        encoding="utf-8",
    )
    build = run_cli(["-B", "--no-cache", "--out-dir", str(work_dir), str(src)])
    assert build.returncode == 0, combined_output(build)
    bin_path = work_dir / "hello_bin.out"
    assert bin_path.exists()

    res_bin = run_cli(["-t", str(bin_path), "world"])
    assert res_bin.returncode == 0, combined_output(res_bin)
    assert "BIN_OK:world" in res_bin.stdout
    assert "Took" in strip_ansi(res_bin.stdout)
    # Prebuilt user binary must NOT be deleted on cleanup.
    assert bin_path.exists()

    # 2. Implicit PATH command (`run -t echo hello`).
    res_path = run_cli(["-t", "echo", "hello_cmd"])
    assert res_path.returncode == 0, combined_output(res_path)
    assert "hello_cmd" in res_path.stdout
    assert "Took" in strip_ansi(res_path.stdout)

    # 3. Explicit `-- <cmd> <flags>` (`run -t -- echo -n flagged`).
    res_dash = run_cli(["-t", "--", "echo", "dash_cmd"])
    assert res_dash.returncode == 0, combined_output(res_dash)
    assert "dash_cmd" in res_dash.stdout
    assert "Took" in strip_ansi(res_dash.stdout)
