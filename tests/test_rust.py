import pytest
from pathlib import Path
from runner.rust_handler import RustHandler

class DummyRustRunner(RustHandler):
    def __init__(self):
        self.is_posix = True
        self.extra_flags = []
        self.run_args = []

def test_get_cargo_package_name(tmp_path):
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_bytes(b"""
    [package]
    name = "my_rust_app"
    version = "0.1.0"
    edition = "2021"
    """)

    handler = DummyRustRunner()
    pkg_name = handler._get_cargo_package_name(cargo_toml)
    assert pkg_name == "my_rust_app"

def test_get_cargo_package_name_missing(tmp_path):
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_bytes(b"""
    [workspace]
    members = ["member_a"]
    """)

    handler = DummyRustRunner()
    pkg_name = handler._get_cargo_package_name(cargo_toml)
    assert pkg_name is None

def test_find_cargo_toml(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_rs = src_dir / "main.rs"
    main_rs.write_text("fn main() {}")

    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_bytes(b'[package]\nname = "test"\n')

    handler = DummyRustRunner()
    found = handler._find_cargo_toml(main_rs)
    assert found == cargo_toml
