"""Shared fixtures and subprocess harness for opaque-box E2E CLI tests."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[2]

ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def is_pid_alive(pid: int) -> bool:
    """Return True if *pid* is currently running across POSIX and Windows."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences from terminal output."""
    return ANSI_RE.sub("", text)

CORE_TOOLCHAINS: frozenset[str] = frozenset(
    {"cc", "clang", "gcc", "c++", "clang++", "g++", "python3", "bash", "sh"}
)

OPTIONAL_TOOLCHAINS: frozenset[str] = frozenset(
    {
        "go",
        "rustc",
        "cargo",
        "javac",
        "java",
        "ruby",
        "node",
        "perl",
        "lua",
        "luajit",
        "valgrind",
        "gdb",
        "lldb",
        "docker",
        "podman",
        "zig",
    }
)

DANGEROUS_ENV_VARS: frozenset[str] = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "LD_BIND_NOW",
        "LD_PROFILE",
        "GLIBC_TUNABLES",
        "GCONV_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_IMAGE_SUFFIX",
        "DYLD_PRINT_LIBRARIES",
        "DYLD_SHARED_CACHE_DIR",
        "BASH_ENV",
        "ENV",
        "PROMPT_COMMAND",
        "IFS",
        "SHELLOPTS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "NODE_PATH",
        "NODE_OPTIONS",
        "PERL5LIB",
        "PERL5OPT",
        "RUBYLIB",
        "RUBYOPT",
        "LUA_PATH",
        "LUA_CPATH",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "CLASSPATH",
        "RUSTFLAGS",
        "RUSTC_WRAPPER",
        "GOFLAGS",
    }
)

DANGEROUS_ENV_PREFIXES: tuple[str, ...] = ("LD_", "DYLD_", "BASH_FUNC_")


def pytest_configure(config: pytest.Config) -> None:
    """Register E2E test tier markers without modifying pyproject.toml."""
    config.addinivalue_line(
        "markers",
        "tier1: Tier 1 opaque-box feature coverage tests across F01-F12",
    )
    config.addinivalue_line(
        "markers",
        "tier2: Tier 2 boundary, corner-case, negative, R3, and security tests",
    )
    config.addinivalue_line(
        "markers",
        "tier3: Tier 3 pairwise multi-feature CLI combination tests",
    )
    config.addinivalue_line(
        "markers",
        "tier4: Tier 4 real-world end-to-end developer workflow scenarios",
    )


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
    """Return True if *name* is found on the sanitized PATH (and working for macOS Java stubs)."""
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
    """Skip only when an optional external toolchain is absent.

    Core toolchains (C/C++/Python3/Bash) are never permitted to be skipped.
    """
    if name in CORE_TOOLCHAINS:
        raise ValueError(f"Core toolchain '{name}' must never be skipped in E2E tests.")
    if name not in OPTIONAL_TOOLCHAINS:
        raise ValueError(f"Unknown toolchain '{name}' passed to require_optional_tool().")
    if not has_tool(name):
        pytest.skip(f"Optional external toolchain '{name}' is not installed on PATH")


def _is_executable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return path.suffix.lower() == ".exe" or os.access(path, os.X_OK)
    return os.access(path, os.X_OK)


def resolve_run_binary() -> Path:
    """Locate the compiled Rust `run` binary or fail the test cleanly.

    Order:
    1. `RUN_BIN` environment variable
    2. `<repo_root>/target/debug/run` (+ `.exe` fallback)
    3. `<repo_root>/target/release/run` (+ `.exe` fallback)
    """
    env_bin = os.environ.get("RUN_BIN", "").strip()
    if env_bin:
        candidate = Path(env_bin)
        if _is_executable_file(candidate):
            return candidate
        if os.name == "nt" and candidate.suffix.lower() != ".exe":
            exe_candidate = candidate.with_suffix(".exe")
            if _is_executable_file(exe_candidate):
                return exe_candidate
        pytest.fail(
            f"RUN_BIN is set to '{env_bin}', which does not exist or is not executable."
        )

    candidates = [
        REPO_ROOT / "target" / "debug" / "run",
        REPO_ROOT / "target" / "debug" / "run.exe",
        REPO_ROOT / "target" / "release" / "run",
        REPO_ROOT / "target" / "release" / "run.exe",
    ]
    for candidate in candidates:
        if _is_executable_file(candidate):
            return candidate

    pytest.fail(
        "Compiled 'run' binary not found in target/debug/run or target/release/run "
        "(and RUN_BIN is unset). Build with `cargo build` or set RUN_BIN."
    )


