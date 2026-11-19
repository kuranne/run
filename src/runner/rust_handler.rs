//! Rust execution handler supporting Cargo workspaces/crates and cached standalone `rustc`.
//!
//! Adapted from Python's `src/runner/rust_handler.py`. Uses `toml` (`toml::Value`)
//! to parse `Cargo.toml` package names with path-traversal validation, climbs up
//! to `PackageMetadata::get().cargo_search_depth` parent directories stopping at
//! `.git` or `stop_dirs`, and caches standalone `rustc` binaries via
//! [`crate::util::cache::CacheManager`].

use std::fs;
use std::path::{Path, PathBuf};

use crate::runner::handler::{
    dotted_ext_lower, normalize_leading_dash, ExecutionContext, LanguageHandler,
};
use crate::util::config::Config;
use crate::util::env::PackageMetadata;
use crate::util::errors::RunError;

/// Return `true` if `path` (or its canonical target) matches any boundary in `stop_dirs`.
fn is_stop_dir(path: &Path, stop_dirs: &[String]) -> bool {
    let canonical = fs::canonicalize(path).ok();
    stop_dirs.iter().any(|boundary| {
        let b = Path::new(boundary);
        path == b || canonical.as_deref() == Some(b)
    })
}

/// Handler for Rust source files (`.rs`) and Cargo crates.
#[derive(Debug, Clone, Copy, Default)]
pub struct RustHandler;

impl RustHandler {
    /// Walk upward from `start_path` (up to `cargo_search_depth` levels) looking for `Cargo.toml`,
    /// stopping at `.git`, filesystem device boundaries (Unix `st_dev`), or `stop_dirs`.
    pub fn find_cargo_toml(start_path: &Path) -> Option<PathBuf> {
        let meta = PackageMetadata::get();
        let abs = std::path::absolute(start_path).unwrap_or_else(|_| start_path.to_path_buf());
        let mut current = if start_path.is_file() || abs.is_file() {
            abs.parent()?.to_path_buf()
        } else {
            abs
        };

        #[cfg(unix)]
        let start_dev = {
            use std::os::unix::fs::MetadataExt;
            fs::metadata(&current).ok().map(|m| m.dev())
        };

        for _ in 0..meta.cargo_search_depth {
            let candidate = current.join("Cargo.toml");
            if candidate.exists() {
                return Some(candidate);
            }

            if current.join(".git").exists() || is_stop_dir(&current, &meta.stop_dirs) {
                break;
            }

            let Some(parent) = current.parent() else {
                break;
            };

            #[cfg(unix)]
            if let Some(dev_id) = start_dev {
                use std::os::unix::fs::MetadataExt;
                let same_dev = fs::metadata(parent)
                    .map(|m| m.dev() == dev_id)
                    .unwrap_or(false);
                if !same_dev {
                    break;
                }
            }

            current = parent.to_path_buf();
        }
        None
    }

    /// Parse `[package].name` from `Cargo.toml` via `toml::Value`, rejecting path traversal sequences.
    pub fn get_cargo_package_name(toml_path: &Path, ctx_printer: &crate::util::output::Printer) -> Option<String> {
        let content = match fs::read_to_string(toml_path) {
            Ok(text) => text,
            Err(err) => {
                ctx_printer.error(&format!("Failed to read {}: {err}", toml_path.display()));
                return None;
            }
        };
        let doc: toml::Value = match toml::from_str(&content) {
            Ok(v) => v,
            Err(err) => {
                ctx_printer.error(&format!("Failed to read {}: {err}", toml_path.display()));
                return None;
            }
        };
        let raw_name = doc
            .get("package")
            .and_then(|p| p.get("name"))
            .and_then(toml::Value::as_str)?;
        let pkg = raw_name.trim();
        if pkg.is_empty() || pkg.contains('/') || pkg.contains('\\') || pkg.contains("..") {
            ctx_printer.error(&format!(
                "Invalid cargo package name containing path traversal characters: '{pkg}'"
            ));
            return None;
        }
        Some(pkg.to_string())
    }

