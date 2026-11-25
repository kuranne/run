# `run` — Universal Compiler, Runner & Dev Toolkit

A fast, cross-platform Rust CLI for compiling, running, testing, benchmarking, stress-testing, and debugging code across multiple languages — with a Vite/Clack-style animated CLI, an interactive `ratatui` TUI, and machine-readable `--json` output for scripts and AI agents.

---

## Install

**Requirements:** Rust toolchain (`cargo` / `rustc`) and `git`.

```bash
git clone https://github.com/kuranne/Run.git ~/.local/share/run
cd ~/.local/share/run
cargo xtask install
```

Update anytime with `git pull && cargo xtask install` (or uninstall with `cargo xtask uninstall`).

---

## Quick Start

```bash
# Compile & run any source file (auto-cleans temporary binaries, even on Ctrl+C)
run main.c
run app.py -- arg1 arg2

# Run prebuilt local binaries or PATH commands with metrics (like `time <cmd>`)
run -t -M ./a.out
run -t -M -- ls -la

# Pass quick inline stdin, file stdin, or piped stdin dynamically
run main.c -i "5 10\n"
run main.c --stdin-file input.txt

# Batch test, micro-benchmark, or differential stress-test
run sol.cpp --test-dir cases/ --filter "edge_*"
run sol.cpp --bench 20 --warmup 3
run sol.cpp --stress gen.py brute.cpp --runs 200

# Interactive workspace TUI or fuzzy file finder (when no file is given)
run --tui
run

# Machine-readable JSON output for CI, editors, and LLM agents
run --json --test-dir cases/ sol.cpp
```

---

## Highlights

| Capability | Key Flags / Features |
|------------|----------------------|
| **Multi-Language & Binaries** | C, C++, Rust/Cargo, Java, Python (auto `.venv`), Go, Zig, Node/TS, Bash, Ruby, Perl, Lua, custom `[[languages]]`, C/C++ `#!` shebangs, prebuilt ELF/Mach-O/PE binaries, and `PATH` commands |
| **Smart Build Cache & CPM** | SHA-256 incremental cache, automatic `main()` multi-file linking (`-m`, `-L`), guaranteed cleanup on `SIGINT`/`SIGTERM` |
| **Testing & Fuzzing** | `--expect` unified diffs, `--test-dir` + `--filter`, `--bench [N]` + `--warmup`, `--stress <gen> <brute>` + `--checker` |
| **Modern UI, TUI & Plain** | Vite/Clack animated CLI + Braille spinners on TTY, `--plain` classic `[ TAG ]` mode, `ratatui` `--tui` (`Ctrl+O` collapsible tests, `/` fuzzy filter, `i` stdin editor, `d` test-dir picker, `w` auto-watch, `b` sparkline bench), `fzf` picker |
| **Debugging & Sandboxing** | `--debug` / `--gdb` / `--lldb` / `--valgrind` / `--asan` / `--tsan`, Docker/Podman `--sandbox`, Linux Bubblewrap `--restrict` |
| **Agent & CI Friendly** | `--json` single-object report, `--json-schema` Draft 2020-12 schema, reusable `.agents/skills/run-cli/` skill |

---

## Documentation Hub

| Guide | Topics Covered |
|-------|----------------|
| [Configuration (`Run.toml`)](docs/configuration.md) | `[core]`, `[runners]`, `[presets]`, `[tasks]`, `[projects]`, `[[languages]]`, `[sandbox]` |
| [Testing, Benchmarking & Stress](docs/testing_and_benchmarking.md) | `-t`/`-M`, `-i`/`--stdin-text`/`--stdin-file`, `--expect`, `--test-dir`/`--filter`, `--bench`, `--stress`/`--checker` |
| [UI, Animations & Interactive TUI](docs/ui_and_tui.md) | Vite/Clack CLI, `--plain`, `--tui` hotkeys, `Ctrl+O` collapsible tests, `fzf` finder |
| [Agent & `--json` Integration](docs/agent_and_json.md) | `--json` schema, `--json-schema`, CI/LLM workflows, token-saving `run_compact.py` |
| [Debugging & Sanitizers](docs/debugging_and_sanitizers.md) | `--debug`, `--gdb`, `--lldb`, `--valgrind`, `--asan`, `--tsan`, `--sanitize` |
| [Sandboxed Execution](docs/sandboxed_execution.md) | Docker/Podman `--sandbox`, Dockerfiles, Compose, persistent watch containers, `--restrict` |
| [Templates & Scaffolding](docs/templates_and_scaffolding.md) | `--new`, `--template`, `--init`, multi-file templates |
| [Shell Completions](docs/completions.md) | Zsh, Bash, Fish, PowerShell tab completion |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and `run --doctor` diagnostics |

---

## Contributing & AI Agents

- **Human contributors**: See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, coding standards, testing, and PR workflow.
- **AI coding agents**: See [AGENTS.md](AGENTS.md) and [.agents/skills/run-cli/SKILL.md](.agents/skills/run-cli/SKILL.md).

## License

MIT
