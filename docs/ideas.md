# Run - Future Ideas & Roadmap Backlog

This document captures architectural concepts, user experience enhancements, and feature ideas for future iterations of `run`.

---

## 1. Shell Autocompletion (`run --completion <shell>`) [COMPLETED]
- **Category**: Developer Experience & CLI Polish
- **Status**: Implemented
- **Key Capabilities**:
  - Support for `zsh`, `bash`, `fish`, and `powershell`.
  - Dynamic completion of presets (`run -p <TAB>`), tasks (`run <TAB>`), and templates (`run --template <TAB>`).
  - Source file extension filtering.
  - 100% compatible with zsh eval caching (`_evalcache run run --completion zsh`).

---

## 2. Debugger & Sanitizer Suite (`--debug`, `--gdb`, `--lldb`, `--valgrind`, `--asan`, `--tsan`, `--sanitize`) [COMPLETED]
- **Category**: Low-Level & Systems Development
- **Status**: Implemented
- **Key Capabilities**:
  - `--debug`: Multi-language smart debugger launcher (`lldb` on macOS, `gdb` on Linux, `pdb` for Python, `--inspect-brk` for Node.js, `rust-lldb`/`rust-gdb` for Rust).
  - `--gdb` / `--lldb`: Explicit GDB or LLDB launcher.
  - `--valgrind`: Detailed runtime memory leak checking (`--leak-check=full --track-origins=yes`).
  - `--asan` / `--tsan` / `--sanitize <type>`: Compiler instrumentation for AddressSanitizer, ThreadSanitizer, and custom sanitizers.

---

## 3. Code Template & Project Generator (`run --new <target> [--template <name>]`) [COMPLETED]
- **Category**: Scaffolding & Competitive Programming
- **Status**: Implemented
- **Key Capabilities**:
  - `run --new <filename>`: Generates single-file starter code for C, C++, Python, Java, Rust, Go, Zig, JS, TS, Bash.
  - `run --new <dir> --template <name>`: Generates multi-file template bundles (e.g. LeetCode `main.rs` + `solve.rs` or C++ `main.cpp` + `solution.hpp`).
  - Customizable in `Run.toml` with `content = "..."`, `file = "..."`, or `files = [ { name = "...", content = "..." }, ... ]`.
  - Dynamic placeholder substitutions (`{{name}}`, `{{filename}}`, `{{date}}`, `{{year}}`).
