# Contributing to `run`

Thank you for contributing to `run`! This guide covers the development setup, repository layout, coding standards, testing workflow, and how to open issues or pull requests.

---

## Development Setup

### Prerequisites
- **Rust toolchain** (`cargo`, `rustc` — Edition 2024)
- **Python 3** + `pytest` (for black-box and E2E test suites)
- **Git**
- Optional: `gcc` / `g++` / `clang`, `go`, `javac`, `fzf`, `docker` / `podman` for full local language & sandbox tests

### Quick Start
```bash
git clone https://github.com/kuranne/Run.git
cd Run
cargo xtask test    # Runs Rust workspace tests, release build, and pytest suite
cargo xtask lint    # Runs cargo clippy and cargo doc with -D warnings
```

You can also use `Run.toml` tasks once `run` is installed:
```bash
run test
run lint
run install
```

---

## Repository Structure

```text
src/
├── main.rs            # Thin CLI entrypoint, dispatch, watch loop, fzf picker, --json/--mcp wrapper
├── lib.rs             # Public crate root: core, mcp, model, tui, util
├── mcp/               # Model Context Protocol (MCP) stdio server (--mcp, --mcp-root)
├── model/             # Pure data schemas, errors, options, reports, and trait abstractions
│   ├── config.rs      # Run.toml structs
│   ├── error.rs       # RunError enum
│   ├── metadata.rs    # Cargo.toml [package.metadata.run] structs
│   ├── options.rs     # RunnerOptions, UiMode, BenchStats, CommandExecutor
│   ├── report.rs      # JsonReport (--json) and Draft 2020-12 JSON Schema
│   └── sandbox.rs     # HostOs, ExecOutput, SandboxEnv
├── core/              # Execution engines & language handlers
│   ├── base.rs        # BaseRunner, SystemCommandExecutor, metrics, diffs, cleanup
│   ├── bench.rs       # Micro-benchmarking (--bench / --warmup)
│   ├── engine.rs      # CompilerRunner, source discovery, direct binary execution
│   ├── project.rs     # ProjectRunner & TaskRunner
│   ├── registry.rs    # LanguageHandler trait & HandlerRegistry
│   ├── stress.rs      # Differential fuzzing (--stress / --checker)
│   ├── test_runner.rs # Batch testcase runner (--test-dir / --filter)
│   └── handlers/      # c_family, rust, java, python, script, custom
├── tui/               # Interactive ratatui workspace (--tui)
└── util/              # args, cache, completion, config, doctor, env, glob, output, process, sandbox, security, spinner, substitutions, templates

scripts/               # Cross-platform dev/test helpers (dev-check.sh, test-local.sh, test-docker.sh, test-windows.sh)
xtask/                 # Cross-platform Rust automation (install, uninstall, test, lint, completions)
tests/
├── conftest.py        # Shared pytest fixtures (isolated HOME/cache/config)
├── whitebox.rs        # Rust mockall integration suite (tests/whitebox/*.rs)
├── blackbox/          # Domain-grouped CLI black-box tests (cli/, config/, languages/, security/)
└── e2e/               # Tier 1–4 end-to-end integration suites
```

---

## Coding Standards

1. **Zero-Warning Policy**:
   - `#![warn(missing_docs)]` is enabled in `src/lib.rs` and `xtask/src/main.rs`. Every public module, struct, enum, field, and function must have doc comments (`///` / `//!`).
   - All PRs must pass:
     ```bash
     cargo clippy --workspace --all-targets -- -D warnings
     RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps
     ```
2. **Configuration & Defaults**:
   - Store static defaults (extensions, excludes, timeouts, security lists, heuristics) in `Cargo.toml` under `[package.metadata.run]` rather than hardcoding them in Rust source files.
3. **Security & Isolation**:
   - Preserve fail-closed behavior for dangerous environment variables (`LD_*`, `DYLD_*`), suspicious compiler flags (unless `--unsafe`), and workspace path containment checks.
   - Never delete user-provided binaries during `BaseRunner::cleanup()`.
4. **Testing Expectations**:
   - Add **Rust unit / `mockall` white-box tests** (`tests/whitebox/`) for internal logic, error paths, and edge cases.
   - Add **pytest black-box tests** (`tests/blackbox/<domain>/`) for CLI behavior and parity.
   - Keep `UiMode::Plain` (non-TTY / `--plain`) output backward-compatible.

---

## Git & Pull Request Workflow

1. **Branching**:
   - Branch off `main` (or the active feature integration branch) with a descriptive name: `feat/<topic>`, `fix/<topic>`, `refactor/<topic>`, or `docs/<topic>`.
2. **Conventional Commits**:
   - Use Conventional Commits:
     - `feat(scope): short summary`
     - `fix(scope): short summary`
     - `refactor(scope): short summary`
     - `docs(scope): short summary`
     - `test(scope): short summary`
     - `chore(scope): short summary`
   - Keep commits atomic and focused.
3. **Before Opening a PR**:
   - Run `cargo test --workspace`
   - Run `cargo clippy --workspace --all-targets -- -D warnings`
   - Run `RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps`
   - Run `pytest -p no:cacheprovider tests/` (with `RUN_BIN="$PWD/target/release/run"`)
4. **Opening an Issue**:
   - **Bug reports**: Include OS, `run --version`, `run --doctor` output, minimal reproduction file/command, and actual vs expected output.
   - **Feature requests**: Describe the user workflow, proposed CLI flags or `Run.toml` schema, and edge cases.
