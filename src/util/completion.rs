//! Cross-shell completion script generation and `--_complete` dynamic query handler.
//!
//! Adapted from Python's `src/util/completion.py`. Generates completion scripts
//! for `zsh`, `bash`, `fish`, and `powershell`/`pwsh`, and answers dynamic
//! `--_complete <presets|tasks|templates>` queries using [`Config`] and
//! [`TemplateManager::builtin_names`].

use std::collections::BTreeSet;

use crate::util::config::Config;
use crate::util::template_manager::TemplateManager;

/// Zsh completion script template.
const ZSH_COMPLETION: &str = r#"#compdef run

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

    _arguments -C \
        '(-w --watch)'{-w,--watch}'[Watch mode: re-compile and run on file change]' \
        '(-f --force)'{-f,--force}'[Force continue on errors without interactive prompts]' \
        '(-d --dry-run)'{-d,--dry-run}'[Simulate execution without running commands]' \
        '(-t --time)'{-t,--time}'[Measure and display execution time]' \
        '(-M --mem --memory)'{-M,--mem,--memory}'[Measure and display peak memory usage]' \
        '(-q --quiet)'{-q,--quiet}'[Silence compiler output and logs]' \
        '*-v[Verbose mode (-v for debug, -vv for trace)]' \
        '--verbose[Verbose mode]' \
        '(-i --stdin)'{-i,--stdin}'[Dynamic stdin (file, -, or inline text)]:source:_files' \
        '--stdin-file[Read child stdin strictly from a file]:file:_files' \
        '--stdin-text[Pass inline text as child stdin]:text:' \
        '--expect[Verify output against expected output file]:file:_files' \
        '--test-dir[Run all matching testcases in directory]:directory:_files -/' \
        '--filter[Filter --test-dir cases by glob pattern]:glob:' \
        '--debug[Launch interactive debugger (LLDB/GDB/pdb/inspect)]' \
        '--gdb[Launch interactive GDB debugger]' \
        '--lldb[Launch interactive LLDB debugger]' \
        '--valgrind[Run with detailed Valgrind leak checking]' \
        '--timeout[Timeout in seconds for execution]:seconds:' \
        '--mem-limit[Enforce execution memory limit (e.g. 256M, 1G)]:size:' \
        '--env[Set environment variable]:key=val:' \
        '--argument[Arguments to pass to program]:args:' \
        '(-m --multi)'{-m,--multi}'[Compile multiple source files together]' \
        '(-p --preset)'{-p,--preset}'[Configuration preset from Run.toml]:preset:_run_presets' \
        '(-j --jobs)'{-j,--jobs}'[Number of parallel compilation worker threads]:jobs:' \
        '(-B --build-only --no-run)'{-B,--build-only,--no-run}'[Compile binary without executing]' \
        '--asan[Compile with AddressSanitizer and UndefinedBehaviorSanitizer]' \
        '--tsan[Compile with ThreadSanitizer]' \
        '--sanitize[Compile with custom sanitizer]:sanitizer:' \
        '--link-auto[Auto find and link C/C++ files]:depth:' \
        '--flags[Compiler or interpreter flags]:flags:' \
        '--compiler[Compiler or interpreter override]:compiler:' \
        '--out-dir[Output directory for compiled binaries]:directory:_files -/' \
        '--keep[Keep the output binary after execution]' \
        '--no-cache[Disable build cache]' \
        '--new[Generate a new file or project from template]:file:_files' \
        '--template[Template name to use from Run.toml]:template:_run_templates' \
        '--doctor[Run toolchain and environment diagnostics]' \
        '--init[Initialize tailored Run.toml for the current project]' \
        '--directory[Change directory before executing]:directory:_files -/' \
        '--cwd[Change directory before executing]:directory:_files -/' \
        '--clean[Clear local build cache and exit]' \
        '--no-color[Disable ANSI color codes in output]' \
        '--plain[Force classic tag output]' \
        '--tui[Launch interactive workspace TUI dashboard]' \
        '--json[Emit machine-readable JSON report]' \
        '--json-schema[Print JSON Schema for --json and exit]' \
        '--mcp[Start MCP stdio server for AI agents]' \
        '--mcp-root[Confine --mcp paths to directory]:directory:_files -/' \
        '--bench[Micro-benchmark target for N iterations]:runs:' \
        '--warmup[Warmup iterations before --bench]:warmup:' \
        '--stress[Differential stress test against generator and brute-force]:generator:_files' \
        '--runs[Number of iterations for --stress]:runs:' \
        '--seed[Starting RNG seed for --stress]:seed:' \
        '--checker[Custom checker program for --stress]:file:_files' \
        '(-l --list)'{-l,--list}'[List tasks defined in Run.toml]' \
        '--unsafe[Allow running as root]' \
        '--completion[Generate shell completion script]:shell:(zsh bash fish powershell)' \
        '(-V --version)'{-V,--version}'[Check version of the binary]' \
        '(-h --help)'{-h,--help}'[Show help message]' \
        '*:target:->targets' && return 0

    case "$state" in
        targets)
            _run_tasks
            _files -g "*.(c|cpp|cc|cxx|h|hpp|rs|py|java|go|zig|js|ts|sh)(-.)"
            ;;
    esac
}

