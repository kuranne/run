import pytest
import os
from util.security import SecurityManager
from util.errors import ConfigError

def test_sanitize_execution_env(monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/lib/evil.so")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/lib/evil_mac.dylib")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/custom/lib")
    monkeypatch.setenv("SAFE_VAR", "hello")

    sanitized = SecurityManager.sanitize_execution_env()
    assert "LD_PRELOAD" not in sanitized
    assert "DYLD_INSERT_LIBRARIES" not in sanitized
    assert "DYLD_LIBRARY_PATH" not in sanitized
    assert sanitized["SAFE_VAR"] == "hello"

def test_check_root_allow_override(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # allow_root=True should not raise ConfigError
    SecurityManager.check_root(allow_root=True)

def test_check_root_blocked(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ConfigError, match="blocked"):
        SecurityManager.check_root(allow_root=False)
