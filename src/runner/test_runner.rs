//! Batch testcase discovery and single-compile/multi-run test runner.
//!
//! Adapted from Python's `src/runner/test_runner.py`. Uses `regex::Regex` for
//! natural numerical ordering of testcase filenames and prefix stripping, and
//! reuses compiled binaries produced by Phase 1 (`--build-only`) across all
//! `(input, expected_output)` pairs in Phase 2.

use std::cmp::Ordering;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;

use crate::runner::c_family::{CFamilyHandler, CPM};
use crate::runner::core::CompilerRunner;
use crate::runner::handler::dotted_ext_lower;
use crate::runner::java_handler::JavaHandler;
use crate::util::env::PackageMetadata;
use crate::util::output::TagColor;

/// Cached regex splitting filenames into numeric and non-numeric chunks.
static NAT_SPLIT_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex stripping leading `in`/`input` prefixes from stems.
static PREFIX_STRIP_RE: OnceLock<Regex> = OnceLock::new();

/// Token produced by natural filename splitting.
#[derive(Debug, Clone, PartialEq, Eq)]
enum NaturalToken {
    /// Non-numeric substring stored lowercase.
    Text(String),
    /// Parsed unsigned integer segment for numerical sorting.
    Number(u128),
}

impl Ord for NaturalToken {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Self::Number(a), Self::Number(b)) => a.cmp(b),
            (Self::Text(a), Self::Text(b)) => a.cmp(b),
            (Self::Number(_), Self::Text(_)) => Ordering::Less,
            (Self::Text(_), Self::Number(_)) => Ordering::Greater,
        }
    }
}

impl PartialOrd for NaturalToken {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Produce a natural-sort key so `in2.txt` precedes `in10.txt`.
fn natural_sort_key(path: &Path) -> Vec<NaturalToken> {
    let re = NAT_SPLIT_RE.get_or_init(|| Regex::new(r"(\d+)").expect("valid digit split regex"));
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();

    let mut tokens = Vec::new();
    let mut last_end = 0;
    for m in re.find_iter(&name) {
        if m.start() > last_end {
            tokens.push(NaturalToken::Text(
                name[last_end..m.start()].to_ascii_lowercase(),
            ));
        }
        if let Ok(num) = m.as_str().parse::<u128>() {
            tokens.push(NaturalToken::Number(num));
        } else {
            tokens.push(NaturalToken::Text(m.as_str().to_ascii_lowercase()));
        }
        last_end = m.end();
    }
    if last_end < name.len() {
        tokens.push(NaturalToken::Text(name[last_end..].to_ascii_lowercase()));
    }
    tokens
}

/// Return `true` if `path` matches supported testcase input naming conventions
/// (`.in`, `.input`, or `in*.txt` / `input*.txt`).
fn is_input_testcase_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
    matches!(ext, "in" | "input")
        || ((name.starts_with("in") || name.starts_with("input")) && ext == "txt")
}

/// Batch testcases runner matching `(input, expected_output)` pairs and printing summaries.
#[derive(Debug, Clone, Copy, Default)]
pub struct TestcasesRunner;

impl TestcasesRunner {
    /// Discover and naturally sort `(input_path, expected_output_path)` pairs under `test_dir`.
    pub fn discover_test_pairs(test_dir: &Path) -> Vec<(PathBuf, PathBuf)> {
        if !test_dir.is_dir() {
            return Vec::new();
        }
        let Ok(entries) = fs::read_dir(test_dir) else {
            return Vec::new();
        };

        let mut all_files: Vec<PathBuf> = entries.filter_map(Result::ok).map(|e| e.path()).collect();
        all_files.sort_by_key(|p| natural_sort_key(p));

        let strip_re = PREFIX_STRIP_RE.get_or_init(|| {
            Regex::new(r"(?i)^(?:input|in)[_-]?").expect("valid prefix strip regex")
        });

        let mut pairs = Vec::new();
        for in_file in all_files.into_iter().filter(|p| is_input_testcase_file(p)) {
            let stem = in_file
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let normalized = strip_re.replace(&stem, "");

            let candidates = [
                format!("{stem}.out"),
                format!("{stem}.ans"),
                format!("{stem}.output"),
                format!("out{normalized}.txt"),
                format!("output{normalized}.txt"),
                format!("ans{normalized}.txt"),
                format!("out_{normalized}.txt"),
                format!("output_{normalized}.txt"),
                format!("ans_{normalized}.txt"),
                format!("{normalized}.out"),
                format!("{normalized}.ans"),
                format!("{normalized}.output"),
            ];

            if let Some(out_file) = candidates
                .into_iter()
                .map(|name| test_dir.join(name))
                .find(|cand| cand.is_file())
            {
                pairs.push((in_file, out_file));
            }
        }

        pairs.sort_by_key(|(in_file, _)| natural_sort_key(in_file));
        pairs
    }

