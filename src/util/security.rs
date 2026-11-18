//! Security policy enforcement, environment sanitization, flag filtering, and path validation.
//!
//! Adapted from Python's `src/util/security.py` and `src/util/validator.py`.
//! Delegates environment variable classification to
//! [`crate::util::env::is_dangerous_env_var`] and reads allowlists and suspicious
//! compiler flag rules from `[package.metadata.run.security]` via
//! [`crate::util::env::PackageMetadata::get`]. Uses `libc::geteuid` on Unix and
//! `windows_sys::Win32::UI::Shell::IsUserAnAdmin` on Windows for privilege checks.

use std::collections::BTreeMap;
use std::path::{Component, Path};

use crate::util::env::{PackageMetadata, is_dangerous_env_var as env_is_dangerous};
use crate::util::errors::RunError;
use crate::util::output::Printer;

/// Enforces privilege restrictions, environment sanitization, and compiler flag filtering.
#[derive(Debug, Clone, Copy, Default)]
pub struct SecurityManager;

impl SecurityManager {
    /// Check whether the current process is running as root/administrator.
    ///
    /// Uses `libc::geteuid() == 0` on Unix and `IsUserAnAdmin() != 0` via
    /// `windows-sys` on Windows. Blocks execution unless `allow_root` (`--unsafe`)
    /// is set.
    pub fn check_root(allow_root: bool) -> Result<(), RunError> {
        let is_root = Self::current_process_is_root();
        Self::check_root_with_status(is_root, allow_root)
    }

    /// Pure privilege check helper allowing unit tests to verify root blocking without UID 0.
    pub fn check_root_with_status(is_root: bool, allow_root: bool) -> Result<(), RunError> {
        if is_root {
            let msg = "Running as root/administrator is dangerous for compiling/running arbitrary code.";
            if allow_root {
                Printer::global().warning(&format!("{msg} Proceeding due to override."));
            } else {
                Printer::global().error(msg);
                return Err(RunError::config(
                    "Execution as root is blocked. Use --unsafe to override.",
                ));
            }
        }
        Ok(())
    }

    /// Detect whether the active OS process holds root/administrator privileges.
    fn current_process_is_root() -> bool {
        #[cfg(unix)]
        {
            // SAFETY: `geteuid` takes no pointers and cannot fail.
            unsafe { libc::geteuid() == 0 }
        }
        #[cfg(windows)]
        {
            // SAFETY: `IsUserAnAdmin` is a parameterless Win32 Shell API returning BOOL.
            unsafe { windows_sys::Win32::UI::Shell::IsUserAnAdmin() != 0 }
        }
        #[cfg(not(any(unix, windows)))]
        {
            false
        }
    }

    /// Return `true` if `var` matches a dangerous environment variable name or prefix.
    ///
    /// Delegates directly to [`crate::util::env::is_dangerous_env_var`] so
    /// `[package.metadata.run.security]` serves as the single source of truth.
    pub fn is_dangerous_env_var(var: &str) -> bool {
        env_is_dangerous(var)
    }

    /// Build a sanitized execution environment map from the current OS environment.
    ///
    /// When `strict_whitelist` is `true`, only keys listed in
    /// `[package.metadata.run.security].safe_env_whitelist` (and not dangerous)
    /// are kept from the host environment before merging safe `custom_env` pairs.
    pub fn sanitize_execution_env(
        custom_env: Option<&BTreeMap<String, String>>,
        strict_whitelist: bool,
    ) -> BTreeMap<String, String> {
        Self::sanitize_env_from(std::env::vars(), custom_env, strict_whitelist)
    }

