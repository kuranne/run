//! Java compilation, `JPM` entry-point/class tracking, and execution handler.
//!
//! Adapted from Python's `src/runner/java_handler.py` and `src/runner/jpm.py`.
//! Uses `regex::Regex` (cached via [`std::sync::OnceLock`]) to locate
//! `public static void main(String` across multi-file sources after stripping
//! comments, snapshots `.class` modification timestamps to track generated
//! bytecode files for automatic cleanup, and rejects `-`-prefixed main class names.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::SystemTime;

use regex::Regex;

use crate::runner::handler::{
    dotted_ext_lower, normalize_leading_dash, ExecutionContext, LanguageHandler,
};
use crate::util::config::Config;
use crate::util::env::PackageMetadata;
use crate::model::error::RunError;
use crate::util::output::TagColor;

/// Maximum bytes read per Java source file when scanning for `main` (10 MiB, matching `dev:jpm.py`).
const MAX_SOURCE_SIZE: u64 = 10 * 1024 * 1024;

/// Cached regex stripping Java line (`//`) and block (`/* */`) comments.
static COMMENT_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex matching `public static void main(String`.
static JAVA_MAIN_RE: OnceLock<Regex> = OnceLock::new();

/// Return the compiled Java comment-stripping regex.
fn comment_regex() -> &'static Regex {
    COMMENT_RE.get_or_init(|| {
        Regex::new(r"(?m)//[^\n]*$|(?s)/\*.*?\*/").expect("valid Java comment regex")
    })
}

/// Return the compiled Java `main` signature regex.
fn java_main_regex() -> &'static Regex {
    JAVA_MAIN_RE.get_or_init(|| {
        Regex::new(r"public\s+static\s+void\s+main\s*\(\s*String").expect("valid Java main regex")
    })
}

/// Java Project Manager (`JPM`) providing entry-class discovery and `.class` cleanup tracking.
#[derive(Debug, Clone, Copy, Default)]
pub struct JPM;

impl JPM {
    /// Extract the expected main class name (`file_stem`) from a Java source path.
    pub fn get_main_class(java_file: &Path) -> Option<String> {
        java_file
            .file_stem()
            .and_then(|s| s.to_str())
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    }

    /// Scan `sources` (up to 10 MiB per file, stripping comments) and return the first file
    /// defining `public static void main(String`.
    pub fn get_main_file(sources: &[PathBuf]) -> Option<PathBuf> {
        let comment_re = comment_regex();
        let main_re = java_main_regex();

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

    /// Snapshot modification timestamps of all `.class` files in `directories`.
    pub fn record_class_files(directories: &[PathBuf]) -> HashMap<PathBuf, SystemTime> {
        let mut state = HashMap::new();
        for dir in directories {
            let Ok(entries) = fs::read_dir(dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let p = entry.path();
                if p.extension().and_then(|e| e.to_str()) == Some("class")
                    && let Ok(meta) = entry.metadata()
                    && let Ok(mtime) = meta.modified()
                {
                    state.insert(p, mtime);
                }
            }
        }
        state
    }

    /// Return `.class` files in `directories` created or modified since `before_state`.
    pub fn get_new_class_files(
        directories: &[PathBuf],
        before_state: &HashMap<PathBuf, SystemTime>,
    ) -> Vec<PathBuf> {
        let mut new_files = Vec::new();
        for dir in directories {
            let Ok(entries) = fs::read_dir(dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let p = entry.path();
                if p.extension().and_then(|e| e.to_str()) == Some("class")
                    && let Ok(meta) = entry.metadata()
                    && let Ok(mtime) = meta.modified()
                {
                    let is_newer = before_state
                        .get(&p)
                        .is_none_or(|prev| mtime > *prev);
                    if is_newer {
                        new_files.push(p);
                    }
                }
            }
        }
        new_files.sort();
        new_files
    }
}

/// Handler for single-file and multi-file Java compilation (`javac`) and execution (`java`).
#[derive(Debug, Clone, Copy, Default)]
pub struct JavaHandler;

impl JavaHandler {
    /// Return `true` if `ext` is a Java source extension configured in `Cargo.toml`.
    pub fn is_java_ext(ext: &str) -> bool {
        let lower = ext.to_ascii_lowercase();
        PackageMetadata::get()
            .extensions
            .java
            .iter()
            .any(|e| e == &lower)
    }

