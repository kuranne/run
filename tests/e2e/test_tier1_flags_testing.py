"""Tier 1 E2E tests: F04 (Tasks/Projects), F05 (Stdin/Args/Expect/Preprocessor), F06 (Batch testcases/Metrics), F07 (Presets/Outputs/Custom langs), F08 (Sanitizers/Debuggers/Timeout)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import pytest

from tests.e2e.conftest import has_tool, is_pid_alive, require_optional_tool, strip_ansi

pytestmark = pytest.mark.tier1


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


# ---------------------------------------------------------------------------
# F04: Tasks ([tasks]) & Projects ([projects])
# ---------------------------------------------------------------------------


def test_f04_task_string_execution(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[tasks]\ngreet = "python3 -c \\"print(\'task_str_ok\')\\""\n',
        encoding="utf-8",
    )
    res = run_cli(["greet"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "[ TASK ]" in out
    assert "task_str_ok" in out


def test_f04_task_table_and_arg_forwarding(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks.echo_args]\n"
        'command = "python3 -c \\"import sys; print(\'|\'.join(sys.argv[1:]))\\""\n',
        encoding="utf-8",
    )
    res = run_cli(["echo_args", "one", "two"])
    assert res.returncode == 0, _combined(res)
    assert "one|two" in res.stdout


def test_f04_task_compound_and_semicolon_commands(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        'chain = "python3 -c \\"print(\'step1\')\\" && '
        'python3 -c \\"print(\'step2\')\\"; '
        'python3 -c \\"print(\'step3\')\\""\n',
        encoding="utf-8",
    )
    res = run_cli(["chain"])
    assert res.returncode == 0, _combined(res)
    i1 = res.stdout.index("step1")
    i2 = res.stdout.index("step2")
    i3 = res.stdout.index("step3")
    assert i1 < i2 < i3


def test_f04_project_custom_manifest_two_step_build_run(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[projects.custom]\n"
        'file = "app.manifest"\n'
        'build = "python3 -c \\"print(\'proj_built\')\\""\n'
        'run = "python3 -c \\"print(\'proj_ran\')\\""\n',
        encoding="utf-8",
    )
    (work_dir / "app.manifest").write_text("marker\n", encoding="utf-8")
    res = run_cli([])
    assert res.returncode == 0, _combined(res)
    assert "Detected project 'custom'" in res.stdout
    assert "proj_built" in res.stdout
    assert "proj_ran" in res.stdout


def test_f04_project_builtin_make_and_git_boundary(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    if not has_tool("make"):
        pytest.skip("make is not installed on PATH")
    outer = work_dir / "outer"
    outer.mkdir()
    (outer / "Makefile").write_text("all:\n\t@echo make_detected_ok\n", encoding="utf-8")

    res_make = run_cli([], cwd=outer)
    assert res_make.returncode == 0, _combined(res_make)
    assert "Detected project 'make'" in res_make.stdout
    assert "make_detected_ok" in res_make.stdout

    # .git inside repo must stop upward manifest traversal to outer/Makefile.
    repo_sub = outer / "repo" / "sub"
    (outer / "repo" / ".git").mkdir(parents=True)
    repo_sub.mkdir(parents=True)
    res_stop = run_cli([], cwd=repo_sub, stdin_text="")
    assert res_stop.returncode == 0, _combined(res_stop)
    stop_out = strip_ansi(res_stop.stdout)
    assert "make_detected_ok" not in stop_out
    assert "[ INPUT ]" in stop_out


# ---------------------------------------------------------------------------
# F05: Stdin, Args, Expect & Preprocessor
# ---------------------------------------------------------------------------


def test_f05_stdin_from_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "stdin_data.txt").write_text("file_stdin_line\n", encoding="utf-8")
    (work_dir / "reader.py").write_text(
        'import sys\nprint("GOT:" + sys.stdin.read().strip())\n',
        encoding="utf-8",
    )
    res = run_cli(["-i", "stdin_data.txt", "reader.py"])
    assert res.returncode == 0, _combined(res)
    assert "GOT:file_stdin_line" in res.stdout


def test_f05_stdin_piped_dash(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "reader.py").write_text(
        'import sys\nprint("PIPED:" + sys.stdin.read().strip())\n',
        encoding="utf-8",
    )
    res = run_cli(["reader.py", "-i", "-"], stdin_text="piped_payload\n")
    assert res.returncode == 0, _combined(res)
    assert "PIPED:piped_payload" in res.stdout


def test_f05_argument_and_double_dash_merge(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "show_args.py").write_text(
        'import sys\nprint("ARGS:" + "|".join(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    res = run_cli(["--argument", "--alpha 1", "show_args.py", "--", "--beta", "2"])
    assert res.returncode == 0, _combined(res)
    assert "ARGS:--alpha|1|--beta|2" in res.stdout


def test_f05_expect_match_and_mismatch_diff(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "prog.py").write_text('print("hello")\nprint("world")\n', encoding="utf-8")
    (work_dir / "good.txt").write_text("hello\nworld\n", encoding="utf-8")
    (work_dir / "bad.txt").write_text("hello\nearth\n", encoding="utf-8")

    ok_res = run_cli(["prog.py", "--expect", "good.txt"])
    assert ok_res.returncode == 0, _combined(ok_res)
    assert "[ PASS ] Output matches" in strip_ansi(ok_res.stdout)

    fail_res = run_cli(["prog.py", "--expect", "bad.txt"])
    fail_out = strip_ansi(fail_res.stdout)
    assert "[ FAIL ] Output mismatch with" in fail_out
    assert "--- Differences ---" in fail_out


def test_f05_preprocessor_i_and_L_before_source_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "input.c").write_text("raw_input_c_text\n", encoding="utf-8")
    (work_dir / "sol.py").write_text(
        'import sys\nprint("STDIN:" + sys.stdin.read().strip())\n',
        encoding="utf-8",
    )
    res_i = run_cli(["-i", "input.c", "sol.py"])
    assert res_i.returncode == 0, _combined(res_i)
    assert "STDIN:raw_input_c_text" in res_i.stdout

    (work_dir / "main.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("L_MAIN_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res_l = run_cli(["-L", "main.c"])
    assert res_l.returncode == 0, _combined(res_l)
    assert "L_MAIN_OK" in res_l.stdout


def test_f05_preprocessor_completion_before_source_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    res = run_cli(["--completion", "main.c"])
    assert res.returncode == 0, _combined(res)
    assert "Shell Autocompletion Setup" in res.stdout
    assert "Unsupported shell" not in _combined(res)


def test_f05_preprocessor_flags_and_argument_dash_values(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "opt.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("OPT_C_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res_flags = run_cli(["opt.c", "--flags", "-O2"])
    assert res_flags.returncode == 0, _combined(res_flags)
    assert "OPT_C_OK" in res_flags.stdout

    (work_dir / "echo_arg.py").write_text(
        'import sys\nprint("ARG0:" + sys.argv[1])\n',
        encoding="utf-8",
    )
    res_arg = run_cli(["echo_arg.py", "--argument", "--debug"])
    assert res_arg.returncode == 0, _combined(res_arg)
    assert "ARG0:--debug" in res_arg.stdout


# ---------------------------------------------------------------------------
# F06: Batch Testcases (--test-dir) & Metrics
# ---------------------------------------------------------------------------


def test_f06_test_dir_all_pass_summary(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "double.c").write_text(
        '#include <stdio.h>\nint main(void) { int x = 0; scanf("%d", &x); printf("%d\\n", x * 2); return 0; }\n',
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("5\n", encoding="utf-8")
    (cases / "1.out").write_text("10\n", encoding="utf-8")
    (cases / "2.in").write_text("21\n", encoding="utf-8")
    (cases / "2.out").write_text("42\n", encoding="utf-8")

    res = run_cli(["double.c", "--test-dir", "cases"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "=== Running Test Suite (2 cases) ===" in out
    assert "[ PASS ]" in out
    assert "=== Test Summary ===" in out
    assert "Passed: 2/2 (100.0%)" in out


def test_f06_test_dir_failure_exit_code_1(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "echo.py").write_text(
        "import sys\nprint(sys.stdin.read().strip())\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "1.in").write_text("ok\n", encoding="utf-8")
    (cases / "1.out").write_text("ok\n", encoding="utf-8")
    (cases / "2.in").write_text("actual\n", encoding="utf-8")
    (cases / "2.out").write_text("expected\n", encoding="utf-8")

    res = run_cli(["echo.py", "--test-dir", "cases"])
    assert res.returncode == 1
    out = strip_ansi(res.stdout)
    assert "[ PASS ]" in out
    assert "[ FAIL ]" in out
    assert "Passed: 1/2 (50.0%)" in out


def test_f06_test_dir_natural_sort_order(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "echo.py").write_text(
        "import sys\nprint(sys.stdin.read().strip())\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    for n in ("1", "2", "10"):
        (cases / f"{n}.in").write_text(f"val_{n}\n", encoding="utf-8")
        (cases / f"{n}.out").write_text(f"val_{n}\n", encoding="utf-8")

    res = run_cli(["echo.py", "--test-dir", "cases"])
    assert res.returncode == 0, _combined(res)
    p1 = res.stdout.index("1.out")
    p2 = res.stdout.index("2.out")
    p10 = res.stdout.index("10.out")
    assert p1 < p2 < p10


def test_f06_test_dir_pairing_variants(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "echo.py").write_text(
        "import sys\nprint(sys.stdin.read().strip())\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "case1.in").write_text("a\n", encoding="utf-8")
    (cases / "case1.ans").write_text("a\n", encoding="utf-8")
    (cases / "in_alpha.txt").write_text("b\n", encoding="utf-8")
    (cases / "out_alpha.txt").write_text("b\n", encoding="utf-8")
    (cases / "input2.txt").write_text("c\n", encoding="utf-8")
    (cases / "output2.txt").write_text("c\n", encoding="utf-8")

    res = run_cli(["echo.py", "--test-dir", "cases"])
    assert res.returncode == 0, _combined(res)
    assert "Discovered 3 test case(s)" in res.stdout
    assert "Passed: 3/3 (100.0%)" in res.stdout


def test_f06_time_metric_flag(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "quick.py").write_text('print("timed_ok")\n', encoding="utf-8")
    res = run_cli(["-t", "quick.py"])
    assert res.returncode == 0, _combined(res)
    assert "timed_ok" in res.stdout
    assert re.search(r"Took \d+\.\d{3}s", res.stdout)


def test_f06_mem_metric_flag(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "mem.py").write_text(
        'buf = bytearray(2 * 1024 * 1024)\nprint("mem_ok", len(buf))\n',
        encoding="utf-8",
    )
    res = run_cli(["-M", "mem.py"])
    assert res.returncode == 0, _combined(res)
    assert "mem_ok" in res.stdout
    assert re.search(r"Peak Memory:\s+\d+\.\d+\s+(?:KB|MB)", res.stdout)


# ---------------------------------------------------------------------------
# F07: Compiler Opts, Presets, Outputs & Custom Languages
# ---------------------------------------------------------------------------


def test_f07_preset_flags_applied(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[presets.opt]\nc = ["-DMY_PRESET_VAL=99"]\n',
        encoding="utf-8",
    )
    (work_dir / "preset.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("pval=%d\\n", MY_PRESET_VAL); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["preset.c", "-p", "opt"])
    assert res.returncode == 0, _combined(res)
    assert "pval=99" in res.stdout


def test_f07_flags_and_compiler_override(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    if not has_tool("clang"):
        pytest.skip("clang is not installed on PATH")
    (work_dir / "comp.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("macro=%d\\n", MACRO_X); return 0; }\n',
        encoding="utf-8",
    )
    dry = run_cli(["--dry-run", "--compiler", "clang", "--flags", "-DMACRO_X=123", "comp.c"])
    assert dry.returncode == 0, _combined(dry)
    assert "clang" in dry.stdout
    assert "-DMACRO_X=123" in dry.stdout

    live = run_cli(["--compiler", "clang", "--flags", "-DMACRO_X=123", "comp.c"])
    assert live.returncode == 0, _combined(live)
    assert "macro=123" in live.stdout


def test_f07_build_only_flag(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "buildonly.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("SHOULD_NOT_RUN\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["-B", "buildonly.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "[ BUILD ]" in out
    assert "SHOULD_NOT_RUN" not in out
    expected_bin = work_dir / ("buildonly.exe" if os.name == "nt" else "buildonly.out")
    assert expected_bin.exists()


def test_f07_out_dir_and_keep(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "kept.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("kept_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["--out-dir", "dist", "--keep", "kept.c"])
    assert res.returncode == 0, _combined(res)
    assert "kept_ok" in res.stdout
    expected_bin = work_dir / "dist" / ("kept.exe" if os.name == "nt" else "kept.out")
    assert expected_bin.exists()


def test_f07_custom_language_compile_and_run_templates(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    if not has_tool("cc"):
        pytest.skip("cc is not installed on PATH")
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "wrapped_c"\n'
        'extensions = [".myext"]\n'
        'compile = "cc -x c {file} -o {out}"\n'
        'run = "python3 -c \\"import os,subprocess,sys; print(\'WRAPPED:\' + subprocess.check_output([os.path.abspath(sys.argv[1])], text=True).strip())\\" ${out}"\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )
    (work_dir / "demo.myext").write_text(
        '#include <stdio.h>\nint main(void) { printf("custom_c_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["demo.myext"])
    assert res.returncode == 0, _combined(res)
    assert "WRAPPED:custom_c_ok" in res.stdout


def test_f07_custom_language_priority_over_builtin_and_runner_mode(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "custom_py"\n'
        'extensions = [".py"]\n'
        'runner = "python3"\n'
        'type = "interpreter"\n'
        'arguments = ["--from-custom-runner"]\n',
        encoding="utf-8",
    )
    (work_dir / "override.py").write_text(
        'import sys\nprint("PY_ARGS:" + "|".join(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    res = run_cli(["override.py"])
    assert res.returncode == 0, _combined(res)
    assert "PY_ARGS:--from-custom-runner" in res.stdout


# ---------------------------------------------------------------------------
# F08: Sanitizers, Debuggers & Timeout
# ---------------------------------------------------------------------------


def test_f08_asan_dry_run_and_exec(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "clean.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("asan_clean_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    dry = run_cli(["--asan", "--dry-run", "clean.c"])
    assert dry.returncode == 0, _combined(dry)
    assert "-fsanitize=address,undefined" in dry.stdout
    assert "-fno-omit-frame-pointer" in dry.stdout

    if sys.platform != "win32":
        live = run_cli(["--asan", "clean.c"])
        assert live.returncode == 0, _combined(live)
        assert "asan_clean_ok" in live.stdout


def test_f08_tsan_and_sanitize_dry_run(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    tsan = run_cli(["--tsan", "--dry-run", "app.c"])
    assert tsan.returncode == 0, _combined(tsan)
    assert "-fsanitize=thread" in tsan.stdout

    ubsan = run_cli(["--sanitize", "undefined", "--dry-run", "app.c"])
    assert ubsan.returncode == 0, _combined(ubsan)
    assert "-fsanitize=undefined" in ubsan.stdout


def test_f08_timeout_kills_runaway_process_and_grandchild(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    pid_file = work_dir / "grandchild.pid"
    (work_dir / "runaway.py").write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        'p = subprocess.Popen([sys.executable, "-S", "-c", "import time; time.sleep(30)"])\n'
        f"Path({str(pid_file)!r}).write_text(str(p.pid), encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    start = time.monotonic()
    res = run_cli(["--timeout", "1.0", "runaway.py"], timeout=10.0)
    elapsed = time.monotonic() - start
    assert elapsed < 8.0
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "timed out" in combined.lower()

    assert pid_file.exists(), "runaway.py did not write grandchild.pid before timeout fired"
    grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
    # Give OS a tiny moment to reap if needed, then verify grandchild is gone.
    time.sleep(0.2)
    assert not is_pid_alive(grandchild_pid)


def test_f08_debug_and_lldb_dry_run(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("node")
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (work_dir / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (work_dir / "app.js").write_text("console.log('hi');\n", encoding="utf-8")

    lldb_dry = run_cli(["--lldb", "--dry-run", "app.c"])
    assert lldb_dry.returncode == 0, _combined(lldb_dry)
    assert "lldb --" in lldb_dry.stdout

    if sys.platform == "darwin":
        c_dbg = run_cli(["--debug", "--dry-run", "app.c"])
        assert c_dbg.returncode == 0, _combined(c_dbg)
        assert "lldb --" in c_dbg.stdout

    py_dbg = run_cli(["--debug", "--dry-run", "app.py"])
    assert py_dbg.returncode == 0, _combined(py_dbg)
    assert "-m pdb --" in py_dbg.stdout

    js_dbg = run_cli(["--debug", "--dry-run", "app.js"])
    assert js_dbg.returncode == 0, _combined(js_dbg)
    assert "--inspect-brk --" in js_dbg.stdout


def test_f08_gdb_and_valgrind_dry_run(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    gdb_dry = run_cli(["--gdb", "--dry-run", "app.c"])
    assert gdb_dry.returncode == 0, _combined(gdb_dry)
    assert "gdb --args" in gdb_dry.stdout

    vg_dry = run_cli(["--valgrind", "--dry-run", "app.c"])
    assert vg_dry.returncode == 0, _combined(vg_dry)
    assert "valgrind --leak-check=full --track-origins=yes" in vg_dry.stdout
