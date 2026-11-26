//! Micro-benchmarking engine (`--bench [N]` / `--warmup [W]`).
//!
//! Compiles compiled-language targets once (reusing the SHA-256 build cache),
//! runs warmup + measured iterations, fails fast on any non-zero exit/timeout,
//! and reports min / median / mean / p95 / max wall-clock time and peak RSS
//! across [`UiMode::Plain`], [`UiMode::Modern`] (with Unicode sparkline), and
//! [`UiMode::Tui`].

use std::path::{Path, PathBuf};
use std::time::Instant;

use crate::core::engine::CompilerRunner;
use crate::core::test_runner::TestcasesRunner;
use crate::model::options::{BenchStats, UiMode};
use crate::util::output::{emit_tui_event, Printer, TagColor, UiEvent, Verbosity};
use crate::util::spinner::ProgressBar;

/// Micro-benchmarking coordinator for source files, binaries, and `PATH` commands.
pub struct BenchRunner;

impl BenchRunner {
    /// Format a duration in seconds into a human-readable string (`µs` / `ms` / `s`).
    pub fn format_duration(secs: f64) -> String {
        if secs < 0.001 {
            format!("{:.1}µs", secs * 1_000_000.0)
        } else if secs < 1.0 {
            format!("{:.2}ms", secs * 1_000.0)
        } else {
            format!("{secs:.3}s")
        }
    }

    /// Format a memory measurement in bytes into `KB` or `MB`.
    pub fn format_memory(bytes: u64) -> String {
        let mb = bytes as f64 / (1024.0 * 1024.0);
        if mb >= 1.0 {
            format!("{mb:.2} MB")
        } else {
            format!("{:.1} KB", bytes as f64 / 1024.0)
        }
    }

    /// Benchmark one or more source files or a local prebuilt binary.
    pub fn run_bench_targets(
        runner: &mut CompilerRunner,
        target_files: &[PathBuf],
        is_multi: bool,
        iterations: usize,
        warmup: usize,
    ) -> bool {
        if target_files.is_empty() {
            runner.printer.error("No target files provided for --bench.");
            return false;
        }
        let runs = iterations.max(1);
        let label = target_files[0]
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| target_files[0].display().to_string());

        let (is_compiled, bin_path) =
            match TestcasesRunner::prepare_compiled_binary(runner, target_files, is_multi) {
                Ok(res) => res,
                Err(msg) => {
                    runner.printer.error(&msg);
                    return false;
                }
            };

