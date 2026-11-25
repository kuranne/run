//! Unified error types for the `run` library and CLI.
//!
//! Adapted from Python's `src/util/errors.py` hierarchy (`RunError`,
//! `ConfigError`, `CompilationError`, `ExecutionError`), modeling each error
//! category as an enum variant plus typed `std::io::Error` wrapping.

use std::error::Error;
use std::fmt;

/// Unified error type returned by `run` library operations and propagated to the CLI.
#[derive(Debug)]
pub enum RunError {
    /// Configuration, validation, security policy, or CLI argument errors.
    Config(String),
    /// Compiler or build-step failures and compilation timeouts.
    Compilation(String),
    /// Runtime execution failures, missing binaries, or execution timeouts.
    Execution(String),
    /// Underlying filesystem or process I/O errors.
    Io(std::io::Error),
}

impl RunError {
    /// Create a [`RunError::Config`] from any string-like message.
    pub fn config(msg: impl Into<String>) -> Self {
        Self::Config(msg.into())
    }

    /// Create a [`RunError::Compilation`] from any string-like message.
    pub fn compilation(msg: impl Into<String>) -> Self {
        Self::Compilation(msg.into())
    }

    /// Create a [`RunError::Execution`] from any string-like message.
    pub fn execution(msg: impl Into<String>) -> Self {
        Self::Execution(msg.into())
    }
}

impl fmt::Display for RunError {
    /// Format the underlying error message verbatim to preserve CLI parity with Python `RunError`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Config(msg) | Self::Compilation(msg) | Self::Execution(msg) => {
                f.write_str(msg)
            }
            Self::Io(err) => fmt::Display::fmt(err, f),
        }
    }
}

impl Error for RunError {
    /// Expose the wrapped [`std::io::Error`] as the error source when present.
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(err) => Some(err),
            Self::Config(_) | Self::Compilation(_) | Self::Execution(_) => None,
        }
    }
}

impl From<std::io::Error> for RunError {
    /// Convert a standard I/O error into [`RunError::Io`].
    fn from(err: std::io::Error) -> Self {
        Self::Io(err)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verify Display outputs the inner message verbatim for string variants.
    #[test]
    fn test_display_variants() {
        assert_eq!(RunError::config("bad config").to_string(), "bad config");
        assert_eq!(
            RunError::compilation("compile failed").to_string(),
            "compile failed"
        );
        assert_eq!(RunError::execution("exec failed").to_string(), "exec failed");
    }

    /// Verify `From<std::io::Error>` and `Error::source()` behavior.
    #[test]
    fn test_io_conversion_and_source() {
        let io_err = std::io::Error::new(std::io::ErrorKind::NotFound, "missing file");
        let run_err = RunError::from(io_err);
        assert!(matches!(run_err, RunError::Io(_)));
        assert!(run_err.to_string().contains("missing file"));
        assert!(run_err.source().is_some());

        let cfg_err = RunError::config("cfg");
        assert!(cfg_err.source().is_none());
    }
}