    /// Return normalized parent directory (mapping empty parent to `"."`).
    fn parent_or_dot(path: &Path) -> PathBuf {
        match path.parent() {
            Some(p) if !p.as_os_str().is_empty() => p.to_path_buf(),
            _ => PathBuf::from("."),
        }
    }

    /// Resolve `javac` compiler honoring `--compiler`, `[runners].java`, and `Cargo.toml`.
    fn resolve_compiler(ctx: &ExecutionContext<'_>) -> String {
        if let Some(ref override_bin) = ctx.runner.options.compiler {
            return override_bin.clone();
        }
        let default_javac = &PackageMetadata::get().default_runners.java;
        ctx.config.get_runner("java", default_javac)
    }
}

impl LanguageHandler for JavaHandler {
    /// Return `"JavaHandler"`.
    fn name(&self) -> &'static str {
        "JavaHandler"
    }

    /// Match `.java` source files.
    fn can_handle(&self, path: &Path, _config: &Config) -> bool {
        let ext = dotted_ext_lower(path);
        !ext.is_empty() && Self::is_java_ext(&ext)
    }

    /// Compile and run a single `.java` source file, tracking newly created `.class` files.
    fn run_single(&self, fp: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let meta = PackageMetadata::get();
        let compiler = Self::resolve_compiler(ctx);
        let preset_flags = ctx
            .config
            .get_preset_flags(ctx.runner.options.preset.as_deref(), "java");

        let main_class = JPM::get_main_class(fp).ok_or_else(|| {
            RunError::execution(format!("Could not extract main class from {}", fp.display()))
        })?;
        if main_class.starts_with('-') {
            return Err(RunError::execution(format!(
                "Invalid main class name: {main_class}"
            )));
        }

        let parent_dir = Self::parent_or_dot(fp);
        let dirs = vec![parent_dir.clone()];
        let before_state = JPM::record_class_files(&dirs);

        let mut cmd = vec![compiler];
        cmd.extend(ctx.runner.extra_flags.iter().cloned());
        cmd.extend(preset_flags);
        cmd.push(normalize_leading_dash(fp));

        if !ctx.run_command(&cmd, false, true)? {
            return Ok(false);
        }

        ctx.runner
            .output_files
            .extend(JPM::get_new_class_files(&dirs, &before_state));

        if ctx.runner.options.build_only {
            ctx.printer.action(
                "BUILD",
                &format!("Java classes compiled successfully for {main_class}"),
                TagColor::Green,
            );
            return Ok(true);
        }

        let cp = parent_dir.to_string_lossy().into_owned();
        let mut run_cmd = vec![
            meta.default_runners.java_runtime.clone(),
            "-cp".to_string(),
            cp,
            main_class,
        ];
        run_cmd.extend(ctx.runner.run_args.iter().cloned());
        ctx.run_command(&run_cmd, false, false)
    }

