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

use crate::util::errors::RunError;

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

/// Cache directory layout and hashing defaults from `[package.metadata.run.cache]`.
#[derive(Debug, Clone, Deserialize)]
pub struct CacheMetadata {
    /// Metadata filename inside each cache bucket (`cache.json`).
    pub metadata_file: String,
    /// Prefix length of SHA-256 key used for cache bucket directories.
    pub hash_prefix_len: usize,
    /// Object files subdirectory name (`objs`).
    pub objs_dir: String,
    /// Compiled binary subdirectory name (`bin`).
    pub bin_dir: String,
    /// Preprocessed source subdirectory name (`src`).
    pub src_dir: String,
    /// Streaming read chunk size in bytes for file hashing.
    pub hash_chunk_bytes: usize,
    /// Maximum header bytes scanned when detecting shebangs.
    pub shebang_head_bytes: usize,
}

/// Default exclusion lists from `[package.metadata.run.excludes]`.
#[derive(Debug, Clone, Deserialize)]
pub struct ExcludesMetadata {
    /// Default file extensions ignored during discovery and compilation.
    pub extensions: Vec<String>,
    /// Default filenames ignored during discovery and compilation.
    pub files: Vec<String>,
    /// Default directory names ignored during compilation walks.
    pub dirs: Vec<String>,
    /// Default directory names ignored during `-L` / `--init` discovery.
    pub discovery_ignore_dirs: Vec<String>,
    /// Default extensions excluded during `--init` scanning.
    pub init_extensions: Vec<String>,
}

/// Default compiler and interpreter binaries from `[package.metadata.run.default_runners]`.
#[derive(Debug, Clone, Deserialize)]
pub struct DefaultRunnersMetadata {
    /// Default C compiler (`gcc`).
    pub c: String,
    /// Default C++ compiler (`g++`).
    pub cpp: String,
    /// Default standalone Rust compiler (`rustc`).
    pub rust: String,
    /// Default Cargo binary (`cargo`).
    pub cargo: String,
    /// Default Java compiler (`javac`).
    pub java: String,
    /// Default Java runtime (`java`).
    pub java_runtime: String,
    /// Ordered Python interpreter candidates (`python`, `python3`).
    pub python: Vec<String>,
    /// Ordered shell interpreter candidates (`bash`, `sh`).
    pub sh: Vec<String>,
    /// Ordered Ruby interpreter candidates.
    pub ruby: Vec<String>,
    /// Ordered Node.js interpreter candidates.
    pub node: Vec<String>,
    /// Ordered Perl interpreter candidates.
    pub perl: Vec<String>,
    /// Ordered Lua interpreter candidates.
    pub lua: Vec<String>,
}

/// Default language file extension mappings from `[package.metadata.run.extensions]`.
#[derive(Debug, Clone, Deserialize)]
pub struct ExtensionsMetadata {
    /// C source file extensions.
    pub c: Vec<String>,
    /// C++ source file extensions.
    pub cpp: Vec<String>,
    /// C/C++ header file extensions.
    pub c_headers: Vec<String>,
    /// Java source file extensions.
    pub java: Vec<String>,
    /// Compiled language extensions eligible for test/watch compilation.
    pub compiled: Vec<String>,
    /// Virtual environment directory names scanned for Python projects.
    pub python_venv_dirs: Vec<String>,
}

/// Default sanitizer and leak-checker flags from `[package.metadata.run.flags]`.
#[derive(Debug, Clone, Deserialize)]
pub struct FlagsMetadata {
    /// Address/UndefinedBehavior sanitizer flags.
    pub asan: Vec<String>,
    /// ThreadSanitizer flags.
    pub tsan: Vec<String>,
    /// Valgrind leak-checking flags.
    pub valgrind: Vec<String>,
}

