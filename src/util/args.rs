//! CLI flag definitions and argv preprocessor (`clap` 4).
//!
//! Adapted from Python's `src/util/args.py`. Preprocesses raw command-line
//! arguments before `clap::Parser` runs so dash-prefixed values for `-F`/`-a`
//! (`-F -O3`), optional arguments for `-L`/`--completion`/`-i`, and `--`
//! runtime argument forwarding behave reliably.

use std::ffi::OsString;
use std::path::Path;

use clap::{ArgAction, Parser};

use crate::model::options::{RunnerOptions, UiMode};

/// Long flags taking an explicit following value token.
const VALUE_FLAGS_LONG: &[&str] = &[
    "--flags",
    "--args",
    "--argument",
    "--preset",
    "--directory",
    "--new",
    "--template",
    "--expect",
    "--test-dir",
    "--jobs",
    "--sanitize",
    "--timeout",
    "--env",
    "--out-dir",
    "--compiler",
    "--sandbox-image",
    "--sandbox-dockerfile",
    "--sandbox-compose",
    "--sandbox-service",
    "--warmup",
];

/// Short flags taking an explicit following value token.
const VALUE_FLAGS_SHORT: &[&str] = &["-F", "-a", "-p", "-C", "-j"];

/// Output produced by [`preprocess_argv`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreprocessedArgv {
    /// Sanitized argv suitable for `clap::Parser::try_parse_from`.
    pub clap_argv: Vec<String>,
    /// Extra tokens appearing after `--` when `--` separates runtime args.
    pub tail_args: Vec<String>,
    /// Whether an explicit `--` separator appeared in the original argv.
    pub saw_double_dash: bool,
}

/// Normalize raw CLI arguments before handing them to `clap`.
///
/// Rewrites `-F -O3` / `-a --help` into `=`-attached forms, attaches default
/// optional values for `-L` (`0`), `--completion` (`auto`), and `-i` (`-`),
/// and splits `--` trailing runtime arguments.
pub fn preprocess_argv<I, T>(raw: I) -> PreprocessedArgv
where
    I: IntoIterator<Item = T>,
    T: Into<OsString>,
{
    let args: Vec<String> = raw
        .into_iter()
        .map(|s| s.into().to_string_lossy().into_owned())
        .collect();

    let (head, tail, saw_double_dash) = match args.iter().position(|a| a == "--") {
        Some(pos) => (&args[..pos], args[pos + 1..].to_vec(), true),
        None => (args.as_slice(), Vec::new(), false),
    };

    let mut out = Vec::with_capacity(head.len() + 1);
    let mut iter = head.iter().enumerate().peekable();
    if let Some((_, bin)) = iter.next() {
        out.push(bin.clone());
    }

    while let Some((_idx, tok)) = iter.next() {
        match tok.as_str() {
            "-F" | "--flags" => {
                if let Some((_, next)) = iter.peek() {
                    let val = (*next).clone();
                    iter.next();
                    out.push(format!("--flags={val}"));
                } else {
                    out.push(tok.clone());
                }
            }
            "-a" | "--args" | "--argument" => {
                if let Some((_, next)) = iter.peek() {
                    let val = (*next).clone();
                    iter.next();
                    out.push(format!("--args={val}"));
                } else {
                    out.push(tok.clone());
                }
            }
            "-L" | "--all" => {
                if let Some((_, next)) = iter.peek()
                    && next.parse::<isize>().is_ok()
                {
                    let depth = (*next).clone();
                    iter.next();
                    out.push(format!("--all={depth}"));
                } else {
                    out.push("--all=-1".to_string());
                }
            }
            "--bench" => {
                if let Some((_, next)) = iter.peek()
                    && next.parse::<usize>().is_ok()
                {
                    let count = (*next).clone();
                    iter.next();
                    out.push(format!("--bench={count}"));
                } else {
                    out.push("--bench=10".to_string());
                }
            }
            "--completion" => {
                if let Some((_, next)) = iter.peek()
                    && !next.starts_with('-')
                    && !next.contains('.')
                    && !Path::new(next.as_str()).exists()
                {
                    let shell = (*next).clone();
                    iter.next();
                    out.push(format!("--completion={shell}"));
                } else {
                    out.push("--completion=auto".to_string());
                }
            }
            "-i" | "--stdin" => {
                let Some((_, next)) = iter.peek() else {
                    out.push("--stdin=-".to_string());
                    continue;
                };
                let next_str = next.as_str();
                if next_str == "-" {
                    iter.next();
                    out.push("--stdin=-".to_string());
                } else if next_str.starts_with('-') {
                    out.push("--stdin=-".to_string());
                } else {
                    let file = (*next).clone();
                    iter.next();
                    out.push(format!("--stdin={file}"));
                }
            }
            s if s.starts_with("-F=") => {
                out.push(format!("--flags={}", &s[3..]));
            }
            s if s.starts_with("-a=") => {
                out.push(format!("--args={}", &s[3..]));
            }
            s if s.starts_with("-L=") => {
                out.push(format!("--all={}", &s[3..]));
            }
            s if s.starts_with("-i=") => {
                out.push(format!("--stdin={}", &s[3..]));
            }
            s if VALUE_FLAGS_SHORT.contains(&s) || VALUE_FLAGS_LONG.contains(&s) => {
                out.push(tok.clone());
                if let Some((_, next)) = iter.next() {
                    out.push(next.clone());
                }
            }
            _ => out.push(tok.clone()),
        }
    }

    PreprocessedArgv {
        clap_argv: out,
        tail_args: tail,
        saw_double_dash,
    }
}

