"""Tier 4 E2E tests: real-world developer workflow scenarios (S01-S09)."""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.e2e.conftest import ANSI_RE, require_optional_tool, strip_ansi

pytestmark = pytest.mark.tier4


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


def _read_until(
    proc: subprocess.Popen[str],
    needle: str,
    *,
    timeout: float = 10.0,
) -> str:
    """Read proc.stdout non-blockingly until *needle* appears or deadline expires."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            events = sel.select(timeout=remaining)
            if not events:
                if proc.poll() is not None:
                    break
                continue
            data = os.read(fd, 4096).decode("utf-8", errors="replace")
            if not data:
                if proc.poll() is not None:
                    break
                continue
            chunks.append(data)
            joined = "".join(chunks)
            if needle in joined:
                return joined
    finally:
        sel.close()
    return "".join(chunks)


def test_s01_competitive_programming_batch_judge(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """S01: C++20 preset compile-once, natural-sorted --test-dir judge, WA exit 1 -> fix -> AC exit 0."""
    (work_dir / "Run.toml").write_text(
        '[presets.cp]\ncpp = ["-std=c++20", "-O2", "-DBONUS=10"]\n',
        encoding="utf-8",
    )
    sol = work_dir / "solution.cpp"
    sol.write_text(
        "#include <iostream>\n"
        "#include <vector>\n"
        "#ifndef BONUS\n"
        "#define BONUS 0\n"
        "#endif\n"
        "int main() {\n"
        "    int x = 0;\n"
        "    if (!(std::cin >> x)) return 1;\n"
        "    std::vector<char> buf(2 * 1024 * 1024, 1);\n"
        "    int adj = (buf[0] == 1) ? BONUS : 0;\n"
        "    if (x == 5) {\n"
        '        std::cout << (x + adj + 99) << "\\n";\n'
        "    } else {\n"
        '        std::cout << (x + adj) << "\\n";\n'
        "    }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    tests_dir = work_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "1.in").write_text("0\n", encoding="utf-8")
    (tests_dir / "1.out").write_text("10\n", encoding="utf-8")
    (tests_dir / "2.in").write_text("5\n", encoding="utf-8")
    (tests_dir / "2.ans").write_text("15\n", encoding="utf-8")
    (tests_dir / "10.in").write_text("32\n", encoding="utf-8")
    (tests_dir / "10.out").write_text("42\n", encoding="utf-8")

    res_wa = run_cli(["-p", "cp", "-t", "-M", "--test-dir", "tests", "solution.cpp"])
    assert res_wa.returncode == 1, _combined(res_wa)
    out_wa = strip_ansi(res_wa.stdout)
    assert "Discovered 3 test case(s)" in out_wa
    assert "=== Running Test Suite (3 cases) ===" in out_wa
    assert out_wa.count("[ COMPILE ]") == 1
    assert out_wa.count("[ RUN ]") == 3
    assert "[ PASS ]" in out_wa
    assert "[ FAIL ]" in out_wa
    assert "--- Differences ---" in out_wa
    assert "=== Test Summary ===" in out_wa
    assert "Passed: 2/3 (66.7%)" in out_wa

    sol.write_text(
        "#include <iostream>\n"
        "#include <vector>\n"
        "#ifndef BONUS\n"
        "#define BONUS 0\n"
        "#endif\n"
        "int main() {\n"
        "    int x = 0;\n"
        "    if (!(std::cin >> x)) return 1;\n"
        "    std::vector<char> buf(2 * 1024 * 1024, 1);\n"
        "    int adj = (buf[0] == 1) ? BONUS : 0;\n"
        '    std::cout << (x + adj) << "\\n";\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    res_ac = run_cli(["-p", "cp", "-t", "-M", "--test-dir", "tests", "solution.cpp"])
    assert res_ac.returncode == 0, _combined(res_ac)
    out_ac = strip_ansi(res_ac.stdout)
    assert out_ac.count("[ COMPILE ]") == 1
    assert out_ac.count("[ RUN ]") == 3
    assert "Passed: 3/3 (100.0%)" in out_ac
    assert out_ac.index("1.out") < out_ac.index("2.ans") < out_ac.index("10.out")
    assert re.search(r"Took\s+\d+\.\d{3}s", out_ac), out_ac
    assert re.search(r"Peak Memory:\s+\d+\.\d+\s+(?:KB|MB)", out_ac), out_ac
    bin_name = "solution.exe" if os.name == "nt" else "solution.out"
    assert not (work_dir / bin_name).exists()


def test_s02_multimodule_c_library_autolink_exclusions(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """S02: Multi-module C library with -L -j 2 --out-dir --keep and [core] exclusions."""
    (work_dir / "Run.toml").write_text(
        "[core]\n"
        'exclude_dirs = ["mocks"]\n'
        'exclude_files = ["scratch.c"]\n'
        'exclude_extensions = [".bak", ".cc"]\n\n'
        "[[languages]]\n"
        'name = "bak_c"\n'
        'extensions = [".bak"]\n'
        'compile = "cc -x c {files} -o {out}"\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )

    math_dir = work_dir / "lib" / "math"
    math_dir.mkdir(parents=True)
    (math_dir / "vec.h").write_text(
        "#ifndef VEC_H\n#define VEC_H\nint vec_dot(int ax, int ay, int bx, int by);\n#endif\n",
        encoding="utf-8",
    )
    (math_dir / "vec.c").write_text(
        '#include "vec.h"\n'
        "int vec_dot(int ax, int ay, int bx, int by) { return ax * bx + ay * by; }\n",
        encoding="utf-8",
    )
    (work_dir / "lib" / "fmt.h").write_text(
        "#ifndef FMT_H\n#define FMT_H\nvoid fmt_result(int v);\n#endif\n",
        encoding="utf-8",
    )
    (work_dir / "lib" / "fmt.c").write_text(
        "#include <stdio.h>\n"
        '#include "fmt.h"\n'
        'void fmt_result(int v) { printf("DOT_RES=%d\\n", v); }\n',
        encoding="utf-8",
    )
    (work_dir / "app.c").write_text(
        '#include "lib/math/vec.h"\n'
        '#include "lib/fmt.h"\n'
        "int main(void) {\n"
        "    int r = vec_dot(3, 4, 2, 5);\n"
        "    fmt_result(r);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    # Poison files that must be excluded by [core].
    mocks = work_dir / "mocks"
    mocks.mkdir()
    (mocks / "fake.c").write_text("THIS IS NOT VALID C SYNTAX @@@;\n", encoding="utf-8")
    (work_dir / "scratch.c").write_text("BROKEN SCRATCH @@@;\n", encoding="utf-8")
    (work_dir / "broken.bak").write_text("BROKEN BAK @@@;\n", encoding="utf-8")
    (work_dir / "legacy.cc").write_text("BROKEN CC @@@;\n", encoding="utf-8")

    res = run_cli(["-L", "-j", "2", "--out-dir", "dist", "--keep"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "Compiling 3 files using 2 threads" in out
    assert "DOT_RES=26" in res.stdout

    kept_name = "app.exe" if os.name == "nt" else "app.out"
    kept_bin = work_dir / "dist" / kept_name
    assert kept_bin.is_file()

    direct = subprocess.run(
        [str(kept_bin)], capture_output=True, text=True, check=False, timeout=10.0
    )
    assert direct.returncode == 0, direct.stderr
    assert "DOT_RES=26" in direct.stdout
    assert list(work_dir.rglob("*.o")) == []


def test_s03_executable_c_shebang_cli_with_header_cache(
    work_dir: Path,
    isolated_env: dict[str, Any],
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """S03: Extensionless C shebang CLI tool with header dependency cache invalidation."""
    cfg_h = work_dir / "config.h"
    cfg_h.write_text("#define TAG_VER 1\n", encoding="utf-8")
    mytool = work_dir / "mytool"
    mytool.write_text(
        "#!/usr/bin/env run\n"
        "// flags: -DTOOL_CODE=77\n"
        "#include <stdio.h>\n"
        '#include "config.h"\n'
        "int main(int argc, char **argv) {\n"
        '    printf("tool:%d:v%d:%s\\n", TOOL_CODE, TAG_VER, argc > 1 ? argv[1] : "");\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    res1 = run_cli(["mytool", "first_arg"])
    assert res1.returncode == 0, _combined(res1)
    out1 = strip_ansi(res1.stdout)
    assert "tool:77:v1:first_arg" in out1
    assert "[ COMPILE ]" in out1
    assert (isolated_env["cache_home"] / "run").exists()
    assert not (isolated_env["cache_home"] / "run_kuranne").exists()
    assert not (work_dir / "mytool.out").exists()
    assert not (work_dir / "mytool.exe").exists()

    res2 = run_cli(["mytool", "second_arg"])
    assert res2.returncode == 0, _combined(res2)
    out2 = strip_ansi(res2.stdout)
    assert "tool:77:v1:second_arg" in out2
    assert "[ INFO ] Using cached binary:" in out2
    assert "[ COMPILE ]" not in out2

    cfg_h.write_text("#define TAG_VER 2\n", encoding="utf-8")
    res3 = run_cli(["mytool", "third_arg"])
    assert res3.returncode == 0, _combined(res3)
    out3 = strip_ansi(res3.stdout)
    assert "tool:77:v2:third_arg" in out3
    assert "[ COMPILE ]" in out3


def test_s04_project_bootstrap_init_new_tasks_dotenv(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """S04: --init -> Run.toml templates/tasks + .env -> --new -> compound task execution."""
    (work_dir / "seed.py").write_text('print("seed")\n', encoding="utf-8")

    res_init = run_cli(["--init"])
    assert res_init.returncode == 0, _combined(res_init)
    assert "[ INIT ]" in strip_ansi(res_init.stdout)

    run_toml = work_dir / "Run.toml"
    assert run_toml.is_file()
    init_text = run_toml.read_text(encoding="utf-8")
    assert "[core]" in init_text
    for legacy in ("[runner]", "[preset]", "[[language]]", "[exclude]"):
        assert legacy not in init_text

    deploy_cmd = (
        'deploy = "python3 -c \\"print(\'ENV:${env:DEPLOY_TARGET}\')\\" '
        '&& python3 worker.py"\n'
    )
    if "[tasks]\n" in init_text:
        updated = init_text.replace("[tasks]\n", f"[tasks]\n{deploy_cmd}", 1)
    else:
        updated = init_text + f"\n[tasks]\n{deploy_cmd}"
    updated += (
        "\n[templates.worker]\n"
        'content = """import os, sys\n'
        'print("WORKER:{{name}}:" + os.environ.get("API_SECRET", "") + ":" + "|".join(sys.argv[1:]))\n'
        '"""\n'
    )
    run_toml.write_text(updated, encoding="utf-8")

    (work_dir / ".env").write_text(
        "DEPLOY_TARGET=prod_eu\nAPI_SECRET=tok_42\n",
        encoding="utf-8",
    )

    res_new = run_cli(["--new", "worker.py", "--template", "worker"])
    assert res_new.returncode == 0, _combined(res_new)
    assert "[ CREATE ]" in strip_ansi(res_new.stdout)
    worker_py = work_dir / "worker.py"
    assert worker_py.is_file()
    worker_src = worker_py.read_text(encoding="utf-8")
    assert "{{" not in worker_src
    assert "worker" in worker_src

    res_task = run_cli(["deploy", "arg_ok"])
    assert res_task.returncode == 0, _combined(res_task)
    out_task = strip_ansi(res_task.stdout)
    assert "[ TASK ]" in out_task
    i_env = out_task.index("ENV:prod_eu")
    i_worker = out_task.index("WORKER:worker:tok_42:arg_ok")
    assert i_env < i_worker


