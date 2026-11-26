//! Configuration schema structs and enums for `Run.toml`.

use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::Deserialize;

/// Core discovery and exclusion settings under `[core]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct CoreConfig {
    /// Filenames or glob patterns excluded from discovery and compilation.
    pub exclude_files: Vec<String>,
    /// Directory names or glob patterns excluded from walks.
    pub exclude_dirs: Vec<String>,
    /// File extensions or glob patterns excluded from scans.
    pub exclude_extensions: Vec<String>,
    /// Enable container sandboxing globally when `true`.
    pub sandbox: bool,
    /// Enable network access inside sandboxes when `true`.
    pub sandbox_net: bool,
    /// Enable native bubblewrap restriction when `true`.
    pub restrict: bool,
}

/// Container and restriction configuration under `[sandbox]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct SandboxConfig {
    /// Explicit container image name override.
    pub image: Option<String>,
    /// Path to a Dockerfile built for sandbox execution.
    pub dockerfile: Option<String>,
    /// Path to a docker-compose file.
    pub compose: Option<String>,
    /// Service name inside the compose file.
    pub compose_service: Option<String>,
    /// Enable container networking.
    pub sandbox_net: Option<bool>,
    /// Enable native bubblewrap restriction.
    pub restrict: Option<bool>,
    /// Mount workspace read-write inside the container.
    pub writable: Option<bool>,
    /// Container user override (`uid:gid` or username).
    pub user: Option<String>,
    /// Working directory inside the compose container.
    pub compose_workdir: Option<String>,
    /// Runtime-assigned persistent container name (not serialized in `Run.toml`).
    #[serde(skip)]
    pub container_name: Option<String>,
}

/// Per-OS overrides under `[tasks.<name>.windows]`, `.linux`, or `.macos`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct TaskOsOverride {
    /// Override `command` on this OS.
    pub command: Option<String>,
    /// Override `cmd` on this OS.
    pub cmd: Option<String>,
    /// Override `cmds` on this OS.
    pub cmds: Vec<String>,
    /// Extra environment variables merged on this OS.
    pub env: BTreeMap<String, String>,
    /// Override `dir` on this OS.
    pub dir: Option<String>,
    /// Override `cwd` on this OS.
    pub cwd: Option<String>,
}

/// Detailed task definition under `[tasks.<name>]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct TaskTable {
    /// Human-readable description shown by `-l` / `--list`.
    pub desc: Option<String>,
    /// Shell command executed by the task.
    pub command: Option<String>,
    /// Alias for `command` (`go-task` style `cmd = "..."`).
    pub cmd: Option<String>,
    /// Ordered list of commands executed sequentially (`cmds = [...]`).
    pub cmds: Vec<String>,
    /// Prerequisite task names executed before this task (`deps = [...]`).
    pub deps: Vec<String>,
    /// Glob patterns of source inputs for SHA-256 up-to-date checking.
    pub sources: Vec<String>,
    /// Paths or globs of generated outputs for SHA-256 up-to-date checking.
    pub generates: Vec<String>,
    /// Named parameters mapped positionally from `-- <argv>`.
    pub params: Vec<String>,
    /// Task-local variables overriding `[vars]`.
    pub vars: BTreeMap<String, String>,
    /// Task-local environment variables merged into child execution.
    pub env: BTreeMap<String, String>,
    /// Working directory for task execution (`dir` or `cwd`).
    pub dir: Option<String>,
    /// Alias for `dir`.
    pub cwd: Option<String>,
    /// Suppress `[ TASK ]` banner output.
    pub silent: bool,
    /// Continue execution if a task command returns a non-zero exit code.
    pub ignore_error: bool,
    /// Run this task inside a container sandbox.
    pub sandbox: bool,
    /// Allow networking when sandboxed.
    pub sandbox_net: bool,
    /// Run this task with native bubblewrap restriction.
    pub restrict: bool,
    /// Optional OS allowlist (`"linux"`, `"macos"`, `"windows"`); empty means all platforms.
    pub platforms: Vec<String>,
    /// Windows-specific overrides (`[tasks.<name>.windows]`).
    pub windows: Option<Box<TaskOsOverride>>,
    /// Linux-specific overrides (`[tasks.<name>.linux]`).
    pub linux: Option<Box<TaskOsOverride>>,
    /// macOS-specific overrides (`[tasks.<name>.macos]`).
    pub macos: Option<Box<TaskOsOverride>>,
}

impl TaskTable {
    /// Return ordered commands configured via `cmds`, `cmd`, or `command`.
    pub fn commands(&self) -> Vec<&str> {
        if !self.cmds.is_empty() {
            return self.cmds.iter().map(String::as_str).collect();
        }
        if let Some(c) = self.cmd.as_deref().or(self.command.as_deref()) {
            return vec![c];
        }
        Vec::new()
    }

    /// Return `true` if `platforms` is empty or includes the current host OS.
    pub fn matches_current_platform(&self) -> bool {
        if self.platforms.is_empty() {
            return true;
        }
        let host = if cfg!(target_os = "windows") {
            "windows"
        } else if cfg!(target_os = "macos") {
            "macos"
        } else {
            "linux"
        };
        self.platforms.iter().any(|p| {
            let norm = p.trim().to_ascii_lowercase();
            norm == host || (host == "macos" && norm == "darwin")
        })
    }

