//! Custom and built-in `[[languages]]` execution handler (including Go).
//!
//! Adapted from Python's `src/runner/custom_language_handler.py`. Uses
//! [`crate::util::substitutions::VariableSubstitutor`] for `{file}` / `${out}`
//! / `${files}` / `${env:...}` expansion and `shlex::split` for command
//! tokenization across all 3 execution modes (`compile`/`build` + optional
//! `run`, `command`, and `runner` + `subcommand` + `type`).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::core::registry::{dotted_ext_lower, ExecutionContext, LanguageHandler};
use crate::util::config::{Config, CustomLanguageConfig};
use crate::model::error::RunError;
use crate::util::substitutions::VariableSubstitutor;

/// Handler executing user-defined `[[languages]]` configurations and built-in Go.
#[derive(Debug, Clone, Copy, Default)]
pub struct CustomLanguageHandler;

impl CustomLanguageHandler {
    /// Split a substituted template command with `shlex::split` and inject
    /// `extra_flags` + `preset_flags` immediately after `cmd[0]`.
    fn build_template_cmd(
        template: &str,
        var_ctx: &HashMap<String, String>,
        extra_flags: &[String],
        preset_flags: &[String],
    ) -> Result<Vec<String>, RunError> {
        let expanded = VariableSubstitutor::substitute_string(template, var_ctx, true);
        let mut tokens = shlex::split(&expanded).ok_or_else(|| {
            RunError::config(format!("Invalid quoting in language command template: {expanded}"))
        })?;
        if tokens.is_empty() {
            return Err(RunError::config("Language command template expanded to empty command."));
        }
        if !extra_flags.is_empty() || !preset_flags.is_empty() {
            let mut merged = Vec::with_capacity(tokens.len() + extra_flags.len() + preset_flags.len());
            merged.push(tokens.remove(0));
            merged.extend(extra_flags.iter().cloned());
            merged.extend(preset_flags.iter().cloned());
            merged.extend(tokens);
            tokens = merged;
        }
        Ok(tokens)
    }

