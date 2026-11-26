//! Snippet evaluator (`-e`/`--eval`) and interactive scratchpad REPL (`--repl <LANG>`).

use std::fs;
use std::io::{self, Write};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::core::engine::CompilerRunner;
use crate::model::error::RunError;

/// Resolve a language identifier (e.g. `c`, `cpp`, `rs`, `py`, `go`, `js`, `sh`) to its file extension.
pub fn lang_to_extension(lang: &str) -> Result<&'static str, RunError> {
    match lang.trim().to_ascii_lowercase().as_str() {
        "c" => Ok("c"),
        "cpp" | "c++" | "cxx" | "cc" => Ok("cpp"),
        "rs" | "rust" => Ok("rs"),
        "py" | "python" | "python3" => Ok("py"),
        "go" | "golang" => Ok("go"),
        "js" | "node" | "javascript" => Ok("js"),
        "sh" | "bash" | "shell" => Ok("sh"),
        other => Err(RunError::config(format!(
            "Unsupported --lang/--repl language '{other}'. Supported: c, cpp, rs, py, go, js, sh."
        ))),
    }
}

/// Auto-wrap a snippet in a `main` entrypoint when missing for compiled languages (`c`, `cpp`, `rs`, `go`).
pub fn wrap_snippet(lang: &str, code: &str) -> String {
    match lang.trim().to_ascii_lowercase().as_str() {
        "c" if !code.contains("main(") && !code.contains("main (") => {
            format!("#include <stdio.h>\n#include <stdlib.h>\nint main(void) {{\n{code}\nreturn 0;\n}}\n")
        }
        "cpp" | "c++" | "cxx" | "cc" if !code.contains("main(") && !code.contains("main (") => {
            format!("#include <iostream>\n#include <vector>\n#include <string>\nusing namespace std;\nint main() {{\n{code}\nreturn 0;\n}}\n")
        }
        "rs" | "rust" if !code.contains("fn main") => {
            format!("fn main() {{\n{code}\n}}\n")
        }
        "go" | "golang" if !code.contains("package ") => {
            format!("package main\nimport \"fmt\"\nfunc main() {{\n_ = fmt.Sprint()\n{code}\n}}\n")
        }
        _ => format!("{code}\n"),
    }
}

/// Evaluate a single code snippet by writing a temporary source file and executing via [`CompilerRunner`].
pub fn eval_snippet(
    runner: &mut CompilerRunner,
    lang: &str,
    code: &str,
) -> Result<bool, RunError> {
    let ext = lang_to_extension(lang)?;
    let wrapped = wrap_snippet(lang, code);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let tmp_dir = std::env::temp_dir().join(format!("run_eval_{}_{}", std::process::id(), nanos));
    fs::create_dir_all(&tmp_dir).map_err(RunError::Io)?;
    let src_path: PathBuf = tmp_dir.join(format!("snippet.{ext}"));
    fs::write(&src_path, wrapped).map_err(RunError::Io)?;

    let ok = runner.compile_and_run(&[src_path], false);
    runner.cleanup();
    let _ = fs::remove_dir_all(&tmp_dir);
    Ok(ok)
}

/// Run an interactive compile-and-eval REPL loop (`--repl <LANG>`).
pub fn run_repl(runner: &mut CompilerRunner, lang: &str) -> Result<i32, RunError> {
    let _ = lang_to_extension(lang)?;
    println!("run REPL ({lang}) — type ':q' to quit, ':clear' to reset buffer, end line with '\\' for multiline.");

    let stdin = io::stdin();
    let mut buffer = String::new();
    let mut line = String::new();

    loop {
        if buffer.is_empty() {
            print!("run[{lang}]> ");
        } else {
            print!("...> ");
        }
        let _ = io::stdout().flush();

        line.clear();
        let n = stdin.read_line(&mut line).map_err(RunError::Io)?;
        if n == 0 {
            break;
        }
        let trimmed = line.trim();
        if buffer.is_empty() && matches!(trimmed, ":q" | ":quit" | ":exit") {
            break;
        }
        if trimmed == ":clear" {
            buffer.clear();
            continue;
        }
        if trimmed.is_empty() && buffer.is_empty() {
            continue;
        }
        if let Some(stripped) = trimmed.strip_suffix('\\') {
            buffer.push_str(stripped);
            buffer.push('\n');
            continue;
        }

        buffer.push_str(&line);
        let open_braces = buffer.chars().filter(|&c| c == '{').count();
        let close_braces = buffer.chars().filter(|&c| c == '}').count();
        if open_braces > close_braces {
            continue;
        }

        let code = std::mem::take(&mut buffer);
        let _ = eval_snippet(runner, lang, &code)?;
    }

    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lang_and_wrap_snippet() {
        assert_eq!(lang_to_extension("c").unwrap(), "c");
        assert_eq!(lang_to_extension("rust").unwrap(), "rs");
        assert!(lang_to_extension("brainfuck").is_err());

        let c_wrapped = wrap_snippet("c", "printf(\"hi\\n\");");
        assert!(c_wrapped.contains("int main(void)"));
        assert!(c_wrapped.contains("printf(\"hi\\n\");"));

        let c_full = wrap_snippet("c", "int main() { return 0; }");
        assert!(!c_full.contains("#include <stdio.h>"));

        let rs_wrapped = wrap_snippet("rs", "println!(\"ok\");");
        assert!(rs_wrapped.contains("fn main()"));
    }
}
