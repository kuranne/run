"""Black-box CLI --new / --template scaffolding and traversal protection tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_new_builtin_and_overwrite_guard(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    res = run_cli(["--new", "hello.py"])
    assert res.returncode == 0, combined_output(res)
    target = work_dir / "hello.py"
    assert target.is_file()

    target.write_text("# keep me\n", encoding="utf-8")
    res_dup = run_cli(["--new", "hello.py"])
    assert res_dup.returncode == 1
    assert "# keep me" in target.read_text(encoding="utf-8")

    res_force = run_cli(["--new", "hello.py", "-f"])
    assert res_force.returncode == 0, combined_output(res_force)
    assert "# keep me" not in target.read_text(encoding="utf-8")


def test_new_custom_inline_and_multifile_templates(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[templates.greet]\n"
        'content = "print(\'HI:{{name}}\')\\n"\n'
        "[templates.bundle]\n"
        "files = [\n"
        '  { name = "a.py", content = "print(\'A\')\\n" },\n'
        '  { name = "b.py", content = "print(\'B\')\\n" },\n'
        "]\n",
        encoding="utf-8",
    )

    r1 = run_cli(["--new", "demo.py", "--template", "greet"])
    assert r1.returncode == 0, combined_output(r1)
    assert "HI:demo" in (work_dir / "demo.py").read_text(encoding="utf-8")

    r2 = run_cli(["--new", "pkg_dir", "--template", "bundle"])
    assert r2.returncode == 0, combined_output(r2)
    assert (work_dir / "pkg_dir" / "a.py").is_file()
    assert (work_dir / "pkg_dir" / "b.py").is_file()


def test_new_template_path_traversal_rejected(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[templates.evil]\n"
        'files = [{ name = "../../escape.txt", content = "bad" }]\n',
        encoding="utf-8",
    )
    res = run_cli(["--new", "safe_dir", "--template", "evil"])
    assert res.returncode == 1
    assert "[ ERROR ]" in strip_ansi(combined_output(res))
    assert not (work_dir.parent / "escape.txt").exists()
