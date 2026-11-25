//! Runner options and subprocess execution specification/outcome models.

use std::collections::BTreeMap;
use std::path::PathBuf;

use crate::model::error::RunError;

/// Terminal presentation mode resolved from CLI flags and TTY detection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum UiMode {
    /// Classic `[ TAG ]` line output (used for `--plain` and non-TTY pipes/CI).
    #[default]
    Plain,
    /// Node-style Vite/Clack rail output with Braille spinners on interactive TTYs.
    Modern,
    /// Full-screen interactive workspace dashboard powered by `ratatui`.
    Tui,
}

/// CLI and configuration options controlling compilation, execution, debugging, and isolation.
#[derive(Debug, Clone, Default)]
pub struct RunnerOptions {
    /// Compile multiple source files together (`-m` / `--multi`).
    pub multi: bool,
    /// Keep compiled output binaries/classes after execution (`--keep` or `-B`).
    pub keep: bool,
    /// Compile only without executing the resulting binary (`-B` / `--build-only` / `--no-run`).
    pub build_only: bool,
    /// Measure and display wall-clock execution duration (`-t` / `--time`).
    pub time: bool,
    /// Measure and display peak resident memory usage (`-M` / `--mem` / `--memory`).
    pub memory: bool,
    /// Print commands without executing or deleting files (`-d` / `--dry-run`).
    pub dry_run: bool,
    /// Named preset from `Run.toml` `[presets.<name>]` (`-p` / `--preset`).
    pub preset: Option<String>,
    /// Disable build/shebang binary caching (`--no-cache`).
    pub no_cache: bool,
    /// Suppress routine compiler output and informational logs (`-q` / `--quiet`).
    pub quiet: bool,
    /// Watch mode flag (`-w` / `--watch`).
    pub watch: bool,
    /// Override interactive prompts, suspicious paths, foreign `.venv` UID, and outside `--expect` (`-f` / `--force`).
    pub force: bool,
    /// Parallel C/C++ object compilation worker count (`-j` / `--jobs`).
    pub jobs: Option<usize>,
    /// Expected output file path for diff verification (`--expect`).
    pub expect: Option<String>,
    /// Batch testcase directory (`--test-dir`).
    pub test_dir: Option<String>,
    /// Launch default interactive debugger (`--debug`).
    pub debug: bool,
    /// Launch interactive GDB (`--gdb`).
    pub gdb: bool,
    /// Launch interactive LLDB (`--lldb`).
    pub lldb: bool,
    /// Run binary under Valgrind leak checking (`--valgrind`).
    pub valgrind: bool,
    /// Compile with AddressSanitizer + UBSan (`--asan`).
    pub asan: bool,
    /// Compile with ThreadSanitizer (`--tsan`).
    pub tsan: bool,
    /// Compile with custom `-fsanitize=<value>` (`--sanitize`).
    pub sanitize: Option<String>,
    /// Maximum execution/compilation timeout in seconds (`--timeout`).
    pub timeout: Option<f64>,
    /// Stdin file path or `"-"` for buffered pipe input (`-i` / `--stdin`).
    pub stdin: Option<String>,
    /// Custom `KEY=VALUE` environment overrides passed via `--env`.
    pub env: Vec<String>,
    /// Output directory for compiled binaries (`--out-dir`).
    pub out_dir: Option<String>,
    /// Explicit compiler override (`--compiler`).
    pub compiler: Option<String>,
    /// Enable native Linux Bubblewrap isolation (`--restrict`).
    pub restrict: bool,
    /// Enable container sandboxing via Docker/Podman (`--sandbox`).
    pub sandbox: bool,
    /// Allow network access inside sandboxed execution (`--sandbox-net`).
    pub sandbox_net: bool,
    /// Allow running as root and bypass suspicious flag filtering (`--unsafe`).
    pub unsafe_mode: bool,
    /// Cargo release build flag used in Cargo project execution mode.
    pub release: bool,
    /// Active source paths, used to extend workspace containment checks for `--expect`.
    pub target_files: Vec<PathBuf>,
    /// Extra compiler/interpreter flags already split or appended before `extra_flags_str`.
    pub extra_flags: Vec<String>,
    /// Extra runtime arguments appended after target args.
    pub run_args: Vec<String>,
    /// Force classic `[ TAG ]` output instead of Node-style animated CLI (`--plain`).
    pub plain: bool,
    /// Launch full-screen interactive workspace TUI (`--tui`).
    pub tui: bool,
    /// Optional micro-benchmark iteration count (`--bench [N]`).
    pub bench: Option<usize>,
    /// Warmup iteration count for `--bench` (`--warmup <W>`, default `2`).
    pub warmup: usize,
}