    /// Return a copy of `self` with any matching OS-specific override merged in.
    pub fn resolve_for_current_os(&self) -> Self {
        let mut resolved = self.clone();
        let os_override = if cfg!(target_os = "windows") {
            self.windows.as_deref()
        } else if cfg!(target_os = "macos") {
            self.macos.as_deref()
        } else {
            self.linux.as_deref()
        };
        if let Some(ov) = os_override {
            if !ov.cmds.is_empty() {
                resolved.cmds = ov.cmds.clone();
                resolved.cmd = None;
                resolved.command = None;
            } else if let Some(c) = ov.cmd.as_ref().or(ov.command.as_ref()) {
                resolved.cmds.clear();
                resolved.cmd = Some(c.clone());
                resolved.command = None;
            }
            for (k, v) in &ov.env {
                resolved.env.insert(k.clone(), v.clone());
            }
            if ov.dir.is_some() {
                resolved.dir = ov.dir.clone();
            }
            if ov.cwd.is_some() {
                resolved.cwd = ov.cwd.clone();
            }
        }
        resolved
    }
}

/// Task entry under `[tasks]` supporting either a command string or a table.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(untagged)]
pub enum TaskConfig {
    /// Simple `name = "command"` shortcut.
    Simple(String),
    /// Structured `[tasks.<name>]` table with dependencies, vars, params, and sandbox flags.
    Detailed(Box<TaskTable>),
}

/// Project detector entry under `[projects.<name>]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct ProjectConfig {
    /// Manifest filename or glob pattern.
    pub file: Option<String>,
    /// Single-step build/run command.
    pub command: Option<String>,
    /// Build step command for two-step projects.
    pub build: Option<String>,
    /// Run step command for two-step projects.
    pub run: Option<String>,
}

/// Preset compiler/interpreter flag specification under `[presets.<preset>.<lang>]`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(untagged)]
pub enum PresetFlags {
    /// Array of flag tokens (e.g. `["-g", "-Wall"]`).
    List(Vec<String>),
    /// Whitespace-delimited string split with `shlex::split`.
    Str(String),
}

/// Custom or overridden language definition under `[[languages]]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct CustomLanguageConfig {
    /// Unique language name (used for preset matching and overriding built-in languages).
    pub name: String,
    /// File extensions or extension globs handled by this language.
    pub extensions: Vec<String>,
    /// Executable command or compiler/interpreter path.
    pub runner: Option<String>,
    /// Optional subcommand passed before flags.
    pub subcommand: Option<String>,
    /// Handler type (`"interpreter"` or `"compiler"`).
    #[serde(rename = "type")]
    pub lang_type: Option<String>,
    /// Default flags appended during compilation or execution.
    pub flags: Vec<String>,
    /// Default runtime arguments passed to the executed program.
    pub arguments: Vec<String>,
    /// Custom compilation command template.
    pub compile: Option<String>,
    /// Two-step build command template.
    pub build: Option<String>,
    /// Single command template.
    pub command: Option<String>,
    /// Two-step run command template.
    pub run: Option<String>,
}

/// Single file entry inside a multi-file `[templates.<name>]` definition.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct TemplateFileEntry {
    /// Relative destination filename within the generated project.
    pub name: String,
    /// Inline file content.
    pub content: Option<String>,
    /// External source file path to copy content from.
    pub file: Option<String>,
}

/// Structured template entry under `[templates.<name>]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct TemplateTable {
    /// Inline template content for single-file templates.
    pub content: Option<String>,
    /// External source file path for single-file templates.
    pub file: Option<String>,
    /// Default file extension when target path omits one.
    pub extension: Option<String>,
    /// Human-readable description shown in template listings.
    pub description: Option<String>,
    /// Multi-file template entries.
    pub files: Vec<TemplateFileEntry>,
}

/// Template definition under `[templates.<name>]`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(untagged)]
pub enum TemplateConfig {
    /// Inline string template content.
    Inline(String),
    /// Structured template table.
    Table(TemplateTable),
}

/// Exclusion lists returned by [`Config::get_exclude`].
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ExcludeConfig {
    /// Excluded filenames or globs from `[core].exclude_files`.
    pub files: Vec<String>,
    /// Excluded extensions or globs from `[core].exclude_extensions`.
    pub extensions: Vec<String>,
    /// Excluded directory names or globs from `[core].exclude_dirs`.
    pub dirs: Vec<String>,
}

/// `[cache]` retention and pruning settings from `Run.toml`.
#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(default)]
pub struct CacheConfig {
    /// Maximum age in days before cached entries are eligible for pruning (`0` disables age pruning).
    pub max_age_days: Option<u64>,
    /// Maximum total cache size in megabytes before LRU eviction (`0` disables size pruning).
    pub max_size_mb: Option<u64>,
}

/// Loaded and validated `Run.toml` configuration.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Config {
    /// Filesystem path of the loaded `Run.toml`, if one was found.
    pub path: Option<PathBuf>,
    /// `[core]` settings.
    pub core: CoreConfig,
    /// `[sandbox]` settings.
    pub sandbox: SandboxConfig,
    /// `[cache]` settings.
    pub cache: CacheConfig,
    /// `[vars]` global task variables.
    pub vars: BTreeMap<String, String>,
    /// `[tasks]` mappings.
    pub tasks: BTreeMap<String, TaskConfig>,
    /// User `[projects]` mappings preserving TOML insertion order.
    pub projects: Vec<(String, ProjectConfig)>,
    /// `[runners]` executable overrides.
    pub runners: BTreeMap<String, String>,
    /// `[presets]` flag maps.
    pub presets: BTreeMap<String, BTreeMap<String, PresetFlags>>,
    /// User `[[languages]]` definitions.
    pub languages: Vec<CustomLanguageConfig>,
    /// `[templates]` definitions.
    pub templates: BTreeMap<String, TemplateConfig>,
}
