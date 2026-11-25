//! Configuration discovery, TOML schema validation, and defaults merging (`Run.toml`).
//!
//! Adapted from Python's `src/util/config.py`, using `toml` (with `preserve_order`)
//! and `serde` to parse `Run.toml` while sourcing all static defaults (search
//! depth, config directory/file names, stop boundaries, default projects, and
//! built-in languages like Go) from [`crate::util::env::PackageMetadata::get`].
//! Deprecated top-level aliases (`[runner]`, `[preset]`, `[language]`,
//! `[exclude]`) are explicitly rejected per R3.

use std::collections::{BTreeMap, HashSet};
use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

pub use crate::model::config::{
    Config, CoreConfig, CustomLanguageConfig, ExcludeConfig, PresetFlags, ProjectConfig,
    SandboxConfig, TaskConfig, TaskTable, TemplateConfig, TemplateFileEntry, TemplateTable,
};
use crate::model::error::RunError;
use crate::model::metadata::PackageMetadata;
use crate::util::glob::match_extension;
use crate::util::output::Printer;

/// Tracks config paths already logged via `[ INFO ] Loaded config: ...` in this process.
static LOGGED_CONFIG_PATHS: OnceLock<Mutex<HashSet<PathBuf>>> = OnceLock::new();

/// Resolve the global configuration directory for the active OS using [`PackageMetadata::get`].
pub fn global_config_dir() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        global_config_dir_from(
            std::env::var_os("APPDATA").as_deref(),
            std::env::var_os("USERPROFILE").as_deref(),
        )
    }
    #[cfg(not(windows))]
    {
        global_config_dir_from(
            std::env::var_os("XDG_CONFIG_HOME").as_deref(),
            std::env::var_os("HOME").as_deref(),
        )
    }
}

/// Pure helper computing the global configuration directory from explicit environment values.
///
/// Resolves to `$XDG_CONFIG_HOME/run` or `$HOME/.config/run` on Unix/macOS, and
/// `%APPDATA%\run` or `%USERPROFILE%\AppData\Roaming\run` on Windows.
pub fn global_config_dir_from(
    primary_override: Option<&OsStr>,
    home_dir: Option<&OsStr>,
) -> Option<PathBuf> {
    let dir_name = &PackageMetadata::get().config_dir_name;
    if let Some(val) = primary_override
        && !val.is_empty()
    {
        return Some(PathBuf::from(val).join(dir_name));
    }
    let home = home_dir.filter(|h| !h.is_empty())?;
    #[cfg(windows)]
    {
        Some(
            PathBuf::from(home)
                .join("AppData")
                .join("Roaming")
                .join(dir_name),
        )
    }
    #[cfg(not(windows))]
    {
        Some(PathBuf::from(home).join(".config").join(dir_name))
    }
}

/// Return `true` if `path` (or its canonical target) matches any boundary in `stop_dirs`.
fn is_stop_dir(path: &Path, stop_dirs: &[String]) -> bool {
    let canonical = fs::canonicalize(path).ok();
    stop_dirs.iter().any(|boundary| {
        let b = Path::new(boundary);
        path == b || canonical.as_deref() == Some(b)
    })
}

/// Discover `Run.toml` by searching upward up to `config_search_depth` levels, then `global_dir`.
pub fn discover_config_path_with_global(
    start_dir: Option<&Path>,
    global_dir: Option<&Path>,
) -> Option<PathBuf> {
    let meta = PackageMetadata::get();
    let start_base = match start_dir {
        Some(dir) => dir.to_path_buf(),
        None => std::env::current_dir().ok()?,
    };
    let mut current = fs::canonicalize(&start_base).unwrap_or(start_base);

    #[cfg(unix)]
    let start_dev = {
        use std::os::unix::fs::MetadataExt;
        fs::metadata(&current).ok().map(|m| m.dev())
    };

    for _ in 0..meta.config_search_depth {
        let candidate = current.join(&meta.config_file);
        if candidate.exists() {
            return Some(candidate);
        }

        if current.join(".git").exists() {
            break;
        }

        if is_stop_dir(&current, &meta.stop_dirs) {
            break;
        }

        let Some(parent) = current.parent() else {
            break;
        };

        #[cfg(unix)]
        if let Some(dev_id) = start_dev {
            use std::os::unix::fs::MetadataExt;
            let same_dev = fs::metadata(parent)
                .map(|m| m.dev() == dev_id)
                .unwrap_or(false);
            if !same_dev {
                break;
            }
        }

        current = parent.to_path_buf();
    }

    global_dir
        .map(|d| d.join(&meta.config_file))
        .filter(|p| p.exists())
}

