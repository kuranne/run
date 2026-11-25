"""Black-box CLI shell completion and --_complete dynamic query tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_completion_scripts_and_install_instructions(
    run_cli: Callable[..., object]
) -> None:
    for shell in ("bash", "zsh", "fish", "powershell", "pwsh"):
        res = run_cli(["--completion", shell])
        assert res.returncode == 0, combined_output(res)
        assert len(res.stdout.strip()) > 20

    auto_res = run_cli(["--completion"])
    assert auto_res.returncode == 0, combined_output(auto_res)
    assert "bash" in auto_res.stdout.lower()

    bad = run_cli(["--completion", "invalid_shell"])
    assert bad.returncode == 1
    assert "[ ERROR ] Unsupported shell" in strip_ansi(combined_output(bad))


def test_internal_complete_queries(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        'lint = "echo lint"\n'
        "[presets.fast]\n"
        'c = ["-O3"]\n'
        "[templates.my_tpl]\n"
        'content = "print(1)\\n"\n',
        encoding="utf-8",
    )

    r_tasks = run_cli(["--_complete", "tasks"])
    assert r_tasks.returncode == 0
    assert "lint" in r_tasks.stdout

    r_presets = run_cli(["--_complete", "presets"])
    assert r_presets.returncode == 0
    assert "fast" in r_presets.stdout

    r_tpls = run_cli(["--_complete", "templates"])
    assert r_tpls.returncode == 0
    assert "my_tpl" in r_tpls.stdout
