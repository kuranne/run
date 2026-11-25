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

/// Detailed task definition under `[tasks.<name>]`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct TaskTable {
    /// Shell command executed by the task.
    pub command: Option<String>,
    /// Run this task inside a container sandbox.
    pub sandbox: bool,
    /// Allow networking when sandboxed.
    pub sandbox_net: bool,
    /// Run this task with native bubblewrap restriction.
    pub restrict: bool,
}

/// Task entry under `[tasks]` supporting either a command string or a table.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(untagged)]
pub enum TaskConfig {
    /// Simple `name = "command"` shortcut.
    Simple(String),
    /// Structured `[tasks.<name>]` table with sandbox/restrict flags.
    Detailed(TaskTable),
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

/// Loaded and validated `Run.toml` configuration.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Config {
    /// Filesystem path of the loaded `Run.toml`, if one was found.
    pub path: Option<PathBuf>,
    /// `[core]` settings.
    pub core: CoreConfig,
    /// `[sandbox]` settings.
    pub sandbox: SandboxConfig,
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
