//! Gitignore-style glob pattern compilation, ReDoS protection, and manifest matching.
//!
//! Adapted from Python's `src/util/glob_matcher.py`, replacing `re` and
//! `functools.lru_cache` with the `regex` crate and a bounded cache keyed by
//! `(pattern, case_sensitive)` whose capacity comes from
//! [`crate::util::env::PackageMetadata::get`]. Path matching is strictly lexical
//! (no symlink dereferencing) so workspace-relative exclusions work for symlinks
//! pointing outside the workspace.

use std::collections::HashMap;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use regex::{Regex, RegexBuilder};

use crate::util::env::PackageMetadata;
use crate::util::errors::RunError;

/// Bounded cache storing compiled glob regular expressions.
static GLOB_CACHE: OnceLock<Mutex<HashMap<(String, bool), Regex>>> = OnceLock::new();

/// Static regex collapsing runs of 3 or more `*` into `**` to prevent ReDoS.
static MULTI_STAR_RE: OnceLock<Regex> = OnceLock::new();

/// Static regex collapsing repeated `**/` segments into a single `**/`.
static REPEAT_GLOBSTAR_PREFIX_RE: OnceLock<Regex> = OnceLock::new();

/// Static regex collapsing repeated `/**` segments into a single `/**`.
static REPEAT_GLOBSTAR_SUFFIX_RE: OnceLock<Regex> = OnceLock::new();

/// Return `true` if `pattern` contains any glob wildcard character (`*`, `?`, `[`).
pub fn has_glob_wildcard(pattern: &str) -> bool {
    pattern.contains('*') || pattern.contains('?') || pattern.contains('[')
}

/// Collapse redundant wildcards (`***` -> `**`, `**/**/` -> `**/`, `/**/**` -> `/**`)
/// using `regex::Regex` to eliminate exponential backtracking (ReDoS).
fn normalize_glob_wildcards(pattern: &str) -> String {
    let multi_star = MULTI_STAR_RE
        .get_or_init(|| Regex::new(r"\*{3,}").expect("valid static MULTI_STAR_RE"));
    let repeat_prefix = REPEAT_GLOBSTAR_PREFIX_RE
        .get_or_init(|| Regex::new(r"(?:\*\*/)+").expect("valid static REPEAT_GLOBSTAR_PREFIX_RE"));
    let repeat_suffix = REPEAT_GLOBSTAR_SUFFIX_RE
        .get_or_init(|| Regex::new(r"(?:/\*\*)+").expect("valid static REPEAT_GLOBSTAR_SUFFIX_RE"));

    let step1 = multi_star.replace_all(pattern, "**");
    let step2 = repeat_prefix.replace_all(&step1, "**/");
    repeat_suffix.replace_all(&step2, "/**").into_owned()
}

/// Translate a single `[...]` character class slice into Rust `regex` syntax.
///
/// Mirrors Python `fnmatch`/`glob_matcher.py` semantics:
/// - Leading `!` becomes `^` (negated class).
/// - Leading `^` is escaped as `\^` (literal caret in glob syntax).
/// - Inner `\` and `]` (such as in `[]a]` or `[!]]`) are escaped for `regex-syntax`.
fn format_char_class(raw_slice: &str) -> String {
    let mut out = String::with_capacity(raw_slice.len() + 4);
    let mut chars = raw_slice.chars();
    if let Some(first) = chars.next() {
        match first {
            '!' => out.push('^'),
            '^' => out.push_str(r"\^"),
            '\\' => out.push_str(r"\\"),
            ']' => out.push_str(r"\]"),
            other => out.push(other),
        }
    }
    for ch in chars {
        match ch {
            '\\' => out.push_str(r"\\"),
            ']' => out.push_str(r"\]"),
            other => out.push(other),
        }
    }
    format!("[{out}]")
}

