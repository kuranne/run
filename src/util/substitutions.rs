//! Template variable and environment variable substitution utilities.
//!
//! Adapted from Python's `src/util/substitutions.py`, using `regex::Regex` for
//! placeholder matching (`${var}`, `{var}`, `${env:VAR}`, `{env:VAR}`) and
//! `shlex::try_quote` for POSIX shell-safe path quoting. Environment expansion
//! consults [`crate::util::env::is_dangerous_env_var`] and strips control
//! characters (`\0`, `\r`, `\n`).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::{Captures, Regex};

use crate::util::env::is_dangerous_env_var;
use crate::util::output::Printer;

/// Regex matching `${key}` or `{key}` placeholders.
static VAR_PATTERN: OnceLock<Regex> = OnceLock::new();

/// POSIX shell-quote `value` via `shlex::try_quote`, stripping NUL bytes first.
pub(crate) fn shell_quote(value: &str) -> String {
    let sanitized = value.replace('\0', "");
    shlex::try_quote(&sanitized)
        .map(|cow| cow.into_owned())
        .unwrap_or(sanitized)
}

/// Namespace for template variable context construction and string/list expansion.
pub struct VariableSubstitutor;

impl VariableSubstitutor {
    /// Build a variable context map for a single source file, optional output binary, and output directory.
    ///
    /// Populates `file`, `files`, `filename` (shell-quoted iff containing a space),
    /// `name`, `stem`, `ext` (dot-prefixed to match Python `Path.suffix`), `dir`,
    /// `parent` (mapping empty `Path::parent()` to `"."`), `out`, `executable`, and `out_dir`.
    pub fn build_file_context(
        file_path: Option<&Path>,
        out_path: Option<&Path>,
        out_dir: Option<&str>,
    ) -> HashMap<String, String> {
        let mut ctx = HashMap::new();

        if let Some(fp) = file_path {
            let fp_str = fp.to_string_lossy();
            let quoted_file = shell_quote(&fp_str);
            ctx.insert("file".to_string(), quoted_file.clone());
            ctx.insert("files".to_string(), quoted_file);

            let fname = fp
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let filename_val = if fname.contains(' ') {
                shell_quote(&fname)
            } else {
                fname
            };
            ctx.insert("filename".to_string(), filename_val);

            let stem = fp
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            ctx.insert("name".to_string(), stem.clone());
            ctx.insert("stem".to_string(), stem);

            let ext = fp
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy()))
                .unwrap_or_default();
            ctx.insert("ext".to_string(), ext);

