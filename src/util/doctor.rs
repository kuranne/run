//! System toolchain and environment diagnostics scanner (`--doctor`).
//!
//! Adapted from Python's `src/util/doctor.py`. Reads the 7 ordered toolchain
//! groups (20 compiler/runtime probes) and truncation rules from
//! `[package.metadata.run.doctor]` in `Cargo.toml` via `serde` + `toml`, and
//! uses [`MonitoredChild`] with `limits.doctor_probe_timeout_secs` from
//! [`crate::util::env::PackageMetadata::get`] to avoid hanging probes.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::Duration;

use serde::Deserialize;

use crate::util::env::PackageMetadata;
use crate::util::output::{Colors, TagColor};
use crate::util::process::MonitoredChild;

/// Embedded `Cargo.toml` used to parse `[package.metadata.run.doctor]` defaults.
const EMBEDDED_CARGO_TOML: &str = include_str!("../../Cargo.toml");

/// Cached singleton of `[package.metadata.run.doctor]`.
static DOCTOR_METADATA: OnceLock<DoctorMetadata> = OnceLock::new();

/// Top-level `Cargo.toml` wrapper for extracting `[package.metadata.run.doctor]`.
#[derive(Debug, Deserialize)]
struct CargoManifest {
    /// `[package]` table.
    package: CargoPackage,
}

/// `[package]` subset for metadata extraction.
#[derive(Debug, Deserialize)]
struct CargoPackage {
    /// `[package.metadata]` table.
    metadata: CargoMetadata,
}

/// `[package.metadata]` subset containing `run.doctor`.
#[derive(Debug, Deserialize)]
struct CargoMetadata {
    /// `[package.metadata.run]` subset.
    run: RunDoctorSection,
}

/// `[package.metadata.run]` subset holding `[package.metadata.run.doctor]`.
#[derive(Debug, Deserialize)]
struct RunDoctorSection {
    /// Doctor configuration section.
    doctor: DoctorMetadata,
}

/// Single named toolchain group from `[[package.metadata.run.doctor.toolchain_groups]]`.
#[derive(Debug, Clone, Deserialize)]
pub struct ToolchainGroup {
    /// Display header for the language/toolchain group.
    pub name: String,
    /// Ordered `(binary, version_arg)` probe pairs.
    pub tools: Vec<[String; 2]>,
}

/// Static diagnostics configuration parsed from `[package.metadata.run.doctor]`.
#[derive(Debug, Clone, Deserialize)]
pub struct DoctorMetadata {
    /// Maximum version-line length before truncation.
    pub truncation_limit: usize,
    /// Ellipsis suffix appended when truncating long version lines.
    pub ellipsis: String,
    /// Ordered toolchain probe groups.
    pub toolchain_groups: Vec<ToolchainGroup>,
}

