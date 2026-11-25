# AGENTS.md — AI Agent Guide for `run`

Concise architecture map, workflows, and verification commands for AI coding agents working in this repository.

---

## 1. Project Overview

`run` is a cross-platform Rust CLI compiler, runner, test harness, micro-benchmarker, differential fuzzer, and TUI workspace for C, C++, Rust, Java, Python, Go, scripts, prebuilt binaries, and `PATH` commands.

- **Crate**: `run` (Rust Edition 2024, library + binary) + `xtask` workspace member
- **Static Defaults**: `[package.metadata.run]` in `Cargo.toml` (embedded at compile time via `src/util/env.rs`)
- **User Config**: `Run.toml` (schema in `src/model/config.rs`, parser/validator in `src/util/config.rs`)

---

## 2. Source & Test Layout

| Path | Responsibility |
|------|----------------|
| `src/main.rs` | Thin CLI entrypoint, dispatch, `--watch`, `fzf` picker, `--json` wrapper |
| `src/model/` | Pure data models & traits: `config`, `error`, `metadata`, `options`, `report`, `sandbox` |
| `src/core/` | Execution engines: `base`, `engine`, `registry`, `project`, `test_runner`, `bench`, `stress` |
| `src/core/handlers/` | Language handlers: `c_family`, `rust_handler`, `java_handler`, `python_handler`, `script_handler`, `custom_handler` |
| `src/tui/` | `ratatui` interactive workspace (`mod.rs`, `app.rs`, `ui.rs`) |
| `src/util/` | `args`, `cache`, `completion`, `config`, `doctor`, `env`, `glob`, `init_config`, `output`, `process`, `sandbox`, `security`, `spinner`, `substitutions`, `template_manager` |
| `xtask/src/main.rs` | Cross-platform automation (`install`, `uninstall`, `test`, `lint`, `completions`) |
| `tests/whitebox.rs` | Cross-module `mockall` Rust integration tests (`tests/whitebox/*.rs`) |
| `tests/blackbox/` | Domain-grouped Python CLI black-box tests (`cli/`, `config/`, `languages/`, `security/`) |
| `tests/e2e/` | Tier 1–4 end-to-end pytest suites |
| `.agents/skills/run-cli/` | Reusable token-efficient skill + `scripts/run_compact.py` for agents using `run --json` |

---

## 3. Agent Workflow Rules

1. **Read narrow, edit surgical**: Locate exact symbols with `rg` before reading whole files.
2. **Keep `src/model/` pure**: Data structs, enums, and traits (`CommandExecutor`, `SandboxEnv`) belong in `src/model/`. Keep I/O and orchestration in `src/core/` and `src/util/`.
3. **Preserve strict gates**:
   - Every public item must have rustdoc comments (`#![warn(missing_docs)]`).
   - `UiMode::Plain` (non-TTY / `--plain`) output must remain backward-compatible with `tests/blackbox/` and `tests/e2e/`.
   - Never break `--json` stdout purity (no raw `println!` when `Printer::has_tui_sink()` is active).
4. **Use `mockall` for white-box tests**: Inject `CommandExecutor` / `SandboxEnv` mocks instead of spawning real compilers or Docker daemons in unit/white-box tests.
5. **Cleanup**: Remove `tests/**/__pycache__` before committing.

---

## 4. Essential Commands & Tools

```bash
# Fast Rust unit + white-box + xtask tests
cargo test --workspace -q

# Strict lint & doc gates (must pass with 0 warnings)
cargo clippy --workspace --all-targets -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps

# Release binary + Python black-box / E2E tests
cargo build --release
PYTHONPATH=.:tests RUN_BIN="$PWD/target/release/run" pytest -p no:cacheprovider tests/

# All-in-one via xtask
cargo xtask lint
cargo xtask test
```

### Using `run` as an Agent Tool (Low-Token Mode)
- Always pass `--json` (never parse ANSI/human output):
  ```bash
  run --json main.c
  run --json --test-dir cases/ --filter "edge_*" main.c
  run --json --bench 10 main.c
  run --json sol.c --stress gen.py brute.py --runs 100
  ```
- Inspect the schema in one call: `run --json-schema`
- For ultra-compact token-bounded output, use `.agents/skills/run-cli/scripts/run_compact.py` (see `.agents/skills/run-cli/SKILL.md`).