        Self::execute_bench_loop(runner, &label, runs, warmup, |r| {
            if is_compiled
                && let Some(ref bin) = bin_path
                && bin.exists()
            {
                r.execute_binary(bin, &[])
            } else if is_multi {
                r.handle_multi_compile(target_files)
            } else {
                r.handle_single_file(&target_files[0])
            }
        })
    }

    /// Benchmark a direct binary or `PATH` command (`run --bench -- ls -la`).
    pub fn run_bench_command(
        runner: &mut CompilerRunner,
        bin_path: &Path,
        args: &[String],
        iterations: usize,
        warmup: usize,
    ) -> bool {
        let runs = iterations.max(1);
        let label = bin_path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| bin_path.display().to_string());

        Self::execute_bench_loop(runner, &label, runs, warmup, |r| {
            r.execute_binary(bin_path, args)
        })
    }

    /// Shared warmup + measured iteration driver with fail-fast error handling.
    fn execute_bench_loop(
        runner: &mut CompilerRunner,
        label: &str,
        runs: usize,
        warmup: usize,
        mut step_fn: impl FnMut(&mut CompilerRunner) -> Result<bool, crate::model::error::RunError>,
    ) -> bool {
        let orig_time = runner.base.options.time;
        let orig_mem = runner.base.options.memory;
        let orig_bench = runner.base.options.bench;
        let verbose = runner.printer.verbosity() >= Verbosity::Verbose;

        runner.base.options.time = false;
        runner.base.options.memory = true;
        runner.base.options.bench = Some(runs);

        let result = runner.with_quiet_capture(!verbose, !verbose, false, |runner, orig_printer| {
            let total_steps = warmup + runs;
            let progress = ProgressBar::new(orig_printer, total_steps);
            let mut samples = Vec::with_capacity(runs);
            let mut peak_mem: Option<u64> = None;

            for step in 0..total_steps {
                let is_warmup = step < warmup;
                let step_label = if is_warmup {
                    format!("warmup {}/{warmup}", step + 1)
                } else {
                    format!("run {}/{runs}", step - warmup + 1)
                };
                progress.update(step + 1, &step_label);

                let t0 = Instant::now();
                match step_fn(runner) {
                    Ok(true) => {
                        let elapsed = t0.elapsed().as_secs_f64();
                        if !is_warmup {
                            samples.push(elapsed);
                            if let Some(mem) = runner.base.last_memory_bytes {
                                peak_mem = Some(peak_mem.map_or(mem, |prev| prev.max(mem)));
                            }
                        }
                    }
                    Ok(false) => {
                        progress.clear();
                        orig_printer.error(&format!(
                            "Benchmark aborted: {label} failed during {step_label}"
                        ));
                        return None;
                    }
                    Err(err) => {
                        progress.clear();
                        orig_printer.error(&format!(
                            "Benchmark aborted during {step_label}: {err}"
                        ));
                        return None;
                    }
                }
            }

            Some(BenchStats::from_samples(warmup, samples, peak_mem))
        });

        runner.base.options.time = orig_time;
        runner.base.options.memory = orig_mem;
        runner.base.options.bench = orig_bench;

        let Some(stats) = result else {
            return false;
        };

        Self::emit_report(runner.printer, label, &stats);
        true
    }

    /// Benchmark two targets side-by-side (`--compare`) and print speedup and memory deltas.
    pub fn run_bench_compare(
        runner: &mut CompilerRunner,
        targets_a: &[PathBuf],
        targets_b: &[PathBuf],
        multi: bool,
        runs: usize,
        warmup: usize,
    ) -> bool {
        let label_a = targets_a
            .first()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "target_a".to_string());
        let label_b = targets_b
            .first()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "target_b".to_string());

        let Some(stats_a) = Self::measure_targets(runner, targets_a, multi, runs, warmup) else {
            return false;
        };
        Self::emit_report(runner.printer, &label_a, &stats_a);

        let Some(stats_b) = Self::measure_targets(runner, targets_b, multi, runs, warmup) else {
            return false;
        };
        Self::emit_report(runner.printer, &label_b, &stats_b);

        let ratio = if stats_b.median_secs > 0.0 {
            stats_a.median_secs / stats_b.median_secs
        } else {
            1.0
        };
        let summary = if ratio > 1.0 {
            format!(
                "Compare: {label_b} is {ratio:.2}x faster than {label_a} (median {} vs {})",
                Self::format_duration(stats_b.median_secs),
                Self::format_duration(stats_a.median_secs),
            )
        } else if ratio < 1.0 && ratio > 0.0 {
            format!(
                "Compare: {label_a} is {:.2}x faster than {label_b} (median {} vs {})",
                1.0 / ratio,
                Self::format_duration(stats_a.median_secs),
                Self::format_duration(stats_b.median_secs),
            )
        } else {
            format!(
                "Compare: {label_a} and {label_b} have equal median ({})",
                Self::format_duration(stats_a.median_secs),
            )
        };
        runner.printer.status("COMPARE", &summary, TagColor::Cyan);
        true
    }

    /// Measure `files` for `runs` (+ `warmup`) and return [`BenchStats`] without printing.
    fn measure_targets(
        runner: &mut CompilerRunner,
        files: &[PathBuf],
        multi: bool,
        runs: usize,
        warmup: usize,
    ) -> Option<BenchStats> {
        let (is_compiled, bin_path) =
            match TestcasesRunner::prepare_compiled_binary(runner, files, multi) {
                Ok(res) => res,
                Err(msg) => {
                    runner.printer.error(&msg);
                    return None;
                }
            };

        let orig_time = runner.base.options.time;
        let orig_mem = runner.base.options.memory;
        let orig_bench = runner.base.options.bench;
        let verbose = runner.printer.verbosity() >= Verbosity::Verbose;

        runner.base.options.time = false;
        runner.base.options.memory = true;
        runner.base.options.bench = Some(runs);

        let stats = runner.with_quiet_capture(!verbose, !verbose, false, |runner, _| {
            let mut samples = Vec::with_capacity(runs);
            let mut peak_mem: Option<u64> = None;
            for step in 0..(warmup + runs) {
                let t0 = Instant::now();
                let step_ok = if is_compiled
                    && let Some(ref bin) = bin_path
                    && bin.exists()
                {
                    runner.execute_binary(bin, &[])
                } else if multi {
                    runner.handle_multi_compile(files)
                } else {
                    runner.handle_single_file(&files[0])
                };
                if step_ok.unwrap_or(false) {
                    let elapsed = t0.elapsed().as_secs_f64();
                    if step >= warmup {
                        samples.push(elapsed);
                        if let Some(mem) = runner.base.last_memory_bytes {
                            peak_mem = Some(peak_mem.map_or(mem, |prev| prev.max(mem)));
                        }
                    }
                } else {
                    return None;
                }
            }
            Some(BenchStats::from_samples(warmup, samples, peak_mem))
        });

        runner.base.options.time = orig_time;
        runner.base.options.memory = orig_mem;
        runner.base.options.bench = orig_bench;

        stats
    }

    /// Render benchmark summary according to active [`UiMode`] or emit [`UiEvent::BenchSummary`].
    pub fn emit_report(printer: Printer, label: &str, stats: &BenchStats) {
        if emit_tui_event(UiEvent::BenchSummary(stats.clone())) {
            return;
        }

        let min_s = Self::format_duration(stats.min_secs);
        let med_s = Self::format_duration(stats.median_secs);
        let mean_s = Self::format_duration(stats.mean_secs);
        let p95_s = Self::format_duration(stats.p95_secs);
        let max_s = Self::format_duration(stats.max_secs);
        let mem_part = stats
            .peak_memory_bytes
            .filter(|&b| b > 0)
            .map(|b| format!(" | Peak Memory: {}", Self::format_memory(b)))
            .unwrap_or_default();

        if printer.ui_mode() == UiMode::Modern {
            let colors = printer.colors();
            let rail = colors.style_text("│", TagColor::Gray, false);
            let corner = colors.style_text("╰", TagColor::Gray, false);
            let spark = colors.style_text(&stats.sparkline(), TagColor::Cyan, true);
            let head = colors.style_text(
                &format!("Bench {label} ({} runs, {} warmup)", stats.runs, stats.warmup),
                TagColor::Green,
                true,
            );
            let metrics = colors.style_text(
                &format!(
                    "min {min_s} · med {med_s} · mean {mean_s} · p95 {p95_s} · max {max_s}{mem_part}"
                ),
                TagColor::Yellow,
                false,
            );
            println!("\r\x1b[2K{rail}  ✔ {head}");
            println!("{rail}  {spark}");
            println!("{corner}  ⏱ {metrics}");
        } else {
            printer.status(
                "BENCH",
                &format!("{label} ({} runs, {} warmup)", stats.runs, stats.warmup),
                TagColor::Cyan,
            );
            println!(
                "  min: {min_s} | median: {med_s} | mean: {mean_s} | p95: {p95_s} | max: {max_s}{mem_part}"
            );
        }
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

    /// Verify percentile/mean/min/max and sparkline calculation.
    #[test]
    fn test_bench_stats_percentiles_and_sparkline() {
        let stats = BenchStats::from_samples(
            2,
            vec![0.010, 0.020, 0.030, 0.040, 0.050],
            Some(4 * 1024 * 1024),
        );
        assert_eq!(stats.runs, 5);
        assert_eq!(stats.warmup, 2);
        assert!((stats.min_secs - 0.010).abs() < 1e-9);
        assert!((stats.median_secs - 0.030).abs() < 1e-9);
        assert!((stats.mean_secs - 0.030).abs() < 1e-9);
        assert!((stats.p95_secs - 0.050).abs() < 1e-9);
        assert!((stats.max_secs - 0.050).abs() < 1e-9);
        assert_eq!(stats.sparkline().chars().count(), 5);
        assert!(stats.sparkline().starts_with('▁'));
        assert!(stats.sparkline().ends_with('█'));
    }

    /// Verify `BenchRunner::run_bench_command` executes warmup + runs and restores options.
    #[test]
    fn test_bench_runner_warmup_and_measured_runs_via_mock() {
        let tmp = TempDir::new().expect("tmp");
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
        mock.expect_execute().times(5).returning(|_| {
            Ok(ExecOutcome {
                captured_stdout: String::new(),
                elapsed_secs: 0.005,
                memory_bytes: Some(2 * 1024 * 1024),
            })
        });
        runner.base.set_executor(Arc::new(mock));

        assert!(BenchRunner::run_bench_command(
            &mut runner,
            Path::new("/bin/echo"),
            &["hi".to_string()],
            3,
            2,
        ));
        assert!(!runner.base.options.time);
        assert!(!runner.base.options.memory);
        assert_eq!(runner.base.options.bench, None);
    }
}