/// Discover `Run.toml` using the standard OS global config directory fallback.
pub fn discover_config_path(start_dir: Option<&Path>) -> Option<PathBuf> {
    let global = global_config_dir();
    discover_config_path_with_global(start_dir, global.as_deref())
}

/// Log `"Loaded config: <path>"` at most once per unique path unless completion flags are active.
fn log_config_loaded(path: &Path) {
    let suppress = std::env::args_os().any(|arg| arg == "--_complete" || arg == "--completion");
    if suppress {
        return;
    }
    let set = LOGGED_CONFIG_PATHS.get_or_init(|| Mutex::new(HashSet::new()));
    if let Ok(mut guard) = set.lock()
        && guard.insert(path.to_path_buf())
    {
        Printer::global().info(&format!("Loaded config: {}", path.display()));
    }
}

/// Check whether a configured relative/absolute path resolves to an existing regular file directly, relative to `base_dir`, or `cwd`.
fn configured_file_exists(raw_path: &str, base_dir: Option<&Path>) -> bool {
    let p = Path::new(raw_path);
    if p.is_file() {
        return true;
    }
    if let Some(dir) = base_dir
        && dir.join(p).is_file()
    {
        return true;
    }
    if let Ok(cwd) = std::env::current_dir()
        && cwd.join(p).is_file()
    {
        return true;
    }
    false
}