    /// Execute `target_files` against every discovered testcase pair in `test_dir`.
    ///
    /// Compiled languages are compiled once during Phase 1 (`build_only = true`) and
    /// executed directly per testcase during Phase 2, then a colorized pass/fail
    /// summary is printed to standard output.
    pub fn run_tests(
        runner: &mut CompilerRunner,
        test_dir: &Path,
        target_files: &[PathBuf],
        mut is_multi: bool,
    ) -> bool {
        if target_files.is_empty() {
            runner
                .printer
                .error("No target files provided for run_tests.");
            return false;
        }
        if target_files.len() > 1 {
            is_multi = true;
        }

        let pairs = Self::discover_test_pairs(test_dir);
        if pairs.is_empty() {
            runner.printer.warning(&format!(
                "No matching test pairs found in '{}'. Expected *.in + *.out or in*.txt + out*.txt.",
                test_dir.display()
            ));
            return false;
        }

        let total = pairs.len();
        runner.printer.info(&format!(
            "Discovered {total} test case(s) in {}",
            test_dir.display()
        ));
        let banner = runner.printer.colors().style_text(
            &format!("=== Running Test Suite ({total} cases) ==="),
            TagColor::Cyan,
            true,
        );
        println!("\n{banner}\n");

        // Phase 1: Compile once if target uses a compiled language.
        let (is_compiled, bin_path) = match Self::prepare_compiled_binary(runner, target_files, is_multi)
        {
            Ok(res) => res,
            Err(msg) => {
                runner.printer.error(&msg);
                return false;
            }
        };

        // Snapshot runner state restored unconditionally after Phase 2.
        let orig_stdin = runner.base.options.stdin.take();
        let orig_expect = runner.base.options.expect.take();
        let orig_test_dir = runner.base.options.test_dir.take();
        let orig_buffered_stdin = runner.base.buffered_stdin().map(|b| b.to_vec());

        runner.base.options.test_dir = Some(test_dir.to_string_lossy().into_owned());

        let mut passed_count = 0usize;
        for (idx, (in_path, out_path)) in pairs.iter().enumerate() {
            let in_bytes = match fs::read(in_path) {
                Ok(bytes) => bytes,
                Err(err) => {
                    runner.printer.error(&format!(
                        "Test #{} ({}) failed to read input: {err}",
                        idx + 1,
                        in_path.display()
                    ));
                    continue;
                }
            };

            runner.base.options.stdin = Some(in_path.to_string_lossy().into_owned());
            runner.base.options.expect = Some(out_path.to_string_lossy().into_owned());
            runner.base.set_buffered_stdin(Some(in_bytes));

            let outcome = if is_compiled
                && let Some(ref bin) = bin_path
                && bin.exists()
            {
                runner.execute_binary(bin, &[])
            } else if is_multi {
                runner.handle_multi_compile(target_files)
            } else {
                runner.handle_single_file(&target_files[0])
            };

            match outcome {
                Ok(true) => passed_count += 1,
                Ok(false) => {}
                Err(err) => {
                    let name = in_path
                        .file_name()
                        .map(|n| n.to_string_lossy().into_owned())
                        .unwrap_or_else(|| in_path.display().to_string());
                    runner.printer.error(&format!(
                        "Test #{} ({name}) failed with exception: {err}",
                        idx + 1
                    ));
                }
            }
        }

        runner.base.options.stdin = orig_stdin;
        runner.base.options.expect = orig_expect;
        runner.base.options.test_dir = orig_test_dir;
        runner.base.set_buffered_stdin(orig_buffered_stdin);

        let colors = runner.printer.colors();
        let summary_header = colors.style_text("=== Test Summary ===", TagColor::Cyan, true);
        let pct = (passed_count as f64 / total as f64) * 100.0;
        let summary_color = if passed_count == total {
            TagColor::Green
        } else {
            TagColor::Red
        };
        let summary_line = colors.style_text(
            &format!("Passed: {passed_count}/{total} ({pct:.1}%)"),
            summary_color,
            false,
        );
        println!("\n{summary_header}");
        println!("{summary_line}\n");

        passed_count == total
    }

