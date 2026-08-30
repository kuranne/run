# Auto Compiler & Runner

A smart, high-performance build system, test runner, and developer productivity tool. It handles automatic language detection, compilation, execution, testing, benchmarking, and cleanup across multiple programming languages.

---

## Table of Contents

- [Features](#-features)
- [Supported Languages](#-supported-languages)
- [Installation](#-installation)
- [Updating](#-updating)
- [Basic Usage](#-basic-usage)
- [Essential Daily Flags](#-essential-daily-flags)
- [Configuration (`Run.toml`)](#-configuration-runtoml)
- [Documentation Hub](#-documentation-hub)

---

## Features

- **Multi-Language Support**: Automatically detects and runs C/C++, Rust, Python, Java, Go, Zig, Node.js, Ruby, Perl, Lua, and Bash.
- **Smart Compilation**: Compiles C/C++/Rust/Java automatically before execution and cleans up temporary binaries.
- **CPM (C Project Manager)**: Multi-file compilation with automatic `main()` entrypoint detection regardless of file ordering.
- **Built-in Testing & Benchmarks**: Real-time execution time (`-t`), peak memory tracking (`-M`), output expectation diffs (`--expect`), and batch testcase runner (`--test-dir`).
- **Interactive Debuggers & Sanitizers**: One-flag launch for LLDB, GDB, Python pdb, Node inspector, Valgrind, AddressSanitizer, and ThreadSanitizer.
- **Template Scaffolding**: Generate single-file starter code or multi-file project bundles (`run --new`).
- **Intelligent Caching**: Fast incremental compilation with automatic cache management.
- **Shell Autocompletion**: Tab completion for Zsh (including `_evalcache`), Bash, Fish, and PowerShell.
- **Cross-Platform**: Full support for Linux, macOS, and Windows.

---

## Supported Languages

| Language | Extension | Method | Default Runner |
|----------|-----------|--------|----------------|
| **C** | `.c` | Compiler | `gcc` / `clang` |
| **C++** | `.cpp`, `.cc`, `.cxx` | Compiler | `g++` / `clang++` |
| **Rust** | `.rs` | Compiler / Cargo | `rustc` / `cargo` |
| **Java** | `.java` | Compiler | `javac` → `java` |
| **Python** | `.py` | Interpreter | Auto-detects `.venv` / `python3` |
| **Go** | `.go` | Compiler / Runner | `go` |
| **Zig** | `.zig` | Compiler | `zig` |
| **JavaScript / TypeScript** | `.js`, `.ts` | Interpreter | `node` |
| **Bash / Shell** | `.sh` | Interpreter | `bash` / `sh` |
| **Ruby / Perl / Lua** | `.rb`, `.pl`, `.lua` | Interpreter | System default |
| **Custom** | Custom | Custom | Configurable via `Run.toml` |

---

## Installation

### Requirements
- **Python 3.11+**
- **Git**
- Linux, macOS, or Windows

### Linux / macOS
```bash
git clone https://github.com/kuranne/run.git ~/.local/share/run_kuranne
cd ~/.local/share/run_kuranne
./setup.sh
```

### Windows (PowerShell)
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
git clone https://github.com/kuranne/run.git "$HOME\AppData\Local\run_kuranne"
cd "$HOME\AppData\Local\run_kuranne"
.\setup.ps1
```

---

## Updating

To update `run` to the latest version:

```bash
cd ~/.local/share/run_kuranne  # Or your installation directory
git pull
./setup.sh                    # On Windows: .\setup.ps1
```

---

## Basic Usage

```bash
run <files...> [options] [-- <program-args...>]
```

### Quick Examples

```bash
# Run a Python script (auto-detects virtualenv)
run script.py

# Compile and run a C++ file with execution timing and memory tracking
run solution.cpp -tM

# Pipe input directly into program
echo "10 20" | run solution.py -i

# Run with an interactive debugger (LLDB on macOS, GDB on Linux)
run main.cpp --debug

# Scaffold a new file from template
run --new solution.cpp

# Diagnose system toolchains
run --doctor
```

---

## Common Flags

| Shorthand | Long Flag | Description |
|-----------|-----------|-------------|
| `-w` | `--watch` | Watch mode: re-compile and run on file change |
| `-t` | `--time` | Measure and display execution time |
| `-M` | `--mem`, `--memory` | Measure and display peak memory usage (combine as `-tM`) |
| `-i` | `--stdin [file]` | Redirect standard input from file or pipe |
| `-d` | `--dry-run` | Preview commands without executing |
| `-m` | `--multi` | Compile multiple source files together |
| `-p` | `--preset <name>` | Use a build preset from `Run.toml` (e.g. `debug`, `release`) |
| `-j` | `--jobs <N>` | Number of parallel compilation threads |
| `-q` | `--quiet` | Silence compiler output and banners |
| `-v` | `--verbose` | Enable verbose debug logging (`-v`) or trace (`-vv`) |
| `-f` | `--force` | Force continue on errors / non-interactive mode |
| `-V` | `--version` | Display installed version |
| `-h` | `--help` | Show help screen |

> For all advanced flags (`--debug`, `--asan`, `--valgrind`, `--test-dir`, `--expect`, `--build-only`), see the [UNIX Man Page](docs/man/run.1) or the [Documentation Hub](#-documentation-hub).

---

## Configuration (`Run.toml`)

### Configuration File Hierarchy

`run` searches for configuration in the following order:
1. **Workspace Directory** (Highest Priority): `./Run.toml`, `../Run.toml`, up to 4 parent levels.
2. **Global Config Directory** (Lowest Priority):
   - Linux/macOS: `~/.config/run_kuranne/Run.toml` (or `$XDG_CONFIG_HOME/run_kuranne/Run.toml`)
   - Windows: `%APPDATA%\run_kuranne\Run.toml`

### Basic `Run.toml` Example

```toml
[runners]
cpp = "clang++"
c = "clang"

[presets.debug]
cpp = ["-g", "-Wall", "-Wextra", "-std=c++20"]

[presets.release]
cpp = ["-O3", "-Wall", "-std=c++20"]

[tasks]
test = "pytest tests/"
build = "cargo build --release"
```

Initialize a template config for your project with:
```bash
run --init
```



## Documentation Hub

Explore detailed topic guides and documentation:

- **[UNIX Man Page (`docs/man/run.1`)](docs/man/run.1)** - Complete CLI reference manual (`man ./docs/man/run.1`).
- **[Configuration Guide (`docs/configuration.md`)](docs/configuration.md)** - Full `Run.toml` schema, runners, custom languages, and task runner.
- **[Debugging & Sanitizers Guide (`docs/debugging_and_sanitizers.md`)](docs/debugging_and_sanitizers.md)** - Interactive debugging (LLDB, GDB, pdb), Valgrind, and ASan/TSan sanitizers.
- **[Testing & Benchmarking Guide (`docs/testing_and_benchmarking.md`)](docs/testing_and_benchmarking.md)** - Batch test runner (`--test-dir`), output diffs (`--expect`), and benchmarking.
- **[Templates & Scaffolding Guide (`docs/templates_and_scaffolding.md`)](docs/templates_and_scaffolding.md)** - Single-file and multi-file code generator (`run --new`).
- **[Shell Autocompletion Guide (`docs/completions.md`)](docs/completions.md)** - Tab completion setup for Zsh (`_evalcache`), Bash, Fish, and PowerShell.
- **[Troubleshooting & Tips (`docs/troubleshooting.md`)](docs/troubleshooting.md)** - Diagnostic scanner, error fixes, and performance tips.
