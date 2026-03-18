# Auto Compiler & Runner

A configurable, intelligent build system and runner. It handles compilation, execution, and cleanup automatically for multiple programming languages and scripts.

Honestly, I was too lazy to compile then run for testing, so I made this tool to automate the workflow.

## Current version

Version `26.3.2` - with multi-language script support and improved configuration management!

## Features

- **Multi-Language Support**: Automatically detects and executes C/C++, Java, Python, Rust, Bash, Ruby, Node.js, Perl, Lua, and custom languages
- **Smart Auto-Detection**: Detects language by file extension or shebang line
- **Intelligent Compilation**: Automatically compiles C/C++/Rust/Java before running
- **Global Configuration**: Store `Run.toml` in XDG config directory (`~/.config/run_kuranne/`) or Windows APPDATA
- **Project-Level Override**: Project-specific `Run.toml` takes precedence over global config
- **Virtual Environment Support**: Auto-detects `.venv` or `.env` and uses local Python
- **Multi-File Compilation**: Compile multiple C/C++/Java files together with intelligent dependency tracking
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

_Note: This will set up a local virtual environment and add a `run` command to your PATH._

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

| Flag | Shorthand | Description |
|------|-----------|-------------|
| `--multi` | `-m` | Compile multiple files together (C/C++/Java) |
| `--preset <name>` | `-p <name>` | Use a preset from `Run.toml` (e.g., `debug`, `release`) |
| `--link-auto [depth]` | `-L [depth]` | Auto-find and compile source files (specify depth or leave empty for unlimited) |
| `--dry-run` | `-d` | Preview commands without executing them |
| `--time` | `-t` | Measure and display execution time |
| `--flags <flags>` | `-f <flags>` | Pass extra compiler/interpreter flags (use `-f="-Wall"` or `-f "-Wall"`) |
| `--argument <args>` | `-a <args>` | Arguments to pass to the executable/script |
| `--keep` | | Keep compiled binaries (don't delete after run) |
| `--no-cache` | | Disable build caching |
| `--debug` | | Enable verbose debug logging |
| `--unsafe` | | Allow running as root (⚠️ dangerous) |
| `--update` | `-u` | Update to latest version automatically |
| `--version` | | Show installed version |

### Usage Examples

**Run a Python script:**

```bash
run script.py
```

**Run a Bash script:**

```bash
run setup.sh
```

**Run a Node.js script with arguments:**

```bash
run app.js -a "arg1 arg2"
```

**Compile and run C++ with debug preset:**

```bash
run main.cpp -p debug
```

**Compile multiple C++ files:**

```bash
run main.cpp helper.cpp utils.cpp -m
```

**Compile with custom flags:**

```bash
run program.c -f="-Wall -O2"
```

**Auto-find and compile all C++ files in current directory:**

```bash
run -L
```

**Dry run to verify commands:**

```bash
run main.cpp -p release -d
```

**Measure execution time:**

```bash
run script.py -t
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
[runner]
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
[language.c]
extensions = [".c"]
runner = "clang"
type = "compiler"

[language.cpp]
extensions = [".cpp", ".cc", ".cxx"]
runner = "clang++"
type = "compiler"

[language.rust]
extensions = [".rs"]
runner = "cargo"
type = "compiler"

[language.python]
extensions = [".py"]
runner = "python3"
type = "interpreter"

[language.bash]
extensions = [".sh"]
runner = "bash"
type = "interpreter"

[language.ruby]
extensions = [".rb"]
runner = "ruby"
type = "interpreter"

[language.javascript]
extensions = [".js"]
runner = "node"
type = "interpreter"
```

### Build Presets

Define compile flags for different build profiles:

```toml
[preset.debug]
c = ["-g", "-Wall", "-Wextra"]
cpp = ["-g", "-Wall", "-Wextra", "-std=c++20"]
rust = ["-g"]
java = ["-g"]

[preset.release]
c = ["-O3", "-Wall"]
cpp = ["-O3", "-Wall", "-std=c++20"]
rust = ["-C", "opt-level=3"]
java = ["-O"]

[preset.strict]
c = ["-Wall", "-Wextra", "-Werror"]
cpp = ["-Wall", "-Wextra", "-Werror", "-std=c++20"]
```

### Custom Languages

Add support for any programming language by defining it in `Run.toml`:

```toml
[language.kotlin]
extensions = [".kt"]
runner = "kotlinc"
type = "compiler"

# With preset support
[preset.debug]
kotlin = ["-nowarn"]
```

Or for interpreted languages (don't forget to set `type = "interpreter"`):

```toml
[language.perl]
extensions = [".pl"]
runner = "perl"
type = "interpreter"

[language.lua]
extensions = [".lua"]
runner = "lua"
type = "interpreter"
```

### Exclude Patterns

Skip certain files or extensions when using wildcard compilation:

```toml
[exclude]
files = ["test_private.cpp", "benchmark.cpp"]
extensions = [".md", ".txt"]
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

Or delete `.run_cache/` directory manually and rebuild.

### Issue: Binary not found after compilation

**Possible causes**:

- Compilation failed (check error messages)
- Wrong output directory for Cargo projects
- Permission issues

**Solution**:

- Use `--dry-run` to verify commands
- Check `--debug` output for detailed information
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
