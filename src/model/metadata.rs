//! Compile-time package metadata models deserialized from `Cargo.toml` `[package.metadata.run]`.

use serde::Deserialize;

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