    /// Pure environment sanitization helper operating on an explicit `base_env` iterator.
    ///
    /// Rejects dangerous custom entries with a `[ WARN ]` message and allows
    /// unit tests to run concurrently without mutating process-global environment
    /// variables.
    pub fn sanitize_env_from<I>(
        base_env: I,
        custom_env: Option<&BTreeMap<String, String>>,
        strict_whitelist: bool,
    ) -> BTreeMap<String, String>
    where
        I: IntoIterator<Item = (String, String)>,
    {
        let whitelist = &PackageMetadata::get().security.safe_env_whitelist;
        let mut env = BTreeMap::new();

        for (k, v) in base_env {
            if Self::is_dangerous_env_var(&k) {
                continue;
            }
            if strict_whitelist && !whitelist.iter().any(|allowed| allowed == &k) {
                continue;
            }
            env.insert(k, v);
        }

        if let Some(custom) = custom_env {
            for (k, v) in custom {
                if !Self::is_dangerous_env_var(k) {
                    env.insert(k.clone(), v.clone());
                } else {
                    Printer::global().warning(&format!(
                        "Rejected dangerous custom environment variable: {k}"
                    ));
                }
            }
        }

        env
    }

    /// Return `true` if all compiler/runtime `flags` are safe, or `false` if any look suspicious.
    ///
    /// Checks substring and exact rules from `[package.metadata.run.security]`
    /// as well as adjacent pairs `-x assembler*`, `-C linker=*`, and `-C link-arg=*`.
    pub fn check_suspicious_flags(flags: &[String]) -> bool {
        let sec = &PackageMetadata::get().security;

        for (idx, flag) in flags.iter().enumerate() {
            for pattern in &sec.suspicious_flag_substrings {
                if flag.contains(pattern.as_str()) {
                    Printer::global().warning(&format!("Suspicious flag detected: {flag}"));
                    return false;
                }
            }
            if sec.suspicious_flag_exact.iter().any(|exact| exact == flag) {
                Printer::global().warning(&format!("Suspicious flag detected: {flag}"));
                return false;
            }
            if flag == "-x"
                && let Some(next_flag) = flags.get(idx + 1)
                && next_flag.starts_with("assembler")
            {
                Printer::global().warning(&format!("Suspicious flag detected: -x {next_flag}"));
                return false;
            }
            if flag == "-C"
                && let Some(next_flag) = flags.get(idx + 1)
                && (next_flag.starts_with("linker=") || next_flag.starts_with("link-arg="))
            {
                Printer::global().warning(&format!("Suspicious flag detected: -C {next_flag}"));
                return false;
            }
        }
        true
    }
}

/// Validates user-supplied filesystem paths against traversal and injection hazards.
#[derive(Debug, Clone, Copy, Default)]
pub struct Validator;

