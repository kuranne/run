import pytest
import tomllib
from pathlib import Path
from util.init_config import ConfigInitializer
from util.config import Config

def test_detect_technologies(tmp_path):
    (tmp_path / "main.cpp").write_text("int main() {}")
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)")
    (tmp_path / "script.py").write_text("print('hi')")

    techs = ConfigInitializer.detect_technologies(tmp_path)
    assert "cpp" in techs
    assert "cmake" in techs
    assert "python" in techs

def test_init_config_generates_valid_toml(tmp_path, monkeypatch):
    (tmp_path / "main.cpp").write_text("int main() {}")
    (tmp_path / "helper.hpp").write_text("#pragma once")

    success = ConfigInitializer.init_config(tmp_path, force=True)
    assert success is True

    toml_path = tmp_path / "Run.toml"
    assert toml_path.exists()

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    assert "core" in data
    assert "presets" in data
    assert "debug" in data["presets"]

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    assert config.get_preset_flags("debug", "cpp") == ["-g", "-Wall", "-Wextra", "-std=c++20", "-O0"]

def test_init_config_existing_abort(tmp_path, monkeypatch):
    toml_path = tmp_path / "Run.toml"
    toml_path.write_text("[core]\n")

    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    success = ConfigInitializer.init_config(tmp_path, force=False)
    assert success is False
