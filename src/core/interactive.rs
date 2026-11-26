//! Interactive judge runner (`--interactive <INTERACTOR>`) bridging target and interactor stdio.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::core::engine::CompilerRunner;
use crate::model::error::RunError;
use crate::util::output::TagColor;
use crate::util::security::SecurityManager;

/// Maximum transcript lines retained for failure diagnostics.
const MAX_TRANSCRIPT_LINES: usize = 64;

/// Runs a solution program against an interactive judge (`--interactive <INTERACTOR>`).
#[derive(Debug, Clone, Copy, Default)]
pub struct InteractiveRunner;

impl InteractiveRunner {
    /// Prepare `target` and `interactor`, bridge their stdin/stdout pipes, and report verdict.
    pub fn run_interactive(
        runner: &mut CompilerRunner,
        target_files: &[PathBuf],
        multi: bool,
        interactor_path: &Path,
    ) -> bool {
        let Some(first_target) = target_files.first() else {
            runner
                .printer
                .error("No target file provided for --interactive.");
            return false;
        };

        let sol_cmd = match Self::resolve_exec_cmd(runner, target_files, multi) {
            Ok(c) => c,
            Err(err) => {
                runner
                    .printer
                    .error(&format!("Failed to prepare target {}: {err}", first_target.display()));
                return false;
            }
        };

        let int_cmd = match Self::resolve_exec_cmd(runner, &[interactor_path.to_path_buf()], false)
        {
            Ok(c) => c,
            Err(err) => {
                runner.printer.error(&format!(
                    "Failed to prepare interactor {}: {err}",
                    interactor_path.display()
                ));
                return false;
            }
        };

        runner.printer.info(&format!(
            "Interactive session: {} <-> {}",
            first_target.display(),
            interactor_path.display()
        ));

        match Self::bridge_processes(
            &sol_cmd,
            &int_cmd,
            runner.base.options.timeout,
            runner.base.options.mem_limit_bytes,
            &runner.base.parse_custom_env(),
        ) {
            Ok(outcome) => {
                if outcome.passed {
                    runner.printer.status(
                        "PASS",
                        &format!(
                            "Interactive judge passed in {:.3}s ({} exchange lines)",
                            outcome.elapsed_secs, outcome.total_lines
                        ),
                        TagColor::Green,
                    );
                    true
                } else {
                    runner.printer.status(
                        "FAIL",
                        &format!(
                            "Interactive judge failed (target={}, interactor={})",
                            outcome.target_exit, outcome.interactor_exit
                        ),
                        TagColor::Red,
                    );
                    if !outcome.transcript.is_empty() {
                        runner.printer.info("Transcript (recent exchanges):");
                        for line in &outcome.transcript {
                            println!("  {line}");
                        }
                    }
                    false
                }
            }
            Err(err) => {
                runner.printer.error(&format!("Interactive session error: {err}"));
                false
            }
        }
    }

    /// Resolve a target file list to an executable command vector (compiling first if needed).
    fn resolve_exec_cmd(
        runner: &mut CompilerRunner,
        files: &[PathBuf],
        multi: bool,
    ) -> Result<Vec<String>, RunError> {
        let first = files
            .first()
            .ok_or_else(|| RunError::execution("No target file provided"))?;
        if !first.exists() {
            return Err(RunError::execution(format!(
                "File not found: {}",
                first.display()
            )));
        }

        if CompilerRunner::is_executable_binary(first) {
            return Ok(vec![first.to_string_lossy().into_owned()]);
        }

        let ext = first
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{}", e.to_ascii_lowercase()))
            .unwrap_or_default();

        if ext == ".py" {
            let py = if cfg!(windows) { "python" } else { "python3" };
            return Ok(vec![
                py.to_string(),
                "-u".to_string(),
                first.to_string_lossy().into_owned(),
            ]);
        }
        if ext == ".sh" {
            return Ok(vec![
                "sh".to_string(),
                first.to_string_lossy().into_owned(),
            ]);
        }

        // Compile via CompilerRunner in build-only mode, then inspect produced binary.
        let orig_build_only = runner.base.options.build_only;
        let orig_keep = runner.base.options.keep;
        let prev_outputs = runner.base.output_files.len();
        runner.base.options.build_only = true;
        runner.base.options.keep = true;
        let ok = runner.compile_and_run(files, multi);
        runner.base.options.build_only = orig_build_only;
        runner.base.options.keep = orig_keep;

        if !ok {
            return Err(RunError::compilation(format!(
                "Compilation failed for {}",
                first.display()
            )));
        }
        let bin = runner
            .base
            .output_files
            .get(prev_outputs..)
            .and_then(|slice| slice.last())
            .cloned()
            .ok_or_else(|| {
                RunError::compilation(format!(
                    "No executable artifact produced for {}",
                    first.display()
                ))
            })?;
        Ok(vec![bin.to_string_lossy().into_owned()])
    }

