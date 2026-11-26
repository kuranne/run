//! Generic project manifest detection and custom `[tasks]` execution.
//!
//! Adapted from Python's `src/runner/project_runner.py`. Uses POSIX-aware shell
//! tokenization via `shlex::split`, template expansion via
//! [`crate::util::substitutions::VariableSubstitutor`], and glob manifest
//! matching via [`crate::util::glob::find_matching_manifests`].

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::core::base::BaseRunner;
use crate::model::error::RunError;
use crate::util::cache::CacheManager;
use crate::util::config::{Config, ProjectConfig, TaskConfig, TaskTable};
use crate::util::glob::{compile_glob, find_matching_manifests, has_glob_wildcard};
use crate::util::output::{Printer, TagColor, UiEvent, emit_tui_event};
use crate::util::substitutions::{VariableSubstitutor, shell_quote};

/// Split a compound shell command string on unquoted `&&` or `;` into individual
/// argument vectors, preserving single quotes, double quotes, and backslash escapes.
///
/// Each resulting command clause is tokenized with `shlex::split`. Returns
/// [`RunError::Config`] if any clause has unclosed quotes or invalid escaping.
pub fn split_compound_command(cmd_str: &str) -> Result<Vec<Vec<String>>, RunError> {
    if cmd_str.trim().is_empty() {
        return Ok(Vec::new());
    }

    let mut clauses = Vec::new();
    let mut current = String::new();
    let mut chars = cmd_str.chars().peekable();
    let mut in_single = false;
    let mut in_double = false;
    let mut escaped = false;

    while let Some(ch) = chars.next() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }

        if in_single {
            current.push(ch);
            if ch == '\'' {
                in_single = false;
            }
            continue;
        }

        if ch == '\\' {
            current.push(ch);
            escaped = true;
            continue;
        }

        if ch == '"' {
            in_double = !in_double;
            current.push(ch);
            continue;
        }

        if !in_double && ch == '\'' {
            in_single = true;
            current.push(ch);
            continue;
        }

        if !in_double && ch == ';' {
            clauses.push(std::mem::take(&mut current));
            continue;
        }

        if !in_double && ch == '&' && chars.peek() == Some(&'&') {
            chars.next();
            clauses.push(std::mem::take(&mut current));
            continue;
        }

        current.push(ch);
    }

    if in_single || in_double {
        return Err(RunError::config(format!(
            "Invalid quoting in compound command: {cmd_str}"
        )));
    }
    if !current.trim().is_empty() {
        clauses.push(current);
    }

    let mut subcommands = Vec::new();
    for clause in clauses {
        if clause.trim().is_empty() {
            continue;
        }
        let tokens = shlex::split(&clause).ok_or_else(|| {
            RunError::config(format!("Invalid quoting in command clause: {clause}"))
        })?;
        if !tokens.is_empty() {
            subcommands.push(tokens);
        }
    }

    Ok(subcommands)
}