impl Validator {
    /// Return `true` if `path` contains no control bytes (`< 32`), `..` segments, or shell metacharacters.
    pub fn validate_path(path: impl AsRef<Path>) -> bool {
        let p = path.as_ref();
        let bytes = p.as_os_str().as_encoded_bytes();

        // 1. Reject ASCII control characters (< 32), including NUL, LF, CR, and TAB.
        if bytes.iter().any(|&b| b < 32) {
            return false;
        }

        // 2. Reject parent-directory traversal segments (`..`).
        if p.components().any(|c| matches!(c, Component::ParentDir)) {
            return false;
        }

        // 3. Reject dangerous shell metacharacters anywhere in the path.
        if bytes
            .iter()
            .any(|&b| matches!(b, b';' | b'&' | b'|' | b'`' | b'$' | b'(' | b')' | b'<' | b'>'))
        {
            return false;
        }

        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper converting string tuples into owned `(String, String)` pairs.
    fn env_pairs(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect()
    }

    /// Port of `dev:tests/test_security.py::test_sanitize_execution_env`.
    #[test]
    fn test_sanitize_execution_env() {
        let base = env_pairs(&[
            ("LD_PRELOAD", "/lib/evil.so"),
            ("DYLD_INSERT_LIBRARIES", "/lib/evil_mac.dylib"),
            ("DYLD_LIBRARY_PATH", "/custom/lib"),
            ("PYTHONPATH", "/malicious/python"),
            ("NODE_OPTIONS", "--require /evil.js"),
            ("SAFE_VAR", "hello"),
        ]);

        let sanitized = SecurityManager::sanitize_env_from(base, None, false);
        assert!(!sanitized.contains_key("LD_PRELOAD"));
        assert!(!sanitized.contains_key("DYLD_INSERT_LIBRARIES"));
        assert!(!sanitized.contains_key("DYLD_LIBRARY_PATH"));
        assert!(!sanitized.contains_key("PYTHONPATH"));
        assert!(!sanitized.contains_key("NODE_OPTIONS"));
        assert_eq!(sanitized.get("SAFE_VAR").map(String::as_str), Some("hello"));
    }

    /// Port of `dev:tests/test_security.py::test_check_suspicious_flags`.
    #[test]
    fn test_check_suspicious_flags() {
        let s = |vals: &[&str]| vals.iter().map(|v| (*v).to_string()).collect::<Vec<_>>();

        assert!(SecurityManager::check_suspicious_flags(&s(&["-g", "-Wall", "-O3"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-fplugin=/tmp/evil.so"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-fplugin-arg-evil=1"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-Wl,-rpath,/tmp"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-Wl,--wrap,malloc"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-x", "assembler"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-specs=/tmp/evil.spec"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-specs", "evil.spec"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-wrapper", "/bin/sh"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-wrapper=/bin/sh"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-Xclang", "-load"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-Clinker=/bin/sh"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-C", "linker=/bin/sh"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&["-Clink-arg=-Wl,-rpath,/tmp"])));
        assert!(!SecurityManager::check_suspicious_flags(&s(&[
            "-C",
            "link-arg=-Wl,-rpath,/tmp"
        ])));
    }

    /// Port of `dev:tests/test_security.py::test_check_root_allow_override`.
    #[test]
    fn test_check_root_allow_override() {
        assert!(SecurityManager::check_root_with_status(true, true).is_ok());
        assert!(SecurityManager::check_root_with_status(false, false).is_ok());
    }

    /// Port of `dev:tests/test_security.py::test_check_root_blocked`.
    #[test]
    fn test_check_root_blocked() {
        let err = SecurityManager::check_root_with_status(true, false)
            .expect_err("root without allow_root must fail");
        assert!(matches!(err, RunError::Config(_)));
        assert!(err.to_string().contains("blocked"));
    }

    /// Port of `dev:tests/test_security.py::test_sanitize_execution_env_expanded_dangerous_vars`.
    #[test]
    fn test_sanitize_execution_env_expanded_dangerous_vars() {
        let dangerous = [
            ("LD_AUDIT", "/lib/audit.so"),
            ("GLIBC_TUNABLES", "glibc.malloc.check=1"),
            ("GCONV_PATH", "/custom/gconv"),
            ("BASH_ENV", "/tmp/evil_bash"),
            ("ENV", "/tmp/evil_sh"),
            ("PROMPT_COMMAND", "malicious_cmd"),
            ("JAVA_TOOL_OPTIONS", "-javaagent:/evil.jar"),
            ("_JAVA_OPTIONS", "-Xbootclasspath:/evil.jar"),
            ("RUBYOPT", "-r/evil.rb"),
            ("PERL5OPT", "-Mevil"),
            ("RUSTFLAGS", "-Clink-arg=-Wl,-rpath,/tmp"),
            ("RUSTC_WRAPPER", "/tmp/evil_rustc"),
            ("GOFLAGS", "-buildmode=plugin"),
            ("DYLD_FALLBACK_LIBRARY_PATH", "/tmp/dyld"),
        ];

        let sanitized = SecurityManager::sanitize_env_from(env_pairs(&dangerous), None, false);
        for (k, _) in dangerous {
            assert!(!sanitized.contains_key(k), "{k} should have been sanitized");
        }
    }

    /// Port of `dev:tests/test_security.py::test_custom_env_cannot_reintroduce_dangerous_vars`.
    #[test]
    fn test_custom_env_cannot_reintroduce_dangerous_vars() {
        let mut custom = BTreeMap::new();
        custom.insert("LD_PRELOAD".to_string(), "/tmp/evil.so".to_string());
        custom.insert("GLIBC_TUNABLES".to_string(), "glibc.malloc=1".to_string());
        custom.insert("SAFE_CUSTOM".to_string(), "safe_val".to_string());

        let sanitized = SecurityManager::sanitize_env_from(Vec::new(), Some(&custom), false);
        assert!(!sanitized.contains_key("LD_PRELOAD"));
        assert!(!sanitized.contains_key("GLIBC_TUNABLES"));
        assert_eq!(
            sanitized.get("SAFE_CUSTOM").map(String::as_str),
            Some("safe_val")
        );
    }

    /// Port of `dev:tests/test_security.py::test_strict_whitelist_sandbox_env`.
    #[test]
    fn test_strict_whitelist_sandbox_env() {
        let base = env_pairs(&[
            ("SECRET_AWS_KEY", "AKIA12345678"),
            ("GITHUB_TOKEN", "ghp_xyz123"),
            ("PATH", "/usr/bin:/bin"),
            ("HOME", "/home/user"),
        ]);

        let sanitized = SecurityManager::sanitize_env_from(base, None, true);
        assert!(!sanitized.contains_key("SECRET_AWS_KEY"));
        assert!(!sanitized.contains_key("GITHUB_TOKEN"));
        assert_eq!(
            sanitized.get("PATH").map(String::as_str),
            Some("/usr/bin:/bin")
        );
        assert_eq!(
            sanitized.get("HOME").map(String::as_str),
            Some("/home/user")
        );
    }

    /// Port of `dev:tests/test_security.py::test_strict_whitelist_preserves_container_daemon_env`.
    #[test]
    fn test_strict_whitelist_preserves_container_daemon_env() {
        let base = env_pairs(&[
            ("DOCKER_HOST", "unix:///var/run/docker.sock"),
            ("DOCKER_CONTEXT", "desktop-linux"),
            ("PODMAN_SOCKET", "/run/podman/podman.sock"),
            ("CONTAINER_HOST", "ssh://user@remote"),
        ]);

        let sanitized = SecurityManager::sanitize_env_from(base, None, true);
        assert_eq!(
            sanitized.get("DOCKER_HOST").map(String::as_str),
            Some("unix:///var/run/docker.sock")
        );
        assert_eq!(
            sanitized.get("DOCKER_CONTEXT").map(String::as_str),
            Some("desktop-linux")
        );
        assert_eq!(
            sanitized.get("PODMAN_SOCKET").map(String::as_str),
            Some("/run/podman/podman.sock")
        );
        assert_eq!(
            sanitized.get("CONTAINER_HOST").map(String::as_str),
            Some("ssh://user@remote")
        );
    }

    /// Port of `dev:tests/test_security.py::test_validator_full_path_checks`.
    #[test]
    fn test_validator_full_path_checks() {
        // Safe paths
        assert!(Validator::validate_path(Path::new("main.c")));
        assert!(Validator::validate_path(Path::new("src/nested/main.cpp")));
        assert!(Validator::validate_path("simple.py"));

        // Path traversal
        assert!(!Validator::validate_path(Path::new("../main.c")));
        assert!(!Validator::validate_path(Path::new("sub/../main.c")));
        assert!(!Validator::validate_path("../../../etc/passwd"));

        // Control characters
        assert!(!Validator::validate_path("main\x00.c"));
        assert!(!Validator::validate_path("src/\nmain.c"));
        assert!(!Validator::validate_path("src/\rmain.c"));
        assert!(!Validator::validate_path("src/\tmain.c"));

        // Shell metacharacters anywhere in path
        assert!(!Validator::validate_path("dir;evil/main.c"));
        assert!(!Validator::validate_path("main;echo.c"));
        assert!(!Validator::validate_path("dir|cat/main.c"));
        assert!(!Validator::validate_path("$(whoami).c"));
        assert!(!Validator::validate_path("dir`rm`/main.c"));
        assert!(!Validator::validate_path("dir&bg/main.c"));
        assert!(!Validator::validate_path("<input>.c"));
        assert!(!Validator::validate_path(">output>.c"));
    }
}
