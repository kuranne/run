//! Machine-readable JSON report schema (`--json`) and JSON Schema definition (`--json-schema`).

use serde::Serialize;

use crate::model::options::BenchStats;
use crate::util::output::UiEvent;

/// Compilation step telemetry inside [`JsonReport`].
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CompileReport {
    /// Compilation wall-clock duration in seconds.
    pub duration_secs: f64,
    /// Peak resident memory in bytes during compilation, if harvested.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub peak_memory_bytes: Option<u64>,
    /// Whether a cached build artifact was reused.
    pub cached: bool,
}

/// Runtime execution telemetry and captured output inside [`JsonReport`].
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RunReport {
    /// Execution wall-clock duration in seconds.
    pub duration_secs: f64,
    /// Peak resident memory in bytes during execution, if harvested.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub peak_memory_bytes: Option<u64>,
    /// Captured child standard output.
    pub stdout: String,
    /// Captured child standard error.
    pub stderr: String,
    /// Whether execution timed out.
    pub timed_out: bool,
}

/// Single `--test-dir` case outcome inside [`TestSuiteReport`].
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TestCaseReport {
    /// Input file name (e.g. `1.in`).
    pub name: String,
    /// Whether the testcase passed.
    pub passed: bool,
    /// Elapsed wall-clock duration in seconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_secs: Option<f64>,
    /// Peak resident memory in bytes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub peak_memory_bytes: Option<u64>,
    /// Expected output content.
    pub expected: String,
    /// Captured actual output content.
    pub actual: String,
}

/// Aggregated `--test-dir` suite report inside [`JsonReport`].
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TestSuiteReport {
    /// Total number of executed test cases.
    pub total: usize,
    /// Number of passing test cases.
    pub passed: usize,
    /// Number of failing test cases.
    pub failed: usize,
    /// Per-case results.
    pub cases: Vec<TestCaseReport>,
}

/// Differential fuzzing (`--stress`) summary inside [`JsonReport`].
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct StressReport {
    /// Total requested iterations.
    pub runs: usize,
    /// Starting seed.
    pub start_seed: u64,
    /// Whether all iterations passed.
    pub passed: bool,
    /// 1-based failing iteration index, if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failed_iteration: Option<usize>,
    /// Failing seed, if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failed_seed: Option<u64>,
    /// Saved `fail.in` path, if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fail_in: Option<String>,
    /// Saved `expected.out` path, if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expected_out: Option<String>,
    /// Saved `actual.out` path, if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub actual_out: Option<String>,
}

/// Top-level machine-readable execution report emitted by `run --json`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct JsonReport {
    /// Report schema version (currently `1`).
    pub schema_version: u32,
    /// `true` if the command completed with exit code `0`.
    pub ok: bool,
    /// Process exit status code.
    pub exit_code: i32,
    /// Execution mode (`"run"`, `"test"`, `"bench"`, `"stress"`, `"task"`, `"project"`).
    pub mode: String,
    /// Target files or commands passed to `run`.
    pub targets: Vec<String>,
    /// Compilation telemetry when a build step occurred.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compile: Option<CompileReport>,
    /// Runtime telemetry and captured output for single/command/task runs.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run: Option<RunReport>,
    /// Unified diff lines when `--expect` or `--stress` detected a mismatch.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub diff: Option<Vec<String>>,
    /// Batch testcase suite report when `--test-dir` ran.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tests: Option<TestSuiteReport>,
    /// Micro-benchmark statistics when `--bench` ran.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bench: Option<BenchStats>,
    /// Differential fuzzing summary when `--stress` ran.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stress: Option<StressReport>,
    /// Warning messages emitted during execution.
    pub warnings: Vec<String>,
    /// Error messages emitted during execution.
    pub errors: Vec<String>,
}

