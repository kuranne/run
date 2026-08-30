# Run - Future Ideas & Roadmap Backlog

This document captures architectural concepts, user experience enhancements, and feature ideas for future iterations of `run`.

---

## 1. Shell Autocompletion (`run --completion <shell>`)
- **Category**: Developer Experience & CLI Polish
- **Description**: Generate shell completion scripts for `bash`, `zsh`, `fish`, and `powershell`.
- **Key Capabilities**:
  - Dynamic completion of presets from `Run.toml` (e.g. `run -p <TAB>` suggests `debug`, `release`, `fast`).
  - Dynamic completion of task names from `[tasks]` in `Run.toml` (e.g. `run <TAB>` suggests `test`, `build`, `lint`).
  - File extension-aware completion (e.g. suggesting `.cpp`, `.c`, `.py`, `.rs`, `.java`, `.go`, `.zig`).
- **Implementation Note**: Can be generated using `argcomplete` or custom script templates.

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

## 3. Code Template & Project Generator (`run --new <filename>`)
- **Category**: Scaffolding & Competitive Programming
- **Description**: Quickly generate starter code templates from built-in or user-configured templates in `Run.toml`.
- **Key Capabilities**:
  - **Competitive Programming Templates**: Fast I/O C++ templates, Python templates with `sys.stdin.readline()`, Java templates with `BufferedReader`.
  - **Customizable Templates in `Run.toml`**:
    ```toml
    [templates.cpp]
    content = """#include <iostream>
    using namespace std;

    int main() {
        ios_base::sync_with_stdio(false);
        cin.tie(NULL);
        // Solution here
        return 0;
    }"""
    ```
  - Bootstraps new files with `run --new solution.cpp`.
