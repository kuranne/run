import pytest
from util.completion import CompletionGenerator

class MockConfig:
    def __init__(self, data=None):
        self.data = data or {}

def test_generate_completion_scripts():
    # Zsh
    zsh_script = CompletionGenerator.generate("zsh")
    assert zsh_script is not None
    assert "#compdef run" in zsh_script
    assert "compdef _run run" in zsh_script
    assert "_run_presets" in zsh_script
    assert "_run_tasks" in zsh_script
    assert "--build-only" in zsh_script

    # Bash
    bash_script = CompletionGenerator.generate("bash")
    assert bash_script is not None
    assert "complete -F _run_complete run" in bash_script
    assert "--preset" in bash_script

    # Fish
    fish_script = CompletionGenerator.generate("fish")
    assert fish_script is not None
    assert "complete -c run" in fish_script
    assert "__fish_run_presets" in fish_script

    # PowerShell
    ps_script = CompletionGenerator.generate("powershell")
    assert ps_script is not None
    assert "Register-ArgumentCompleter" in ps_script

    pwsh_script = CompletionGenerator.generate("pwsh")
    assert pwsh_script == ps_script

    # Invalid shell
    assert CompletionGenerator.generate("unknown_shell") is None

def test_handle_internal_complete():
    cfg = MockConfig({
        "presets": {
            "debug": {"flags": "-g"},
            "release": {"flags": "-O3"}
        },
        "tasks": {
            "test": "pytest tests/",
            "build": "cargo build"
        },
        "templates": {
            "leetcode_rs": {"description": "Leetcode template"}
        }
    })

    # Presets
    presets = CompletionGenerator.handle_internal_complete("presets", cfg)
    assert "debug" in presets
    assert "release" in presets

    # Tasks
    tasks = CompletionGenerator.handle_internal_complete("tasks", cfg)
    assert "test" in tasks
    assert "build" in tasks

    # Templates
    templates = CompletionGenerator.handle_internal_complete("templates", cfg)
    assert "leetcode_rs" in templates
    assert "cpp" in templates
    assert "py" in templates
    assert "rs" in templates

def test_get_install_instructions():
    guide = CompletionGenerator.get_install_instructions()
    assert "Shell Autocompletion Setup" in guide
    assert "Zsh:" in guide
    assert "_evalcache" in guide
    assert "Bash:" in guide
    assert "Fish:" in guide
    assert "PowerShell:" in guide