impl JsonReport {
    /// Assemble a [`JsonReport`] from captured [`UiEvent`]s and final exit status.
    pub fn from_events(
        mode: impl Into<String>,
        targets: Vec<String>,
        exit_code: i32,
        events: Vec<UiEvent>,
    ) -> Self {
        let mut compile: Option<CompileReport> = None;
        let mut run_report: Option<RunReport> = None;
        let mut diff: Option<Vec<String>> = None;
        let mut cases: Vec<TestCaseReport> = Vec::new();
        let mut bench: Option<BenchStats> = None;
        let mut stress: Option<StressReport> = None;
        let mut warnings: Vec<String> = Vec::new();
        let mut errors: Vec<String> = Vec::new();
        let mut stderr_buf = String::new();
        let mut loose_stdout_lines: Vec<String> = Vec::new();

        for ev in events {
            match ev {
                UiEvent::Log { tag, message, .. } => match tag.as_str() {
                    "ERROR" | "CRIT" => errors.push(message),
                    "WARN" => warnings.push(message),
                    "INFO" if message.starts_with("Using cached binary:") => {
                        compile = Some(CompileReport {
                            duration_secs: 0.0,
                            peak_memory_bytes: None,
                            cached: true,
                        });
                    }
                    _ => {}
                },
                UiEvent::OutputLine(line) => loose_stdout_lines.push(line),
                UiEvent::Stderr(text) => {
                    if !text.is_empty() {
                        stderr_buf.push_str(&text);
                    }
                }
                UiEvent::Diff { lines, .. } => diff = Some(lines),
                UiEvent::Metrics { .. } => {}
                UiEvent::CompileResult {
                    duration_secs,
                    memory_bytes,
                    cached,
                } => {
                    compile = Some(CompileReport {
                        duration_secs,
                        peak_memory_bytes: memory_bytes,
                        cached,
                    });
                }
                UiEvent::RunResult {
                    duration_secs,
                    memory_bytes,
                    stdout,
                    timed_out,
                } => {
                    run_report = Some(RunReport {
                        duration_secs,
                        peak_memory_bytes: memory_bytes,
                        stdout,
                        stderr: String::new(),
                        timed_out,
                    });
                }
                UiEvent::TestCaseResult {
                    name,
                    passed,
                    elapsed_secs,
                    memory_bytes,
                    actual_output,
                    expected_output,
                } => {
                    cases.push(TestCaseReport {
                        name,
                        passed,
                        duration_secs: elapsed_secs,
                        peak_memory_bytes: memory_bytes,
                        expected: expected_output,
                        actual: actual_output,
                    });
                }
                UiEvent::BenchSummary(stats) => bench = Some(stats),
                UiEvent::StressResult {
                    runs,
                    start_seed,
                    passed,
                    failed_iteration,
                    failed_seed,
                    fail_in,
                    expected_out,
                    actual_out,
                } => {
                    stress = Some(StressReport {
                        runs,
                        start_seed,
                        passed,
                        failed_iteration,
                        failed_seed,
                        fail_in,
                        expected_out,
                        actual_out,
                    });
                }
            }
        }

        if let Some(ref mut r) = run_report {
            if r.stderr.is_empty() && !stderr_buf.is_empty() {
                r.stderr = stderr_buf;
            }
        } else if !stderr_buf.is_empty() || !loose_stdout_lines.is_empty() {
            let stdout = if loose_stdout_lines.is_empty() {
                String::new()
            } else {
                format!("{}\n", loose_stdout_lines.join("\n"))
            };
            run_report = Some(RunReport {
                duration_secs: 0.0,
                peak_memory_bytes: None,
                stdout,
                stderr: stderr_buf,
                timed_out: false,
            });
        }

        let tests = if cases.is_empty() {
            None
        } else {
            let total = cases.len();
            let passed = cases.iter().filter(|c| c.passed).count();
            Some(TestSuiteReport {
                total,
                passed,
                failed: total - passed,
                cases,
            })
        };

        Self {
            schema_version: 1,
            ok: exit_code == 0,
            exit_code,
            mode: mode.into(),
            targets,
            compile,
            run: run_report,
            diff,
            tests,
            bench,
            stress,
            warnings,
            errors,
        }
    }

