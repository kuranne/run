//! Binary entry point for the `run` CLI.
//!
//! Separates argument parsing, CLI dispatch, sandbox lifecycle orchestration,
//! and `--watch` polling from the core execution library crate [`run`].

use std::collections::HashMap;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use crossterm::terminal;
use crossterm::tty::IsTty;

use run::core::handlers::c_family::CFamilyHandler;
use run::core::handlers::custom_handler::CustomLanguageHandler;
use run::core::registry::LanguageHandler;
use run::core::handlers::java_handler::JavaHandler;
use run::core::handlers::script_handler::ScriptHandler;
use run::core::{
    BenchRunner, CompilerRunner, InteractiveRunner, ProjectRunner, StressConfig, StressRunner,
    TaskRunner, TestcasesRunner,
};
use run::util::args::CliArgs;
use run::util::cache::CacheManager;
use run::util::completion::CompletionGenerator;
use run::util::config::{Config, SandboxConfig};
use run::util::doctor::Doctor;
use run::util::env::{load_dotenv, PackageMetadata};
use run::model::error::RunError;
use run::model::options::UiMode;
use run::model::report::JsonReport;
use run::util::init_config::ConfigInitializer;
use run::util::output::{set_tui_sink, Colors, Printer, TagColor, UiEvent, Verbosity};
use run::model::sandbox::SandboxEnv;
use run::util::sandbox::{ComposeSandbox, ContainerSandbox, PersistentSandbox, SystemSandboxEnv};
use run::util::security::SecurityManager;
use run::util::template_manager::TemplateManager;

/// Global termination flag flipped on `SIGINT` / Ctrl+C so watch mode exits cleanly.
static STOP_REQUESTED: AtomicBool = AtomicBool::new(false);

/// Async-signal-safe `SIGINT` / `SIGTERM` handler flagging termination and child cleanup.
#[cfg(unix)]
extern "C" fn handle_sigint(_sig: libc::c_int) {
    STOP_REQUESTED.store(true, Ordering::Relaxed);
    run::util::process::request_interrupt();
}

/// Register minimal `SIGINT` and `SIGTERM` handlers on Unix without `SA_RESTART`
/// so blocking waits wake up immediately to kill children and clean up artifacts.
#[cfg(unix)]
fn install_sigint_handler() {
    unsafe {
        let mut sa: libc::sigaction = std::mem::zeroed();
        sa.sa_sigaction = handle_sigint as *const () as usize;
        libc::sigemptyset(&mut sa.sa_mask);
        sa.sa_flags = 0; // No SA_RESTART so wait4 returns EINTR immediately.
        libc::sigaction(libc::SIGINT, &sa, std::ptr::null_mut());
        libc::sigaction(libc::SIGTERM, &sa, std::ptr::null_mut());
    }
}

/// Windows fallback stub (Ctrl+C during terminal polling is handled via `crossterm::event`).
#[cfg(not(unix))]
fn install_sigint_handler() {}

/// RAII guard restoring terminal raw mode cleanly even across early returns.
struct RawModeGuard(bool);

impl RawModeGuard {
    /// Enable raw mode if `stdin` is attached to an interactive terminal.
    fn enter_if_tty() -> Self {
        if io::stdin().is_tty() && terminal::enable_raw_mode().is_ok() {
            Self(true)
        } else {
            Self(false)
        }
    }
}

impl Drop for RawModeGuard {
    fn drop(&mut self) {
        if self.0 {
            let _ = terminal::disable_raw_mode();
        }
    }
}

/// Outcome returned by [`run_once`] to drive exit codes and `--watch` tracked files.
struct RunOutcome {
    /// Process exit code (`0` on success, `1` on config/compile/runtime failure).
    exit_code: i32,
    /// Candidate files whose modification times should be monitored in `--watch` mode.
    watch_files: Vec<PathBuf>,
    /// Cached piped stdin bytes for reuse across watch re-runs.
    captured_stdin: Option<Vec<u8>>,
}

/// Return `true` if `token` refers to an existing directory or runnable source/shebang file.
fn is_existing_runnable_target(token: &str, runner: &CompilerRunner) -> bool {
    let p = Path::new(token);
    if p.is_dir() {
        return true;
    }
    if !p.is_file() {
        return false;
    }
    if p.extension().is_some() {
        return runner.registry.get_handler(p, &runner.config).is_some()
            || CompilerRunner::is_executable_binary(p);
    }
    !ScriptHandler::detect_language_from_shebang(p, None).is_empty()
        || CompilerRunner::is_executable_binary(p)
}

/// Return `true` when auto-discovered files contain compiled/custom sources that should link together.
fn has_multi_compilable(files: &[PathBuf], config: &Config) -> bool {
    files.iter().any(|p| {
        let ext = p
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{}", e.to_ascii_lowercase()))
            .unwrap_or_default();
        (!ext.is_empty()
            && (CFamilyHandler::is_c_family_source_ext(&ext) || JavaHandler::is_java_ext(&ext)))
            || CustomLanguageHandler.can_handle(p, config)
    })
}

