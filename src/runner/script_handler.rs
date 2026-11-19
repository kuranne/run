//! Script language execution, shebang detection, and inline compiler directive parsing.
//!
//! Adapted from Python's `src/runner/script_handler.py`. Uses `regex::Regex`
//! (cached via [`std::sync::OnceLock`]) for `run` shebang recognition, inline
//! `// run:` / `// flags:` extraction, and C vs C++ heuristic detection on
//! extensionless scripts, `shlex::split` for inline flag tokenization, and
//! [`crate::util::sandbox::SystemSandboxEnv`] for PATH interpreter lookup.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;

use crate::runner::handler::{
    dotted_ext_lower, normalize_leading_dash, ExecutionContext, LanguageHandler,
};
use crate::util::config::Config;
use crate::util::env::PackageMetadata;
use crate::util::errors::RunError;
use crate::util::output::Printer;
use crate::util::sandbox::{SandboxEnv, SystemSandboxEnv};

/// Cached regex recognizing `run` in a shebang line (`#!/usr/bin/env run`, `#!/usr/bin/run`, etc.).
static RUN_SHEBANG_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex extracting inline `// run: <flags>` / `/* flags: <flags> */` directives.
static INLINE_DIRECTIVE_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex recognizing explicit C++ language directive (`// lang: cpp`).
static EXPLICIT_CPP_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex recognizing explicit C language directive (`// lang: c`).
static EXPLICIT_C_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex recognizing C++ standard library headers and keywords in extensionless scripts.
static CPP_INDICATOR_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex recognizing POSIX `sh` shebangs (`#!/bin/sh`, `#!/usr/bin/env sh`).
static POSIX_SH_RE: OnceLock<Regex> = OnceLock::new();

/// Handler for interpreted script languages (`.sh`, `.rb`, `.js`, `.pl`, `.lua`) and shebang helpers.
#[derive(Debug, Clone, Copy, Default)]
pub struct ScriptHandler;

impl ScriptHandler {
    /// Locate the first available interpreter from `candidates`, returning `candidates[0]` directly in `--sandbox` mode.
    pub fn get_interpreter_path(candidates: &[String], sandbox: bool) -> Result<String, RunError> {
        let Some(first) = candidates.first() else {
            return Err(RunError::config("Interpreter candidate list is empty."));
        };
        if sandbox {
            return Ok(first.clone());
        }
        for name in candidates {
            if let Some(resolved) = SystemSandboxEnv.which(name) {
                return Ok(resolved.to_string_lossy().into_owned());
            }
        }
        Err(RunError::config(format!(
            "Interpreter not found. Tried: {}",
            candidates.join(", ")
        )))
    }

    /// Return `true` if `fp` is a regular file whose first line invokes `run` via a shebang (`#!`).
    pub fn is_run_shebang(fp: &Path) -> bool {
        if !fp.is_file() {
            return false;
        }
        let Ok(file) = File::open(fp) else {
            return false;
        };
        let mut first_line = String::new();
        if BufReader::new(file).read_line(&mut first_line).is_err() {
            return false;
        }
        let trimmed = first_line.trim();
        if !trimmed.starts_with("#!") {
            return false;
        }
        let re = RUN_SHEBANG_RE.get_or_init(|| {
            Regex::new(r"(?:/|\s)run(?:\s|$)").expect("valid run shebang regex")
        });
        re.is_match(trimmed)
    }

    /// Scan up to `limits.inline_directive_scan_lines` top lines for `// run:` or `// flags:`
    /// compiler/runner directives and tokenize them with `shlex::split`.
    pub fn extract_inline_directives(fp: &Path) -> Vec<String> {
        if !fp.is_file() {
            return Vec::new();
        }
        let Ok(file) = File::open(fp) else {
            return Vec::new();
        };
        let re = INLINE_DIRECTIVE_RE.get_or_init(|| {
            Regex::new(r"(?i)(?://|/\*)\s*(?:run|flags)\s*:\s*(.+?)(?:\*/|$)")
                .expect("valid inline directive regex")
        });

        let limit = PackageMetadata::get().limits.inline_directive_scan_lines;
        let mut flags = Vec::new();
        let reader = BufReader::new(file);

        for line_res in reader.lines().take(limit) {
            let Ok(line) = line_res else {
                break;
            };
            let clean = line.trim();
            if let Some(caps) = re.captures(clean)
                && let Some(m) = caps.get(1)
            {
                let flag_str = m.as_str().trim();
                let lower = flag_str.to_ascii_lowercase();
                if matches!(lower.as_str(), "cpp" | "c" | "c++") {
                    continue;
                }
                if let Some(tokens) = shlex::split(flag_str) {
                    flags.extend(tokens);
                }
            }
        }
        flags
    }

