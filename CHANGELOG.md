# Changelog

All notable changes to the `run` project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