    /// Execute a Cargo project either via `cargo build` + direct binary execution (when
    /// debugging, leak-checking, or in release mode) or `cargo run -q`.
    pub fn run_cargo_mode(
        toml_path: Option<&Path>,
        ctx: &mut ExecutionContext<'_>,
    ) -> Result<bool, RunError> {
        let toml = toml_path.unwrap_or_else(|| Path::new("Cargo.toml"));
        let cargo = PackageMetadata::get().default_runners.cargo.clone();

        let is_debug = ctx.runner.options.debug
            || ctx.runner.options.gdb
            || ctx.runner.options.lldb
            || ctx.runner.options.valgrind;
        let is_release = ctx.runner.options.release;

        if is_release || is_debug {
            let mode_name = if is_release { "release" } else { "debug" };
            ctx.printer.info(&format!("Building {mode_name}..."));

            let mut build_cmd = vec![cargo, "build".to_string()];
            if is_release {
                build_cmd.push("--release".to_string());
            }
            build_cmd.extend(ctx.runner.extra_flags.iter().cloned());

            if !ctx.run_command(&build_cmd, false, true)? {
                return Ok(false);
            }

            let pkg_name = Self::get_cargo_package_name(toml, ctx.printer).ok_or_else(|| {
                RunError::execution("Could not parse package name from Cargo.toml")
            })?;
            let bin_name = if cfg!(windows) {
                format!("{pkg_name}.exe")
            } else {
                pkg_name
            };

            let proj_dir = toml
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or_else(|| Path::new("."));
            let raw_target_dir = proj_dir.join("target").join(mode_name);
            let target_dir = fs::canonicalize(&raw_target_dir).unwrap_or(raw_target_dir);
            let raw_target_bin = target_dir.join(&bin_name);
            let target_bin = fs::canonicalize(&raw_target_bin).unwrap_or(raw_target_bin);

            if !target_bin.starts_with(&target_dir) {
                let msg = format!(
                    "Target binary '{}' escapes target directory '{}'",
                    target_bin.display(),
                    target_dir.display()
                );
                ctx.printer.error(&msg);
                return Err(RunError::execution(msg));
            }

            if target_bin.exists() || ctx.runner.options.dry_run {
                return ctx.execute_binary(&target_bin, &[]);
            }
            let msg = format!("Binary not found at {}", target_bin.display());
            ctx.printer.error(&msg);
            return Err(RunError::execution(msg));
        }

        let mut cmd = vec![cargo, "run".to_string(), "-q".to_string()];
        cmd.extend(ctx.runner.extra_flags.iter().cloned());
        if !ctx.runner.run_args.is_empty() {
            cmd.push("--".to_string());
            cmd.extend(ctx.runner.run_args.iter().cloned());
        }
        ctx.run_command(&cmd, false, false)
    }
}

