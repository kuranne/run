"""Tier 2 E2E boundary, security, configuration, and R3 tests: F07-F12."""

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

from tests.e2e.conftest import ANSI_RE, clean_path, strip_ansi

pytestmark = pytest.mark.tier2


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


def _make_dummy_docker(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        script = bin_dir / "docker.cmd"
        script.write_text("@echo off\nexit /b 0\n", encoding="utf-8")
    else:
        script = bin_dir / "docker"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
    return script


def _read_watch_until(
    proc: subprocess.Popen[str],
    needle: str,
    timeout: float = 8.0,
) -> str:
    assert proc.stdout is not None
    assert proc.stderr is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout.fileno(), selectors.EVENT_READ, data="out")
    sel.register(proc.stderr.fileno(), selectors.EVENT_READ, data="err")
    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and sel.get_map():
            remaining = max(0.05, deadline - time.monotonic())
            events = sel.select(timeout=remaining)
            if not events:
                continue
            for key, _ in events:
                data = os.read(key.fd, 4096)
                if not data:
                    sel.unregister(key.fd)
                    continue
                chunks.append(data.decode("utf-8", errors="replace"))
            if needle in strip_ansi("".join(chunks)):
                break
    finally:
        sel.close()
    return "".join(chunks)


# ---------------------------------------------------------------------------
# F07: Compiler Opts, Presets, Outputs & Custom Languages (6 tests)
# ---------------------------------------------------------------------------


def test_f07_unknown_preset_ignored_gracefully(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[presets.known]\nc = ["-DKNOWN=1"]\n',
        encoding="utf-8",
    )
    (work_dir / "app.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("preset_fallback_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )

    dry = run_cli(["--dry-run", "-p", "nonexistent_preset", "app.c"])
    assert dry.returncode == 0, _combined(dry)
    assert "-DKNOWN" not in dry.stdout

    live = run_cli(["-p", "nonexistent_preset", "app.c"])
    assert live.returncode == 0, _combined(live)
    out = strip_ansi(_combined(live))
    assert "preset_fallback_ok" in out
    assert "[ ERROR ]" not in out


