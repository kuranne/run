---
name: run-cli
description: Use `run --json` (and the compact wrapper script) to compile, run, test, benchmark, or stress-test code with minimal output tokens and deterministic JSON parsing.
---

# `run` CLI Skill (Low-Token Agent Workflow)

Always prefer `run --json` (or `scripts/run_compact.py`) instead of manual compiler invocations or parsing human terminal output.

## Quick Commands

```bash
# Single compile & run (with inline stdin)
run --json <file> --stdin-text "5 10\n"

# Batch tests (optionally filtered)
run --json --test-dir <dir> --filter "edge_*" <file>

# Micro-benchmark (compile once, N runs + warmup)
run --json --bench 10 --warmup 2 <file>

# Differential stress test (stops on first mismatch, saves fail.in)
run --json <sol> --stress <gen> <brute> --runs 100

# Inspect exact output schema
run --json-schema
```

## Token-Saving Wrapper (`scripts/run_compact.py`)

When child stdout/stderr or test suites may be large, call the wrapper to strip empty fields, keep only failing testcases in full, and truncate long output:

```bash
python3 .agents/skills/run-cli/scripts/run_compact.py <file> [run-args...]
python3 .agents/skills/run-cli/scripts/run_compact.py --max-chars 400 -- --test-dir cases/ <file>
```

## Rules for Agents

1. **Always pass `--json`**: Guarantees valid JSON on `stdout` even on compile/config errors and disables interactive `fzf`/stdin prompts.
2. **Use `--stdin-text` or `-i "text\n"`**: Avoid writing throwaway input files for quick checks.
3. **Check `ok` + `errors` first**: If `ok` is `false`, inspect `errors`, `run.stderr`, `diff`, `tests.cases` (failing only), or `stress.failed_seed`.
4. **Never pass `--tui`**: Interactive TUI requires a human TTY.
