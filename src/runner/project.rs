//! Generic project manifest detection and custom `[tasks]` execution.
//!
//! Adapted from Python's `src/runner/project_runner.py`. Uses POSIX-aware shell
//! tokenization via `shlex::split`, template expansion via
//! [`crate::util::substitutions::VariableSubstitutor`], and glob manifest
//! matching via [`crate::util::glob::find_matching_manifests`].

use std::fs;
use std::path::{Path, PathBuf};

use crate::runner::base::BaseRunner;
use crate::util::config::{Config, ProjectConfig, TaskConfig};
use crate::util::errors::RunError;
use crate::util::glob::{find_matching_manifests, has_glob_wildcard};
use crate::util::output::{Printer, TagColor};
use crate::util::substitutions::VariableSubstitutor;

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

    /// Substitute template variables, propagate task-level sandbox flags, and run
    /// the named `[tasks.<name>]` command chain.
    pub fn run_task(
        name: &str,
        config: &Config,
        extra_args: &[String],
        runner: &mut BaseRunner,
    ) -> Result<bool, RunError> {
        let task_info = config
            .get_tasks()
            .get(name)
            .ok_or_else(|| RunError::config(format!("Task '{name}' not found in Run.toml.")))?;

        let raw_cmd = match task_info {
            TaskConfig::Simple(cmd) => {
                if cmd.trim().is_empty() {
                    return Err(RunError::config(format!(
                        "Task '{name}' must define a non-empty command string."
                    )));
                }
                cmd.clone()
            }
            TaskConfig::Detailed(table) => {
                let cmd = table
                    .command
                    .as_deref()
                    .filter(|s| !s.trim().is_empty())
                    .ok_or_else(|| {
                        RunError::config(format!("Task '{name}' must define a 'command' string."))
                    })?;
                if table.sandbox {
                    runner.options.sandbox = true;
                }
                if table.sandbox_net {
                    runner.options.sandbox_net = true;
                }
                if table.restrict {
                    runner.options.restrict = true;
                }
                cmd.to_string()
            }
        };

        let out_dir = runner.options.out_dir.as_deref();
        let var_ctx = VariableSubstitutor::build_file_context(None, None, out_dir);
        let task_cmd = VariableSubstitutor::substitute_string(&raw_cmd, &var_ctx, true);

        runner
            .printer
            .action("TASK", &format!("{name}: {task_cmd}"), TagColor::Cyan);
        run_step(&task_cmd, extra_args, runner, false)
    }
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
    use crate::runner::handler::RunnerOptions;
    use crate::runner::test_support::TempDir;
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
}