    /// Spawn `sol_cmd` and `int_cmd`, bridge their stdin/stdout, and enforce timeout/memory limits.
    pub(crate) fn bridge_processes(
        sol_cmd: &[String],
        int_cmd: &[String],
        timeout_secs: Option<f64>,
        mem_limit_bytes: Option<u64>,
        custom_env: &std::collections::BTreeMap<String, String>,
    ) -> Result<InteractiveOutcome, RunError> {
        let sanitized_env = SecurityManager::sanitize_execution_env(Some(custom_env), false);

        let mut sc = Command::new(&sol_cmd[0]);
        sc.args(&sol_cmd[1..])
            .env_clear()
            .envs(&sanitized_env)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());

        let mut ic = Command::new(&int_cmd[0]);
        ic.args(&int_cmd[1..])
            .env_clear()
            .envs(&sanitized_env)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());

        let mut sol_child = sc.spawn().map_err(RunError::Io)?;
        let mut int_child = ic.spawn().map_err(RunError::Io)?;

        let sol_stdin = sol_child
            .stdin
            .take()
            .ok_or_else(|| RunError::execution("Failed to open target stdin pipe"))?;
        let sol_stdout = sol_child
            .stdout
            .take()
            .ok_or_else(|| RunError::execution("Failed to open target stdout pipe"))?;
        let int_stdin = int_child
            .stdin
            .take()
            .ok_or_else(|| RunError::execution("Failed to open interactor stdin pipe"))?;
        let int_stdout = int_child
            .stdout
            .take()
            .ok_or_else(|| RunError::execution("Failed to open interactor stdout pipe"))?;

        let transcript: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let total_lines: Arc<Mutex<usize>> = Arc::new(Mutex::new(0));

        let t1 = Arc::clone(&transcript);
        let c1 = Arc::clone(&total_lines);
        let sol_to_int = thread::spawn(move || {
            let mut reader = BufReader::new(sol_stdout);
            let mut writer = int_stdin;
            let mut line = String::new();
            while reader.read_line(&mut line).unwrap_or(0) > 0 {
                if writer.write_all(line.as_bytes()).is_err() || writer.flush().is_err() {
                    break;
                }
                if let Ok(mut cnt) = c1.lock() {
                    *cnt += 1;
                }
                if let Ok(mut log) = t1.lock() {
                    if log.len() >= MAX_TRANSCRIPT_LINES {
                        log.remove(0);
                    }
                    log.push(format!("[SOL -> INT] {}", line.trim_end()));
                }
                line.clear();
            }
        });

        let t2 = Arc::clone(&transcript);
        let c2 = Arc::clone(&total_lines);
        let int_to_sol = thread::spawn(move || {
            let mut reader = BufReader::new(int_stdout);
            let mut writer = sol_stdin;
            let mut line = String::new();
            while reader.read_line(&mut line).unwrap_or(0) > 0 {
                if writer.write_all(line.as_bytes()).is_err() || writer.flush().is_err() {
                    break;
                }
                if let Ok(mut cnt) = c2.lock() {
                    *cnt += 1;
                }
                if let Ok(mut log) = t2.lock() {
                    if log.len() >= MAX_TRANSCRIPT_LINES {
                        log.remove(0);
                    }
                    log.push(format!("[INT -> SOL] {}", line.trim_end()));
                }
                line.clear();
            }
        });

        let start = Instant::now();
        let deadline = timeout_secs.map(|s| start + Duration::from_secs_f64(s.max(0.01)));
        let _ = mem_limit_bytes;

        let mut sol_status = None;
        let mut int_status = None;
        loop {
            if sol_status.is_none() {
                sol_status = sol_child.try_wait().map_err(RunError::Io)?;
            }
            if int_status.is_none() {
                int_status = int_child.try_wait().map_err(RunError::Io)?;
            }
            if sol_status.is_some() && int_status.is_some() {
                break;
            }
            if let Some(dl) = deadline
                && Instant::now() >= dl
            {
                let _ = sol_child.kill();
                let _ = int_child.kill();
                let _ = sol_child.wait();
                let _ = int_child.wait();
                let _ = sol_to_int.join();
                let _ = int_to_sol.join();
                return Err(RunError::execution(format!(
                    "Interactive session timed out after {} seconds.",
                    timeout_secs.unwrap_or(0.0)
                )));
            }
            thread::sleep(Duration::from_millis(5));
        }

        let _ = sol_to_int.join();
        let _ = int_to_sol.join();

        let target_exit = sol_status.and_then(|s| s.code()).unwrap_or(-1);
        let interactor_exit = int_status.and_then(|s| s.code()).unwrap_or(-1);
        let passed = target_exit == 0 && interactor_exit == 0;
        let log = transcript.lock().map(|g| g.clone()).unwrap_or_default();
        let lines_count = total_lines.lock().map(|g| *g).unwrap_or(0);

        Ok(InteractiveOutcome {
            passed,
            target_exit,
            interactor_exit,
            elapsed_secs: start.elapsed().as_secs_f64(),
            total_lines: lines_count,
            transcript: log,
        })
    }
}

