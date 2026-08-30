import pytest
import sys
from pathlib import Path
from runner.core import CompilerRunner

def test_sanitizer_flags_injection():
    # ASan
    runner_asan = CompilerRunner({"asan": True, "dry_run": True})
    assert "-fsanitize=address,undefined" in runner_asan.extra_flags
    assert "-fno-omit-frame-pointer" in runner_asan.extra_flags

    # TSan
    runner_tsan = CompilerRunner({"tsan": True, "dry_run": True})
    assert "-fsanitize=thread" in runner_tsan.extra_flags

    # Custom sanitize
    runner_custom = CompilerRunner({"sanitize": "memory,leak", "dry_run": True})
    assert "-fsanitize=memory,leak" in runner_custom.extra_flags

def test_debugger_command_construction(monkeypatch):
    executed_cmds = []

    def mock_run_command(self, cmd, compiling=False, use_shell=False):
        executed_cmds.append(cmd)
        return True

    monkeypatch.setattr(CompilerRunner, "run_command", mock_run_command)

    bin_path = Path("app.out")

    # GDB
    executed_cmds.clear()
    runner_gdb = CompilerRunner({"gdb": True}, run_args="--port 8080")
    runner_gdb._execute_binary(bin_path)
    assert len(executed_cmds) == 1
    assert executed_cmds[0][0] == "gdb"
    assert executed_cmds[0][1] == "--args"
    assert "--port" in executed_cmds[0]

    # LLDB
    executed_cmds.clear()
    runner_lldb = CompilerRunner({"lldb": True})
    runner_lldb._execute_binary(bin_path)
    assert len(executed_cmds) == 1
    assert executed_cmds[0][0] == "lldb"
    assert executed_cmds[0][1] == "--"

    # Smart --debug
    executed_cmds.clear()
    runner_debug = CompilerRunner({"debug": True})
    runner_debug._execute_binary(bin_path)
    assert len(executed_cmds) == 1
    expected_dbg = "lldb" if sys.platform == "darwin" else "gdb"
    assert executed_cmds[0][0] == expected_dbg

    # Valgrind
    executed_cmds.clear()
    runner_valgrind = CompilerRunner({"valgrind": True})
    runner_valgrind._execute_binary(bin_path)
    assert len(executed_cmds) == 1
    assert executed_cmds[0][0] == "valgrind"
    assert "--leak-check=full" in executed_cmds[0]
    assert "--track-origins=yes" in executed_cmds[0]

def test_python_debug_command(monkeypatch):
    executed_cmds = []

    def mock_run_command(self, cmd, compiling=False, use_shell=False):
        executed_cmds.append(cmd)
        return True

    monkeypatch.setattr(CompilerRunner, "run_command", mock_run_command)

    runner = CompilerRunner({"debug": True}, run_args="arg1")
    runner._handle_python_execution(Path("script.py"))
    assert len(executed_cmds) == 1
    assert "-m" in executed_cmds[0]
    assert "pdb" in executed_cmds[0]
    assert "script.py" in executed_cmds[0]
    assert "arg1" in executed_cmds[0]

def test_node_debug_command(monkeypatch):
    executed_cmds = []

    def mock_run_command(self, cmd, compiling=False, use_shell=False):
        executed_cmds.append(cmd)
        return True

    monkeypatch.setattr(CompilerRunner, "run_command", mock_run_command)
    monkeypatch.setattr(CompilerRunner, "_get_interpreter_path", lambda self, candidates: "node")

    runner = CompilerRunner({"debug": True})
    runner._handle_node_execution(Path("app.js"))
    assert len(executed_cmds) == 1
    assert executed_cmds[0][0] == "node"
    assert executed_cmds[0][1] == "--inspect-brk"
    assert executed_cmds[0][2] == "app.js"