/// Apply sandbox CLI overrides onto a [`SandboxConfig`] instance.
fn apply_sandbox_overrides(sb: &mut SandboxConfig, cli: &CliArgs) {
    if let Some(ref img) = cli.sandbox_image {
        sb.image = Some(img.clone());
    }
    if let Some(ref df) = cli.sandbox_dockerfile {
        sb.dockerfile = Some(df.clone());
    }
    if let Some(ref comp) = cli.sandbox_compose {
        sb.compose = Some(comp.clone());
    }
    if let Some(ref svc) = cli.sandbox_service {
        sb.compose_service = Some(svc.clone());
    }
}

/// Read `-i -` stdin once before execution when stdin is piped so `--watch` can replay it.
fn read_piped_stdin_once(cli: &CliArgs) -> Option<Vec<u8>> {
    if cli.stdin.as_deref() == Some("-") && !io::stdin().is_tty() {
        let mut buf = Vec::new();
        if io::Read::read_to_end(&mut io::stdin(), &mut buf).is_ok() {
            return Some(buf);
        }
    }
    None
}

/// Collect relative paths of runnable source files under `cwd` for `fzf` selection.
fn collect_fzf_candidates(cwd: &Path, config: &Config, runner: &CompilerRunner) -> Vec<String> {
    let meta = PackageMetadata::get();
    let exclude = config.get_exclude();
    let mut results = Vec::new();
    let mut stack = vec![(cwd.to_path_buf(), 0usize)];

    while let Some((dir, depth)) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') {
                continue;
            }
            if path.is_dir() {
                if depth < 4
                    && !meta.excludes.discovery_ignore_dirs.iter().any(|d| d == &name)
                    && !exclude.dirs.iter().any(|d| d == &name)
                {
                    stack.push((path, depth + 1));
                }
            } else if path.is_file()
                && !exclude.files.iter().any(|f| f == &name)
                && is_existing_runnable_target(&path.to_string_lossy(), runner)
            {
                let rel = path
                    .strip_prefix(cwd)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .into_owned();
                results.push(rel);
            }
        }
    }

    results.sort();
    results
}

/// Launch interactive `fzf` picker on TTY when `fzf` is available on `PATH`.
///
/// Returns `Some(Some(path))` if a file was picked, `Some(None)` if the user
/// cancelled `fzf`, or `None` if `fzf` is unavailable / stdout+stdin are non-TTY.
fn try_fzf_picker(cwd: &Path, config: &Config, runner: &CompilerRunner) -> Option<Option<PathBuf>> {
    if !io::stdin().is_tty() || !io::stdout().is_tty() {
        return None;
    }
    let fzf_bin = SystemSandboxEnv.which("fzf")?;
    let candidates = collect_fzf_candidates(cwd, config, runner);
    if candidates.is_empty() {
        return None;
    }

    let mut child = std::process::Command::new(fzf_bin)
        .args([
            "--prompt",
            "run > ",
            "--height",
            "40%",
            "--layout",
            "reverse",
            "--border",
            "--exit-0",
        ])
        .current_dir(cwd)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .spawn()
        .ok()?;

    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(candidates.join("\n").as_bytes());
    }

    let output = child.wait_with_output().ok()?;
    if !output.status.success() {
        return Some(None);
    }
    let selected = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if selected.is_empty() {
        Some(None)
    } else {
        Some(Some(PathBuf::from(selected)))
    }
}

/// Prompt interactively (via `fzf` on TTY when installed, or line prompt fallback) when `run` is invoked with no target files or project.
fn prompt_for_file(
    cwd: &Path,
    config: &Config,
    runner: &CompilerRunner,
    ui_mode: UiMode,
) -> Option<PathBuf> {
    if let Some(fzf_choice) = try_fzf_picker(cwd, config, runner) {
        return fzf_choice;
    }

    if ui_mode == UiMode::Modern {
        print!("│  ? No file given. Enter file to run: ");
    } else {
        print!("[ INPUT ] No file given. Enter file to run: ");
    }
    let _ = io::stdout().flush();

    let mut line = String::new();
    match io::stdin().lock().read_line(&mut line) {
        Ok(0) | Err(_) => None,
        Ok(_) => {
            let trimmed = line.trim().trim_matches(|c| c == '"' || c == '\'');
            if trimmed.is_empty() {
                None
            } else {
                Some(PathBuf::from(trimmed))
            }
        }
    }
}

/// Perform sandbox setup (compose, Dockerfile build, or persistent container start) before execution.
fn prepare_sandbox(
    runner: &mut CompilerRunner,
    cli: &CliArgs,
    target_files: &[PathBuf],
    cwd: &Path,
) -> Result<(bool, Option<String>), RunError> {
    let mut sb = runner.base.sandbox_config.clone();
    apply_sandbox_overrides(&mut sb, cli);
    runner.base.sandbox_config = sb.clone();

    let effective_sandbox = runner.base.options.sandbox || sb.compose.is_some();
    if !effective_sandbox {
        return Ok((false, None));
    }

    if let Some(ref compose_file) = sb.compose {
        ComposeSandbox::setup(compose_file)?;
        return Ok((true, None));
    }

    let engine = ContainerSandbox::get_engine()?;

    if let Some(ref df_path) = sb.dockerfile {
        let image_tag = ContainerSandbox::build_dockerfile(Path::new(df_path), &engine)?;
        runner.base.sandbox_config.image = Some(image_tag.clone());
        sb.image = Some(image_tag);
    }

    if cli.sandbox_keep {
        let image = sb.image.clone().unwrap_or_else(|| {
            let first_ext = target_files
                .first()
                .and_then(|p| p.extension())
                .and_then(|e| e.to_str())
                .map(|e| format!(".{e}"))
                .unwrap_or_default();
            ContainerSandbox::get_heuristic_image_for_ext(&first_ext)
        });
        let allow_net = runner.base.options.sandbox_net;
        PersistentSandbox::start(
            &engine,
            &image,
            allow_net,
            Some(cwd),
            cli.sandbox_rebuild,
        )?;
        return Ok((false, None));
    }

    Ok((false, None))
}

