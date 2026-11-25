//! Compile-time `Cargo.toml` metadata defaults, version resolution, and `.env` loading.
//!
//! Uses `serde` + `toml` to deserialize static defaults stored under
//! `[package.metadata.run]` in `Cargo.toml` via `include_str!("../../Cargo.toml")`
//! and caches them in a [`std::sync::OnceLock`], avoiding hardcoded defaults in
//! Rust source code. Also provides a minimal, dependency-free `.env` parser and
//! loader that rejects dangerous environment variables.

use std::collections::BTreeMap;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::sync::OnceLock;

use serde::Deserialize;

use crate::model::error::RunError;
pub use crate::model::metadata::{
    BuiltinLanguageMetadata, CacheMetadata, DefaultProjectMetadata, DefaultRunnersMetadata,
    ExcludesMetadata, ExtensionsMetadata, FlagsMetadata, ImageHeuristicMetadata, LimitsMetadata,
    PackageMetadata, SandboxMetadata, SecurityMetadata,
};

/// Top-level `Cargo.toml` wrapper used only to extract `[package.metadata.run]`.
#[derive(Debug, Deserialize)]
struct CargoManifest {
    /// `[package]` section in `Cargo.toml`.
    package: CargoPackage,
}

/// `[package]` section subset needed for metadata extraction.
#[derive(Debug, Deserialize)]
struct CargoPackage {
    /// `[package.metadata]` table.
    metadata: CargoMetadata,
}

/// `[package.metadata]` wrapper containing `[package.metadata.run]`.
#[derive(Debug, Deserialize)]
struct CargoMetadata {
    /// `[package.metadata.run]` defaults table.
    run: PackageMetadata,
}

/// Embedded compile-time `Cargo.toml` contents so runtime defaults require no external manifest file.
const EMBEDDED_CARGO_TOML: &str = include_str!("../../Cargo.toml");

/// Cached singleton of deserialized `[package.metadata.run]` defaults.
static PACKAGE_METADATA: OnceLock<PackageMetadata> = OnceLock::new();

impl PackageMetadata {
    /// Return the static [`PackageMetadata`] parsed from embedded `Cargo.toml`.
    ///
    /// Uses [`OnceLock`] so deserialization via `toml` + `serde` occurs at most once.
    pub fn get() -> &'static Self {
        PACKAGE_METADATA.get_or_init(|| {
            let manifest: CargoManifest = toml::from_str(EMBEDDED_CARGO_TOML)
                .expect("embedded Cargo.toml must contain valid [package.metadata.run]");
            manifest.package.metadata.run
        })
    }

    /// Return `true` if `var` matches a dangerous environment variable name or prefix.
    pub fn is_dangerous_env_var(&self, var: &str) -> bool {
        let upper = var.to_ascii_uppercase();
        self.security.dangerous_env_vars.iter().any(|v| v == &upper)
            || self
                .security
                .dangerous_env_prefixes
                .iter()
                .any(|prefix| upper.starts_with(prefix))
    }
}

/// Return `true` if `var` is blocked by the security policy in [`PackageMetadata`].
pub fn is_dangerous_env_var(var: &str) -> bool {
    PackageMetadata::get().is_dangerous_env_var(var)
}

/// Return the compiled crate version string from `Cargo.toml`.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Read the version from an explicit `Cargo.toml` (or legacy `pyproject.toml`) path,
/// falling back to [`version()`] if the file is missing or lacks a version string.
///
/// Uses `toml::Value` parsing so any valid TOML manifest can be inspected without
/// hardcoding version strings.
pub fn resolve_version(manifest_path: Option<&Path>) -> String {
    if let Some(path) = manifest_path
        && let Ok(content) = fs::read_to_string(path)
        && let Ok(doc) = toml::from_str::<toml::Value>(&content)
    {
        if let Some(ver) = doc
            .get("package")
            .and_then(|p| p.get("version"))
            .and_then(toml::Value::as_str)
        {
            return ver.to_string();
        }
        if let Some(ver) = doc
            .get("project")
            .and_then(|p| p.get("version"))
            .and_then(toml::Value::as_str)
        {
            return ver.to_string();
        }
    }
    version().to_string()
}

