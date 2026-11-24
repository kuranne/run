"""Black-box CLI Rust standalone and Cargo project execution tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, require_optional_tool, strip_ansi


def test_rust_dry_run_standalone_and_cargo_project(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "solo.rs").write_text('fn main() { println!("RS_OK"); }\n', encoding="utf-8")
    res_solo = run_cli(["--dry-run", "solo.rs"])
    assert res_solo.returncode == 0, combined_output(res_solo)
    assert "rustc" in strip_ansi(res_solo.stdout)

    cargo_dir = work_dir / "demo_crate"
    (cargo_dir / "src").mkdir(parents=True)
    (cargo_dir / "Cargo.toml").write_text(
        '[package]\nname = "demo_crate"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (cargo_dir / "src" / "main.rs").write_text('fn main() { println!("CRATE_OK"); }\n', encoding="utf-8")

    res_cargo = run_cli(["--dry-run", "src/main.rs"], cwd=cargo_dir)
    assert res_cargo.returncode == 0, combined_output(res_cargo)
    assert "cargo run" in strip_ansi(res_cargo.stdout)


def test_rust_standalone_execution_and_cache(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    require_optional_tool("rustc")
    (work_dir / "hello.rs").write_text('fn main() { println!("RUSTC_LIVE"); }\n', encoding="utf-8")

    r1 = run_cli(["hello.rs"])
    assert r1.returncode == 0, combined_output(r1)
    assert "RUSTC_LIVE" in r1.stdout

    r2 = run_cli(["hello.rs"])
    assert r2.returncode == 0, combined_output(r2)
    assert "[ INFO ] Using cached binary:" in strip_ansi(r2.stdout)
    assert "RUSTC_LIVE" in r2.stdout
