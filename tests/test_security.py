import pytest
import os
from util.security import SecurityManager
from util.errors import ConfigError

def test_sanitize_execution_env(monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/lib/evil.so")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/lib/evil_mac.dylib")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/custom/lib")
    monkeypatch.setenv("PYTHONPATH", "/malicious/python")
    monkeypatch.setenv("NODE_OPTIONS", "--require /evil.js")
    monkeypatch.setenv("SAFE_VAR", "hello")

    sanitized = SecurityManager.sanitize_execution_env()
    assert "LD_PRELOAD" not in sanitized
    assert "DYLD_INSERT_LIBRARIES" not in sanitized
    assert "DYLD_LIBRARY_PATH" not in sanitized
    assert "PYTHONPATH" not in sanitized
    assert "NODE_OPTIONS" not in sanitized
    assert sanitized["SAFE_VAR"] == "hello"

def test_check_suspicious_flags():
    assert SecurityManager.check_suspicious_flags(["-g", "-Wall", "-O3"]) is True
    assert SecurityManager.check_suspicious_flags(["-fplugin=/tmp/evil.so"]) is False
    assert SecurityManager.check_suspicious_flags(["-fplugin-arg-evil=1"]) is False
    assert SecurityManager.check_suspicious_flags(["-Wl,-rpath,/tmp"]) is False
    assert SecurityManager.check_suspicious_flags(["-Wl,--wrap,malloc"]) is False
    assert SecurityManager.check_suspicious_flags(["-x", "assembler"]) is False
    assert SecurityManager.check_suspicious_flags(["-specs=/tmp/evil.spec"]) is False
    assert SecurityManager.check_suspicious_flags(["-specs", "evil.spec"]) is False
    assert SecurityManager.check_suspicious_flags(["-wrapper", "/bin/sh"]) is False
    assert SecurityManager.check_suspicious_flags(["-wrapper=/bin/sh"]) is False
    assert SecurityManager.check_suspicious_flags(["-Xclang", "-load"]) is False
    assert SecurityManager.check_suspicious_flags(["-Clinker=/bin/sh"]) is False
    assert SecurityManager.check_suspicious_flags(["-C", "linker=/bin/sh"]) is False
    assert SecurityManager.check_suspicious_flags(["-Clink-arg=-Wl,-rpath,/tmp"]) is False
    assert SecurityManager.check_suspicious_flags(["-C", "link-arg=-Wl,-rpath,/tmp"]) is False

def test_check_root_allow_override(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # allow_root=True should not raise ConfigError
    SecurityManager.check_root(allow_root=True)

def test_check_root_blocked(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ConfigError, match="blocked"):
        SecurityManager.check_root(allow_root=False)

def test_sanitize_execution_env_expanded_dangerous_vars(monkeypatch):
    dangerous = {
        "LD_AUDIT": "/lib/audit.so",
        "GLIBC_TUNABLES": "glibc.malloc.check=1",
        "GCONV_PATH": "/custom/gconv",
        "BASH_ENV": "/tmp/evil_bash",
        "ENV": "/tmp/evil_sh",
        "PROMPT_COMMAND": "malicious_cmd",
        "JAVA_TOOL_OPTIONS": "-javaagent:/evil.jar",
        "_JAVA_OPTIONS": "-Xbootclasspath:/evil.jar",
        "RUBYOPT": "-r/evil.rb",
        "PERL5OPT": "-Mevil",
        "RUSTFLAGS": "-Clink-arg=-Wl,-rpath,/tmp",
        "RUSTC_WRAPPER": "/tmp/evil_rustc",
        "GOFLAGS": "-buildmode=plugin",
        "DYLD_FALLBACK_LIBRARY_PATH": "/tmp/dyld",
    }
    for k, v in dangerous.items():
        monkeypatch.setenv(k, v)

    sanitized = SecurityManager.sanitize_execution_env()
    for k in dangerous:
        assert k not in sanitized, f"{k} should have been sanitized"

def test_custom_env_cannot_reintroduce_dangerous_vars():
    custom = {
        "LD_PRELOAD": "/tmp/evil.so",
        "GLIBC_TUNABLES": "glibc.malloc=1",
        "SAFE_CUSTOM": "safe_val",
    }
    sanitized = SecurityManager.sanitize_execution_env(custom_env=custom)
    assert "LD_PRELOAD" not in sanitized
    assert "GLIBC_TUNABLES" not in sanitized
    assert sanitized.get("SAFE_CUSTOM") == "safe_val"

def test_strict_whitelist_sandbox_env(monkeypatch):
    monkeypatch.setenv("SECRET_AWS_KEY", "AKIA12345678")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xyz123")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/user")

    sanitized = SecurityManager.sanitize_execution_env(strict_whitelist=True)
    assert "SECRET_AWS_KEY" not in sanitized
    assert "GITHUB_TOKEN" not in sanitized
    assert sanitized["PATH"] == "/usr/bin:/bin"
    assert sanitized["HOME"] == "/home/user"


