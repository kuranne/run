# Configuration Guide (`Run.toml`)

`run` is deeply configurable via `Run.toml`. You can configure compiler overrides, custom languages, build presets, project tasks, and template bundles.


## Config File Discovery & Precedence

The runner searches for `Run.toml` in the following priority order:

1. **Workspace Directory**: Checks the current directory and up to 4 parent directories:
   - `./Run.toml`
   - `../Run.toml`
   - `../../Run.toml`
2. **Global Config Directory**:
   - **Linux / macOS**: `~/.config/run_kuranne/Run.toml` (or `$XDG_CONFIG_HOME/run_kuranne/Run.toml`)
   - **Windows**: `%APPDATA%\run_kuranne\Run.toml`

Workspace configurations take precedence over global settings.

## Runners Table (`[runners]`)

Override default compiler or interpreter binaries:

```toml
[runners]
c = "clang"
cpp = "clang++"
python = "python3"
rust = "rustc"
java = "javac"
```

## Language Definitions (`[[languages]]`)

Add custom programming languages, configure template-driven compiler commands, or override built-in behavior:

```toml
# Built-in Go language default (out-of-the-box)
[[languages]]
name = "go"
extensions = [".go"]
compile = "go build -o ${out} ${files}"
type = "compiler"

# Custom compiled language with template variables
[[languages]]
name = "csharp"
extensions = [".cs"]
compile = "csc /out:${out} ${files}"
run = "mono ${out}"
type = "compiler"

# Interpreted language with subcommand and flags
[[languages]]
name = "kotlin_script"
extensions = [".kts"]
runner = "kotlinc"
subcommand = "-script"
type = "interpreter"

# Simple compiled language definition
[[languages]]
name = "zig_c"
extensions = [".c"]
runner = "zig"
subcommand = "cc"
type = "compiler"
flags = ["-Wall", "-O2"]
```

### Template Substitution Variables
When defining `compile`, `run`, or `command` in `[[languages]]` (or `[projects]`), the following variables are automatically expanded:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `${file}` / `{file}` | Path to the target source file | `main.go` |
| `${files}` / `{files}` | All related source files (in multi-compile `-m` mode) | `add.go main.go parser.go` |
| `${dir}` / `{dir}` | Directory containing the source file | `./src` |
| `${stem}` / `{stem}` | Filename without extension | `main` |
| `${out}` / `{out}` | Output executable path (respects `-o` / `--out-dir` / cache) | `main.out` |
| `${flags}` / `{flags}` | Extra compiler/interpreter flags passed from CLI or presets | `-Wall -O3` |
| `${args}` / `{args}` | Runtime program arguments passed after `--` | `arg1 arg2` |

## Build Presets (`[presets]`)

Define flag bundles applied with `-p <name>` or `--preset <name>`:

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
```

Usage:
```bash
run main.cpp -p debug
run solution.cpp -p release
```

## Custom Project Tasks (`[tasks]`)

Define reusable commands in `Run.toml`:

```toml
[tasks]
test = "pytest tests/"
build = "cargo build --release"
lint = "flake8 src/"
```

Usage:
```bash
run test
run build
```

## Sandbox & Isolation (`[sandbox]`)

Configure container environments and orchestration when `--sandbox` is active or `[core] sandbox = true` is set:

```toml
[sandbox]
# Use a custom Docker/Podman image
image = "ubuntu:latest"

# Or auto-build dynamically from a local Dockerfile (hashes file to avoid redundant builds)
dockerfile = "./Dockerfile"

# Or integrate with Docker Compose (auto-manages up/exec/down)
compose = "docker-compose.yml"
compose_service = "app"
```

You can also enable sandboxing globally in `[core]` or per-task:

```toml
[core]
sandbox = true

[tasks.secure_test]
command = "pytest tests/ -v"
sandbox = true
```

See [Sandboxed & Isolated Execution Guide](sandboxed_execution.md) for full details.

## Project Manifest Detectors (`[projects]`)

When `run` is executed without arguments, it scans the current directory and up to 3 parent directories for known project manifest files and runs the project.

### Built-in Detectors
Out of the box, `run` recognizes:
- **Rust (`cargo`)**: `file = "Cargo.toml"`, `command = "cargo run -q"`
- **Go (`go`)**: `file = "go.mod"`, `command = "go run ."`
- **Zig (`zig`)**: `file = "build.zig"`, `command = "zig build run"`
- **CMake (`cmake`)**: `file = "CMakeLists.txt"`, `build = "cmake -B build && cmake --build build"`, `run = "./build/app"`
- **Make (`make`)**: `file = "Makefile"`, `command = "make"`

### Custom Projects & Glob Manifests
Define custom project workflows or use glob wildcards (e.g. `*.sln`, `*.csproj`):

```toml
# .NET Solution Runner with Glob Pattern
[projects.dotnet]
file = "*.sln"
command = "dotnet run --project ${file}"

# Gradle Project Runner
[projects.gradle]
file = "build.gradle"
command = "./gradlew run"

# Node.js / NPM Runner
[projects.node]
file = "package.json"
command = "npm start"
```

> [!NOTE]
> If a manifest glob matches multiple files in a directory (e.g., `App1.sln` and `App2.sln`), `run` logs an informational notice and deterministically selects the first file in alphabetical order.

## Project Exclusions & Glob Matching (`[core]`)

Control file and directory discovery across single runs, multi-file compilations (`-m`), and directory scans (`-L`):

```toml
[core]
# Gitignore-style file patterns
exclude_files = [
    "benchmark.cpp",         # Exact filename anywhere
    "*.tmp.c",               # Wildcard pattern matching filename anywhere
    "tests/**/mock_*.c",     # Recursive path pattern matching nested files
    "vendor/*"               # Single-level path wildcard
]

# Directory patterns pruned early during recursive directory traversal
exclude_dirs = [
    "vendor*",               # Prunes directories like vendor, vendor_libs
    "dist",                  # Prunes dist directory
    "tmp_*"                  # Prunes temporary build folders
]

# Flexible extension patterns
exclude_extensions = [
    ".md",                   # With leading dot
    "txt",                   # Without leading dot
    "*.bak",                 # Extension glob
    ".tmp*"                  # Wildcard extensions (.tmp1, .tmp2, etc.)
]
```

### Matching Rules
- **Filenames vs. Paths**: Patterns without slashes (e.g., `*.tmp`, `mock_*`) match against the filename directly. Patterns containing `/` (e.g., `tests/*`, `src/vendor/**`) match against relative paths from the workspace root.
- **Recursive Wildcards**: Supports `*` within a single directory segment and `**` across multiple directory levels.
- **Case Sensitivity**: All glob pattern matching is strictly case-sensitive across all operating systems.
- **Directory Pruning**: Patterns in `exclude_dirs` prune directories during traversal, avoiding unnecessary search overhead in ignored subtrees.

## Templates (`[templates]`)

Define starter code templates for `run --new`:

```toml
[templates.cp]
extension = ".cpp"
content = """#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);
    return 0;
}
"""
```

See [Templates & Scaffolding Guide](templates_and_scaffolding.md) for full details on multi-file templates.

