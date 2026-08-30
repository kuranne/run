# Debugging & Sanitizers Guide

`run` provides one-flag integration for interactive debuggers (LLDB, GDB, pdb, Node inspector), runtime memory checkers (Valgrind), and compiler sanitizers (AddressSanitizer, ThreadSanitizer).

## Interactive Debugging

### Smart Multi-Language Debugger (`--debug`)
The `--debug` flag automatically detects your operating system and programming language to launch the ideal interactive debugger:

- **C / C++**: Launches `lldb` on macOS or `gdb` on Linux/Windows.
- **Rust**: Launches `rust-lldb` / `rust-gdb` (or `lldb`/`gdb`) for single files and Cargo projects.
- **Python**: Launches `python3 -m pdb <script> <args>`.
- **Node.js**: Launches `node --inspect-brk <script> <args>`.

```bash
run main.cpp --debug
run app.py --debug
run server.js --debug
```

### Explicit Debuggers (`--gdb`, `--lldb`)
To force a specific debugger regardless of platform:

```bash
run main.c --lldb
run main.c --gdb
```

> [!NOTE]
> During an interactive debug session, `run` gives full direct terminal TTY access and automatically suspends execution timers (`-t`), peak memory tracking (`-M`), and timeouts.

## Memory Leak Analysis (`--valgrind`)

Run your compiled binaries under Valgrind with detailed leak checking enabled (`--leak-check=full --track-origins=yes`):

```bash
run server.c --valgrind
```

## Compiler Sanitizers

Instrument compiled code with native compiler sanitizers without manually configuring complex compiler flags:

### AddressSanitizer & UB Sanitizer (`--asan`)
Injects `-fsanitize=address,undefined -fno-omit-frame-pointer` into C/C++ compilation:
```bash
run solution.cpp --asan
```

### ThreadSanitizer (`--tsan`)
Injects `-fsanitize=thread` to detect data races in multi-threaded programs:
```bash
run multi_threaded_server.cpp --tsan
```

### Custom Sanitizer (`--sanitize <type>`)
Pass any sanitizer supported by Clang/GCC (e.g. `memory`, `leak`):
```bash
run program.c --sanitize memory
```