/// Tear down ephemeral compose services started for this invocation unless `--sandbox-keep` was requested.
fn cleanup_sandbox(started_compose: bool, cli: &CliArgs) {
    if started_compose && !cli.sandbox_keep {
        ComposeSandbox::teardown_all();
    }
}

/// Snapshot modification times for all candidate watch paths, sibling C/C++ headers, and `Run.toml`.
fn collect_mtimes(paths: &[PathBuf], cwd: &Path) -> HashMap<PathBuf, SystemTime> {
    let mut map = HashMap::new();
    let header_exts = &PackageMetadata::get().extensions.c_headers;
    let mut header_dirs = Vec::new();

    for p in paths {
        if let Ok(meta) = fs::metadata(p)
            && let Ok(mtime) = meta.modified()
        {
            map.insert(p.clone(), mtime);
        }
        let ext = p
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{}", e.to_ascii_lowercase()))
            .unwrap_or_default();
        if !ext.is_empty() && CFamilyHandler::is_c_family_source_ext(&ext) {
            let dir = match p.parent() {
                Some(parent) if !parent.as_os_str().is_empty() => parent.to_path_buf(),
                _ => cwd.to_path_buf(),
            };
            if !header_dirs.contains(&dir) {
                header_dirs.push(dir);
            }
        }
    }

    for dir in header_dirs {
        if let Ok(entries) = fs::read_dir(&dir) {
            for entry in entries.flatten() {
                let ep = entry.path();
                if !ep.is_file() {
                    continue;
                }
                let ext = ep
                    .extension()
                    .and_then(|e| e.to_str())
                    .map(|e| format!(".{}", e.to_ascii_lowercase()))
                    .unwrap_or_default();
                if header_exts.iter().any(|h| h == &ext)
                    && let Ok(meta) = entry.metadata()
                    && let Ok(mtime) = meta.modified()
                {
                    map.insert(ep, mtime);
                }
            }
        }
    }

    let run_toml = cwd.join("Run.toml");
    if run_toml.is_file()
        && let Ok(meta) = fs::metadata(&run_toml)
        && let Ok(mtime) = meta.modified()
    {
        map.insert(run_toml, mtime);
    }
    map
}

