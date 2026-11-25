//! Differential fuzzing and stress testing engine (`--stress <gen> <brute>`).
//!
//! Compiles `sol`, `gen`, `brute`, and optional `--checker` once via
//! [`TestcasesRunner`] (reusing the SHA-256 build
//! cache), runs seeded sequential iterations, stops on the first mismatch or
//! crash, saves `fail.in`, `expected.out`, and `actual.out`, and prints the
//! colorized unified diff and failing seed.

use std::fs;
use std::path::{Path, PathBuf};

use crate::core::engine::CompilerRunner;
use crate::core::test_runner::TestcasesRunner;
use crate::model::error::RunError;
use crate::model::options::UiMode;
use crate::util::output::{Printer, TagColor, Verbosity};
use crate::util::spinner::ProgressBar;

/// Configuration for a `--stress` differential fuzzing session.
#[derive(Debug, Clone)]
pub struct StressConfig {
    /// Path to the test-case generator program (prints one test case to stdout).
    pub gen_path: PathBuf,
    /// Path to the trusted reference / brute-force program.
    pub brute_path: PathBuf,
    /// Optional custom checker program invoked with `<in> <expected> <actual>` (exit 0 = pass).
    pub checker_path: Option<PathBuf>,
    /// Number of iterations to run (default: 100).
    pub runs: usize,
    /// Starting seed passed to `gen` as `argv[1]` and `RUN_SEED` (default: 1).
    pub start_seed: u64,
    /// Directory where `fail.in`, `expected.out`, and `actual.out` are written on failure.
    pub save_dir: PathBuf,
}

/// Prepared executable target for stress iterations (compiled once or script).
#[derive(Debug, Clone)]
enum PreparedTarget {
    /// Pre-compiled binary (from `prepare_compiled_binary` or prebuilt executable).
    Binary(PathBuf),
    /// Interpreted script or multi-file target executed via `CompilerRunner`.
    Source {
        /// Target source file(s).
        files: Vec<PathBuf>,
        /// Whether multi-file compilation mode applies.
        is_multi: bool,
    },
}

/// Differential stress-testing coordinator.
pub struct StressRunner;

impl StressRunner {
    /// Normalize output lines for default comparison (strip trailing whitespace per line and trailing empty lines).
    pub fn normalize_output(text: &str) -> String {
        let mut lines: Vec<&str> = text.lines().map(str::trim_end).collect();
        while lines.last().is_some_and(|l| l.is_empty()) {
            lines.pop();
        }
        lines.join("\n")
    }

    /// Compile a target once if it is a compiled language, returning a [`PreparedTarget`].
    fn prepare_target(
        runner: &mut CompilerRunner,
        files: &[PathBuf],
        is_multi: bool,
    ) -> Result<PreparedTarget, String> {
        let (is_compiled, bin_path) =
            TestcasesRunner::prepare_compiled_binary(runner, files, is_multi)?;
        if is_compiled
            && let Some(bin) = bin_path
            && bin.exists()
        {
            Ok(PreparedTarget::Binary(bin))
        } else {
            Ok(PreparedTarget::Source {
                files: files.to_vec(),
                is_multi,
            })
        }
    }

    /// Execute a [`PreparedTarget`] with optional extra runtime args and return `(ok, captured_stdout)`.
    fn run_target(
        runner: &mut CompilerRunner,
        target: &PreparedTarget,
        args: &[String],
    ) -> Result<(bool, String), RunError> {
        let orig_run_args = std::mem::replace(&mut runner.base.run_args, args.to_vec());
        runner.base.last_captured_stdout.clear();

        let res = match target {
            PreparedTarget::Binary(bin) => runner.execute_binary(bin, &[]),
            PreparedTarget::Source { files, is_multi } => {
                if *is_multi {
                    runner.handle_multi_compile(files)
                } else {
                    runner.handle_single_file(&files[0])
                }
            }
        };

        runner.base.run_args = orig_run_args;
        let out = std::mem::take(&mut runner.base.last_captured_stdout);
        res.map(|ok| (ok, out))
    }

