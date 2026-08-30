# Auto Compiler & Runner

A configurable, intelligent build system and runner. It handles compilation, execution, and cleanup automatically for multiple programming languages and scripts.

Honestly, I was too lazy to compile then run for testing, so I made this tool to automate the workflow.

## Current version

Version `0.0.2` - Hot Fix patch!

## Features

- **Multi-Language Support**: Automatically detects and executes C/C++, Java, Python, Rust, Bash, Ruby, Node.js, Perl, Lua, and custom languages
- **Smart Auto-Detection**: Detects language by file extension or shebang line
- **Intelligent Compilation**: Automatically compiles C/C++/Rust/Java before running
- **Global Configuration**: Store `Run.toml` in XDG config directory (`~/.config/run_kuranne/`) or Windows APPDATA
- **Project-Level Override**: Project-specific `Run.toml` takes precedence over global config
- **Virtual Environment Support**: Auto-detects `.venv` or `.env` and uses local Python
- **Multi-File Compilation**: Compile multiple C/C++/Java files together. Includes **CPM (C Project Manager)** to intelligently detect your `main()` entry point regardless of file order!
- **Lightning Fast Lookup**: The `--link-auto` (`-L`) scanner aggressively ignores heavy directories (like `.git`, `node_modules`, `.venv`) for instant file discovery.
- **Custom Language Support**: Add any language via `Run.toml` configuration
- **Build Presets**: Define compile-time flags for different build profiles (debug, release, etc.)
- **Caching**: Intelligent build cache for faster recompilation (can be disabled with `--no-cache`)
- **Dry Run Mode**: Preview commands without executing them
- **Debug Logging**: Detailed logging for troubleshooting build issues
- **Security**: Runs safety checks (root detection, environment sanitization)
- **Cross-Platform**: Full support for Linux, macOS, and Windows

## Installation

### Requirements

- **Python 3.11+** (Required for TOML support)
- **Git**
- **Linux(POSIX base), MacOS, or Windows**
- **Compiler and/or Interpreter** [Optional]
- **Internet** (For downloading the repository)

### Linux / macOS

```bash
git clone https://github.com/kuranne/run.git ~/.local/share/run_kuranne
cd ~/.local/share/run_kuranne
./setup.sh
```

### Windows (PowerShell)

Before run this, ensure that you can run the script on PowerShell.

```powershell
Set-ExecutionPolicy RemoteSigned
```

```powershell
git clone https://github.com/kuranne/run.git "$HOME\AppData\Local\run_kuranne"
cd "$HOME\AppData\Local\run_kuranne"
.\setup.ps1
```

_Note: This will set up a local virtual environment and symlink the `run` command to `~/.local/bin` (Linux/macOS) or your PATH._

## Usage

```bash
run <files> [flags]
```

### Supported Languages

| Language | Extension | Method | Notes |
|----------|-----------|--------|-------|
| C | `.c` | Compiler | Uses gcc (configurable) |
| C++ | `.cpp`, `.cc` | Compiler | Uses g++ (configurable) |
| Java | `.java` | Compiler | Javac → Java |
| Rust | `.rs` | Compiler/Cargo | Auto-detects Cargo projects |
| Python | `.py` | Interpreter | Auto-detects venv |
| Bash/Shell | `.sh` | Interpreter | Detects bash or sh |
| Ruby | `.rb` | Interpreter | Requires ruby installed |
| Node.js | `.js` | Interpreter | Detects node or nodejs |
| Perl | `.pl` | Interpreter | Requires perl installed |
| Lua | `.lua` | Interpreter | Tries lua, falls back to luajit |
| Custom | Custom | Custom | Configurable via Run.toml |

### Available Flags

#### Daily Essential Shortcuts

| Flag | Shorthand | Description |
|------|-----------|-------------|
| `--watch` | `-w` | Re-compile and run on file change (press `c` to retry) |
| `--force` | `-f` | Force continue on errors / non-interactive mode |
| `--multi` | `-m` | Compile multiple files together (C/C++/Java) |
| `--preset <name>` | `-p <name>` | Use a preset from `Run.toml` (e.g., `debug`, `release`) |
| `--jobs <N>` | `-j <N>` | Number of parallel worker threads for multi-file compilation |
| `--stdin [file]` | `-i [file]` | Redirect standard input from file or shell pipe (`-i` or `-i <file>`) |
| `--time` | `-t` | Measure and display execution time |
| `--mem`, `--memory` | `-M` | Measure and display peak memory usage (usable as `-tM`) |
| `--dry-run` | `-d` | Preview commands without executing them |
| `--quiet` | `-q` | Silence compiler output and logs |
| `--verbose` | `-v, -vv` | Enable verbose debug logging (`-v`) or trace with stack traces (`-vv`) |
| `--version` | `-V` | Show installed version |
| `--help` | `-h` | Show help screen |

#### Specialized & Advanced Options

