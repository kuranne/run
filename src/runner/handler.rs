//! Unified language handler trait, execution context, and priority-ordered registry.
//!
//! Adapted from Python's `src/runner/handler_interface.py` and
//! `src/runner/registry.py`. Replaces Python's 7-class multiple inheritance on
//! `CompilerRunner` with trait-based dispatch through [`LanguageHandler`] and
//! [`HandlerRegistry`], always registering [`CustomLanguageHandler`] first so
//! user `[[languages]]` rules in `Run.toml` override built-in handlers.

use std::path::{Path, PathBuf};

use crate::runner::base::BaseRunner;
use crate::runner::c_family::CFamilyHandler;
use crate::runner::custom_handler::CustomLanguageHandler;
use crate::runner::java_handler::JavaHandler;
use crate::runner::python_handler::PythonHandler;
use crate::runner::rust_handler::RustHandler;
use crate::runner::script_handler::ScriptHandler;
use crate::util::cache::CacheManager;
use crate::util::config::Config;
use crate::model::error::RunError;
use crate::util::output::Printer;

/// Prefix `-`-leading relative paths with `./` so compilers and interpreters
/// never mistake a source or script filename for a CLI option flag.
pub(crate) fn normalize_leading_dash(path: &Path) -> String {
    let s = path.to_string_lossy();
    if s.starts_with('-') {
        format!("./{s}")
    } else {
        s.into_owned()
    }
}

/// Return the lowercase dot-prefixed file extension (e.g. `".cpp"`), or `""`
/// when `path` has no extension.
pub(crate) fn dotted_ext_lower(path: &Path) -> String {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| format!(".{}", e.to_ascii_lowercase()))
        .unwrap_or_default()
}

pub use crate::model::options::RunnerOptions;

/// Shared execution context passed into [`LanguageHandler`] implementations.
pub struct ExecutionContext<'a> {
    /// Active `Run.toml` configuration.
    pub config: &'a Config,
    /// Configured tagged terminal printer.
    pub printer: &'a Printer,
    /// Optional SHA-256 build/binary cache (`None` when `--no-cache` is active).
    pub cache: Option<&'a CacheManager>,
    /// Mutable command execution engine and artifact tracker.
    pub runner: &'a mut BaseRunner,
    /// Optional extension inferred from shebang inspection for extensionless files.
    pub detected_ext: Option<String>,
}

impl ExecutionContext<'_> {
    /// Execute a command through the underlying [`BaseRunner`].
    pub fn run_command(
        &mut self,
        cmd: &[String],
        use_shell: bool,
        compiling: bool,
    ) -> Result<bool, RunError> {
        self.runner.run_command(cmd, use_shell, compiling)
    }

    /// Resolve the compiled output executable path for `source_path`.
    pub fn get_executable_path(&self, source_path: &Path) -> Result<PathBuf, RunError> {
        self.runner.get_executable_path(source_path)
    }

    /// Execute a compiled binary, honoring `--build-only`, debuggers, and Valgrind.
    pub fn execute_binary(&mut self, bin_path: &Path, args: &[String]) -> Result<bool, RunError> {
        self.runner.execute_binary(bin_path, args)
    }
}

/// Unified interface implemented by every built-in and custom language handler.
pub trait LanguageHandler: Send + Sync {
    /// Return a human-readable handler identifier for diagnostics and errors.
    fn name(&self) -> &'static str;

    /// Return `true` if this handler processes `path` under `config`.
    fn can_handle(&self, path: &Path, config: &Config) -> bool;

    /// Compile and/or execute a single source file.
    fn run_single(&self, path: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError>;

    /// Compile and/or execute multiple source files together.
    fn run_multi(
        &self,
        _paths: &[PathBuf],
        _ctx: &mut ExecutionContext<'_>,
    ) -> Result<bool, RunError> {
        Err(RunError::config(format!(
            "{} does not support multi-file compilation.",
            self.name()
        )))
    }
}

/// Priority-ordered registry selecting the first matching [`LanguageHandler`].
///
/// Registers [`CustomLanguageHandler`] first so `[[languages]]` overrides in
/// `Run.toml` (and built-in Go from `Cargo.toml`) take precedence over
/// built-in language handlers.
pub struct HandlerRegistry {
    /// Ordered list of registered handlers.
    handlers: Vec<Box<dyn LanguageHandler>>,
}

impl Default for HandlerRegistry {
    /// Construct the default registry via [`HandlerRegistry::new`].
    fn default() -> Self {
        Self::new()
    }
}

impl HandlerRegistry {
    /// Create a [`HandlerRegistry`] populated with [`CustomLanguageHandler`] first
    /// followed by all built-in language handlers.
    pub fn new() -> Self {
        let mut reg = Self::with_empty();
        reg.register(Box::new(CustomLanguageHandler));
        reg.register(Box::new(CFamilyHandler));
        reg.register(Box::new(RustHandler));
        reg.register(Box::new(JavaHandler));
        reg.register(Box::new(PythonHandler));
        reg.register(Box::new(ScriptHandler));
        reg
    }

