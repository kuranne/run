import os
import pytest
from pathlib import Path
from util.substitutions import VariableSubstitutor
from runner.custom_language_handler import CustomLanguageHandler
from runner.handler_interface import ExecutionContext

def test_build_file_context():
    src = Path("src/main.cpp")
    out = Path("build/main.out")
    ctx = VariableSubstitutor.build_file_context(file_path=src, out_path=out, out_dir="build")

    assert ctx["file"] == "src/main.cpp"
    assert ctx["filename"] == "main.cpp"
    assert ctx["name"] == "main"
    assert ctx["stem"] == "main"
    assert ctx["ext"] == ".cpp"
    assert ctx["dir"] == "src"
    assert ctx["out"] == "build/main.out"
    assert ctx["executable"] == "build/main.out"
    assert ctx["out_dir"] == "build"

def test_substitute_string_and_env(monkeypatch):
    monkeypatch.setenv("TEST_PORT", "9000")
    context = {"name": "app", "out": "bin/app"}

    s = VariableSubstitutor.substitute_string("run ${name} on port ${env:TEST_PORT} -> ${out}", context)
    assert s == "run app on port 9000 -> bin/app"

def test_substitute_list():
    context = {"out_dir": "target", "stem": "server"}
    template = ["-o", "${out_dir}/${stem}.bin", "-DNAME=${stem}"]
    result = VariableSubstitutor.substitute_list(template, context)

    assert result == ["-o", "target/server.bin", "-DNAME=server"]

class DummySubRunner(CustomLanguageHandler):
    def __init__(self):
        self.preset = None
        self.extra_flags = []
        self.run_args = []
        self.output_files = []
        self.executed_commands = []
        self.config = type("Config", (), {"get_preset_flags": lambda self, p, l: []})()

    def run_command(self, cmd, compiling=False, use_shell=False):
        self.executed_commands.append((cmd, compiling))
        return True

    def _execute_binary(self, bin_path, args=[]):
        self.executed_commands.append((["EXEC", str(bin_path)] + args, False))

def test_custom_language_substitution(tmp_path):
    runner = DummySubRunner()
    lang_config = {
        "name": "custom_c",
        "runner": "gcc",
        "type": "compiler",
        "flags": ["-Wall", "-I${dir}"],
        "arguments": ["--target=${name}"]
    }
    src_file = tmp_path / "app.ccstm"
    src_file.write_text("int main() {}")
    out_file = tmp_path / "app.out"

    ctx = ExecutionContext(
        flags={},
        extra_flags=[],
        run_args=[],
        preset=None,
        config=runner.config,
        cache=None,
        output_files=runner.output_files,
        is_posix=True,
        runner_ref=runner
    )

    runner._execute_custom(src_file, lang_config, out_file, ctx)

    assert len(runner.executed_commands) == 2
    comp_cmd, compiling = runner.executed_commands[0]
    assert f"-I{tmp_path}" in comp_cmd
    assert compiling is True

    exec_cmd, compiling = runner.executed_commands[1]
    assert exec_cmd == ["EXEC", str(out_file), "--target=app"]