    /// Infer a dotted language extension (e.g. `".py"`, `".cpp"`, `".c"`) from a file's shebang line.
    pub fn detect_language_from_shebang(fp: &Path, printer: Option<&Printer>) -> String {
        let Ok(file) = File::open(fp) else {
            return String::new();
        };
        let mut reader = BufReader::new(file);
        let mut first_line_raw = String::new();
        if reader.read_line(&mut first_line_raw).is_err() {
            return String::new();
        }
        let first_line = first_line_raw.trim();
        if !first_line.starts_with("#!") {
            return String::new();
        }

        if Self::is_run_shebang(fp) {
            let ext = dotted_ext_lower(fp);
            if matches!(
                ext.as_str(),
                ".c" | ".cpp"
                    | ".cc"
                    | ".cxx"
                    | ".rs"
                    | ".java"
                    | ".py"
                    | ".sh"
                    | ".rb"
                    | ".js"
                    | ".ts"
                    | ".go"
            ) {
                return ext;
            }

            // Extensionless `run` shebang file: inspect top lines to distinguish C++ vs C.
            let detect_limit = PackageMetadata::get().limits.shebang_c_detect_lines;
            let mut header_text = String::new();
            header_text.push_str(&first_line_raw);
            for line_res in reader.lines().take(detect_limit.saturating_sub(1)) {
                match line_res {
                    Ok(line) => {
                        header_text.push_str(&line);
                        header_text.push('\n');
                    }
                    Err(_) => break,
                }
            }

            let explicit_cpp = EXPLICIT_CPP_RE.get_or_init(|| {
                Regex::new(r"(?i)(?://|/\*)\s*(?:lang|run)\s*:\s*(?:cpp|c\+\+)")
                    .expect("valid explicit cpp regex")
            });
            if explicit_cpp.is_match(&header_text) {
                return ".cpp".to_string();
            }

            let explicit_c = EXPLICIT_C_RE.get_or_init(|| {
                Regex::new(r"(?i)(?://|/\*)\s*(?:lang|run)\s*:\s*c\b")
                    .expect("valid explicit c regex")
            });
            if explicit_c.is_match(&header_text) {
                return ".c".to_string();
            }

            let cpp_ind = CPP_INDICATOR_RE.get_or_init(|| {
                Regex::new(
                    r"#\s*include\s*<(?:iostream|vector|string|algorithm|memory)>|\bstd::|\busing\s+namespace\s+std\b|\btemplate\s*<|\bclass\s+[A-Za-z_][A-Za-z0-9_]*\s*[{:]",
                )
                .expect("valid cpp indicator regex")
            });
            if cpp_ind.is_match(&header_text) {
                return ".cpp".to_string();
            }
            return ".c".to_string();
        }

        let sh_re = POSIX_SH_RE
            .get_or_init(|| Regex::new(r"(?:/|\s)sh(?:\s|$)").expect("valid posix sh regex"));
        if first_line.contains("python") {
            ".py".to_string()
        } else if first_line.contains("bash") || sh_re.is_match(first_line) {
            ".sh".to_string()
        } else if first_line.contains("ruby") {
            ".rb".to_string()
        } else if first_line.contains("node") || first_line.contains("javascript") {
            ".js".to_string()
        } else if first_line.contains("perl") {
            ".pl".to_string()
        } else if first_line.contains("lua") {
            ".lua".to_string()
        } else {
            if let Some(p) = printer {
                p.warning(&format!("Unknown shebang in {}: {first_line}", fp.display()));
            }
            String::new()
        }
    }
}

