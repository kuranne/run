//! C/C++ compilation, shebang scripting, inline directives, and `CPM` entry-point detection.
//!
//! Adapted from Python's `src/runner/c_family_handler.py` and `src/runner/cpm.py`.
//! Uses `regex::Regex` (cached via [`std::sync::OnceLock`]) for comment stripping
//! and `main()` detection, [`std::thread::scope`] for lock-free parallel object
//! compilation bounded by `-j` / `[package.metadata.run.limits]`, and
//! [`crate::util::cache::CacheManager`] for incremental object and shebang binary caching.

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::OnceLock;
use std::thread;

use regex::Regex;

use crate::runner::handler::{
    dotted_ext_lower, normalize_leading_dash, ExecutionContext, LanguageHandler,
};
use crate::runner::script_handler::ScriptHandler;
use crate::util::config::Config;
use crate::util::env::PackageMetadata;
use crate::util::errors::RunError;
use crate::util::output::TagColor;

/// Maximum bytes read per source file when scanning for `main()` (10 MiB, matching `dev:cpm.py`).
const MAX_SOURCE_SIZE: u64 = 10 * 1024 * 1024;

/// Cached regex stripping C/C++ line (`//`) and block (`/* */`) comments.
static COMMENT_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex matching C/C++ entry signatures (`int|void|auto [w]main(`).
static MAIN_RE: OnceLock<Regex> = OnceLock::new();

/// Monotonic counter used to name unique temporary sanitized shebang sources when `--no-cache` is set.
static TMP_SOURCE_SEQ: AtomicU64 = AtomicU64::new(1);

/// Return the compiled C/C++ comment-stripping regex.
fn comment_regex() -> &'static Regex {
    COMMENT_RE.get_or_init(|| {
        Regex::new(r"(?m)//[^\n]*$|(?s)/\*.*?\*/").expect("valid C/C++ comment regex")
    })
}

/// Return the compiled C/C++ `main()` signature regex.
fn main_regex() -> &'static Regex {
    MAIN_RE.get_or_init(|| {
        Regex::new(r"(?:int|void|auto)\s+(?:w)?main\s*\(").expect("valid C/C++ main regex")
    })
}

/// C/C++ Project Manager (`CPM`) scanning multiple translation units for `main()`.
#[derive(Debug, Clone, Copy, Default)]
pub struct CPM;

impl CPM {
    /// Scan `sources` (capping reads at 10 MiB and stripping comments) and return the
    /// first file containing a C/C++ `main` function signature, or `None`.
    pub fn get_main_file(sources: &[PathBuf]) -> Option<PathBuf> {
        let comment_re = comment_regex();
        let main_re = main_regex();

        for src in sources {
            let Ok(file) = File::open(src) else {
                continue;
            };
            let mut buf = Vec::new();
            if file.take(MAX_SOURCE_SIZE).read_to_end(&mut buf).is_err() {
                continue;
            }
            let text = String::from_utf8_lossy(&buf);
            let stripped = comment_re.replace_all(&text, "");
            if main_re.is_match(&stripped) {
                return Some(src.clone());
            }
        }
        None
    }
}

/// Handler supporting single-file and multi-file C/C++ compilation and shebang execution.
#[derive(Debug, Clone, Copy, Default)]
pub struct CFamilyHandler;

impl CFamilyHandler {
    /// Return `true` if `ext` is a C or C++ source extension in [`PackageMetadata`].
    pub fn is_c_family_source_ext(ext: &str) -> bool {
        let meta = &PackageMetadata::get().extensions;
        let lower = ext.to_ascii_lowercase();
        meta.c.iter().any(|e| e == &lower) || meta.cpp.iter().any(|e| e == &lower)
    }

    /// Return `true` if `ext` is a C/C++ header extension in [`PackageMetadata`].
    pub fn is_c_family_header_ext(ext: &str) -> bool {
        let lower = ext.to_ascii_lowercase();
        PackageMetadata::get()
            .extensions
            .c_headers
            .iter()
            .any(|e| e == &lower)
    }

