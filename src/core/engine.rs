//! High-level compilation/execution orchestrator and source discovery (`CompilerRunner`).
//!
//! Adapted from Python's `src/runner/core.py`. Rather than inheriting from 6
//! handler mixin classes, [`CompilerRunner`] stores disjoint fields (`base`,
//! `config`, `printer`, `cache`, `registry`) so [`ExecutionContext`] can borrow
//! them cleanly and dispatch through [`HandlerRegistry`].

use std::fs;
use std::path::{Path, PathBuf};

use crate::core::base::BaseRunner;
use crate::core::handlers::c_family::CFamilyHandler;
use crate::core::handlers::custom_handler::CustomLanguageHandler;
use crate::core::registry::{
    dotted_ext_lower, ExecutionContext, HandlerRegistry, LanguageHandler, RunnerOptions,
};
use crate::core::handlers::java_handler::JavaHandler;
use crate::core::handlers::script_handler::ScriptHandler;
use crate::util::cache::CacheManager;
use crate::util::config::Config;
use crate::util::env::PackageMetadata;
use crate::model::error::RunError;
use crate::util::glob::{match_extension, match_path};
use crate::util::output::{Printer, TagColor, Verbosity};
use crate::util::security::Validator;

/// Main orchestrator managing caching, source discovery (`-L`), and [`HandlerRegistry`] dispatch.
pub struct CompilerRunner {
    /// Core execution engine, options, and cleanup tracker.
    pub base: BaseRunner,
    /// Loaded `Run.toml` configuration.
    pub config: Config,
    /// Configured tagged terminal printer.
    pub printer: Printer,
    /// Optional SHA-256 build/binary cache (`None` when `--no-cache` is enabled).
    pub cache: Option<CacheManager>,
    /// Priority-ordered language handler registry (`CustomLanguageHandler` first).
    pub registry: HandlerRegistry,
}

impl CompilerRunner {
    /// Create a [`CompilerRunner`] using the OS user cache directory (`~/.cache/run`)
    /// unless `--no-cache` is enabled.
    pub fn new(
        options: RunnerOptions,
        extra_flags_str: Option<&str>,
        run_args_str: Option<&str>,
        config: Config,
        printer: Printer,
    ) -> Result<Self, RunError> {
        let cache = if options.no_cache {
            printer.debug("Cache disabled via --no-cache");
            None
        } else {
            let cwd = std::env::current_dir()?;
            Some(CacheManager::new(&cwd)?)
        };
        let base = BaseRunner::new(options, extra_flags_str, run_args_str, &config, printer)?;
        Ok(Self {
            base,
            config,
            printer,
            cache,
            registry: HandlerRegistry::new(),
        })
    }

    /// Create a [`CompilerRunner`] with an explicit cache base directory (for isolated tests).
    pub fn with_cache_base(
        options: RunnerOptions,
        extra_flags_str: Option<&str>,
        run_args_str: Option<&str>,
        config: Config,
        printer: Printer,
        project_root: &Path,
        cache_base: &Path,
    ) -> Result<Self, RunError> {
        let cache = if options.no_cache {
            None
        } else {
            Some(CacheManager::with_cache_base(project_root, cache_base)?)
        };
        let base = BaseRunner::new_with_stdin(
            options,
            extra_flags_str,
            run_args_str,
            &config,
            printer,
            None,
        )?;
        Ok(Self {
            base,
            config,
            printer,
            cache,
            registry: HandlerRegistry::new(),
        })
    }

    /// Run `f` with runner printers and `capture_silent` optionally silenced,
    /// restoring the original `printer`, `base.printer`, `base.options.quiet`,
    /// and `base.capture_silent` state unconditionally afterward.
    pub fn with_quiet_capture<R>(
        &mut self,
        silence_base: bool,
        silence_runner: bool,
        capture_silent: bool,
        f: impl FnOnce(&mut Self, Printer) -> R,
    ) -> R {
        let orig_printer = self.printer;
        let orig_base_printer = self.base.printer;
        let orig_quiet = self.base.options.quiet;
        let orig_capture_silent = self.base.capture_silent;

        let quiet_printer = Printer::with_ui_mode(
            Verbosity::Quiet,
            orig_base_printer.colors(),
            orig_base_printer.ui_mode(),
        );
        if silence_base {
            self.base.printer = quiet_printer;
        }
        if silence_runner {
            self.printer = quiet_printer;
            self.base.options.quiet = true;
        }
        if capture_silent {
            self.base.capture_silent = true;
        }

        let res = f(self, orig_printer);

        self.printer = orig_printer;
        self.base.printer = orig_base_printer;
        self.base.options.quiet = orig_quiet;
        self.base.capture_silent = orig_capture_silent;
        res
    }

