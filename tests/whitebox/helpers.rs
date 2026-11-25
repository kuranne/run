//! Shared `mockall` mocks and temporary workspace helpers for white-box tests.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use mockall::mock;

use run::model::error::RunError;
use run::model::options::{CommandExecutor, ExecOutcome, ExecSpec};
use run::model::sandbox::{ExecOutput, HostOs, SandboxEnv};

mock! {
    #[derive(Debug)]
    pub CmdExec {}
    impl CommandExecutor for CmdExec {
        fn execute(&self, spec: &ExecSpec) -> Result<ExecOutcome, RunError>;
    }
}

mock! {
    pub SbEnv {}
    impl SandboxEnv for SbEnv {
        fn os(&self) -> HostOs;
        fn which(&self, bin: &str) -> Option<PathBuf>;
        fn cwd(&self) -> Result<PathBuf, RunError>;
        fn uid_gid(&self) -> Option<(u32, u32)>;
        fn current_pid(&self) -> u32;
        fn is_pid_alive(&self, pid: u32) -> bool;
        fn xdg_cache_home(&self) -> Option<PathBuf>;
        fn home_dir(&self) -> Option<PathBuf>;
        fn run_capture(
            &self,
            program: &str,
            args: &[String],
            timeout: Option<Duration>,
        ) -> Result<ExecOutput, RunError>;
        fn warn(&self, msg: &str);
    }
}

/// Monotonic counter preventing temp workspace path collisions across parallel tests.
static SEQ: AtomicU64 = AtomicU64::new(1);

/// RAII temporary workspace directory removed automatically on drop.
pub struct TempWorkspace(PathBuf);

impl TempWorkspace {
    pub fn new(label: &str) -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "run_wb_integ_{label}_{}_{}_{}",
            std::process::id(),
            nanos,
            seq
        ));
        fs::create_dir_all(&path).expect("create temp workspace");
        Self(path)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempWorkspace {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub fn ok_outcome(stdout: &str) -> Result<ExecOutcome, RunError> {
    Ok(ExecOutcome {
        captured_stdout: stdout.to_string(),
        elapsed_secs: 0.01,
        memory_bytes: Some(2 * 1024 * 1024),
    })
}