            let parent_str = match fp.parent() {
                Some(p) if !p.as_os_str().is_empty() => p.to_string_lossy().into_owned(),
                _ => ".".to_string(),
            };
            let quoted_parent = shell_quote(&parent_str);
            ctx.insert("dir".to_string(), quoted_parent.clone());
            ctx.insert("parent".to_string(), quoted_parent);
        }

        if let Some(op) = out_path {
            let quoted_out = shell_quote(&op.to_string_lossy());
            ctx.insert("out".to_string(), quoted_out.clone());
            ctx.insert("executable".to_string(), quoted_out);
        }

        if let Some(od) = out_dir {
            ctx.insert("out_dir".to_string(), shell_quote(od));
        }

        ctx
    }

    /// Build a variable context map for multiple source files.
    ///
    /// Sets single-file variables from the first entry and joins all shell-quoted
    /// paths into `files`.
    pub fn build_multi_file_context(
        file_paths: &[PathBuf],
        out_path: Option<&Path>,
        out_dir: Option<&str>,
    ) -> HashMap<String, String> {
        let first = file_paths.first().map(PathBuf::as_path);
        let mut ctx = Self::build_file_context(first, out_path, out_dir);
        if !file_paths.is_empty() {
            let joined = file_paths
                .iter()
                .map(|p| shell_quote(&p.to_string_lossy()))
                .collect::<Vec<_>>()
                .join(" ");
            ctx.insert("files".to_string(), joined);
        }
        ctx
    }

    /// Replace `${var}`, `{var}`, `${env:VAR}`, and `{env:VAR}` placeholders in `template`
    /// using the process environment.
    pub fn substitute_string(
        template: &str,
        context: &HashMap<String, String>,
        quote_env: bool,
    ) -> String {
        Self::substitute_string_with_env(template, context, quote_env, |name| {
            std::env::var(name).ok()
        })
    }

    /// Replace placeholders in `template` using a custom environment lookup closure.
    ///
    /// Allows pure, race-free unit testing without mutating process-global env vars.
    pub fn substitute_string_with_env<F>(
        template: &str,
        context: &HashMap<String, String>,
        quote_env: bool,
        env_lookup: F,
    ) -> String
    where
        F: Fn(&str) -> Option<String>,
    {
        let re = VAR_PATTERN.get_or_init(|| {
            Regex::new(r"(?:\$\{([a-zA-Z0-9_:.]+)\}|\{([a-zA-Z0-9_:.]+)\})")
                .expect("valid static VAR_PATTERN")
        });

        re.replace_all(template, |caps: &Captures<'_>| {
            let full = caps.get(0).map_or("", |m| m.as_str());
            let key = caps
                .get(1)
                .or_else(|| caps.get(2))
                .map_or("", |m| m.as_str());

            if let Some(env_var) = key.strip_prefix("env:") {
                if is_dangerous_env_var(env_var) {
                    Printer::global().warning(&format!(
                        "Rejected dangerous environment variable expansion in template: {env_var}"
                    ));
                    return String::new();
                }
                let raw = env_lookup(env_var).unwrap_or_default();
                let sanitized = raw.replace(['\0', '\r', '\n'], "");
                if quote_env {
                    shell_quote(&sanitized)
                } else {
                    sanitized
                }
            } else {
                context
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| full.to_string())
            }
        })
        .into_owned()
    }

    /// Replace placeholders across a slice of template strings using the process environment.
    pub fn substitute_list(
        templates: &[String],
        context: &HashMap<String, String>,
        quote_env: bool,
    ) -> Vec<String> {
        templates
            .iter()
            .map(|item| Self::substitute_string(item, context, quote_env))
            .collect()
    }

    /// Replace placeholders across a slice of template strings using a custom environment lookup.
    pub fn substitute_list_with_env<F>(
        templates: &[String],
        context: &HashMap<String, String>,
        quote_env: bool,
        env_lookup: F,
    ) -> Vec<String>
    where
        F: Fn(&str) -> Option<String>,
    {
        templates
            .iter()
            .map(|item| Self::substitute_string_with_env(item, context, quote_env, &env_lookup))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Port of `dev:tests/test_substitutions.py::test_build_file_context`.
    #[test]
    fn test_build_file_context() {
        let src = Path::new("src/main.cpp");
        let out = Path::new("build/main.out");
        let ctx = VariableSubstitutor::build_file_context(Some(src), Some(out), Some("build"));

        assert_eq!(ctx.get("file").map(String::as_str), Some("src/main.cpp"));
        assert_eq!(ctx.get("filename").map(String::as_str), Some("main.cpp"));
        assert_eq!(ctx.get("name").map(String::as_str), Some("main"));
        assert_eq!(ctx.get("stem").map(String::as_str), Some("main"));
        assert_eq!(ctx.get("ext").map(String::as_str), Some(".cpp"));
        assert_eq!(ctx.get("dir").map(String::as_str), Some("src"));
        assert_eq!(ctx.get("parent").map(String::as_str), Some("src"));
        assert_eq!(ctx.get("out").map(String::as_str), Some("build/main.out"));
        assert_eq!(
            ctx.get("executable").map(String::as_str),
            Some("build/main.out")
        );
        assert_eq!(ctx.get("out_dir").map(String::as_str), Some("build"));

        // Verify "." parent fallback for bare filename
        let bare = VariableSubstitutor::build_file_context(Some(Path::new("a.go")), None, None);
        assert_eq!(bare.get("dir").map(String::as_str), Some("."));
        assert_eq!(bare.get("parent").map(String::as_str), Some("."));
    }

    /// Port of `dev:tests/test_substitutions.py::test_substitute_string_and_env`.
    #[test]
    fn test_substitute_string_and_env() {
        let context = HashMap::from([
            ("name".to_string(), "app".to_string()),
            ("out".to_string(), "bin/app".to_string()),
        ]);
        let lookup = |k: &str| match k {
            "TEST_PORT" => Some("9000".to_string()),
            _ => None,
        };

        let s1 = VariableSubstitutor::substitute_string_with_env(
            "run ${name} on port ${env:TEST_PORT} -> ${out} ${unknown}",
            &context,
            false,
            lookup,
        );
        assert_eq!(s1, "run app on port 9000 -> bin/app ${unknown}");

        let s2 = VariableSubstitutor::substitute_string_with_env(
            "run {name} on port {env:TEST_PORT} -> {out} {env:MISSING}",
            &context,
            false,
            lookup,
        );
        assert_eq!(s2, "run app on port 9000 -> bin/app ");
    }

    /// Port of `dev:tests/test_substitutions.py::test_build_multi_file_context`.
    #[test]
    fn test_build_multi_file_context() {
        let files = vec![
            PathBuf::from("a.go"),
            PathBuf::from("b.go"),
            PathBuf::from("c space.go"),
        ];
        let out = Path::new("bin/app");
        let ctx = VariableSubstitutor::build_multi_file_context(&files, Some(out), None);
        assert_eq!(ctx.get("file").map(String::as_str), Some("a.go"));
        let all_files = ctx.get("files").expect("files key");
        assert!(all_files.contains("a.go"));
        assert!(all_files.contains("b.go"));
        assert!(all_files.contains("'c space.go'"));
        assert_eq!(ctx.get("out").map(String::as_str), Some("bin/app"));
    }

    /// Port of `dev:tests/test_substitutions.py::test_substitute_list`.
    #[test]
    fn test_substitute_list() {
        let context = HashMap::from([
            ("out_dir".to_string(), "target".to_string()),
            ("stem".to_string(), "server".to_string()),
        ]);
        let template = vec![
            "-o".to_string(),
            "${out_dir}/${stem}.bin".to_string(),
            "-DNAME={stem}".to_string(),
        ];
        let result = VariableSubstitutor::substitute_list(&template, &context, false);
        assert_eq!(
            result,
            vec!["-o", "target/server.bin", "-DNAME=server"]
        );
    }

    /// Port of `dev:tests/test_substitutions.py::test_env_var_substitution_with_spaces_no_quotes`.
    #[test]
    fn test_env_var_substitution_with_spaces_no_quotes() {
        let context = HashMap::new();
        let lookup = |k: &str| match k {
            "SPACED_VAR" => Some("hello world".to_string()),
            _ => None,
        };
        let result = VariableSubstitutor::substitute_string_with_env(
            "prefix ${env:SPACED_VAR} suffix",
            &context,
            false,
            lookup,
        );
        assert_eq!(result, "prefix hello world suffix");

        let list_result = VariableSubstitutor::substitute_list_with_env(
            &["--opt=${env:SPACED_VAR}".to_string()],
            &context,
            false,
            lookup,
        );
        assert_eq!(list_result, vec!["--opt=hello world"]);
    }

    /// Port of `dev:tests/test_substitutions.py::test_substitutions_path_with_spaces_quoted`.
    #[test]
    fn test_substitutions_path_with_spaces_quoted() {
        let src = Path::new("my workspace/main code.cpp");
        let out = Path::new("build output/app binary.out");
        let ctx =
            VariableSubstitutor::build_file_context(Some(src), Some(out), Some("build output"));

        let expanded = VariableSubstitutor::substitute_string(
            "g++ -I${dir} ${file} -o ${out}",
            &ctx,
            false,
        );
        let tokens = shlex::split(&expanded).expect("valid shlex output");
        assert_eq!(
            tokens,
            vec![
                "g++",
                "-Imy workspace",
                "my workspace/main code.cpp",
                "-o",
                "build output/app binary.out",
            ]
        );
    }

    /// Port of `dev:tests/test_substitutions.py::test_substitutions_files_consistently_quoted`.
    #[test]
    fn test_substitutions_files_consistently_quoted() {
        let files = vec![
            PathBuf::from("normal.c"),
            PathBuf::from("has space.c"),
            PathBuf::from("special$name.c"),
        ];
        let ctx = VariableSubstitutor::build_multi_file_context(&files, None, None);
        let tokens = shlex::split(ctx.get("files").expect("files")).expect("valid shlex");
        assert_eq!(tokens, vec!["normal.c", "has space.c", "special$name.c"]);
    }

    /// Port of `dev:tests/test_substitutions.py::test_env_var_substitution_dangerous_blocked`.
    #[test]
    fn test_env_var_substitution_dangerous_blocked() {
        let context = HashMap::new();
        let lookup = |k: &str| match k {
            "LD_PRELOAD" => Some("/lib/evil.so".to_string()),
            "DYLD_INSERT_LIBRARIES" => Some("/lib/evil.dylib".to_string()),
            "LD_CUSTOM" => Some("bad".to_string()),
            "BASH_FUNC_x" => Some("() { :; }".to_string()),
            _ => None,
        };
        assert_eq!(
            VariableSubstitutor::substitute_string_with_env(
                "run ${env:LD_PRELOAD}",
                &context,
                false,
                lookup,
            ),
            "run "
        );
        assert_eq!(
            VariableSubstitutor::substitute_string_with_env(
                "run ${env:DYLD_INSERT_LIBRARIES}",
                &context,
                false,
                lookup,
            ),
            "run "
        );
        assert_eq!(
            VariableSubstitutor::substitute_string_with_env(
                "run ${env:LD_CUSTOM} ${env:BASH_FUNC_x}",
                &context,
                false,
                lookup,
            ),
            "run  "
        );
    }

    /// Port of `dev:tests/test_substitutions.py::test_env_var_substitution_sanitizes_control_chars_and_quotes`.
    #[test]
    fn test_env_var_substitution_sanitizes_control_chars_and_quotes() {
        let context = HashMap::new();
        let lookup = |k: &str| match k {
            "INJECT_VAR" => Some("foo\r\n\0baz".to_string()),
            "SPACED_OPT" => Some("val with spaces".to_string()),
            _ => None,
        };
        assert_eq!(
            VariableSubstitutor::substitute_string_with_env(
                "${env:INJECT_VAR}",
                &context,
                false,
                lookup,
            ),
            "foobaz"
        );
        assert_eq!(
            VariableSubstitutor::substitute_string_with_env(
                "cmd ${env:SPACED_OPT}",
                &context,
                true,
                lookup,
            ),
            "cmd 'val with spaces'"
        );
    }
}
