import pytest
from pathlib import Path
from runner.project_runner import ProjectRunner
from util.config import Config

class DummyRunner:
    def __init__(self):
        self.executed = []

    def run_command(self, cmd, use_shell=False, compiling=False):
        self.executed.append((cmd, use_shell, compiling))
        return True

def test_detect_default_cargo_project(tmp_path, monkeypatch):
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\n')
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()

    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None
    proj_name, proj_cfg, manifest_path = detected
    assert proj_name == "cargo"
    assert manifest_path == cargo_toml

def test_detect_default_go_project(tmp_path, monkeypatch):
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/app\n")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()

    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None
    proj_name, proj_cfg, manifest_path = detected
    assert proj_name == "go"
    assert manifest_path == go_mod

def test_run_project_single_command(tmp_path, monkeypatch):
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/app\n")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    runner = DummyRunner()

    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None
    success = ProjectRunner.run_project(detected, runner, run_args=["arg1"])
    assert success is True
    assert len(runner.executed) == 1
    cmd, use_shell, compiling = runner.executed[0]
    assert cmd == ["go", "run", ".", "arg1"]
    assert compiling is False

def test_run_project_build_and_run(tmp_path, monkeypatch):
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [projects.custom_cmake]
    file = "CMakeLists.txt"
    build = "cmake --build build"
    run = "./build/app"
    """)
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("cmake_minimum_required(VERSION 3.20)\n")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    runner = DummyRunner()

    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None
    proj_name, proj_cfg, manifest_path = detected
    assert proj_name == "custom_cmake"

    success = ProjectRunner.run_project(detected, runner, run_args=["--server"])
    assert success is True
    assert len(runner.executed) == 2

    build_cmd, use_shell, compiling = runner.executed[0]
    assert build_cmd == ["cmake", "--build", "build"]
    assert compiling is True

    run_cmd, use_shell, compiling = runner.executed[1]
    assert run_cmd == ["./build/app", "--server"]
    assert compiling is False

def test_get_watch_files_includes_src(tmp_path):
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("")
    src = tmp_path / "src"
    src.mkdir()
    main_rs = src / "main.rs"
    main_rs.write_text("")

    watch_files = ProjectRunner.get_watch_files(manifest)
    assert str(manifest) in watch_files
    assert str(main_rs) in watch_files