    /// Execute single or multiple files using the resolved [`CustomLanguageConfig`].
    fn execute_custom(
        paths: &[PathBuf],
        lang_config: &CustomLanguageConfig,
        out_name: &Path,
        var_ctx: &HashMap<String, String>,
        ctx: &mut ExecutionContext<'_>,
    ) -> Result<bool, RunError> {
        let lang_name = &lang_config.name;
        let compile_template = lang_config
            .compile
            .as_deref()
            .or(lang_config.build.as_deref());
        let command_template = lang_config.command.as_deref();
        let run_template = lang_config.run.as_deref();

        let preset_flags = ctx
            .config
            .get_preset_flags(ctx.runner.options.preset.as_deref(), lang_name);
        let execute_args =
            VariableSubstitutor::substitute_list(&lang_config.arguments, var_ctx, false);

        // 1. Template-driven compiler (`compile` / `build`, optional `run`).
        if let Some(tmpl) = compile_template {
            let cmd = Self::build_template_cmd(
                tmpl,
                var_ctx,
                &ctx.runner.extra_flags,
                &preset_flags,
            )?;
            if !ctx.run_command(&cmd, false, true)? {
                return Ok(false);
            }
            ctx.runner.output_files.push(out_name.to_path_buf());

            if let Some(rt) = run_template {
                let trimmed = rt.trim();
                if !trimmed.is_empty()
                    && !matches!(trimmed, "${out}" | "{out}" | "$out" | "out")
                {
                    let expanded_run = VariableSubstitutor::substitute_string(rt, var_ctx, true);
                    let mut run_cmd = shlex::split(&expanded_run).ok_or_else(|| {
                        RunError::config(format!(
                            "Invalid quoting in language run template: {expanded_run}"
                        ))
                    })?;
                    run_cmd.extend(execute_args);
                    run_cmd.extend(ctx.runner.run_args.iter().cloned());
                    return ctx.run_command(&run_cmd, false, false);
                }
            }
            return ctx.execute_binary(out_name, &execute_args);
        }

        // 2. Template-driven interpreter / direct `command`.
        if let Some(tmpl) = command_template {
            let mut cmd = Self::build_template_cmd(
                tmpl,
                var_ctx,
                &ctx.runner.extra_flags,
                &preset_flags,
            )?;
            cmd.extend(execute_args);
            cmd.extend(ctx.runner.run_args.iter().cloned());
            return ctx.run_command(&cmd, false, false);
        }

        // 3. Structured `runner` + optional `subcommand` + `type` (`"interpreter"` / `"compiler"`).
        let runner_raw = lang_config.runner.as_deref().ok_or_else(|| {
            RunError::config(format!(
                "No runner, compile, or command specified for language: {lang_name}"
            ))
        })?;
        let runner_bin = VariableSubstitutor::substitute_string(runner_raw, var_ctx, false);
        let mut run_cmd = vec![runner_bin];

        if let Some(sub_raw) = lang_config.subcommand.as_deref() {
            let sub = VariableSubstitutor::substitute_string(sub_raw, var_ctx, false);
            if let Some(tokens) = shlex::split(&sub) {
                run_cmd.extend(tokens);
            }
        }

        let flags = VariableSubstitutor::substitute_list(&lang_config.flags, var_ctx, false);
        run_cmd.extend(flags);
        run_cmd.extend(ctx.runner.extra_flags.iter().cloned());
        run_cmd.extend(preset_flags);

        let lang_type = lang_config.lang_type.as_deref().unwrap_or("interpreter");
        match lang_type {
            "interpreter" => {
                for p in paths {
                    run_cmd.push(p.to_string_lossy().into_owned());
                }
                run_cmd.extend(execute_args);
                run_cmd.extend(ctx.runner.run_args.iter().cloned());
                ctx.run_command(&run_cmd, false, false)
            }
            "compiler" => {
                for p in paths {
                    run_cmd.push(p.to_string_lossy().into_owned());
                }
                run_cmd.push("-o".to_string());
                run_cmd.push(out_name.to_string_lossy().into_owned());
                if !ctx.run_command(&run_cmd, false, true)? {
                    return Ok(false);
                }
                ctx.runner.output_files.push(out_name.to_path_buf());
                ctx.execute_binary(out_name, &execute_args)
            }
            other => Err(RunError::config(format!(
                "Unknown language type '{other}' for {lang_name}"
            ))),
        }
    }
}