    /// Compile compiled-language targets once in `--build-only` mode before iterating testcases.
    fn prepare_compiled_binary(
        runner: &mut CompilerRunner,
        target_files: &[PathBuf],
        is_multi: bool,
    ) -> Result<(bool, Option<PathBuf>), String> {
        let mut is_compiled = false;
        let mut bin_path: Option<PathBuf> = None;

        if is_multi {
            let c_sources: Vec<PathBuf> = target_files
                .iter()
                .filter(|p| CFamilyHandler::is_c_family_source_ext(&dotted_ext_lower(p)))
                .cloned()
                .collect();
            let first_ext = dotted_ext_lower(&target_files[0]);
            let lang_cfg = runner.config.get_language_by_extension(&first_ext);

            if !c_sources.is_empty() {
                let main_source = CPM::get_main_file(&c_sources)
                    .unwrap_or_else(|| c_sources[0].clone());
                bin_path = runner.get_executable_path(&main_source).ok();
                is_compiled = true;
            } else if let Some(ref cfg) = lang_cfg
                && (cfg.compile.is_some() || cfg.build.is_some())
            {
                let main_candidate = target_files
                    .iter()
                    .find(|p| {
                        p.file_stem()
                            .and_then(|s| s.to_str())
                            .is_some_and(|s| s.eq_ignore_ascii_case("main"))
                    })
                    .unwrap_or(&target_files[0]);
                bin_path = runner.get_executable_path(main_candidate).ok();
                is_compiled = true;
            } else if target_files
                .iter()
                .any(|p| JavaHandler::is_java_ext(&dotted_ext_lower(p)))
            {
                is_compiled = true;
            }

            if is_compiled {
                let prev_build_only = runner.base.options.build_only;
                runner.base.options.build_only = true;
                let compile_res = runner.handle_multi_compile(target_files);
                runner.base.options.build_only = prev_build_only;

                match compile_res {
                    Ok(true) => {
                        if let Some(ref bin) = bin_path
                            && !bin.exists()
                            && !runner.base.options.dry_run
                        {
                            return Err(format!(
                                "Multi-file compilation failed to produce binary: {}",
                                bin.display()
                            ));
                        }
                    }
                    Ok(false) => {
                        return Err("Multi-file compilation failed.".to_string());
                    }
                    Err(err) => {
                        return Err(format!("Multi-file compilation failed: {err}"));
                    }
                }
            }
        } else {
            let target = &target_files[0];
            let ext = dotted_ext_lower(target);
            let meta_compiled = PackageMetadata::get()
                .extensions
                .compiled
                .iter()
                .any(|c| c.eq_ignore_ascii_case(&ext));
            let custom_compiled = runner
                .config
                .get_language_by_extension(&ext)
                .is_some_and(|l| l.compile.is_some() || l.build.is_some());

            is_compiled = meta_compiled || custom_compiled;
            if is_compiled {
                bin_path = runner.get_executable_path(target).ok();
                let prev_build_only = runner.base.options.build_only;
                runner.base.options.build_only = true;
                let compile_res = runner.handle_single_file(target);
                runner.base.options.build_only = prev_build_only;

                match compile_res {
                    Ok(true) => {}
                    Ok(false) => {
                        return Err(format!("Compilation failed for {}", target.display()));
                    }
                    Err(err) => {
                        return Err(format!("Compilation failed for {}: {err}", target.display()));
                    }
                }
            }
        }

        Ok((is_compiled, bin_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::handler::RunnerOptions;
    use crate::runner::test_support::TempDir;
    use crate::util::config::Config;
    use crate::util::output::{Colors, Printer, Verbosity};

    #[test]
    fn test_natural_sort_and_discover_pairs() {
        let tmp = TempDir::new().expect("tempdir");
        for (inp, out) in [
            ("in1.txt", "out1.txt"),
            ("in2.txt", "out2.txt"),
            ("in10.txt", "out10.txt"),
            ("sample.in", "sample.out"),
            ("input_a.txt", "ans_a.txt"),
        ] {
            fs::write(tmp.path().join(inp), "1\n").unwrap();
            fs::write(tmp.path().join(out), "1\n").unwrap();
        }
        // Unpaired input is ignored.
        fs::write(tmp.path().join("orphan.in"), "x\n").unwrap();

        let pairs = TestcasesRunner::discover_test_pairs(tmp.path());
        let names: Vec<String> = pairs
            .iter()
            .map(|(i, _)| i.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        assert_eq!(
            names,
            vec!["in1.txt", "in2.txt", "in10.txt", "input_a.txt", "sample.in"]
        );
    }

    #[test]
    fn test_run_tests_dry_run_and_state_restore() {
        let tmp = TempDir::new().expect("tempdir");
        let td = tmp.path().join("cases");
        fs::create_dir_all(&td).unwrap();
        fs::write(td.join("1.in"), "hello\n").unwrap();
        fs::write(td.join("1.out"), "hello\n").unwrap();
        let script = tmp.path().join("echo.py");
        fs::write(&script, "print(input())\n").unwrap();

        let opts = RunnerOptions {
            dry_run: true,
            stdin: Some("orig.in".to_string()),
            expect: Some("orig.out".to_string()),
            ..RunnerOptions::default()
        };
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut runner = CompilerRunner::with_cache_base(
            opts,
            None,
            None,
            Config::default(),
            printer,
            tmp.path(),
            &tmp.path().join("cache"),
        )
        .expect("create runner");

        let ok = TestcasesRunner::run_tests(&mut runner, &td, &[script], false);
        assert!(ok);
        assert_eq!(runner.base.options.stdin.as_deref(), Some("orig.in"));
        assert_eq!(runner.base.options.expect.as_deref(), Some("orig.out"));
        assert_eq!(runner.base.options.test_dir, None);
    }

    #[test]
    fn test_run_tests_empty_dir_returns_false() {
        let tmp = TempDir::new().expect("tempdir");
        let td = tmp.path().join("empty_cases");
        fs::create_dir_all(&td).unwrap();
        let src = tmp.path().join("a.py");
        fs::write(&src, "print(1)\n").unwrap();

        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let mut runner = CompilerRunner::with_cache_base(
            RunnerOptions::default(),
            None,
            None,
            Config::default(),
            printer,
            tmp.path(),
            &tmp.path().join("cache"),
        )
        .expect("create runner");

        assert!(!TestcasesRunner::run_tests(&mut runner, &td, &[src], false));
    }

    /// White-box test: `natural_sort_key` handles > `u128::MAX` digit sequences without panic.
    #[test]
    fn test_natural_sort_huge_number_overflow_fallback() {
        let huge = "case_9999999999999999999999999999999999999999999999.in";
        let small = "case_2.in";
        let k_huge = natural_sort_key(Path::new(huge));
        let k_small = natural_sort_key(Path::new(small));
        assert!(k_small < k_huge);
    }

    /// White-box test: `TestcasesRunner::run_tests` with `MockCommandExecutor` restores
    /// `build_only`, `stdin`, `expect`, and `test_dir` even when Phase 1 compilation fails.
    #[test]
    fn test_run_tests_restores_state_on_compile_failure() {
        use std::sync::Arc;
        use crate::runner::base::MockCommandExecutor;
        use crate::util::errors::RunError;

        let tmp = TempDir::new().expect("tempdir");
        let td = tmp.path().join("cases");
        fs::create_dir_all(&td).unwrap();
        fs::write(td.join("1.in"), "5\n").unwrap();
        fs::write(td.join("1.out"), "10\n").unwrap();
        let src = tmp.path().join("prog.c");
        fs::write(&src, "int main(){return 0;}\n").unwrap();

        let opts = RunnerOptions {
            stdin: Some("prev.in".to_string()),
            expect: Some("prev.out".to_string()),
            ..RunnerOptions::default()
        };
        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let mut runner = CompilerRunner::with_cache_base(
            opts,
            None,
            None,
            Config::default(),
            printer,
            tmp.path(),
            &tmp.path().join("cache"),
        )
        .expect("create runner");

        let mut mock = MockCommandExecutor::new();
        mock.expect_execute()
            .times(1)
            .withf(|spec| spec.compiling)
            .returning(|_| Err(RunError::compilation("syntax error")));
        runner.base.set_executor(Arc::new(mock));

        assert!(!TestcasesRunner::run_tests(&mut runner, &td, &[src], false));
        assert!(!runner.base.options.build_only);
        assert_eq!(runner.base.options.stdin.as_deref(), Some("prev.in"));
        assert_eq!(runner.base.options.expect.as_deref(), Some("prev.out"));
        assert_eq!(runner.base.options.test_dir, None);
    }
}