impl DoctorMetadata {
    /// Return the cached [`DoctorMetadata`] deserialized from embedded `Cargo.toml`.
    pub fn get() -> &'static Self {
        DOCTOR_METADATA.get_or_init(|| {
            let manifest: CargoManifest = toml::from_str(EMBEDDED_CARGO_TOML)
                .expect("embedded Cargo.toml must contain valid [package.metadata.run.doctor]");
            manifest.package.metadata.run.doctor
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

/// Locate `binary` in the active `PATH` environment variable.
fn find_in_path(binary: &str) -> Option<PathBuf> {
    let bin_path = Path::new(binary);
    if bin_path.components().count() > 1 {
        return is_executable_file(bin_path).then(|| bin_path.to_path_buf());
    }
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(binary);
        if is_executable_file(&candidate) {
            return Some(candidate);
        }
        #[cfg(windows)]
        if bin_path.extension().is_none() {
            for ext in ["exe", "cmd", "bat"] {
                let win_candidate = dir.join(format!("{binary}.{ext}"));
                if is_executable_file(&win_candidate) {
                    return Some(win_candidate);
                }
            }
        }
    }
    None
}

/// Truncate a single version line according to `[package.metadata.run.doctor]`.
fn truncate_version_line(line: &str, limit: usize, ellipsis: &str) -> String {
    let trimmed = line.trim();
    if trimmed.chars().count() < limit {
        return trimmed.to_string();
    }
    let ellipsis_len = ellipsis.chars().count();
    let keep = limit.saturating_sub(ellipsis_len);
    let prefix: String = trimmed.chars().take(keep).collect();
    format!("{prefix}{ellipsis}")
}

/// System toolchain and environment diagnostics scanner (`--doctor`).
#[derive(Debug, Clone, Copy, Default)]
pub struct Doctor;

impl Doctor {
    /// Check if `binary` is installed in `PATH` and retrieve its first version line.
    ///
    /// Probes `[binary, version_arg]` with timeout `limits.doctor_probe_timeout_secs`
    /// using [`MonitoredChild`], falling back to the resolved binary path if output
    /// is empty or the probe fails/times out.
    pub fn check_binary(binary: &str, version_arg: &str) -> Option<String> {
        let path = find_in_path(binary)?;
        let path_str = path.to_string_lossy().into_owned();

        let timeout = Duration::from_secs(PackageMetadata::get().limits.doctor_probe_timeout_secs);
        let mut cmd = Command::new(&path);
        cmd.arg(version_arg)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let Ok(mut child) = MonitoredChild::spawn(&mut cmd) else {
            return Some(path_str);
        };
        let Ok(out) = child.wait_with_timeout(Some(timeout), None) else {
            return Some(path_str);
        };
        if out.timed_out {
            return Some(path_str);
        }

        let stdout = String::from_utf8_lossy(&out.stdout);
        let stderr = String::from_utf8_lossy(&out.stderr);
        let raw = if !stdout.trim().is_empty() {
            stdout.trim()
        } else {
            stderr.trim()
        };

        if let Some(first_line) = raw.lines().next() {
            let doc_meta = DoctorMetadata::get();
            return Some(truncate_version_line(
                first_line,
                doc_meta.truncation_limit,
                &doc_meta.ellipsis,
            ));
        }

        Some(path_str)
    }

    /// Run diagnostics across all configured toolchains, printing to stdout, and return `0`.
    pub fn diagnose(colors: Colors) -> i32 {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        let virtual_env = std::env::var("VIRTUAL_ENV").ok();
        let mut stdout = io::stdout();
        let _ = Self::diagnose_to(&mut stdout, colors, &cwd, virtual_env.as_deref());
        0
    }

    /// Write toolchain and Python virtual-environment diagnostics to `w` and return `0`.
    pub fn diagnose_to(
        w: &mut impl Write,
        colors: Colors,
        cwd: &Path,
        virtual_env: Option<&str>,
    ) -> io::Result<i32> {
        let header = colors.style_text("=== System Toolchain Diagnostics ===", TagColor::Cyan, true);
        writeln!(w, "\n{header}\n")?;

        let doc_meta = DoctorMetadata::get();
        let mut found_total = 0usize;
        let mut checked_total = 0usize;

        for group in &doc_meta.toolchain_groups {
            let group_header = colors.style_text(&format!("{}:", group.name), TagColor::Reset, true);
            writeln!(w, "{group_header}")?;

            for pair in &group.tools {
                let binary = &pair[0];
                let varg = &pair[1];
                checked_total += 1;
                if let Some(version_info) = Self::check_binary(binary, varg) {
                    found_total += 1;
                    let badge = colors.style_text("[ OK ]", TagColor::Green, false);
                    writeln!(w, "  {badge} {binary:<12} : {version_info}")?;
                } else {
                    let badge = colors.style_text("[ -- ]", TagColor::Gray, false);
                    writeln!(
                        w,
                        "  {badge} {binary:<12} : Not installed / not in PATH"
                    )?;
                }
            }
            writeln!(w)?;
        }

        // Check Python venv using VIRTUAL_ENV or configured venv directory names (must be dirs, not `.env` files).
        let venv_dirs = &PackageMetadata::get().extensions.python_venv_dirs;
        let venv_active = virtual_env.is_some_and(|v| !v.is_empty())
            || venv_dirs.iter().any(|dir| cwd.join(dir).is_dir());
        let venv_str = if venv_active {
            "Active / Detected"
        } else {
            "Not detected"
        };
        let env_header = colors.style_text("Environment:", TagColor::Reset, true);
        writeln!(w, "{env_header}")?;
        let venv_badge = if venv_active {
            colors.style_text("[ OK ]", TagColor::Green, false)
        } else {
            colors.style_text("[ -- ]", TagColor::Gray, false)
        };
        writeln!(w, "  {venv_badge} {:<12} : {venv_str}", "Python venv")?;

        let footer = colors.style_text(
            &format!("Detected {found_total}/{checked_total} development tools."),
            TagColor::Cyan,
            false,
        );
        writeln!(w, "\n{footer}\n")?;
        Ok(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Create an isolated temporary directory for doctor tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_doctor_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Verify embedded `[package.metadata.run.doctor]` loads all 7 groups and 20 tools.
    #[test]
    fn test_doctor_metadata_counts() {
        let meta = DoctorMetadata::get();
        assert_eq!(meta.toolchain_groups.len(), 7);
        let total: usize = meta.toolchain_groups.iter().map(|g| g.tools.len()).sum();
        assert_eq!(total, 20);
    }

    /// Port of `dev:tests/test_doctor.py::test_doctor_check_binary`.
    #[test]
    fn test_doctor_check_binary_and_truncation() {
        // Test a ubiquitous binary on macOS/Linux (e.g. `sh` or `rustc`).
        let probe = Doctor::check_binary("rustc", "--version")
            .or_else(|| Doctor::check_binary("python3", "--version"));
        assert!(probe.is_some());

        let missing = Doctor::check_binary("definitely_nonexistent_binary_xyz123", "--version");
        assert_eq!(missing, None);

        let long_line = "a".repeat(80);
        let truncated = truncate_version_line(&long_line, 60, "...");
        assert_eq!(truncated.len(), 60);
        assert!(truncated.ends_with("..."));
    }

    /// Port of `dev:tests/test_doctor.py::test_doctor_diagnose` plus venv dir-vs-file checks.
    #[test]
    fn test_doctor_diagnose_output_and_venv_detection() {
        let tmp = make_temp_dir("diagnose");

        // Regular `.env` file must NOT falsely trigger Python venv detection.
        fs::write(tmp.join(".env"), "SECRET=1\n").expect("write .env file");
        let mut buf_file_only = Vec::new();
        let code = Doctor::diagnose_to(&mut buf_file_only, Colors::new(false), &tmp, None)
            .expect("diagnose_to");
        assert_eq!(code, 0);
        let out_file_only = String::from_utf8(buf_file_only).expect("utf8");
        assert!(out_file_only.contains("=== System Toolchain Diagnostics ==="));
        assert!(out_file_only.contains("C / C++:"));
        assert!(out_file_only.contains("Python:"));
        assert!(out_file_only.contains("Python venv  : Not detected"));
        assert!(out_file_only.contains("development tools."));
        assert!(!out_file_only.contains('\x1b'));

        // Creating `.venv` directory or passing `VIRTUAL_ENV` activates detection.
        fs::create_dir_all(tmp.join(".venv")).expect("create .venv dir");
        let mut buf_venv = Vec::new();
        Doctor::diagnose_to(&mut buf_venv, Colors::new(false), &tmp, None).expect("diagnose venv");
        let out_venv = String::from_utf8(buf_venv).expect("utf8");
        assert!(out_venv.contains("[ OK ] Python venv  : Active / Detected"));

        let _ = fs::remove_dir_all(&tmp);
    }
}
