import pytest
from pathlib import Path
from runner.custom_language_handler import CustomLanguageHandler
from util.errors import ConfigError

class DummyCustomRunner(CustomLanguageHandler):
    def __init__(self):
        self.preset = None
        self.extra_flags = []
        self.run_args = []
        self.output_files = []
        self.executed_commands = []
        self.config = type("Config", (), {"get_preset_flags": lambda self, p, l: []})()

    def run_command(self, cmd, compiling=False):
        self.executed_commands.append((cmd, compiling))

    def _execute_binary(self, bin_path, args=[]):
        self.executed_commands.append((["EXEC", str(bin_path)] + args, False))

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
