//! Domain models, configuration schemas, metadata structures, options, errors, and traits.
//!
//! Centralizes the core data types shared across [`crate::util`],
//! execution engines, and integration tests without coupling them to I/O
//! or runtime orchestration.

pub mod config;
pub mod error;
pub mod metadata;
pub mod options;
pub mod sandbox;

pub use config::{
    Config, CoreConfig, CustomLanguageConfig, ExcludeConfig, PresetFlags, ProjectConfig,
    SandboxConfig, TaskConfig, TaskTable, TemplateConfig, TemplateFileEntry, TemplateTable,
};
pub use error::RunError;
pub use metadata::{
    BuiltinLanguageMetadata, CacheMetadata, DefaultProjectMetadata, DefaultRunnersMetadata,
    ExcludesMetadata, ExtensionsMetadata, FlagsMetadata, ImageHeuristicMetadata, LimitsMetadata,
    PackageMetadata, SandboxMetadata, SecurityMetadata,
};
pub use options::{CommandExecutor, ExecOutcome, ExecSpec, RunnerOptions};
pub use sandbox::{ExecOutput, HostOs, SandboxEnv};