    /// Recursively discover C/C++/Java and custom-language source files under `path`
    /// up to `max_depth` directory levels, respecting hidden folders and `[core]` exclusions.
    pub fn find_source_files(&self, path: &Path, max_depth: Option<usize>) -> Vec<PathBuf> {
        let meta = PackageMetadata::get();
        let mut target_exts: Vec<String> = Vec::new();
        target_exts.extend(meta.extensions.c.iter().cloned());
        target_exts.extend(meta.extensions.cpp.iter().cloned());
        target_exts.extend(meta.extensions.java.iter().cloned());
        for lang in self.config.get_custom_languages() {
            for ext in lang.extensions {
                if !target_exts.contains(&ext) {
                    target_exts.push(ext);
                }
            }
        }

        let mut files = Vec::new();
        self.walk_sources(path, path, 0, max_depth, &target_exts, &mut files);
        files.sort();
        files
    }

    /// Recursive helper collecting matching source files inside `dir` at `depth`.
    fn walk_sources(
        &self,
        root: &Path,
        dir: &Path,
        depth: usize,
        max_depth: Option<usize>,
        target_exts: &[String],
        out: &mut Vec<PathBuf>,
    ) {
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        let ignore_dirs = &PackageMetadata::get().excludes.discovery_ignore_dirs;

        let mut subdirs = Vec::new();
        for entry in entries.flatten() {
            let Ok(ft) = entry.file_type() else {
                continue;
            };
            let entry_path = entry.path();
            let name = entry.file_name();
            let name_str = name.to_string_lossy();

            if ft.is_dir() {
                if name_str.starts_with('.')
                    || ignore_dirs.iter().any(|d| d == name_str.as_ref())
                    || self
                        .base
                        .exclude_dirs
                        .iter()
                        .any(|pat| match_path(&entry_path, pat, Some(root), true))
                {
                    continue;
                }
                if max_depth.is_none_or(|limit| depth < limit) {
                    subdirs.push(entry_path);
                }
            } else if ft.is_file() {
                if self
                    .base
                    .exclude_files
                    .iter()
                    .any(|pat| match_path(&entry_path, pat, Some(root), true))
                {
                    continue;
                }
                let ext = dotted_ext_lower(&entry_path);
                if ext.is_empty() {
                    continue;
                }
                let matches_lang = target_exts
                    .iter()
                    .any(|e| e == &ext || match_extension(&ext, e, true));
                let excluded_ext = self
                    .base
                    .exclude_exts
                    .iter()
                    .any(|pat| match_extension(&ext, pat, true));
                if matches_lang && !excluded_ext {
                    out.push(entry_path);
                }
            }
        }

        for sub in subdirs {
            self.walk_sources(root, &sub, depth + 1, max_depth, target_exts, out);
        }
    }

    /// Validate, filter, and dispatch a single source file through [`HandlerRegistry`].
    pub fn handle_single_file(&mut self, fp: &Path) -> Result<bool, RunError> {
        if !Validator::validate_path(fp) {
            if !self.base.options.force {
                return Err(RunError::config(format!(
                    "Refusing to process file with suspicious characters: {}. Use -f / --force to override.",
                    fp.display()
                )));
            }
            self.printer.warning(&format!(
                "Processing file with suspicious characters due to --force: {}",
                fp.display()
            ));
        }

        let cwd = std::env::current_dir().ok();
        let name = fp
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| fp.display().to_string());
        if self
            .base
            .exclude_files
            .iter()
            .any(|pat| match_path(fp, pat, cwd.as_deref(), true))
        {
            if !self.base.options.quiet {
                self.printer.action(
                    "SKIP",
                    &format!("{name} is in exclude files"),
                    TagColor::Gray,
                );
            }
            return Ok(true);
        }

        let raw_ext = dotted_ext_lower(fp);
        let mut ext = raw_ext.clone();
        if ext.is_empty() && fp.is_file() {
            ext = ScriptHandler::detect_language_from_shebang(fp, Some(&self.printer));
        }

        let is_excluded_ext = self.base.exclude_exts.iter().any(|pat| {
            (!raw_ext.is_empty() && match_extension(&raw_ext, pat, true))
                || (!ext.is_empty() && match_extension(&ext, pat, true))
        });
        if is_excluded_ext {
            if !self.base.options.quiet {
                self.printer.action(
                    "SKIP",
                    &format!("{ext} file is exclude extensions"),
                    TagColor::Gray,
                );
            }
            return Ok(true);
        }

