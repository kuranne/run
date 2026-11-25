"""Tier 1 E2E tests: F09 (Output/Verbosity/Watch), F10 (Scaffolding/Utils), F11 (Config/XDG/R3), F12 (Security/Sandbox)."""

from __future__ import annotations

import datetime
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.e2e.conftest import ANSI_RE, REPO_ROOT, strip_ansi

pytestmark = pytest.mark.tier1


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


# ---------------------------------------------------------------------------
# F09: Output, Verbosity & Watch Mode
# ---------------------------------------------------------------------------


def test_f09_dry_run_no_side_effects(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "dry.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("SHOULD_NOT_RUN\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["--dry-run", "dry.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "[ DRY-RUN ]" in out
    assert "COMPILE:" in out
    assert "RUN:" in out
    assert "SHOULD_NOT_RUN" not in out
    assert not (work_dir / "dry.out").exists()


def test_f09_default_verbosity_vs_quiet_and_verbose(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "verb.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("payload_out\\n"); return 0; }\n',
        encoding="utf-8",
    )
    default_res = run_cli(["verb.c"])
    assert default_res.returncode == 0, _combined(default_res)
    default_out = strip_ansi(default_res.stdout)
    assert "[ COMPILE ]" in default_out
    assert "[ RUN ]" in default_out
    assert "payload_out" in default_out

    quiet_res = run_cli(["-q", "verb.c"])
    assert quiet_res.returncode == 0, _combined(quiet_res)
    quiet_out = strip_ansi(quiet_res.stdout)
    assert "[ COMPILE ]" not in quiet_out
    assert "[ RUN ]" not in quiet_out
    assert "payload_out" in quiet_out

    verbose_res = run_cli(["-v", "verb.c"])
    assert verbose_res.returncode == 0, _combined(verbose_res)
    assert "[ DEBUG ] Debug logging enabled" in strip_ansi(verbose_res.stdout)


def test_f09_no_color_flag_and_env(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "color.py").write_text('print("color_ok")\n', encoding="utf-8")

    flag_res = run_cli(["--no-color", "color.py"])
    assert flag_res.returncode == 0, _combined(flag_res)
    assert not ANSI_RE.search(_combined(flag_res))

    env_res = run_cli(["color.py"], env_overrides={"NO_COLOR": "1"})
    assert env_res.returncode == 0, _combined(env_res)
    assert not ANSI_RE.search(_combined(env_res))


def test_f09_version_and_help(
    run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    import tomllib

    expected_version = tomllib.loads((REPO_ROOT / "Cargo.toml").read_text(encoding="utf-8"))[
        "package"
    ]["version"]
    ver_res = run_cli(["-V"])
    assert ver_res.returncode == 0, _combined(ver_res)
    assert expected_version in _combined(ver_res)

    help_res = run_cli(["--help"])
    assert help_res.returncode == 0, _combined(help_res)
    help_text = _combined(help_res)
    assert "--test-dir" in help_text
    assert "--doctor" in help_text
    # Strictly dev baseline: feat/tui flags must not exist.
    assert "--sticker" not in help_text
    assert "--tasks" not in help_text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT/select on pipes not supported on Windows")
def test_f09_watch_mode_initial_run_and_sigint(
    work_dir: Path, spawn_cli: Callable[..., subprocess.Popen[str]]
) -> None:
    (work_dir / "watch_target.py").write_text('print("watch_initial_ok", flush=True)\n', encoding="utf-8")
    proc = spawn_cli(["-w", "watch_target.py"])
    assert proc.stdout is not None

    fd = proc.stdout.fileno()
    chunks: list[str] = []
    deadline = time.monotonic() + 8.0
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            events = sel.select(timeout=remaining)
            if not events:
                continue
            data = os.read(fd, 4096)
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
            if "watch_initial_ok" in "".join(chunks):
                break
    finally:
        sel.close()

    joined = "".join(chunks)
    assert "Watch mode enabled" in joined
    assert "watch_initial_ok" in joined

    proc.send_signal(signal.SIGINT)
    rc = proc.wait(timeout=5.0)
    assert rc == 0


def test_f09_no_args_eof_dev_prompt(
    run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    res = run_cli([], stdin_text="")
    assert res.returncode == 0, _combined(res)
    assert "[ INPUT ] No file given" in strip_ansi(res.stdout)


# ---------------------------------------------------------------------------
# F10: Scaffolding & Utility Flags
# ---------------------------------------------------------------------------


def test_f10_new_builtin_template(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    res = run_cli(["--new", "app.c"])
    assert res.returncode == 0, _combined(res)
    assert "[ CREATE ]" in strip_ansi(res.stdout)
    target = work_dir / "app.c"
    assert target.exists()
    exec_res = run_cli(["app.c"])
    assert exec_res.returncode == 0, _combined(exec_res)


def test_f10_new_custom_template_from_run_toml(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[templates.greeting]\n"
        'extension = ".c"\n'
        'content = "// {{name}} {{year}}\\n#include <stdio.h>\\nint main(void) { printf(\\"tpl_ok\\\\n\\"); return 0; }\\n"\n',
        encoding="utf-8",
    )
    res = run_cli(["--new", "hello.c", "--template", "greeting"])
    assert res.returncode == 0, _combined(res)
    assert "[ CREATE ]" in strip_ansi(res.stdout)
    generated = (work_dir / "hello.c").read_text(encoding="utf-8")
    year = str(datetime.date.today().year)
    assert f"// hello {year}" in generated
    assert "{{" not in generated


def test_f10_init_generates_run_toml(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (work_dir / "helper.py").write_text("print('hi')\n", encoding="utf-8")

    res = run_cli(["--init"])
    assert res.returncode == 0, _combined(res)
    assert "[ INIT ]" in strip_ansi(res.stdout)
    cfg_path = work_dir / "Run.toml"
    assert cfg_path.exists()
    content = cfg_path.read_text(encoding="utf-8")
    assert "[core]" in content
    for legacy in ("[runner]", "[preset]", "[language]", "[exclude]"):
        assert legacy not in content


def test_f10_doctor_ascii_output(
    run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    res = run_cli(["--doctor"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "=== System Toolchain Diagnostics ===" in out
    assert "[ OK ]" in out
    assert "Environment:" in out
    assert "Detected " in out
    for box_char in ("┌", "│", "─", "└"):
        assert box_char not in out


def test_f10_clean_clears_xdg_cache(
    isolated_env: dict[str, Any],
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    script = work_dir / "cacheable.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("cached\\n"); return 0; }\n',
        encoding="utf-8",
    )
    first = run_cli(["cacheable.c"])
    assert first.returncode == 0, _combined(first)
    cache_root = isolated_env["cache_home"] / "run"
    assert cache_root.exists()
    assert any(cache_root.iterdir())

    clean_res = run_cli(["--clean"])
    assert clean_res.returncode == 0, _combined(clean_res)
    assert "[ CLEAN ] Build cache cleared successfully." in strip_ansi(clean_res.stdout)
    remaining_files = [p for p in cache_root.rglob("*") if p.is_file()]
    assert not remaining_files


def test_f10_completion_and_internal_complete(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[presets.fast]\nc = ["-O3"]\n[tasks]\nbuild_it = "echo hi"\n',
        encoding="utf-8",
    )

    zsh = run_cli(["--completion", "zsh"])
    assert zsh.returncode == 0, _combined(zsh)
    assert "#compdef run" in zsh.stdout

    bash = run_cli(["--completion", "bash"])
    assert bash.returncode == 0, _combined(bash)
    assert "_run_complete" in bash.stdout

    fish = run_cli(["--completion", "fish"])
    assert fish.returncode == 0, _combined(fish)
    assert "complete -c run" in fish.stdout

    pwsh = run_cli(["--completion", "powershell"])
    assert pwsh.returncode == 0, _combined(pwsh)
    assert "Register-ArgumentCompleter" in pwsh.stdout

    bad = run_cli(["--completion=invalid_shell"])
    assert bad.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(bad))

    comp_presets = run_cli(["--_complete", "presets"])
    assert comp_presets.returncode == 0
    assert "fast" in comp_presets.stdout

    comp_tasks = run_cli(["--_complete", "tasks"])
    assert comp_tasks.returncode == 0
    assert "build_it" in comp_tasks.stdout

    comp_tpl = run_cli(["--_complete", "templates"])
    assert comp_tpl.returncode == 0
    assert "cpp" in comp_tpl.stdout


def test_f10_directory_cwd_flag(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    sub = work_dir / "subdir"
    sub.mkdir()
    (sub / "sub_app.py").write_text('print("from_subdir_ok")\n', encoding="utf-8")

    ok = run_cli(["--directory", "subdir", "sub_app.py"])
    assert ok.returncode == 0, _combined(ok)
    assert "from_subdir_ok" in ok.stdout

    bad = run_cli(["--cwd", "nonexistent_dir_xyz"])
    assert bad.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(bad))


# ---------------------------------------------------------------------------
# F11: Config Discovery, XDG Paths & R3
# ---------------------------------------------------------------------------


def test_f11_global_config_xdg_run_path(
    isolated_env: dict[str, Any],
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    # Legacy run_kuranne path must be ignored; modernized run/Run.toml must be loaded.
    legacy_dir = isolated_env["config_home"] / "run_kuranne"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "Run.toml").write_text(
        '[tasks]\nglobal_probe = "python3 -c \\"print(\'WRONG_LEGACY\')\\""\n',
        encoding="utf-8",
    )

    modern_dir = isolated_env["config_home"] / "run"
    modern_dir.mkdir(parents=True)
    (modern_dir / "Run.toml").write_text(
        '[tasks]\nglobal_probe = "python3 -c \\"print(\'FROM_XDG_RUN\')\\""\n',
        encoding="utf-8",
    )

    res = run_cli(["global_probe"])
    assert res.returncode == 0, _combined(res)
    assert "FROM_XDG_RUN" in res.stdout
    assert "WRONG_LEGACY" not in res.stdout


def test_f11_upward_4_level_discovery_and_git_stop(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    root = work_dir / "root"
    l3 = root / "a" / "b" / "c"
    l4 = l3 / "d"
    l4.mkdir(parents=True)
    (root / "Run.toml").write_text(
        '[tasks]\nping = "python3 -c \\"print(\'depth_found\')\\""\n',
        encoding="utf-8",
    )

    # 3 levels below root (depth indices 0,1,2,3 -> 4 dirs checked): must find root/Run.toml.
    found = run_cli(["ping"], cwd=l3)
    assert found.returncode == 0, _combined(found)
    assert "depth_found" in found.stdout

    # 4 levels below root: beyond 4-level search limit.
    missed = run_cli(["ping"], cwd=l4)
    assert "depth_found" not in missed.stdout

    # .git boundary must stop upward traversal before root/Run.toml.
    repo_sub = root / "repo" / "sub"
    (root / "repo" / ".git").mkdir(parents=True)
    repo_sub.mkdir(parents=True)
    git_stop = run_cli(["ping"], cwd=repo_sub)
    assert "depth_found" not in git_stop.stdout


def test_f11_cache_under_xdg_cache_run_and_no_cache(
    isolated_env: dict[str, Any],
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    script = work_dir / "cached.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("cache_path_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    r1 = run_cli(["cached.c"])
    assert r1.returncode == 0, _combined(r1)
    assert (isolated_env["cache_home"] / "run").exists()
    assert not (isolated_env["cache_home"] / "run_kuranne").exists()

    r2 = run_cli(["--no-cache", "cached.c"])
    assert r2.returncode == 0, _combined(r2)
    assert "cache_path_ok" in r2.stdout
    assert "Using cached binary:" not in r2.stdout


def test_f11_header_include_cache_invalidation(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    hdr = work_dir / "defs.h"
    hdr.write_text("#define MAGIC_VAL 101\n", encoding="utf-8")
    src = work_dir / "header_user.c"
    src.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        '#include "defs.h"\n'
        'int main(void) { printf("VAL=%d\\n", MAGIC_VAL); return 0; }\n',
        encoding="utf-8",
    )

    r1 = run_cli(["header_user.c"])
    assert r1.returncode == 0, _combined(r1)
    assert "VAL=101" in r1.stdout

    hdr.write_text("#define MAGIC_VAL 202\n", encoding="utf-8")
    r2 = run_cli(["header_user.c"])
    assert r2.returncode == 0, _combined(r2)
    assert "VAL=202" in r2.stdout


def test_f11_dotenv_loading_and_env_expansion(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / ".env").write_text(
        "APP_SECRET=s3cr3t_val\n"
        "OVERRIDE_ME=from_dotenv\n",
        encoding="utf-8",
    )
    (work_dir / "Run.toml").write_text(
        '[tasks]\nshow_sec = "python3 -c \\"print(\'TPL:${env:APP_SECRET}\')\\""\n',
        encoding="utf-8",
    )
    (work_dir / "read_env.py").write_text(
        "import os\n"
        "print('APP_SECRET=' + os.environ.get('APP_SECRET', 'MISSING'))\n"
        "print('OVERRIDE_ME=' + os.environ.get('OVERRIDE_ME', 'MISSING'))\n",
        encoding="utf-8",
    )

    task_res = run_cli(["show_sec"])
    assert task_res.returncode == 0, _combined(task_res)
    assert "TPL:s3cr3t_val" in task_res.stdout

    py_res = run_cli(["read_env.py"], env_overrides={"OVERRIDE_ME": "from_os"})
    assert py_res.returncode == 0, _combined(py_res)
    assert "APP_SECRET=s3cr3t_val" in py_res.stdout
    assert "OVERRIDE_ME=from_os" in py_res.stdout


@pytest.mark.parametrize(
    "legacy_toml",
    [
        '[runner]\nc = "clang"\n',
        '[preset.debug]\nc = ["-g"]\n',
        '[[language]]\nname = "x"\nextensions = [".x"]\nrunner = "echo"\n',
        '[exclude]\nfiles = ["a.c"]\n',
    ],
)
def test_f11_r3_dropped_aliases_rejected(
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    legacy_toml: str,
) -> None:
    (work_dir / "Run.toml").write_text(legacy_toml, encoding="utf-8")
    (work_dir / "hello.py").write_text('print("ok")\n', encoding="utf-8")
    res = run_cli(["hello.py"])
    assert res.returncode == 1
    combined = _combined(res).lower()
    assert "deprecated" in combined or "no longer supported" in combined


# ---------------------------------------------------------------------------
# F12: Security, Validation & Sandbox CLI
# ---------------------------------------------------------------------------


def test_f12_dangerous_env_vars_blocked(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / ".env").write_text("PYTHONPATH=/tmp/evil_dotenv\n", encoding="utf-8")
    (work_dir / "Run.toml").write_text(
        '[tasks]\nleak = "python3 -c \\"print(\'EXP=[${env:LD_PRELOAD}]\')\\""\n',
        encoding="utf-8",
    )
    (work_dir / "check_env.py").write_text(
        "import os\n"
        "for k in ('LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'PYTHONPATH'):\n"
        "    print(f'{k}={os.environ.get(k, \"BLOCKED\")}')\n",
        encoding="utf-8",
    )

    py_res = run_cli(
        [
            "--env",
            "LD_PRELOAD=/tmp/evil.so",
            "--env",
            "DYLD_INSERT_LIBRARIES=/tmp/evil.dylib",
            "--env",
            "PYTHONPATH=/tmp/evil_cli",
            "check_env.py",
        ]
    )
    assert py_res.returncode == 0, _combined(py_res)
    py_out = strip_ansi(py_res.stdout)
    assert "LD_PRELOAD=BLOCKED" in py_out
    assert "DYLD_INSERT_LIBRARIES=BLOCKED" in py_out
    assert "PYTHONPATH=BLOCKED" in py_out
    assert "[ WARN ]" in py_out

    tpl_res = run_cli(["leak"], env_overrides={"LD_PRELOAD": "/tmp/evil_host.so"})
    assert tpl_res.returncode == 0, _combined(tpl_res)
    assert "EXP=[]" in tpl_res.stdout


def test_f12_suspicious_compiler_flags_rejected_unless_unsafe(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "app.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    blocked = run_cli(["--flags", "-Wl,-rpath,/tmp", "app.c"])
    combined = strip_ansi(_combined(blocked))
    assert "[ WARN ]" in combined
    assert "Suspicious flag" in combined
    assert "[ ERROR ]" in combined

    allowed = run_cli(["--unsafe", "--dry-run", "--flags", "-Wl,-rpath,/tmp", "app.c"])
    assert allowed.returncode == 0, _combined(allowed)
    assert "-Wl,-rpath,/tmp" in allowed.stdout


def test_f12_path_traversal_and_shell_chars_blocked_unless_force(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    trav = run_cli(["--new", "../escape.c"])
    assert trav.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(trav))

    weird = work_dir / "bad;name.py"
    weird.write_text('print("forced_ok")\n', encoding="utf-8")

    denied = run_cli(["bad;name.py"])
    denied_combined = strip_ansi(_combined(denied))
    assert "[ ERROR ]" in denied_combined
    assert "forced_ok" not in denied_combined

    forced = run_cli(["-f", "bad;name.py"])
    assert forced.returncode == 0, _combined(forced)
    forced_out = strip_ansi(forced.stdout)
    assert "[ WARN ]" in forced_out
    assert "forced_ok" in forced_out


def test_f12_restrict_macos_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS-specific --restrict unsupported diagnostic test")
    (work_dir / "app.py").write_text('print("hi")\n', encoding="utf-8")
    res = run_cli(["--restrict", "app.py"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "--restrict" in combined
    assert "--sandbox" in combined


def test_f12_sandbox_missing_engine_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    empty_bin = work_dir / "empty_bin"
    empty_bin.mkdir()
    (work_dir / "app.py").write_text('print("hi")\n', encoding="utf-8")
    res = run_cli(["--sandbox", "app.py"], env_overrides={"PATH": str(empty_bin)})
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "No container engine" in combined
    assert "hi" not in combined


def test_f12_sandbox_invalid_image_name_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[sandbox]\nimage = "-privileged"\n',
        encoding="utf-8",
    )
    (work_dir / "app.py").write_text('print("hi")\n', encoding="utf-8")
    res = run_cli(["--sandbox", "app.py"])
    assert res.returncode == 1
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "image" in combined.lower()