/// Execute a single invocation of the CLI pipeline (config load -> target resolution -> run -> cleanup).
fn run_once(
    cli: &CliArgs,
    printer: Printer,
    cwd: &Path,
    prebuffered_stdin: Option<&Vec<u8>>,
) -> RunOutcome {
    let config = match Config::load(Some(cwd), None) {
        Ok(cfg) => cfg,
        Err(err) => {
            printer.error(&err.to_string());
            return RunOutcome {
                exit_code: 1,
                watch_files: Vec::new(),
                captured_stdin: prebuffered_stdin.cloned(),
            };
        }
    };

    if cli.clean_cache {
        match CacheManager::new(cwd) {
            Ok(cache) => {
                cache.clear();
                printer.action("CLEAN", "Build cache cleared successfully.", TagColor::Green);
            }
            Err(err) => {
                printer.error(&format!("Failed to clean cache: {err}"));
                return RunOutcome {
                    exit_code: 1,
                    watch_files: Vec::new(),
                    captured_stdin: prebuffered_stdin.cloned(),
                };
            }
        }
        if cli.files.is_empty() && cli.new_target.is_none() && cli.all.is_none() {
            return RunOutcome {
                exit_code: 0,
                watch_files: Vec::new(),
                captured_stdin: prebuffered_stdin.cloned(),
            };
        }
    }

    if cli.cache_stats || cli.cache_gc {
        match CacheManager::new(cwd) {
            Ok(cache) => {
                if cli.cache_gc {
                    let meta = &run::util::env::PackageMetadata::get().cache;
                    let max_age = config.cache.max_age_days.unwrap_or(meta.max_age_days);
                    let max_mb = config.cache.max_size_mb.unwrap_or(meta.max_size_mb);
                    let pruned = cache.prune(max_age, max_mb);
                    printer.action(
                        "CLEAN",
                        &format!(
                            "Pruned {pruned} cache entry(s) (max_age_days={max_age}, max_size_mb={max_mb})."
                        ),
                        TagColor::Green,
                    );
                }
                if cli.cache_stats {
                    let stats = cache.stats();
                    let kb = stats.total_bytes as f64 / 1024.0;
                    let age_str = stats
                        .oldest_age_secs
                        .map(|s| format!("{:.1}h", s as f64 / 3600.0))
                        .unwrap_or_else(|| "n/a".to_string());
                    printer.info(&format!(
                        "Cache bucket: {} | entries: {} | size: {:.1} KB | oldest: {age_str}",
                        stats.bucket_dir.display(),
                        stats.entry_count,
                        kb
                    ));
                }
            }
            Err(err) => {
                printer.error(&format!("Failed to inspect cache: {err}"));
                return RunOutcome {
                    exit_code: 1,
                    watch_files: Vec::new(),
                    captured_stdin: prebuffered_stdin.cloned(),
                };
            }
        }
        if cli.files.is_empty() && cli.new_target.is_none() && cli.all.is_none() {
            return RunOutcome {
                exit_code: 0,
                watch_files: Vec::new(),
                captured_stdin: prebuffered_stdin.cloned(),
            };
        }
    }

    if let Some(ref target) = cli.new_target {
        let code = match TemplateManager::generate(
            target,
            cli.template.as_deref(),
            Some(&config),
            cli.force,
            Some(cwd),
        ) {
            Ok(true) => 0,
            Ok(false) => 1,
            Err(err) => {
                printer.error(&err.to_string());
                1
            }
        };
        return RunOutcome {
            exit_code: code,
            watch_files: Vec::new(),
            captured_stdin: prebuffered_stdin.cloned(),
        };
    }

    let options = cli.to_runner_options();
    let flags_str = cli.flags_str();
    let args_str = cli.args_str();
    let mut runner = match CompilerRunner::new(
        options,
        flags_str.as_deref(),
        args_str.as_deref(),
        config.clone(),
        printer,
    ) {
        Ok(r) => r,
        Err(err) => {
            printer.error(&err.to_string());
            return RunOutcome {
                exit_code: 1,
                watch_files: Vec::new(),
                captured_stdin: prebuffered_stdin.cloned(),
            };
        }
    };

    if let Some(bytes) = prebuffered_stdin {
        runner.base.set_buffered_stdin(Some(bytes.clone()));
    }
    let captured_stdin = prebuffered_stdin
        .cloned()
        .or_else(|| runner.base.buffered_stdin().map(|b| b.to_vec()));

    apply_sandbox_overrides(&mut runner.base.sandbox_config, cli);

    // 1. Check for configured [tasks.<name>] execution when first positional is not an existing path.
    if !cli.files.is_empty()
        && !Path::new(&cli.files[0]).exists()
        && TaskRunner::is_task(&cli.files[0], &config)
    {
        let mut task_args: Vec<String> = cli.files[1..].to_vec();
        task_args.extend(runner.base.run_args.clone());

        let (started_compose, _) = match prepare_sandbox(&mut runner, cli, &[], cwd) {
            Ok(res) => res,
            Err(err) => {
                printer.error(&err.to_string());
                return RunOutcome {
                    exit_code: 1,
                    watch_files: Vec::new(),
                    captured_stdin,
                };
            }
        };

        let ok = match TaskRunner::run_task(&cli.files[0], &config, &task_args, &mut runner.base) {
            Ok(res) => res,
            Err(err) => {
                printer.error(&err.to_string());
                false
            }
        };
        cleanup_sandbox(started_compose, cli);
        runner.cleanup();
        return RunOutcome {
            exit_code: i32::from(!ok),
            watch_files: TaskRunner::collect_watch_files(&cli.files[0], &config, cwd),
            captured_stdin,
        };
    }

    // 1b. Direct PATH command via explicit `-- <cmd> [args...]` (e.g. `run -t -M -- ls -la`).
    // When a project manifest is detected in `cwd`, only intercept `-- <first>` if `<first>`
    // resolves to an existing path or `PATH` command; otherwise forward `-- args` to `ProjectRunner`.
    if cli.files.is_empty()
        && cli.all.is_none()
        && !cli.extra_run_args.is_empty()
        && (Path::new(&cli.extra_run_args[0]).exists()
            || SystemSandboxEnv.which(&cli.extra_run_args[0]).is_some()
            || ProjectRunner::detect_project(cwd, &config, None).is_none())
    {
        let mut raw_cmd = std::mem::take(&mut runner.base.run_args);
        let prog = raw_cmd.remove(0);
        let resolved = if Path::new(&prog).exists() {
            PathBuf::from(&prog)
        } else if let Some(found) = SystemSandboxEnv.which(&prog) {
            found
        } else {
            printer.error(&format!("Command not found: {prog}"));
            return RunOutcome {
                exit_code: 1,
                watch_files: Vec::new(),
                captured_stdin,
            };
        };

        let (started_compose, _) = match prepare_sandbox(&mut runner, cli, &[], cwd) {
            Ok(res) => res,
            Err(err) => {
                printer.error(&err.to_string());
                return RunOutcome {
                    exit_code: 1,
                    watch_files: Vec::new(),
                    captured_stdin,
                };
            }
        };
        let ok = if let Some(bench_runs) = cli.bench {
            BenchRunner::run_bench_command(
                &mut runner,
                &resolved,
                &raw_cmd,
                bench_runs,
                cli.warmup.unwrap_or(2),
            )
        } else {
            match runner.execute_binary(&resolved, &raw_cmd) {
                Ok(res) => res,
                Err(err) => {
                    printer.error(&err.to_string());
                    false
                }
            }
        };
        cleanup_sandbox(started_compose, cli);
        runner.cleanup();
        return RunOutcome {
            exit_code: i32::from(!ok),
            watch_files: Vec::new(),
            captured_stdin,
        };
    }

    // 1c. Implicit PATH command when first positional is not a file/task and has no extension (e.g. `run -t echo hi`).
    if !cli.files.is_empty()
        && !Path::new(&cli.files[0]).exists()
        && !cli.files[0].contains('/')
        && !cli.files[0].contains('\\')
        && Path::new(&cli.files[0]).extension().is_none()
        && let Some(resolved) = SystemSandboxEnv.which(&cli.files[0])
    {
        let cmd_args: Vec<String> = cli.files[1..].to_vec();
        let (started_compose, _) = match prepare_sandbox(&mut runner, cli, &[], cwd) {
            Ok(res) => res,
            Err(err) => {
                printer.error(&err.to_string());
                return RunOutcome {
                    exit_code: 1,
                    watch_files: Vec::new(),
                    captured_stdin,
                };
            }
        };
        let ok = if let Some(bench_runs) = cli.bench {
            BenchRunner::run_bench_command(
                &mut runner,
                &resolved,
                &cmd_args,
                bench_runs,
                cli.warmup.unwrap_or(2),
            )
        } else {
            match runner.execute_binary(&resolved, &cmd_args) {
                Ok(res) => res,
                Err(err) => {
                    printer.error(&err.to_string());
                    false
                }
            }
        };
        cleanup_sandbox(started_compose, cli);
        runner.cleanup();
        return RunOutcome {
            exit_code: i32::from(!ok),
            watch_files: Vec::new(),
            captured_stdin,
        };
    }

    // 2. No files provided: handle --test-dir error, -L/--all, ProjectRunner, or interactive prompt.
    if cli.files.is_empty() && cli.all.is_none() {
        if cli.test_dir.is_some() {
            printer.error("A source file must be specified with --test-dir");
            return RunOutcome {
                exit_code: 1,
                watch_files: Vec::new(),
                captured_stdin,
            };
        }

        if let Some(proj_info) = ProjectRunner::detect_project(cwd, &config, Some(&printer)) {
            let watch_files = ProjectRunner::get_watch_files(&proj_info.2);
            let (started_compose, _) = match prepare_sandbox(&mut runner, cli, &[], cwd) {
                Ok(res) => res,
                Err(err) => {
                    printer.error(&err.to_string());
                    return RunOutcome {
                        exit_code: 1,
                        watch_files,
                        captured_stdin,
                    };
                }
            };

            let extra_flags = runner.base.extra_flags.clone();
            let run_args = runner.base.run_args.clone();
            let ok = match ProjectRunner::run_project(
                &proj_info,
                &mut runner.base,
                &extra_flags,
                &run_args,
            ) {
                Ok(res) => res,
                Err(err) => {
                    printer.error(&err.to_string());
                    false
                }
            };
            cleanup_sandbox(started_compose, cli);
            runner.cleanup();
            return RunOutcome {
                exit_code: i32::from(!ok),
                watch_files,
                captured_stdin,
            };
        }

        if cli.json {
            printer.error("No target file specified.");
            return RunOutcome {
                exit_code: 1,
                watch_files: Vec::new(),
                captured_stdin,
            };
        }

        if let Some(prompted) = prompt_for_file(cwd, &config, &runner, printer.ui_mode()) {
            let target_files = vec![prompted];
            let (started_compose, _) = match prepare_sandbox(&mut runner, cli, &target_files, cwd)
            {
                Ok(res) => res,
                Err(err) => {
                    printer.error(&err.to_string());
                    return RunOutcome {
                        exit_code: 1,
                        watch_files: target_files,
                        captured_stdin,
                    };
                }
            };
            let ok = runner.compile_and_run(&target_files, cli.multi);
            cleanup_sandbox(started_compose, cli);
            runner.cleanup();
            return RunOutcome {
                exit_code: i32::from(!ok),
                watch_files: target_files,
                captured_stdin,
            };
        }

        return RunOutcome {
            exit_code: 0,
            watch_files: Vec::new(),
            captured_stdin,
        };
    }

    // 3. Collect explicit target files/directories, trailing positional args, or -L/--all discovery.
    let max_depth = cli
        .all
        .and_then(|d| if d < 0 { None } else { Some(d as usize) });
    let mut target_files: Vec<PathBuf> = Vec::new();
    let mut discovered_by_walk = cli.all.is_some();

    if cli.files.is_empty() && cli.all.is_some() {
        let discovered = runner.find_source_files(cwd, max_depth);
        if discovered.is_empty() {
            printer.warning("No supported source files found via -L auto-search");
            return RunOutcome {
                exit_code: 1,
                watch_files: Vec::new(),
                captured_stdin,
            };
        }
        printer.info(&format!("Auto-found {} source file(s).", discovered.len()));
        target_files = discovered;
    } else if !cli.multi
        && cli.files.len() > 1
        && Path::new(&cli.files[0]).is_file()
        && !cli.files[1..]
            .iter()
            .all(|f| is_existing_runnable_target(f, &runner))
    {
        target_files.push(PathBuf::from(&cli.files[0]));
        runner
            .base
            .run_args
            .extend(cli.files[1..].iter().cloned());
    } else {
        for raw_target in &cli.files {
            let p = PathBuf::from(raw_target);
            if p.is_dir() {
                discovered_by_walk = true;
                let discovered = runner.find_source_files(&p, max_depth);
                if discovered.is_empty() {
                    printer.warning(&format!("No source files found in {}", p.display()));
                } else {
                    target_files.extend(discovered);
                }
            } else {
                target_files.push(p);
            }
        }
    }

    if target_files.is_empty() {
        printer.warning("No runnable source files found.");
        return RunOutcome {
            exit_code: 0,
            watch_files: Vec::new(),
            captured_stdin,
        };
    }

    let (started_compose, _) = match prepare_sandbox(&mut runner, cli, &target_files, cwd) {
        Ok(res) => res,
        Err(err) => {
            printer.error(&err.to_string());
            return RunOutcome {
                exit_code: 1,
                watch_files: target_files,
                captured_stdin,
            };
        }
    };

    let effective_multi = cli.multi
        || (discovered_by_walk
            && target_files.len() > 1
            && has_multi_compilable(&target_files, &config));

    let ok = if let Some(ref interactor) = cli.interactive {
        InteractiveRunner::run_interactive(
            &mut runner,
            &target_files,
            effective_multi,
            Path::new(interactor),
        )
    } else if let Some(ref cmp_target) = cli.compare {
        let cmp_files = vec![PathBuf::from(cmp_target)];
        if let Some(ref td_str) = cli.test_dir {
            printer.info(&format!(
                "Comparing {} vs {cmp_target} on {}",
                target_files
                    .first()
                    .map(|p| p.display().to_string())
                    .unwrap_or_default(),
                td_str
            ));
            let ok_a = TestcasesRunner::run_tests(
                &mut runner,
                Path::new(td_str),
                &target_files,
                effective_multi,
            );
            let ok_b = TestcasesRunner::run_tests(
                &mut runner,
                Path::new(td_str),
                &cmp_files,
                false,
            );
            ok_a && ok_b
        } else {
            BenchRunner::run_bench_compare(
                &mut runner,
                &target_files,
                &cmp_files,
                effective_multi,
                cli.bench.unwrap_or(10),
                cli.warmup.unwrap_or(2),
            )
        }
    } else if let Some(ref stress_pair) = cli.stress {
        let stress_cfg = StressConfig {
            gen_path: PathBuf::from(&stress_pair[0]),
            brute_path: PathBuf::from(&stress_pair[1]),
            checker_path: cli.checker.as_deref().map(PathBuf::from),
            runs: cli.runs.unwrap_or(100),
            start_seed: cli.seed.unwrap_or(1),
            save_dir: cli
                .out_dir
                .as_deref()
                .map(PathBuf::from)
                .unwrap_or_else(|| cwd.to_path_buf()),
        };
        StressRunner::run_stress(&mut runner, &target_files, effective_multi, &stress_cfg)
    } else if let Some(ref td_str) = cli.test_dir {
        TestcasesRunner::run_tests(&mut runner, Path::new(td_str), &target_files, effective_multi)
    } else if let Some(bench_runs) = cli.bench {
        BenchRunner::run_bench_targets(
            &mut runner,
            &target_files,
            effective_multi,
            bench_runs,
            cli.warmup.unwrap_or(2),
        )
    } else {
        runner.compile_and_run(&target_files, effective_multi)
    };

    cleanup_sandbox(started_compose, cli);
    runner.cleanup();

    RunOutcome {
        exit_code: i32::from(!ok),
        watch_files: target_files,
        captured_stdin,
    }
}

