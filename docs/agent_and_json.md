# AI Agent & `--json` Integration Guide

`run --json` is designed for LLM agents, CI pipelines, and editor extensions that need deterministic, parseable results without ANSI codes or interactive prompts.

---

## 1. Self-Discovery (`--json-schema`)

An agent can inspect the exact Draft 2020-12 JSON Schema in one command:

```bash
run --json-schema
```

---

## 2. Guarantees of `--json`

- **Single JSON object on `stdout`**: Human logs, spinners, and banners are suppressed; child `stdout` and `stderr` are captured inside JSON fields so `json.loads()` / `jq` always succeeds.
- **Structured errors**: Even early failures (missing file, invalid `Run.toml`, unsupported extension) emit valid JSON with `"ok": false`, non-zero `"exit_code"`, and populated `"errors"`.
- **Strictly non-interactive**: Never spawns `fzf` or interactive stdin prompts.

---

## 3. Common Agent Recipes

```bash
# Compile & run with inline input
run --json main.c --stdin-text "5 10\n"

# Run filtered test suite and inspect failed cases
run --json --test-dir cases/ --filter "edge_*" main.c

# Micro-benchmark and inspect min/median/p95/max + peak RSS
run --json --bench 10 --warmup 2 main.c

# Differential fuzzing: get failing seed + saved counterexample paths in JSON
run --json sol.c --stress gen.py brute.py --runs 100
```

---

## 4. Token-Saving Reusable Skill & Wrapper Script

This repository includes a ready-to-use agent skill at `.agents/skills/run-cli/`:
- **Skill instructions**: [`.agents/skills/run-cli/SKILL.md`](../.agents/skills/run-cli/SKILL.md)
- **Compact wrapper**: [`.agents/skills/run-cli/scripts/run_compact.py`](../.agents/skills/run-cli/scripts/run_compact.py)

`run_compact.py` invokes `run --json`, strips empty fields, summarizes passing testcases while keeping failing case details, and truncates huge stdout/stderr payloads so LLM agents stay well within their context budget:

```bash
python3 .agents/skills/run-cli/scripts/run_compact.py main.c --stdin-text "5 10\n"
python3 .agents/skills/run_cli/scripts/run_compact.py --max-chars 300 -- --test-dir cases/ main.c
```

---

## 5. MCP Server Mode (`--mcp`)

`run --mcp` starts a Model Context Protocol (MCP) stdio server powered by `rmcp`, allowing Antigravity, Claude Desktop, Cursor, VS Code, or Codex to invoke `run` natively without spawning shell commands.

### Exposed Tools & Resources

| Kind | Name / URI | Description |
|------|------------|-------------|
| Tool | `run_code` | Compile and run files, projects, tasks, or `PATH` commands (`--json`) |
| Tool | `run_tests` | Run a `--test-dir` suite with optional `--filter` globbing |
| Tool | `bench_code` | Micro-benchmark with `--bench` and `--warmup` |
| Tool | `stress_test` | Differential fuzzing with `--stress <GEN> <BRUTE>` |
| Tool | `doctor` | Inspect installed compilers, runtimes, debuggers, and sandboxes |
| Tool | `clean_cache` | Clear `run`'s persistent compilation cache |
| Tool | `init_project` | Initialize `Run.toml` (`--init`) or scaffold a template (`--new`) |
| Resource | `run://json-schema` | JSON Schema for `run --json` structured reports |

### Client Configuration (`mcpServers`)

```json
{
  "mcpServers": {
    "run": {
      "command": "run",
      "args": ["--mcp", "--mcp-root", "/path/to/workspace"]
    }
  }
}
```

Pass `--mcp-root <DIR>` to confine all `cwd` and file path arguments within a specific workspace directory. Built-in `SecurityManager` rules remain enforced (`--unsafe` and `--force` are omitted from MCP tool schemas).
