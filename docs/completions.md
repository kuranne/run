# Shell Autocompletion Guide

`run` provides dynamic tab completion for `zsh`, `bash`, `fish`, and `powershell`.

## Quick Setup

### Zsh

#### Standard Setup
Add the following line to your `~/.zshrc`:
```zsh
eval "$(run --completion zsh)"
```

#### Save to Fpath
```zsh
run --completion zsh > "${fpath[1]}/_run"
```

---

### Bash

Add the following to your `~/.bashrc`:
```bash
source <(run --completion bash)
```

---

### Fish

Save to your fish completions folder:
```fish
run --completion fish > ~/.config/fish/completions/run.fish
```

---

### PowerShell (Windows / pwsh)

Add to your PowerShell `$PROFILE`:
```powershell
run --completion powershell | Out-String | Invoke-Expression
```

---

## Dynamic Features

When autocompletion is active, pressing `TAB` provides live contextual suggestions:

- **Source Files & Tasks**: Suggests source files and project tasks defined under `[tasks]` in `Run.toml`.
- **Presets (`-p`, `--preset`)**: Dynamically queries and suggests preset names from `Run.toml` (e.g. `debug`, `release`).
- **Templates (`--template`)**: Dynamically suggests custom and built-in template names.
- **Flags**: Autocompletes all short and long flags with descriptive descriptions.
