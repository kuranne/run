"""Black-box MCP stdio server (`run --mcp`) protocol and tool-call tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from conftest import resolve_run_binary


def _send_msg(proc: subprocess.Popen[str], msg: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _read_msg(proc: subprocess.Popen[str]) -> dict[str, Any]:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line, "MCP server closed stdout unexpectedly"
    return json.loads(line)


def test_mcp_stdio_initialize_tools_resources_and_run_code(
    work_dir: Path, isolated_env: dict[str, Any]
) -> None:
    binary = resolve_run_binary()
    (work_dir / "hello.py").write_text('print("MCP_PY_OK")\n', encoding="utf-8")

    proc = subprocess.Popen(
        [str(binary), "--mcp", "--mcp-root", str(work_dir)],
        cwd=str(work_dir),
        env=isolated_env["env"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        # 1. Initialize handshake
        _send_msg(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-mcp", "version": "1.0.0"},
                },
            },
        )
        init_resp = _read_msg(proc)
        assert init_resp["id"] == 1
        assert "result" in init_resp
        assert init_resp["result"]["serverInfo"]["name"] == "run"

        _send_msg(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2. List all 7 tools
        _send_msg(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = _read_msg(proc)
        tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
        assert {
            "run_code",
            "run_tests",
            "bench_code",
            "stress_test",
            "doctor",
            "clean_cache",
            "init_project",
        }.issubset(tool_names)

        # 3. List & read run://json-schema resource
        _send_msg(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
        res_list = _read_msg(proc)
        uris = {r["uri"] for r in res_list["result"]["resources"]}
        assert "run://json-schema" in uris

        _send_msg(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/read",
                "params": {"uri": "run://json-schema"},
            },
        )
        res_read = _read_msg(proc)
        schema_doc = json.loads(res_read["result"]["contents"][0]["text"])
        assert schema_doc["title"] == "RunJsonReport"

        # 4. Call run_code
        _send_msg(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "run_code",
                    "arguments": {"targets": ["hello.py"]},
                },
            },
        )
        call_resp = _read_msg(proc)
        assert call_resp["result"].get("isError") in (False, None)
        report = json.loads(call_resp["result"]["content"][0]["text"])
        assert report["ok"] is True
        assert "MCP_PY_OK" in report["run"]["stdout"]

        # 5. Verify --mcp-root confinement blocks escaping path
        _send_msg(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "run_code",
                    "arguments": {"targets": ["../escape.py"]},
                },
            },
        )
        escape_resp = _read_msg(proc)
        assert "error" in escape_resp
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=5)