/// Compile a gitignore-style glob pattern into a [`regex::Regex`].
///
/// Supports `*` within directory boundaries, `?` for a single non-`/` character,
/// `[...]` character classes, `**/` for zero or more directory segments, and
/// `**` across directory boundaries. Results are cached up to
/// [`crate::util::env::LimitsMetadata::glob_cache_size`].
pub fn compile_glob(pattern: &str, case_sensitive: bool) -> Result<Regex, RunError> {
    let cache = GLOB_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let key = (pattern.to_string(), case_sensitive);
    if let Ok(guard) = cache.lock()
        && let Some(re) = guard.get(&key)
    {
        return Ok(re.clone());
    }

    let mut clean_pat = pattern.trim();
    if let Some(stripped) = clean_pat.strip_prefix('/') {
        clean_pat = stripped;
    }
    if let Some(stripped) = clean_pat.strip_suffix('/') {
        clean_pat = stripped;
    }

    let collapsed = normalize_glob_wildcards(clean_pat);
    let chars: Vec<char> = collapsed.chars().collect();
    let n = chars.len();
    let mut i = 0;
    let mut regex_src = String::with_capacity(n * 2 + 2);
    regex_src.push('^');

    while i < n {
        let c = chars[i];
        i += 1;
        match c {
            '*' => {
                if i < n && chars[i] == '*' {
                    i += 1;
                    if i < n && chars[i] == '/' {
                        i += 1;
                        regex_src.push_str("(?:[^/]+/)*");
                    } else {
                        regex_src.push_str(".*");
                    }
                } else {
                    regex_src.push_str("[^/]*");
                }
            }
            '?' => regex_src.push_str("[^/]"),
            '[' => {
                let mut j = i;
                if j < n && chars[j] == '!' {
                    j += 1;
                }
                if j < n && chars[j] == ']' {
                    j += 1;
                }
                while j < n && chars[j] != ']' {
                    j += 1;
                }
                if j >= n {
                    regex_src.push_str(r"\[");
                } else {
                    let raw_slice: String = chars[i..j].iter().collect();
                    i = j + 1;
                    regex_src.push_str(&format_char_class(&raw_slice));
                }
            }
            other => {
                let mut buf = [0u8; 4];
                let s = other.encode_utf8(&mut buf);
                regex_src.push_str(&regex::escape(s));
            }
        }
    }

    regex_src.push('$');
    let compiled = RegexBuilder::new(&regex_src)
        .case_insensitive(!case_sensitive)
        .build()
        .map_err(|err| RunError::Config(format!("Invalid glob pattern '{pattern}': {err}")))?;

    let max_entries = PackageMetadata::get().limits.glob_cache_size;
    if let Ok(mut guard) = cache.lock() {
        if guard.len() >= max_entries {
            guard.clear();
        }
        guard.insert(key, compiled.clone());
    }

    Ok(compiled)
}

/// Normalize `path` lexically (collapsing `.` and `..` without resolving symlinks).
fn lexical_normalize(path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };

    let mut stack: Vec<Component<'_>> = Vec::new();
    for comp in joined.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => match stack.last() {
                Some(Component::Normal(_)) => {
                    stack.pop();
                }
                Some(Component::RootDir | Component::Prefix(_)) => {}
                _ => stack.push(comp),
            },
            other => stack.push(other),
        }
    }

    let mut out = PathBuf::new();
    for comp in stack {
        out.push(comp.as_os_str());
    }
    out
}

/// Convert a [`Path`] to a forward-slash POSIX-style string.
fn to_posix_string(path: &Path) -> String {
    let mut parts = Vec::new();
    for comp in path.components() {
        match comp {
            Component::Prefix(p) => parts.push(p.as_os_str().to_string_lossy().into_owned()),
            Component::RootDir => {}
            Component::CurDir => parts.push(".".to_string()),
            Component::ParentDir => parts.push("..".to_string()),
            Component::Normal(s) => parts.push(s.to_string_lossy().into_owned()),
        }
    }
    if path.is_absolute() && parts.is_empty() {
        "/".to_string()
    } else if path.is_absolute() {
        format!("/{}", parts.join("/"))
    } else {
        parts.join("/")
    }
}

/// Compute a lexical relative POSIX path from `root` to `path` without dereferencing symlinks.
fn lexical_relpath(path: &Path, root: &Path) -> String {
    let norm_path = lexical_normalize(path);
    let norm_root = lexical_normalize(root);

    let p_comps: Vec<Component<'_>> = norm_path.components().collect();
    let r_comps: Vec<Component<'_>> = norm_root.components().collect();

    // Different Windows drive prefixes cannot be made relative; fall back to stripped POSIX path.
    if matches!(
        (p_comps.first(), r_comps.first()),
        (Some(Component::Prefix(a)), Some(Component::Prefix(b))) if a != b
    ) {
        return to_posix_string(path).trim_start_matches('/').to_string();
    }

    let mut common = 0;
    while common < p_comps.len() && common < r_comps.len() && p_comps[common] == r_comps[common] {
        common += 1;
    }

    let mut rel_parts = Vec::new();
    for _ in common..r_comps.len() {
        rel_parts.push("..".to_string());
    }
    for comp in &p_comps[common..] {
        if let Component::Normal(s) = comp {
            rel_parts.push(s.to_string_lossy().into_owned());
        }
    }

    if rel_parts.is_empty() {
        ".".to_string()
    } else {
        rel_parts.join("/")
    }
}

