//! Python execution handler with `.venv`/`.env` auto-detection and UID ownership checks.
//!
//! Adapted from Python's `src/runner/python_handler.py`. Uses `libc::getuid` and
//! `std::os::unix::fs::MetadataExt::uid` on Unix to prevent executing an
//! untrusted virtualenv owned by another user unless `--force` is supplied, and
//! locates fallback system interpreters via [`crate::util::sandbox::SystemSandboxEnv`].

use std::fs;
use std::path::Path;

use crate::runner::handler::{
    dotted_ext_lower, normalize_leading_dash, ExecutionContext, LanguageHandler, RunnerOptions,
};
use crate::util::config::Config;
use crate::util::env::PackageMetadata;
use crate::model::error::RunError;
use crate::util::output::Printer;
use crate::util::sandbox::{SandboxEnv, SystemSandboxEnv};

/// Handler for Python (`.py`) scripts supporting `.venv`/`.env` detection and `--debug` (`pdb`).
#[derive(Debug, Clone, Copy, Default)]
pub struct PythonHandler;

impl PythonHandler {
    /// Pure helper evaluating virtualenv ownership against the active user UID.
    ///
    /// Returns `true` if the virtualenv binary should be used, emitting a `[ WARN ]`
    /// message when `owner_uid != current_uid`.
    pub fn check_venv_ownership(
        venv_name: &str,
        current_uid: u32,
        owner_uid: u32,
        force: bool,
        printer: &Printer,
    ) -> bool {
        if owner_uid == current_uid {
            return true;
        }
        if force {
            printer.warning(&format!(
                "Using venv '{venv_name}' with non-matching owner UID {owner_uid} due to --force"
            ));
            true
        } else {
            printer.warning(&format!(
                "Skipping venv '{venv_name}': python binary owned by UID {owner_uid} (current UID is {current_uid}). Use -f / --force to override."
            ));
            false
        }
    }

    /// Locate the Python interpreter binary inside `cwd`, honoring `--compiler`,
    /// `--sandbox`, `.venv`/`.env` ownership checks, and `PackageMetadata::default_runners.python`.
    pub fn get_python_executable_in(
        cwd: &Path,
        options: &RunnerOptions,
        config: &Config,
        printer: &Printer,
    ) -> Result<String, RunError> {
        if let Some(ref override_bin) = options.compiler {
            return Ok(override_bin.clone());
        }
        if let Some(configured) = config.runners.get("python") {
            return Ok(configured.clone());
        }

        let meta = PackageMetadata::get();
        if !options.sandbox {
            for venv in &meta.extensions.python_venv_dirs {
                let venv_path = cwd.join(venv);
                if !venv_path.is_dir() {
                    continue;
                }
                let py_path = if cfg!(windows) {
                    venv_path.join("Scripts").join("python.exe")
                } else {
                    venv_path.join("bin").join("python")
                };
                if !py_path.exists() {
                    continue;
                }

                #[cfg(unix)]
                {
                    use std::os::unix::fs::MetadataExt;
                    let Ok(metadata) = fs::metadata(&py_path) else {
                        continue;
                    };
                    // SAFETY: `libc::getuid` reads the real UID of the current process and cannot fail.
                    let current_uid = unsafe { libc::getuid() };
                    let owner_uid = metadata.uid();
                    if !Self::check_venv_ownership(
                        venv,
                        current_uid,
                        owner_uid,
                        options.force,
                        printer,
                    ) {
                        continue;
                    }
                }

                printer.info(&format!("Using venv: {venv}"));
                return Ok(py_path.to_string_lossy().into_owned());
            }
        }

        if options.sandbox {
            return Ok("python3".to_string());
        }

        for candidate in &meta.default_runners.python {
            if let Some(resolved) = SystemSandboxEnv.which(candidate) {
                return Ok(resolved.to_string_lossy().into_owned());
            }
        }

        Err(RunError::config("Not found python runtime path."))
    }
}

impl LanguageHandler for PythonHandler {
    /// Return `"PythonHandler"`.
    fn name(&self) -> &'static str {
        "PythonHandler"
    }

    /// Match `.py` source files.
    fn can_handle(&self, path: &Path, _config: &Config) -> bool {
        dotted_ext_lower(path) == ".py"
    }

    /// Execute a Python script (using `-m pdb` when `--debug` is set).
    fn run_single(&self, fp: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let cwd = std::env::current_dir().unwrap_or_else(|_| Path::new(".").to_path_buf());
        let prog =
            match Self::get_python_executable_in(&cwd, &ctx.runner.options, ctx.config, ctx.printer)
            {
                Ok(p) => p,
                Err(err) => {
                    ctx.printer.error(&err.to_string());
                    return Ok(false);
                }
            };

        let target = normalize_leading_dash(fp);
        let mut cmd = if ctx.runner.options.debug {
            vec![prog, "-m".to_string(), "pdb".to_string(), "--".to_string(), target]
        } else {
            vec![prog, "--".to_string(), target]
        };
        cmd.extend(ctx.runner.run_args.iter().cloned());
        ctx.run_command(&cmd, false, false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::output::{Colors, Verbosity};
    use crate::runner::test_support::TempDir;

    /// Port of `dev:tests/test_security.py::test_python_handler_venv_ownership_verification`.
    #[test]
    fn test_venv_ownership_check_logic() {
        let p = Printer::with_colors(Verbosity::Info, Colors::new(false));
        assert!(PythonHandler::check_venv_ownership(".venv", 1000, 1000, false, &p));
        assert!(!PythonHandler::check_venv_ownership(".venv", 1000, 99999, false, &p));
        assert!(PythonHandler::check_venv_ownership(".venv", 1000, 99999, true, &p));
    }

    /// Verify `.venv` owned by the current user is selected automatically.
    #[test]
    fn test_get_python_executable_detects_local_venv() {
        let tmp = TempDir::new().expect("tempdir");
        let py_bin = if cfg!(windows) {
            tmp.path().join(".venv").join("Scripts").join("python.exe")
        } else {
            tmp.path().join(".venv").join("bin").join("python")
        };
        fs::create_dir_all(py_bin.parent().unwrap()).expect("mkdir venv bin");
        fs::write(&py_bin, "#!/bin/sh\nexit 0\n").expect("write dummy python");

        let cfg = Config::default();
        let p = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let resolved =
            PythonHandler::get_python_executable_in(tmp.path(), &RunnerOptions::default(), &cfg, &p)
                .expect("should find local .venv python");
        assert_eq!(Path::new(&resolved), py_bin);
    }

    /// Verify `--sandbox` bypasses host `.venv` resolution and uses `"python3"`.
    #[test]
    fn test_get_python_executable_sandbox_uses_python3() {
        let tmp = TempDir::new().expect("tempdir");
        let cfg = Config::default();
        let p = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let opts = RunnerOptions {
            sandbox: true,
            ..Default::default()
        };
        let resolved =
            PythonHandler::get_python_executable_in(tmp.path(), &opts, &cfg, &p).expect("sandbox py");
        assert_eq!(resolved, "python3");
    }
}