impl LanguageHandler for RustHandler {
    /// Return `"RustHandler"`.
    fn name(&self) -> &'static str {
        "RustHandler"
    }

    /// Match `.rs` source files.
    fn can_handle(&self, path: &Path, _config: &Config) -> bool {
        dotted_ext_lower(path) == ".rs"
    }

    /// Run a Rust file via Cargo (if `Cargo.toml` is found nearby) or standalone `rustc` with caching.
    fn run_single(&self, fp: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        if let Some(cargo_toml) = Self::find_cargo_toml(fp) {
            return Self::run_cargo_mode(Some(&cargo_toml), ctx);
        }

        let meta = PackageMetadata::get();
        let compiler = ctx
            .runner
            .options
            .compiler
            .clone()
            .unwrap_or_else(|| ctx.config.get_runner("rust", &meta.default_runners.rust));

        // Persist standalone rustc binaries in ~/.cache/run/<hash>/bin/ when no workspace
        // output (--keep, -B, --out-dir) was requested so repeated runs hit the cache.
        let use_persistent_cache_bin = ctx.cache.is_some()
            && !ctx.runner.options.keep
            && !ctx.runner.options.build_only
            && ctx.runner.options.out_dir.is_none();

        let out_name = if use_persistent_cache_bin
            && let Some(cache) = ctx.cache
        {
            cache.get_cached_binary_path(fp)
        } else {
            ctx.get_executable_path(fp)?
        };

        if let Some(cache) = ctx.cache
            && !cache.is_changed(fp, Some(&out_name))
        {
            if !ctx.runner.options.quiet {
                ctx.printer
                    .info(&format!("Using cached binary: {}", out_name.display()));
            }
            return ctx.execute_binary(&out_name, &[]);
        }

        if use_persistent_cache_bin
            && !ctx.runner.options.dry_run
            && let Some(parent) = out_name.parent()
        {
            fs::create_dir_all(parent)?;
        }

        let preset_flags = ctx
            .config
            .get_preset_flags(ctx.runner.options.preset.as_deref(), "rust");
        let mut cmd = vec![compiler];
        cmd.extend(ctx.runner.extra_flags.iter().cloned());
        cmd.extend(preset_flags);
        cmd.push(normalize_leading_dash(fp));
        cmd.push("-o".to_string());
        cmd.push(out_name.to_string_lossy().into_owned());

        if !ctx.run_command(&cmd, false, true)? {
            return Ok(false);
        }

        if let Some(cache) = ctx.cache
            && !ctx.runner.options.dry_run
        {
            cache.update_cache(fp, Some(&out_name));
        }

        if !use_persistent_cache_bin {
            ctx.runner.output_files.push(out_name.clone());
        }

        ctx.execute_binary(&out_name, &[])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::output::{Colors, Printer, Verbosity};
    use crate::runner::test_support::TempDir;

    fn test_printer() -> Printer {
        Printer::with_colors(Verbosity::Info, Colors::new(false))
    }

    /// Port of `dev:tests/test_rust.py::test_get_cargo_package_name`.
    #[test]
    fn test_get_cargo_package_name() {
        let tmp = TempDir::new().expect("tempdir");
        let toml_path = tmp.path().join("Cargo.toml");
        fs::write(
            &toml_path,
            "[package]\nname = \"my_rust_app\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
        )
        .expect("write Cargo.toml");

        assert_eq!(
            RustHandler::get_cargo_package_name(&toml_path, &test_printer()).as_deref(),
            Some("my_rust_app")
        );
    }

    /// Port of `dev:tests/test_rust.py::test_get_cargo_package_name_missing`.
    #[test]
    fn test_get_cargo_package_name_missing() {
        let tmp = TempDir::new().expect("tempdir");
        let toml_path = tmp.path().join("Cargo.toml");
        fs::write(&toml_path, "[workspace]\nmembers = [\"member_a\"]\n").expect("write workspace");
        assert!(RustHandler::get_cargo_package_name(&toml_path, &test_printer()).is_none());
    }

    /// Port of `dev:tests/test_rust.py::test_find_cargo_toml`.
    #[test]
    fn test_find_cargo_toml() {
        let tmp = TempDir::new().expect("tempdir");
        let src_dir = tmp.path().join("src");
        fs::create_dir_all(&src_dir).expect("mkdir src");
        let main_rs = src_dir.join("main.rs");
        fs::write(&main_rs, "fn main() {}\n").expect("write main.rs");
        let cargo_toml = tmp.path().join("Cargo.toml");
        fs::write(&cargo_toml, "[package]\nname = \"test\"\n").expect("write Cargo.toml");

        let found = RustHandler::find_cargo_toml(&main_rs).expect("find Cargo.toml");
        assert_eq!(
            fs::canonicalize(&found).unwrap(),
            fs::canonicalize(&cargo_toml).unwrap()
        );
    }

    /// Port of `dev:tests/test_rust.py::test_get_cargo_package_name_path_traversal_rejected`.
    #[test]
    fn test_get_cargo_package_name_path_traversal_rejected() {
        let tmp = TempDir::new().expect("tempdir");
        let toml_path = tmp.path().join("Cargo.toml");
        let p = test_printer();

        for bad_name in ["../../../../bin/sh", "foo/bar", "foo\\bar", "   "] {
            fs::write(
                &toml_path,
                format!("[package]\nname = \"{}\"\n", bad_name.replace('\\', "\\\\")),
            )
            .expect("write Cargo.toml");
            assert!(
                RustHandler::get_cargo_package_name(&toml_path, &p).is_none(),
                "expected rejection for '{bad_name}'"
            );
        }
    }
}
