# Changelog

All notable changes to the `run` project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-11-26

### ✨ MCP Server, Resource Limits, Task DAG, Cache Locking, CP Tools & REPL

Version `1.1.0` expands `run` for AI agents, competitive programming, and cross-platform task automation.

### Added
- **MCP Server Mode (`--mcp`, `--mcp-root`)**: Stdio MCP server powered by `rmcp` exposing 9 tools (`run_code`, `run_tests`, `bench_code`, `stress_test`, `doctor`, `clean_cache`, `init_project`, `list_tasks`, `run_task`), the `run://json-schema` resource, per-workspace `RwLock` concurrency control, and a 64 KB default `--max-output` cap.
- **Resource Limits & Output Guard**: Added `--mem-limit <SIZE>` (`B`, `K`/`KB`, `M`/`MB`, `G`/`GB`, bare MB) with cross-platform RSS enforcement and `memory_limit_exceeded` in `--json`, plus `--max-output <SIZE>` head/tail UTF-8 truncation (`truncated: true`).
- **Task Runner (`Run.toml`)**: Added `[vars]` (`${vars.*}`), structured `[tasks.<name>]` (`desc`, `cmd`/`cmds`, `deps` DAG with parallel leaf execution, `sources`/`generates` SHA-256 incremental caching, `params` mapped from `-- <argv>`, `platforms` + `[tasks.<name>.<os>]` overrides), task `--watch`, and `-l`/`--list`.
- **Competitive Programming & Testing**: Added `--cmp exact|tokens|float:<eps>` for `--expect`/`--test-dir`/`--stress`, `--interactive <INTERACTOR>` bidirectional judge mode, and `--compare <TARGET_B>` side-by-side benchmarking/testing.
- **TUI, Scratchpad/REPL & Dev Scripts**: Added `Space` multi-compile selection and `a` runtime args/limits modal in `--tui`, `-e`/`--eval <CODE> --lang <LANG>` (with auto-`main` wrapping) and `--repl <LANG>`, compact Modern output, updated Zsh/Bash/Fish/PowerShell completions (including `-L`/`--all`), and `scripts/` helpers (`dev-check.sh`, `test-local.sh`, `test-docker.sh`, `test-windows.sh`).

### Fixed & Improved
- **Cross-Process Cache Reliability**: Added OS advisory `cache.lock`, atomic temp-file+rename writes, reload-on-write merge, auto-GC of deleted source/artifact entries, age/LRU pruning, `--cache-stats`, and `--cache-gc`.
- **Cross-Platform Test & Windows Fixes**: Fixed Windows `--mem-limit` RSS enforcement parity, UTF-8 decoding in `tests/conftest.py`, Linux `read_procfs_vmhwm` compilation, and E2E skip guards for optional external tools.

---

## [1.0.0] - 2026-11-26

### 🚀 Complete Rust Rewrite & Interactive CLI/TUI Platform

Version `1.0.0` marks the full rewrite of `run` from Python into native Rust (Edition 2024), delivering faster startup, zero runtime dependencies, an interactive `ratatui` TUI dashboard, micro-benchmarking, differential stress testing, and machine-readable `--json` output for editors and AI agents.

### Added
- **Pure-Rust Core & Language Handlers**: Native compiler/runner engines for C, C++, Rust, Java, Python, Go, scripts, prebuilt binaries, and `PATH` commands (`-- <cmd>`).
- **Animated CLI & `--tui` Dashboard**: Vite/Clack-inspired animated CLI (`--plain` for CI/non-TTY) and interactive `ratatui` TUI with auto-rerun watch toggle (`w`), inline stdin editor (`i`), `--test-dir` picker, fuzzy sidebar filter (`/`), and collapsible test output (`Ctrl+O`).
- **Micro-Benchmarking (`--bench`, `--warmup`)**: Statistical timing (`min`, `median`, `mean`, `p95`, `max`) with sparkline visualization.
- **Differential Stress Testing (`--stress`, `--runs`, `--seed`, `--checker`)**: Automated generator/brute-force/custom-checker fuzzing with progress bar and mismatch reproduction.
- **Inline Input & Test Filtering**: Added `--stdin-text`, `--stdin-file`, dynamic `-i`/`--stdin`, and `--filter <glob>` for `--test-dir`.
- **Machine-Readable JSON (`--json`, `--json-schema`)**: Structured JSON reports (compile/run timing, peak RSS, exit code, per-testcase diffs, bench stats, stress results) plus `.agents/skills/run-cli/` for token-efficient AI agent workflows.
- **Cross-Platform Automation (`cargo xtask`)**: Pure-Rust `install`, `uninstall`, `test`, `lint`, and `completions` commands across Linux, macOS, and Windows.

