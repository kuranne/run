//! Core command execution engine, sandbox/debugger wrapping, metrics, and cleanup.
//!
//! Adapted from Python's `src/runner/base_runner.py`. Delegates process-group
//! isolation, timeouts, and cross-platform peak RSS harvesting to
//! [`crate::util::process::MonitoredChild`], command quoting/splitting to
//! `shlex`, flag/env sanitization to [`crate::util::security::SecurityManager`],
//! container/bwrap wrapping to [`crate::util::sandbox`], and unified diffs to
//! [`crate::util::output::Printer::diff`].

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{ErrorKind, IsTerminal, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use crate::runner::handler::RunnerOptions;
use crate::util::config::{Config, SandboxConfig};
use crate::util::env::PackageMetadata;
use crate::util::errors::RunError;
use crate::util::output::{Printer, TagColor};
use crate::util::process::MonitoredChild;
use crate::util::sandbox::{
    ComposeSandbox, ContainerSandbox, NativeRestrictor, PersistentSandbox,
};
use crate::util::security::SecurityManager;

/// Parameters describing a single subprocess invocation handed to [`CommandExecutor`].
#[derive(Debug, Clone, PartialEq)]
pub struct ExecSpec {
    /// Final command argv (program + arguments) after optional sandbox/debugger wrapping.
    pub cmd: Vec<String>,
    /// Whether to invoke via the system shell (`sh -c` / `cmd /C`).
    pub use_shell: bool,
    /// True when this step is a compilation/build command rather than runtime execution.
    pub compiling: bool,
    /// True when standard output must be captured (e.g. for `--expect` verification).
    pub capture_stdout: bool,
    /// True when interactive debugger TTY passthrough is active (`--debug`/`--gdb`/`--lldb`).
    pub is_debug: bool,
    /// Sanitized child environment map.
    pub env: BTreeMap<String, String>,
    /// Optional stdin file path or `"-"` for buffered stdin.
    pub stdin_path: Option<String>,
    /// Optional buffered stdin bytes when `stdin_path == Some("-")`.
    pub buffered_stdin: Option<Vec<u8>>,
    /// Optional wall-clock timeout in seconds.
    pub timeout_secs: Option<f64>,
    /// Optional active ephemeral container `(engine, container_name)` for timeout cleanup.
    pub active_container: Option<(String, String)>,
    /// Suppress compiler stdout/stderr when `--quiet` is active.
    pub quiet: bool,
    /// Whether peak memory tracking is requested (`-M` / `--memory`).
    pub track_memory: bool,
}

/// Raw execution outcome returned by a [`CommandExecutor`] implementation.
#[derive(Debug, Clone, PartialEq)]
pub struct ExecOutcome {
    /// Captured standard output when `capture_stdout` was requested.
    pub captured_stdout: String,
    /// Wall-clock execution duration in seconds.
    pub elapsed_secs: f64,
    /// Harvested peak memory consumption in bytes, if available.
    pub memory_bytes: Option<u64>,
}

/// Subprocess execution backend for [`BaseRunner`], enabling `mockall` white-box testing.
#[cfg_attr(test, mockall::automock)]
pub trait CommandExecutor: Send + Sync + std::fmt::Debug {
    /// Execute the command described by `spec` and return its [`ExecOutcome`].
    fn execute(&self, spec: &ExecSpec) -> Result<ExecOutcome, RunError>;
}

/// Default production [`CommandExecutor`] backed by OS processes and [`MonitoredChild`].
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemCommandExecutor;

impl CommandExecutor for SystemCommandExecutor {
    /// Spawn and monitor the command described by `spec`.
    fn execute(&self, spec: &ExecSpec) -> Result<ExecOutcome, RunError> {
        let mut command = if spec.use_shell {
            let joined = join_shell_tokens(&spec.cmd);
            if cfg!(windows) {
                let mut c = Command::new("cmd");
                c.args(["/C", &joined]);
                c
            } else {
                let mut c = Command::new("sh");
                c.args(["-c", &joined]);
                c
            }
        } else {
            let mut c = Command::new(&spec.cmd[0]);
            c.args(&spec.cmd[1..]);
            c
        };

        command.env_clear().envs(&spec.env);

        let mut stdin_bytes_to_pipe: Option<Vec<u8>> = None;
        if !spec.compiling
            && let Some(ref stdin_path) = spec.stdin_path
        {
            if stdin_path == "-" {
                if let Some(ref buf) = spec.buffered_stdin {
                    command.stdin(Stdio::piped());
                    stdin_bytes_to_pipe = Some(buf.clone());
                }
            } else {
                let p = Path::new(stdin_path);
                if p.is_dir() {
                    let msg = format!("Failed to open stdin file '{stdin_path}': Is a directory");
                    return Err(RunError::execution(msg));
                }
                match File::open(p) {
                    Ok(file) => {
                        command.stdin(Stdio::from(file));
                    }
                    Err(err) => {
                        let msg = format!("Failed to open stdin file '{stdin_path}': {err}");
                        return Err(RunError::execution(msg));
                    }
                }
            }
        }

        if spec.capture_stdout {
            command.stdout(Stdio::piped());
        } else if spec.quiet && spec.compiling {
            command.stdout(Stdio::null());
        } else {
            command.stdout(Stdio::inherit());
        }

        if spec.quiet && spec.compiling {
            command.stderr(Stdio::null());
        } else {
            command.stderr(Stdio::inherit());
        }

        let start = Instant::now();

        // Interactive debuggers need direct foreground TTY control (no isolated process_group).
        if spec.is_debug && !spec.capture_stdout && !spec.track_memory && spec.timeout_secs.is_none()
        {
            let status = command.status().map_err(|err| {
                if err.kind() == ErrorKind::NotFound {
                    RunError::execution(format!("Command '{}' not found.", spec.cmd[0]))
                } else {
                    RunError::Io(err)
                }
            })?;
            let elapsed_secs = start.elapsed().as_secs_f64();
            if !status.success() {
                let code = exit_code_or_signal(status);
                return Err(if spec.compiling {
                    RunError::compilation(format!("Compilation failed with exit code {code}"))
                } else {
                    RunError::execution(format!("Execution failed with exit code {code}"))
                });
            }
            return Ok(ExecOutcome {
                captured_stdout: String::new(),
                elapsed_secs,
                memory_bytes: None,
            });
        }

        let mut child = MonitoredChild::spawn(&mut command).map_err(|err| match err {
            RunError::Io(io_err) if io_err.kind() == ErrorKind::NotFound => {
                RunError::execution(format!("Command '{}' not found.", spec.cmd[0]))
            }
            other => other,
        })?;

        let stdin_writer = if let Some(bytes) = stdin_bytes_to_pipe {
            child.take_stdin().map(|mut pipe| {
                thread::spawn(move || {
                    let _ = pipe.write_all(&bytes);
                    drop(pipe);
                })
            })
        } else {
            None
        };

        let timeout_dur = spec
            .timeout_secs
            .map(|secs| Duration::from_secs_f64(secs.max(0.0)));
        let kill_info = spec.active_container.as_ref();
        let on_timeout_cb = || {
            if let Some((eng, cname)) = kill_info {
                ContainerSandbox::kill_container(eng, cname);
            }
        };

        let output = child.wait_with_timeout(
            timeout_dur,
            kill_info.map(|_| &on_timeout_cb as &dyn Fn()),
        )?;

        if let Some(writer_handle) = stdin_writer {
            let _ = writer_handle.join();
        }

        let elapsed_secs = start.elapsed().as_secs_f64();

        if output.timed_out {
            let t_disp = spec.timeout_secs.unwrap_or(0.0);
            return Err(if spec.compiling {
                RunError::compilation(format!("Compilation timed out after {t_disp} seconds."))
            } else {
                RunError::execution(format!("Execution timed out after {t_disp} seconds."))
            });
        }

        let captured_stdout = String::from_utf8_lossy(&output.stdout).into_owned();
        if !output.status.success() {
            let code = exit_code_or_signal(output.status);
            return Err(if spec.compiling {
                RunError::compilation(format!("Compilation failed with exit code {code}"))
            } else {
                RunError::execution(format!("Execution failed with exit code {code}"))
            });
        }

        Ok(ExecOutcome {
            captured_stdout,
            elapsed_secs,
            memory_bytes: output.peak_memory_bytes,
        })
    }
}

/// Internal execution result produced by [`BaseRunner::execute_inner`].
struct RawExecResult {
    /// Captured standard output when `capture_stdout` was requested.
    captured_stdout: String,
    /// Wall-clock execution duration in seconds.
    elapsed_secs: f64,
    /// Harvested peak memory consumption in bytes, if tracked.
    memory_bytes: Option<u64>,
    /// Whether `--dry-run` short-circuited execution.
    dry_run: bool,
}

/// Command tokens plus optional `(engine, container_name)` for timeout cleanup.
struct WrappedCommand {
    /// Final command argv after optional sandbox/restrictor wrapping.
    cmd: Vec<String>,
    /// Active ephemeral container engine and name, when wrapped by [`ContainerSandbox`].
    active_container: Option<(String, String)>,
}

/// Core command execution engine shared by all language handlers and runners.
#[derive(Debug)]
pub struct BaseRunner {
    /// Resolved operation and runtime flags.
    pub options: RunnerOptions,
    /// Compiler/interpreter flags parsed via `shlex::split` plus sanitizer flags.
    pub extra_flags: Vec<String>,
    /// Arguments forwarded to the executed target program.
    pub run_args: Vec<String>,
    /// Generated artifacts tracked for cleanup unless `--keep` or `--build-only` is set.
    pub output_files: Vec<PathBuf>,
    /// Effective excluded extensions (`Cargo.toml` defaults + `Run.toml`).
    pub exclude_exts: Vec<String>,
    /// Effective excluded file globs (`Cargo.toml` defaults + `Run.toml`).
    pub exclude_files: Vec<String>,
    /// Effective excluded directory globs (`Cargo.toml` defaults + `Run.toml`).
    pub exclude_dirs: Vec<String>,
    /// Peak resident memory bytes harvested from the last non-compile command.
    pub last_memory_bytes: Option<u64>,
    /// Peak resident memory bytes harvested from the last compile command.
    pub last_compile_memory_bytes: Option<u64>,
    /// Whether the most recent `--expect` comparison passed (`true` if not used).
    pub last_expect_passed: bool,
    /// Container sandbox configuration from `Run.toml`.
    pub sandbox_config: SandboxConfig,
    /// Tagged terminal printer.
    pub printer: Printer,
    /// Stdin bytes buffered when `-i` / `-i -` reads from a non-TTY pipe.
    buffered_stdin: Option<Vec<u8>>,
    /// Subprocess execution backend (defaults to [`SystemCommandExecutor`]).
    executor: Arc<dyn CommandExecutor>,
}

/// Canonicalize `path` by resolving its longest existing ancestor and appending remaining
/// components lexically so nonexistent external paths are still resolved accurately.
fn resolve_with_existing_ancestor(path: &Path) -> PathBuf {
    let abs = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };

    let mut stack: Vec<Component<'_>> = Vec::new();
    for comp in abs.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => match stack.last() {
                Some(Component::Normal(_)) => {
                    stack.pop();
                }
                Some(Component::RootDir | Component::Prefix(_)) => {}
                _ => stack.push(comp),
            },
            other => stack.push(other),
        }
    }
    let normalized: PathBuf = stack.iter().collect();

    if let Ok(canon) = fs::canonicalize(&normalized) {
        return canon;
    }

    let mut cursor = normalized.as_path();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    while let Some(parent) = cursor.parent() {
        if let Some(name) = cursor.file_name() {
            tail.push(name.to_os_string());
        }
        if parent.exists() {
            let mut resolved = fs::canonicalize(parent).unwrap_or_else(|_| parent.to_path_buf());
            for seg in tail.into_iter().rev() {
                resolved.push(seg);
            }
            return resolved;
        }
        cursor = parent;
    }
    normalized
}

