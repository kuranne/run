# Changelog

All notable changes to the `run` project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
