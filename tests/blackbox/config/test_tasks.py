"""Black-box CLI [tasks] execution, compound operators, and argument forwarding tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_tasks_simple_compound_and_quoted_operators(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        'echo_args = "python3 -c \\"import sys; print(\'ARGS:\' + \'|\'.join(sys.argv[1:]))\\""\n'
        'chain_ok = "python3 -c \\"print(\'STEP1\')\\" && python3 -c \\"print(\'STEP2\')\\""\n'
        'chain_fail = "python3 -c \\"import sys; sys.exit(1)\\" && python3 -c \\"print(\'SKIPPED_STEP\')\\""\n'
        'semi_ok = "python3 -c \\"print(\'SEMI1\')\\" ; python3 -c \\"print(\'SEMI2\')\\""\n'
        "quoted = \"python3 -c \\\"import sys; print('Q:' + sys.argv[1])\\\" 'a && b ; c'\"\n",
        encoding="utf-8",
    )

    r_args = run_cli(["echo_args", "one", "two"])
    assert r_args.returncode == 0, combined_output(r_args)
    assert "ARGS:one|two" in r_args.stdout

    r_ok = run_cli(["chain_ok"])
    assert r_ok.returncode == 0, combined_output(r_ok)
    assert r_ok.stdout.index("STEP1") < r_ok.stdout.index("STEP2")

    r_fail = run_cli(["chain_fail"])
    assert r_fail.returncode == 1
    non_task = [l for l in strip_ansi(combined_output(r_fail)).splitlines() if "[ TASK ]" not in l]
    assert all("SKIPPED_STEP" not in l for l in non_task)

    r_semi = run_cli(["semi_ok"])
    assert r_semi.returncode == 0, combined_output(r_semi)
    assert r_semi.stdout.index("SEMI1") < r_semi.stdout.index("SEMI2")

    r_q = run_cli(["quoted"])
    assert r_q.returncode == 0, combined_output(r_q)
    assert "Q:a && b ; c" in r_q.stdout


def test_tasks_vars_deps_sha256_sources_params_and_list(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "src.txt").write_text("v1\n", encoding="utf-8")
    (work_dir / "Run.toml").write_text(
        "[vars]\n"
        'out_file = "artifact.txt"\n\n'
        "[tasks.base]\n"
        'desc = "base step"\n'
        'cmd = "python3 -c \\"from pathlib import Path; p=Path(\'base_count.txt\'); p.write_text(str(int(p.read_text())+1) if p.exists() else \'1\')\\""\n\n'
        "[tasks.build]\n"
        'desc = "compile artifact"\n'
        'deps = ["base"]\n'
        'sources = ["src.txt"]\n'
        'generates = ["${vars.out_file}"]\n'
        "cmds = [\n"
        '  "python3 -c \\"from pathlib import Path; Path(\'${vars.out_file}\').write_text(Path(\'src.txt\').read_text())\\""\n'
        "]\n\n"
        "[tasks.all]\n"
        'desc = "diamond dag"\n'
        'deps = ["base", "build"]\n\n'
        "[tasks.deploy]\n"
        'desc = "deploy with params"\n'
        'params = ["env", "tag"]\n'
        'cmd = "python3 -c \\"print(\'DEPLOY:${env}:${tag}\')\\""\n\n'
        "[tasks.cyc_a]\n"
        'deps = ["cyc_b"]\n'
        "[tasks.cyc_b]\n"
        'deps = ["cyc_a"]\n',
        encoding="utf-8",
    )

    # 1. --list shows tasks with params & descriptions
    r_list = run_cli(["--list"])
    assert r_list.returncode == 0, combined_output(r_list)
    assert "build — compile artifact" in r_list.stdout
    assert "deploy <env> <tag> — deploy with params" in r_list.stdout

    # 2. Diamond DAG runs `base` only once, and generates artifact.txt
    r_all1 = run_cli(["all"])
    assert r_all1.returncode == 0, combined_output(r_all1)
    assert (work_dir / "artifact.txt").read_text(encoding="utf-8") == "v1\n"
    assert (work_dir / "base_count.txt").read_text(encoding="utf-8") == "1"

    # 3. Second build skips because SHA-256 of sources + generates match
    r_b2 = run_cli(["build"])
    assert r_b2.returncode == 0, combined_output(r_b2)
    assert "Task 'build' is up to date." in strip_ansi(r_b2.stdout)

    # 4. Modifying source triggers rebuild
    (work_dir / "src.txt").write_text("v2\n", encoding="utf-8")
    r_b3 = run_cli(["build"])
    assert r_b3.returncode == 0, combined_output(r_b3)
    assert (work_dir / "artifact.txt").read_text(encoding="utf-8") == "v2\n"

    # 5. Justfile-style params via `-- <argv>` and missing param error
    r_dep = run_cli(["deploy", "--", "prod", "v1.0.0"])
    assert r_dep.returncode == 0, combined_output(r_dep)
    assert "DEPLOY:prod:v1.0.0" in r_dep.stdout

    r_dep_missing = run_cli(["deploy", "--", "prod"])
    assert r_dep_missing.returncode == 1
    assert "missing required parameter 'tag'" in strip_ansi(combined_output(r_dep_missing))

    # 6. Cycle detection
    r_cyc = run_cli(["cyc_a"])
    assert r_cyc.returncode == 1
    assert "Cyclic task dependency" in strip_ansi(combined_output(r_cyc))


def test_tasks_platforms_parallel_deps_and_cache_stats_gc(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    """Verify OS-conditional tasks, parallel leaf deps, --cache-stats, and --cache-gc."""
    import sys

    other_os = "windows" if sys.platform != "win32" else "linux"
    host_os = "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")
    (work_dir / "Run.toml").write_text(
        f"[tasks.dep_a]\n"
        f'cmd = "python3 -c \\"print(\'DEP_A\')\\""\n'
        f"[tasks.dep_b]\n"
        f'cmd = "python3 -c \\"print(\'DEP_B\')\\""\n'
        f"[tasks.par]\n"
        f'deps = ["dep_a", "dep_b"]\n'
        f'cmd = "python3 -c \\"print(\'PAR_DONE\')\\""\n'
        f"[tasks.skip_me]\n"
        f'platforms = ["{other_os}"]\n'
        f'cmd = "python3 -c \\"import sys; sys.exit(9)\\""\n'
        f"[tasks.os_override]\n"
        f'cmd = "python3 -c \\"print(\'BASE\')\\""\n'
        f"[tasks.os_override.{host_os}]\n"
        f'cmd = "python3 -c \\"print(\'HOST_OVERRIDE\')\\""\n',
        encoding="utf-8",
    )

    r_par = run_cli(["par"])
    assert r_par.returncode == 0, r_par.stdout + r_par.stderr
    assert "DEP_A" in r_par.stdout
    assert "DEP_B" in r_par.stdout
    assert "PAR_DONE" in r_par.stdout

    r_skip = run_cli(["skip_me"])
    assert r_skip.returncode == 0, r_skip.stdout + r_skip.stderr
    assert "[ SKIP ]" in r_skip.stdout

    r_ov = run_cli(["os_override"])
    assert r_ov.returncode == 0, r_ov.stdout + r_ov.stderr
    assert "HOST_OVERRIDE" in r_ov.stdout

    r_stats = run_cli(["--cache-stats", "--cache-gc"])
    assert r_stats.returncode == 0, r_stats.stdout + r_stats.stderr
    assert "Cache bucket:" in r_stats.stdout
    assert "Pruned " in r_stats.stdout
