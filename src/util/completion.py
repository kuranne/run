from typing import Optional, Any
from pathlib import Path

class CompletionGenerator:
    """
    Generator for cross-shell completion scripts and dynamic query handler.
    """

    @classmethod
    def handle_internal_complete(cls, query_type: str, config: Optional[Any]) -> str:
        """
        Handle internal dynamic completion query (presets, tasks, templates).

        Args:
            query_type (str): Query target ("presets", "tasks", "templates").
            config (Optional[Any]): Config instance containing workspace Run.toml data.

        Returns:
            str: Space or newline-separated completion items.
        """
        cfg_data = config.data if config and hasattr(config, "data") else {}

        if query_type == "presets":
            presets = list(cfg_data.get("presets", {}).keys())
            return " ".join(presets)

        elif query_type == "tasks":
            tasks = list(cfg_data.get("tasks", {}).keys())
            return " ".join(tasks)

        elif query_type == "templates":
            from util.template_manager import TemplateManager
            custom_templates = list(cfg_data.get("templates", {}).keys())
            builtin = [k.lstrip(".") for k in TemplateManager.BUILTIN_TEMPLATES.keys()]
            all_templates = sorted(set(custom_templates + builtin))
            return " ".join(all_templates)

        return ""

    @classmethod
    def generate_zsh(cls) -> str:
        """Generate Zsh completion script."""
        return """#compdef run

_run_presets() {
    local -a presets
    presets=($(run --_complete presets 2>/dev/null))
    _describe 'presets' presets
}

_run_tasks() {
    local -a tasks
    tasks=($(run --_complete tasks 2>/dev/null))
    _describe 'tasks' tasks
}

_run_templates() {
    local -a templates
    templates=($(run --_complete templates 2>/dev/null))
    _describe 'templates' templates
}

_run() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    _arguments -C \\
        '(-w --watch)'{-w,--watch}'[Watch mode: re-compile and run on file change]' \\
        '(-f --force)'{-f,--force}'[Force continue on errors without interactive prompts]' \\
        '(-d --dry-run)'{-d,--dry-run}'[Simulate execution without running commands]' \\
        '(-t --time)'{-t,--time}'[Measure and display execution time]' \\
        '(-M --mem --memory)'{-M,--mem,--memory}'[Measure and display peak memory usage]' \\
        '(-q --quiet)'{-q,--quiet}'[Silence compiler output and logs]' \\
        '*-v[Verbose mode (-v for debug, -vv for trace)]' \\
        '--verbose[Verbose mode]' \\
        '(-i --stdin)'{-i,--stdin}'[Read stdin from file or pipe]:file:_files' \\
        '--expect[Verify output against expected output file]:file:_files' \\
        '--test-dir[Run all matching testcases in directory]:directory:_files -/' \\
        '--debug[Launch interactive debugger (LLDB/GDB/pdb/inspect)]' \\
        '--gdb[Launch interactive GDB debugger]' \\
        '--lldb[Launch interactive LLDB debugger]' \\
        '--valgrind[Run with detailed Valgrind leak checking]' \\
        '--timeout[Timeout in seconds for execution]:seconds:' \\
        '--env[Set environment variable]:key=val:' \\
        '--argument[Arguments to pass to program]:args:' \\
        '(-m --multi)'{-m,--multi}'[Compile multiple source files together]' \\
        '(-p --preset)'{-p,--preset}'[Configuration preset from Run.toml]:preset:_run_presets' \\
        '(-j --jobs)'{-j,--jobs}'[Number of parallel compilation worker threads]:jobs:' \\
        '(-B --build-only --no-run)'{-B,--build-only,--no-run}'[Compile binary without executing]' \\
        '--asan[Compile with AddressSanitizer and UndefinedBehaviorSanitizer]' \\
        '--tsan[Compile with ThreadSanitizer]' \\
        '--sanitize[Compile with custom sanitizer]:sanitizer:' \\
        '--link-auto[Auto find and link C/C++ files]:depth:' \\
        '--flags[Compiler or interpreter flags]:flags:' \\
        '--compiler[Compiler or interpreter override]:compiler:' \\
        '--out-dir[Output directory for compiled binaries]:directory:_files -/' \\
        '--keep[Keep the output binary after execution]' \\
        '--no-cache[Disable build cache]' \\
        '--new[Generate a new file or project from template]:file:_files' \\
        '--template[Template name to use from Run.toml]:template:_run_templates' \\
        '--doctor[Run toolchain and environment diagnostics]' \\
        '--init[Initialize tailored Run.toml for the current project]' \\
        '--directory[Change directory before executing]:directory:_files -/' \\
        '--cwd[Change directory before executing]:directory:_files -/' \\
        '--clean[Clear local build cache and exit]' \\
        '--no-color[Disable ANSI color codes in output]' \\
        '--unsafe[Allow running as root]' \\
        '--completion[Generate shell completion script]:shell:(zsh bash fish powershell)' \\
        '(-V --version)'{-V,--version}'[Check version of the binary]' \\
        '(-h --help)'{-h,--help}'[Show help message]' \\
        '*:target:->targets' && return 0

    case "$state" in
        targets)
            _run_tasks
            _files -g "*.(c|cpp|cc|cxx|h|hpp|rs|py|java|go|zig|js|ts|sh)(-.)"
            ;;
    esac
}

compdef _run run
"""

    @classmethod
    def generate_bash(cls) -> str:
        """Generate Bash completion script."""
        return """_run_complete() {
    local cur prev words cword
    _init_completion || return

    local opts="-w --watch -f --force -d --dry-run -t --time -M --mem --memory -q --quiet -v -vv --verbose -i --stdin --expect --test-dir --debug --gdb --lldb --valgrind --timeout --env --argument -m --multi -p --preset -j --jobs -B --build-only --no-run --asan --tsan --sanitize --link-auto --flags --compiler --out-dir --keep --no-cache --new --template --doctor --init --directory --cwd --clean --no-color --unsafe --completion -V --version -h --help"

    case "$prev" in
        -p|--preset)
            local presets=$(run --_complete presets 2>/dev/null)
            COMPREPLY=($(compgen -W "$presets" -- "$cur"))
            return 0
            ;;
        --template)
            local templates=$(run --_complete templates 2>/dev/null)
            COMPREPLY=($(compgen -W "$templates" -- "$cur"))
            return 0
            ;;
        --completion)
            COMPREPLY=($(compgen -W "zsh bash fish powershell" -- "$cur"))
            return 0
            ;;
        --expect|-i|--stdin)
            COMPREPLY=($(compgen -f -- "$cur"))
            return 0
            ;;
        --test-dir|--out-dir|--directory|--cwd)
            COMPREPLY=($(compgen -d -- "$cur"))
            return 0
            ;;
    esac

    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "$opts" -- "$cur"))
        return 0
    fi

    local tasks=$(run --_complete tasks 2>/dev/null)
    local files=$(compgen -f -X '!*.@(c|cpp|cc|cxx|h|hpp|rs|py|java|go|zig|js|ts|sh)' -- "$cur")
    COMPREPLY=($(compgen -W "$tasks" -- "$cur") $files)
}

complete -F _run_complete run
"""

    @classmethod
    def generate_fish(cls) -> str:
        """Generate Fish completion script."""
        return """# Fish completion for run

function __fish_run_presets
    run --_complete presets 2>/dev/null | string split ' '
end

function __fish_run_tasks
    run --_complete tasks 2>/dev/null | string split ' '
end

function __fish_run_templates
    run --_complete templates 2>/dev/null | string split ' '
end

complete -c run -s w -l watch -d "Watch mode: re-compile and run on file change"
complete -c run -s f -l force -d "Force continue on errors without interactive prompts"
complete -c run -s d -l dry-run -d "Simulate execution without running commands"
complete -c run -s t -l time -d "Measure and display execution time"
complete -c run -s M -l mem -l memory -d "Measure and display peak memory usage"
complete -c run -s q -l quiet -d "Silence compiler output and logs"
complete -c run -s v -l verbose -d "Verbose debug mode"
complete -c run -s i -l stdin -r -d "Read stdin from file or pipe"
complete -c run -l expect -r -d "Verify output against expected output file"
complete -c run -l test-dir -a "(__fish_complete_directories)" -d "Run all matching testcases in directory"
complete -c run -l debug -d "Launch interactive debugger"
complete -c run -l gdb -d "Launch interactive GDB debugger"
complete -c run -l lldb -d "Launch interactive LLDB debugger"
complete -c run -l valgrind -d "Run with detailed Valgrind leak checking"
complete -c run -l timeout -r -d "Timeout in seconds for execution"
complete -c run -l env -r -d "Set environment variable"
complete -c run -l argument -r -d "Arguments to pass to program"
complete -c run -s m -l multi -d "Compile multiple source files together"
complete -c run -s p -l preset -x -a "(__fish_run_presets)" -d "Configuration preset from Run.toml"
complete -c run -s j -l jobs -r -d "Number of parallel worker threads"
complete -c run -s B -l build-only -l no-run -d "Compile binary without executing"
complete -c run -l asan -d "Compile with AddressSanitizer"
complete -c run -l tsan -d "Compile with ThreadSanitizer"
complete -c run -l sanitize -r -d "Compile with custom sanitizer"
complete -c run -l link-auto -d "Auto find and link C/C++ files"
complete -c run -l flags -r -d "Compiler or interpreter flags"
complete -c run -l compiler -r -d "Compiler or interpreter override"
complete -c run -l out-dir -a "(__fish_complete_directories)" -d "Output directory for compiled binaries"
complete -c run -l keep -d "Keep the output binary after execution"
complete -c run -l no-cache -d "Disable build cache"
complete -c run -l new -r -d "Generate a new file or project from template"
complete -c run -l template -x -a "(__fish_run_templates)" -d "Template name from Run.toml"
complete -c run -l doctor -d "Run toolchain and environment diagnostics"
complete -c run -l init -d "Initialize tailored Run.toml"
complete -c run -l directory -l cwd -a "(__fish_complete_directories)" -d "Change directory before executing"
complete -c run -l clean -d "Clear local build cache and exit"
complete -c run -l no-color -d "Disable ANSI color codes"
complete -c run -l unsafe -d "Allow running as root"
complete -c run -l completion -x -a "zsh bash fish powershell" -d "Generate shell completion script"
complete -c run -s V -l version -d "Check version of binary"
complete -c run -s h -l help -d "Show help screen"

# Positional task suggestions
complete -c run -n "__fish_is_first_token" -a "(__fish_run_tasks)" -d "Task from Run.toml"
"""

    @classmethod
    def generate_powershell(cls) -> str:
        """Generate PowerShell completion script."""
        return """Register-ArgumentCompleter -Native -CommandName run -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    $options = @(
        '-w', '--watch',
        '-f', '--force',
        '-d', '--dry-run',
        '-t', '--time',
        '-M', '--mem', '--memory',
        '-q', '--quiet',
        '-v', '-vv', '--verbose',
        '-i', '--stdin',
        '--expect',
        '--test-dir',
        '--debug',
        '--gdb',
        '--lldb',
        '--valgrind',
        '--timeout',
        '--env',
        '--argument',
        '-m', '--multi',
        '-p', '--preset',
        '-j', '--jobs',
        '-B', '--build-only', '--no-run',
        '--asan',
        '--tsan',
        '--sanitize',
        '--link-auto',
        '--flags',
        '--compiler',
        '--out-dir',
        '--keep',
        '--no-cache',
        '--new',
        '--template',
        '--doctor',
        '--init',
        '--directory', '--cwd',
        '--clean',
        '--no-color',
        '--unsafe',
        '--completion',
        '-V', '--version',
        '-h', '--help'
    )

    $elements = $commandAst.CommandElements
    $prev = if ($elements.Count -gt 1) { $elements[-2].Extent.Text } else { '' }

    if ($prev -in @('-p', '--preset')) {
        $presets = (run --_complete presets 2>$null) -split ' '
        $presets | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }
        return
    }

    if ($prev -eq '--template') {
        $templates = (run --_complete templates 2>$null) -split ' '
        $templates | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }
        return
    }

    if ($prev -eq '--completion') {
        @('zsh', 'bash', 'fish', 'powershell') | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }
        return
    }

    if ($wordToComplete -like '-*') {
        $options | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterName', $_)
        }
        return
    }

    $tasks = (run --_complete tasks 2>$null) -split ' '
    $tasks | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', "Task: $_")
    }
}
"""

    @classmethod
    def generate(cls, shell: str) -> Optional[str]:
        """
        Generate completion script for specified shell.

        Args:
            shell (str): Shell name (zsh, bash, fish, powershell, pwsh).

        Returns:
            Optional[str]: Generated completion script or None.
        """
        shell_norm = shell.lower().strip()
        if shell_norm == "zsh":
            return cls.generate_zsh()
        elif shell_norm == "bash":
            return cls.generate_bash()
        elif shell_norm == "fish":
            return cls.generate_fish()
        elif shell_norm in ("powershell", "pwsh"):
            return cls.generate_powershell()
        return None

    @classmethod
    def get_install_instructions(cls) -> str:
        """Get quick setup instructions for all shells."""
        return """=== Shell Autocompletion Setup ===

Zsh:
  # Fast startup with evalcache:
  _evalcache run run --completion zsh

  # Or add to ~/.zshrc:
  eval "$(run --completion zsh)"

  # Or save to file:
  run --completion zsh > "${fpath[1]}/_run"

Bash:
  # Add to ~/.bashrc:
  source <(run --completion bash)

Fish:
  # Save to fish completions directory:
  run --completion fish > ~/.config/fish/completions/run.fish

PowerShell:
  # Add to $PROFILE:
  run --completion powershell | Out-String | Invoke-Expression
"""
