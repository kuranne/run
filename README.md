# Auto Compiler & Runner

A configurable runner. It handles compilation, execution, and cleanup automatically after executed.  
Honesly, I lazied to compile then run for testing, so I made this tool.  

## Current version

Now version is `26.1.3`, hope you updated to latest version!

## Features

- **Auto-Detection**: detailed execution info with colored output.
- **Smart Compilation**: Automatically compiles C/C++/Rust before running.
- **Custom Language Support**: Add any language via `Run.toml` configuration.
- **Project Config (`Run.toml`)**: Define custom runners and flag preset.
- **Virtual Env Support**: Automatically detects `.venv` or `.env` and uses the local Python.
- **Multi-File Support**: Can link multiple C/C++/Java files.
- **TOML Config**: for each project, if define `Run.toml` inside, it will use it instead of default config.
- **Dry Run**: Simulate execution to check commands without running them.
- **Debug Logging**: Create log from compiling or running executable.  

## In Future Features (Maybe)

I have no plan yet... email me for your idea please.

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

| Flag                    | Description                                       |
| ----------------------- | ------------------------------------------------- |
| `-m`, `--multi`         | Compile multiple files together (C/C++/Java)      |
| `-p`, `--preset <name>` | Use a flag preset from `Run.toml`                 |
| `-L [depth]`            | Auto-find and link C/C++ source files             |
| `-d`, `--dry-run`       | Print commands without executing                  |
| `-t`, `--time`          | Show execution time                               |
| `-f <flags>`            | Pass extra flags to the compiler                  |
| `--keep`                | Keep the compiled binary (don't delete after run) |
| `--debug`               | Create log after running                          |
| `--unsafe`              | Allow running in root                             |
| `--version`             | Check current version (local)                     |
| `--update`              | Update  automaticly                               |

### Examples

**Run a Python script (auto-detects venv [default]):**

```bash
run script.py ...
```

**Run a C++ file with a preset:**

For C/C++ file(s), you may add linker flag(s) manually or create a preset and use it.

```bash
run main.cpp -p debug
```

**Auto-find and compile all must to compile first files in current dir:**

```bash
run -L <depth>
```

**Compile multiple Java files together then run:**

```bash
run Main.java Helper.java Utils.java -m
```

**Dry run to see what would happen before real run:**

```bash
run main.cpp -p release -d
```

## Configuration (Run.toml)

Create a `Run.toml` in your project root or in the script installation directory.

```toml
[runner]
# Override default runners
c = "clang"
cpp = "clang++"
python = "python3"
```

### Preset
Use this configure with `--preset <preset>` or `-p <preset>` to tell the binary use these flags:

```toml
[preset.debug]
c = "-g -Wall -Wextra"
cpp = "-g -Wall -Wextra -std=c++20"
rust = "-g"

[preset.release]
c = "-O3"
cpp = "-O3 -std=c++20"
rust = "-C opt-level=3"
```

## Custom Languages

You can add support for any languages by defining it in `Run.toml`. This allows you to use the runner with language beyond the built-in support.

### Interpreter Languages

For interpreted languages (like Ruby, Perl, PHP), set `type = "interpreter"`:

```toml
[language.ruby]
extensions = [".rb"]
runner = "ruby"
type = "interpreter"
```

Then run your script:

```bash
run script.rb
```

### Compiled Languages

For compiled languages (like Kotlin, Zig, D), set `type = "compiler"`:

```toml
[language.kotlin]
extensions = [".kt", ".kts"]
runner = "kotlinc"
type = "compiler"
flags = ["-include-runtime", "-d"]
```

The runner will compile first, then execute the binary automatically.  