/// Validate a `toml::map::Map` against deprecated aliases and section schemas before deserializing.
fn validate_toml_table(
    table: &toml::map::Map<String, toml::Value>,
    base_dir: Option<&Path>,
) -> Result<(), RunError> {
    let deprecated = [
        ("runner", "[runners]"),
        ("preset", "[presets]"),
        ("language", "[[languages]]"),
        ("exclude", "[core]"),
    ];
    for (old_key, replacement) in deprecated {
        if table.contains_key(old_key) {
            return Err(RunError::Config(format!(
                "Deprecated configuration section '[{old_key}]' is no longer supported; use '{replacement}' instead."
            )));
        }
    }

    if let Some(v) = table.get("runners")
        && !v.is_table()
    {
        return Err(RunError::Config(
            "'runners' section must be a table (dict)".to_string(),
        ));
    }

    if let Some(v) = table.get("presets")
        && !v.is_table()
    {
        return Err(RunError::Config(
            "'presets' section must be a table (dict)".to_string(),
        ));
    }

    if let Some(v) = table.get("core") {
        let Some(core_tbl) = v.as_table() else {
            return Err(RunError::Config(
                "'core' section must be a table (dict)".to_string(),
            ));
        };
        for key in ["exclude_files", "exclude_extensions", "exclude_dirs"] {
            if let Some(item) = core_tbl.get(key)
                && !item.is_array()
            {
                return Err(RunError::Config(format!("'core.{key}' must be a list")));
            }
        }
        for key in ["sandbox", "sandbox_net", "restrict"] {
            if let Some(item) = core_tbl.get(key)
                && !item.is_bool()
            {
                return Err(RunError::Config(format!("'core.{key}' must be a boolean")));
            }
        }
    }

    if let Some(v) = table.get("tasks")
        && !v.is_table()
    {
        return Err(RunError::Config(
            "'tasks' section must be a table (dict)".to_string(),
        ));
    }

    if let Some(v) = table.get("projects")
        && !v.is_table()
    {
        return Err(RunError::Config(
            "'projects' section must be a table (dict)".to_string(),
        ));
    }

    if let Some(v) = table.get("templates")
        && !v.is_table()
    {
        return Err(RunError::Config(
            "'templates' section must be a table (dict)".to_string(),
        ));
    }

    if let Some(v) = table.get("sandbox") {
        let Some(sb) = v.as_table() else {
            return Err(RunError::Config(
                "'sandbox' section must be a table (dict)".to_string(),
            ));
        };
        if let Some(img_val) = sb.get("image") {
            let Some(img) = img_val.as_str() else {
                return Err(RunError::Config(
                    "sandbox 'image' must be a non-empty string".to_string(),
                ));
            };
            let trimmed = img.trim();
            if trimmed.is_empty() {
                return Err(RunError::Config(
                    "sandbox 'image' must be a non-empty string".to_string(),
                ));
            }
            if trimmed.starts_with('-') || trimmed.chars().any(char::is_whitespace) {
                return Err(RunError::Config(format!(
                    "Invalid sandbox image name '{img}'"
                )));
            }
        }
        if let Some(df_val) = sb.get("dockerfile") {
            let Some(df) = df_val.as_str() else {
                return Err(RunError::Config(
                    "sandbox 'dockerfile' must be a non-empty string".to_string(),
                ));
            };
            if df.trim().is_empty() {
                return Err(RunError::Config(
                    "sandbox 'dockerfile' must be a non-empty string".to_string(),
                ));
            }
            if !configured_file_exists(df, base_dir) {
                return Err(RunError::Config(format!(
                    "Configured sandbox Dockerfile '{df}' not found"
                )));
            }
        }
        if let Some(comp_val) = sb.get("compose") {
            let Some(compose) = comp_val.as_str() else {
                return Err(RunError::Config(
                    "sandbox 'compose' must be a non-empty string".to_string(),
                ));
            };
            if compose.trim().is_empty() {
                return Err(RunError::Config(
                    "sandbox 'compose' must be a non-empty string".to_string(),
                ));
            }
            if !configured_file_exists(compose, base_dir) {
                return Err(RunError::Config(format!(
                    "Configured sandbox compose file '{compose}' not found"
                )));
            }
        }
        if let Some(svc_val) = sb.get("compose_service") {
            let Some(svc) = svc_val.as_str() else {
                return Err(RunError::Config(
                    "sandbox 'compose_service' must be a non-empty string".to_string(),
                ));
            };
            if svc.trim().is_empty() {
                return Err(RunError::Config(
                    "sandbox 'compose_service' must be a non-empty string".to_string(),
                ));
            }
        }
        for key in ["sandbox_net", "restrict", "writable"] {
            if let Some(b) = sb.get(key)
                && !b.is_bool()
            {
                return Err(RunError::Config(format!("sandbox '{key}' must be a boolean")));
            }
        }
    }

    if let Some(v) = table.get("languages") {
        let Some(arr) = v.as_array() else {
            return Err(RunError::Config(
                "'languages' section must be an array of tables ([[languages]])".to_string(),
            ));
        };
        for item in arr {
            let Some(lang_tbl) = item.as_table() else {
                return Err(RunError::Config(
                    "Language config must be a table".to_string(),
                ));
            };
            let Some(name) = lang_tbl.get("name").and_then(toml::Value::as_str) else {
                return Err(RunError::Config(
                    "Language config missing required 'name' field".to_string(),
                ));
            };
            if name.trim().is_empty() {
                return Err(RunError::Config(
                    "Language config 'name' must be a non-empty string".to_string(),
                ));
            }
            let Some(exts) = lang_tbl.get("extensions") else {
                return Err(RunError::Config(format!(
                    "Language '{name}' missing required 'extensions' list"
                )));
            };
            if !exts.is_array() {
                return Err(RunError::Config(format!(
                    "Language '{name}' 'extensions' must be a list"
                )));
            }
            let has_exec = ["runner", "compile", "command", "build"]
                .iter()
                .any(|k| lang_tbl.contains_key(*k));
            if !has_exec {
                return Err(RunError::Config(format!(
                    "Language '{name}' missing required 'runner', 'compile', or 'command' definition"
                )));
            }
        }
    }

    Ok(())
}