def _build_sanitized_env(
    home: Path,
    config_home: Path,
    cache_home: Path,
) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, val in os.environ.items():
        upper = key.upper()
        if upper.startswith("RUN_"):
            continue
        if upper in {"NO_COLOR", "VIRTUAL_ENV"}:
            continue
        if upper in DANGEROUS_ENV_VARS or upper.startswith(DANGEROUS_ENV_PREFIXES):
            continue
        env[key] = val

    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["XDG_CACHE_HOME"] = str(cache_home)
    env["APPDATA"] = str(config_home)
    env["LOCALAPPDATA"] = str(cache_home)
    env["PATH"] = clean_path(env.get("PATH"))

    # Preserve rustup/cargo toolchain locations when HOME is isolated for tests.
    real_home = Path.home()
    rustup_home = os.environ.get("RUSTUP_HOME") or str(real_home / ".rustup")
    cargo_home = os.environ.get("CARGO_HOME") or str(real_home / ".cargo")
    if Path(rustup_home).is_dir():
        env["RUSTUP_HOME"] = rustup_home
    if Path(cargo_home).is_dir():
        env["CARGO_HOME"] = cargo_home
    return env


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, Any]:
    """Create isolated HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME, and workspace dirs."""
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    cache_home = tmp_path / "cache"
    work = tmp_path / "work"
    for directory in (home, config_home, cache_home, work):
        directory.mkdir(parents=True, exist_ok=True)

    return {
        "home": home,
        "config_home": config_home,
        "cache_home": cache_home,
        "work": work,
        "env": _build_sanitized_env(home, config_home, cache_home),
    }


@pytest.fixture
def work_dir(isolated_env: dict[str, Any]) -> Path:
    """Return the per-test isolated workspace directory."""
    return isolated_env["work"]


def _prepare_env(
    base_env: Mapping[str, str],
    env_overrides: Mapping[str, str | None] | None,
    strip_env: Sequence[str] | None,
) -> dict[str, str]:
    env = dict(base_env)
    if strip_env:
        for key in strip_env:
            env.pop(key, None)
    if env_overrides:
        for key, val in env_overrides.items():
            if val is None:
                env.pop(key, None)
            else:
                env[key] = str(val)
    return env


@pytest.fixture
def run_cli(
    isolated_env: dict[str, Any],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Invoke the compiled `run` CLI binary in an isolated subprocess."""

    def _run(
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        stdin_text: str | None = None,
        env_overrides: Mapping[str, str | None] | None = None,
        strip_env: Sequence[str] | None = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        binary = resolve_run_binary()
        run_cwd = Path(cwd) if cwd is not None else isolated_env["work"]
        env = _prepare_env(isolated_env["env"], env_overrides, strip_env)
        return subprocess.run(
            [str(binary), *args],
            cwd=str(run_cwd),
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )

    return _run


@pytest.fixture
def spawn_cli(
    isolated_env: dict[str, Any],
) -> Iterator[Callable[..., subprocess.Popen[str]]]:
    """Spawn the `run` CLI binary for streaming/watch-mode tests with guaranteed cleanup."""
    spawned: list[subprocess.Popen[str]] = []

    def _spawn(
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_overrides: Mapping[str, str | None] | None = None,
        strip_env: Sequence[str] | None = None,
    ) -> subprocess.Popen[str]:
        binary = resolve_run_binary()
        run_cwd = Path(cwd) if cwd is not None else isolated_env["work"]
        env = _prepare_env(isolated_env["env"], env_overrides, strip_env)
        kwargs: dict[str, Any] = {
            "cwd": str(run_cwd),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen([str(binary), *args], **kwargs)
        spawned.append(proc)
        return proc

    try:
        yield _spawn
    finally:
        for proc in spawned:
            if proc.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGTERM)
                    else:
                        proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        if os.name == "posix":
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