def test_f07_out_dir_nested_creation_and_cleanup_vs_keep(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "app.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("out_dir_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    nested = Path("nested/sub/bin")
    bin_name = "app.exe" if os.name == "nt" else "app.out"

    r_clean = run_cli(["--out-dir", str(nested), "app.c"])
    assert r_clean.returncode == 0, _combined(r_clean)
    assert "out_dir_ok" in r_clean.stdout
    assert (work_dir / nested).is_dir()
    assert not (work_dir / nested / bin_name).exists()

    r_keep = run_cli(["--out-dir", str(nested), "--keep", "app.c"])
    assert r_keep.returncode == 0, _combined(r_keep)
    assert "out_dir_ok" in r_keep.stdout
    assert (work_dir / nested / bin_name).exists()


@pytest.mark.parametrize("flag", ["-B", "--build-only", "--no-run"])
def test_f07_build_only_aliases_skip_nonzero_binary(
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    flag: str,
) -> None:
    (work_dir / "fail.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("RAN_SIDE_EFFECT\\n"); return 42; }\n',
        encoding="utf-8",
    )
    res = run_cli([flag, "fail.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(_combined(res))
    assert "[ BUILD ] Binary generated successfully:" in out
    assert "RAN_SIDE_EFFECT" not in out
    expected_bin = work_dir / ("fail.exe" if os.name == "nt" else "fail.out")
    assert expected_bin.exists()


def test_f07_custom_language_missing_exec_definition_rejected(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[[languages]]\nname = "badlang"\nextensions = [".bad"]\n',
        encoding="utf-8",
    )
    (work_dir / "a.bad").write_text("x\n", encoding="utf-8")

    res = run_cli(["a.bad"])
    assert res.returncode == 1
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert re.search(r"missing required.*(?:runner|compile|command)", combined, re.IGNORECASE)


def test_f07_custom_language_multi_files_with_spaces(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "sp"\n'
        'extensions = [".sp"]\n'
        'command = "python3 -c \\"import sys; print(\'FILES:\' + \'|\'.join(sys.argv[1:]))\\" ${files}"\n',
        encoding="utf-8",
    )
    d1 = work_dir / "dir one"
    d2 = work_dir / "dir two"
    d1.mkdir()
    d2.mkdir()
    (d1 / "first file.sp").write_text("1\n", encoding="utf-8")
    (d2 / "second file.sp").write_text("2\n", encoding="utf-8")

    res = run_cli(["-m", "dir one/first file.sp", "dir two/second file.sp"])
    assert res.returncode == 0, _combined(res)
    assert "FILES:dir one/first file.sp|dir two/second file.sp" in res.stdout


def test_f07_custom_language_overrides_builtin_c_and_custom_run_out(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "override_c"\n'
        'extensions = [".c"]\n'
        'compile = "cc {file} -o {out}"\n'
        'run = "python3 -c \\"import os,subprocess,sys; print(\'CUSTOM_RUN:\' + subprocess.check_output([os.path.abspath(sys.argv[1])], text=True).strip())\\" ${out}"\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )
    (work_dir / "hello.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("hi_c\\n"); return 0; }\n',
        encoding="utf-8",
    )

    r_default = run_cli(["hello.c"])
    assert r_default.returncode == 0, _combined(r_default)
    assert "CUSTOM_RUN:hi_c" in r_default.stdout

    r_outdir = run_cli(["--out-dir", "custom_bin", "hello.c"])
    assert r_outdir.returncode == 0, _combined(r_outdir)
    assert "CUSTOM_RUN:hi_c" in r_outdir.stdout


# ---------------------------------------------------------------------------
# F08: Sanitizers, Debuggers & Timeout (6 tests)
# ---------------------------------------------------------------------------


def test_f08_timeout_tight_boundary_kills_fast(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "slow.py").write_text("import time\ntime.sleep(3)\n", encoding="utf-8")
    start = time.monotonic()
    res = run_cli(["--timeout", "0.2", "slow.py"], timeout=8.0)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "timed out" in combined.lower()


def test_f08_timeout_during_compilation_vs_fast_compile_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "slowcc"\n'
        'extensions = [".sc"]\n'
        'compile = "python3 -c \\"import time; time.sleep(5)\\""\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )
    (work_dir / "a.sc").write_text("source\n", encoding="utf-8")
    (work_dir / "err.c").write_text('#error "intentional_compile_error"\n', encoding="utf-8")

    t1 = time.monotonic()
    r_slow = run_cli(["--timeout", "0.2", "a.sc"], timeout=8.0)
    e1 = time.monotonic() - t1
    assert e1 < 2.5
    c_slow = strip_ansi(_combined(r_slow))
    assert "[ ERROR ]" in c_slow
    assert "compilation timed out" in c_slow.lower()

    t2 = time.monotonic()
    r_err = run_cli(["--timeout", "5.0", "err.c"], timeout=8.0)
    e2 = time.monotonic() - t2
    assert e2 < 2.5
    c_err = strip_ansi(_combined(r_err))
    assert "[ ERROR ]" in c_err
    assert "compilation failed" in c_err.lower()
    assert "timed out" not in c_err.lower()


def test_f08_timeout_kills_multithreaded_and_grandchild_tree(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    pid_file = work_dir / "grandchild.pid"
    (work_dir / "tree.py").write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        'p = subprocess.Popen([sys.executable, "-S", "-c", "import time; time.sleep(30)"])\n'
        f"Path({str(pid_file)!r}).write_text(str(p.pid), encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    t1 = time.monotonic()
    r_tree = run_cli(["--timeout", "0.8", "tree.py"], timeout=8.0)
    assert time.monotonic() - t1 < 4.0
    c_tree = strip_ansi(_combined(r_tree))
    assert "[ ERROR ]" in c_tree
    assert "timed out" in c_tree.lower()
    assert pid_file.exists(), "tree.py did not write grandchild.pid before timeout fired"
    grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)

    (work_dir / "mt.py").write_text(
        "import threading, time\n"
        "def spin():\n"
        "    while True:\n"
        "        time.sleep(0.01)\n"
        "for _ in range(10):\n"
        "    threading.Thread(target=spin).start()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    t2 = time.monotonic()
    r_mt = run_cli(["--timeout", "0.3", "mt.py"], timeout=8.0)
    assert time.monotonic() - t2 < 2.5
    c_mt = strip_ansi(_combined(r_mt))
    assert "[ ERROR ]" in c_mt
    assert "timed out" in c_mt.lower()


def test_f08_asan_detects_use_after_free_at_runtime(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "uaf.c").write_text(
        "#include <stdlib.h>\n"
        "int main(void) {\n"
        "    int * volatile p = (int *)malloc(sizeof(int));\n"
        "    *p = 42;\n"
        "    free(p);\n"
        "    return *p;\n"
        "}\n",
        encoding="utf-8",
    )
    res = run_cli(["--asan", "uaf.c"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "AddressSanitizer" in combined or "use-after-free" in combined


def test_f08_invalid_sanitize_rejected_by_compiler(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    res = run_cli(["--sanitize", "nonexistent_san_xyz", "app.c"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "compilation failed" in combined.lower()


def test_f08_debugger_dry_run_command_construction_with_args(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (work_dir / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (work_dir / "app.js").write_text("console.log('hi');\n", encoding="utf-8")

    gdb = run_cli(["--gdb", "--dry-run", "--argument", "--port 8080", "app.c"])
    assert gdb.returncode == 0, _combined(gdb)
    assert "gdb --args" in gdb.stdout
    assert "--port 8080" in gdb.stdout

    lldb = run_cli(["--lldb", "--dry-run", "app.c", "--", "--trace"])
    assert lldb.returncode == 0, _combined(lldb)
    assert "lldb --" in lldb.stdout
    assert "--trace" in lldb.stdout

    vg = run_cli(["--valgrind", "--dry-run", "app.c"])
    assert vg.returncode == 0, _combined(vg)
    assert "valgrind --leak-check=full --track-origins=yes" in vg.stdout

    py_dbg = run_cli(["--debug", "--dry-run", "app.py", "--", "arg1"])
    assert py_dbg.returncode == 0, _combined(py_dbg)
    assert "-m pdb --" in py_dbg.stdout
    assert "arg1" in py_dbg.stdout

    js_dbg = run_cli(["--debug", "--dry-run", "app.js"])
    assert js_dbg.returncode == 0, _combined(js_dbg)
    assert "--inspect-brk --" in js_dbg.stdout

    if sys.platform == "darwin":
        c_dbg = run_cli(["--debug", "--dry-run", "app.c"])
        assert c_dbg.returncode == 0, _combined(c_dbg)
        assert "lldb --" in c_dbg.stdout


# ---------------------------------------------------------------------------
# F09: Output, Verbosity & Watch Mode (6 tests)
# ---------------------------------------------------------------------------


def test_f09_no_color_env_empty_vs_nonempty_vs_flag(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "hi.py").write_text('print("hi")\n', encoding="utf-8")

    nonempty = run_cli(["hi.py"], env_overrides={"NO_COLOR": "1"})
    assert nonempty.returncode == 0, _combined(nonempty)
    assert not ANSI_RE.search(_combined(nonempty))

    empty = run_cli(["hi.py"], env_overrides={"NO_COLOR": ""})
    assert empty.returncode == 0, _combined(empty)
    assert ANSI_RE.search(empty.stdout) is not None

    flag_override = run_cli(["--no-color", "hi.py"], env_overrides={"NO_COLOR": ""})
    assert flag_override.returncode == 0, _combined(flag_override)
    assert not ANSI_RE.search(_combined(flag_override))


def test_f09_quiet_suppresses_compiler_warnings_keeps_errors(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "warn.c").write_text(
        '#include <stdio.h>\nint main(void) { int unused = 1; printf("warn_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "bad.c").write_text("int broken = ;\n", encoding="utf-8")

    q_warn = run_cli(["-q", "--flags", "-Wall", "warn.c"])
    assert q_warn.returncode == 0, _combined(q_warn)
    q_out = strip_ansi(q_warn.stdout)
    assert "[ COMPILE ]" not in q_out
    assert "[ RUN ]" not in q_out
    assert "unused" not in _combined(q_warn)
    assert "warn_ok" in q_out

    q_err = run_cli(["-q", "bad.c"])
    assert "[ ERROR ]" in strip_ansi(_combined(q_err))


def test_f09_vv_debug_output_and_quiet_precedence(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "hi.py").write_text('print("hi")\n', encoding="utf-8")

    vv = run_cli(["-vv", "hi.py"])
    assert vv.returncode == 0, _combined(vv)
    vv_out = strip_ansi(vv.stdout)
    assert "[ DEBUG ] Debug logging enabled" in vv_out
    assert "hi" in vv_out

    q_vv = run_cli(["-q", "-vv", "hi.py"])
    assert q_vv.returncode == 0, _combined(q_vv)
    q_vv_out = strip_ansi(q_vv.stdout)
    assert "[ DEBUG ]" not in q_vv_out
    assert "hi" in q_vv_out


def test_f09_help_and_version_exact_cargo_version(
    run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    for vflag in ("--version", "-V"):
        ver = run_cli([vflag])
        assert ver.returncode == 0, _combined(ver)
        assert "0.2.1" in _combined(ver)

    for hflag in ("--help", "-h"):
        hlp = run_cli([hflag])
        assert hlp.returncode == 0, _combined(hlp)
        text = _combined(hlp)
        for expected in ("--timeout", "--sandbox", "--init", "--new"):
            assert expected in text
        assert "--sticker" not in text
        assert "--tasks" not in text


@pytest.mark.parametrize(
    "bad_argv",
    [
        ["--nonexistent-flag-xyz"],
        ["--jobs", "notanint"],
        ["--timeout", "notfloat"],
    ],
)
def test_f09_invalid_cli_flags_exit_code_2(
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    bad_argv: list[str],
) -> None:
    res = run_cli(bad_argv)
    assert res.returncode == 2


def test_f09_watch_mode_error_resilience_reload_and_sigint(
    work_dir: Path, spawn_cli: Callable[..., subprocess.Popen[str]]
) -> None:
    target = work_dir / "target.py"
    target.write_text('print("broken"\n', encoding="utf-8")

    proc = spawn_cli(["-w", "target.py"])
    initial = strip_ansi(_read_watch_until(proc, "[ ERROR ]", timeout=8.0))
    assert "Watch mode enabled" in initial
    assert "[ ERROR ]" in initial
    assert proc.poll() is None

    target.write_text('print("watch_recovered_ok", flush=True)\n', encoding="utf-8")
    now = time.time()
    os.utime(target, (now + 5, now + 5))

    reloaded = strip_ansi(_read_watch_until(proc, "watch_recovered_ok", timeout=6.0))
    assert "Restarting..." in reloaded
    assert "watch_recovered_ok" in reloaded

    proc.send_signal(signal.SIGINT)
    rc = proc.wait(timeout=5.0)
    assert rc == 0


# ---------------------------------------------------------------------------
# F10: Scaffolding & Utility Flags (6 tests)
# ---------------------------------------------------------------------------


def test_f10_completion_invalid_shell_and_pwsh_alias(
    run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    for bad in (["--completion", "invalid_shell"], ["--completion=tcsh"]):
        r_bad = run_cli(bad)
        assert r_bad.returncode == 1
        assert "[ ERROR ] Unsupported shell" in strip_ansi(_combined(r_bad))

    r_pwsh = run_cli(["--completion", "pwsh"])
    r_ps = run_cli(["--completion", "powershell"])
    assert r_pwsh.returncode == 0, _combined(r_pwsh)
    assert r_ps.returncode == 0, _combined(r_ps)
    assert r_pwsh.stdout == r_ps.stdout
    assert "Register-ArgumentCompleter" in r_pwsh.stdout


def test_f10_completion_empty_prints_install_guide(
    run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    res = run_cli(["--completion"])
    assert res.returncode == 0, _combined(res)
    for section in ("=== Shell Autocompletion Setup ===", "Zsh:", "Bash:", "Fish:", "PowerShell:"):
        assert section in res.stdout


def test_f10_internal_complete_empty_vs_configured_and_unknown(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    r_presets = run_cli(["--_complete", "presets"])
    assert r_presets.returncode == 0
    assert r_presets.stdout.strip() == ""

    r_tasks = run_cli(["--_complete", "tasks"])
    assert r_tasks.returncode == 0
    assert r_tasks.stdout.strip() == ""

    r_tpls = run_cli(["--_complete", "templates"])
    assert r_tpls.returncode == 0
    for builtin in ("c", "cpp", "py", "rs"):
        assert builtin in r_tpls.stdout.split()

    r_unk = run_cli(["--_complete", "unknown_type"])
    assert r_unk.returncode == 0
    assert r_unk.stdout.strip() == ""

    (work_dir / "Run.toml").write_text(
        '[presets.opt]\nc = ["-O2"]\n'
        '[tasks]\nbuild = "echo build"\n'
        '[templates.my_tpl]\nextensions = [".py"]\ncontent = "print(1)"\n',
        encoding="utf-8",
    )
    for kind, expected in (("presets", "opt"), ("tasks", "build"), ("templates", "my_tpl")):
        res = run_cli(["--_complete", kind])
        assert res.returncode == 0, _combined(res)
        assert expected in res.stdout.split()
        assert "Loaded config:" not in _combined(res)


def test_f10_new_overwrite_safety_and_missing_template(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    existing = work_dir / "existing.py"
    existing.write_text("ORIGINAL_CONTENT\n", encoding="utf-8")

    r_deny = run_cli(["--new", "existing.py"])
    assert r_deny.returncode == 1
    deny_combined = strip_ansi(_combined(r_deny))
    assert "[ ERROR ]" in deny_combined
    assert "already exists" in deny_combined
    assert existing.read_text(encoding="utf-8") == "ORIGINAL_CONTENT\n"

    r_force = run_cli(["--new", "existing.py", "-f"])
    assert r_force.returncode == 0, _combined(r_force)
    assert "[ CREATE ]" in strip_ansi(r_force.stdout)
    assert "def main():" in existing.read_text(encoding="utf-8")

    r_ext = run_cli(["--new", "foo.unknownext"])
    assert r_ext.returncode == 1
    assert "Cannot infer template" in strip_ansi(_combined(r_ext))

    r_tpl = run_cli(["--new", "foo.py", "--template", "no_such_tpl"])
    assert r_tpl.returncode == 1
    assert "not found" in strip_ansi(_combined(r_tpl)).lower()


def test_f10_new_multi_file_and_external_file_traversal_rejected(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    outside_secret = work_dir.parent / "outside_secret.txt"
    outside_secret.write_text("TOP_SECRET\n", encoding="utf-8")
    (work_dir / "Run.toml").write_text(
        "[templates.mal_multi]\n"
        'files = [{ name = "../escape.txt", content = "pwned" }]\n'
        "[templates.mal_file]\n"
        'file = "../outside_secret.txt"\n',
        encoding="utf-8",
    )
    sandbox_dir = work_dir / "sandbox_dir"
    sandbox_dir.mkdir()

    r_multi = run_cli(["--new", "sandbox_dir", "--template", "mal_multi", "-f"])
    assert r_multi.returncode == 1
    c_multi = strip_ansi(_combined(r_multi))
    assert "[ ERROR ]" in c_multi
    assert "Path traversal detected" in c_multi
    assert not (work_dir / "escape.txt").exists()

    r_file = run_cli(["--new", "leak.txt", "--template", "mal_file", "-f"])
    assert r_file.returncode == 1
    c_file = strip_ansi(_combined(r_file))
    assert "[ ERROR ]" in c_file
    assert "outside base directory" in c_file or "traversal" in c_file.lower()
    assert not (work_dir / "leak.txt").exists()


def test_f10_init_existing_run_toml_abort_and_confirm(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    cfg = work_dir / "Run.toml"
    cfg.write_text("[core]\n# sentinel\n", encoding="utf-8")

    r_abort = run_cli(["--init"], stdin_text="n\n")
    assert r_abort.returncode == 1
    c_abort = strip_ansi(_combined(r_abort))
    assert "already exists" in c_abort
    assert "Initialization cancelled" in c_abort
    assert "# sentinel" in cfg.read_text(encoding="utf-8")

    r_ok = run_cli(["--init"], stdin_text="y\n")
    assert r_ok.returncode == 0, _combined(r_ok)
    assert "[ INIT ]" in strip_ansi(r_ok.stdout)
    assert "[tasks]" in cfg.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F11: Config Discovery, XDG Paths & R3 (6 tests)
# ---------------------------------------------------------------------------


def test_f11_upward_4_level_limit_ignores_5th_parent(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    root = work_dir / "root"
    l3 = root / "a" / "b" / "c"
    l4 = l3 / "d"
    l4.mkdir(parents=True)
    (root / "Run.toml").write_text(
        '[tasks]\nprobe = "python3 -c \\"print(\'FOUND_ROOT\')\\""\n',
        encoding="utf-8",
    )

    r3 = run_cli(["probe"], cwd=l3)
    assert r3.returncode == 0, _combined(r3)
    assert "FOUND_ROOT" in r3.stdout

    r4 = run_cli(["probe"], cwd=l4)
    assert "FOUND_ROOT" not in r4.stdout


def test_f11_upward_stops_at_git_boundary(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    outer = work_dir / "outer"
    outer.mkdir()
    (outer / "Run.toml").write_text(
        '[tasks]\nouter_task = "python3 -c \\"print(\'OUTER_LEAK\')\\""\n',
        encoding="utf-8",
    )

    repo1_sub = outer / "repo1" / "sub"
    (outer / "repo1" / ".git").mkdir(parents=True)
    repo1_sub.mkdir(parents=True)
    r1 = run_cli(["outer_task"], cwd=repo1_sub)
    assert "OUTER_LEAK" not in r1.stdout

    repo2 = outer / "repo2"
    repo2_sub = repo2 / "sub"
    repo2_sub.mkdir(parents=True)
    (repo2 / ".git").write_text("gitdir: ../.git/worktrees/repo2\n", encoding="utf-8")
    r2 = run_cli(["outer_task"], cwd=repo2_sub)
    assert "OUTER_LEAK" not in r2.stdout

    (repo2 / "Run.toml").write_text(
        '[tasks]\ninner_task = "python3 -c \\"print(\'INNER_OK\')\\""\n',
        encoding="utf-8",
    )
    r3 = run_cli(["inner_task"], cwd=repo2_sub)
    assert r3.returncode == 0, _combined(r3)
    assert "INNER_OK" in r3.stdout


def test_f11_malformed_run_toml_exits_1(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text('[tasks\nbroken = "unclosed\n', encoding="utf-8")
    (work_dir / "ok.py").write_text('print("should_not_run")\n', encoding="utf-8")

    res = run_cli(["ok.py"])
    assert res.returncode == 1
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "Failed to parse" in combined


def test_f11_dotenv_comments_quotes_missing_and_os_precedence(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "ok.py").write_text('print("no_dotenv_ok")\n', encoding="utf-8")
    r_missing = run_cli(["ok.py"])
    assert r_missing.returncode == 0, _combined(r_missing)
    assert "no_dotenv_ok" in r_missing.stdout

    (work_dir / ".env").write_text(
        "# full line comment\n"
        'export QUOTED_ESC="line1\\nline2" # trailing comment\n'
        "SINGLE_LIT='raw\\nval'\n"
        "INLINE_HASH=abc#123\n"
        "STRIPPED_COMMENT=val # inline comment\n"
        "PRECEDENCE=from_dotenv\n",
        encoding="utf-8",
    )
    (work_dir / "check.py").write_text(
        "import os\n"
        "for k in ('QUOTED_ESC', 'SINGLE_LIT', 'INLINE_HASH', 'STRIPPED_COMMENT', 'PRECEDENCE'):\n"
        "    print(f'{k}={os.environ.get(k)!r}')\n",
        encoding="utf-8",
    )

    res = run_cli(["check.py"], env_overrides={"PRECEDENCE": "from_os"})
    assert res.returncode == 0, _combined(res)
    assert "QUOTED_ESC='line1\\nline2'" in res.stdout
    assert "SINGLE_LIT='raw\\\\nval'" in res.stdout
    assert "INLINE_HASH='abc#123'" in res.stdout
    assert "STRIPPED_COMMENT='val'" in res.stdout
    assert "PRECEDENCE='from_os'" in res.stdout


def test_f11_unset_env_expansion_and_default_home_paths_no_legacy(
    isolated_env: dict[str, Any],
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    home = isolated_env["home"]
    legacy_cfg_dir = home / ".config" / "run_kuranne"
    legacy_cfg_dir.mkdir(parents=True)
    (legacy_cfg_dir / "Run.toml").write_text(
        '[tasks]\nprobe = "python3 -c \\"print(\'LEGACY_CFG\')\\""\n',
        encoding="utf-8",
    )

    modern_cfg_dir = home / ".config" / "run"
    modern_cfg_dir.mkdir(parents=True)
    (modern_cfg_dir / "Run.toml").write_text(
        '[tasks]\nprobe = "python3 -c \\"print(\'UNSET=[${env:DEFINITELY_UNSET_999}]\')\\""\n',
        encoding="utf-8",
    )

    r_cfg = run_cli(["probe"], strip_env=["XDG_CONFIG_HOME", "XDG_CACHE_HOME"])
    assert r_cfg.returncode == 0, _combined(r_cfg)
    assert "UNSET=[]" in r_cfg.stdout
    assert "LEGACY_CFG" not in r_cfg.stdout

    (work_dir / "cached.c").write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("HOME_CACHE_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    r_cache = run_cli(["cached.c"], strip_env=["XDG_CONFIG_HOME", "XDG_CACHE_HOME"])
    assert r_cache.returncode == 0, _combined(r_cache)
    assert "HOME_CACHE_OK" in r_cache.stdout
    assert (home / ".cache" / "run").exists()
    assert not (home / ".cache" / "run_kuranne").exists()


@pytest.mark.parametrize(
    "legacy_toml",
    [
        '[runner]\nc = "clang"\n',
        '[preset.debug]\nc = ["-g"]\n',
        '[language.my]\nextensions = [".x"]\nrunner = "echo"\n',
        '[[language]]\nname = "x"\nextensions = [".x"]\nrunner = "echo"\n',
        '[exclude]\nfiles = ["a.c"]\n',
    ],
)
def test_f11_r3_dropped_aliases_strictly_rejected(
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    legacy_toml: str,
) -> None:
    (work_dir / "Run.toml").write_text(legacy_toml, encoding="utf-8")
    (work_dir / "hello.py").write_text('print("ok")\n', encoding="utf-8")

    res = run_cli(["hello.py"])
    assert res.returncode == 1
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    lowered = combined.lower()
    assert "no longer supported" in lowered or "deprecated" in lowered


# ---------------------------------------------------------------------------
# F12: Security, Validation & Sandbox CLI (6 tests)
# ---------------------------------------------------------------------------


def test_f12_dangerous_env_vars_blocked_all_channels(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    dangerous_vars = (
        "LD_PRELOAD",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "GLIBC_TUNABLES",
        "PYTHONPATH",
        "BASH_ENV",
        "RUSTFLAGS",
        "GOFLAGS",
    )
    (work_dir / ".env").write_text(
        "\n".join(f"{var}=from_dotenv" for var in dangerous_vars) + "\n",
        encoding="utf-8",
    )
    tpl_expr = ",".join(f"${{env:{v}}}" for v in dangerous_vars)
    (work_dir / "Run.toml").write_text(
        f'[tasks]\nexpand = "python3 -c \\"print(\'EXP={tpl_expr}\')\\""\n',
        encoding="utf-8",
    )
    (work_dir / "inspect_env.py").write_text(
        "import os\n"
        f"for k in {dangerous_vars!r}:\n"
        "    print(f'{k}={os.environ.get(k, \"BLOCKED\")}')\n",
        encoding="utf-8",
    )

    cli_args: list[str] = []
    for idx, var in enumerate(dangerous_vars):
        key = var.lower() if idx == 0 else var
        cli_args.extend(["--env", f"{key}=from_cli"])
    cli_args.append("inspect_env.py")

    host_overrides = {
        var: "from_host"
        for var in dangerous_vars
        if not (sys.platform == "darwin" and var == "DYLD_INSERT_LIBRARIES")
    }
    res_exec = run_cli(cli_args, env_overrides=host_overrides)
    assert res_exec.returncode == 0, _combined(res_exec)
    exec_out = strip_ansi(res_exec.stdout)
    assert "[ WARN ]" in exec_out
    for var in dangerous_vars:
        assert f"{var}=BLOCKED" in exec_out

    res_tpl = run_cli(["expand"], env_overrides=host_overrides)
    assert res_tpl.returncode == 0, _combined(res_tpl)
    tpl_out = strip_ansi(res_tpl.stdout)
    assert "[ WARN ]" in tpl_out
    assert "EXP=" + ("," * (len(dangerous_vars) - 1)) in tpl_out


@pytest.mark.parametrize(
    "flag",
    [
        "-fplugin=/tmp/evil.so",
        "-Wl,-rpath,/tmp",
        "-Wl,--wrap,malloc",
        "-x assembler",
        "-specs=/tmp/evil.spec",
        "-wrapper=/bin/sh",
        "-Xclang",
        "-C linker=/bin/sh",
    ],
)
def test_f12_suspicious_compiler_flags_all_patterns_blocked_unless_unsafe(
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    flag: str,
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    blocked = run_cli(["--flags", flag, "app.c"])
    blocked_combined = strip_ansi(_combined(blocked))
    assert "[ WARN ]" in blocked_combined
    assert "Suspicious flag" in blocked_combined
    assert "[ ERROR ]" in blocked_combined

    allowed = run_cli(["--unsafe", "--dry-run", "--flags", flag, "app.c"])
    assert allowed.returncode == 0, _combined(allowed)
    assert flag.split()[0] in allowed.stdout


def test_f12_suspicious_preset_flag_blocked_unless_unsafe(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[presets.evil]\nc = ["-fplugin=/tmp/evil.so"]\n',
        encoding="utf-8",
    )
    (work_dir / "app.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("PRESET_RAN\\n"); return 0; }\n',
        encoding="utf-8",
    )

    blocked = run_cli(["-p", "evil", "app.c"])
    combined = strip_ansi(_combined(blocked))
    assert "[ WARN ]" in combined
    assert "[ ERROR ]" in combined
    assert "PRESET_RAN" not in combined

    allowed = run_cli(["--unsafe", "--dry-run", "-p", "evil", "app.c"])
    assert allowed.returncode == 0, _combined(allowed)
    assert "-fplugin=/tmp/evil.so" in allowed.stdout


@pytest.mark.parametrize(
    "target_name",
    [
        "../outside.c",
        "bad; rm.py",
        "$(id).py",
        "`id`.py",
        "bad\nname.py",
    ],
)
def test_f12_path_traversal_and_shell_metachars_blocked_unless_force(
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    target_name: str,
) -> None:
    if target_name == "../outside.c":
        (work_dir.parent / "outside.c").write_text(
            '#include <stdio.h>\nint main(void) { printf("SAFE_MARKER\\n"); return 0; }\n',
            encoding="utf-8",
        )
    else:
        (work_dir / target_name).write_text('print("SAFE_MARKER")\n', encoding="utf-8")

    denied = run_cli([target_name])
    denied_combined = strip_ansi(_combined(denied))
    assert "[ ERROR ]" in denied_combined
    assert "SAFE_MARKER" not in denied_combined

    forced = run_cli(["-f", target_name])
    assert forced.returncode == 0, _combined(forced)
    forced_out = strip_ansi(forced.stdout)
    assert "[ WARN ]" in forced_out
    assert "SAFE_MARKER" in forced_out


def test_f12_new_path_traversal_rejected_even_with_force(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    nested = work_dir / "a" / "b" / "c"
    nested.mkdir(parents=True)

    for extra in ([], ["-f"]):
        res = run_cli(["--new", "../../../escape.py", *extra], cwd=nested)
        assert res.returncode == 1
        combined = strip_ansi(_combined(res))
        assert "[ ERROR ]" in combined
        assert "Path traversal detected" in combined
        assert not (work_dir / "escape.py").exists()


@pytest.mark.parametrize(
    ("sandbox_toml", "needs_dummy_docker"),
    [
        ('[sandbox]\nimage = "--privileged"\n', False),
        ('[sandbox]\nimage = "ubuntu:latest; rm -rf /"\n', False),
        ('[sandbox]\nimage = "   "\n', False),
        ('[sandbox]\ndockerfile = "missing.Dockerfile"\n', False),
        ('[sandbox]\ncompose = "missing-compose.yml"\n', False),
        ('[sandbox]\nimage = "bad;img"\n', True),
    ],
)
def test_f12_sandbox_malformed_image_and_missing_dockerfile_rejected(
    isolated_env: dict[str, Any],
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    sandbox_toml: str,
    needs_dummy_docker: bool,
) -> None:
    (work_dir / "Run.toml").write_text(sandbox_toml, encoding="utf-8")
    (work_dir / "app.py").write_text('print("hi")\n', encoding="utf-8")

    env_overrides: dict[str, str] | None = None
    if needs_dummy_docker:
        dummy_bin = work_dir / "dummy_bin"
        _make_dummy_docker(dummy_bin)
        base_path = clean_path(isolated_env["env"].get("PATH"))
        env_overrides = {"PATH": f"{dummy_bin}{os.pathsep}{base_path}"}

    res = run_cli(["--sandbox", "app.py"], env_overrides=env_overrides)
    assert res.returncode == 1
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    lowered = combined.lower()
    assert "image" in lowered or "not found" in lowered


def test_f12_expect_outside_workspace_blocked_unless_force(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    outside_exp = work_dir.parent / "outside_expect.txt"
    outside_exp.write_text("expected_out\n", encoding="utf-8")
    (work_dir / "app.py").write_text('print("expected_out")\n', encoding="utf-8")

    denied = run_cli(["app.py", "--expect", str(outside_exp)])
    denied_combined = strip_ansi(_combined(denied))
    assert "[ ERROR ]" in denied_combined
    assert "outside the workspace" in denied_combined

    forced = run_cli(["-f", "app.py", "--expect", str(outside_exp)])
    assert forced.returncode == 0, _combined(forced)
    assert "[ PASS ] Output matches" in strip_ansi(forced.stdout)