| Flag | Description |
|------|-------------|
| `--debug` | Launch interactive debugger (`lldb` on macOS, `gdb` on Linux, `pdb` for Python, `--inspect-brk` for Node.js) |
| `--gdb` | Launch interactive GDB debugger session |
| `--lldb` | Launch interactive LLDB debugger session |
| `--valgrind` | Run with detailed Valgrind memory leak checking (`--leak-check=full --track-origins=yes`) |
| `--asan` | Compile with AddressSanitizer and UndefinedBehaviorSanitizer (`-fsanitize=address,undefined`) |
| `--tsan` | Compile with ThreadSanitizer (`-fsanitize=thread`) |
| `--sanitize <type>` | Compile with custom sanitizer flags (e.g. `--sanitize memory,leak`) |
| `--build-only`, `--no-run`, `-B` | Compile binary without executing (preserves output binary) |
| `--expect <file>` | Automatically verify output and print line-by-line diffs against expected file |
| `--test-dir <dir>` | Run batch testcases (*.in + *.out) from directory and display summary table |
| `--doctor` | Run comprehensive system toolchain and compiler diagnostics |
| `--no-color` | Disable ANSI color formatting (also respects `NO_COLOR` env var) |
| `--argument <args>` | Arguments to pass to the executable/script (or use `--`) |
| `--flags <flags>` | Pass extra compiler/interpreter flags |
| `--compiler <bin>` | Override compiler or interpreter binary |
| `--out-dir <dir>` | Output directory for compiled binaries |
| `--link-auto [depth]` | Auto-find and compile source files (specify depth or leave empty for unlimited) |
| `--timeout <sec>` | Timeout in seconds for execution |
| `--env <KEY=VAL>` | Set environment variables |
| `--keep` | Keep compiled binaries (don't delete after run) |
| `--no-cache` | Disable build caching |
| `--new <target>` | Generate starter source code or multi-file project from template (e.g. `run --new solution.cpp`) |
| `--template <name>` | Specify custom template name defined in `Run.toml` |
| `--clean` | Clear local build cache and exit |
| `--init` | Initialize tailored `Run.toml` for the current project |
| `--directory <dir>`, `--cwd <dir>` | Change to directory before executing |
| `--unsafe` | Allow running as root (⚠️ dangerous) |

### Updating

To update `run` to the latest version:

```bash
cd ~/.local/share/run_kuranne  # Or your cloned directory
git pull
./setup.sh                    # On Windows: .\setup.ps1
```

### Usage Examples

**Code Scaffolding & Multi-File Template Generation:**

```bash
run --new solution.cpp            # Scaffolds C++ starter code
run --new Solution.java           # Scaffolds Java with 'public class Solution'
run --new ./problem1 --template leetcode_rs # Generates multi-file template (main.rs + solve.rs)
```

**Interactive Debugging:**

```bash
run main.cpp --debug              # Auto-launches LLDB on macOS or GDB on Linux
run app.py --debug                # Launches Python pdb session
run server.js --debug             # Launches Node.js with --inspect-brk
```

**Memory Leak & Sanitizer Analysis:**

```bash
run main.cpp --asan               # Compile and run with AddressSanitizer
run server.cpp --tsan             # Compile and run with ThreadSanitizer
run main.c --valgrind             # Run with full Valgrind leak checking
```

**Toolchain Diagnostics:**

```bash
run --doctor
```

**Piping input directly into program with `-i`:**

```bash
echo "test input 123" | run test.py -i
```

**Competitive programming benchmark (time, memory, stdin redirection):**

```bash
run solution.cpp -tM -i input.txt
```

**Verify output against expected answer file:**

```bash
run solution.cpp -i in.txt --expect out.txt -tM
```

**Run full testcase directory suite:**

```bash
run solution.cpp --test-dir ./testcases/ -tM
```

**Build binary without running (`--build-only`):**

```bash
run main.cpp --build-only --out-dir bin/
```

**Run a Python script with program arguments:**

```bash
run script.py -- --port 8080 --debug
```

**Compile multiple C++ files in parallel with 8 threads:**

```bash
run main.cpp helper.cpp utils.cpp -m -j 8
```

**Auto-find and compile all C++ files in current directory:**

```bash
run --link-auto
```

**Run from a specific project directory:**

```bash
run --directory ./subproject
```

**Clear build cache:**

```bash
run --clean
```

**Keep compiled binary for reuse:**

```bash
run main.cpp --keep
```

## Configuration

### Config File Location

The runner searches for `Run.toml` in the following order of priority:

1. **Project Directory** (highest priority): Current directory, checking up to 4 levels up
   - `./Run.toml`
   - `../Run.toml`
   - `../../Run.toml`
   - etc.

2. **Global Config Directory** (lowest priority):
   - **Linux/macOS**: `~/.config/run_kuranne/Run.toml` (respects `XDG_CONFIG_HOME`)
   - **Windows**: `%APPDATA%\run_kuranne\Run.toml` (typically `C:\Users\Username\AppData\Roaming\run_kuranne\Run.toml`)

Project-level configuration overrides global settings.

### Basic Configuration

Create `Run.toml` in your project root:

```toml
[runners]
# Override default compiler/interpreter commands
c = "clang"
cpp = "clang++"
python = "python3"
rust = "rustc"
java = "javac"
```

### Language Definitions

Override default language behavior or add custom language support:

```toml
[[languages]]
name = "c"
extensions = [".c"]
runner = "clang"
type = "compiler"

[[languages]]
name = "cpp"
extensions = [".cpp", ".cc", ".cxx"]
runner = "clang++"
type = "compiler"

[[languages]]
name = "rust"
extensions = [".rs"]
runner = "cargo"
type = "compiler"

[[languages]]
name = "python"
extensions = [".py"]
runner = "python3"
type = "interpreter"

[[languages]]
name = "bash"
extensions = [".sh"]
runner = "bash"
type = "interpreter"

[[languages]]
name = "ruby"
extensions = [".rb"]
runner = "ruby"
type = "interpreter"

[[languages]]
name = "javascript"
extensions = [".js"]
runner = "node"
type = "interpreter"
```

### Build Presets

Define compile flags for different build profiles:

```toml
[presets.debug]
c = ["-g", "-Wall", "-Wextra"]
cpp = ["-g", "-Wall", "-Wextra", "-std=c++20"]
rust = ["-g"]
java = ["-g"]

[presets.release]
c = ["-O3", "-Wall"]
cpp = ["-O3", "-Wall", "-std=c++20"]
rust = ["-C", "opt-level=3"]
java = ["-O"]

[presets.strict]
c = ["-Wall", "-Wextra", "-Werror"]
cpp = ["-Wall", "-Wextra", "-Werror", "-std=c++20"]
```

### Custom Languages

Add support for any programming language by defining it in `Run.toml`:

```toml
[[languages]]
name = "kotlin"
extensions = [".kt"]
runner = "kotlinc"
type = "compiler"

# With preset support
[presets.debug]
kotlin = ["-nowarn"]
```

Or for interpreted languages (don't forget to set `type = "interpreter"`):

```toml
[[languages]]
name = "perl"
extensions = [".pl"]
runner = "perl"
type = "interpreter"

[[languages]]
name = "lua"
extensions = [".lua"]
runner = "lua"
type = "interpreter"
```

### Exclude Patterns

Skip certain files or extensions when using wildcard compilation:

```toml
[core]
exclude_files = ["test_private.cpp", "benchmark.cpp"]
exclude_extensions = [".md", ".txt"]
```

When using `-L` or `-m`, files matching these patterns will be ignored.

## Script Execution & Shebang Detection

The runner supports executing scripts without explicit file extensions by detecting the shebang line. This is useful for script files without extensions or non-standard extensions.

### Supported Shebangs

Automatically detected:

```bash
#!/usr/bin/env python3     → Python
#!/bin/bash               → Bash
#!/usr/bin/env ruby       → Ruby
#!/usr/bin/env node       → Node.js
#!/usr/bin/env perl       → Perl
#!/usr/bin/env lua        → Lua
```

### Running Scripts Without Extensions

```bash
# Create executable script
cat > process_data << 'EOF'
#!/usr/bin/env python3
import sys
print("Processing:", sys.argv[1:])
EOF

chmod +x process_data

# Run it - automatically detects Python from shebang
run process_data arg1 arg2
```

## Troubleshooting

### Issue: "Interpreter not found"

**Cause**: The required interpreter/compiler isn't installed or not in PATH

**Solution**:

- Install the missing language/compiler
- Or override the runner in `Run.toml`:
```toml
[runner]
python = "/usr/bin/python3.11"  # full path
```

### Issue: "Unsupported extension"

**Cause**: File type not recognized

**Solution**:

- Add custom language to `Run.toml`
- Or check for typos in the filename

### Issue: Cached build not updating

**Cause**: Build cache may be out of date for header file changes

**Solution**:

```bash
run file.cpp --no-cache  # Skip cache this time
```

Or delete `~/.cache/run_kuranne/` directory manually and rebuild.

### Issue: Binary not found after compilation

**Possible causes**:

- Compilation failed (check error messages)
- Wrong output directory for Cargo projects
- Permission issues

**Solution**:

- Use `--dry-run` to verify commands
- Check `-v` or `-vv` output for detailed information
- Use `--keep` to preserve binaries for inspection

## Performance Tips

1. **Use presets** - Define commonly-used flags in `Run.toml`:

```bash
run main.cpp -p release  # Much faster than typing flags
```

2. **Multi-file compilation** - Compile multiple files at once:

```bash
run *.cpp -m  # Compiles all at once, not sequentially
```

3. **Leverage caching** - Build cache is automatic:

```bash
# First run: slow (full compilation)
run main.cpp
# Second run: fast (from cache, if unchanged)
run main.cpp
```

4. **Use dry-run first** - Test commands before executing:

```bash
run program.c -p release -d  # Preview what will happen
```  