    /// Resolve the C or C++ compiler binary honoring `--compiler`, then `[runners]`, then `Cargo.toml` defaults.
    fn resolve_compiler(lang: &str, ctx: &ExecutionContext<'_>) -> String {
        if let Some(ref override_bin) = ctx.runner.options.compiler {
            return override_bin.clone();
        }
        let runners = &PackageMetadata::get().default_runners;
        let default_compiler = if lang == "c" {
            &runners.c
        } else {
            &runners.cpp
        };
        ctx.config.get_runner(lang, default_compiler)
    }

    /// Compile a single C/C++ source file to an object file, reusing `CacheManager` when unchanged.
    fn compile_object_file(
        compiler: &str,
        source: &Path,
        extra_cmd: &[String],
        ctx: &ExecutionContext<'_>,
    ) -> Option<PathBuf> {
        let obj_file = match ctx.cache {
            Some(cache) => cache.get_object_path(source),
            None => source.with_extension(if cfg!(windows) { "obj" } else { "o" }),
        };

        if let Some(cache) = ctx.cache
            && !cache.is_changed(source, Some(&obj_file))
        {
            return Some(obj_file);
        }

        let name = source
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| source.display().to_string());
        ctx.printer
            .action("COMPILE", &format!("{name} -> object"), TagColor::Green);

        let mut cmd = vec![
            compiler.to_string(),
            "-c".to_string(),
            normalize_leading_dash(source),
            "-o".to_string(),
            obj_file.to_string_lossy().into_owned(),
        ];
        cmd.extend(extra_cmd.iter().cloned());

        match ctx.runner.run_compile_step(&cmd) {
            Ok(()) => {
                if let Some(cache) = ctx.cache
                    && !ctx.runner.options.dry_run
                {
                    cache.update_cache(source, Some(&obj_file));
                }
                Some(obj_file)
            }
            Err(err) => {
                ctx.printer.error(&err.to_string());
                None
            }
        }
    }

    /// Compile and run a single C/C++ file (including shebang preprocessing and inline directives).
    fn handle_single_with_ext(
        fp: &Path,
        detected_ext: Option<&str>,
        ctx: &mut ExecutionContext<'_>,
    ) -> Result<bool, RunError> {
        let mut ext = detected_ext
            .map(|e| e.to_ascii_lowercase())
            .unwrap_or_else(|| dotted_ext_lower(fp));
        if ext.is_empty() {
            ext = ScriptHandler::detect_language_from_shebang(fp, Some(ctx.printer));
        }
        if ext.is_empty() {
            ext = ".c".to_string();
        }

        let lang = if ext == ".c" { "c" } else { "cpp" };
        let compiler = Self::resolve_compiler(lang, ctx);
        let preset_flags = ctx
            .config
            .get_preset_flags(ctx.runner.options.preset.as_deref(), lang);

        let has_shebang = File::open(fp)
            .ok()
            .and_then(|f| {
                let mut line = String::new();
                BufReader::new(f).read_line(&mut line).ok()?;
                Some(line.starts_with("#!"))
            })
            .unwrap_or(false);

        let inline_flags = if has_shebang {
            ScriptHandler::extract_inline_directives(fp)
        } else {
            Vec::new()
        };

        let out_name = if has_shebang && let Some(cache) = ctx.cache {
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

        let cmd = if has_shebang {
            let sanitized_source = if let Some(cache) = ctx.cache {
                cache.get_sanitized_source_path(fp, &ext)
            } else {
                let seq = TMP_SOURCE_SEQ.fetch_add(1, Ordering::Relaxed);
                let tmp = std::env::temp_dir().join(format!(
                    "run_shebang_{}_{}{}",
                    std::process::id(),
                    seq,
                    ext
                ));
                ctx.runner.output_files.push(tmp.clone());
                tmp
            };

            if !ctx.runner.options.dry_run {
                if let Some(parent) = sanitized_source.parent() {
                    fs::create_dir_all(parent)?;
                }

                let raw_bytes = fs::read(fp)?;
                let raw_text = String::from_utf8_lossy(&raw_bytes);
                let rest = match raw_text.find('\n') {
                    Some(idx) => &raw_text[idx + 1..],
                    None => "",
                };

                let resolved_fp = fs::canonicalize(fp)
                    .unwrap_or_else(|_| std::path::absolute(fp).unwrap_or_else(|_| fp.to_path_buf()));
                let escaped_fp = resolved_fp
                    .to_string_lossy()
                    .replace('\\', "\\\\")
                    .replace('"', "\\\"");
                let sanitized_body = format!("#line 2 \"{escaped_fp}\"\n{rest}");
                fs::write(&sanitized_source, sanitized_body)?;
            }

            let parent_dir = fp
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .map(|p| fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf()))
                .unwrap_or_else(|| {
                    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
                });

            let mut c = vec![compiler];
            c.extend(ctx.runner.extra_flags.iter().cloned());
            c.extend(inline_flags);
            c.extend(preset_flags);
            c.push(format!("-I{}", parent_dir.display()));
            c.push(normalize_leading_dash(&sanitized_source));
            c.push("-o".to_string());
            c.push(out_name.to_string_lossy().into_owned());
            c
        } else {
            let mut c = vec![compiler];
            c.extend(ctx.runner.extra_flags.iter().cloned());
            c.extend(preset_flags);
            c.push(normalize_leading_dash(fp));
            c.push("-o".to_string());
            c.push(out_name.to_string_lossy().into_owned());
            c
        };

        ctx.run_command(&cmd, false, true)?;

        if let Some(cache) = ctx.cache
            && !ctx.runner.options.dry_run
        {
            cache.update_cache(fp, Some(&out_name));
        }

        if !(has_shebang && ctx.cache.is_some()) {
            ctx.runner.output_files.push(out_name.clone());
        }

        ctx.execute_binary(&out_name, &[])
    }
}