    /// Compile multiple `.java` files together, locate the main entry class via [`JPM`], and execute.
    fn run_multi(&self, paths: &[PathBuf], ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let java_sources: Vec<PathBuf> = paths
            .iter()
            .filter(|p| {
                let ext = dotted_ext_lower(p);
                !ext.is_empty() && Self::is_java_ext(&ext)
            })
            .cloned()
            .collect();
        if java_sources.is_empty() {
            return Err(RunError::config("No Java source files provided for multi-compile."));
        }

        let meta = PackageMetadata::get();
        let compiler = Self::resolve_compiler(ctx);
        let preset_flags = ctx
            .config
            .get_preset_flags(ctx.runner.options.preset.as_deref(), "java");

        let main_source = JPM::get_main_file(&java_sources).unwrap_or_else(|| java_sources[0].clone());
        let main_class = JPM::get_main_class(&main_source).ok_or_else(|| {
            RunError::execution(format!(
                "Could not extract main class from {}",
                main_source.display()
            ))
        })?;
        if main_class.starts_with('-') {
            return Err(RunError::execution(format!(
                "Invalid main class name: {main_class}"
            )));
        }

        let mut seen = HashSet::new();
        let mut parent_dirs = Vec::new();
        for src in &java_sources {
            let dir = Self::parent_or_dot(src);
            if seen.insert(dir.clone()) {
                parent_dirs.push(dir);
            }
        }
        let before_state = JPM::record_class_files(&parent_dirs);

        let mut cmd = vec![compiler];
        cmd.extend(ctx.runner.extra_flags.iter().cloned());
        cmd.extend(preset_flags);
        for src in &java_sources {
            cmd.push(normalize_leading_dash(src));
        }

        if !ctx.run_command(&cmd, false, true)? {
            return Ok(false);
        }

        ctx.runner
            .output_files
            .extend(JPM::get_new_class_files(&parent_dirs, &before_state));

        if ctx.runner.options.build_only {
            ctx.printer.action(
                "BUILD",
                &format!("Java classes compiled successfully for {main_class}"),
                TagColor::Green,
            );
            return Ok(true);
        }

        let sep = if cfg!(windows) { ";" } else { ":" };
        let mut cp_parts = Vec::new();
        let mut cp_seen = HashSet::new();
        for dir in &parent_dirs {
            let s = dir.to_string_lossy().into_owned();
            if cp_seen.insert(s.clone()) {
                cp_parts.push(s);
            }
        }
        if cp_seen.insert(".".to_string()) {
            cp_parts.push(".".to_string());
        }
        let cp = cp_parts.join(sep);

        let mut run_cmd = vec![
            meta.default_runners.java_runtime.clone(),
            "-cp".to_string(),
            cp,
            main_class,
        ];
        run_cmd.extend(ctx.runner.run_args.iter().cloned());
        ctx.run_command(&run_cmd, false, false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::base::BaseRunner;
    use crate::runner::handler::RunnerOptions;
    use crate::util::output::{Colors, Printer, Verbosity};
    use crate::runner::test_support::TempDir;

    /// Port of `dev:tests/test_jpm.py::test_jpm_get_main_class` and `test_jpm_get_main_file`.
    #[test]
    fn test_jpm_main_class_and_file_detection() {
        let tmp = TempDir::new().expect("tempdir");
        let helper = tmp.path().join("Helper.java");
        let app = tmp.path().join("AppMain.java");
        fs::write(&helper, "public class Helper { static int x() { return 1; } }\n")
            .expect("write helper");
        fs::write(
            &app,
            "// public static void main(String[] fake)\npublic class AppMain {\n    public static void main(String[] args) {}\n}\n",
        )
        .expect("write app");

        assert_eq!(JPM::get_main_class(&app).as_deref(), Some("AppMain"));
        assert_eq!(JPM::get_main_file(&[helper, app.clone()]), Some(app));
    }

    /// Port of `dev:tests/test_jpm.py` class file tracking.
    #[test]
    fn test_jpm_record_and_diff_class_files() {
        let tmp = TempDir::new().expect("tempdir");
        let old_cls = tmp.path().join("Old.class");
        fs::write(&old_cls, b"old").expect("write old");

        let dirs = vec![tmp.path().to_path_buf()];
        let before = JPM::record_class_files(&dirs);
        let new_cls = tmp.path().join("New.class");
        fs::write(&new_cls, b"new").expect("write new");

        let created = JPM::get_new_class_files(&dirs, &before);
        assert_eq!(created, vec![new_cls]);
    }

    /// Port of `dev:tests/test_core.py::test_java_handler_classpath`.
    #[test]
    fn test_java_handler_rejects_leading_dash_main_class_in_dry_run() {
        let tmp = TempDir::new().expect("tempdir");
        let bad_java = tmp.path().join("-BadClass.java");
        fs::write(&bad_java, "public class Bad {}").expect("write bad java");

        let cfg = Config::default();
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions {
                dry_run: true,
                ..Default::default()
            },
            None,
            None,
            &cfg,
            printer,
            None,
        )
        .expect("runner");
        let mut ctx = ExecutionContext {
            config: &cfg,
            printer: &printer,
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };

        let err = JavaHandler
            .run_single(&bad_java, &mut ctx)
            .expect_err("leading dash class name must be rejected");
        assert!(err.to_string().contains("Invalid main class name"));
    }

    /// White-box test: `JavaHandler::run_multi` verifies exact `javac` argv, deduplicated `-cp`
    /// classpath, and `java` runtime argv via `MockCommandExecutor` without needing a host JDK.
    #[test]
    fn test_java_handler_multi_mock_argv_and_classpath_dedup() {
        use std::sync::Arc;
        use crate::model::options::MockCommandExecutor;
        use crate::runner::base::ExecOutcome;

        let tmp = TempDir::new().expect("tempdir");
        let sub = tmp.path().join("pkg");
        fs::create_dir_all(&sub).unwrap();
        let helper = sub.join("Helper.java");
        let entry = tmp.path().join("Entry.java");
        fs::write(&helper, "public class Helper { static int v() { return 7; } }\n").unwrap();
        fs::write(
            &entry,
            "public class Entry { public static void main(String... args) {} }\n",
        )
        .unwrap();

        let cfg = Config::default();
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions::default(),
            None,
            Some("arg1 arg2"),
            &cfg,
            printer,
            None,
        )
        .expect("runner");

        let mut seq = mockall::Sequence::new();
        let mut mock = MockCommandExecutor::new();
        mock.expect_execute()
            .times(1)
            .in_sequence(&mut seq)
            .withf(|spec| {
                spec.compiling
                    && spec.cmd[0] == "javac"
                    && spec.cmd.iter().any(|a| a.ends_with("Helper.java"))
                    && spec.cmd.iter().any(|a| a.ends_with("Entry.java"))
            })
            .returning(|_| {
                Ok(ExecOutcome {
                    captured_stdout: String::new(),
                    elapsed_secs: 0.01,
                    memory_bytes: None,
                })
            });
        mock.expect_execute()
            .times(1)
            .in_sequence(&mut seq)
            .withf(|spec| {
                !spec.compiling
                    && spec.cmd[0] == "java"
                    && spec.cmd[1] == "-cp"
                    && spec.cmd[3] == "Entry"
                    && spec.cmd[4..] == ["arg1", "arg2"]
            })
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
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };
        assert!(JavaHandler.run_multi(&[helper, entry], &mut ctx).unwrap());
    }

    /// White-box test: `--build-only` compiles Java classes and skips `java` runtime invocation.
    #[test]
    fn test_java_handler_single_build_only_skips_runtime() {
        use std::sync::Arc;
        use crate::model::options::MockCommandExecutor;
        use crate::runner::base::ExecOutcome;

        let tmp = TempDir::new().expect("tempdir");
        let app = tmp.path().join("App.java");
        fs::write(&app, "public class App { public static void main(String[] args) {} }\n")
            .unwrap();

        let cfg = Config::default();
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut base = BaseRunner::new_with_stdin(
            RunnerOptions {
                build_only: true,
                ..Default::default()
            },
            None,
            None,
            &cfg,
            printer,
            None,
        )
        .expect("runner");

        let mut mock = MockCommandExecutor::new();
        mock.expect_execute()
            .times(1)
            .withf(|spec| spec.compiling && spec.cmd[0] == "javac")
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
            cache: None,
            runner: &mut base,
            detected_ext: None,
        };
        assert!(JavaHandler.run_single(&app, &mut ctx).unwrap());
    }
}
