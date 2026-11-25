"""Tier 3 E2E tests: pairwise multi-feature CLI combinations across F01-F12."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.e2e.conftest import ANSI_RE, has_tool, strip_ansi

pytestmark = pytest.mark.tier3


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


def test_t3_01_multi_preset_flags_argument_and_trailing_separator(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """-m + -p <preset> + --flags + --argument + -- merges compile flags and ordered argv."""
    (work_dir / "Run.toml").write_text(
        '[presets.combo]\nc = ["-DPRESET_VAL=40"]\n',
        encoding="utf-8",
    )
    (work_dir / "helper.c").write_text(
        "int get_bonus(void) { return 2; }\n",
        encoding="utf-8",
    )
    (work_dir / "main.c").write_text(
        "#include <stdio.h>\n"
        "int get_bonus(void);\n"
        "#ifndef PRESET_VAL\n"
        "#define PRESET_VAL 0\n"
        "#endif\n"
        "#ifndef EXTRA_VAL\n"
        "#define EXTRA_VAL 0\n"
        "#endif\n"
        "int main(int argc, char **argv) {\n"
        '    printf("sum=%d\\n", PRESET_VAL + EXTRA_VAL + get_bonus());\n'
        '    printf("args=");'
        "    for (int i = 1; i < argc; ++i) {\n"
        '        printf("%s%s", i > 1 ? "|" : "", argv[i]);\n'
        "    }\n"
        '    printf("\\n");\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    res = run_cli(
        [
            "-m",
            "-p",
            "combo",
            "--flags",
            "-DEXTRA_VAL=10",
            "--argument",
            "alpha beta",
            "main.c",
            "helper.c",
            "--",
            "gamma",
            "--debug",
        ]
    )
    assert res.returncode == 0, _combined(res)
    assert "sum=52" in res.stdout
    assert "args=alpha|beta|gamma|--debug" in res.stdout


def test_t3_02_autolink_test_dir_and_memory_metrics(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """-L + --test-dir + -M discovers subdir C sources, compiles once, and reports memory."""
    sub = work_dir / "sub"
    sub.mkdir()
    (sub / "math_ops.h").write_text(
        "#ifndef MATH_OPS_H\n#define MATH_OPS_H\nint triple_plus_one(int x);\n#endif\n",
        encoding="utf-8",
    )
    (sub / "math_ops.c").write_text(
        '#include "math_ops.h"\nint triple_plus_one(int x) { return x * 3 + 1; }\n',
        encoding="utf-8",
    )
    (work_dir / "main.c").write_text(
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "#include <string.h>\n"
        '#include "sub/math_ops.h"\n'
        "int main(void) {\n"
        "    int x = 0;\n"
        '    if (scanf("%d", &x) != 1) return 1;\n'
        "    size_t sz = 2u * 1024u * 1024u;\n"
        "    volatile char *buf = (volatile char *)malloc(sz);\n"
        "    if (!buf) return 2;\n"
        "    for (size_t i = 0; i < sz; i += 4096) { buf[i] = 1; }\n"
        "    int adj = (buf[0] == 1) ? 0 : 999;\n"
        "    free((void *)buf);\n"
        '    printf("%d\\n", triple_plus_one(x) + adj);\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("4\n", encoding="utf-8")
    (cases / "1.out").write_text("13\n", encoding="utf-8")
    (cases / "2.in").write_text("10\n", encoding="utf-8")
    (cases / "2.ans").write_text("31\n", encoding="utf-8")

    res = run_cli(["-L", "-M", "--test-dir", "cases"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "[ INFO ] Auto-found" in out
    assert "=== Running Test Suite (2 cases) ===" in out
    assert out.count("[ RUN ]") == 2
    assert out.count("[ PASS ]") == 2
    assert re.search(r"Peak Memory:\s+\d+\.\d+\s+(?:KB|MB)", out), out
    assert "=== Test Summary ===" in out
    assert "Passed: 2/2 (100.0%)" in out


def test_t3_03_stdin_pipe_expect_time_and_memory(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """-i - + --expect + -t + -M buffers piped stdin, reports metrics, and diffs mismatches."""
    (work_dir / "echo_calc.c").write_text(
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "int main(void) {\n"
        "    int n = 0;\n"
        '    if (scanf("%d", &n) != 1) return 1;\n'
        "    size_t sz = 2u * 1024u * 1024u;\n"
        "    volatile char *buf = (volatile char *)malloc(sz);\n"
        "    if (!buf) return 2;\n"
        "    for (size_t i = 0; i < sz; i += 4096) { buf[i] = 1; }\n"
        "    int adj = (buf[0] == 1) ? 0 : 999;\n"
        "    free((void *)buf);\n"
        '    printf("%d\\n", n * 2 + adj);\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (work_dir / "expected_match.txt").write_text("42\n", encoding="utf-8")
    (work_dir / "expected_diff.txt").write_text("99\n", encoding="utf-8")

    res_ok = run_cli(
        ["-i", "-", "--expect", "expected_match.txt", "-t", "-M", "echo_calc.c"],
        stdin_text="21\n",
    )
    assert res_ok.returncode == 0, _combined(res_ok)
    out_ok = strip_ansi(res_ok.stdout)
    assert "[ COMPILE ]" in out_ok
    assert "[ RUN ]" in out_ok
    assert "42" in out_ok
    assert re.search(
        r"Took\s+\d+\.\d{3}s\s*\|\s*Peak Memory:\s+\d+\.\d+\s+(?:KB|MB)", out_ok
    ), out_ok
    assert "[ PASS ] Output matches" in out_ok

    res_bad = run_cli(
        ["-i", "-", "--expect", "expected_diff.txt", "-t", "-M", "echo_calc.c"],
        stdin_text="21\n",
    )
    out_bad = strip_ansi(_combined(res_bad))
    assert "[ FAIL ] Output mismatch with" in out_bad
    assert "--- Differences ---" in out_bad
    assert re.search(
        r"Took\s+\d+\.\d{3}s\s*\|\s*Peak Memory:\s+\d+\.\d+\s+(?:KB|MB)", out_bad
    ), out_bad


def test_t3_04_c_shebang_inline_flags_trailing_args_and_xdg_cache(
    work_dir: Path,
    isolated_env: dict[str, Any],
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Extensionless C shebang + // flags: + trailing args hits $XDG_CACHE_HOME/run on run 2."""
    script = work_dir / "cli_tool"
    script.write_text(
        "#!/usr/bin/env run\n"
        "// flags: -DFLAG_CODE=7331\n"
        "#include <stdio.h>\n"
        "int main(int argc, char **argv) {\n"
        '    printf("CODE=%d:%s\\n", FLAG_CODE, argc > 1 ? argv[1] : "none");\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    res1 = run_cli(["cli_tool", "arg_one"])
    assert res1.returncode == 0, _combined(res1)
    out1 = strip_ansi(res1.stdout)
    assert "CODE=7331:arg_one" in out1
    assert "[ COMPILE ]" in out1
    assert (isolated_env["cache_home"] / "run").exists()
    assert not (isolated_env["cache_home"] / "run_kuranne").exists()
    assert not (work_dir / "cli_tool.out").exists()
    assert not (work_dir / "cli_tool.exe").exists()

    res2 = run_cli(["cli_tool", "arg_two"])
    assert res2.returncode == 0, _combined(res2)
    out2 = strip_ansi(res2.stdout)
    assert "CODE=7331:arg_two" in out2
    assert "[ INFO ] Using cached binary:" in out2
    assert "[ COMPILE ]" not in out2


def test_t3_05_dotenv_run_toml_expansion_env_override_and_dangerous_filter(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """.env feeds ${env:...} and child env, --env overrides child env, dangerous vars are blocked."""
    (work_dir / ".env").write_text(
        "APP_MODE=from_dotenv\n"
        "API_TOKEN=secret_dotenv\n"
        "PYTHONPATH=/tmp/poison_py\n"
        "LD_PRELOAD=/tmp/poison_so\n",
        encoding="utf-8",
    )
    (work_dir / "probe.py").write_text(
        "import os, sys\n"
        "print('TPL=' + sys.argv[1])\n"
        "print('BAD_TPL=' + sys.argv[2])\n"
        "print('CHILD_MODE=' + os.environ.get('APP_MODE', 'MISSING'))\n"
        "print('CHILD_TOK=' + os.environ.get('API_TOKEN', 'MISSING'))\n"
        "print('PYPATH=' + os.environ.get('PYTHONPATH', 'MISSING'))\n"
        "print('LD=' + os.environ.get('LD_PRELOAD', 'MISSING'))\n"
        "print('DYLD=' + os.environ.get('DYLD_INSERT_LIBRARIES', 'MISSING'))\n",
        encoding="utf-8",
    )
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        'probe = "python3 probe.py ${env:APP_MODE} x${env:PYTHONPATH}x"\n',
        encoding="utf-8",
    )

    res = run_cli(
        [
            "--env",
            "APP_MODE=from_flag",
            "--env",
            "DYLD_INSERT_LIBRARIES=/tmp/poison_dylib",
            "probe",
        ]
    )
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(_combined(res))
    assert "TPL=from_dotenv" in out
    assert "BAD_TPL=xx" in out
    assert "CHILD_MODE=from_flag" in out
    assert "CHILD_TOK=secret_dotenv" in out
    assert "PYPATH=MISSING" in out
    assert "LD=MISSING" in out
    assert "DYLD=MISSING" in out
    assert "[ WARN ]" in out


def test_t3_06_directory_init_and_tasks_execution(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--directory + --init scaffolds Run.toml in target dir; follow-up run executes [tasks]."""
    proj = work_dir / "sub_proj"
    proj.mkdir()
    (proj / "seed.py").write_text('print("seed")\n', encoding="utf-8")

    res_init = run_cli(["--directory", str(proj), "--init"])
    assert res_init.returncode == 0, _combined(res_init)
    assert "[ INIT ]" in strip_ansi(res_init.stdout)

    run_toml = proj / "Run.toml"
    assert run_toml.is_file()
    toml_text = run_toml.read_text(encoding="utf-8")
    for legacy in ("[runner]", "[preset]", "[[language]]", "[exclude]"):
        assert legacy not in toml_text

    if "[tasks]\n" in toml_text:
        updated_toml = toml_text.replace(
            "[tasks]\n",
            '[tasks]\ndir_probe = "python3 -c \\"print(\'DIR_TASK_OK\')\\""\n',
            1,
        )
    else:
        updated_toml = (
            toml_text
            + '\n[tasks]\ndir_probe = "python3 -c \\"print(\'DIR_TASK_OK\')\\""\n'
        )
    run_toml.write_text(updated_toml, encoding="utf-8")

    res_task = run_cli(["--directory", str(proj), "dir_probe"])
    assert res_task.returncode == 0, _combined(res_task)
    out_task = strip_ansi(res_task.stdout)
    assert "[ TASK ]" in out_task
    assert "DIR_TASK_OK" in out_task

    res_missing = run_cli(["--directory", str(work_dir / "missing_dir"), "--init"])
    assert res_missing.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(res_missing))


def test_t3_07_out_dir_build_only_keep_and_direct_execution(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--out-dir + -B + --keep emits binary without executing it, then binary runs directly."""
    (work_dir / "hello.c").write_text(
        "#include <stdio.h>\n"
        'int main(void) { puts("KEPT_BIN_OK:42"); return 0; }\n',
        encoding="utf-8",
    )
    out_sub = work_dir / "artifacts" / "bin"

    res = run_cli(["--out-dir", str(out_sub), "-B", "--keep", "hello.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "[ COMPILE ]" in out
    assert "[ BUILD ] Binary generated successfully:" in out
    assert "KEPT_BIN_OK:42" not in out

    bin_name = "hello.exe" if os.name == "nt" else "hello.out"
    bin_path = out_sub / bin_name
    assert bin_path.is_file()

    direct = subprocess.run(
        [str(bin_path)], capture_output=True, text=True, check=False, timeout=10.0
    )
    assert direct.returncode == 0, direct.stderr
    assert "KEPT_BIN_OK:42" in direct.stdout


def test_t3_08_jobs_multi_compiler_override_and_verbose(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """-j 2 + -m + --compiler + -v compiles multi-file C in parallel with explicit compiler."""
    compiler = "clang" if has_tool("clang") else "cc"
    (work_dir / "a.c").write_text(
        "#include <stdio.h>\n"
        "int fn_b(void);\n"
        "int fn_c(void);\n"
        'int main(void) { printf("MULTI_J2_OK=%d\\n", fn_b() + fn_c()); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "b.c").write_text("int fn_b(void) { return 20; }\n", encoding="utf-8")
    (work_dir / "c.c").write_text("int fn_c(void) { return 22; }\n", encoding="utf-8")

    res = run_cli(["-j", "2", "-m", "--compiler", compiler, "-v", "a.c", "b.c", "c.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(_combined(res))
    assert "[ DEBUG ] Debug logging enabled" in out
    assert "using 2 threads" in out
    assert f"[ COMPILE ] {compiler}" in out
    assert "MULTI_J2_OK=42" in res.stdout


def test_t3_09_asan_timeout_force_and_unsafe_interaction(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--asan + --timeout interacts safely with --unsafe flag gate and -f path gate."""
    (work_dir / "clean.c").write_text(
        "#include <stdio.h>\n"
        'int main(void) { puts("ASAN_CLEAN_OK"); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "slow.c").write_text(
        "#include <unistd.h>\n"
        "int main(void) { sleep(30); return 0; }\n",
        encoding="utf-8",
    )
    (work_dir / "weird;file.c").write_text(
        "#include <stdio.h>\n"
        'int main(void) { puts("FORCE_ASAN_OK"); return 0; }\n',
        encoding="utf-8",
    )

    res_blocked = run_cli(
        ["--asan", "--timeout", "5.0", "--flags", "-Wl,-rpath,/tmp", "clean.c"]
    )
    blocked_out = strip_ansi(_combined(res_blocked))
    assert "[ ERROR ]" in blocked_out or "[ WARN ]" in blocked_out
    assert "suspicious" in blocked_out.lower()
    assert "ASAN_CLEAN_OK" not in blocked_out

    res_unsafe_dry = run_cli(
        [
            "--asan",
            "--timeout",
            "5.0",
            "--unsafe",
            "--dry-run",
            "--flags",
            "-Wl,-rpath,/tmp",
            "clean.c",
        ]
    )
    assert res_unsafe_dry.returncode == 0, _combined(res_unsafe_dry)
    dry_out = strip_ansi(res_unsafe_dry.stdout)
    assert "-fsanitize=address,undefined" in dry_out
    assert "-Wl,-rpath,/tmp" in dry_out

    if sys.platform != "win32":
        res_asan = run_cli(["--asan", "--timeout", "5.0", "clean.c"])
        assert res_asan.returncode == 0, _combined(res_asan)
        assert "ASAN_CLEAN_OK" in res_asan.stdout

        res_timeout = run_cli(["--asan", "--timeout", "1.0", "slow.c"], timeout=10.0)
        to_out = strip_ansi(_combined(res_timeout))
        assert "[ ERROR ]" in to_out
        assert "timed out" in to_out.lower()
    else:
        res_timeout = run_cli(["--timeout", "1.0", "slow.c"], timeout=10.0)
        to_out = strip_ansi(_combined(res_timeout))
        assert "[ ERROR ]" in to_out
        assert "timed out" in to_out.lower()

    res_no_force = run_cli(["--asan", "--timeout", "5.0", "weird;file.c"])
    no_force_out = strip_ansi(_combined(res_no_force))
    assert "[ ERROR ]" in no_force_out
    assert "FORCE_ASAN_OK" not in no_force_out

    res_force = run_cli(["-f", "--asan", "--timeout", "5.0", "weird;file.c"])
    assert res_force.returncode == 0, _combined(res_force)
    assert "[ WARN ]" in strip_ansi(_combined(res_force))
    assert "FORCE_ASAN_OK" in res_force.stdout


def test_t3_10_custom_language_compiler_test_dir_and_no_color(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """[[languages]] custom compiler + --test-dir + --no-color produces clean summary & exit codes."""
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "my_c"\n'
        'extensions = [".myc"]\n'
        'compile = "cc -x c {file} -o {out}"\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )
    (work_dir / "prog.myc").write_text(
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int x = 0;\n"
        '    if (scanf("%d", &x) != 1) return 1;\n'
        '    printf("%d\\n", x * 3);\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("4\n", encoding="utf-8")
    (cases / "1.out").write_text("12\n", encoding="utf-8")
    (cases / "2.in").write_text("7\n", encoding="utf-8")
    (cases / "2.out").write_text("21\n", encoding="utf-8")

    res_pass = run_cli(["--no-color", "--test-dir", "cases", "prog.myc"])
    assert res_pass.returncode == 0, _combined(res_pass)
    assert not ANSI_RE.search(_combined(res_pass))
    assert "=== Running Test Suite (2 cases) ===" in res_pass.stdout
    assert res_pass.stdout.count("[ PASS ]") == 2
    assert "=== Test Summary ===" in res_pass.stdout
    assert "Passed: 2/2 (100.0%)" in res_pass.stdout

    (cases / "2.out").write_text("999\n", encoding="utf-8")
    res_fail = run_cli(["--no-color", "--test-dir", "cases", "prog.myc"])
    assert res_fail.returncode == 1, _combined(res_fail)
    assert not ANSI_RE.search(_combined(res_fail))
    assert "[ FAIL ]" in res_fail.stdout
    assert "Passed: 1/2 (50.0%)" in res_fail.stdout


def test_t3_11_projects_manifest_argument_and_dry_run(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """[projects] manifest + --argument + --dry-run prints build/run plans without executing."""
    (work_dir / "Run.toml").write_text(
        "[projects.custom_tool]\n"
        'file = "tool.manifest"\n'
        'build = "python3 -c \\"print(\'SHOULD_NOT_BUILD\')\\""\n'
        'run = "python3 -c \\"import sys; print(\'SHOULD_NOT_RUN\', sys.argv[1:])\\""\n',
        encoding="utf-8",
    )
    (work_dir / "tool.manifest").write_text("manifest\n", encoding="utf-8")

    res = run_cli(["--dry-run", "--argument", "--port 8080 --verbose"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "Detected project 'custom_tool' via tool.manifest" in out
    assert "[ DRY-RUN ] COMPILE:" in out
    run_lines = [line for line in out.splitlines() if "[ DRY-RUN ] RUN:" in line]
    assert run_lines, out
    assert "--port" in run_lines[0]
    assert "8080" in run_lines[0]
    assert "--verbose" in run_lines[0]
    non_dry_lines = [line for line in out.splitlines() if "[ DRY-RUN ]" not in line]
    assert all("SHOULD_NOT_BUILD" not in line for line in non_dry_lines)
    assert all("SHOULD_NOT_RUN" not in line for line in non_dry_lines)


def test_t3_12_new_template_from_config_and_immediate_execution(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--new + --template from Run.toml scaffolds file that executes cleanly next."""
    (work_dir / "Run.toml").write_text(
        "[templates.greeter]\n"
        'content = "print(\'TPL:{{name}}:{{filename}}\')\\n"\n',
        encoding="utf-8",
    )

    res_new = run_cli(["--new", "hello_app.py", "--template", "greeter"])
    assert res_new.returncode == 0, _combined(res_new)
    assert "[ CREATE ]" in strip_ansi(res_new.stdout)

    target = work_dir / "hello_app.py"
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "{{" not in content
    assert "hello_app" in content

    res_run = run_cli(["hello_app.py"])
    assert res_run.returncode == 0, _combined(res_run)
    assert "TPL:hello_app:hello_app.py" in res_run.stdout

    res_escape = run_cli(["--new", "../escape.py", "--template", "greeter"])
    assert res_escape.returncode == 1


def test_t3_13_no_cache_shebang_and_header_edit(
    work_dir: Path,
    isolated_env: dict[str, Any],
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """--no-cache + shebang script recompiles on every run and reflects edited #include header."""
    header = work_dir / "helper.h"
    header.write_text("#define VER_NUM 101\n", encoding="utf-8")
    script = work_dir / "app_shebang"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        '#include "helper.h"\n'
        'int main(void) { printf("HDR=%d\\n", VER_NUM); return 0; }\n',
        encoding="utf-8",
    )

    res1 = run_cli(["--no-cache", "app_shebang"])
    assert res1.returncode == 0, _combined(res1)
    out1 = strip_ansi(res1.stdout)
    assert "HDR=101" in out1
    assert "[ COMPILE ]" in out1
    assert "Using cached binary" not in out1

    cache_root = isolated_env["cache_home"] / "run"
    assert not cache_root.exists() or not any(p.is_file() for p in cache_root.rglob("*"))

    header.write_text("#define VER_NUM 202\n", encoding="utf-8")
    res2 = run_cli(["--no-cache", "app_shebang"])
    assert res2.returncode == 0, _combined(res2)
    out2 = strip_ansi(res2.stdout)
    assert "HDR=202" in out2
    assert "[ COMPILE ]" in out2
    assert "Using cached binary" not in out2


def test_t3_14_quiet_expect_mismatch_and_test_dir_failure_exit(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """-q suppresses [ RUN ]/echo on --expect mismatch while keeping diff/summary and --test-dir exit 1."""
    (work_dir / "calc.py").write_text('print("actual_payload_line")\n', encoding="utf-8")
    (work_dir / "exp.txt").write_text("expected_payload_line\n", encoding="utf-8")

    res_exp = run_cli(["-q", "--expect", "exp.txt", "calc.py"])
    out_exp = strip_ansi(_combined(res_exp))
    assert "[ RUN ]" not in out_exp
    assert "[ FAIL ] Output mismatch with" in out_exp
    assert "--- Differences ---" in out_exp

    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("", encoding="utf-8")
    (cases / "1.out").write_text("expected_payload_line\n", encoding="utf-8")

    res_td = run_cli(["-q", "--test-dir", "cases", "calc.py"])
    assert res_td.returncode == 1, _combined(res_td)
    out_td = strip_ansi(res_td.stdout)
    assert "[ RUN ]" not in out_td
    assert "[ FAIL ]" in out_td
    assert "=== Test Summary ===" in out_td
    assert "Passed: 0/1" in out_td


def test_t3_15_expect_no_color_env_and_flags(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """--expect + NO_COLOR=1 + --flags disables all ANSI codes on both match and diff."""
    (work_dir / "macro_echo.c").write_text(
        "#include <stdio.h>\n"
        "#ifndef NUM_VAL\n"
        "#define NUM_VAL 0\n"
        "#endif\n"
        'int main(void) { printf("VAL=%d\\n", NUM_VAL); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "good.txt").write_text("VAL=777\n", encoding="utf-8")
    (work_dir / "bad.txt").write_text("VAL=888\n", encoding="utf-8")

    res_ok = run_cli(
        ["--expect", "good.txt", "--flags", "-DNUM_VAL=777", "macro_echo.c"],
        env_overrides={"NO_COLOR": "1"},
    )
    assert res_ok.returncode == 0, _combined(res_ok)
    assert not ANSI_RE.search(_combined(res_ok))
    assert "[ PASS ] Output matches" in res_ok.stdout

    res_diff = run_cli(
        ["--expect", "bad.txt", "--flags", "-DNUM_VAL=777", "macro_echo.c"],
        env_overrides={"NO_COLOR": "1"},
    )
    assert not ANSI_RE.search(_combined(res_diff))
    assert "[ FAIL ] Output mismatch with" in res_diff.stdout
    assert "--- Differences ---" in res_diff.stdout