/// Match `file_path` against a gitignore-style `pattern`.
///
/// Patterns without `/` match only the basename of `file_path`. Patterns
/// containing `/` match against the lexical relative path from `root_dir` (or
/// the forward-slash path with leading `/` stripped when `root_dir` is `None`).
pub fn match_path(
    file_path: &Path,
    pattern: &str,
    root_dir: Option<&Path>,
    case_sensitive: bool,
) -> bool {
    let clean_pat = pattern.trim();
    if clean_pat.is_empty() {
        return false;
    }

    let norm_pat = clean_pat.trim_end_matches('/');
    let target = if norm_pat.contains('/') {
        if let Some(root) = root_dir {
            lexical_relpath(file_path, root)
        } else {
            to_posix_string(file_path)
                .trim_start_matches('/')
                .to_string()
        }
    } else {
        file_path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    };

    compile_glob(clean_pat, case_sensitive)
        .map(|re| re.is_match(&target))
        .unwrap_or(false)
}

/// Match a file extension against an extension or glob pattern.
///
/// Supports `".ext"`, `"ext"`, `"*.ext"`, and wildcards such as `"*.tmp*"`.
pub fn match_extension(ext: &str, pattern: &str, case_sensitive: bool) -> bool {
    if ext.is_empty() || pattern.is_empty() {
        return false;
    }

    let ext_norm = if ext.starts_with('.') {
        ext.to_string()
    } else {
        format!(".{ext}")
    };

    let pat = pattern.trim();
    if pat.is_empty() {
        return false;
    }

    let normalized_pat = if pat.starts_with('*') || pat.starts_with('.') {
        pat.to_string()
    } else {
        format!(".{pat}")
    };

    compile_glob(&normalized_pat, case_sensitive)
        .map(|re| re.is_match(&ext_norm))
        .unwrap_or(false)
}