compdef _run run
"#;

/// Bash completion script template.
const BASH_COMPLETION: &str = r#"_run_complete() {
    local cur prev words cword
    _init_completion || return

    local opts="-w --watch -f --force -d --dry-run -t --time -M --mem --memory -q --quiet -v -vv --verbose -i --stdin --stdin-file --stdin-text --expect --test-dir --filter --debug --gdb --lldb --valgrind --timeout --mem-limit --env --argument -m --multi -p --preset -j --jobs -B --build-only --no-run --asan --tsan --sanitize --link-auto --flags --compiler --out-dir --keep --no-cache --new --template -l --list --doctor --init --directory --cwd --clean --no-color --plain --tui --json --json-schema --mcp --mcp-root --bench --warmup --stress --runs --seed --checker --unsafe --completion -V --version -h --help"

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
        --expect|-i|--stdin|--stdin-file|--checker)
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
"#;

/// Fish completion script template.
const FISH_COMPLETION: &str = r#"# Fish completion for run

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
complete -c run -s i -l stdin -r -d "Dynamic stdin (file, -, or inline text)"
complete -c run -l stdin-file -r -d "Read child stdin strictly from a file"
complete -c run -l stdin-text -r -d "Pass inline text as child stdin"
complete -c run -l expect -r -d "Verify output against expected output file"
complete -c run -l test-dir -a "(__fish_complete_directories)" -d "Run all matching testcases in directory"
complete -c run -l filter -r -d "Filter --test-dir cases by glob pattern"
complete -c run -l debug -d "Launch interactive debugger"
complete -c run -l gdb -d "Launch interactive GDB debugger"
complete -c run -l lldb -d "Launch interactive LLDB debugger"
complete -c run -l valgrind -d "Run with detailed Valgrind leak checking"
complete -c run -l timeout -r -d "Timeout in seconds for execution"
complete -c run -l mem-limit -r -d "Enforce execution memory limit (e.g. 256M, 1G)"
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
complete -c run -l plain -d "Force classic tag output"
complete -c run -l tui -d "Launch interactive workspace TUI dashboard"
complete -c run -l json -d "Emit machine-readable JSON report"
complete -c run -l json-schema -d "Print JSON Schema for --json and exit"
complete -c run -l mcp -d "Start MCP stdio server for AI agents"
complete -c run -l mcp-root -a "(__fish_complete_directories)" -d "Confine --mcp paths to directory"
complete -c run -l bench -r -d "Micro-benchmark target for N iterations"
complete -c run -l warmup -r -d "Warmup iterations before --bench"
complete -c run -l stress -r -d "Differential stress test against generator and brute-force"
complete -c run -l runs -r -d "Number of iterations for --stress"
complete -c run -l seed -r -d "Starting RNG seed for --stress"
complete -c run -l checker -r -d "Custom checker program for --stress"
complete -c run -s l -l list -d "List tasks defined in Run.toml"
complete -c run -l unsafe -d "Allow running as root"
complete -c run -l completion -x -a "zsh bash fish powershell" -d "Generate shell completion script"
complete -c run -s V -l version -d "Check version of binary"
complete -c run -s h -l help -d "Show help screen"

# Positional task suggestions
complete -c run -n "__fish_is_first_token" -a "(__fish_run_tasks)" -d "Task from Run.toml"
"#;

/// PowerShell completion script template.
const POWERSHELL_COMPLETION: &str = r#"Register-ArgumentCompleter -Native -CommandName run -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    $options = @(
        '-w', '--watch',
        '-f', '--force',
        '-d', '--dry-run',
        '-t', '--time',
        '-M', '--mem', '--memory',
        '-q', '--quiet',
        '-v', '-vv', '--verbose',
        '-i', '--stdin', '--stdin-file', '--stdin-text',
        '--expect',
        '--test-dir', '--filter',
        '--debug',
        '--gdb',
        '--lldb',
        '--valgrind',
        '--timeout', '--mem-limit',
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
        '-l', '--list',
        '--no-color', '--plain', '--tui', '--json', '--json-schema',
        '--mcp', '--mcp-root',
        '--bench', '--warmup', '--stress', '--runs', '--seed', '--checker',
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
"#;

/// Quick setup instructions printed when `--completion` is invoked without a shell argument.
const INSTALL_INSTRUCTIONS: &str = r#"=== Shell Autocompletion Setup ===

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
"#;

/// Generator for cross-shell completion scripts and `--_complete` dynamic queries.
#[derive(Debug, Clone, Copy, Default)]
pub struct CompletionGenerator;