### Fixed & Improved
- **Windows Process-Tree & Cleanup Reliability**: Added Win32 Job Objects (`KILL_ON_JOB_CLOSE` / `TerminateJobObject`) for `--timeout` grandchild cleanup and retry-on-lock `.exe` cleanup.
- **Documentation & Contributor Experience**: Added `CONTRIBUTING.md`, `AGENTS.md`, modular `docs/`, and `Dockerfile.test`.

---

## [0.2.1] - 2026-09-11

### 🛡️ Security, Stability & Process Lifecycle Hardening

Version `0.2.1` delivers comprehensive security hardening, process lifecycle robustness, signal propagation fixes, and sandbox boundary protections identified during in-depth security audits.

### Fixed & Improved

- **Process Lifecycle & Signal Propagation**:
  - Intercepted `KeyboardInterrupt` (Ctrl+C) under detached sessions (`-M`), forwarding `SIGINT` to the child process group before escalating and exiting without orphaning background processes (`SEC-R3-01`).
  - Added process tree termination on timeout (`kill_process_tree`) terminating multi-process grandchild trees (`gcc`, `make -j`, `cargo`) on POSIX (`os.killpg`) and Windows (`taskkill /F /T`) (`SEC-R3-02`).
  - Added graceful termination escalation: sends `SIGTERM` first with a grace period before escalating to `SIGKILL` (`SEC-R3-05`).
  - Enforced `--timeout` uniformly on compilation steps, raising `CompilationError` on compiler freeze (`SEC-R3-06`).
  - Eliminated the 10,000 Hz spin-wait loop in `MonitoredPopen._try_wait` on Linux, replacing it with direct blocking `os.wait4` (`SEC-R3-07`).
  - Prevented return code forgery on `ChildProcessError`, ensuring un-reapable child failures do not forge exit code 0 (`SEC-R3-08`).
  - Wrapped stdin file opening in outer `try...finally`, eliminating file descriptor leaks on early configuration errors (`SEC-R3-10`).
  - Hardened `ProcfsSampler` background polling interval to 1 ms (1,000 Hz), added `threading.Lock` thread-safety, and validated `/proc/[pid]/stat` process starttime against PID recycling races (`SEC-R3-11`, `SEC-R3-12`).
  - Configured child subreaper (`PR_SET_CHILD_SUBREAPER`) on Linux container environments (`SEC-R3-13`).
- **Sandbox Security & Container Isolation**:
  - Chained previous signal handlers and added reentrancy guards in `ComposeSandbox` and `PersistentSandbox` (`SEC-R3-09`).
  - Restricted Linux `bwrap` mounts from exposing host home directories, user credentials, and Docker daemon sockets (`SEC-R1-02`).
  - Enforced fail-closed behavior for `--restrict` on unsupported macOS configurations (`SEC-R1-01`).
  - Isolated Dockerfile auto-building in staged temporary build directories with context hashing and container name collision defense (`SEC-R1-03`, `SEC-R1-04`, `SEC-R1-05`).