/// Find direct regular files within `directory` matching `pattern`, sorted alphabetically by filename.
pub fn find_matching_manifests(
    directory: &Path,
    pattern: &str,
    case_sensitive: bool,
) -> Vec<PathBuf> {
    if !directory.is_dir() {
        return Vec::new();
    }

    let Ok(entries) = fs::read_dir(directory) else {
        return Vec::new();
    };

    let mut matches: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_file() && match_path(p, pattern, Some(directory), case_sensitive))
        .collect();

    matches.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
    matches
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Instant, SystemTime, UNIX_EPOCH};

    /// Create an isolated temporary directory path for glob tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock went backwards")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_glob_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp test dir");
        dir
    }

    /// Port of `dev:tests/test_glob_config.py::test_has_glob_wildcard`.
    #[test]
    fn test_has_glob_wildcard() {
        assert!(has_glob_wildcard("*.sln"));
        assert!(has_glob_wildcard("test_?.c"));
        assert!(has_glob_wildcard("[a-z].py"));
        assert!(!has_glob_wildcard("Makefile"));
        assert!(!has_glob_wildcard("CMakeLists.txt"));
    }

    /// Port of `dev:tests/test_glob_config.py::test_match_path_filename_glob`.
    #[test]
    fn test_match_path_filename_glob() {
        assert!(match_path(Path::new("foo/bar/temp.tmp"), "*.tmp", None, true));
        assert!(match_path(
            Path::new("foo/bar/test_case.c"),
            "test_*",
            None,
            true
        ));
        assert!(match_path(Path::new("secret.py"), "secret.py", None, true));
        assert!(!match_path(Path::new("foo/bar/temp.c"), "*.tmp", None, true));
        assert!(!match_path(
            Path::new("foo/bar/prod_case.c"),
            "test_*",
            None,
            true
        ));
    }

    /// Port of `dev:tests/test_glob_config.py::test_match_path_path_glob_with_slash`.
    #[test]
    fn test_match_path_path_glob_with_slash() {
        let root = Path::new("/workspace/project");

        assert!(match_path(
            &root.join("tests/test1.c"),
            "tests/*",
            Some(root),
            true
        ));
        assert!(!match_path(
            &root.join("tests/sub/test1.c"),
            "tests/*",
            Some(root),
            true
        ));

        assert!(match_path(
            &root.join("src/vendor/lib.c"),
            "src/vendor/**",
            Some(root),
            true
        ));
        assert!(match_path(
            &root.join("src/vendor/sub/lib.c"),
            "src/vendor/**",
            Some(root),
            true
        ));
        assert!(match_path(
            &root.join("tests/a/b/mock_test.c"),
            "tests/**/mock_*.c",
            Some(root),
            true
        ));
        assert!(!match_path(
            &root.join("src/a/b/mock_test.c"),
            "tests/**/mock_*.c",
            Some(root),
            true
        ));
    }

    /// Port of `dev:tests/test_glob_config.py::test_match_path_case_sensitivity`.
    #[test]
    fn test_match_path_case_sensitivity() {
        assert!(match_path(Path::new("test.TMP"), "*.TMP", None, true));
        assert!(!match_path(Path::new("test.TMP"), "*.tmp", None, true));
        assert!(match_path(Path::new("test.TMP"), "*.tmp", None, false));
    }

    /// Port of `dev:tests/test_glob_config.py::test_match_extension_normalization`.
    #[test]
    fn test_match_extension_normalization() {
        assert!(match_extension(".c", ".c", true));
        assert!(match_extension(".c", "c", true));
        assert!(match_extension(".c", "*.c", true));
        assert!(match_extension(".cpp", "*.c*", true));
        assert!(match_extension(".tmp1", ".tmp*", true));
        assert!(match_extension(".tmp2", "tmp*", true));
        assert!(match_extension(".tmp3", "*.tmp*", true));
        assert!(!match_extension(".c", ".cpp", true));
        assert!(!match_extension(".c", "cpp", true));
        assert!(!match_extension(".C", ".c", true));
        assert!(match_extension(".C", ".c", false));
    }

    /// Port of `dev:tests/test_glob_config.py::test_find_matching_manifests`.
    #[test]
    fn test_find_matching_manifests() {
        let dir = make_temp_dir("manifests");
        fs::write(dir.join("SolutionB.sln"), "").unwrap();
        fs::write(dir.join("SolutionA.sln"), "").unwrap();
        fs::write(dir.join("Other.txt"), "").unwrap();

        let matches = find_matching_manifests(&dir, "*.sln", true);
        assert_eq!(matches.len(), 2);
        assert_eq!(matches[0].file_name().unwrap(), "SolutionA.sln");
        assert_eq!(matches[1].file_name().unwrap(), "SolutionB.sln");

        assert!(find_matching_manifests(&dir.join("missing"), "*.sln", true).is_empty());
        let _ = fs::remove_dir_all(&dir);
    }

    /// Port of `dev:tests/test_glob_config.py::test_compile_glob_redos_prevention`.
    #[test]
    fn test_compile_glob_redos_prevention() {
        let pattern = "**/**/**/**/**/**/**/target.txt";
        let non_matching_path = format!("{}/mismatch.txt", vec!["segment"; 25].join("/"));

        let start = Instant::now();
        let re = compile_glob(pattern, true).expect("compile glob");
        let matched = re.is_match(&non_matching_path);
        let elapsed = start.elapsed();

        assert!(!matched);
        assert!(elapsed.as_secs_f64() < 0.1);
        assert!(!match_path(Path::new(&non_matching_path), pattern, None, true));
        assert!(match_path(
            Path::new("a/b/c/target.txt"),
            pattern,
            None,
            true
        ));
    }

    /// Port of `dev:tests/test_glob_config.py::test_match_path_external_symlink_exclusion`.
    #[cfg(unix)]
    #[test]
    fn test_match_path_external_symlink_exclusion() {
        let root = make_temp_dir("symlink");
        let workspace = root.join("workspace");
        let vendor = workspace.join("vendor");
        let outside = root.join("outside");
        fs::create_dir_all(&vendor).unwrap();
        fs::create_dir_all(&outside).unwrap();

        let outside_file = outside.join("external_lib.c");
        fs::write(&outside_file, "void external() {}").unwrap();

        let symlink_path = vendor.join("lib.c");
        std::os::unix::fs::symlink(&outside_file, &symlink_path).unwrap();

        assert!(match_path(&symlink_path, "vendor/**", Some(&workspace), true));
        assert!(match_path(
            &symlink_path,
            "vendor/*.c",
            Some(&workspace),
            true
        ));
        assert!(match_path(
            &symlink_path,
            "vendor/lib.c",
            Some(&workspace),
            true
        ));
        assert!(!match_path(&symlink_path, "src/**", Some(&workspace), true));

        let _ = fs::remove_dir_all(&root);
    }

    /// Verify character class edge cases (`[a-z]`, `[!a-z]`, `[]a]`, `[!]]`, unclosed `[`).
    #[test]
    fn test_glob_character_classes() {
        assert!(match_path(Path::new("b.c"), "[a-z].c", None, true));
        assert!(!match_path(Path::new("a.c"), "[!a-z].c", None, true));
        assert!(match_path(Path::new("1.c"), "[!a-z].c", None, true));
        assert!(match_path(Path::new("].c"), "[]a].c", None, true));
        assert!(!match_path(Path::new("].c"), "[!]].c", None, true));
        assert!(match_path(Path::new("x.c"), "[!]].c", None, true));
        assert!(match_path(Path::new("[unclosed.c"), "[unclosed.c", None, true));
    }
}