/// Built-in project detector entry from `[[package.metadata.run.default_projects]]`.
#[derive(Debug, Clone, Deserialize)]
pub struct DefaultProjectMetadata {
    /// Project detector identifier (e.g. `cargo`, `cmake`).
    pub name: String,
    /// Manifest filename or glob pattern.
    pub file: String,
    /// Single-step run command, if applicable.
    #[serde(default)]
    pub command: Option<String>,
    /// Build step command for two-step projects.
    #[serde(default)]
    pub build: Option<String>,
    /// Execution step command for two-step projects.
    #[serde(default)]
    pub run: Option<String>,
}

/// Built-in custom language entry from `[[package.metadata.run.builtin_languages]]`.
#[derive(Debug, Clone, Deserialize)]
pub struct BuiltinLanguageMetadata {
    /// Unique language name (e.g. `go`).
    pub name: String,
    /// Supported extensions (e.g. `[".go"]`).
    pub extensions: Vec<String>,
    /// Runner binary, if configured.
    #[serde(default)]
    pub runner: Option<String>,
    /// Optional subcommand passed before flags.
    #[serde(default)]
    pub subcommand: Option<String>,
    /// Handler mode (`"compiler"` or `"interpreter"`).
    #[serde(default, rename = "type")]
    pub lang_type: Option<String>,
    /// Default compiler/interpreter flags.
    #[serde(default)]
    pub flags: Vec<String>,
    /// Default runtime arguments.
    #[serde(default)]
    pub arguments: Vec<String>,
    /// Compile command template.
    #[serde(default)]
    pub compile: Option<String>,
    /// Two-step build command template.
    #[serde(default)]
    pub build: Option<String>,
    /// Single command template.
    #[serde(default)]
    pub command: Option<String>,
    /// Two-step run command template.
    #[serde(default)]
    pub run: Option<String>,
}

/// Container image heuristic rule from `[[package.metadata.run.sandbox.image_heuristics]]`.
#[derive(Debug, Clone, Deserialize)]
pub struct ImageHeuristicMetadata {
    /// Container image name (e.g. `gcc:latest`).
    pub image: String,
    /// Executable names that map to this image.
    pub executables: Vec<String>,
    /// File extensions that map to this image.
    pub extensions: Vec<String>,
}

/// Container sandbox defaults from `[package.metadata.run.sandbox]`.
#[derive(Debug, Clone, Deserialize)]
pub struct SandboxMetadata {
    /// Fallback container image when no heuristic matches.
    pub default_image: String,
    /// Default service name for docker-compose sandboxes.
    pub compose_default_service: String,
    /// Prefix for built sandbox image tags.
    pub image_prefix: String,
    /// Prefix for persistent watch-mode sandbox containers.
    pub persist_prefix: String,
    /// Hash prefix length for Dockerfile image tags.
    pub dockerfile_hash_len: usize,
    /// Probe timeout in seconds for container runtime availability checks.
    pub probe_timeout_secs: u64,
    /// Cleanup timeout in seconds for stopping containers.
    pub cleanup_timeout_secs: u64,
    /// Ordered image selection heuristics.
    pub image_heuristics: Vec<ImageHeuristicMetadata>,
}

/// Numeric limits, polling intervals, and buffer sizes from `[package.metadata.run.limits]`.
#[derive(Debug, Clone, Deserialize)]
pub struct LimitsMetadata {
    /// Linux procfs polling interval in milliseconds.
    pub procfs_poll_interval_ms: u64,
    /// Memory sampler thread join timeout in milliseconds.
    pub sampler_join_timeout_ms: u64,
    /// Graceful termination wait period before SIGKILL in milliseconds.
    pub kill_grace_period_ms: u64,
    /// Interrupt wait period in milliseconds.
    pub interrupt_grace_period_ms: u64,
    /// Windows `taskkill` timeout in seconds.
    pub taskkill_timeout_secs: u64,
    /// Post-kill wait timeout in seconds.
    pub wait_after_kill_secs: u64,
    /// `--doctor` tool probe timeout in seconds.
    pub doctor_probe_timeout_secs: u64,
    /// `--watch` polling interval in milliseconds.
    pub watch_poll_interval_ms: u64,
    /// Maximum parallel compilation jobs cap.
    pub max_parallel_jobs_cap: usize,
    /// Extra jobs added beyond logical CPU count.
    pub parallel_jobs_extra: usize,
    /// Maximum cached compiled glob regex entries.
    pub glob_cache_size: usize,
    /// Buffer read size in bytes for procfs status files.
    pub procfs_read_bytes: usize,
    /// Maximum header lines scanned for inline `// run:` / `// flags:` directives.
    pub inline_directive_scan_lines: usize,
    /// Maximum lines scanned for C/C++ shebang language detection.
    pub shebang_c_detect_lines: usize,
}

