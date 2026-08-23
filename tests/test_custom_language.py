import pytest
from pathlib import Path
from runner.custom_language_handler import CustomLanguageHandler
from util.errors import ConfigError

from runner.handler_interface import ExecutionContext

class DummyCustomRunner(CustomLanguageHandler):
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

    def _get_dummy_context(self, tmp_path):
        return ExecutionContext(
            flags={},
            extra_flags=self.extra_flags,
            run_args=self.run_args,
            preset=self.preset,
            config=self.config,
            cache=None,
            output_files=self.output_files,
            is_posix=True,
            runner_ref=self
        )

def test_missing_runner_raises_error(tmp_path):
    runner = DummyCustomRunner()
    lang_config = {"name": "invalid", "type": "interpreter"}
    dummy_file = tmp_path / "test.inv"

    with pytest.raises(ConfigError, match="No runner specified"):
        runner._handle_custom_language(dummy_file, lang_config, tmp_path / "test.out")

def test_interpreter_custom_language(tmp_path):
    runner = DummyCustomRunner()
    lang_config = {
        "name": "python_custom",
        "runner": "python3",
        "subcommand": "-u",
        "type": "interpreter",
        "flags": ["-O"],
        "arguments": ["arg1"]
    }
    dummy_file = tmp_path / "script.pycstm"
    runner._handle_custom_language(dummy_file, lang_config, tmp_path / "script.out")

    assert len(runner.executed_commands) == 1
    cmd, compiling = runner.executed_commands[0]
    assert cmd == ["python3", "-u", "-O", str(dummy_file), "arg1"]
    assert compiling is False

def test_compiler_custom_language_multi(tmp_path):
    runner = DummyCustomRunner()
    lang_config = {
        "name": "c_custom",
        "runner": "clang",
        "type": "compiler",
        "flags": ["-Wall"],
        "arguments": ["run_arg"]
    }
    f1 = tmp_path / "a.cstm"
    f2 = tmp_path / "b.cstm"
    out_file = tmp_path / "a.out"

    runner._execute_custom_multi([f1, f2], lang_config, out_file, runner._get_dummy_context(tmp_path))

    assert len(runner.executed_commands) == 2
    comp_cmd, compiling = runner.executed_commands[0]
    assert comp_cmd == ["clang", "-Wall", str(f1), str(f2), "-o", str(out_file)]
    assert compiling is True

    exec_cmd, compiling = runner.executed_commands[1]
    assert exec_cmd == ["EXEC", str(out_file), "run_arg"]
    assert compiling is False
