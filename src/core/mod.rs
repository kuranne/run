//! Core execution engines, language handlers, registry, and project/task/test runners.
//!
//! Organizes compilation and runtime execution around [`LanguageHandler`],
//! [`HandlerRegistry`], [`ExecutionContext`], [`BaseRunner`], and
//! [`CompilerRunner`].

pub mod base;
pub mod bench;
pub mod engine;
pub mod handlers;
pub mod project;
pub mod registry;
pub mod test_runner;

pub use base::{BaseRunner, SystemCommandExecutor};
pub use bench::BenchRunner;
pub use crate::model::options::{CommandExecutor, ExecOutcome, ExecSpec, RunnerOptions};
pub use engine::CompilerRunner;
pub use project::{split_compound_command, ProjectRunner, TaskRunner};
pub use registry::{ExecutionContext, HandlerRegistry, LanguageHandler};
pub use test_runner::TestcasesRunner;

/// Minimal RAII temporary directory helper for `#[cfg(test)]` modules (avoids adding `tempfile` crate).
#[cfg(test)]
pub(crate) mod test_support {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Monotonic sequence counter preventing collisions across parallel tests.
    static SEQ: AtomicU64 = AtomicU64::new(1);

    /// RAII temporary directory automatically deleted on drop.
    pub struct TempDir {
        /// Path to the created temporary directory.
        path: PathBuf,
    }

    impl TempDir {
        /// Create a uniquely named directory under `std::env::temp_dir()`.
        pub fn new() -> std::io::Result<Self> {
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos());
            let seq = SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "run_core_test_{}_{}_{}",
                std::process::id(),
                nanos,
                seq
            ));
            fs::create_dir_all(&path)?;
            Ok(Self { path })
        }

        /// Return the path to this temporary directory.
        pub fn path(&self) -> &Path {
            &self.path
        }
    }

    impl Drop for TempDir {
        /// Remove the temporary directory recursively on drop.
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}