    /// Create an empty [`HandlerRegistry`] (useful for unit tests).
    pub fn with_empty() -> Self {
        Self {
            handlers: Vec::new(),
        }
    }

    /// Append a [`LanguageHandler`] to the end of the registry.
    pub fn register(&mut self, handler: Box<dyn LanguageHandler>) {
        self.handlers.push(handler);
    }

    /// Return all registered handlers in priority order.
    pub fn handlers(&self) -> &[Box<dyn LanguageHandler>] {
        &self.handlers
    }

    /// Return the first handler whose [`LanguageHandler::can_handle`] accepts `path`.
    pub fn get_handler(&self, path: &Path, config: &Config) -> Option<&dyn LanguageHandler> {
        self.handlers
            .iter()
            .find(|h| h.can_handle(path, config))
            .map(Box::as_ref)
    }

    /// Resolve a handler for an extensionless file whose shebang yielded `detected_ext`.
    pub fn get_handler_for_ext(
        &self,
        detected_ext: &str,
        config: &Config,
    ) -> Option<&dyn LanguageHandler> {
        if detected_ext.is_empty() {
            return None;
        }
        let normalized = if detected_ext.starts_with('.') {
            detected_ext.to_string()
        } else {
            format!(".{detected_ext}")
        };
        let probe = PathBuf::from(format!("_shebang{normalized}"));
        self.get_handler(&probe, config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Dummy handler used to verify [`HandlerRegistry`] registration and matching order.
    struct MockHandler {
        /// Handler name.
        handler_name: &'static str,
        /// Target extension to match.
        ext: &'static str,
    }

    impl LanguageHandler for MockHandler {
        /// Return the mock handler name.
        fn name(&self) -> &'static str {
            self.handler_name
        }

        /// Match paths with `self.ext`.
        fn can_handle(&self, path: &Path, _config: &Config) -> bool {
            dotted_ext_lower(path) == self.ext
        }

        /// Return `Ok(true)` without executing anything.
        fn run_single(
            &self,
            _path: &Path,
            _ctx: &mut ExecutionContext<'_>,
        ) -> Result<bool, RunError> {
            Ok(true)
        }
    }

    /// Port of `dev:tests/test_registry.py::test_registry_register_and_get`.
    #[test]
    fn test_registry_register_and_get() {
        let mut reg = HandlerRegistry::with_empty();
        reg.register(Box::new(MockHandler {
            handler_name: "mock_xyz",
            ext: ".xyz",
        }));
        let cfg = Config::default();
        assert_eq!(
            reg.get_handler(Path::new("test.xyz"), &cfg)
                .map(LanguageHandler::name),
            Some("mock_xyz")
        );
        assert!(reg.get_handler(Path::new("test.abc"), &cfg).is_none());
    }

    /// Port of `dev:tests/test_registry.py::test_registry_priority_order`.
    #[test]
    fn test_registry_priority_order() {
        let cfg = Config::from_toml_str(
            "[[languages]]\nname = \"override_py\"\nextensions = [\".py\"]\nrunner = \"pypy3\"\n",
            None,
            None,
        )
        .expect("valid custom py config");

        let reg = HandlerRegistry::new();
        let h = reg
            .get_handler(Path::new("app.py"), &cfg)
            .expect("custom handler should match .py");
        assert_eq!(h.name(), "CustomLanguageHandler");

        // Without custom override, PythonHandler handles .py while Go falls back to CustomLanguageHandler.
        let default_cfg = Config::default();
        assert_eq!(
            reg.get_handler(Path::new("app.py"), &default_cfg)
                .map(LanguageHandler::name),
            Some("PythonHandler")
        );
        assert_eq!(
            reg.get_handler(Path::new("main.go"), &default_cfg)
                .map(LanguageHandler::name),
            Some("CustomLanguageHandler")
        );
    }

    /// Verify default `LanguageHandler::run_multi` rejects unsupported languages cleanly.
    #[test]
    fn test_default_run_multi_returns_config_error() {
        let handler = MockHandler {
            handler_name: "MockOnly",
            ext: ".m",
        };
        let cfg = Config::default();
        let printer = Printer::global();
        let mut base =
            BaseRunner::new_with_stdin(RunnerOptions::default(), None, None, &cfg, printer, None)
                .expect("base runner");
        let mut ctx = ExecutionContext {
            config: &cfg,
            printer: &printer,
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };
        let err = handler
            .run_multi(&[PathBuf::from("a.m"), PathBuf::from("b.m")], &mut ctx)
            .expect_err("multi compile should fail");
        assert!(err
            .to_string()
            .contains("MockOnly does not support multi-file compilation."));
    }
}