def test_strict_whitelist_preserves_container_daemon_env(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    monkeypatch.setenv("PODMAN_SOCKET", "/run/podman/podman.sock")
    monkeypatch.setenv("CONTAINER_HOST", "ssh://user@remote")

    sanitized = SecurityManager.sanitize_execution_env(strict_whitelist=True)
    assert sanitized.get("DOCKER_HOST") == "unix:///var/run/docker.sock"
    assert sanitized.get("DOCKER_CONTEXT") == "desktop-linux"
    assert sanitized.get("PODMAN_SOCKET") == "/run/podman/podman.sock"
    assert sanitized.get("CONTAINER_HOST") == "ssh://user@remote"

def test_base_runner_rejects_suspicious_flags():
    from runner.base_runner import BaseRunner
    from util.errors import CompilationError, ExecutionError

    runner = BaseRunner(op_flags={"quiet": True})
    with pytest.raises(CompilationError, match="Rejected suspicious flag"):
        runner.run_command(["gcc", "-fplugin=/tmp/evil.so", "main.c"], compiling=True)

    with pytest.raises(ExecutionError, match="Rejected suspicious flag"):
        runner.run_command(["./app", "-Wl,-rpath,/tmp"], compiling=False)


def test_validator_full_path_checks():
    from util.validator import Validator
    from pathlib import Path

    # Safe paths
    assert Validator.validate_path(Path("main.c"))
    assert Validator.validate_path(Path("src/nested/main.cpp"))
    assert Validator.validate_path("simple.py")

    # Path traversal
    assert not Validator.validate_path(Path("../main.c"))
    assert not Validator.validate_path(Path("sub/../main.c"))
    assert not Validator.validate_path("../../../etc/passwd")

    # Control characters
    assert not Validator.validate_path("main\x00.c")
    assert not Validator.validate_path("src/\nmain.c")
    assert not Validator.validate_path("src/\rmain.c")
    assert not Validator.validate_path("src/\tmain.c")

    # Shell metacharacters anywhere in path
    assert not Validator.validate_path("dir;evil/main.c")
    assert not Validator.validate_path("main;echo.c")
    assert not Validator.validate_path("dir|cat/main.c")
    assert not Validator.validate_path("$(whoami).c")
    assert not Validator.validate_path("dir`rm`/main.c")
    assert not Validator.validate_path("dir&bg/main.c")
    assert not Validator.validate_path("<input>.c")
    assert not Validator.validate_path(">output>.c")


def test_core_runner_rejects_suspicious_path(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    from runner.core import CompilerRunner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    # Path with traversal segment
    runner = CompilerRunner(op_flags={"quiet": False, "force": False})
    res = runner._handle_single_file(Path("../outside.c"))
    assert res is False
    assert "Refusing to process file with suspicious characters" in caplog.text

    # Override with force
    caplog.clear()
    runner_force = CompilerRunner(op_flags={"quiet": False, "force": True})
    runner_force._handle_single_file(Path("../outside.c"))
    assert "Processing file with suspicious characters due to --force" in caplog.text


def test_base_runner_expect_boundary_enforcement(tmp_path, monkeypatch):
    from runner.base_runner import BaseRunner
    from util.errors import ConfigError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret_data")

    # 1. Reject --expect outside workspace without --force
    runner = BaseRunner(op_flags={"expect": str(outside), "force": False})
    with pytest.raises(ConfigError, match="outside the workspace"):
        runner.run_command(["echo", "hello"], compiling=False)

    # 2. Allow with --force
    runner_force = BaseRunner(op_flags={"expect": str(outside), "force": True})
    # Execution proceeds to check expectation; fails match but does not raise ConfigError
    success = runner_force.run_command(["echo", "hello"], compiling=False)
    assert success is False