/// Statistical summary computed across micro-benchmark iterations (`--bench`).
#[derive(Debug, Clone, PartialEq)]
pub struct BenchStats {
    /// Number of measured iterations executed.
    pub runs: usize,
    /// Number of warmup iterations executed before measurement.
    pub warmup: usize,
    /// Raw wall-clock durations in seconds for each measured iteration.
    pub samples_secs: Vec<f64>,
    /// Minimum duration in seconds.
    pub min_secs: f64,
    /// Median (50th percentile) duration in seconds.
    pub median_secs: f64,
    /// Arithmetic mean duration in seconds.
    pub mean_secs: f64,
    /// 95th percentile duration in seconds.
    pub p95_secs: f64,
    /// Maximum duration in seconds.
    pub max_secs: f64,
    /// Peak resident memory observed across measured iterations in bytes.
    pub peak_memory_bytes: Option<u64>,
}

impl BenchStats {
    /// Compute [`BenchStats`] from raw duration samples and peak memory.
    pub fn from_samples(warmup: usize, samples_secs: Vec<f64>, peak_memory_bytes: Option<u64>) -> Self {
        let runs = samples_secs.len();
        if runs == 0 {
            return Self {
                runs: 0,
                warmup,
                samples_secs,
                min_secs: 0.0,
                median_secs: 0.0,
                mean_secs: 0.0,
                p95_secs: 0.0,
                max_secs: 0.0,
                peak_memory_bytes,
            };
        }
        let mut sorted = samples_secs.clone();
        sorted.sort_by(|a, b| a.total_cmp(b));
        let min_secs = sorted[0];
        let max_secs = sorted[runs - 1];
        let mean_secs = sorted.iter().sum::<f64>() / (runs as f64);
        let median_secs = if runs % 2 == 1 {
            sorted[runs / 2]
        } else {
            (sorted[runs / 2 - 1] + sorted[runs / 2]) / 2.0
        };
        let p95_idx = ((runs as f64 * 0.95).ceil() as usize)
            .saturating_sub(1)
            .min(runs - 1);
        let p95_secs = sorted[p95_idx];

        Self {
            runs,
            warmup,
            samples_secs,
            min_secs,
            median_secs,
            mean_secs,
            p95_secs,
            max_secs,
            peak_memory_bytes,
        }
    }

    /// Render a compact Unicode sparkline (`▁▂▃▄▅▆▇█`) for the measured duration samples.
    pub fn sparkline(&self) -> String {
        const BLOCKS: &[char] = &['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
        if self.samples_secs.is_empty() {
            return String::new();
        }
        let span = self.max_secs - self.min_secs;
        self.samples_secs
            .iter()
            .map(|&v| {
                if span <= f64::EPSILON {
                    BLOCKS[3]
                } else {
                    let norm = ((v - self.min_secs) / span).clamp(0.0, 1.0);
                    let idx = (norm * ((BLOCKS.len() - 1) as f64)).round() as usize;
                    BLOCKS[idx.min(BLOCKS.len() - 1)]
                }
            })
            .collect()
    }
}

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

/// Subprocess execution backend for `BaseRunner`, enabling `mockall` white-box testing.
#[cfg_attr(test, mockall::automock)]
pub trait CommandExecutor: Send + Sync + std::fmt::Debug {
    /// Execute the command described by `spec` and return its [`ExecOutcome`].
    fn execute(&self, spec: &ExecSpec) -> Result<ExecOutcome, RunError>;
}