/// Entry point handling early completion hooks, utility flags, single runs, and `--watch`.
fn real_main() -> i32 {
    // Handle dynamic shell completion callback (`--_complete <presets|tasks|templates>`).
    let raw_args: Vec<String> = std::env::args().collect();
    if raw_args.get(1).map(String::as_str) == Some("--_complete")
        && let Some(query_type) = raw_args.get(2)
    {
        let cfg = Config::load(None, None).ok();
        let out = CompletionGenerator::handle_internal_complete(query_type, cfg.as_ref());
        if !out.is_empty() {
            println!("{out}");
        }
        return 0;
    }

    let cli = CliArgs::parse_env();

    if cli.json_schema {
        println!(
            "{}",
            serde_json::to_string_pretty(&JsonReport::json_schema()).unwrap_or_default()
        );
        return 0;
    }

    if cli.mcp {
        if let Err(err) = run::mcp::run_mcp_server(cli.mcp_root.as_deref()) {
            eprintln!("MCP server error: {err}");
            return 1;
        }
        return 0;
    }

    let verbosity = Verbosity::from_cli(cli.quiet, cli.verbose);
    let ui_mode = cli.resolve_ui_mode(Colors::is_stdout_tty());
    let printer = Printer::init_global_with_ui(verbosity, cli.no_color || cli.json, ui_mode);

    let json_rx = if cli.json {
        let (tx, rx) = std::sync::mpsc::channel::<UiEvent>();
        set_tui_sink(Some(tx));
        Some(rx)
    } else {
        None
    };

    let code = execute_cli(&cli, printer, ui_mode);

    if let Some(rx) = json_rx {
        set_tui_sink(None);
        let events: Vec<UiEvent> = rx.try_iter().collect();
        let mode = if cli.stress.is_some() {
            "stress"
        } else if cli.test_dir.is_some() {
            "test"
        } else if cli.bench.is_some() {
            "bench"
        } else if cli.files.is_empty() && cli.extra_run_args.is_empty() {
            "project"
        } else {
            "run"
        };
        let targets = if !cli.files.is_empty() {
            cli.files.clone()
        } else {
            cli.extra_run_args.clone()
        };
        let report = JsonReport::from_events(mode, targets, code, events);
        println!(
            "{}",
            serde_json::to_string_pretty(&report).unwrap_or_default()
        );
    }

    code
}