impl LanguageHandler for CFamilyHandler {
    /// Return `"CFamilyHandler"`.
    fn name(&self) -> &'static str {
        "CFamilyHandler"
    }

    /// Accept `.c`, `.cpp`, `.cc`, and `.cxx` files defined in `Cargo.toml`.
    fn can_handle(&self, path: &Path, _config: &Config) -> bool {
        let ext = dotted_ext_lower(path);
        !ext.is_empty() && Self::is_c_family_source_ext(&ext)
    }

    /// Compile and run a single C/C++ file.
    fn run_single(&self, path: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let detected = ctx.detected_ext.clone();
        Self::handle_single_with_ext(path, detected.as_deref(), ctx)
    }

    /// Compile multiple C/C++ files in parallel, locate `main()` via [`CPM`], link, and execute.
    fn run_multi(&self, paths: &[PathBuf], ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let sources: Vec<PathBuf> = paths
            .iter()
            .filter(|p| {
                let ext = dotted_ext_lower(p);
                !ext.is_empty() && Self::is_c_family_source_ext(&ext)
            })
            .cloned()
            .collect();
        let headers: Vec<PathBuf> = paths
            .iter()
            .filter(|p| {
                let ext = dotted_ext_lower(p);
                !ext.is_empty() && Self::is_c_family_header_ext(&ext)
            })
            .cloned()
            .collect();

        let main_source = CPM::get_main_file(&sources).ok_or_else(|| {
            RunError::execution(
                "Could not find a 'main' function in any of the provided C/C++ files.",
            )
        })?;

        let ext = dotted_ext_lower(&main_source);
        let lang = if ext == ".c" { "c" } else { "cpp" };
        let compiler = Self::resolve_compiler(lang, ctx);
        let preset_flags = ctx
            .config
            .get_preset_flags(ctx.runner.options.preset.as_deref(), lang);

        let mut base_cmd = ctx.runner.extra_flags.clone();
        base_cmd.extend(preset_flags.iter().cloned());

        let mut include_dirs = BTreeSet::new();
        for h in &headers {
            let dir = match h.parent() {
                Some(p) if !p.as_os_str().is_empty() => p.to_string_lossy().into_owned(),
                _ => ".".to_string(),
            };
            include_dirs.insert(dir);
        }
        for dir in include_dirs {
            base_cmd.push(format!("-I{dir}"));
        }

        let limits = &PackageMetadata::get().limits;
        let max_workers = ctx
            .runner
            .options
            .jobs
            .unwrap_or_else(|| {
                let cpus = thread::available_parallelism()
                    .map(|n| n.get())
                    .unwrap_or(1);
                (cpus + limits.parallel_jobs_extra).min(limits.max_parallel_jobs_cap)
            })
            .max(1);

        ctx.printer.info(&format!(
            "Compiling {} files using {} threads...",
            sources.len(),
            max_workers
        ));

        let worker_count = max_workers.min(sources.len()).max(1);
        let next_idx = AtomicUsize::new(0);
        let results: Vec<std::sync::Mutex<Option<PathBuf>>> =
            (0..sources.len()).map(|_| std::sync::Mutex::new(None)).collect();

        let ctx_ref: &ExecutionContext<'_> = ctx;
        thread::scope(|scope| {
            for _ in 0..worker_count {
                scope.spawn(|| {
                    loop {
                        let idx = next_idx.fetch_add(1, Ordering::Relaxed);
                        if idx >= sources.len() {
                            break;
                        }
                        if let Some(obj_path) =
                            Self::compile_object_file(&compiler, &sources[idx], &base_cmd, ctx_ref)
                            && let Ok(mut slot) = results[idx].lock()
                        {
                            *slot = Some(obj_path);
                        }
                    }
                });
            }
        });

        let mut object_files = Vec::with_capacity(sources.len());
        let mut failed = false;
        for slot_mutex in results {
            let maybe_obj = slot_mutex.into_inner().unwrap_or(None);
            if let Some(obj_path) = maybe_obj {
                if ctx.cache.is_none() {
                    ctx.runner.output_files.push(obj_path.clone());
                }
                object_files.push(obj_path);
            } else {
                failed = true;
            }
        }

        if failed {
            return Err(RunError::execution("Build failed during compilation phase."));
        }

        let out_name = ctx.get_executable_path(&main_source)?;
        let mut link_cmd = vec![compiler];
        for obj in &object_files {
            link_cmd.push(obj.to_string_lossy().into_owned());
        }
        link_cmd.push("-o".to_string());
        link_cmd.push(out_name.to_string_lossy().into_owned());
        link_cmd.extend(ctx.runner.extra_flags.iter().cloned());
        link_cmd.extend(preset_flags);

        let link_ok = ctx.run_command(&link_cmd, false, true)?;
        if link_ok {
            ctx.runner.output_files.push(out_name.clone());
            return ctx.execute_binary(&out_name, &[]);
        }
        Ok(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::base::BaseRunner;
    use crate::runner::handler::RunnerOptions;
    use crate::util::output::{Colors, Printer, Verbosity};
    use crate::runner::test_support::TempDir;

    /// Port of `dev:tests/test_cpm.py::test_cpm_get_main_file`.
    #[test]
    fn test_cpm_get_main_file() {
        let tmp = TempDir::new().expect("tempdir");
        let utils = tmp.path().join("utils.c");
        let main_c = tmp.path().join("app.c");
        fs::write(&utils, "int helper(void) { return 1; }\n").expect("write utils");
        fs::write(&main_c, "int main(void) { return 0; }\n").expect("write main");

        let found = CPM::get_main_file(&[utils, main_c.clone()]);
        assert_eq!(found, Some(main_c));
    }

    /// Port of `dev:tests/test_cpm.py::test_cpm_ignores_comments`.
    #[test]
    fn test_cpm_ignores_comments() {
        let tmp = TempDir::new().expect("tempdir");
        let commented = tmp.path().join("commented.cpp");
        let real_main = tmp.path().join("real.cpp");
        fs::write(
            &commented,
            "// int main() { return 0; }\n/* void main() {} */\nint helper() { return 2; }\n",
        )
        .expect("write commented");
        fs::write(&real_main, "auto main() -> int { return 0; }\n").expect("write real");

        let found = CPM::get_main_file(&[commented, real_main.clone()]);
        assert_eq!(found, Some(real_main));
    }

    /// Port of `dev:tests/test_cpm.py::test_cpm_no_main`.
    #[test]
    fn test_cpm_no_main() {
        let tmp = TempDir::new().expect("tempdir");
        let lib = tmp.path().join("lib.c");
        fs::write(&lib, "void foo(void) {}\n").expect("write lib");
        assert!(CPM::get_main_file(&[lib]).is_none());
    }

    /// Verify `--compiler` overrides `Run.toml` `[runners]` in [`CFamilyHandler::resolve_compiler`].
    #[test]
    fn test_c_family_compiler_override_priority() {
        let cfg = Config::from_toml_str("[runners]\nc = \"clang\"\n", None, None)
            .expect("valid runners config");
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions {
                compiler: Some("my-custom-cc".to_string()),
                ..Default::default()
            },
            None,
            None,
            &cfg,
            printer,
            None,
        )
        .expect("base runner");

        let ctx = ExecutionContext {
            config: &cfg,
            printer: &printer,
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };
        assert_eq!(CFamilyHandler::resolve_compiler("c", &ctx), "my-custom-cc");
    }

    /// White-box test: `CPM::get_main_file` recognizes `wmain`, `auto main()`, and ignores non-UTF8.
    #[test]
    fn test_cpm_wmain_and_auto_main() {
        let tmp = TempDir::new().expect("tempdir");
        let helper = tmp.path().join("helper.cpp");
        let entry = tmp.path().join("entry.cpp");
        fs::write(&helper, b"\xff\xfe // binary noise\nint helper() { return 1; }\n").unwrap();
        fs::write(&entry, "auto main() -> int { return 0; }\n").unwrap();
        assert_eq!(CPM::get_main_file(&[helper, entry.clone()]), Some(entry));

        let win = tmp.path().join("win.c");
        fs::write(&win, "int wmain(int argc, wchar_t *argv[]) { return 0; }\n").unwrap();
        assert_eq!(CPM::get_main_file(std::slice::from_ref(&win)), Some(win));
    }

    /// White-box test: parallel multi-file build uses `CacheManager` to skip unchanged `.o`
    /// compilation and invokes `MockCommandExecutor` only for dirty objects + final link + run.
    #[test]
    fn test_c_family_multi_file_cache_hit_skips_object_compile() {
        use std::sync::Arc;
        use crate::runner::base::{ExecOutcome, MockCommandExecutor};
        use crate::util::cache::CacheManager;

        let tmp = TempDir::new().expect("tempdir");
        let a_c = tmp.path().join("a.c");
        let main_c = tmp.path().join("main.c");
        fs::write(&a_c, "int f(void) { return 1; }\n").unwrap();
        fs::write(&main_c, "int f(void); int main(void) { return f(); }\n").unwrap();

        let cache = CacheManager::with_cache_base(tmp.path(), &tmp.path().join("cache"))
            .expect("cache");
        // Pre-populate a.c object cache so only main.c needs `-c` compilation.
        let a_obj = cache.get_object_path(&a_c);
        fs::create_dir_all(a_obj.parent().unwrap()).unwrap();
        fs::write(&a_obj, b"ELF_FAKE_OBJ").unwrap();
        cache.update_cache(&a_c, Some(&a_obj));

        let cfg = Config::default();
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions {
                out_dir: Some(tmp.path().join("out").to_string_lossy().into_owned()),
                jobs: Some(2),
                ..Default::default()
            },
            None,
            None,
            &cfg,
            printer,
            None,
        )
        .expect("base runner");

        let mut mock = MockCommandExecutor::new();
        // Expect 1 compile (`main.c` only, NOT `a.c`), 1 link (both `.o`s), and 1 run.
        mock.expect_execute()
            .times(1)
            .withf(|spec| spec.compiling && spec.cmd.iter().any(|a| a == "-c") && spec.cmd.iter().any(|a| a.ends_with("main.c")))
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.01,
                    memory_bytes: None,
                })
            });
        mock.expect_execute()
            .times(1)
            .withf(|spec| spec.compiling && !spec.cmd.iter().any(|a| a == "-c") && spec.cmd.iter().any(|a| a == "-o"))
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.01,
                    memory_bytes: None,
                })
            });
        mock.expect_execute()
            .times(1)
            .withf(|spec| !spec.compiling)
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.01,
                    memory_bytes: None,
                })
            });
        base.set_executor(Arc::new(mock));

        let mut ctx = ExecutionContext {
            config: &cfg,
            printer: &printer,
            cache: Some(&cache),
            runner: &mut base,
            detected_ext: None,
        };
        let ok = CFamilyHandler
            .run_multi(&[a_c, main_c], &mut ctx)
            .expect("run_multi");
        assert!(ok);
    }
}