/// Split `step_str` on `&&`/`;`, append `tail_args` onto the final subcommand,
/// and execute each sequentially via [`BaseRunner::run_command`].
fn run_step(
    step_str: &str,
    tail_args: &[String],
    runner: &mut BaseRunner,
    compiling: bool,
) -> Result<bool, RunError> {
    let subcmds = split_compound_command(step_str)?;
    if subcmds.is_empty() {
        return Ok(true);
    }
    let last_idx = subcmds.len() - 1;
    for (idx, mut cmd) in subcmds.into_iter().enumerate() {
        if idx == last_idx && !tail_args.is_empty() {
            cmd.extend(tail_args.iter().cloned());
        }
        if cmd.is_empty() {
            continue;
        }
        if !runner.run_command(&cmd, false, compiling)? {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Executes custom tasks defined under `[tasks]` in `Run.toml`.
#[derive(Debug, Clone, Copy, Default)]
pub struct TaskRunner;

impl TaskRunner {
    /// Return `true` if `name` matches a defined task in `config`.
    pub fn is_task(name: &str, config: &Config) -> bool {
        config.get_tasks().contains_key(name)
    }

    /// Print all `[tasks]` defined in `config` with optional parameters and descriptions (`-l` / `--list`).
    pub fn list_tasks(config: &Config, printer: &Printer) -> i32 {
        let tasks = config.get_tasks();
        if tasks.is_empty() {
            printer.info("No tasks defined in Run.toml.");
            return 0;
        }
        for (name, task_cfg) in tasks {
            let line = match task_cfg {
                TaskConfig::Simple(cmd) => format!("{name} -> {cmd}"),
                TaskConfig::Detailed(table) => {
                    let params = if table.params.is_empty() {
                        String::new()
                    } else {
                        format!(
                            " {}",
                            table
                                .params
                                .iter()
                                .map(|p| format!("<{p}>"))
                                .collect::<Vec<_>>()
                                .join(" ")
                        )
                    };
                    match table.desc.as_deref() {
                        Some(d) if !d.is_empty() => format!("{name}{params} — {d}"),
                        _ => format!("{name}{params}"),
                    }
                }
            };
            if printer.has_tui_sink() {
                emit_tui_event(UiEvent::OutputLine(line));
            } else {
                println!("{line}");
            }
        }
        0
    }

    /// Substitute template variables, resolve DAG `deps` (running independent deps in parallel),
    /// check OS `platforms`/overrides and SHA-256 `sources`/`generates`, map `params` / `-- <argv>`,
    /// and run the named `[tasks.<name>]` command chain.
    pub fn run_task(
        name: &str,
        config: &Config,
        extra_args: &[String],
        runner: &mut BaseRunner,
    ) -> Result<bool, RunError> {
        let mut visiting = HashSet::new();
        let mut completed = HashSet::new();
        Self::run_task_dag(name, config, extra_args, runner, &mut visiting, &mut completed)
    }

    /// Collect source files matching `sources` globs of task `name` (and its `deps`) plus `Run.toml`
    /// for `run --watch <task>`.
    pub fn collect_watch_files(name: &str, config: &Config, cwd: &Path) -> Vec<PathBuf> {
        let mut out = Vec::new();
        let mut seen_tasks = HashSet::new();
        let mut stack = vec![name.to_string()];
        while let Some(task_name) = stack.pop() {
            if !seen_tasks.insert(task_name.clone()) {
                continue;
            }
            if let Some(TaskConfig::Detailed(table)) = config.get_tasks().get(&task_name) {
                for dep in &table.deps {
                    stack.push(dep.clone());
                }
                for pat in &table.sources {
                    out.extend(collect_glob_matches(cwd, pat));
                }
            }
        }
        if let Some(ref cfg_path) = config.path
            && cfg_path.exists()
        {
            out.push(cfg_path.clone());
        }
        out.sort();
        out.dedup();
        out
    }

    fn run_task_dag(
        name: &str,
        config: &Config,
        extra_args: &[String],
        runner: &mut BaseRunner,
        visiting: &mut HashSet<String>,
        completed: &mut HashSet<String>,
    ) -> Result<bool, RunError> {
        if completed.contains(name) {
            return Ok(true);
        }
        if !visiting.insert(name.to_string()) {
            return Err(RunError::config(format!(
                "Cyclic task dependency detected involving '{name}'."
            )));
        }

        let res = Self::run_task_inner(name, config, extra_args, runner, visiting, completed);
        visiting.remove(name);
        if matches!(res, Ok(true)) {
            completed.insert(name.to_string());
        }
        res
    }

    fn run_task_inner(
        name: &str,
        config: &Config,
        extra_args: &[String],
        runner: &mut BaseRunner,
        visiting: &mut HashSet<String>,
        completed: &mut HashSet<String>,
    ) -> Result<bool, RunError> {
        let task_info = config
            .get_tasks()
            .get(name)
            .ok_or_else(|| RunError::config(format!("Task '{name}' not found in Run.toml.")))?;

        let out_dir = runner.options.out_dir.as_deref();
        let mut var_ctx = VariableSubstitutor::build_file_context(None, None, out_dir);
        for (k, v) in config.get_vars() {
            var_ctx.insert(format!("vars.{k}"), v.clone());
        }

        match task_info {
            TaskConfig::Simple(cmd) => {
                if cmd.trim().is_empty() {
                    return Err(RunError::config(format!(
                        "Task '{name}' must define a non-empty command string."
                    )));
                }
                let append_extra = populate_arg_vars(&mut var_ctx, &[], extra_args, &[cmd.as_str()], name)?;
                let task_cmd = VariableSubstitutor::substitute_string(cmd, &var_ctx, true);
                runner
                    .printer
                    .action("TASK", &format!("{name}: {task_cmd}"), TagColor::Cyan);
                let step_args: &[String] = if append_extra { extra_args } else { &[] };
                run_step(&task_cmd, step_args, runner, false)
            }
            TaskConfig::Detailed(raw_table) => {
                let table = &raw_table.resolve_for_current_os();
                if !table.matches_current_platform() {
                    if !table.silent {
                        runner.printer.status(
                            "SKIP",
                            &format!("Task '{name}' skipped on current platform."),
                            TagColor::Yellow,
                        );
                    }
                    return Ok(true);
                }

                // Collect uncompleted direct deps; run independent leaf/disjoint deps concurrently
                // when there are 2+ deps and not in dry-run mode.
                let pending_deps: Vec<&String> = table
                    .deps
                    .iter()
                    .filter(|d| !completed.contains(d.as_str()))
                    .collect();

                let can_parallelize = pending_deps.len() > 1
                    && !runner.options.dry_run
                    && pending_deps.iter().all(|d| match config.get_tasks().get(d.as_str()) {
                        Some(TaskConfig::Simple(_)) => true,
                        Some(TaskConfig::Detailed(t)) => t.deps.is_empty(),
                        None => false,
                    });

                if can_parallelize {
                    // Check for cycles with visiting set first.
                    for dep in &pending_deps {
                        if visiting.contains(dep.as_str()) {
                            return Err(RunError::config(format!(
                                "Cyclic task dependency detected involving '{dep}'."
                            )));
                        }
                    }
                    let results: Vec<(Result<bool, RunError>, String)> = std::thread::scope(|scope| {
                        let mut handles = Vec::with_capacity(pending_deps.len());
                        for dep in &pending_deps {
                            let dep_name = (*dep).clone();
                            let mut sub_runner = match runner.clone_for_parallel_task(config) {
                                Ok(r) => r,
                                Err(err) => {
                                    handles.push(scope.spawn(move || (Err(err), String::new())));
                                    continue;
                                }
                            };
                            sub_runner.capture_silent = true;
                            handles.push(scope.spawn(move || {
                                let mut sub_visiting = HashSet::new();
                                let mut sub_completed = HashSet::new();
                                let res = Self::run_task_dag(
                                    &dep_name,
                                    config,
                                    &[],
                                    &mut sub_runner,
                                    &mut sub_visiting,
                                    &mut sub_completed,
                                );
                                (res, std::mem::take(&mut sub_runner.last_captured_stdout))
                            }));
                        }
                        handles
                            .into_iter()
                            .map(|h| {
                                h.join().unwrap_or_else(|_| {
                                    (
                                        Err(RunError::execution("Parallel task worker panicked")),
                                        String::new(),
                                    )
                                })
                            })
                            .collect()
                    });
                    for (dep, (res, stdout)) in pending_deps.iter().zip(results) {
                        if !stdout.is_empty() {
                            print!("{stdout}");
                        }
                        if !res? {
                            return Ok(false);
                        }
                        completed.insert((*dep).clone());
                    }
                } else {
                    for dep in &table.deps {
                        if !Self::run_task_dag(dep, config, &[], runner, visiting, completed)? {
                            return Ok(false);
                        }
                    }
                }

                for (k, v) in &table.vars {
                    var_ctx.insert(format!("vars.{k}"), v.clone());
                }

                let raw_cmds = table.commands();
                if raw_cmds.is_empty() && table.deps.is_empty() {
                    return Err(RunError::config(format!(
                        "Task '{name}' must define 'cmd', 'cmds', 'command', or 'deps'."
                    )));
                }
                if raw_cmds.is_empty() {
                    return Ok(true);
                }

                let append_extra = populate_arg_vars(&mut var_ctx, &table.params, extra_args, &raw_cmds, name)?;

                let resolved_cmds: Vec<String> = raw_cmds
                    .iter()
                    .map(|c| VariableSubstitutor::substitute_string(c, &var_ctx, true))
                    .collect();

                let orig_cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
                let task_cwd = if let Some(dir_tmpl) = table.dir.as_deref().or(table.cwd.as_deref()) {
                    let expanded = VariableSubstitutor::substitute_string(dir_tmpl, &var_ctx, false);
                    let p = PathBuf::from(&expanded);
                    let abs = if p.is_absolute() { p } else { orig_cwd.join(p) };
                    if !abs.is_dir() {
                        return Err(RunError::config(format!(
                            "Task '{name}' directory does not exist: {}",
                            abs.display()
                        )));
                    }
                    abs
                } else {
                    orig_cwd.clone()
                };

                let fingerprint_info = if !table.sources.is_empty() && !runner.options.force {
                    let fp = compute_task_fingerprint(name, table, &var_ctx, &resolved_cmds, &task_cwd)?;
                    if fp.up_to_date {
                        if !table.silent {
                            runner.printer.info(&format!("Task '{name}' is up to date."));
                        }
                        return Ok(true);
                    }
                    Some(fp)
                } else {
                    None
                };

                let prev_env = runner.options.env.clone();

                if table.sandbox {
                    runner.options.sandbox = true;
                }
                if table.sandbox_net {
                    runner.options.sandbox_net = true;
                }
                if table.restrict {
                    runner.options.restrict = true;
                }
                for (ek, ev) in &table.env {
                    let expanded_ev = VariableSubstitutor::substitute_string(ev, &var_ctx, false);
                    runner.options.env.push(format!("{ek}={expanded_ev}"));
                }

                let switched_cwd = task_cwd != orig_cwd;
                if switched_cwd {
                    std::env::set_current_dir(&task_cwd)?;
                }

                let mut all_ok = true;
                let total_cmds = resolved_cmds.len();
                for (idx, cmd_str) in resolved_cmds.iter().enumerate() {
                    if !table.silent {
                        runner
                            .printer
                            .action("TASK", &format!("{name}: {cmd_str}"), TagColor::Cyan);
                    }
                    let step_args: &[String] =
                        if append_extra && idx + 1 == total_cmds {
                            extra_args
                        } else {
                            &[]
                        };
                    match run_step(cmd_str, step_args, runner, false) {
                        Ok(true) => {}
                        Ok(false) => {
                            if table.ignore_error {
                                runner.printer.warning(&format!(
                                    "Task '{name}' command failed (ignored via ignore_error)."
                                ));
                            } else {
                                all_ok = false;
                                break;
                            }
                        }
                        Err(err) => {
                            if table.ignore_error {
                                runner.printer.warning(&format!(
                                    "Task '{name}' command failed ({err}) (ignored via ignore_error)."
                                ));
                            } else {
                                if switched_cwd {
                                    let _ = std::env::set_current_dir(&orig_cwd);
                                }
                                runner.options.env = prev_env;
                                return Err(err);
                            }
                        }
                    }
                }

                if switched_cwd {
                    let _ = std::env::set_current_dir(&orig_cwd);
                }
                runner.options.env = prev_env;

                if all_ok && let Some(fp) = fingerprint_info {
                    let _ = fp.write_digest();
                }

                Ok(all_ok)
            }
        }
    }
}

/// Populate `${args}`, `${1}`..`${N}`, `${args.<i>}`, and declared `params` into `var_ctx`.
///
/// Returns `true` when `extra_args` were not explicitly referenced by any command template
/// and should therefore be appended to the final command for backward compatibility.
fn populate_arg_vars(
    var_ctx: &mut HashMap<String, String>,
    params: &[String],
    extra_args: &[String],
    raw_cmds: &[&str],
    task_name: &str,
) -> Result<bool, RunError> {
    let joined_args = extra_args
        .iter()
        .map(|a| shell_quote(a))
        .collect::<Vec<_>>()
        .join(" ");
    var_ctx.insert("args".to_string(), joined_args);

    for (idx, val) in extra_args.iter().enumerate() {
        var_ctx.insert((idx + 1).to_string(), val.clone());
        var_ctx.insert(format!("args.{idx}"), val.clone());
    }

    for (idx, param_name) in params.iter().enumerate() {
        let Some(val) = extra_args.get(idx) else {
            return Err(RunError::config(format!(
                "Task '{task_name}' missing required parameter '{param_name}' (position {}).",
                idx + 1
            )));
        };
        var_ctx.insert(param_name.clone(), val.clone());
        var_ctx.insert(format!("args.{param_name}"), val.clone());
        var_ctx.insert(format!("vars.{param_name}"), val.clone());
    }

    if extra_args.is_empty() {
        return Ok(false);
    }

    let references_args = raw_cmds.iter().any(|cmd| {
        if cmd.contains("{args}") || cmd.contains("{args.") {
            return true;
        }
        if (1..=extra_args.len()).any(|i| cmd.contains(&format!("{{{i}}}"))) {
            return true;
        }
        params
            .iter()
            .any(|p| cmd.contains(&format!("{{{p}}}")) || cmd.contains(&format!("{{vars.{p}}}")))
    });

    Ok(!references_args)
}

/// SHA-256 task fingerprint state for `sources` / `generates` incremental skipping.
struct TaskFingerprint {
    stamp_file: PathBuf,
    digest: String,
    up_to_date: bool,
    cwd: PathBuf,
}

impl TaskFingerprint {
    fn write_digest(&self) -> Result<(), RunError> {
        if let Ok(cache) = CacheManager::new(&self.cwd) {
            return cache.with_file_lock(|| {
                crate::util::cache::atomic_write_bytes(&self.stamp_file, self.digest.as_bytes())
            });
        }
        crate::util::cache::atomic_write_bytes(&self.stamp_file, self.digest.as_bytes())
    }
}

fn compute_task_fingerprint(
    name: &str,
    table: &TaskTable,
    var_ctx: &HashMap<String, String>,
    resolved_cmds: &[String],
    cwd: &Path,
) -> Result<TaskFingerprint, RunError> {
    let mut generates_exist = true;
    for gen_pat in &table.generates {
        let expanded = VariableSubstitutor::substitute_string(gen_pat, var_ctx, false);
        let matched = collect_glob_matches(cwd, &expanded);
        if matched.is_empty() {
            generates_exist = false;
            break;
        }
    }

    let mut source_files = Vec::new();
    for src_pat in &table.sources {
        let expanded = VariableSubstitutor::substitute_string(src_pat, var_ctx, false);
        source_files.extend(collect_glob_matches(cwd, &expanded));
    }
    source_files.sort();
    source_files.dedup();

    let mut hasher = Sha256::new();
    for cmd in resolved_cmds {
        hasher.update(cmd.as_bytes());
        hasher.update(b"\0");
    }
    for path in &source_files {
        let rel = path.strip_prefix(cwd).unwrap_or(path);
        hasher.update(rel.to_string_lossy().as_bytes());
        hasher.update(b"\0");
        if let Ok(bytes) = fs::read(path) {
            hasher.update((bytes.len() as u64).to_le_bytes());
            hasher.update(&bytes);
        }
    }
    let digest = format!("{:x}", hasher.finalize());

    let stamp_dir = CacheManager::new(cwd)
        .map(|c| c.cache_dir.join("tasks"))
        .unwrap_or_else(|_| cwd.join(".run_cache").join("tasks"));
    let safe_name: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect();
    let stamp_file = stamp_dir.join(format!("{safe_name}.sha256"));

    let up_to_date = generates_exist
        && !source_files.is_empty()
        && fs::read_to_string(&stamp_file)
            .is_ok_and(|prev| prev.trim() == digest);

    Ok(TaskFingerprint {
        stamp_file,
        digest,
        up_to_date,
        cwd: cwd.to_path_buf(),
    })
}

fn collect_glob_matches(root: &Path, pattern: &str) -> Vec<PathBuf> {
    if !has_glob_wildcard(pattern) {
        let p = Path::new(pattern);
        let candidate = if p.is_absolute() { p.to_path_buf() } else { root.join(p) };
        return if candidate.exists() { vec![candidate] } else { Vec::new() };
    }

    let Ok(re) = compile_glob(pattern, !cfg!(windows)) else {
        return Vec::new();
    };
    let mut matches = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            if path.is_dir() {
                if entry.file_name() != ".git" && entry.file_name() != "target" {
                    stack.push(path);
                }
            } else if re.is_match(&rel) {
                matches.push(path);
            }
        }
    }
    matches
}

/// Generic manifest-based project detector and runner driven by `[projects]` in `Run.toml`.
#[derive(Debug, Clone, Copy, Default)]
pub struct ProjectRunner;

impl ProjectRunner {
    /// Scan `start_dir` and up to 3 parent directories for known project manifests,
    /// stopping cleanly at `.git`, filesystem mount boundaries, or system temp roots.
    pub fn detect_project(
        start_dir: &Path,
        config: &Config,
        printer: Option<&Printer>,
    ) -> Option<(String, ProjectConfig, PathBuf)> {
        let projects = config.get_projects();
        let mut current = std::path::absolute(start_dir).unwrap_or_else(|_| start_dir.to_path_buf());

        for _ in 0..4 {
            for (proj_name, proj_cfg) in &projects {
                let Some(manifest_file) = proj_cfg.file.as_deref() else {
                    continue;
                };
                if has_glob_wildcard(manifest_file) {
                    let matches = find_matching_manifests(&current, manifest_file, true);
                    if let Some(first) = matches.first() {
                        if matches.len() > 1
                            && let Some(p) = printer
                        {
                            let names = matches
                                .iter()
                                .filter_map(|m| m.file_name())
                                .map(|n| format!("'{}'", n.to_string_lossy()))
                                .collect::<Vec<_>>()
                                .join(", ");
                            let chosen = first
                                .file_name()
                                .map(|n| n.to_string_lossy().into_owned())
                                .unwrap_or_default();
                            p.info(&format!(
                                "Multiple manifest files matched '{manifest_file}' for project '{proj_name}': [{names}]. Using '{chosen}'."
                            ));
                        }
                        return Some((proj_name.clone(), proj_cfg.clone(), first.clone()));
                    }
                } else {
                    let manifest_path = current.join(manifest_file);
                    if manifest_path.exists() {
                        return Some((proj_name.clone(), proj_cfg.clone(), manifest_path));
                    }
                }
            }

            if current.join(".git").exists() {
                break;
            }
            let Some(parent) = current.parent() else {
                break;
            };
            if parent == current {
                break;
            }

            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                let Ok(cur_meta) = fs::metadata(&current) else {
                    break;
                };
                let Ok(par_meta) = fs::metadata(parent) else {
                    break;
                };
                if cur_meta.dev() != par_meta.dev() {
                    break;
                }
            }

            let cur_str = current.to_string_lossy();
            if matches!(cur_str.as_ref(), "/tmp" | "/var/tmp" | "/private/tmp") {
                break;
            }

            current = parent.to_path_buf();
        }

        None
    }

    /// Execute a detected project's two-step (`build` + `run`) or single-step (`command`) workflow.
    pub fn run_project(
        project_info: &(String, ProjectConfig, PathBuf),
        runner: &mut BaseRunner,
        extra_flags: &[String],
        run_args: &[String],
    ) -> Result<bool, RunError> {
        let (proj_name, proj_cfg, manifest_path) = project_info;
        let manifest_name = manifest_path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| manifest_path.display().to_string());

        runner
            .printer
            .info(&format!("Detected project '{proj_name}' via {manifest_name}"));

        let out_dir = runner.options.out_dir.as_deref();
        let var_ctx =
            VariableSubstitutor::build_file_context(Some(manifest_path), None, out_dir);

        let build_step = proj_cfg
            .build
            .as_deref()
            .map(|s| VariableSubstitutor::substitute_string(s, &var_ctx, true));
        let run_step_cmd = proj_cfg
            .run
            .as_deref()
            .map(|s| VariableSubstitutor::substitute_string(s, &var_ctx, true));
        let command_step = proj_cfg
            .command
            .as_deref()
            .map(|s| VariableSubstitutor::substitute_string(s, &var_ctx, true));

        match (build_step, run_step_cmd, command_step) {
            (Some(build_cmd), Some(run_cmd), _) if !build_cmd.is_empty() && !run_cmd.is_empty() => {
                if !run_step(&build_cmd, extra_flags, runner, true)? {
                    return Ok(false);
                }
                run_step(&run_cmd, run_args, runner, false)
            }
            (_, _, Some(cmd)) if !cmd.is_empty() => {
                let mut combined = Vec::with_capacity(extra_flags.len() + run_args.len());
                combined.extend(extra_flags.iter().cloned());
                combined.extend(run_args.iter().cloned());
                run_step(&cmd, &combined, runner, false)
            }
            _ => Err(RunError::config(format!(
                "Project '{proj_name}' must define 'command' or both 'build' and 'run'."
            ))),
        }
    }

    /// Collect the manifest file and any regular files under its sibling `src/` directory
    /// for watch-mode mtime tracking.
    pub fn get_watch_files(manifest_path: &Path) -> Vec<PathBuf> {
        let mut watch_list = vec![manifest_path.to_path_buf()];
        if let Some(proj_dir) = manifest_path.parent() {
            let src_dir = proj_dir.join("src");
            if src_dir.is_dir() {
                collect_files_recursive(&src_dir, &mut watch_list);
            }
        }
        watch_list
    }
}