/// Core CLI execution body separated so `--json` can capture and emit a single report on every exit path.
fn execute_cli(cli: &CliArgs, printer: Printer, ui_mode: UiMode) -> i32 {
    let verbosity = printer.verbosity();

    if let Some(ref shell) = cli.completion {
        if shell.eq_ignore_ascii_case("auto") {
            println!("{}", CompletionGenerator::get_install_instructions());
            return 0;
        }
        let Some(script) = CompletionGenerator::generate(shell) else {
            printer.error(&format!("Unsupported shell for completion: {shell}"));
            return 1;
        };
        println!("{script}");
        return 0;
    }

    if verbosity >= Verbosity::Verbose {
        printer.debug("Debug logging enabled");
    }

    if let Some(ref dir) = cli.directory {
        let target_dir = Path::new(dir);
        if !target_dir.is_dir() {
            printer.error(&format!("Specified directory does not exist: {dir}"));
            return 1;
        }
        if let Err(err) = std::env::set_current_dir(target_dir) {
            printer.error(&format!("Failed to change directory to '{dir}': {err}"));
            return 1;
        }
    }

    if let Err(err) = SecurityManager::check_root(cli.unsafe_mode) {
        printer.error(&err.to_string());
        return 1;
    }

    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let _ = load_dotenv(&cwd);

    if cli.doctor {
        return Doctor::diagnose(printer.colors());
    }

    if cli.init {
        return i32::from(!ConfigInitializer::init_config(&cwd, cli.force));
    }

    if cli.list {
        let config = match Config::load(Some(&cwd), None) {
            Ok(cfg) => cfg,
            Err(err) => {
                printer.error(&err.to_string());
                return 1;
            }
        };
        return TaskRunner::list_tasks(&config, &printer);
    }

    if cli.sandbox_stop {
        PersistentSandbox::stop();
        ComposeSandbox::teardown_all();
        return 0;
    }

    if ui_mode == UiMode::Tui {
        let config = match Config::load(Some(&cwd), None) {
            Ok(cfg) => cfg,
            Err(err) => {
                printer.error(&err.to_string());
                return 1;
            }
        };
        return match run::tui::run_tui(cli, config, &cwd) {
            Ok(code) => code,
            Err(err) => {
                printer.error(&err.to_string());
                1
            }
        };
    }

    install_sigint_handler();

    if !cli.watch {
        printer.banner(
            &format!("run v{}", env!("CARGO_PKG_VERSION")),
            &format!("· {}", cwd.display()),
        );
        let outcome = run_once(cli, printer, &cwd, None);
        if run::util::process::is_interrupted() {
            return 130;
        }
        return outcome.exit_code;
    }

    printer.info(
        "Watch mode enabled. Watching for changes... (Press Ctrl+C to exit, 'c' or Enter to re-run)",
    );
    let _ = io::stdout().flush();

    let initial_stdin = read_piped_stdin_once(cli);
    let mut outcome = run_once(cli, printer, &cwd, initial_stdin.as_ref());

    let mut tracked_mtimes = collect_mtimes(&outcome.watch_files, &cwd);

    while !STOP_REQUESTED.load(Ordering::Relaxed) {
        let mut trigger_rerun = false;
        let poll_window = Duration::from_millis(500);
        let start = Instant::now();

        let _raw_guard = RawModeGuard::enter_if_tty();
        while !STOP_REQUESTED.load(Ordering::Relaxed) {
            let elapsed = start.elapsed();
            if elapsed >= poll_window {
                break;
            }
            let remaining = poll_window - elapsed;
            let step = remaining.min(Duration::from_millis(100));

            if io::stdin().is_tty() && event::poll(step).unwrap_or(false) {
                if let Ok(Event::Key(key)) = event::read() {
                    if key.code == KeyCode::Char('c')
                        && key.modifiers.contains(KeyModifiers::CONTROL)
                    {
                        STOP_REQUESTED.store(true, Ordering::Relaxed);
                        break;
                    }
                    if matches!(
                        key.code,
                        KeyCode::Enter | KeyCode::Char('c') | KeyCode::Char('C')
                    ) {
                        trigger_rerun = true;
                        break;
                    }
                }
            } else {
                thread::sleep(step);
            }
        }
        drop(_raw_guard);

        if STOP_REQUESTED.load(Ordering::Relaxed) {
            break;
        }

        let new_mtimes = collect_mtimes(&outcome.watch_files, &cwd);
        if !trigger_rerun && new_mtimes != tracked_mtimes {
            trigger_rerun = true;
        }

        if trigger_rerun {
            printer.separator();
            printer.info("Change detected, Restarting...");
            let _ = io::stdout().flush();
            let cached = outcome.captured_stdin.as_ref();
            outcome = run_once(cli, printer, &cwd, cached);
            tracked_mtimes = collect_mtimes(&outcome.watch_files, &cwd);
        }
    }

    printer.info("Watch mode stopped.");
    0
}