    /// Save counterexample artifacts (`fail.in`, `expected.out`, `actual.out`) into `save_dir`.
    fn save_counterexample(
        save_dir: &Path,
        input: &str,
        expected: &str,
        actual: &str,
    ) -> Result<(PathBuf, PathBuf, PathBuf), RunError> {
        fs::create_dir_all(save_dir)?;
        let fail_in = save_dir.join("fail.in");
        let exp_out = save_dir.join("expected.out");
        let act_out = save_dir.join("actual.out");
        fs::write(&fail_in, input)?;
        fs::write(&exp_out, expected)?;
        fs::write(&act_out, actual)?;
        Ok((fail_in, exp_out, act_out))
    }

    /// Run differential stress testing of `sol_files` against `cfg.gen_path` and `cfg.brute_path`.
    pub fn run_stress(
        runner: &mut CompilerRunner,
        sol_files: &[PathBuf],
        is_multi: bool,
        cfg: &StressConfig,
    ) -> bool {
        if sol_files.is_empty() {
            runner.printer.error("No solution file provided for --stress.");
            return false;
        }
        if !cfg.gen_path.is_file() {
            runner.printer.error(&format!(
                "Generator file not found: {}",
                cfg.gen_path.display()
            ));
            return false;
        }
        if !cfg.brute_path.is_file() {
            runner.printer.error(&format!(
                "Brute/reference file not found: {}",
                cfg.brute_path.display()
            ));
            return false;
        }
        if let Some(ref chk) = cfg.checker_path
            && !chk.is_file()
        {
            runner
                .printer
                .error(&format!("Checker file not found: {}", chk.display()));
            return false;
        }

        let runs = cfg.runs.max(1);
        runner.printer.info(&format!(
            "Preparing stress test targets ({runs} runs, seed {}..={})",
            cfg.start_seed,
            cfg.start_seed + (runs as u64) - 1
        ));

        let sol_target = match Self::prepare_target(runner, sol_files, is_multi) {
            Ok(t) => t,
            Err(msg) => {
                runner.printer.error(&format!("Solution build failed: {msg}"));
                return false;
            }
        };
        let gen_target = match Self::prepare_target(runner, std::slice::from_ref(&cfg.gen_path), false) {
            Ok(t) => t,
            Err(msg) => {
                runner.printer.error(&format!("Generator build failed: {msg}"));
                return false;
            }
        };
        let brute_target =
            match Self::prepare_target(runner, std::slice::from_ref(&cfg.brute_path), false) {
                Ok(t) => t,
                Err(msg) => {
                    runner.printer.error(&format!("Brute build failed: {msg}"));
                    return false;
                }
            };
        let checker_target = match cfg.checker_path.as_ref() {
            Some(chk) => match Self::prepare_target(runner, std::slice::from_ref(chk), false) {
                Ok(t) => Some(t),
                Err(msg) => {
                    runner.printer.error(&format!("Checker build failed: {msg}"));
                    return false;
                }
            },
            None => None,
        };

        // Snapshot runner state restored unconditionally after stress loop.
        let orig_stdin = runner.base.options.stdin.take();
        let orig_expect = runner.base.options.expect.take();
        let orig_quiet = runner.base.options.quiet;
        let orig_time = runner.base.options.time;
        let orig_mem = runner.base.options.memory;
        let orig_env = runner.base.options.env.clone();
        let orig_buffered = runner.base.buffered_stdin().map(|b| b.to_vec());
        let orig_silent = runner.base.capture_silent;
        let orig_printer = runner.printer;
        let orig_base_printer = runner.base.printer;

        let quiet_printer = Printer::with_ui_mode(
            Verbosity::Quiet,
            orig_printer.colors(),
            orig_printer.ui_mode(),
        );
        runner.base.options.quiet = true;
        runner.base.options.time = false;
        runner.base.options.memory = false;
        runner.base.capture_silent = true;
        runner.printer = quiet_printer;
        runner.base.printer = quiet_printer;

        let progress = ProgressBar::new(orig_printer, runs);
        let mut all_passed = true;

        for idx in 0..runs {
            let seed = cfg.start_seed + idx as u64;
            progress.update(idx + 1, &format!("seed {seed}"));

            // 1. Run generator with `argv[1] = seed` and `RUN_SEED=<seed>`.
            runner.base.options.stdin = None;
            runner.base.set_buffered_stdin(None);
            let mut gen_env = orig_env.clone();
            gen_env.push(format!("RUN_SEED={seed}"));
            runner.base.options.env = gen_env;

            let gen_input = match Self::run_target(runner, &gen_target, &[seed.to_string()]) {
                Ok((true, out)) => out,
                Ok((false, _)) => {
                    progress.clear();
                    orig_printer.error(&format!(
                        "Generator failed on iteration #{} (seed {seed})",
                        idx + 1
                    ));
                    all_passed = false;
                    break;
                }
                Err(err) => {
                    progress.clear();
                    orig_printer.error(&format!(
                        "Generator error on iteration #{} (seed {seed}): {err}",
                        idx + 1
                    ));
                    all_passed = false;
                    break;
                }
            };

            // 2. Run brute-force / reference solution with generated input.
            runner.base.options.env = orig_env.clone();
            runner.base.options.stdin = Some("-".to_string());
            runner
                .base
                .set_buffered_stdin(Some(gen_input.as_bytes().to_vec()));

            let brute_out = match Self::run_target(runner, &brute_target, &[]) {
                Ok((true, out)) => out,
                Ok((false, out)) => {
                    progress.clear();
                    let _ = Self::save_counterexample(&cfg.save_dir, &gen_input, &out, "");
                    orig_printer.error(&format!(
                        "Brute/reference failed on iteration #{} (seed {seed}). Saved fail.in in {}",
                        idx + 1,
                        cfg.save_dir.display()
                    ));
                    all_passed = false;
                    break;
                }
                Err(err) => {
                    progress.clear();
                    let _ = Self::save_counterexample(&cfg.save_dir, &gen_input, "", "");
                    orig_printer.error(&format!(
                        "Brute/reference error on iteration #{} (seed {seed}): {err}",
                        idx + 1
                    ));
                    all_passed = false;
                    break;
                }
            };

            // 3. Run candidate solution with the exact same generated input.
            runner.base.options.stdin = Some("-".to_string());
            runner
                .base
                .set_buffered_stdin(Some(gen_input.as_bytes().to_vec()));

            let sol_out = match Self::run_target(runner, &sol_target, &[]) {
                Ok((true, out)) => out,
                Ok((false, out)) => {
                    progress.clear();
                    let _ = Self::save_counterexample(&cfg.save_dir, &gen_input, &brute_out, &out);
                    orig_printer.status(
                        "FAIL",
                        &format!(
                            "Solution exited non-zero on iteration #{} (seed {seed}). Saved fail.in, expected.out, actual.out in {}",
                            idx + 1,
                            cfg.save_dir.display()
                        ),
                        TagColor::Red,
                    );
                    all_passed = false;
                    break;
                }
                Err(err) => {
                    progress.clear();
                    let _ = Self::save_counterexample(&cfg.save_dir, &gen_input, &brute_out, "");
                    orig_printer.status(
                        "FAIL",
                        &format!(
                            "Solution failed on iteration #{} (seed {seed}): {err}. Saved fail.in in {}",
                            idx + 1,
                            cfg.save_dir.display()
                        ),
                        TagColor::Red,
                    );
                    all_passed = false;
                    break;
                }
            };

            // 4. Validate output via custom `--checker` or normalized diff.
            let matched = if let Some(ref chk_target) = checker_target {
                let tmp_dir = std::env::temp_dir().join(format!(
                    "run_stress_chk_{}_{}",
                    std::process::id(),
                    seed
                ));
                let chk_res = Self::save_counterexample(&tmp_dir, &gen_input, &brute_out, &sol_out)
                    .and_then(|(in_p, exp_p, act_p)| {
                        runner.base.options.stdin = None;
                        runner.base.set_buffered_stdin(None);
                        let chk_args = vec![
                            in_p.to_string_lossy().into_owned(),
                            exp_p.to_string_lossy().into_owned(),
                            act_p.to_string_lossy().into_owned(),
                        ];
                        Self::run_target(runner, chk_target, &chk_args).map(|(ok, _)| ok)
                    });
                let _ = fs::remove_dir_all(&tmp_dir);
                chk_res.unwrap_or(false)
            } else {
                Self::normalize_output(&brute_out) == Self::normalize_output(&sol_out)
            };

            if !matched {
                progress.clear();
                let _ = Self::save_counterexample(&cfg.save_dir, &gen_input, &brute_out, &sol_out);
                orig_printer.status(
                    "FAIL",
                    &format!(
                        "Mismatch on iteration #{} (seed {seed}). Saved fail.in, expected.out, actual.out in {}",
                        idx + 1,
                        cfg.save_dir.display()
                    ),
                    TagColor::Red,
                );
                orig_printer.diff(&brute_out, &sol_out, "expected.out");
                all_passed = false;
                break;
            }
        }

        // Restore runner state unconditionally.
        runner.base.options.stdin = orig_stdin;
        runner.base.options.expect = orig_expect;
        runner.base.options.quiet = orig_quiet;
        runner.base.options.time = orig_time;
        runner.base.options.memory = orig_mem;
        runner.base.options.env = orig_env;
        runner.base.set_buffered_stdin(orig_buffered);
        runner.base.capture_silent = orig_silent;
        runner.printer = orig_printer;
        runner.base.printer = orig_base_printer;

        if all_passed {
            if runner.printer.ui_mode() == UiMode::Modern && !runner.printer.has_tui_sink() {
                progress.finish(runs, 0);
            } else {
                runner.printer.status(
                    "PASS",
                    &format!(
                        "All {runs} stress tests passed (seeds {}..={})",
                        cfg.start_seed,
                        cfg.start_seed + (runs as u64) - 1
                    ),
                    TagColor::Green,
                );
            }
        }
        all_passed
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::core::test_support::TempDir;
    use crate::model::config::Config;
    use crate::model::options::{ExecOutcome, MockCommandExecutor, RunnerOptions};
    use crate::util::output::{Colors, Printer, Verbosity};

    use super::*;

    /// Verify trailing whitespace and blank line normalization.
    #[test]
    fn test_normalize_output() {
        assert_eq!(
            StressRunner::normalize_output("1 2  \r\n3 4\t\n\n"),
            "1 2\n3 4"
        );
    }

    /// Verify fail-fast counterexample saving on mismatch via `MockCommandExecutor`.
    #[test]
    fn test_stress_runner_saves_counterexample_on_mismatch() {
        let tmp = TempDir::new().expect("tmp");
        let sol = tmp.path().join("sol.py");
        let gen_file = tmp.path().join("gen.py");
        let brute = tmp.path().join("brute.py");
        fs::write(&sol, "print(0)\n").unwrap();
        fs::write(&gen_file, "print(1)\n").unwrap();
        fs::write(&brute, "print(1)\n").unwrap();

        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let mut runner = CompilerRunner::with_cache_base(
            RunnerOptions::default(),
            None,
            None,
            Config::default(),
            printer,
            tmp.path(),
            &tmp.path().join("cache"),
        )
        .expect("runner");

        let mut mock = MockCommandExecutor::new();
        let mut step = 0usize;
        mock.expect_execute().times(3).returning(move |_| {
            step += 1;
            let out = match step {
                1 => "42\n", // gen
                2 => "84\n", // brute
                _ => "99\n", // sol (mismatch!)
            };
            Ok(ExecOutcome {
                captured_stdout: out.to_string(),
                elapsed_secs: 0.001,
                memory_bytes: None,
            })
        });
        runner.base.set_executor(Arc::new(mock));

        let cfg = StressConfig {
            gen_path: gen_file,
            brute_path: brute,
            checker_path: None,
            runs: 5,
            start_seed: 10,
            save_dir: tmp.path().to_path_buf(),
        };

        assert!(!StressRunner::run_stress(&mut runner, &[sol], false, &cfg));
        assert_eq!(fs::read_to_string(tmp.path().join("fail.in")).unwrap(), "42\n");
        assert_eq!(
            fs::read_to_string(tmp.path().join("expected.out")).unwrap(),
            "84\n"
        );
        assert_eq!(
            fs::read_to_string(tmp.path().join("actual.out")).unwrap(),
            "99\n"
        );
    }
}