/// Summary of entries processed when loading a `.env` file.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct DotenvReport {
    /// Variables accepted and applied (`KEY -> VALUE`).
    pub loaded: BTreeMap<String, String>,
    /// Variable names rejected because [`is_dangerous_env_var`] returned `true`.
    pub skipped_dangerous: Vec<String>,
    /// Variable names skipped because an existing value was preserved.
    pub skipped_existing: Vec<String>,
}

/// Parse `.env` text into `(key, value)` pairs.
///
/// Supports UTF-8 BOM stripping, blank/`#` comment lines, optional `export `
/// prefixes, double-quoted values with escape sequences (`\n`, `\r`, `\t`, `\"`,
/// `\\`, `\$`), single-quoted literal values, and unquoted values with inline
/// whitespace-preceded `#` comments.
pub fn parse_dotenv(content: &str) -> Vec<(String, String)> {
    let content = content.strip_prefix('\u{FEFF}').unwrap_or(content);
    let mut entries = Vec::new();

    for raw_line in content.lines() {
        let trimmed = raw_line.trim_start();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }

        let after_export = if let Some(rest) = trimmed.strip_prefix("export") {
            if rest.chars().next().is_some_and(char::is_whitespace) {
                rest.trim_start()
            } else {
                trimmed
            }
        } else {
            trimmed
        };

        let Some((raw_key, raw_val)) = after_export.split_once('=') else {
            continue;
        };
        let key = raw_key.trim();
        if key.is_empty() || key.chars().any(char::is_whitespace) || key.contains('\0') {
            continue;
        }

        let value = parse_dotenv_value(raw_val);
        if value.contains('\0') {
            continue;
        }
        entries.push((key.to_string(), value));
    }

    entries
}

/// Parse a single `.env` value segment after `=`.
fn parse_dotenv_value(raw_val: &str) -> String {
    let s = raw_val.trim_start();
    if let Some(rest) = s.strip_prefix('"') {
        let mut out = String::new();
        let mut chars = rest.chars();
        while let Some(ch) = chars.next() {
            match ch {
                '"' => break,
                '\\' => match chars.next() {
                    Some('n') => out.push('\n'),
                    Some('r') => out.push('\r'),
                    Some('t') => out.push('\t'),
                    Some('"') => out.push('"'),
                    Some('\\') => out.push('\\'),
                    Some('$') => out.push('$'),
                    Some(other) => {
                        out.push('\\');
                        out.push(other);
                    }
                    None => out.push('\\'),
                },
                other => out.push(other),
            }
        }
        out
    } else if let Some(rest) = s.strip_prefix('\'') {
        if let Some(end) = rest.find('\'') {
            rest[..end].to_string()
        } else {
            rest.to_string()
        }
    } else if s.starts_with('#') {
        String::new()
    } else {
        let mut prev_ws = false;
        let mut cut = s.len();
        for (idx, ch) in s.char_indices() {
            if ch == '#' && prev_ws {
                cut = idx;
                break;
            }
            prev_ws = ch.is_whitespace();
        }
        s[..cut].trim_end().to_string()
    }
}

/// Apply parsed `.env` entries into an explicit map without mutating process environment.
///
/// Used directly by unit tests for race-free verification under Rust 2024 edition
/// and to share the exact filtering logic with [`load_dotenv_with_overwrite`].
pub fn apply_dotenv_entries(
    entries: Vec<(String, String)>,
    env_map: &mut BTreeMap<String, String>,
    overwrite: bool,
) -> DotenvReport {
    let mut report = DotenvReport::default();

    for (key, val) in entries {
        if key.contains('\0') || val.contains('\0') {
            continue;
        }
        if is_dangerous_env_var(&key) {
            report.skipped_dangerous.push(key);
            continue;
        }
        if !overwrite && env_map.contains_key(&key) {
            report.skipped_existing.push(key);
            continue;
        }
        env_map.insert(key.clone(), val.clone());
        report.loaded.insert(key, val);
    }

    report
}

/// Load `.env` from `dir` into the current process environment without overwriting existing OS variables.
pub fn load_dotenv(dir: &Path) -> Result<DotenvReport, RunError> {
    load_dotenv_with_overwrite(dir, false)
}

