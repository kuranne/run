import pytest
from pathlib import Path
from runner.project_runner import TaskRunner
from util.config import Config
from util.errors import ConfigError

class DummyRunner:
    def __init__(self):
        self.executed = []

    def run_command(self, cmd, use_shell=False, compiling=False):
        self.executed.append((cmd, use_shell, compiling))
        return True

def test_is_task(tmp_path, monkeypatch):
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [tasks]
    test = "pytest tests/"
    build = "cargo build"
    """)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()

    assert TaskRunner.is_task("test", config) is True
    assert TaskRunner.is_task("build", config) is True
    assert TaskRunner.is_task("nonexistent", config) is False

def test_run_task_simple(tmp_path, monkeypatch):
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [tasks]
    test = "pytest tests/"
    """)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    runner = DummyRunner()

    TaskRunner.run_task("test", config, ["-k", "test_cache"], runner)
    assert len(runner.executed) == 1
    cmd, use_shell, compiling = runner.executed[0]
    assert cmd == ["pytest", "tests/", "-k", "test_cache"]
    assert use_shell is False

def test_run_task_with_shell_operator(tmp_path, monkeypatch):
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [tasks]
    build = "mkdir -p build && cmake -B build"
    """)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    runner = DummyRunner()

    TaskRunner.run_task("build", config, ["--extra"], runner)
    assert len(runner.executed) == 1
    cmd, use_shell, compiling = runner.executed[0]
    assert use_shell is False
    assert cmd == ["mkdir", "-p", "build", "&&", "cmake", "-B", "build", "--extra"]

def test_run_task_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    runner = DummyRunner()

    with pytest.raises(ConfigError, match="Task 'missing' not found"):
        TaskRunner.run_task("missing", config, [], runner)