impl CompletionGenerator {
    /// Handle `--_complete <presets|tasks|templates>` using `config` and built-in templates.
    pub fn handle_internal_complete(query_type: &str, config: Option<&Config>) -> String {
        match query_type {
            "presets" => config
                .map(|c| {
                    c.presets
                        .keys()
                        .map(String::as_str)
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .unwrap_or_default(),
            "tasks" => config
                .map(|c| {
                    c.tasks
                        .keys()
                        .map(String::as_str)
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .unwrap_or_default(),
            "templates" => {
                let mut all: BTreeSet<String> =
                    TemplateManager::builtin_names().into_iter().collect();
                if let Some(c) = config {
                    all.extend(c.templates.keys().cloned());
                }
                all.into_iter().collect::<Vec<_>>().join(" ")
            }
            _ => String::new(),
        }
    }

    /// Generate the Zsh completion script.
    pub fn generate_zsh() -> &'static str {
        ZSH_COMPLETION
    }

    /// Generate the Bash completion script.
    pub fn generate_bash() -> &'static str {
        BASH_COMPLETION
    }

    /// Generate the Fish completion script.
    pub fn generate_fish() -> &'static str {
        FISH_COMPLETION
    }

    /// Generate the PowerShell completion script.
    pub fn generate_powershell() -> &'static str {
        POWERSHELL_COMPLETION
    }

    /// Generate a completion script for `shell` (`zsh`, `bash`, `fish`, `powershell`, `pwsh`).
    pub fn generate(shell: &str) -> Option<&'static str> {
        match shell.trim().to_ascii_lowercase().as_str() {
            "zsh" => Some(Self::generate_zsh()),
            "bash" => Some(Self::generate_bash()),
            "fish" => Some(Self::generate_fish()),
            "powershell" | "pwsh" => Some(Self::generate_powershell()),
            _ => None,
        }
    }

    /// Return quick autocompletion installation instructions for all supported shells.
    pub fn get_install_instructions() -> &'static str {
        INSTALL_INSTRUCTIONS
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Port of `dev:tests/test_completion.py::test_generate_completion_scripts`.
    #[test]
    fn test_generate_completion_scripts() {
        let zsh = CompletionGenerator::generate("zsh").expect("zsh");
        assert!(zsh.contains("#compdef run"));
        assert!(zsh.contains("compdef _run run"));
        assert!(zsh.contains("_run_presets"));
        assert!(zsh.contains("_run_tasks"));
        assert!(zsh.contains("--build-only"));

        let bash = CompletionGenerator::generate("bash").expect("bash");
        assert!(bash.contains("complete -F _run_complete run"));
        assert!(bash.contains("--preset"));

        let fish = CompletionGenerator::generate("fish").expect("fish");
        assert!(fish.contains("complete -c run"));
        assert!(fish.contains("__fish_run_presets"));

        let ps = CompletionGenerator::generate("powershell").expect("powershell");
        assert!(ps.contains("Register-ArgumentCompleter"));
        let pwsh = CompletionGenerator::generate("pwsh").expect("pwsh");
        assert_eq!(pwsh, ps);

        assert_eq!(CompletionGenerator::generate("unknown_shell"), None);
    }

    /// Port of `dev:tests/test_completion.py::test_handle_internal_complete`.
    #[test]
    fn test_handle_internal_complete() {
        let cfg = Config::from_toml_str(
            r#"
[tasks]
test = "pytest tests/"
build = "cargo build"

[presets.debug]
cpp = ["-g"]

[presets.release]
cpp = ["-O3"]

[templates.leetcode_rs]
description = "Leetcode template"
content = "fn main() {}"
"#,
            None,
            None,
        )
        .expect("parse config");

        let presets = CompletionGenerator::handle_internal_complete("presets", Some(&cfg));
        assert!(presets.contains("debug"));
        assert!(presets.contains("release"));

        let tasks = CompletionGenerator::handle_internal_complete("tasks", Some(&cfg));
        assert!(tasks.contains("test"));
        assert!(tasks.contains("build"));

        let templates = CompletionGenerator::handle_internal_complete("templates", Some(&cfg));
        assert!(templates.contains("leetcode_rs"));
        assert!(templates.contains("cpp"));
        assert!(templates.contains("py"));
        assert!(templates.contains("rs"));

        assert_eq!(
            CompletionGenerator::handle_internal_complete("unknown", Some(&cfg)),
            ""
        );
    }

    /// Port of `dev:tests/test_completion.py::test_get_install_instructions`.
    #[test]
    fn test_get_install_instructions() {
        let guide = CompletionGenerator::get_install_instructions();
        assert!(guide.contains("Shell Autocompletion Setup"));
        assert!(guide.contains("Zsh:"));
        assert!(guide.contains("_evalcache"));
        assert!(guide.contains("Bash:"));
        assert!(guide.contains("Fish:"));
        assert!(guide.contains("PowerShell:"));
    }
}