/// Load `.env` from `dir` into the current process environment, optionally overwriting existing variables.
///
/// Variables matching [`is_dangerous_env_var`] are always skipped and recorded in
/// [`DotenvReport::skipped_dangerous`].
pub fn load_dotenv_with_overwrite(dir: &Path, overwrite: bool) -> Result<DotenvReport, RunError> {
    let dotenv_path = dir.join(&PackageMetadata::get().dotenv_file);
    let content = match fs::read_to_string(&dotenv_path) {
        Ok(text) => text,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(DotenvReport::default()),
        Err(err) => return Err(RunError::Io(err)),
    };

    let entries = parse_dotenv(&content);
    let mut report = DotenvReport::default();

    for (key, val) in entries {
        if key.contains('\0') || val.contains('\0') {
            continue;
        }
        if is_dangerous_env_var(&key) {
            report.skipped_dangerous.push(key);
            continue;
        }
        if !overwrite && std::env::var_os(&key).is_some() {
            report.skipped_existing.push(key);
            continue;
        }
        // SAFETY: Called during single-threaded CLI startup before spawning worker
        // threads or child processes, and both `key` and `val` are NUL-free. Unit
        // tests verify `.env` application logic via pure `apply_dotenv_entries`
        // rather than mutating process-global env.
        unsafe {
            std::env::set_var(&key, &val);
        }
        report.loaded.insert(key, val);
    }

    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Create an isolated temporary directory path for unit tests.
    fn make_temp_dir(label: &str) -> std::path::PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock went backwards")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_env_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp test dir");
        dir
    }

    /// Verify embedded `[package.metadata.run]` deserializes cleanly with expected defaults.
    #[test]
    fn test_package_metadata_loads_embedded_cargo_toml() {
        let meta = PackageMetadata::get();
        assert_eq!(meta.config_dir_name, "run");
        assert_eq!(meta.cache_dir_name, "run");
        assert_eq!(meta.config_file, "Run.toml");
        assert_eq!(meta.dotenv_file, ".env");
        assert_eq!(meta.config_search_depth, 4);
        assert_eq!(meta.cargo_search_depth, 3);
        let proj_names: Vec<&str> = meta
            .default_projects
            .iter()
            .map(|p| p.name.as_str())
            .collect();
        assert_eq!(proj_names, vec!["cargo", "go", "zig", "cmake", "make"]);
        assert!(meta.builtin_languages.iter().any(|l| l.name == "go"));
        assert!(is_dangerous_env_var("LD_PRELOAD"));
        assert!(is_dangerous_env_var("dyld_insert_libraries"));
        assert!(is_dangerous_env_var("LD_CUSTOM_HOOK"));
        assert!(is_dangerous_env_var("BASH_FUNC_evil"));
        assert!(!is_dangerous_env_var("APP_SECRET_KEY"));
    }

    /// Port of `dev:tests/test_version.py::test_version_from_file`.
    #[test]
    fn test_version_from_file() {
        let dir = make_temp_dir("ver_file");
        let manifest = dir.join("Cargo.toml");
        fs::write(&manifest, "[package]\nname = \"run\"\nversion = \"1.2.3\"\n")
            .expect("write manifest");
        assert_eq!(resolve_version(Some(&manifest)), "1.2.3");

        let pyproject = dir.join("pyproject.toml");
        fs::write(
            &pyproject,
            "[project]\nname = \"run\"\nversion = \"2.4.6\"\n",
        )
        .expect("write pyproject");
        assert_eq!(resolve_version(Some(&pyproject)), "2.4.6");
        let _ = fs::remove_dir_all(&dir);
    }

    /// Port of `dev:tests/test_version.py::test_version_missing_file`.
    #[test]
    fn test_version_missing_file() {
        let dir = make_temp_dir("ver_missing");
        let missing = dir.join("non_existent.toml");
        assert_eq!(resolve_version(Some(&missing)), env!("CARGO_PKG_VERSION"));
        assert_eq!(version(), env!("CARGO_PKG_VERSION"));
        let _ = fs::remove_dir_all(&dir);
    }

    /// Verify `.env` parser handles BOM, comments, `export`, quoting, escapes, and inline comments.
    #[test]
    fn test_parse_dotenv_quotes_comments_and_export() {
        let input = "\u{FEFF}\n# header comment\n\nexport API_KEY=\"line1\\nline2\" # trailing\nSINGLE='literal\\nvalue' # comment\nPLAIN=hello world # inline\nHASH_IN_VAL=abc#123\nexport_not_prefix=yes\nINVALID LINE\n=empty_key\n";
        let parsed = parse_dotenv(input);
        assert_eq!(
            parsed,
            vec![
                ("API_KEY".to_string(), "line1\nline2".to_string()),
                ("SINGLE".to_string(), "literal\\nvalue".to_string()),
                ("PLAIN".to_string(), "hello world".to_string()),
                ("HASH_IN_VAL".to_string(), "abc#123".to_string()),
                ("export_not_prefix".to_string(), "yes".to_string()),
            ]
        );
    }

    /// Verify dangerous env vars are blocked and existing OS env vars are preserved without mutating global env.
    #[test]
    fn test_apply_dotenv_filters_dangerous_and_preserves_existing() {
        let entries = parse_dotenv(
            "LD_PRELOAD=/evil.so\nDYLD_INSERT_LIBRARIES=/evil.dylib\nBASH_FUNC_x=bad\nEXISTING_VAR=from_dotenv\nNEW_SECRET=secret123\n",
        );
        let mut env_map = BTreeMap::new();
        env_map.insert("EXISTING_VAR".to_string(), "from_os".to_string());

        let report = apply_dotenv_entries(entries, &mut env_map, false);
        assert_eq!(
            report.skipped_dangerous,
            vec!["LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_FUNC_x"]
        );
        assert_eq!(report.skipped_existing, vec!["EXISTING_VAR"]);
        assert_eq!(report.loaded.len(), 1);
        assert_eq!(
            report.loaded.get("NEW_SECRET").map(String::as_str),
            Some("secret123")
        );
        assert_eq!(
            env_map.get("EXISTING_VAR").map(String::as_str),
            Some("from_os")
        );
        assert!(!env_map.contains_key("LD_PRELOAD"));
    }

    /// Verify missing `.env` returns an empty report without error.
    #[test]
    fn test_load_dotenv_missing_file_ok() {
        let dir = make_temp_dir("missing_dotenv");
        let report = load_dotenv(&dir).expect("missing .env should succeed");
        assert!(report.loaded.is_empty());
        assert!(report.skipped_dangerous.is_empty());
        let _ = fs::remove_dir_all(&dir);
    }

    /// Verify empty `.env` values followed by `#` comments parse as empty while unquoted `abc#123` stays intact.
    #[test]
    fn test_parse_dotenv_empty_and_inline_hash_comments() {
        let input = "FOO= # inline comment after empty\nBAR=#comment\nVAL=abc#123\nVAL_WS=abc # trailing\nQUOTED=\"#literal\"\n";
        let parsed = parse_dotenv(input);
        assert_eq!(
            parsed,
            vec![
                ("FOO".to_string(), String::new()),
                ("BAR".to_string(), String::new()),
                ("VAL".to_string(), "abc#123".to_string()),
                ("VAL_WS".to_string(), "abc".to_string()),
                ("QUOTED".to_string(), "#literal".to_string()),
            ]
        );
    }

    /// Verify `.env` entries containing NUL bytes in values are skipped without panicking.
    #[test]
    fn test_dotenv_nul_byte_in_value_skipped_without_panic() {
        let parsed = parse_dotenv(
            "PROBE_NUL_VAL=hello\0world\nPROBE_QUOTED_NUL=\"a\0b\"\nPROBE_AFTER_NUL=ok\n",
        );
        assert_eq!(
            parsed,
            vec![("PROBE_AFTER_NUL".to_string(), "ok".to_string())]
        );

        let mut env_map = BTreeMap::new();
        let report = apply_dotenv_entries(
            vec![
                ("DIRECT_NUL".to_string(), "bad\0val".to_string()),
                ("VALID".to_string(), "good".to_string()),
            ],
            &mut env_map,
            true,
        );
        assert_eq!(report.loaded.len(), 1);
        assert_eq!(report.loaded.get("VALID").map(String::as_str), Some("good"));
        assert!(!env_map.contains_key("DIRECT_NUL"));

        let dir = make_temp_dir("nul_dotenv");
        fs::write(
            dir.join(".env"),
            "PROBE_NUL_VAL=hello\0world\nPROBE_QUOTED_NUL=\"a\0b\"\n",
        )
        .expect("write .env with NUL");
        let load_report =
            load_dotenv_with_overwrite(&dir, true).expect("NUL .env should not panic or fail");
        assert!(load_report.loaded.is_empty());
        let _ = fs::remove_dir_all(&dir);
    }
}