/// Security allowlists, blocklists, and suspicious compiler flag patterns from `[package.metadata.run.security]`.
#[derive(Debug, Clone, Deserialize)]
pub struct SecurityMetadata {
    /// Exact environment variable names blocked from `.env` and template expansion.
    pub dangerous_env_vars: Vec<String>,
    /// Environment variable prefixes blocked (`DYLD_`, `LD_`, `BASH_FUNC_`).
    pub dangerous_env_prefixes: Vec<String>,
    /// Minimal safe environment variables preserved during sanitized execution.
    pub safe_env_whitelist: Vec<String>,
    /// Compiler flag substrings flagged as potentially unsafe without `--unsafe`.
    pub suspicious_flag_substrings: Vec<String>,
    /// Exact compiler flags flagged as potentially unsafe without `--unsafe`.
    pub suspicious_flag_exact: Vec<String>,
}

/// Static package defaults parsed once from `Cargo.toml` `[package.metadata.run]`.
#[derive(Debug, Clone, Deserialize)]
pub struct PackageMetadata {
    /// Configuration directory name under `$XDG_CONFIG_HOME` / `~/.config` / `%APPDATA%`.
    pub config_dir_name: String,
    /// Cache directory name under `$XDG_CACHE_HOME` / `~/.cache` / `%LOCALAPPDATA%`.
    pub cache_dir_name: String,
    /// Configuration filename (`Run.toml`).
    pub config_file: String,
    /// Dotenv filename (`.env`).
    pub dotenv_file: String,
    /// Maximum directory levels climbed during `Run.toml` discovery.
    pub config_search_depth: usize,
    /// Maximum directory levels climbed during `Cargo.toml` discovery.
    pub cargo_search_depth: usize,
    /// Directory boundaries where upward manifest/config traversal stops.
    pub stop_dirs: Vec<String>,
    /// Cache layout and hashing defaults.
    pub cache: CacheMetadata,
    /// Default exclusion lists.
    pub excludes: ExcludesMetadata,
    /// Default compiler and interpreter names.
    pub default_runners: DefaultRunnersMetadata,
    /// Default source/header extension groups.
    pub extensions: ExtensionsMetadata,
    /// Default sanitizer and valgrind flags.
    pub flags: FlagsMetadata,
    /// Ordered default project detectors.
    pub default_projects: Vec<DefaultProjectMetadata>,
    /// Built-in custom language definitions (e.g. Go).
    pub builtin_languages: Vec<BuiltinLanguageMetadata>,
    /// Container sandbox configuration defaults.
    pub sandbox: SandboxMetadata,
    /// Operational limits and intervals.
    pub limits: LimitsMetadata,
    /// Security lists and suspicious flag patterns.
    pub security: SecurityMetadata,
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
        if is_dangerous_env_var(&key) {
            report.skipped_dangerous.push(key);
            continue;
        }
        if !overwrite && std::env::var_os(&key).is_some() {
            report.skipped_existing.push(key);
            continue;
        }
        // SAFETY: Called during single-threaded CLI startup before spawning worker
        // threads or child processes. Unit tests verify `.env` application logic
        // via pure `apply_dotenv_entries` rather than mutating process-global env.
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
}
