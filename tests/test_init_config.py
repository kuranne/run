"""Black-box CLI --init configuration scaffolding tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_init_creates_run_toml_and_respects_force(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    res = run_cli(["--init"])
    assert res.returncode == 0, combined_output(res)
    cfg_path = work_dir / "Run.toml"
    assert cfg_path.is_file()
    content = cfg_path.read_text(encoding="utf-8")
    assert "[core]" in content

    cfg_path.write_text("# custom marker\n", encoding="utf-8")
    res_no_force = run_cli(["--init"], stdin_text="n\n")
    assert res_no_force.returncode == 1
    assert "# custom marker" in cfg_path.read_text(encoding="utf-8")
    assert "[ WARN ]" in strip_ansi(combined_output(res_no_force))

    res_force = run_cli(["--init", "-f"])
    assert res_force.returncode == 0, combined_output(res_force)
    assert "[core]" in cfg_path.read_text(encoding="utf-8")