impl LanguageHandler for ScriptHandler {
    /// Return `"ScriptHandler"`.
    fn name(&self) -> &'static str {
        "ScriptHandler"
    }

    /// Match built-in script extensions `.sh`, `.rb`, `.js`, `.pl`, and `.lua`.
    fn can_handle(&self, path: &Path, _config: &Config) -> bool {
        matches!(
            dotted_ext_lower(path).as_str(),
            ".sh" | ".rb" | ".js" | ".pl" | ".lua"
        )
    }

    /// Execute the script file using the appropriate system interpreter (or `--inspect-brk` for Node debug).
    fn run_single(&self, fp: &Path, ctx: &mut ExecutionContext<'_>) -> Result<bool, RunError> {
        let ext = ctx
            .detected_ext
            .as_deref()
            .map(str::to_ascii_lowercase)
            .unwrap_or_else(|| dotted_ext_lower(fp));

        let runners = &PackageMetadata::get().default_runners;
        let candidates: &[String] = match ext.as_str() {
            ".sh" => &runners.sh,
            ".rb" => &runners.ruby,
            ".js" => &runners.node,
            ".pl" => &runners.perl,
            ".lua" => &runners.lua,
            other => {
                return Err(RunError::config(format!(
                    "Unsupported script extension: {other}"
                )));
            }
        };

        let prog = match Self::get_interpreter_path(candidates, ctx.runner.options.sandbox) {
            Ok(p) => p,
            Err(err) => {
                ctx.printer.error(&err.to_string());
                return Ok(false);
            }
        };

        let target = normalize_leading_dash(fp);
        let mut cmd = vec![prog];
        if ext == ".js" && ctx.runner.options.debug {
            cmd.push("--inspect-brk".to_string());
        }
        cmd.push("--".to_string());
        cmd.push(target);
        cmd.extend(ctx.runner.run_args.iter().cloned());
        ctx.run_command(&cmd, false, false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use crate::runner::test_support::TempDir;

    /// Port of `dev:tests/test_c_shebang.py::test_is_run_shebang`.
    #[test]
    fn test_is_run_shebang() {
        let tmp = TempDir::new().expect("tempdir");
        for (name, body, expected) in [
            ("env_run.c", "#!/usr/bin/env run\nint main(){}", true),
            ("bin_run.c", "#!/usr/bin/run\nint main(){}", true),
            ("local_run.c", "#!/usr/local/bin/run\nint main(){}", true),
            ("py.py", "#!/usr/bin/env python3\nprint('hi')", false),
            ("c.c", "#!/usr/bin/env c\nint main(){}", false),
            ("plain.c", "int main(){}", false),
        ] {
            let p = tmp.path().join(name);
            fs::write(&p, body).expect("write test script");
            assert_eq!(ScriptHandler::is_run_shebang(&p), expected, "{name}");
        }
    }

    /// Port of `dev:tests/test_c_shebang.py::test_extract_inline_directives`.
    #[test]
    fn test_extract_inline_directives() {
        let tmp = TempDir::new().expect("tempdir");
        let p = tmp.path().join("directives.c");
        fs::write(
            &p,
            "#!/usr/bin/env run\n// run: -O3 -Wall\n// flags: -lm -pthread\n/* run: -DTEST=1 */\n// lang: cpp\n#include <stdio.h>\nint main() { return 0; }\n",
        )
        .expect("write directives");

        let flags = ScriptHandler::extract_inline_directives(&p);
        assert_eq!(flags, vec!["-O3", "-Wall", "-lm", "-pthread", "-DTEST=1"]);
    }

    /// Port of `dev:tests/test_c_shebang.py::test_detect_language_from_shebang`.
    #[test]
    fn test_detect_language_from_shebang() {
        let tmp = TempDir::new().expect("tempdir");

        let cases = [
            ("script.c", "#!/usr/bin/env run\n#include <stdio.h>\nint main(){}", ".c"),
            ("script.cpp", "#!/usr/bin/env run\n#include <iostream>\nint main(){}", ".cpp"),
            ("tool_dir", "#!/usr/bin/env run\n// lang: cpp\nint main(){}", ".cpp"),
            ("tool_cpp", "#!/usr/bin/env run\n#include <iostream>\nint main() { std::cout << 1; }", ".cpp"),
            ("tool_c", "#!/usr/bin/env run\n#include <stdio.h>\nint main() { printf(\"hi\"); }", ".c"),
            ("py_script", "#!/usr/bin/env python3\nprint(1)\n", ".py"),
            ("sh_script", "#!/bin/sh\necho hi\n", ".sh"),
        ];

        for (name, body, expected) in cases {
            let p = tmp.path().join(name);
            fs::write(&p, body).expect("write shebang sample");
            assert_eq!(
                ScriptHandler::detect_language_from_shebang(&p, None),
                expected,
                "{name}"
            );
        }
    }
}