/// Deserialize a typed section from an optional TOML value using `serde`.
fn deserialize_section<T: serde::de::DeserializeOwned + Default>(
    table: &toml::map::Map<String, toml::Value>,
    key: &str,
) -> Result<T, RunError> {
    match table.get(key) {
        Some(val) => val
            .clone()
            .try_into::<T>()
            .map_err(|err| RunError::Config(format!("Invalid '[{key}]' section: {err}"))),
        None => Ok(T::default()),
    }
}

impl Config {
    /// Discover and load `Run.toml` starting from `cwd` (or current directory), or from `explicit_path`.
    pub fn load(cwd: Option<&Path>, explicit_path: Option<&Path>) -> Result<Self, RunError> {
        let global = global_config_dir();
        Self::load_with_global_dir(cwd, explicit_path, global.as_deref())
    }

    /// Discover and load `Run.toml` with an explicit global fallback directory (for unit tests).
    pub fn load_with_global_dir(
        cwd: Option<&Path>,
        explicit_path: Option<&Path>,
        global_dir: Option<&Path>,
    ) -> Result<Self, RunError> {
        let resolved_path = match explicit_path {
            Some(path) => {
                if !path.exists() {
                    return Err(RunError::Config(format!(
                        "Config file '{}' not found",
                        path.display()
                    )));
                }
                Some(path.to_path_buf())
            }
            None => discover_config_path_with_global(cwd, global_dir),
        };

        let Some(config_path) = resolved_path else {
            return Ok(Self::default());
        };

        let content = fs::read_to_string(&config_path).map_err(|err| {
            RunError::Config(format!("Failed to read {}: {err}", config_path.display()))
        })?;
        let base_dir = config_path.parent().or(cwd);
        let cfg = Self::from_toml_str(&content, Some(config_path.clone()), base_dir)?;
        log_config_loaded(&config_path);
        Ok(cfg)
    }

    /// Parse and validate a `Run.toml` string directly.
    ///
    /// Uses `toml::from_str::<toml::Value>` (with `preserve_order`) followed by
    /// schema validation and typed `serde` deserialization, preserving user
    /// `[projects]` insertion order explicitly.
    pub fn from_toml_str(
        content: &str,
        path: Option<PathBuf>,
        base_dir: Option<&Path>,
    ) -> Result<Self, RunError> {
        let root: toml::Value = toml::from_str(content).map_err(|err| {
            let loc = path
                .as_ref()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "Run.toml".to_string());
            RunError::Config(format!("Failed to parse {loc}: {err}"))
        })?;
        let Some(table) = root.as_table() else {
            return Err(RunError::Config(
                "Root of Run.toml must be a TOML table".to_string(),
            ));
        };

        validate_toml_table(table, base_dir)?;

        let core: CoreConfig = deserialize_section(table, "core")?;
        let sandbox: SandboxConfig = deserialize_section(table, "sandbox")?;
        let tasks: BTreeMap<String, TaskConfig> = deserialize_section(table, "tasks")?;
        let runners: BTreeMap<String, String> = deserialize_section(table, "runners")?;
        let presets: BTreeMap<String, BTreeMap<String, PresetFlags>> =
            deserialize_section(table, "presets")?;
        let languages: Vec<CustomLanguageConfig> = match table.get("languages") {
            Some(v) => v
                .clone()
                .try_into::<Vec<CustomLanguageConfig>>()
                .map_err(|err| RunError::Config(format!("Invalid '[[languages]]' section: {err}")))?,
            None => Vec::new(),
        };
        let templates: BTreeMap<String, TemplateConfig> = deserialize_section(table, "templates")?;

