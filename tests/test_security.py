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
    assert SecurityManager.check_suspicious_flags(["-Wl,-rpath,/tmp"]) is False

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

def test_base_runner_rejects_suspicious_flags():
    from runner.base_runner import BaseRunner
    from util.errors import CompilationError, ExecutionError

    runner = BaseRunner(op_flags={"quiet": True})
    with pytest.raises(CompilationError, match="Rejected suspicious flag"):
        runner.run_command(["gcc", "-fplugin=/tmp/evil.so", "main.c"], compiling=True)

    with pytest.raises(ExecutionError, match="Rejected suspicious flag"):
        runner.run_command(["./app", "-Wl,-rpath,/tmp"], compiling=False)