        // Dispatch via HandlerRegistry (custom languages first, then built-in handlers).
        let handler_opt = if fp.extension().is_some() {
            self.registry.get_handler(fp, &self.config)
        } else {
            self.registry.get_handler_for_ext(&ext, &self.config)
        };
        let Some(handler) = handler_opt else {
            if Self::is_executable_binary(fp) {
                self.base.options.target_files = vec![fp.to_path_buf()];
                return self.base.execute_binary(fp, &[]);
            }
            return Err(RunError::config(format!("Unsupported extension: {ext}")));
        };

        self.base.options.target_files = vec![fp.to_path_buf()];
        let detected = (!ext.is_empty()).then_some(ext);
        let mut ctx = ExecutionContext {
            config: &self.config,
            printer: &self.printer,
            cache: self.cache.as_ref(),
            runner: &mut self.base,
            detected_ext: detected,
        };
        handler.run_single(fp, &mut ctx)
    }

    /// Dispatch multi-file compilation (custom language first, then C/C++, then Java).
    pub fn handle_multi_compile(&mut self, paths: &[PathBuf]) -> Result<bool, RunError> {
        if paths.is_empty() {
            return Ok(true);
        }

        self.base.options.target_files = paths.to_vec();
        let mut ctx = ExecutionContext {
            config: &self.config,
            printer: &self.printer,
            cache: self.cache.as_ref(),
            runner: &mut self.base,
            detected_ext: None,
        };

        if CustomLanguageHandler.can_handle(&paths[0], ctx.config) {
            return CustomLanguageHandler.run_multi(paths, &mut ctx);
        }

        let has_c = paths.iter().any(|p| {
            let ext = dotted_ext_lower(p);
            !ext.is_empty() && CFamilyHandler::is_c_family_source_ext(&ext)
        });
        if has_c {
            return CFamilyHandler.run_multi(paths, &mut ctx);
        }

        let has_java = paths.iter().any(|p| {
            let ext = dotted_ext_lower(p);
            !ext.is_empty() && JavaHandler::is_java_ext(&ext)
        });
        if has_java {
            return JavaHandler.run_multi(paths, &mut ctx);
        }

        Err(RunError::config(
            "No supported files found for multi-compile",
        ))
    }

    /// Compile and run `files` (in multi-file mode when `multi` is `true`, otherwise
    /// sequentially per file), logging errors and returning `true` iff all succeeded.
    pub fn compile_and_run(&mut self, files: &[PathBuf], multi: bool) -> bool {
        if files.is_empty() {
            return true;
        }
        if multi {
            match self.handle_multi_compile(files) {
                Ok(ok) => ok,
                Err(err) => {
                    self.printer
                        .error(&format!("Multi-file compilation failed: {err}"));
                    false
                }
            }
        } else {
            let mut all_ok = true;
            for fp in files {
                match self.handle_single_file(fp) {
                    Ok(true) => {}
                    Ok(false) => all_ok = false,
                    Err(err) => {
                        self.printer
                            .error(&format!("Failed to process {}: {err}", fp.display()));
                        all_ok = false;
                    }
                }
            }
            all_ok
        }
    }

    /// Delegate raw command execution to [`BaseRunner::run_command`].
    pub fn run_command(
        &mut self,
        cmd: &[String],
        use_shell: bool,
        compiling: bool,
    ) -> Result<bool, RunError> {
        self.base.run_command(cmd, use_shell, compiling)
    }

    /// Return `true` if `path` is a directly executable native binary (ELF, Mach-O, PE,
    /// or Unix executable file without a source extension / with `.out` or `.exe`).
    pub fn is_executable_binary(path: &Path) -> bool {
        use std::io::Read;

        if !path.is_file() {
            return false;
        }

        if let Ok(mut f) = fs::File::open(path) {
            let mut magic = [0u8; 4];
            if let Ok(n) = f.read(&mut magic) {
                if n >= 4 {
                    let is_elf = magic == [0x7f, b'E', b'L', b'F'];
                    let is_macho = matches!(
                        magic,
                        [0xfe, 0xed, 0xfa, 0xce]
                            | [0xfe, 0xed, 0xfa, 0xcf]
                            | [0xce, 0xfa, 0xed, 0xfe]
                            | [0xcf, 0xfa, 0xed, 0xfe]
                            | [0xca, 0xfe, 0xba, 0xbe]
                            | [0xbe, 0xba, 0xfe, 0xca]
                    );
                    if is_elf || is_macho {
                        return true;
                    }
                }
                if n >= 2 && magic[0] == b'M' && magic[1] == b'Z' {
                    return true;
                }
            }
        }

        let ext = dotted_ext_lower(path);
        if matches!(ext.as_str(), "" | ".out" | ".exe") {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(meta) = fs::metadata(path)
                    && meta.permissions().mode() & 0o111 != 0
                {
                    return true;
                }
            }
            #[cfg(windows)]
            {
                if ext == ".exe" {
                    return true;
                }
            }
        }

        false
    }

    /// Delegate binary execution to [`BaseRunner::execute_binary`].
    pub fn execute_binary(&mut self, bin_path: &Path, args: &[String]) -> Result<bool, RunError> {
        self.base.execute_binary(bin_path, args)
    }

    /// Delegate output binary path computation to [`BaseRunner::get_executable_path`].
    pub fn get_executable_path(&self, source_path: &Path) -> Result<PathBuf, RunError> {
        self.base.get_executable_path(source_path)
    }

    /// Delegate artifact cleanup to [`BaseRunner::cleanup`].
    pub fn cleanup(&self) {
        self.base.cleanup();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::test_support::TempDir;
    use crate::util::output::{Colors, Verbosity};

    /// Helper building a [`CompilerRunner`] isolated inside `tmp`.
    fn make_runner(tmp: &Path, opts: RunnerOptions, cfg: Config) -> CompilerRunner {
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        CompilerRunner::with_cache_base(
            opts,
            None,
            None,
            cfg,
            printer,
            tmp,
            &tmp.join("cache"),
        )
        .expect("create CompilerRunner")
    }

    /// Port of `dev:tests/test_core.py::test_find_source_files_ignores_dirs`.
    #[test]
    fn test_find_source_files_ignores_dirs() {
        let tmp = TempDir::new().expect("tempdir");
        fs::create_dir_all(tmp.path().join(".git")).unwrap();
        fs::write(tmp.path().join(".git/bad.c"), "").unwrap();
        fs::create_dir_all(tmp.path().join("node_modules")).unwrap();
        fs::write(tmp.path().join("node_modules/bad.cpp"), "").unwrap();
        fs::create_dir_all(tmp.path().join("src")).unwrap();
        let good_c = tmp.path().join("src/good.c");
        fs::write(&good_c, "").unwrap();

        let runner = make_runner(tmp.path(), RunnerOptions::default(), Config::default());
        let found = runner.find_source_files(tmp.path(), None);

        assert!(found.contains(&good_c));
        assert!(!found.contains(&tmp.path().join(".git/bad.c")));
        assert!(!found.contains(&tmp.path().join("node_modules/bad.cpp")));
    }

    /// Port of `dev:tests/test_core.py::test_find_source_files_max_depth`.
    #[test]
    fn test_find_source_files_max_depth() {
        let tmp = TempDir::new().expect("tempdir");
        let level1 = tmp.path().join("level1");
        let level2 = level1.join("level2");
        fs::create_dir_all(&level2).unwrap();
        let f1 = level1.join("f1.c");
        let f2 = level2.join("f2.c");
        fs::write(&f1, "").unwrap();
        fs::write(&f2, "").unwrap();

        let runner = make_runner(tmp.path(), RunnerOptions::default(), Config::default());

        assert!(runner.find_source_files(tmp.path(), Some(0)).is_empty());

        let found_1 = runner.find_source_files(tmp.path(), Some(1));
        assert!(found_1.contains(&f1));
        assert!(!found_1.contains(&f2));

        let found_2 = runner.find_source_files(tmp.path(), Some(2));
        assert!(found_2.contains(&f1));
        assert!(found_2.contains(&f2));
    }

    /// Port of `dev:tests/test_security.py::test_core_runner_rejects_suspicious_path`.
    #[test]
    fn test_core_runner_rejects_suspicious_path() {
        let tmp = TempDir::new().expect("tempdir");
        let mut runner = make_runner(tmp.path(), RunnerOptions::default(), Config::default());
        let err = runner
            .handle_single_file(Path::new("../outside.c"))
            .expect_err("traversal path must fail without --force");
        assert!(err
            .to_string()
            .contains("Refusing to process file with suspicious characters"));

        let mut forced = make_runner(
            tmp.path(),
            RunnerOptions {
                force: true,
                dry_run: true,
                ..Default::default()
            },
            Config::default(),
        );
        let ok = forced
            .handle_single_file(Path::new("../outside.c"))
            .expect("force + dry_run should proceed");
        assert!(ok);
    }

    /// Port of `dev:tests/test_core.py` hyphenated filenames (`-main.c`, `-main.rs`).
    #[test]
    fn test_hyphenated_filename_dry_run_handling() {
        let tmp = TempDir::new().expect("tempdir");
        let mut runner = make_runner(
            tmp.path(),
            RunnerOptions {
                dry_run: true,
                ..Default::default()
            },
            Config::default(),
        );
        assert!(runner.handle_single_file(Path::new("-main.c")).unwrap());
        assert!(runner.handle_single_file(Path::new("-main.rs")).unwrap());
    }
}
