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

## 2. Debugger & Sanitizer Shortcuts (`--gdb`, `--lldb`, `--asan`)
- **Category**: Low-Level & Systems Development
- **Description**: Provide one-flag compilation and debugger integration for C, C++, Rust, and Zig.
- **Key Capabilities**:
  - `--asan`: Injects `-fsanitize=address,undefined -g` compiler flags automatically.
  - `--gdb`: Compiles with debug symbols (`-g`) and immediately attaches GDB in an interactive session.
  - `--lldb`: Compiles with debug symbols (`-g`) and attaches LLDB (ideal for macOS/Clang workflows).

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