def test_s05_polyglot_custom_languages_expect_no_color(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """S05: Polyglot workspace with custom [[languages]], --expect diff/pass, and --no-color."""
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "mini_dsl"\n'
        'extensions = [".dsl"]\n'
        'command = "python3 runner_dsl.py {file}"\n\n'
        "[[languages]]\n"
        'name = "xc_c"\n'
        'extensions = [".xc"]\n'
        'compile = "cc -x c {file} -o {out}"\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )
    (work_dir / "runner_dsl.py").write_text(
        "import pathlib, sys\n"
        "text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').strip()\n"
        "print(f'DSL:{text}')\n",
        encoding="utf-8",
    )
    (work_dir / "pipeline.dsl").write_text("step_alpha\n", encoding="utf-8")
    (work_dir / "native.xc").write_text(
        "#include <stdio.h>\n"
        'int main(void) { puts("XC_NATIVE_OK"); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "expected_bad.txt").write_text("DSL:wrong\n", encoding="utf-8")
    (work_dir / "expected_good.txt").write_text("DSL:step_alpha\n", encoding="utf-8")

    res_bad = run_cli(["--no-color", "--expect", "expected_bad.txt", "pipeline.dsl"])
    assert not ANSI_RE.search(res_bad.stdout)
    assert not ANSI_RE.search(res_bad.stderr)
    assert "[ FAIL ] Output mismatch with" in res_bad.stdout
    assert "--- Differences ---" in res_bad.stdout

    res_good = run_cli(["--no-color", "--expect", "expected_good.txt", "pipeline.dsl"])
    assert res_good.returncode == 0, _combined(res_good)
    assert not ANSI_RE.search(res_good.stdout)
    assert not ANSI_RE.search(res_good.stderr)
    assert "[ PASS ] Output matches" in res_good.stdout

    res_xc = run_cli(["--no-color", "native.xc"])
    assert res_xc.returncode == 0, _combined(res_xc)
    assert not ANSI_RE.search(res_xc.stdout)
    assert not ANSI_RE.search(res_xc.stderr)
    assert "XC_NATIVE_OK" in res_xc.stdout


def test_s06_security_hardened_ci_pipeline(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """S06: Blocks dangerous env/.env/flags unless --unsafe, and enforces --asan --timeout."""
    (work_dir / ".env").write_text(
        "LD_PRELOAD=/tmp/bad.so\n"
        "PYTHONPATH=/tmp/bad_py\n"
        "CI_TAG=safe_99\n",
        encoding="utf-8",
    )
    (work_dir / "inspect_ci.py").write_text(
        "import os\n"
        "for k in ('LD_PRELOAD', 'PYTHONPATH', 'DYLD_INSERT_LIBRARIES', 'CI_TAG', 'USER_TAG'):\n"
        "    print(f'{k}={os.environ.get(k, \"MISSING\")}')\n",
        encoding="utf-8",
    )
    (work_dir / "Run.toml").write_text(
        '[tasks]\nci_env = "python3 inspect_ci.py"\n',
        encoding="utf-8",
    )
    (work_dir / "app.c").write_text(
        "#include <stdio.h>\n"
        'int main(void) { puts("CI_APP_OK"); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "slow.c").write_text(
        "#include <unistd.h>\n"
        "int main(void) { sleep(30); return 0; }\n",
        encoding="utf-8",
    )

    res_env = run_cli(
        [
            "--env",
            "DYLD_INSERT_LIBRARIES=/tmp/bad.dylib",
            "--env",
            "USER_TAG=ok_1",
            "ci_env",
        ]
    )
    assert res_env.returncode == 0, _combined(res_env)
    env_out = strip_ansi(_combined(res_env))
    assert "LD_PRELOAD=MISSING" in env_out
    assert "PYTHONPATH=MISSING" in env_out
    assert "DYLD_INSERT_LIBRARIES=MISSING" in env_out
    assert "CI_TAG=safe_99" in env_out
    assert "USER_TAG=ok_1" in env_out
    assert "[ WARN ]" in env_out

    res_susp = run_cli(["--flags", "-Wl,-rpath,/tmp", "app.c"])
    susp_out = strip_ansi(_combined(res_susp))
    assert "[ WARN ]" in susp_out
    assert "suspicious" in susp_out.lower()
    assert "[ ERROR ]" in susp_out
    assert "CI_APP_OK" not in susp_out
    bin_name = "app.exe" if os.name == "nt" else "app.out"
    assert not (work_dir / bin_name).exists()

    res_unsafe = run_cli(
        ["--unsafe", "--dry-run", "--flags", "-Wl,-rpath,/tmp", "app.c"]
    )
    assert res_unsafe.returncode == 0, _combined(res_unsafe)
    assert "-Wl,-rpath,/tmp" in strip_ansi(res_unsafe.stdout)

    if sys.platform != "win32":
        res_asan = run_cli(["--asan", "--timeout", "5.0", "app.c"])
        assert res_asan.returncode == 0, _combined(res_asan)
        assert "CI_APP_OK" in res_asan.stdout

    t0 = time.monotonic()
    timeout_args = (
        ["--timeout", "1.0", "slow.c"]
        if sys.platform == "win32"
        else ["--asan", "--timeout", "1.0", "slow.c"]
    )
    res_to = run_cli(timeout_args, timeout=10.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 8.0
    to_out = strip_ansi(_combined(res_to))
    assert "[ ERROR ]" in to_out
    assert "timed out" in to_out.lower()


def test_s07_monorepo_nested_config_git_isolation(
    work_dir: Path,
    isolated_env: dict[str, Any],
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """S07: .git stops Run.toml/Makefile upward search, falling back to $XDG_CONFIG_HOME/run/Run.toml."""
    xdg_run = isolated_env["config_home"] / "run"
    xdg_run.mkdir(parents=True, exist_ok=True)
    (xdg_run / "Run.toml").write_text(
        '[tasks]\nfallback_task = "python3 -c \\"print(\'FROM_GLOBAL_XDG\')\\""\n',
        encoding="utf-8",
    )

    monorepo = work_dir / "monorepo"
    monorepo.mkdir()
    (monorepo / "Run.toml").write_text(
        '[tasks]\nouter_task = "python3 -c \\"print(\'FROM_OUTER_MONO\')\\""\n',
        encoding="utf-8",
    )
    (monorepo / "Makefile").write_text(
        "all:\n\t@echo OUTER_MAKE_OK\n",
        encoding="utf-8",
    )

    svc_sub = monorepo / "packages" / "svc" / "sub"
    (monorepo / "packages" / "svc" / ".git").mkdir(parents=True)
    svc_sub.mkdir(parents=True)

    svc_b = monorepo / "packages" / "svc_b"
    (svc_b / ".git").mkdir(parents=True)
    (svc_b / "Run.toml").write_text(
        '[tasks]\nouter_task = "python3 -c \\"print(\'FROM_SVC_B\')\\""\n',
        encoding="utf-8",
    )

    res_global = run_cli(
        ["--directory", "packages/svc/sub", "fallback_task"], cwd=monorepo
    )
    assert res_global.returncode == 0, _combined(res_global)
    assert "FROM_GLOBAL_XDG" in res_global.stdout

    res_blocked = run_cli(
        ["--directory", "packages/svc/sub", "outer_task"], cwd=monorepo
    )
    assert "FROM_OUTER_MONO" not in _combined(res_blocked)

    res_no_make = run_cli(
        ["--directory", "packages/svc/sub"], cwd=monorepo, stdin_text=""
    )
    assert "OUTER_MAKE_OK" not in res_no_make.stdout
    assert "[ INPUT ]" in strip_ansi(res_no_make.stdout)

    res_outer_make = run_cli(["--directory", "."], cwd=monorepo)
    assert res_outer_make.returncode == 0, _combined(res_outer_make)
    assert "Detected project 'make'" in strip_ansi(res_outer_make.stdout)
    assert "OUTER_MAKE_OK" in res_outer_make.stdout

    res_svc_b = run_cli(["--directory", "packages/svc_b", "outer_task"], cwd=monorepo)
    assert res_svc_b.returncode == 0, _combined(res_svc_b)
    assert "FROM_SVC_B" in res_svc_b.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT/select on pipes not supported on Windows")
def test_s08_live_watch_mode_reload_and_sigint(
    work_dir: Path, spawn_cli: Callable[..., subprocess.Popen[str]]
) -> None:
    """S08: -w runs initial build, detects header edit to reload, and exits 0 on SIGINT."""
    hdr = work_dir / "hdr.h"
    hdr.write_text('#define MSG "WATCH_V1"\n', encoding="utf-8")
    (work_dir / "live.c").write_text(
        "#include <stdio.h>\n"
        '#include "hdr.h"\n'
        'int main(void) { printf("%s\\n", MSG); fflush(stdout); return 0; }\n',
        encoding="utf-8",
    )

    proc = spawn_cli(["-w", "live.c"])
    first_chunk = _read_until(proc, "WATCH_V1", timeout=10.0)
    first_clean = strip_ansi(first_chunk)
    assert "Watch mode enabled" in first_clean, first_clean
    assert "WATCH_V1" in first_clean, first_clean

    time.sleep(0.15)
    old_ns = hdr.stat().st_mtime_ns
    hdr.write_text('#define MSG "WATCH_V2"\n', encoding="utf-8")
    if hdr.stat().st_mtime_ns <= old_ns:
        bumped = old_ns + 2_000_000_000
        os.utime(hdr, ns=(bumped, bumped))

    second_chunk = _read_until(proc, "WATCH_V2", timeout=10.0)
    second_clean = strip_ansi(second_chunk)
    assert "Restarting..." in second_clean, second_clean
    assert "WATCH_V2" in second_clean, second_clean

    proc.send_signal(signal.SIGINT)
    rc = proc.wait(timeout=5.0)
    assert rc == 0


def test_s09_optional_cargo_and_rustc_cache_lifecycle(
    work_dir: Path,
    isolated_env: dict[str, Any],
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """S09 (optional): Zero-dep Cargo project dry-run/run + standalone rustc cache & --clean."""
    require_optional_tool("cargo")
    require_optional_tool("rustc")

    cargo_dir = work_dir / "demo_crate"
    src_dir = cargo_dir / "src"
    src_dir.mkdir(parents=True)
    (cargo_dir / "Cargo.toml").write_text(
        '[package]\nname = "demo_crate"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (src_dir / "main.rs").write_text(
        "fn main() {\n"
        '    let arg = std::env::args().nth(1).unwrap_or_else(|| "none".to_string());\n'
        '    println!("CARGO_OK:{}", arg);\n'
        "}\n",
        encoding="utf-8",
    )

    res_dry = run_cli(["--dry-run"], cwd=cargo_dir)
    assert res_dry.returncode == 0, _combined(res_dry)
    dry_out = strip_ansi(res_dry.stdout)
    assert "Detected project 'cargo' via Cargo.toml" in dry_out
    assert "cargo run -q" in dry_out

    res_cargo = run_cli(["src/main.rs", "--", "arg_9"], cwd=cargo_dir)
    assert res_cargo.returncode == 0, _combined(res_cargo)
    assert "CARGO_OK:arg_9" in res_cargo.stdout

    solo_dir = work_dir / "solo"
    solo_dir.mkdir()
    (solo_dir / "tool.rs").write_text(
        'fn main() { println!("RUSTC_SOLO_OK"); }\n',
        encoding="utf-8",
    )

    res_solo1 = run_cli(["tool.rs"], cwd=solo_dir)
    assert res_solo1.returncode == 0, _combined(res_solo1)
    assert "RUSTC_SOLO_OK" in res_solo1.stdout

    res_solo2 = run_cli(["tool.rs"], cwd=solo_dir)
    assert res_solo2.returncode == 0, _combined(res_solo2)
    assert "RUSTC_SOLO_OK" in res_solo2.stdout
    assert "[ INFO ] Using cached binary:" in strip_ansi(res_solo2.stdout)

    res_clean = run_cli(["--clean"], cwd=solo_dir)
    assert res_clean.returncode == 0, _combined(res_clean)
    assert "[ CLEAN ] Build cache cleared successfully." in strip_ansi(res_clean.stdout)
    cache_root = isolated_env["cache_home"] / "run"
    assert not cache_root.exists() or not any(p.is_file() for p in cache_root.rglob("*"))