        let mut projects = Vec::new();
        if let Some(proj_table) = table.get("projects").and_then(toml::Value::as_table) {
            for (name, val) in proj_table {
                let proj_cfg: ProjectConfig = val.clone().try_into().map_err(|err| {
                    RunError::Config(format!("Invalid '[projects.{name}]' section: {err}"))
                })?;
                projects.push((name.clone(), proj_cfg));
            }
        }

        Ok(Self {
            path,
            core,
            sandbox,
            tasks,
            projects,
            runners,
            presets,
            languages,
            templates,
        })
    }

    /// Return the configured runner binary for `lang`, falling back to `default`.
    pub fn get_runner(&self, lang: &str, default: &str) -> String {
        self.runners
            .get(lang)
            .cloned()
            .unwrap_or_else(|| default.to_string())
    }

    /// Return configured `[tasks]`.
    pub fn get_tasks(&self) -> &BTreeMap<String, TaskConfig> {
        &self.tasks
    }

    /// Return configured `[sandbox]` settings.
    pub fn get_sandbox_config(&self) -> &SandboxConfig {
        &self.sandbox
    }

    /// Return ordered project manifest detectors with user `[projects]` taking priority
    /// over [`PackageMetadata::default_projects`].
    pub fn get_projects(&self) -> Vec<(String, ProjectConfig)> {
        let mut merged = self.projects.clone();
        let seen: HashSet<String> = merged.iter().map(|(name, _)| name.clone()).collect();

        for def in &PackageMetadata::get().default_projects {
            if !seen.contains(&def.name) {
                merged.push((
                    def.name.clone(),
                    ProjectConfig {
                        file: Some(def.file.clone()),
                        command: def.command.clone(),
                        build: def.build.clone(),
                        run: def.run.clone(),
                    },
                ));
            }
        }

        merged
    }

    /// Return compiler/interpreter flags for `preset_name` and `lang`.
    ///
    /// Supports both TOML arrays and whitespace-delimited strings (parsed via `shlex::split`).
    pub fn get_preset_flags(&self, preset_name: Option<&str>, lang: &str) -> Vec<String> {
        let Some(name) = preset_name else {
            return Vec::new();
        };
        let Some(preset) = self.presets.get(name) else {
            return Vec::new();
        };
        match preset.get(lang) {
            Some(PresetFlags::List(items)) => items.clone(),
            Some(PresetFlags::Str(s)) => shlex::split(s).unwrap_or_default(),
            None => Vec::new(),
        }
    }

    /// Return all custom languages, starting with [`PackageMetadata::builtin_languages`] (e.g. Go)
    /// and overlaying/appending user `[[languages]]` definitions by `name`.
    pub fn get_custom_languages(&self) -> Vec<CustomLanguageConfig> {
        let mut merged: Vec<CustomLanguageConfig> = PackageMetadata::get()
            .builtin_languages
            .iter()
            .map(|b| CustomLanguageConfig {
                name: b.name.clone(),
                extensions: b.extensions.clone(),
                runner: b.runner.clone(),
                subcommand: b.subcommand.clone(),
                lang_type: b.lang_type.clone(),
                flags: b.flags.clone(),
                arguments: b.arguments.clone(),
                compile: b.compile.clone(),
                build: b.build.clone(),
                command: b.command.clone(),
                run: b.run.clone(),
            })
            .collect();

        for user_lang in &self.languages {
            if let Some(existing) = merged.iter_mut().find(|l| l.name == user_lang.name) {
                *existing = user_lang.clone();
            } else {
                merged.push(user_lang.clone());
            }
        }

        merged
    }

    /// Find the first custom or built-in language configuration matching file extension `ext`.
    pub fn get_language_by_extension(&self, ext: &str) -> Option<CustomLanguageConfig> {
        self.get_custom_languages().into_iter().find(|lang| {
            lang.extensions
                .iter()
                .any(|pattern| match_extension(ext, pattern, true))
        })
    }

    /// Return `true` if `ext` matches a configured custom or built-in language.
    pub fn is_custom_language_configured(&self, ext: &str) -> bool {
        self.get_language_by_extension(ext).is_some()
    }

    /// Return exclusion lists defined under `[core]`.
    pub fn get_exclude(&self) -> ExcludeConfig {
        ExcludeConfig {
            files: self.core.exclude_files.clone(),
            extensions: self.core.exclude_extensions.clone(),
            dirs: self.core.exclude_dirs.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Create an isolated temporary directory path for config tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock went backwards")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_cfg_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp test dir");
        dir
    }

    /// Port of `dev:tests/test_config.py::test_config_load`, `test_preset_flags`, `test_get_custom_languages`.
    #[test]
    fn test_config_load_runners_presets_and_languages() {
        let dir = make_temp_dir("load");
        let toml_path = dir.join("Run.toml");
        fs::write(
            &toml_path,
            r#"
[core]
exclude_files = ["test.py"]

[runners]
c = "my_clang"

[presets.debug]
c = ["-g"]

[presets.release]
cpp = "-O3 -DNDEBUG"

[[languages]]
name = "custom"
extensions = [".custom"]
runner = "custom_run"
type = "interpreter"
"#,
        )
        .unwrap();

        let cfg = Config::load_with_global_dir(Some(&dir), None, None).expect("load config");
        assert_eq!(cfg.get_runner("c", "clang"), "my_clang");
        assert_eq!(cfg.get_runner("cpp", "clang++"), "clang++");
        assert_eq!(cfg.get_preset_flags(Some("debug"), "c"), vec!["-g"]);
        assert_eq!(
            cfg.get_preset_flags(Some("release"), "cpp"),
            vec!["-O3", "-DNDEBUG"]
        );
        assert!(cfg.get_preset_flags(Some("missing"), "c").is_empty());

        let langs = cfg.get_custom_languages();
        assert!(langs.iter().any(|l| l.name == "go"));
        assert!(langs.iter().any(|l| l.name == "custom"));
        assert!(cfg.is_custom_language_configured(".custom"));
        assert!(cfg.is_custom_language_configured(".go"));
        assert!(!cfg.is_custom_language_configured(".unknown"));

        let _ = fs::remove_dir_all(&dir);
    }

    /// Port of `dev:tests/test_config.py::test_sandbox_config_validation_*`.
    #[test]
    fn test_sandbox_config_validation() {
        let dir = make_temp_dir("sandbox");
        let df = dir.join("Dockerfile");
        fs::write(&df, "FROM alpine\n").unwrap();
        let compose = dir.join("docker-compose.yml");
        fs::write(&compose, "version: '3'\n").unwrap();

        let valid = format!(
            "[sandbox]\nimage = \"gcc:latest\"\ndockerfile = '{}'\ncompose = '{}'\ncompose_service = \"app\"\nsandbox_net = true\nrestrict = false\n",
            df.display(),
            compose.display()
        );
        let cfg = Config::from_toml_str(&valid, None, Some(&dir)).expect("valid sandbox");
        assert_eq!(
            cfg.get_sandbox_config().image.as_deref(),
            Some("gcc:latest")
        );

        let invalid_img = "[sandbox]\nimage = \"--privileged\"\n";
        let err = Config::from_toml_str(invalid_img, None, Some(&dir)).unwrap_err();
        assert!(err.to_string().contains("Invalid sandbox image name"));

        let missing_df = "[sandbox]\ndockerfile = \"nonexistent_dockerfile\"\n";
        let err2 = Config::from_toml_str(missing_df, None, Some(&dir)).unwrap_err();
        assert!(err2.to_string().contains("sandbox Dockerfile"));

        let _ = fs::remove_dir_all(&dir);
    }

    /// Port of `dev:tests/test_config.py::test_config_upward_traversal_stops_at_git_root`.
    #[test]
    fn test_config_upward_traversal_stops_at_git_root() {
        let dir = make_temp_dir("git_stop");
        fs::write(
            dir.join("Run.toml"),
            "[core]\nexclude_files = [\"malicious_exclude\"]\n",
        )
        .unwrap();
        let repo = dir.join("repo");
        fs::create_dir_all(repo.join(".git")).unwrap();
        let sub = repo.join("sub");
        fs::create_dir_all(&sub).unwrap();

        let cfg = Config::load_with_global_dir(Some(&sub), None, None).expect("load");
        assert!(cfg.path.is_none());
        assert!(cfg.get_exclude().files.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    /// Verify 4-level upward discovery depth limit (levels 0..=3 found, level 4 ignored).
    #[test]
    fn test_config_upward_traversal_depth_limit() {
        let dir = make_temp_dir("depth");
        fs::write(dir.join("Run.toml"), "[runners]\nc = \"depth_cc\"\n").unwrap();
        let l1 = dir.join("a");
        let l2 = l1.join("b");
        let l3 = l2.join("c");
        let l4 = l3.join("d");
        fs::create_dir_all(&l4).unwrap();

        let cfg3 = Config::load_with_global_dir(Some(&l3), None, None).unwrap();
        assert_eq!(cfg3.get_runner("c", "gcc"), "depth_cc");

        let cfg4 = Config::load_with_global_dir(Some(&l4), None, None).unwrap();
        assert!(cfg4.path.is_none());
        assert_eq!(cfg4.get_runner("c", "gcc"), "gcc");

        let _ = fs::remove_dir_all(&dir);
    }

    /// Port of `dev:tests/test_glob_config.py::test_config_exclude_dirs_validation`.
    #[test]
    fn test_config_exclude_dirs_validation() {
        let dir = make_temp_dir("excludes");
        fs::write(
            dir.join("Run.toml"),
            r#"
[core]
exclude_files = ["*.tmp", "mock_*"]
exclude_dirs = ["tmp_*", "vendor"]
exclude_extensions = ["*.bak", ".swp"]
"#,
        )
        .unwrap();

        let cfg = Config::load_with_global_dir(Some(&dir), None, None).unwrap();
        let excludes = cfg.get_exclude();
        assert_eq!(excludes.files, vec!["*.tmp", "mock_*"]);
        assert_eq!(excludes.dirs, vec!["tmp_*", "vendor"]);
        assert_eq!(excludes.extensions, vec!["*.bak", ".swp"]);

        let _ = fs::remove_dir_all(&dir);
    }

    /// Verify user `[projects]` preserve insertion order and precede built-in defaults,
    /// and user `[[languages]]` override built-in Go.
    #[test]
    fn test_projects_order_and_builtin_language_override() {
        let toml_str = r#"
[projects.custom_cmake]
file = "CMakeLists.txt"
command = "custom-cmake-run"

[projects.alpha_proj]
file = "alpha.toml"
command = "alpha"

[[languages]]
name = "go"
extensions = [".go"]
compile = "custom-go build ${out} ${files}"
type = "compiler"
"#;
        let cfg = Config::from_toml_str(toml_str, None, None).unwrap();
        let projects = cfg.get_projects();
        let names: Vec<&str> = projects.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(
            names,
            vec![
                "custom_cmake",
                "alpha_proj",
                "cargo",
                "go",
                "zig",
                "cmake",
                "make"
            ]
        );

        let go_lang = cfg.get_language_by_extension(".go").expect("go language");
        assert_eq!(
            go_lang.compile.as_deref(),
            Some("custom-go build ${out} ${files}")
        );
    }

    /// Verify modernized global config path (`~/.config/run` / `$XDG_CONFIG_HOME/run`) and fallback loading.
    #[test]
    fn test_global_config_modernized_path_and_fallback() {
        #[cfg(not(windows))]
        {
            let xdg = global_config_dir_from(Some(OsStr::new("/xdg/cfg")), Some(OsStr::new("/home/u")));
            assert_eq!(xdg, Some(PathBuf::from("/xdg/cfg/run")));
            let fallback = global_config_dir_from(Some(OsStr::new("")), Some(OsStr::new("/home/u")));
            assert_eq!(fallback, Some(PathBuf::from("/home/u/.config/run")));
        }

        let dir = make_temp_dir("global_fb");
        let work = dir.join("work");
        let global_dir = dir.join("config").join("run");
        fs::create_dir_all(&work).unwrap();
        fs::create_dir_all(&global_dir).unwrap();
        fs::write(global_dir.join("Run.toml"), "[runners]\nc = \"global_cc\"\n").unwrap();

        let cfg = Config::load_with_global_dir(Some(&work), None, Some(&global_dir)).unwrap();
        assert_eq!(cfg.get_runner("c", "gcc"), "global_cc");

        let _ = fs::remove_dir_all(&dir);
    }

    /// Verify stop-directory boundary matching handles `/tmp`, `/var/tmp`, and `/private/tmp`.
    #[test]
    fn test_is_stop_dir_boundaries() {
        let stop_dirs = &PackageMetadata::get().stop_dirs;
        assert!(is_stop_dir(Path::new("/tmp"), stop_dirs));
        assert!(is_stop_dir(Path::new("/var/tmp"), stop_dirs));
        assert!(is_stop_dir(Path::new("/private/tmp"), stop_dirs));
        assert!(!is_stop_dir(Path::new("/tmp/some_nested_dir"), stop_dirs));
    }

    /// Verify deprecated aliases `[runner]`, `[preset]`, `[language]`, and `[exclude]` are rejected (R3).
    #[test]
    fn test_deprecated_aliases_rejected() {
        for (section, content) in [
            ("runner", "[runner]\nc = \"clang\"\n"),
            ("preset", "[preset.debug]\nc = [\"-g\"]\n"),
            (
                "language",
                "[language.foo]\nextensions = [\".foo\"]\nrunner = \"foo\"\n",
            ),
            ("exclude", "[exclude]\nfiles = [\"a.c\"]\n"),
        ] {
            let err = Config::from_toml_str(content, None, None).unwrap_err();
            assert!(
                matches!(err, RunError::Config(_)),
                "expected Config error for [{section}]"
            );
            assert!(
                err.to_string().contains(&format!("[{section}]")),
                "error should mention deprecated section [{section}]: {err}"
            );
        }
    }

    /// Verify `[sandbox].image` rejects any whitespace (` `, `\t`, `\n`, `\r`).
    #[test]
    fn test_sandbox_image_rejects_any_whitespace() {
        for bad_image in [
            "ubuntu --privileged",
            "ubuntu\t--privileged",
            "ubuntu\n--privileged",
            "ubuntu\r--privileged",
        ] {
            let escaped = bad_image
                .replace('\t', "\\t")
                .replace('\n', "\\n")
                .replace('\r', "\\r");
            let content = format!("[sandbox]\nimage = \"{escaped}\"\n");
            let err = Config::from_toml_str(&content, None, None).unwrap_err();
            assert!(
                matches!(err, RunError::Config(_)),
                "expected Config error for image {escaped:?}"
            );
            assert!(
                err.to_string().contains("Invalid sandbox image name"),
                "unexpected error message: {err}"
            );
        }
    }

    /// Verify `[sandbox].dockerfile` and `[sandbox].compose` reject directories.
    #[test]
    fn test_sandbox_dockerfile_and_compose_reject_directories() {
        let dir = make_temp_dir("sandbox_dirs");
        let subdir = dir.join("subdir");
        fs::create_dir_all(&subdir).unwrap();

        for (key, value) in [
            ("dockerfile", subdir.display().to_string()),
            ("dockerfile", "subdir".to_string()),
            ("compose", subdir.display().to_string()),
            ("compose", "subdir".to_string()),
        ] {
            let content = format!("[sandbox]\n{key} = \"{value}\"\n");
            let err = Config::from_toml_str(&content, None, Some(&dir)).unwrap_err();
            assert!(
                matches!(err, RunError::Config(_)),
                "expected Config error when sandbox.{key} points to directory {value}"
            );
        }

        let _ = fs::remove_dir_all(&dir);
    }
}