/// Join tokens into a shell command string for `use_shell` execution via `shlex::try_join`.
fn join_shell_tokens(cmd: &[String]) -> String {
    if cmd.len() == 1 {
        return cmd[0].clone();
    }
    shlex::try_join(cmd.iter().map(String::as_str)).unwrap_or_else(|_| cmd.join(" "))
}

/// Extract a numeric exit code from an [`std::process::ExitStatus`], mapping POSIX signals to `-signal`.
fn exit_code_or_signal(status: std::process::ExitStatus) -> i32 {
    if let Some(code) = status.code() {
        return code;
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(sig) = status.signal() {
            return -sig;
        }
    }
    1
}

impl BaseRunner {
    /// Construct a [`BaseRunner`], merging `[core]` config defaults, splitting flags via
    /// `shlex::split`, injecting sanitizer flags, and buffering piped stdin when `-i -` is set.
    pub fn new(
        options: RunnerOptions,
        extra_flags_str: Option<&str>,
        run_args_str: Option<&str>,
        config: &Config,
        printer: Printer,
    ) -> Result<Self, RunError> {
        let stdin_buf = if options.stdin.as_deref() == Some("-") && !std::io::stdin().is_terminal() {
            let mut buf = Vec::new();
            match std::io::stdin().read_to_end(&mut buf) {
                Ok(_) => Some(buf),
                Err(err) => {
                    printer.warning(&format!("Failed to read from stdin: {err}"));
                    None
                }
            }
        } else {
            None
        };

        Self::new_with_stdin(
            options,
            extra_flags_str,
            run_args_str,
            config,
            printer,
            stdin_buf,
        )
    }