- **Path Traversal, Pattern & Environment Hardening**:
  - Enforced strict workspace boundary verification on `--expect` file paths (`SEC-R2-009`).
  - Sanitized and quoted template variable and environment variable expansions (`SEC-R2-010`, `SEC-R2-011`, `SEC-R2-012`).
  - Replaced naive shell command concatenations with lexical analyzer parsing (`SEC-R2-013`, `SEC-R2-014`).
  - Added ReDoS protection for glob patterns, prevented directory traversal in template file generators, and verified virtualenv ownership (`SEC-R2-006`, `SEC-R2-008`, `SEC-R2-016`, `SEC-R2-017`, `SEC-R2-018`).

---

## [0.2.0] - 2026-09-09

### 🚀 Highlights

Version `0.2.0` is a major feature and reliability milestone bringing native and containerized sandboxing, out-of-the-box Go support with multi-file template compilation, full gitignore-style glob matching in configuration, robust per-process memory tracking isolation, and automated GitHub Actions CI.

### Added

- **Sandboxed Execution & Container Integration**:
  - Native OS isolation via `--restrict` using `bwrap` (Bubblewrap) on Linux and `sandbox_init` / `setrlimit` on macOS.
  - Container sandboxing via `--sandbox` supporting Docker and Podman with automatic Dockerfile discovery and build caching.
  - Multi-container stack orchestration via Docker Compose integration.
  - Network isolation toggling with `--sandbox-net`.
- **Glob Pattern Engine (`Run.toml`)**:
  - Support for `exclude_files`, `exclude_dirs`, and `exclude_extensions` with standard wildcard (`*`) and recursive path (`**`) patterns.
  - Early directory pruning during filesystem traversal to accelerate large repository scans.
  - Wildcard project manifest detection (e.g. `file = "*.sln"`, `file = "*.csproj"`).
- **Out-of-the-Box Go & Template Multi-Compile**:
  - Native Go execution and package compilation (`go build . -o ...`).
  - Template variable expansion supporting both `${var}` and `{var}` syntax (`${file}`, `${files}`, `${dir}`, `${stem}`, `${out}`, `${flags}`, `${args}`).
  - Flexible multi-file compilation entrypoint resolution.
- **CI / CD Automation & Fedora Test Matrix**:
  - Multi-stage GitHub Actions CI workflow (`.github/workflows/ci.yml`) featuring gated fast-fail linting (`flake8`), macOS testing (`macos-latest`), and Fedora container testing via Docker Buildx with GitHub Actions caching (`type=gha`).
  - Upgraded test image (`Dockerfile.test`) to `fedora:latest` with pre-installed multi-language toolchains (GCC, Clang, Rust/Cargo, Go, Java OpenJDK, Python 3, Make, Valgrind).
- **Build System & Tooling**:
  - Comprehensive POSIX `Makefile` with targets for `test`, `lint`, `format`, `build`, and standalone binary generation via Nuitka.
  - Complete UNIX man page (`docs/man/run.1`) and comprehensive documentation guides under `docs/`.

### Fixed & Improved

- **Per-Process Memory Tracking Isolation**:
  - Completely redesigned memory measurement (`-M`, `-tM`) to record true per-process peak RSS independently, eliminating cumulative `RUSAGE_CHILDREN` memory accumulation across sequential test runs.
  - Implemented low-latency Linux `ProcfsSampler` polling `/proc/[pid]/status` (`VmHWM`) with inline sampling on process startup and wait.
- **Process Lifecycle & Signal Management**:
  - Added process group signal isolation (`os.killpg`) to eliminate orphan background processes and zombie workers.
- **Test Runner & CLI Enhancements**:
  - Natural numerical sorting for testcase input files (`01.in`, `02.in`, `10.in`).
  - Regex prefix matching for testcases in `--test-dir`.
  - Preserved CLI flags across batch test runs.
  - Resolved greedy swallowing of optional flag arguments in the CLI preprocessor.
- **Cache Management**:
  - Object directories are now cleaned up properly on `run --clean`, and EOF comments are safely handled during cache serialization.

---

## [0.1.0] - 2026-08-30

- Initial release of `run`: multi-language compilation, runner, CPM entrypoint detection, test runner, and scaffolding.
