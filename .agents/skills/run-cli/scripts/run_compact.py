#!/usr/bin/env python3
"""Token-efficient wrapper around `run --json` for LLM agents.

Invokes `run --json ...`, strips empty/null keys, keeps only failing test cases
in full (while preserving pass/fail counts), and truncates oversized stdout,
stderr, or diff fields to keep agent context usage minimal.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any


def truncate_str(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}... [+{omitted} chars truncated]"


def compact_report(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": data.get("ok", False),
        "exit_code": data.get("exit_code", 1),
        "mode": data.get("mode", "run"),
    }

    if data.get("compile"):
        c = data["compile"]
        out["compile"] = {
            "s": round(c.get("duration_secs", 0.0), 4),
            "cached": c.get("cached", False),
        }

    if data.get("run"):
        r = data["run"]
        run_obj: dict[str, Any] = {"s": round(r.get("duration_secs", 0.0), 4)}
        if r.get("peak_memory_bytes"):
            run_obj["mem_mb"] = round(r["peak_memory_bytes"] / (1024 * 1024), 2)
        if r.get("timed_out"):
            run_obj["timed_out"] = True
        if r.get("stdout"):
            run_obj["stdout"] = truncate_str(r["stdout"], max_chars)
        if r.get("stderr"):
            run_obj["stderr"] = truncate_str(r["stderr"], max_chars)
        out["run"] = run_obj

    if data.get("tests"):
        t = data["tests"]
        failed_cases = [
            {
                "name": tc["name"],
                "expected": truncate_str(tc.get("expected", ""), max_chars),
                "actual": truncate_str(tc.get("actual", ""), max_chars),
            }
            for tc in t.get("cases", [])
            if not tc.get("passed")
        ]
        tests_obj: dict[str, Any] = {
            "passed": t.get("passed", 0),
            "failed": t.get("failed", 0),
            "total": t.get("total", 0),
        }
        if failed_cases:
            tests_obj["failures"] = failed_cases[:5]
        out["tests"] = tests_obj

    if data.get("bench"):
        b = data["bench"]
        out["bench"] = {
            "runs": b.get("runs"),
            "min_ms": round(b.get("min_secs", 0.0) * 1000, 3),
            "med_ms": round(b.get("median_secs", 0.0) * 1000, 3),
            "p95_ms": round(b.get("p95_secs", 0.0) * 1000, 3),
            "max_ms": round(b.get("max_secs", 0.0) * 1000, 3),
        }

    if data.get("stress"):
        s = data["stress"]
        stress_obj: dict[str, Any] = {
            "runs": s.get("runs"),
            "passed": s.get("passed"),
        }
        if not s.get("passed"):
            for k in ("failed_iteration", "failed_seed", "fail_in", "expected_out", "actual_out"):
                if s.get(k) is not None:
                    stress_obj[k] = s[k]
        out["stress"] = stress_obj

    if data.get("diff"):
        out["diff"] = data["diff"][:30]
    if data.get("warnings"):
        out["warnings"] = data["warnings"]
    if data.get("errors"):
        out["errors"] = data["errors"]

    return out


def resolve_run_bin(override: str | None) -> str:
    if override:
        return override
    env_bin = os.environ.get("RUN_BIN")
    if env_bin:
        return env_bin
    found = shutil.which("run")
    if found:
        return found
    return "./target/release/run"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compact token-saving wrapper around `run --json` for LLM agents."
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=500,
        help="Maximum characters to keep per stdout/stderr/expected/actual field (default: 500).",
    )
    parser.add_argument(
        "--run-bin",
        default=None,
        help="Path to `run` binary (defaults to $RUN_BIN, PATH `run`, or ./target/release/run).",
    )
    args, remainder = parser.parse_known_args()
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]

    run_bin = resolve_run_bin(args.run_bin)
    proc = subprocess.run(
        [run_bin, "--json", *remainder],
        capture_output=True,
        text=True,
    )

    try:
        parsed = json.loads(proc.stdout)
        compact = compact_report(parsed, args.max_chars)
        print(json.dumps(compact, separators=(",", ":")))
    except json.JSONDecodeError:
        fallback = {
            "ok": False,
            "exit_code": proc.returncode,
            "errors": [truncate_str(proc.stderr or proc.stdout or "Invalid JSON output", args.max_chars)],
        }
        print(json.dumps(fallback, separators=(",", ":")))

    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