impl LanguageHandler for CustomLanguageHandler {
    /// Return `"CustomLanguageHandler"`.
    fn name(&self) -> &'static str {
        "CustomLanguageHandler"
    }

    /// Match any extension configured in `Run.toml` `[[languages]]` or `Cargo.toml` built-ins (Go).
    fn can_handle(&self, path: &Path, config: &Config) -> bool {
        let ext = dotted_ext_lower(path);
        !ext.is_empty() && config.is_custom_language_configured(&ext)
    }

    /// Execute a single custom-language source file.
    fn run_single(&self, fp: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let ext = ctx
            .detected_ext
            .as_deref()
            .map(str::to_ascii_lowercase)
            .unwrap_or_else(|| dotted_ext_lower(fp));
        let lang_config = ctx.config.get_language_by_extension(&ext).ok_or_else(|| {
            RunError::config(format!("No custom configuration found for {}", fp.display()))
        })?;
        let out_name = ctx.get_executable_path(fp)?;
        let var_ctx = VariableSubstitutor::build_file_context(
            Some(fp),
            Some(&out_name),
            ctx.runner.options.out_dir.as_deref(),
        );
        Self::execute_custom(&[fp.to_path_buf()], &lang_config, &out_name, &var_ctx, ctx)
    }

    /// Execute multiple custom-language source files (picking `main.*` stem if present for binary naming).
    fn run_multi(&self, paths: &[PathBuf], ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        if paths.is_empty() {
            return Ok(true);
        }
        let ext = dotted_ext_lower(&paths[0]);
        let lang_config = ctx.config.get_language_by_extension(&ext).ok_or_else(|| {
            RunError::config(format!(
                "No custom configuration found for {}",
                paths[0].display()
            ))
        })?;
        let main_candidate = paths
            .iter()
            .find(|p| {
                p.file_stem()
                    .and_then(|s| s.to_str())
                    .is_some_and(|s| s.eq_ignore_ascii_case("main"))
            })
            .unwrap_or(&paths[0]);
        let out_name = ctx.get_executable_path(main_candidate)?;
        let var_ctx = VariableSubstitutor::build_multi_file_context(
            paths,
            Some(&out_name),
            ctx.runner.options.out_dir.as_deref(),
        );
        Self::execute_custom(paths, &lang_config, &out_name, &var_ctx, ctx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::base::BaseRunner;
    use crate::core::registry::RunnerOptions;
    use crate::util::output::{Colors, Printer, Verbosity};

    /// Port of `dev:tests/test_go_runner.py::test_go_builtin_config`.
    #[test]
    fn test_go_builtin_config() {
        let cfg = Config::default();
        let go_cfg = cfg
            .get_language_by_extension(".go")
            .expect("Go must be configured out of the box");
        assert_eq!(go_cfg.name, "go");
        assert!(go_cfg.extensions.contains(&".go".to_string()));
        let compile = go_cfg.compile.as_deref().expect("go compile template");
        assert!(compile.contains("${files}") || compile.contains("{files}"));
        assert!(CustomLanguageHandler.can_handle(Path::new("main.go"), &cfg));
    }

    /// Port of `dev:tests/test_custom_language.py` dry-run checks across compile, command, and runner modes.
    #[test]
    fn test_custom_language_modes_dry_run() {
        let toml_cfg = r#"
[[languages]]
name = "wrapped"
extensions = [".w"]
compile = "cc -x c {file} -o {out}"
run = "{out} --flag"
type = "compiler"

[[languages]]
name = "cmdlang"
extensions = [".cmd"]
command = "python3 -V {file}"

[[languages]]
name = "legacylang"
extensions = [".leg"]
runner = "python3"
subcommand = "-u"
type = "interpreter"
flags = ["-B"]
arguments = ["--extra"]
"#;
        let cfg = Config::from_toml_str(toml_cfg, None, None).expect("parse custom config");
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions {
                dry_run: true,
                ..Default::default()
            },
            Some("-DTEST=1"),
            None,
            &cfg,
            printer,
            None,
        )
        .expect("runner");
        let mut ctx = ExecutionContext {
            config: &cfg,
            printer: &printer,
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };

        assert!(CustomLanguageHandler
            .run_single(Path::new("a.w"), &mut ctx)
            .expect("compile template"));
        assert!(CustomLanguageHandler
            .run_single(Path::new("b.cmd"), &mut ctx)
            .expect("command template"));
        assert!(CustomLanguageHandler
            .run_single(Path::new("c.leg"), &mut ctx)
            .expect("runner mode"));
    }

    /// Verify unknown language `type` rejects configuration cleanly.
    #[test]
    fn test_custom_language_unknown_type_errors() {
        let cfg = Config::from_toml_str(
            "[[languages]]\nname = \"weird\"\nextensions = [\".wrd\"]\nrunner = \"echo\"\ntype = \"alien\"\n",
            None,
            None,
        )
        .expect("parse weird");
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions::default(),
            None,
            None,
            &cfg,
            printer,
            None,
        )
        .expect("runner");
        let mut ctx = ExecutionContext {
            config: &cfg,
            printer: &printer,
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };
        let err = CustomLanguageHandler
            .run_single(Path::new("test.wrd"), &mut ctx)
            .expect_err("unknown type should fail");
        assert!(err.to_string().contains("Unknown language type 'alien'"));
    }
}