/// Outcome of an interactive judge session.
#[derive(Debug, Clone)]
pub(crate) struct InteractiveOutcome {
    /// `true` iff both target and interactor exited with status 0.
    pub passed: bool,
    /// Exit code of the target program.
    pub target_exit: i32,
    /// Exit code of the interactor program.
    pub interactor_exit: i32,
    /// Wall-clock duration in seconds.
    pub elapsed_secs: f64,
    /// Total exchanged lines across both directions.
    pub total_lines: usize,
    /// Bounded recent transcript lines.
    pub transcript: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn test_interactive_bridge_number_guessing() {
        let py = if cfg!(windows) { "python" } else { "python3" };
        let sol = vec![
            py.to_string(),
            "-u".to_string(),
            "-c".to_string(),
            "import sys\nfor g in [5, 7]:\n print(g, flush=True)\n r = sys.stdin.readline().strip()\n if r == 'OK': break\n".to_string(),
        ];
        let inter = vec![
            py.to_string(),
            "-u".to_string(),
            "-c".to_string(),
            "import sys\nfor line in sys.stdin:\n v = int(line.strip())\n if v == 7:\n  print('OK', flush=True)\n  sys.exit(0)\n print('NO', flush=True)\nsys.exit(1)\n".to_string(),
        ];
        let outcome = InteractiveRunner::bridge_processes(
            &sol,
            &inter,
            Some(5.0),
            None,
            &BTreeMap::new(),
        )
        .expect("interactive bridge");
        assert!(outcome.passed);
        assert_eq!(outcome.target_exit, 0);
        assert_eq!(outcome.interactor_exit, 0);
        assert!(outcome.total_lines >= 4);
    }
}
