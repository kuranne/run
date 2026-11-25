# UI Modes, Animations & Interactive TUI

`run` adapts automatically to your terminal environment while keeping script/CI output 100% deterministic.

---

## 1. CLI Presentation Modes

| Mode | Activation | Behavior |
|------|------------|----------|
| **Modern (Vite/Clack)** | Default on interactive TTYs | `◆` header banner, `│` vertical step rails, `✔`/`✖`/`⚠`/`◇`/`▸` glyphs, Braille compilation spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) with live elapsed timer, inline progress bar for `--test-dir`/`--bench`/`--stress`, `╰ ⏱` metrics footer |
| **Plain (`--plain`)** | `--plain` flag or non-TTY pipe/CI | Classic `[ INFO ]`, `[ COMPILE ]`, `[ RUN ]`, `[ PASS ]`, `[ FAIL ]` lines with zero spinner control codes |
| **JSON (`--json`)** | `--json` flag | Single self-contained JSON object on `stdout` (see [Agent & JSON Guide](agent_and_json.md)) |
| **TUI (`--tui`)** | `--tui` flag | Full-screen interactive `ratatui` workspace dashboard |

---

## 2. Interactive `fzf` File Finder

When `run` is invoked with no target files and no detected project manifest:
- On an interactive TTY with `fzf` installed on `PATH`, `run` scans runnable files (up to depth 4, respecting exclusions) and opens an interactive `fzf` picker (`run > `).
- When `fzf` is unavailable or stdio is piped, `run` falls back cleanly to a single-line prompt.

---

## 3. Interactive Workspace TUI (`run --tui`)

Launch the dashboard in any workspace (optionally pre-selecting a file or `--test-dir`):

```bash
run --tui
run --tui main.c --test-dir cases/
```

### Keybindings

| Key | Action |
|-----|--------|
| `↑` / `↓` or `j` / `k` | Navigate sidebar items, test cases, or scroll output |
| `Tab` | Switch focus between Sidebar and Main pane |
| `1` / `2` / `3` | Switch Main pane tabs (`Output`, `Diff`, `Tests`) |
| `r` or `Enter` | Compile & run the selected file or `[tasks]` entry |
| `b` | Micro-benchmark the selected file and render a live `Sparkline` |
| `Ctrl+O` | **Expand / collapse** test case output in the `Tests` tab (collapsed by default, agent-CLI style) |
| `/` | Built-in fuzzy filter (`fzf`-style) for the sidebar (`Enter` confirm, `Esc` clear) |
| `i` | Open multi-line **Stdin Editor** modal (`Enter` newline, `Ctrl+X` clear, `Esc`/`Ctrl+S` save) |
| `d` | Open **`--test-dir` Picker** modal to select or disable a testcase directory |
| `w` | Toggle **Watch Mode** — automatically re-runs the active file on save (including C/C++ headers and `Run.toml`) |
| `p` | Cycle `Run.toml` presets |
| `t` / `m` | Toggle `--time` / `--mem` metrics |
| `c` | Clear output, diffs, and test results |
| `q` or `Ctrl+C` | Exit cleanly and restore terminal state |