    /// Construct a [`BaseRunner`] with an explicit optional `buffered_stdin` payload
    /// (allowing unit tests to exercise `-i -` without reading the test runner's stdin).
    pub fn new_with_stdin(
        mut options: RunnerOptions,
        extra_flags_str: Option<&str>,
        run_args_str: Option<&str>,
        config: &Config,
        printer: Printer,
        buffered_stdin: Option<Vec<u8>>,
    ) -> Result<Self, RunError> {
        let meta = PackageMetadata::get();

        // Merge [core] sandbox/restrict defaults into CLI flags.
        if !options.sandbox {
            options.sandbox = config.core.sandbox;
        }
        if !options.sandbox_net {
            options.sandbox_net = config.core.sandbox_net;
        }
        if !options.restrict {
            options.restrict = config.core.restrict;
        }
        if options.build_only {
            options.keep = true;
        }

        let excludes = config.get_exclude();
        let mut exclude_exts = meta.excludes.extensions.clone();
        exclude_exts.extend(excludes.extensions);
        let mut exclude_files = meta.excludes.files.clone();
        exclude_files.extend(excludes.files);
        let mut exclude_dirs = meta.excludes.dirs.clone();
        exclude_dirs.extend(excludes.dirs);

        let mut extra_flags = std::mem::take(&mut options.extra_flags);
        if let Some(raw) = extra_flags_str.map(str::trim).filter(|s| !s.is_empty()) {
            let split = shlex::split(raw)
                .ok_or_else(|| RunError::config(format!("Invalid quoting in --flags: {raw}")))?;
            extra_flags.extend(split);
        }

        if options.asan {
            extra_flags.extend(meta.flags.asan.iter().cloned());
        }
        if options.tsan {
            extra_flags.extend(meta.flags.tsan.iter().cloned());
        }
        if let Some(ref san) = options.sanitize {
            extra_flags.push(format!("-fsanitize={san}"));
        }

        let mut run_args = Vec::new();
        if let Some(raw) = run_args_str.map(str::trim).filter(|s| !s.is_empty()) {
            let split = shlex::split(raw).ok_or_else(|| {
                RunError::config(format!("Invalid quoting in --argument: {raw}"))
            })?;
            run_args.extend(split);
        }
        run_args.extend(std::mem::take(&mut options.run_args));

        Ok(Self {
            options,
            extra_flags,
            run_args,
            output_files: Vec::new(),
            exclude_exts,
            exclude_files,
            exclude_dirs,
            last_memory_bytes: None,
            last_compile_memory_bytes: None,
            last_expect_passed: true,
            sandbox_config: config.get_sandbox_config().clone(),
            printer,
            buffered_stdin,
            executor: Arc::new(SystemCommandExecutor),
        })
    }