/// Recursively append regular file paths beneath `dir` into `out`.
fn collect_files_recursive(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    let mut paths: Vec<PathBuf> = entries.filter_map(Result::ok).map(|e| e.path()).collect();
    paths.sort();
    for path in paths {
        if path.is_dir() {
            collect_files_recursive(&path, out);
        } else if path.is_file() {
            out.push(path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::registry::RunnerOptions;
    use crate::core::test_support::TempDir;
    use crate::util::output::{Colors, Verbosity};

    fn make_base(opts: RunnerOptions) -> BaseRunner {
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        BaseRunner::new(opts, None, None, &Config::default(), printer).expect("create BaseRunner")
    }

    #[test]
    fn test_split_compound_command_quotes_and_separators() {
        let cmds = split_compound_command(r#"echo "a && b"; echo 'c ; d' && true"#)
            .expect("split compound");
        assert_eq!(
            cmds,
            vec![
                vec!["echo".to_string(), "a && b".to_string()],
                vec!["echo".to_string(), "c ; d".to_string()],
                vec!["true".to_string()],
            ]
        );
        assert!(split_compound_command("   ").expect("empty").is_empty());
        assert!(split_compound_command("echo \"unclosed").is_err());
    }

    #[test]
    fn test_task_runner_is_task_and_dry_run_detailed() {
        let cfg = Config::from_toml_str(
            r#"
            [tasks.fmt]
            command = "echo formatting && echo done"
            sandbox = true
            restrict = true
            "#,
            None,
            None,
        )
        .expect("parse task config");

        assert!(TaskRunner::is_task("fmt", &cfg));
        assert!(!TaskRunner::is_task("missing", &cfg));

        let mut base = make_base(RunnerOptions {
            dry_run: true,
            ..RunnerOptions::default()
        });
        let ok = TaskRunner::run_task("fmt", &cfg, &["--check".to_string()], &mut base)
            .expect("run task");
        assert!(ok);
        assert!(base.options.sandbox);
        assert!(base.options.restrict);
    }

    #[test]
    fn test_project_runner_detect_and_watch_files() {
        let tmp = TempDir::new().expect("tempdir");
        let sub = tmp.path().join("a").join("b");
        fs::create_dir_all(&sub).unwrap();
        let src_dir = tmp.path().join("a").join("src");
        fs::create_dir_all(&src_dir).unwrap();
        let manifest = tmp.path().join("a").join("Cargo.toml");
        fs::write(&manifest, "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n").unwrap();
        let lib_rs = src_dir.join("lib.rs");
        fs::write(&lib_rs, "").unwrap();
        // Put .git in tmp root so walk finds Cargo.toml first
        fs::create_dir_all(tmp.path().join(".git")).unwrap();

        let cfg = Config::default();
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let detected = ProjectRunner::detect_project(&sub, &cfg, Some(&printer));
        let (name, _pcfg, path) = detected.expect("detect cargo project");
        assert_eq!(name, "cargo");
        assert_eq!(path, manifest);

        let watch = ProjectRunner::get_watch_files(&path);
        assert_eq!(watch.len(), 2);
        assert!(watch.contains(&manifest));
        assert!(watch.contains(&lib_rs));
    }

    #[test]
    fn test_project_runner_two_step_and_single_step_dry_run() {
        let tmp = TempDir::new().expect("tempdir");
        let manifest = tmp.path().join("Makefile");
        fs::write(&manifest, "all:\n\techo ok\n").unwrap();

        let cfg = Config::from_toml_str(
            r#"
            [projects.custom]
            file = "Makefile"
            build = "make {filename}"
            run = "./app"
            "#,
            Some(tmp.path().join("Run.toml")),
            Some(tmp.path()),
        )
        .expect("parse project config");

        let detected = ProjectRunner::detect_project(tmp.path(), &cfg, None).expect("detected");
        let mut base = make_base(RunnerOptions {
            dry_run: true,
            ..RunnerOptions::default()
        });
        let ok = ProjectRunner::run_project(
            &detected,
            &mut base,
            &["-j2".to_string()],
            &["--arg".to_string()],
        )
        .expect("run project");
        assert!(ok);
    }

    /// White-box test: `split_compound_command` handles escaped operators, empty clauses,
    /// and unclosed single quotes.
    #[test]
    fn test_split_compound_command_edge_cases() {
        let escaped = split_compound_command(r"echo a\&\&b \; c && echo ok").expect("escaped");
        assert_eq!(
            escaped,
            vec![
                vec!["echo", "a&&b", ";", "c"],
                vec!["echo", "ok"],
            ]
        );
        let empty_clauses = split_compound_command("echo 1 && ; echo 2 ; ").expect("empty");
        assert_eq!(empty_clauses, vec![vec!["echo", "1"], vec!["echo", "2"]]);
        assert!(split_compound_command("echo 'unclosed").is_err());
    }

    /// White-box test: `TaskRunner::run_task` short-circuits `&&` via `MockCommandExecutor`
    /// so the second command is never executed when the first fails.
    #[test]
    fn test_task_runner_short_circuits_and_via_mock() {
        use std::sync::Arc;
        use crate::model::options::MockCommandExecutor;
        use crate::core::base::ExecOutcome;

        let cfg = Config::from_toml_str(
            r#"
            [tasks]
            fail_chain = "cmd_one && cmd_two"
            "#,
            None,
            None,
        )
        .expect("cfg");
        let mut base = make_base(RunnerOptions::default());

        let mut mock = MockCommandExecutor::new();
        mock.expect_execute()
            .times(1)
            .withf(|spec| spec.cmd == vec!["cmd_one"])
            .returning(|_| Err(RunError::execution("exit 1")));
        base.set_executor(Arc::new(mock));

        let err = TaskRunner::run_task("fail_chain", &cfg, &[], &mut base).unwrap_err();
        assert!(err.to_string().contains("exit 1"));

        // Verify extra_args are appended ONLY to the final subcommand.
        let cfg2 = Config::from_toml_str(
            r#"
            [tasks]
            two = "step1 && step2"
            "#,
            None,
            None,
        )
        .expect("cfg2");
        let mut seq = mockall::Sequence::new();
        let mut mock2 = MockCommandExecutor::new();
        mock2
            .expect_execute()
            .times(1)
            .in_sequence(&mut seq)
            .withf(|spec| spec.cmd == vec!["step1"])
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.01,
                    memory_bytes: None,
                })
            });
        mock2
            .expect_execute()
            .times(1)
            .in_sequence(&mut seq)
            .withf(|spec| spec.cmd == vec!["step2", "--tail"])
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.01,
                    memory_bytes: None,
                })
            });
        base.set_executor(Arc::new(mock2));
        assert!(
            TaskRunner::run_task("two", &cfg2, &["--tail".to_string()], &mut base).unwrap()
        );
    }
}