/// CLI entrypoint delegating immediately to [`real_main`].
fn main() {
    std::process::exit(real_main());
}

#[cfg(test)]
mod tests {
    use super::*;
    use run::core::RunnerOptions;
    use run::util::output::Colors;
    use std::time::UNIX_EPOCH;

    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let dir = std::env::temp_dir().join(format!(
            "run_main_wb_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn test_apply_sandbox_overrides() {
        let mut sb = SandboxConfig::default();
        let cli = CliArgs {
            sandbox_image: Some("alpine:3.20".to_string()),
            sandbox_dockerfile: Some("Dockerfile.dev".to_string()),
            sandbox_compose: Some("compose.yml".to_string()),
            sandbox_service: Some("worker".to_string()),
            ..CliArgs::default()
        };
        apply_sandbox_overrides(&mut sb, &cli);
        assert_eq!(sb.image.as_deref(), Some("alpine:3.20"));
        assert_eq!(sb.dockerfile.as_deref(), Some("Dockerfile.dev"));
        assert_eq!(sb.compose.as_deref(), Some("compose.yml"));
        assert_eq!(sb.compose_service.as_deref(), Some("worker"));
    }

    #[test]
    fn test_collect_mtimes_tracks_c_headers_and_run_toml_only() {
        let dir = make_temp_dir("mtimes");
        let main_c = dir.join("main.c");
        let hdr_h = dir.join("util.h");
        let notes_txt = dir.join("notes.txt");
        let run_toml = dir.join("Run.toml");
        fs::write(&main_c, "int main(void) { return 0; }\n").unwrap();
        fs::write(&hdr_h, "#define X 1\n").unwrap();
        fs::write(&notes_txt, "ignore\n").unwrap();
        fs::write(&run_toml, "[core]\n").unwrap();

        let tracked = collect_mtimes(std::slice::from_ref(&main_c), &dir);
        assert!(tracked.contains_key(&main_c));
        assert!(tracked.contains_key(&hdr_h));
        assert!(tracked.contains_key(&run_toml));
        assert!(!tracked.contains_key(&notes_txt));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_is_existing_runnable_target_and_has_multi_compilable() {
        let dir = make_temp_dir("targets");
        let sub = dir.join("sub");
        fs::create_dir_all(&sub).unwrap();
        let src_c = dir.join("a.c");
        let src_py = dir.join("b.py");
        let shebang_tool = dir.join("mytool");
        let plain_txt = dir.join("data.txt");
        fs::write(&src_c, "int main(void){return 0;}\n").unwrap();
        fs::write(&src_py, "print(1)\n").unwrap();
        fs::write(&shebang_tool, "#!/usr/bin/env run\nint main(void){return 0;}\n").unwrap();
        fs::write(&plain_txt, "not runnable\n").unwrap();

        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let runner = CompilerRunner::with_cache_base(
            RunnerOptions::default(),
            None,
            None,
            Config::default(),
            printer,
            &dir,
            &dir.join("cache"),
        )
        .expect("runner");

        assert!(is_existing_runnable_target(&sub.to_string_lossy(), &runner));
        assert!(is_existing_runnable_target(&src_c.to_string_lossy(), &runner));
        assert!(is_existing_runnable_target(&src_py.to_string_lossy(), &runner));
        assert!(is_existing_runnable_target(
            &shebang_tool.to_string_lossy(),
            &runner
        ));
        assert!(!is_existing_runnable_target(
            &plain_txt.to_string_lossy(),
            &runner
        ));
        assert!(!is_existing_runnable_target("nonexistent_arg", &runner));

        let cfg = Config::default();
        assert!(has_multi_compilable(&[src_c.clone(), src_py.clone()], &cfg));
        assert!(!has_multi_compilable(std::slice::from_ref(&src_py), &cfg));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_collect_fzf_candidates_filters_runnable_files() {
        let dir = make_temp_dir("fzf");
        let sub = dir.join("src");
        let ignored = dir.join("node_modules");
        fs::create_dir_all(&sub).unwrap();
        fs::create_dir_all(&ignored).unwrap();
        fs::write(dir.join("main.c"), "int main(void){return 0;}\n").unwrap();
        fs::write(sub.join("helper.py"), "print(1)\n").unwrap();
        fs::write(dir.join("README.md"), "# doc\n").unwrap();
        fs::write(ignored.join("dep.py"), "print(2)\n").unwrap();

        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let cfg = Config::default();
        let runner = CompilerRunner::with_cache_base(
            RunnerOptions::default(),
            None,
            None,
            cfg.clone(),
            printer,
            &dir,
            &dir.join("cache"),
        )
        .expect("runner");

        let candidates = collect_fzf_candidates(&dir, &cfg, &runner);
        assert!(candidates.contains(&"main.c".to_string()));
        assert!(candidates.contains(&format!("src{}helper.py", std::path::MAIN_SEPARATOR)));
        assert!(!candidates.iter().any(|c| c.contains("README.md")));
        assert!(!candidates.iter().any(|c| c.contains("node_modules")));

        let _ = fs::remove_dir_all(&dir);
    }
}
