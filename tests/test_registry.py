import pytest
from pathlib import Path
from runner.registry import HandlerRegistry
from runner.custom_language_handler import CustomLanguageHandler
from runner.c_family_handler import CFamilyHandler
from util.config import Config

def test_registry_custom_priority(tmp_path, monkeypatch):
    toml_content = """
    [[languages]]
    name = "c"
    extensions = [".c"]
    runner = "zig"
    subcommand = "cc"
    type = "compiler"
    """
    toml_file = tmp_path / "Run.toml"
    toml_file.write_text(toml_content)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    config = Config()
    registry = HandlerRegistry()

    # Custom language config should match .c first via CustomLanguageHandler
    c_file = tmp_path / "main.c"
    handler = registry.get_handler(c_file, config)
    assert isinstance(handler, CustomLanguageHandler)

def test_registry_fallback_c_family(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    registry = HandlerRegistry()

    c_file = tmp_path / "main.c"
    # When no custom config for .c, handler should be None from custom check
    # and registry custom_handler.can_handle is False
    assert not registry.custom_handler.can_handle(c_file, config)