/// Unified command-line arguments parsed by `clap`.
#[derive(Debug, Clone, Default, Parser)]
#[command(
    name = "run",
    version,
    about = "Universal runner and compiler CLI for single files, multi-file projects, tasks, and tests",
    disable_version_flag = false
)]
pub struct CliArgs {
    /// Source file(s), directory, or configured `[tasks]` name to execute.
    #[arg(value_name = "FILES")]
    pub files: Vec<String>,

    /// Compile multiple files together as one executable/program.
    #[arg(short = 'm', long = "multi")]
    pub multi: bool,

    /// Keep generated binaries and class files after execution.
    #[arg(short = 'b', long = "keep")]
    pub keep: bool,

    /// Compile/build without executing the resulting binary.
    #[arg(short = 'B', long = "build-only", visible_alias = "no-run")]
    pub build_only: bool,

    /// Extra compiler or interpreter flags (quoted or repeatable).
    #[arg(short = 'F', long = "flags", action = ArgAction::Append)]
    pub flags: Vec<String>,

    /// Arguments forwarded to the executed program or task.
    #[arg(short = 'a', long = "args", visible_alias = "argument", action = ArgAction::Append)]
    pub args: Vec<String>,

    /// Build Cargo projects in release mode (`--release`).
    #[arg(long = "release")]
    pub release: bool,

    /// Display execution wall-clock timing metrics.
    #[arg(short = 't', long = "time")]
    pub time: bool,

    /// Display peak resident memory usage metrics.
    #[arg(short = 'M', long = "memory", visible_alias = "mem")]
    pub memory: bool,

    /// Print planned commands and cleanups without running them.
    #[arg(short = 'd', long = "dry-run")]
    pub dry_run: bool,

    /// Scan all sources in target directory (`-L` = unlimited `-1`, or `-L DEPTH`).
    #[arg(short = 'L', long = "all", value_name = "DEPTH", allow_hyphen_values = true)]
    pub all: Option<isize>,

    /// Apply compiler/interpreter flags from `[presets.<NAME>]` in `Run.toml`.
    #[arg(short = 'p', long = "preset", value_name = "NAME")]
    pub preset: Option<String>,

    /// Disable build/binary cache reads and writes for this invocation.
    #[arg(long = "no-cache")]
    pub no_cache: bool,

    /// Clear cached build artifacts for the current project before running.
    #[arg(long = "clean-cache", visible_alias = "clean")]
    pub clean_cache: bool,

    /// Suppress non-essential logs and external compiler stdout/stderr.
    #[arg(short = 'q', long = "quiet")]
    pub quiet: bool,

