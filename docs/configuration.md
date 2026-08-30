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

Add custom programming languages or override built-in behavior:

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
name = "kotlin"
extensions = [".kt"]
runner = "kotlinc"
type = "compiler"

[[languages]]
name = "perl"
extensions = [".pl"]
runner = "perl"
type = "interpreter"
```

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

## Project Exclusions (`[core]`)

Exclude specific files or extensions from multi-file compilation (`-m`) or auto-link (`--link-auto`):

```toml
[core]
exclude_files = ["benchmark.cpp", "test_private.cpp"]
exclude_extensions = [".md", ".txt", ".tmp"]
```

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
