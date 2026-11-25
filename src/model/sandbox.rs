//! Sandbox environment traits and OS/output models.

use std::path::PathBuf;
use std::time::Duration;

use crate::model::error::RunError;
use crate::util::output::Printer;

/// Host operating system classification used by [`SandboxEnv`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostOs {
    /// Linux host (supports Bubblewrap native restriction).
    Linux,
    /// macOS host (fails closed for `--restrict`; uses `--sandbox`).
    MacOs,
    /// Windows host (fails closed for `--restrict`; uses `--sandbox`).
    Windows,
    /// Other Unix/POSIX host.
    Other,
}

impl HostOs {
    /// Return the compile-time [`HostOs`] for the active build target.
    pub const fn current() -> Self {
        #[cfg(target_os = "linux")]
        {
            Self::Linux
        }
        #[cfg(target_os = "macos")]
        {
            Self::MacOs
        }
        #[cfg(target_os = "windows")]
        {
            Self::Windows
        }
        #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
        {
            Self::Other
        }
    }
}

/// Output captured from a sandbox helper command (e.g., `docker info`, `docker build`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecOutput {
    /// Process exit status code (non-zero on failure or signal).
    pub code: i32,
    /// Captured UTF-8 lossy standard output.
    pub stdout: String,
    /// Captured UTF-8 lossy standard error.
    pub stderr: String,
}

/// Platform and subprocess abstraction enabling deterministic unit tests without spawning Docker.
#[cfg_attr(test, mockall::automock)]
pub trait SandboxEnv {
    /// Return the active host operating system.
    fn os(&self) -> HostOs;
    /// Locate an executable binary on `PATH`.
    fn which(&self, bin: &str) -> Option<PathBuf>;
    /// Return the current working directory.
    fn cwd(&self) -> Result<PathBuf, RunError>;
    /// Return `(uid, gid)` on POSIX platforms, or `None` on Windows.
    fn uid_gid(&self) -> Option<(u32, u32)>;
    /// Return the current process ID.
    fn current_pid(&self) -> u32;
    /// Return `true` if `pid` corresponds to a live OS process.
    fn is_pid_alive(&self, pid: u32) -> bool;
    /// Return `$XDG_CACHE_HOME` if set and non-empty.
    fn xdg_cache_home(&self) -> Option<PathBuf>;
    /// Return the user home directory (`$HOME` or `%USERPROFILE%`).
    fn home_dir(&self) -> Option<PathBuf>;
    /// Execute `program` with `args` and optional timeout, capturing exit code and output.
    fn run_capture(
        &self,
        program: &str,
        args: &[String],
        timeout: Option<Duration>,
    ) -> Result<ExecOutput, RunError>;
    /// Emit a sandbox warning message (defaults to [`Printer::global`]).
    fn warn(&self, msg: &str) {
        Printer::global().warning(msg);
    }
}