    /// Watch target files for modifications and automatically re-run.
    #[arg(short = 'w', long = "watch")]
    pub watch: bool,

    /// Increase diagnostic verbosity (`-v` verbose, `-vv` trace).
    #[arg(short = 'v', long = "verbose", action = ArgAction::Count)]
    pub verbose: u8,

    /// Bypass safety confirmations, overwrite existing files, or ignore non-owner venvs.
    #[arg(short = 'f', long = "force")]
    pub force: bool,

    /// Change working directory before loading configuration or resolving paths.
    #[arg(short = 'C', long = "directory", visible_alias = "cwd", value_name = "DIR")]
    pub directory: Option<String>,

    /// Generate a default `Run.toml` configuration in the current directory.
    #[arg(long = "init")]
    pub init: bool,

    /// Create a new starter source file or directory from a built-in/custom template.
    #[arg(long = "new", value_name = "TARGET")]
    pub new_target: Option<String>,

    /// Explicit template name to use with `--new`.
    #[arg(long = "template", value_name = "NAME")]
    pub template: Option<String>,

    /// Diagnose available host compilers, runtimes, debuggers, and sandbox tools.
    #[arg(long = "doctor")]
    pub doctor: bool,

    /// Generate shell completion setup script (`auto`, `bash`, `zsh`, `fish`, `pwsh`).
    #[arg(long = "completion", value_name = "SHELL")]
    pub completion: Option<String>,

    /// Compare program stdout against expected output file and print unified diff on mismatch.
    #[arg(long = "expect", value_name = "FILE")]
    pub expect: Option<String>,

    /// Discover and execute matching input/output testcase pairs from a directory.
    #[arg(long = "test-dir", value_name = "DIR")]
    pub test_dir: Option<String>,

    /// Maximum parallel worker jobs for multi-file C/C++ object compilation.
    #[arg(short = 'j', long = "jobs", value_name = "N")]
    pub jobs: Option<usize>,

    /// Compile with debug symbols and launch interactive debugger (`gdb`/`lldb`/`pdb`).
    #[arg(long = "debug")]
    pub debug: bool,

    /// Compile with `-g` and run under GNU Debugger (`gdb`).
    #[arg(long = "gdb")]
    pub gdb: bool,

    /// Compile with `-g` and run under LLVM Debugger (`lldb`).
    #[arg(long = "lldb")]
    pub lldb: bool,

    /// Compile with `-g` and run under Valgrind memcheck.
    #[arg(long = "valgrind")]
    pub valgrind: bool,

    /// Enable AddressSanitizer and UndefinedBehaviorSanitizer (`-fsanitize=address,undefined`).
    #[arg(long = "asan")]
    pub asan: bool,

    /// Enable ThreadSanitizer (`-fsanitize=thread`).
    #[arg(long = "tsan")]
    pub tsan: bool,

    /// Enable custom `-fsanitize=<VALUE>` instrumentation.
    #[arg(long = "sanitize", value_name = "VALUE")]
    pub sanitize: Option<String>,

    /// Abort compilation or execution after `SECONDS` wall-clock timeout.
    #[arg(long = "timeout", value_name = "SECONDS")]
    pub timeout: Option<f64>,

    /// Provide stdin from a file path or `-` for piped stdin (`-i` defaults to `-`).
    #[arg(short = 'i', long = "stdin", value_name = "SOURCE")]
    pub stdin: Option<String>,

    /// Inject custom `KEY=VALUE` environment variable(s) into child execution.
    #[arg(long = "env", value_name = "KEY=VALUE", action = ArgAction::Append)]
    pub env: Vec<String>,

    /// Directory where compiled binaries or generated artifacts are written.
    #[arg(long = "out-dir", value_name = "DIR")]
    pub out_dir: Option<String>,

    /// Disable ANSI color escapes in terminal output.
    #[arg(long = "no-color")]
    pub no_color: bool,

    /// Force classic `[ TAG ]` output instead of Node-style animated CLI.
    #[arg(long = "plain", conflicts_with = "tui")]
    pub plain: bool,