    /// Return a Draft 2020-12 JSON Schema describing [`JsonReport`] for `--json-schema`.
    pub fn json_schema() -> serde_json::Value {
        serde_json::json!({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "RunJsonReport",
            "description": "Machine-readable execution report emitted by `run --json`.",
            "type": "object",
            "required": ["schema_version", "ok", "exit_code", "mode", "targets", "warnings", "errors"],
            "properties": {
                "schema_version": { "type": "integer", "const": 1 },
                "ok": { "type": "boolean" },
                "exit_code": { "type": "integer" },
                "mode": { "type": "string", "enum": ["run", "test", "bench", "stress", "task", "project"] },
                "targets": { "type": "array", "items": { "type": "string" } },
                "compile": {
                    "type": "object",
                    "properties": {
                        "duration_secs": { "type": "number" },
                        "peak_memory_bytes": { "type": "integer" },
                        "cached": { "type": "boolean" }
                    }
                },
                "run": {
                    "type": "object",
                    "properties": {
                        "duration_secs": { "type": "number" },
                        "peak_memory_bytes": { "type": "integer" },
                        "stdout": { "type": "string" },
                        "stderr": { "type": "string" },
                        "timed_out": { "type": "boolean" }
                    }
                },
                "diff": { "type": "array", "items": { "type": "string" } },
                "tests": {
                    "type": "object",
                    "properties": {
                        "total": { "type": "integer" },
                        "passed": { "type": "integer" },
                        "failed": { "type": "integer" },
                        "cases": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": { "type": "string" },
                                    "passed": { "type": "boolean" },
                                    "duration_secs": { "type": "number" },
                                    "peak_memory_bytes": { "type": "integer" },
                                    "expected": { "type": "string" },
                                    "actual": { "type": "string" }
                                }
                            }
                        }
                    }
                },
                "bench": {
                    "type": "object",
                    "properties": {
                        "runs": { "type": "integer" },
                        "warmup": { "type": "integer" },
                        "min_secs": { "type": "number" },
                        "median_secs": { "type": "number" },
                        "mean_secs": { "type": "number" },
                        "p95_secs": { "type": "number" },
                        "max_secs": { "type": "number" },
                        "peak_memory_bytes": { "type": "integer" }
                    }
                },
                "stress": {
                    "type": "object",
                    "properties": {
                        "runs": { "type": "integer" },
                        "start_seed": { "type": "integer" },
                        "passed": { "type": "boolean" },
                        "failed_iteration": { "type": "integer" },
                        "failed_seed": { "type": "integer" },
                        "fail_in": { "type": "string" },
                        "expected_out": { "type": "string" },
                        "actual_out": { "type": "string" }
                    }
                },
                "warnings": { "type": "array", "items": { "type": "string" } },
                "errors": { "type": "array", "items": { "type": "string" } }
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::output::TagColor;

    /// Verify `JsonReport::from_events` aggregates compile, run, stderr, warnings, and errors cleanly.
    #[test]
    fn test_json_report_from_events_and_schema() {
        let events = vec![
            UiEvent::CompileResult {
                duration_secs: 0.12,
                memory_bytes: Some(8_000_000),
                cached: false,
            },
            UiEvent::RunResult {
                duration_secs: 0.005,
                memory_bytes: Some(2_000_000),
                stdout: "hello\n".to_string(),
                timed_out: false,
            },
            UiEvent::Stderr("note\n".to_string()),
            UiEvent::Log {
                tag: "WARN".to_string(),
                message: "be careful".to_string(),
                color: TagColor::Yellow,
            },
        ];
        let report = JsonReport::from_events("run", vec!["main.c".to_string()], 0, events);
        assert_eq!(report.schema_version, 1);
        assert!(report.ok);
        assert_eq!(report.run.as_ref().unwrap().stdout, "hello\n");
        assert_eq!(report.run.as_ref().unwrap().stderr, "note\n");
        assert_eq!(report.warnings, vec!["be careful"]);

        let schema = JsonReport::json_schema();
        assert_eq!(schema["title"], "RunJsonReport");
        assert!(schema["properties"]["tests"].is_object());
    }
}
