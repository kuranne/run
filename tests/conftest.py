"""Shared fixtures and subprocess harness for black-box Rust CLI pytest verification."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

CORE_TOOLCHAINS: frozenset[str] = frozenset({"gcc", "g++", "python3", "bash"})


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return ANSI_RE.sub("", text)


def clean_path(raw_path: str | None = None) -> str:
    """Return PATH with mise shim directories removed to prevent sandbox EPERM."""
    source = raw_path if raw_path is not None else os.environ.get("PATH", "")
    parts = [
        entry
        for entry in source.split(os.pathsep)
        if entry and not entry.rstrip("/\\").endswith(("mise/shims", "mise\\shims"))
    ]
    return os.pathsep.join(parts)


def has_tool(name: str) -> bool:
    """Return True if *name* is found on the sanitized PATH (and functional for macOS Java stubs)."""
    resolved = shutil.which(name, path=clean_path())
    if resolved is None:
        return False
    if name in ("java", "javac") and sys.platform == "darwin":
        try:
            probe = subprocess.run(
                [resolved, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return probe.returncode == 0
        except OSError:
            return False
    return True


def require_optional_tool(name: str) -> None:
    """Skip test only when an optional external toolchain is unavailable."""
    if name in CORE_TOOLCHAINS:
        raise ValueError(f"Core toolchain '{name}' must never be skipped.")
    if not has_tool(name):
        pytest.skip(f"Optional external toolchain '{name}' is not installed on PATH")


def _is_executable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return path.suffix.lower() == ".exe" or os.access(path, os.X_OK)
    return os.access(path, os.X_OK)


def resolve_run_binary() -> Path:
    """Locate the compiled Rust `run` binary (`RUN_BIN`, `target/debug/run`, or `target/release/run`)."""
    env_bin = os.environ.get("RUN_BIN", "").strip()
    if env_bin:
        candidate = Path(env_bin)
        if _is_executable_file(candidate):
            return candidate
        if os.name == "nt" and candidate.suffix.lower() != ".exe":
            exe_candidate = candidate.with_suffix(".exe")
            if _is_executable_file(exe_candidate):
                return exe_candidate
        pytest.fail(f"RUN_BIN points to a non-executable path: {env_bin}")

    candidates = [
        REPO_ROOT / "target" / "debug" / "run",
        REPO_ROOT / "target" / "debug" / "run.exe",
        REPO_ROOT / "target" / "release" / "run",
        REPO_ROOT / "target" / "release" / "run.exe",
    ]
    for candidate in candidates:
        if _is_executable_file(candidate):
            return candidate

    pytest.fail("Compiled Rust `run` binary not found. Build with `cargo build` first.")


def combined_output(res: subprocess.CompletedProcess[str]) -> str:
    """Return stdout + stderr combined for error-message assertions."""
    return f"{res.stdout}\n{res.stderr}"


@pytest.fixture(scope="session")
def run_binary() -> Path:
    """Session-scoped path to the compiled Rust `run` binary."""
    return resolve_run_binary()


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, Any]:
    """Provide isolated HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME, and workspace directories."""
    home = tmp_path / "home"
    xdg_config = tmp_path / "config"
    xdg_cache = tmp_path / "cache"
    work = tmp_path / "work"
    for d in (home, xdg_config, xdg_cache, work):
        d.mkdir(parents=True, exist_ok=True)

    env: dict[str, str] = {
        "PATH": clean_path(),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_CACHE_HOME": str(xdg_cache),
        "APPDATA": str(xdg_config),
        "LOCALAPPDATA": str(xdg_cache),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR"):
            if key in os.environ:
                env[key] = os.environ[key]

    # Preserve rustup/cargo toolchain locations when HOME is isolated for tests.
    real_home = Path.home()
    rustup_home = os.environ.get("RUSTUP_HOME") or str(real_home / ".rustup")
    cargo_home = os.environ.get("CARGO_HOME") or str(real_home / ".cargo")
    if Path(rustup_home).is_dir():
        env["RUSTUP_HOME"] = rustup_home
    if Path(cargo_home).is_dir():
        env["CARGO_HOME"] = cargo_home

    return {
        "env": env,
        "home": home,
        "xdg_config": xdg_config,
        "xdg_cache": xdg_cache,
        "work": work,
    }


@pytest.fixture
def work_dir(isolated_env: dict[str, Any]) -> Path:
    """Return the isolated working directory for a test."""
    return isolated_env["work"]


@pytest.fixture
def run_cli(
    run_binary: Path, isolated_env: dict[str, Any]
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Invoke the compiled Rust `run` binary in the isolated workspace."""

    def _invoke(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin_text: str | None = None,
        env_overrides: Mapping[str, str | None] | None = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(isolated_env["env"])
        if env_overrides:
            for k, v in env_overrides.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        return subprocess.run(
            [str(run_binary), *args],
            cwd=str(cwd or isolated_env["work"]),
            env=env,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    return _invoke