    /// Launch full-screen interactive workspace TUI dashboard.
    #[arg(long = "tui", conflicts_with = "plain")]
    pub tui: bool,

    /// Micro-benchmark target for `N` iterations (defaults to 10 when value is omitted).
    #[arg(long = "bench", value_name = "N")]
    pub bench: Option<usize>,

    /// Number of warmup iterations before `--bench` measurements (default: 2).
    #[arg(long = "warmup", value_name = "W")]
    pub warmup: Option<usize>,

    /// Override compiler or interpreter executable for the active language.
    #[arg(long = "compiler", value_name = "BIN")]
    pub compiler: Option<String>,

    /// Isolate execution inside native Linux Bubblewrap namespace sandbox.
    #[arg(long = "restrict")]
    pub restrict: bool,

    /// Run compilation/execution inside a Docker/Podman container sandbox.
    #[arg(long = "sandbox")]
    pub sandbox: bool,

    /// Container image to use with `--sandbox`.
    #[arg(long = "sandbox-image", value_name = "IMAGE")]
    pub sandbox_image: Option<String>,

    /// Allow network access inside `--sandbox` containers.
    #[arg(long = "sandbox-net")]
    pub sandbox_net: bool,

    /// Custom Dockerfile path for building `--sandbox` container images.
    #[arg(long = "sandbox-dockerfile", value_name = "FILE")]
    pub sandbox_dockerfile: Option<String>,

    /// Docker Compose file for multi-service `--sandbox` environments.
    #[arg(long = "sandbox-compose", value_name = "FILE")]
    pub sandbox_compose: Option<String>,

    /// Target service name inside `--sandbox-compose`.
    #[arg(long = "sandbox-service", value_name = "SERVICE")]
    pub sandbox_service: Option<String>,

    /// Force rebuilding `--sandbox` container image or recreating persistent container.
    #[arg(long = "sandbox-rebuild")]
    pub sandbox_rebuild: bool,

    /// Keep persistent sandbox container running after command exit.
    #[arg(long = "sandbox-keep")]
    pub sandbox_keep: bool,

    /// Stop all active persistent sandbox and compose containers immediately.
    #[arg(long = "sandbox-stop")]
    pub sandbox_stop: bool,

    /// Allow execution as root/administrator and bypass suspicious compiler flag filters.
    #[arg(long = "unsafe", visible_alias = "allow-root")]
    pub unsafe_mode: bool,

    /// Runtime arguments captured after `--` separator (not a CLI flag).
    #[arg(skip)]
    pub extra_run_args: Vec<String>,
}

impl CliArgs {
    /// Parse [`CliArgs`] from an arbitrary argument iterator (including `argv[0]`).
    pub fn parse_from_raw<I, T>(raw: I) -> Result<Self, clap::Error>
    where
        I: IntoIterator<Item = T>,
        T: Into<OsString>,
    {
        let pre = preprocess_argv(raw);
        let mut parsed = Self::try_parse_from(&pre.clap_argv)?;
        if pre.saw_double_dash && !pre.tail_args.is_empty() {
            if parsed.files.is_empty() && Path::new(&pre.tail_args[0]).exists() {
                let mut rest_idx = 0;
                while rest_idx < pre.tail_args.len() && Path::new(&pre.tail_args[rest_idx]).exists()
                {
                    parsed.files.push(pre.tail_args[rest_idx].clone());
                    rest_idx += 1;
                }
                parsed
                    .extra_run_args
                    .extend(pre.tail_args[rest_idx..].iter().cloned());
            } else {
                parsed.extra_run_args = pre.tail_args;
            }
        }
        Ok(parsed)
    }

    /// Parse [`CliArgs`] from [`std::env::args_os`], exiting gracefully on `--help`/`--version`
    /// or invalid syntax (exit code `2`, matching `clap` / `argparse`).
    pub fn parse_env() -> Self {
        match Self::parse_from_raw(std::env::args_os()) {
            Ok(args) => args,
            Err(err) => err.exit(),
        }
    }