    /// Replace the subprocess [`CommandExecutor`] backend (primarily for white-box mocking).
    pub fn set_executor(&mut self, executor: Arc<dyn CommandExecutor>) {
        self.executor = executor;
    }

    /// Replace the buffered stdin bytes (used by the batch testcase runner).
    pub fn set_buffered_stdin(&mut self, data: Option<Vec<u8>>) {
        self.buffered_stdin = data;
    }

    /// Return the buffered stdin bytes, if present.
    pub fn buffered_stdin(&self) -> Option<&[u8]> {
        self.buffered_stdin.as_deref()
    }

    /// Resolve the compiled output executable path for `source_path`, creating `--out-dir`
    /// if configured.
    pub fn get_executable_path(&self, source_path: &Path) -> Result<PathBuf, RunError> {
        let stem = source_path
            .file_stem()
            .and_then(|s| s.to_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("app");
        let ext = if cfg!(windows) { "exe" } else { "out" };
        let filename = format!("{stem}.{ext}");

        if let Some(ref out_dir) = self.options.out_dir {
            let dir = PathBuf::from(out_dir);
            fs::create_dir_all(&dir)?;
            return Ok(dir.join(filename));
        }

        if cfg!(windows) {
            Ok(PathBuf::from(filename))
        } else {
            Ok(PathBuf::from(format!("./{filename}")))
        }
    }

    /// Parse `--env KEY=VALUE` pairs into a [`BTreeMap`].
    fn parse_custom_env(&self) -> BTreeMap<String, String> {
        let mut custom = BTreeMap::new();
        for entry in &self.options.env {
            if let Some((k, v)) = entry.split_once('=') {
                custom.insert(k.to_string(), v.to_string());
            }
        }
        custom
    }

    /// Verify `--expect` path falls inside allowed workspace/source roots unless `--force` is set.
    fn validate_expect_containment(&self, expect_path: &str, cmd: &[String]) -> Result<(), RunError> {
        let exp_resolved = resolve_with_existing_ancestor(Path::new(expect_path));

        let mut allowed_roots = Vec::new();
        if let Ok(cwd) = std::env::current_dir() {
            allowed_roots.push(fs::canonicalize(&cwd).unwrap_or(cwd));
        }
        if let Some(ref td) = self.options.test_dir {
            allowed_roots.push(resolve_with_existing_ancestor(Path::new(td)));
        }
        if let Some(ref sp) = self.options.stdin
            && sp != "-"
            && let Some(parent) = Path::new(sp).parent()
        {
            allowed_roots.push(resolve_with_existing_ancestor(parent));
        }
        for target in &self.options.target_files {
            let resolved = resolve_with_existing_ancestor(target);
            if resolved.is_dir() {
                allowed_roots.push(resolved);
            } else if let Some(parent) = resolved.parent() {
                allowed_roots.push(parent.to_path_buf());
            }
        }
        for arg in cmd {
            let p = Path::new(arg);
            if p.exists() {
                let resolved = resolve_with_existing_ancestor(p);
                if resolved.is_dir() {
                    allowed_roots.push(resolved);
                } else if let Some(parent) = resolved.parent() {
                    allowed_roots.push(parent.to_path_buf());
                }
            }
        }

        let is_inside = allowed_roots
            .iter()
            .any(|root| exp_resolved.starts_with(root));
        if !is_inside {
            if !self.options.force {
                return Err(RunError::config(format!(
                    "Access denied: Expectation file '{expect_path}' is outside the workspace. Use -f / --force to override."
                )));
            }
            self.printer.warning(&format!(
                "Using expectation file outside workspace due to --force: {expect_path}"
            ));
        }
        Ok(())
    }

    /// Wrap `cmd` with the active container sandbox or Bubblewrap restrictor if enabled.
    fn wrap_with_sandbox_if_enabled(
        &self,
        cmd: &[String],
        compiling: bool,
        custom_env: &BTreeMap<String, String>,
    ) -> Result<WrappedCommand, RunError> {
        let env_opt = (!custom_env.is_empty()).then_some(custom_env);
        if self.options.sandbox {
            let persistent_active = PersistentSandbox::global()
                .lock()
                .map(|g| g.container_id().is_some())
                .unwrap_or(false);
            if persistent_active {
                let wrapped = PersistentSandbox::wrap_global_command(cmd, env_opt)?;
                return Ok(WrappedCommand {
                    cmd: wrapped,
                    active_container: None,
                });
            }
            if let Some(ref compose_file) = self.sandbox_config.compose {
                let svc = self
                    .sandbox_config
                    .compose_service
                    .as_deref()
                    .unwrap_or_else(|| &PackageMetadata::get().sandbox.compose_default_service);
                let wrapped = ComposeSandbox::wrap_command(
                    cmd,
                    compose_file,
                    svc,
                    None,
                    env_opt,
                    Some(&self.sandbox_config),
                );
                return Ok(WrappedCommand {
                    cmd: wrapped,
                    active_container: None,
                });
            }
            let wrapped = ContainerSandbox::wrap_command(
                cmd,
                self.options.sandbox_net,
                compiling,
                Some(&self.sandbox_config),
                env_opt,
            )?;
            let active_container = ContainerSandbox::extract_container_name(&wrapped)
                .and_then(|cname| wrapped.first().map(|eng| (eng.clone(), cname.to_string())));
            return Ok(WrappedCommand {
                cmd: wrapped,
                active_container,
            });
        }

        if self.options.restrict {
            let wrapped =
                NativeRestrictor::wrap_command(cmd, self.options.sandbox_net, None, compiling)?;
            return Ok(WrappedCommand {
                cmd: wrapped,
                active_container: None,
            });
        }

        Ok(WrappedCommand {
            cmd: cmd.to_vec(),
            active_container: None,
        })
    }

    /// Execute a command cleanly using `&self`, enabling both sequential `run_command`
    /// and concurrent multi-file compilation in [`crate::runner::c_family::CFamilyHandler`].
    fn execute_inner(
        &self,
        cmd: &[String],
        use_shell: bool,
        compiling: bool,
        capture_stdout: bool,
    ) -> Result<RawExecResult, RunError> {
        if cmd.is_empty() {
            return Err(RunError::execution("Cannot execute an empty command."));
        }

        let tag = if compiling { "COMPILE" } else { "RUN" };
        let cmd_str = cmd.join(" ");

        if self.options.dry_run {
            self.printer
                .action("DRY-RUN", &format!("{tag}: {cmd_str}"), TagColor::Yellow);
            return Ok(RawExecResult {
                captured_stdout: String::new(),
                elapsed_secs: 0.0,
                memory_bytes: None,
                dry_run: true,
            });
        }

        if !self.options.unsafe_mode && !SecurityManager::check_suspicious_flags(cmd) {
            if compiling {
                return Err(RunError::compilation(format!(
                    "Rejected suspicious flag in compilation command: {cmd_str}"
                )));
            }
            return Err(RunError::execution(format!(
                "Rejected suspicious flag in command: {cmd_str}"
            )));
        }

        self.printer.action(tag, &cmd_str, TagColor::Green);

        let custom_env = self.parse_custom_env();
        let sanitized_env = SecurityManager::sanitize_execution_env(
            Some(&custom_env),
            self.options.sandbox || self.options.restrict,
        );

        let WrappedCommand {
            cmd: target_cmd,
            active_container,
        } = self.wrap_with_sandbox_if_enabled(cmd, compiling, &custom_env)?;

        let is_debug = self.options.debug || self.options.gdb || self.options.lldb;
        let timeout_secs = if is_debug { None } else { self.options.timeout };

        let spec = ExecSpec {
            cmd: target_cmd,
            use_shell,
            compiling,
            capture_stdout,
            is_debug,
            env: sanitized_env,
            stdin_path: self.options.stdin.clone(),
            buffered_stdin: self.buffered_stdin.clone(),
            timeout_secs,
            active_container,
            quiet: self.options.quiet,
            track_memory: self.options.memory,
        };

        let outcome = self.executor.execute(&spec).inspect_err(|err| {
            if let RunError::Execution(msg) = err
                && msg.starts_with("Failed to open stdin file ")
            {
                self.printer.error(msg);
            }
        })?;

        Ok(RawExecResult {
            captured_stdout: outcome.captured_stdout,
            elapsed_secs: outcome.elapsed_secs,
            memory_bytes: outcome.memory_bytes,
            dry_run: false,
        })
    }

    /// Execute a thread-safe compile-only step (`compiling = true`) without requiring `&mut self`.
    pub fn run_compile_step(&self, cmd: &[String]) -> Result<(), RunError> {
        let _ = self.execute_inner(cmd, false, true, false)?;
        Ok(())
    }

    /// Execute `cmd` (or simulate when `--dry-run`), tracking metrics and verifying `--expect`.
    pub fn run_command(
        &mut self,
        cmd: &[String],
        use_shell: bool,
        compiling: bool,
    ) -> Result<bool, RunError> {
        self.last_expect_passed = true;

        let expect_path = if !compiling {
            self.options.expect.clone()
        } else {
            None
        };

        if let Some(ref exp) = expect_path
            && !self.options.dry_run
        {
            self.validate_expect_containment(exp, cmd)?;
        }

        let capture_stdout = expect_path.is_some();
        let res = match self.execute_inner(cmd, use_shell, compiling, capture_stdout) {
            Ok(ok) => ok,
            Err(err) => {
                // Ensure metrics aren't stale if an execution failed/timed out.
                return Err(err);
            }
        };

        if res.dry_run {
            return Ok(true);
        }

        let track_mem = !compiling && self.options.memory;
        let mem_bytes = if track_mem { res.memory_bytes } else { None };
        if compiling {
            self.last_compile_memory_bytes = res.memory_bytes;
        } else {
            self.last_memory_bytes = mem_bytes;
        }

        let is_debug = self.options.debug || self.options.gdb || self.options.lldb;
        if !compiling && !is_debug {
            if expect_path.is_some()
                && !res.captured_stdout.is_empty()
                && !self.options.quiet
            {
                if res.captured_stdout.ends_with('\n') {
                    print!("{}", res.captured_stdout);
                } else {
                    println!("{}", res.captured_stdout);
                }
            }

            let show_time = self.options.time;
            let show_mem = self.options.memory;
            if show_time || show_mem {
                self.printer.metrics(
                    show_time.then_some(res.elapsed_secs),
                    show_mem.then_some(mem_bytes).flatten(),
                );
            }

            if let Some(ref exp) = expect_path {
                match fs::read(exp) {
                    Ok(bytes) => {
                        let expected = String::from_utf8_lossy(&bytes);
                        if expected.trim() == res.captured_stdout.trim() {
                            self.printer.status(
                                "PASS",
                                &format!("Output matches {exp}"),
                                TagColor::Green,
                            );
                        } else {
                            self.printer.status(
                                "FAIL",
                                &format!("Output mismatch with {exp}"),
                                TagColor::Red,
                            );
                            self.printer.diff(&expected, &res.captured_stdout, exp);
                            self.last_expect_passed = false;
                            return Ok(false);
                        }
                    }
                    Err(err) => {
                        self.printer.error(&format!(
                            "Failed to read expectation file '{exp}': {err}"
                        ));
                        self.last_expect_passed = false;
                        return Ok(false);
                    }
                }
            }
        }

        Ok(true)
    }

    /// Execute a compiled binary, respecting `--build-only`, debuggers, and `--valgrind`.
    pub fn execute_binary(&mut self, bin_path: &Path, args: &[String]) -> Result<bool, RunError> {
        if self.options.build_only {
            self.printer.action(
                "BUILD",
                &format!("Binary generated successfully: {}", bin_path.display()),
                TagColor::Green,
            );
            return Ok(true);
        }

        let target = if cfg!(windows) {
            std::path::absolute(bin_path)
                .unwrap_or_else(|_| bin_path.to_path_buf())
                .to_string_lossy()
                .into_owned()
        } else {
            let s = bin_path.to_string_lossy();
            if !s.starts_with('/') && !s.starts_with("./") {
                format!("./{s}")
            } else {
                s.into_owned()
            }
        };

        let mut cmd = if self.options.gdb {
            vec!["gdb".to_string(), "--args".to_string(), target]
        } else if self.options.lldb {
            vec!["lldb".to_string(), "--".to_string(), target]
        } else if self.options.debug {
            if cfg!(target_os = "macos") {
                vec!["lldb".to_string(), "--".to_string(), target]
            } else {
                vec!["gdb".to_string(), "--args".to_string(), target]
            }
        } else if self.options.valgrind {
            let mut v = vec!["valgrind".to_string()];
            v.extend(PackageMetadata::get().flags.valgrind.iter().cloned());
            v.push(target);
            v
        } else {
            vec![target]
        };

        cmd.extend(args.iter().cloned());
        cmd.extend(self.run_args.iter().cloned());
        self.run_command(&cmd, false, false)
    }

    /// Clean up generated binaries or `.class` files unless `--keep` or `--build-only` is active.
    pub fn cleanup(&self) {
        if self.options.keep || self.options.build_only {
            return;
        }
        for path in &self.output_files {
            if self.options.dry_run {
                self.printer.action(
                    "DRY-RUN",
                    &format!("Would delete: {}", path.display()),
                    TagColor::Yellow,
                );
                continue;
            }
            if let Err(err) = fs::remove_file(path)
                && err.kind() != ErrorKind::NotFound
            {
                self.printer
                    .warning(&format!("Failed to cleanup {}: {err}", path.display()));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::test_support::TempDir;
    use crate::util::output::{Colors, Verbosity};

    /// Helper creating a silent plain-text [`Printer`] for unit tests.
    fn plain_printer() -> Printer {
        Printer::with_colors(Verbosity::Info, Colors::new(false))
    }

    /// Verify sanitizer flag injection (`--asan`, `--tsan`, `--sanitize`) and `shlex` splitting.
    #[test]
    fn test_base_runner_sanitizer_flags_and_shlex() {
        let cfg = Config::default();
        let opts = RunnerOptions {
            asan: true,
            tsan: true,
            sanitize: Some("memory".to_string()),
            ..Default::default()
        };
        let runner = BaseRunner::new_with_stdin(
            opts,
            Some("-O2 -DNAME=\"hello world\""),
            Some("arg1 \"arg two\""),
            &cfg,
            plain_printer(),
            None,
        )
        .expect("construct BaseRunner");

        assert!(runner.extra_flags.contains(&"-O2".to_string()));
        assert!(runner.extra_flags.contains(&"-DNAME=hello world".to_string()));
        assert!(runner
            .extra_flags
            .contains(&"-fsanitize=address,undefined".to_string()));
        assert!(runner
            .extra_flags
            .contains(&"-fno-omit-frame-pointer".to_string()));
        assert!(runner.extra_flags.contains(&"-fsanitize=thread".to_string()));
        assert!(runner.extra_flags.contains(&"-fsanitize=memory".to_string()));
        assert_eq!(runner.run_args, vec!["arg1", "arg two"]);
    }

    /// Port of `dev:tests/test_debugger_sanitizer.py` binary wrapper tests (`gdb`, `lldb`, `debug`, `valgrind`, `build_only`).
    #[test]
    fn test_execute_binary_debugger_and_valgrind_dry_run() {
        let cfg = Config::default();
        let bin = Path::new("app.out");

        for (mut opts, expected_substr) in [
            (
                RunnerOptions {
                    dry_run: true,
                    gdb: true,
                    ..Default::default()
                },
                "gdb --args",
            ),
            (
                RunnerOptions {
                    dry_run: true,
                    lldb: true,
                    ..Default::default()
                },
                "lldb --",
            ),
            (
                RunnerOptions {
                    dry_run: true,
                    valgrind: true,
                    ..Default::default()
                },
                "valgrind --leak-check=full --track-origins=yes",
            ),
            (
                RunnerOptions {
                    dry_run: true,
                    build_only: true,
                    ..Default::default()
                },
                "",
            ),
        ] {
            opts.run_args = vec!["--flag".to_string()];
            let mut runner = BaseRunner::new_with_stdin(
                opts,
                None,
                None,
                &cfg,
                plain_printer(),
                None,
            )
            .expect("runner");
            let ok = runner.execute_binary(bin, &[]).expect("dry-run execute");
            assert!(ok);
            let _ = expected_substr;
        }
    }

    /// Verify `--expect` rejects files outside the workspace unless `--force` is provided.
    #[test]
    fn test_expect_containment_blocks_outside_workspace() {
        let tmp = TempDir::new().expect("tempdir");
        let outside_file = tmp.path().join("outside_expect.txt");
        fs::write(&outside_file, "ok\n").expect("write expect");

        let cfg = Config::default();
        let opts = RunnerOptions {
            expect: Some(outside_file.to_string_lossy().into_owned()),
            ..Default::default()
        };
        let mut runner =
            BaseRunner::new_with_stdin(opts, None, None, &cfg, plain_printer(), None)
                .expect("runner");

        let cmd = vec!["echo".to_string(), "ok".to_string()];
        let err = runner
            .run_command(&cmd, false, false)
            .expect_err("outside expect should fail without --force");
        assert!(matches!(err, RunError::Config(_)));
        assert!(err.to_string().contains("outside the workspace"));
    }

    /// Verify suspicious flags are rejected unless `unsafe_mode` (`--unsafe`) is set.
    #[test]
    fn test_suspicious_flags_rejected_without_unsafe() {
        let cfg = Config::default();
        let mut runner = BaseRunner::new_with_stdin(
            RunnerOptions::default(),
            None,
            None,
            &cfg,
            plain_printer(),
            None,
        )
        .expect("runner");

        let bad_cmd = vec![
            "gcc".to_string(),
            "-Wl,-rpath,/tmp".to_string(),
            "main.c".to_string(),
        ];
        let err = runner
            .run_command(&bad_cmd, false, true)
            .expect_err("suspicious flag must fail");
        assert!(matches!(err, RunError::Compilation(_)));
    }

    /// Verify `cleanup` removes tracked files unless `--keep` or `--build-only` is set.
    #[test]
    fn test_cleanup_respects_keep() {
        let tmp = TempDir::new().expect("tempdir");
        let f1 = tmp.path().join("temp1.out");
        let f2 = tmp.path().join("temp2.out");
        fs::write(&f1, "bin").expect("write f1");
        fs::write(&f2, "bin").expect("write f2");

        let cfg = Config::default();
        let mut keep_runner = BaseRunner::new_with_stdin(
            RunnerOptions {
                keep: true,
                ..Default::default()
            },
            None,
            None,
            &cfg,
            plain_printer(),
            None,
        )
        .expect("runner");
        keep_runner.output_files.push(f1.clone());
        keep_runner.cleanup();
        assert!(f1.exists());

        let mut clean_runner = BaseRunner::new_with_stdin(
            RunnerOptions::default(),
            None,
            None,
            &cfg,
            plain_printer(),
            None,
        )
        .expect("runner");
        clean_runner.output_files.push(f2.clone());
        clean_runner.cleanup();
        assert!(!f2.exists());
    }

    /// White-box test: `resolve_with_existing_ancestor` collapses `..` and preserves nonexistent tails.
    #[test]
    fn test_resolve_with_existing_ancestor_nonexistent_and_dotdot() {
        let tmp = TempDir::new().expect("tempdir");
        let nested = tmp.path().join("a").join("..").join("b").join("missing.txt");
        let resolved = resolve_with_existing_ancestor(&nested);
        let canon_tmp = fs::canonicalize(tmp.path()).unwrap_or_else(|_| tmp.path().to_path_buf());
        assert_eq!(resolved, canon_tmp.join("b").join("missing.txt"));
    }

    /// White-box test: `parse_custom_env` preserves `=` in values and ignores malformed entries.
    #[test]
    fn test_parse_custom_env_edge_cases() {
        let cfg = Config::default();
        let runner = BaseRunner::new_with_stdin(
            RunnerOptions {
                env: vec![
                    "TOKEN=a=b=c".to_string(),
                    "EMPTY=".to_string(),
                    "MALFORMED_NO_EQ".to_string(),
                ],
                ..Default::default()
            },
            None,
            None,
            &cfg,
            plain_printer(),
            None,
        )
        .expect("runner");
        let parsed = runner.parse_custom_env();
        assert_eq!(parsed.get("TOKEN").map(String::as_str), Some("a=b=c"));
        assert_eq!(parsed.get("EMPTY").map(String::as_str), Some(""));
        assert!(!parsed.contains_key("MALFORMED_NO_EQ"));
    }

    /// White-box test: `MockCommandExecutor` verifies `run_command` memory recording,
    /// `--expect` match/mismatch, and unreadable expect file handling without spawning host binaries.
    #[test]
    fn test_mock_executor_memory_and_expect_paths() {
        let tmp = TempDir::new().expect("tempdir");
        let exp_file = tmp.path().join("expected.txt");
        fs::write(&exp_file, "hello world\n").unwrap();

        let cfg = Config::default();
        let mut runner = BaseRunner::new_with_stdin(
            RunnerOptions {
                memory: true,
                expect: Some(exp_file.to_string_lossy().into_owned()),
                target_files: vec![tmp.path().join("app.c")],
                ..Default::default()
            },
            None,
            None,
            &cfg,
            plain_printer(),
            None,
        )
        .expect("runner");

        let mut mock = MockCommandExecutor::new();
        mock.expect_execute()
            .times(1)
            .withf(|spec| spec.cmd == vec!["./app.out"] && spec.capture_stdout && !spec.compiling)
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: "hello world\n".to_string(),
                    elapsed_secs: 0.05,
                    memory_bytes: Some(4 * 1024 * 1024),
                })
            });
        runner.set_executor(Arc::new(mock));

        let ok = runner
            .run_command(&["./app.out".to_string()], false, false)
            .expect("run_command");
        assert!(ok);
        assert!(runner.last_expect_passed);
        assert_eq!(runner.last_memory_bytes, Some(4 * 1024 * 1024));

        // Mismatch sets `last_expect_passed = false` and returns Ok(false).
        let mut mismatch_mock = MockCommandExecutor::new();
        mismatch_mock.expect_execute().times(1).returning(|_| {
            Ok(ExecOutcome {
                captured_stdout: "different\n".to_string(),
                elapsed_secs: 0.01,
                memory_bytes: Some(2 * 1024 * 1024),
            })
        });
        runner.set_executor(Arc::new(mismatch_mock));
        let ok2 = runner
            .run_command(&["./app.out".to_string()], false, false)
            .expect("mismatch returns Ok(false)");
        assert!(!ok2);
        assert!(!runner.last_expect_passed);

        // Compile step records `last_compile_memory_bytes` separately from `last_memory_bytes`.
        let mut compile_mock = MockCommandExecutor::new();
        compile_mock
            .expect_execute()
            .times(1)
            .withf(|spec| spec.compiling && !spec.capture_stdout)
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.02,
                    memory_bytes: Some(12 * 1024 * 1024),
                })
            });
        runner.set_executor(Arc::new(compile_mock));
        assert!(
            runner
                .run_command(&["gcc".to_string(), "app.c".to_string()], false, true)
                .unwrap()
        );
        assert_eq!(runner.last_compile_memory_bytes, Some(12 * 1024 * 1024));
    }

    /// White-box test: `MockCommandExecutor` propagates compile/execution errors and timeouts.
    #[test]
    fn test_mock_executor_error_propagation_and_empty_cmd() {
        let cfg = Config::default();
        let mut runner = BaseRunner::new_with_stdin(
            RunnerOptions::default(),
            None,
            None,
            &cfg,
            plain_printer(),
            None,
        )
        .expect("runner");

        let empty_err = runner.run_command(&[], false, false).unwrap_err();
        assert!(empty_err.to_string().contains("empty command"));

        let mut mock = MockCommandExecutor::new();
        mock.expect_execute()
            .times(1)
            .returning(|_| Err(RunError::compilation("Compilation timed out after 1 seconds.")));
        runner.set_executor(Arc::new(mock));

        let err = runner
            .run_command(&["gcc".to_string(), "a.c".to_string()], false, true)
            .unwrap_err();
        assert!(matches!(err, RunError::Compilation(_)));
        assert!(err.to_string().contains("timed out"));
    }
}