    /// Return combined `--flags` values as a single string if non-empty.
    pub fn flags_str(&self) -> Option<String> {
        let joined = self.flags.join(" ");
        (!joined.trim().is_empty()).then_some(joined)
    }

    /// Return combined `-a` / `--args` / `--argument` values as a single string if non-empty.
    pub fn args_str(&self) -> Option<String> {
        let joined = self.args.join(" ");
        (!joined.trim().is_empty()).then_some(joined)
    }

    /// Convert CLI flags into [`RunnerOptions`] for `CompilerRunner` / `BaseRunner`.
    pub fn to_runner_options(&self) -> RunnerOptions {
        RunnerOptions {
            multi: self.multi,
            keep: self.keep,
            build_only: self.build_only,
            time: self.time,
            memory: self.memory,
            dry_run: self.dry_run,
            preset: self.preset.clone(),
            no_cache: self.no_cache,
            quiet: self.quiet,
            watch: self.watch,
            force: self.force,
            jobs: self.jobs,
            expect: self.expect.clone(),
            test_dir: self.test_dir.clone(),
            debug: self.debug,
            gdb: self.gdb,
            lldb: self.lldb,
            valgrind: self.valgrind,
            asan: self.asan,
            tsan: self.tsan,
            sanitize: self.sanitize.clone(),
            timeout: self.timeout,
            stdin: self.stdin.clone(),
            env: self.env.clone(),
            out_dir: self.out_dir.clone(),
            compiler: self.compiler.clone(),
            restrict: self.restrict,
            sandbox: self.sandbox,
            sandbox_net: self.sandbox_net,
            unsafe_mode: self.unsafe_mode,
            release: self.release,
            target_files: Vec::new(),
            extra_flags: Vec::new(),
            run_args: self.extra_run_args.clone(),
            plain: self.plain,
            tui: self.tui,
            bench: self.bench,
            warmup: self.warmup.unwrap_or(2),
        }
    }

    /// Resolve active [`UiMode`] from `--tui`, `--plain`, and stdout TTY detection.
    pub const fn resolve_ui_mode(&self, is_tty: bool) -> UiMode {
        if self.tui {
            UiMode::Tui
        } else if self.plain || !is_tty {
            UiMode::Plain
        } else {
            UiMode::Modern
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn parse(args: &[&str]) -> CliArgs {
        let mut full = vec!["run"];
        full.extend_from_slice(args);
        CliArgs::parse_from_raw(full).expect("parse args")
    }

    #[test]
    fn test_dash_values_for_flags_and_args() {
        let cli = parse(&["-F", "-O3 -Wall", "-a", "--help", "main.c"]);
        assert_eq!(cli.flags_str().as_deref(), Some("-O3 -Wall"));
        assert_eq!(cli.args_str().as_deref(), Some("--help"));
        assert_eq!(cli.files, vec!["main.c"]);
    }

    #[test]
    fn test_double_dash_forwarding_and_leading_dash_filename() {
        let cli = parse(&["script.py", "--", "--port", "8080", "-n"]);
        assert_eq!(cli.files, vec!["script.py"]);
        assert_eq!(cli.extra_run_args, vec!["--port", "8080", "-n"]);

        // Leading-dash existing filename after `--` becomes a target file.
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("run_args_test_{nanos}"));
        fs::create_dir_all(&dir).unwrap();
        let dash_file = dir.join("-main.c");
        fs::write(&dash_file, "int main(){}").unwrap();
        let dash_str = dash_file.to_string_lossy().into_owned();

        let cli2 = parse(&["-d", "--", &dash_str]);
        assert!(cli2.dry_run);
        assert_eq!(cli2.files, vec![dash_str]);
        assert!(cli2.extra_run_args.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_optional_all_depth_and_completion() {
        let c1 = parse(&["-L"]);
        assert_eq!(c1.all, Some(-1));

        let c2 = parse(&["-L", "2", "src"]);
        assert_eq!(c2.all, Some(2));
        assert_eq!(c2.files, vec!["src"]);

        let c3 = parse(&["-L", "src"]);
        assert_eq!(c3.all, Some(-1));
        assert_eq!(c3.files, vec!["src"]);

        let c4 = parse(&["--completion"]);
        assert_eq!(c4.completion.as_deref(), Some("auto"));

        let c5 = parse(&["--completion", "zsh"]);
        assert_eq!(c5.completion.as_deref(), Some("zsh"));

        let c6 = parse(&["--completion", "invalid_shell"]);
        assert_eq!(c6.completion.as_deref(), Some("invalid_shell"));

        let c7 = parse(&["--completion", "main.c"]);
        assert_eq!(c7.completion.as_deref(), Some("auto"));
        assert_eq!(c7.files, vec!["main.c"]);
    }

    #[test]
    fn test_stdin_optional_vs_existing_source() {
        let c1 = parse(&["-i", "input.c", "main.py"]);
        assert_eq!(c1.stdin.as_deref(), Some("input.c"));
        assert_eq!(c1.files, vec!["main.py"]);

        let c2 = parse(&["-i", "-q", "main.py"]);
        assert_eq!(c2.stdin.as_deref(), Some("-"));
        assert!(c2.quiet);
        assert_eq!(c2.files, vec!["main.py"]);

        let c3 = parse(&["-i", "input.cpp"]);
        assert_eq!(c3.stdin.as_deref(), Some("input.cpp"));
        assert!(c3.files.is_empty());
    }

    #[test]
    fn test_aliases_and_verbose_counts() {
        let cli = parse(&[
            "--no-run",
            "--mem",
            "--argument=--x",
            "--allow-root",
            "-vv",
            "--env",
            "A=1",
            "--env",
            "B=2",
        ]);
        assert!(cli.build_only);
        assert!(cli.memory);
        assert_eq!(cli.args_str().as_deref(), Some("--x"));
        assert!(cli.unsafe_mode);
        assert_eq!(cli.verbose, 2);
        assert_eq!(cli.env, vec!["A=1", "B=2"]);
    }

    #[test]
    fn test_preprocessor_equals_forms_and_value_flag_passthrough() {
        let pre = preprocess_argv(["run", "--expect", "-L", "src"]);
        assert_eq!(pre.clap_argv, vec!["run", "--expect", "-L", "src"]);

        let cli = parse(&[
            "-F=-O2",
            "-F=-Wall",
            "-a=--mode=fast",
            "-L=1",
            "-i=in.txt",
            "--expect",
            "expected.txt",
            "src",
        ]);
        assert_eq!(cli.flags_str().as_deref(), Some("-O2 -Wall"));
        assert_eq!(cli.args_str().as_deref(), Some("--mode=fast"));
        assert_eq!(cli.all, Some(1));
        assert_eq!(cli.stdin.as_deref(), Some("in.txt"));
        assert_eq!(cli.expect.as_deref(), Some("expected.txt"));
        assert_eq!(cli.files, vec!["src"]);
    }

    #[test]
    fn test_ui_mode_resolution_and_flags() {
        let default_cli = parse(&["main.c"]);
        assert_eq!(default_cli.resolve_ui_mode(true), UiMode::Modern);
        assert_eq!(default_cli.resolve_ui_mode(false), UiMode::Plain);

        let plain_cli = parse(&["--plain", "main.c"]);
        assert_eq!(plain_cli.resolve_ui_mode(true), UiMode::Plain);
        assert!(plain_cli.to_runner_options().plain);

        let tui_cli = parse(&["--tui"]);
        assert_eq!(tui_cli.resolve_ui_mode(false), UiMode::Tui);
        assert!(tui_cli.to_runner_options().tui);

        assert!(CliArgs::parse_from_raw(["run", "--plain", "--tui"]).is_err());

        let bench_default = parse(&["--bench", "main.c"]);
        assert_eq!(bench_default.bench, Some(10));
        assert_eq!(bench_default.to_runner_options().warmup, 2);

        let bench_custom = parse(&["--bench", "25", "--warmup", "4", "main.c"]);
        assert_eq!(bench_custom.bench, Some(25));
        assert_eq!(bench_custom.to_runner_options().warmup, 4);
    }
}
