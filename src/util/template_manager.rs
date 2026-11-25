//! Starter code and multi-file project template scaffolding (`--new` / `--template`).
//!
//! Adapted from Python's `src/util/template_manager.py`. Reads the 11 built-in
//! language templates from `[package.metadata.run.templates.builtins]` in
//! `Cargo.toml` via `serde` + `toml`, supports custom `[templates.<name>]`
//! definitions (`Inline`, `content`, `file`, and multi-file `files`), interpolates
//! `{{name}}`, `{{filename}}`, `{{date}}`, and `{{year}}` using local OS time
//! (`libc::localtime_r` on Unix), and enforces strict path containment via
//! existing-ancestor canonicalization.

use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Deserialize;

use crate::util::config::{Config, TemplateConfig, TemplateFileEntry, TemplateTable};
use crate::model::error::RunError;
use crate::util::output::{Printer, TagColor};

/// Embedded `Cargo.toml` used to parse `[package.metadata.run.templates]` defaults.
const EMBEDDED_CARGO_TOML: &str = include_str!("../../Cargo.toml");

/// Cached singleton of `[package.metadata.run.templates]`.
static TEMPLATES_METADATA: OnceLock<TemplatesMetadata> = OnceLock::new();

/// Top-level `Cargo.toml` wrapper for extracting `[package.metadata.run.templates]`.
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

/// `[package.metadata]` subset containing `run.templates`.
#[derive(Debug, Deserialize)]
struct CargoMetadata {
    /// `[package.metadata.run]` subset.
    run: RunTemplatesSection,
}

/// `[package.metadata.run]` subset holding `[package.metadata.run.templates]`.
#[derive(Debug, Deserialize)]
struct RunTemplatesSection {
    /// Built-in templates table.
    templates: TemplatesMetadata,
}

/// Built-in template registry parsed from `[package.metadata.run.templates]`.
#[derive(Debug, Clone, Deserialize)]
pub struct TemplatesMetadata {
    /// Mapping from dotted extension (e.g. `".rs"`) to template body.
    pub builtins: BTreeMap<String, String>,
}

impl TemplatesMetadata {
    /// Return the cached [`TemplatesMetadata`] deserialized from embedded `Cargo.toml`.
    pub fn get() -> &'static Self {
        TEMPLATES_METADATA.get_or_init(|| {
            let manifest: CargoManifest = toml::from_str(EMBEDDED_CARGO_TOML)
                .expect("embedded Cargo.toml must contain valid [package.metadata.run.templates]");
            manifest.package.metadata.run.templates
        })
    }
}

/// Resolved `(year, month, day)` in local calendar time for template interpolation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LocalDate {
    /// Four-digit year (e.g. `2026`).
    pub year: i32,
    /// Month `1..=12`.
    pub month: u8,
    /// Day of month `1..=31`.
    pub day: u8,
}

impl LocalDate {
    /// Return today's local calendar date using `libc::localtime_r` on Unix or UTC civil fallback elsewhere.
    pub fn today() -> Self {
        #[cfg(unix)]
        {
            // SAFETY: `libc::time` and `libc::localtime_r` write to stack-allocated `time_t` / `tm`.
            unsafe {
                let now = libc::time(std::ptr::null_mut());
                let mut tm: libc::tm = std::mem::zeroed();
                if !libc::localtime_r(&now, &mut tm).is_null() {
                    return Self {
                        year: tm.tm_year + 1900,
                        month: (tm.tm_mon + 1) as u8,
                        day: tm.tm_mday as u8,
                    };
                }
            }
        }
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_secs() as i64);
        civil_from_unix_days(secs.div_euclid(86_400))
    }

    /// Format as `YYYY-MM-DD`.
    pub fn ymd(self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }

    /// Format as `YYYY`.
    pub fn year_str(self) -> String {
        format!("{:04}", self.year)
    }
}

/// Howard Hinnant's civil-from-days algorithm for non-Unix fallback date calculation.
fn civil_from_unix_days(z: i64) -> LocalDate {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = (y + if m <= 2 { 1 } else { 0 }) as i32;
    LocalDate {
        year,
        month: m as u8,
        day: d as u8,
    }
}

/// Maximum symbolic link hops followed when resolving containment paths.
const MAX_SYMLINK_DEPTH: usize = 40;

/// Lexically normalize `path` onto an absolute path, failing closed if `..` escapes root.
fn lexical_normalize(path: &Path) -> Result<PathBuf, RunError> {
    let abs = std::path::absolute(path).map_err(RunError::Io)?;
    let mut out = PathBuf::new();
    let mut normal_depth = 0usize;
    for comp in abs.components() {
        match comp {
            Component::Prefix(p) => out.push(p.as_os_str()),
            Component::RootDir => out.push(Component::RootDir.as_os_str()),
            Component::CurDir => {}
            Component::Normal(seg) => {
                out.push(seg);
                normal_depth += 1;
            }
            Component::ParentDir => {
                if normal_depth == 0 {
                    return Err(RunError::config(format!(
                        "Path traversal detected: '{}' escapes root (outside base directory).",
                        path.display()
                    )));
                }
                out.pop();
                normal_depth -= 1;
            }
        }
    }
    Ok(out)
}

/// Walk every prefix component of a lexically normalized path using `fs::symlink_metadata`.
///
/// Resolves both existing and dangling symlinks up to [`MAX_SYMLINK_DEPTH`] hops so
/// dangling symlinks targeting paths outside `base` cannot bypass containment.
fn resolve_normalized_with_symlinks(normalized: &Path, depth: usize) -> Result<PathBuf, RunError> {
    let mut current = PathBuf::new();
    let mut comps = normalized.components();
    while let Some(comp) = comps.next() {
        match comp {
            Component::Prefix(p) => current.push(p.as_os_str()),
            Component::RootDir => current.push(Component::RootDir.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                return Err(RunError::config(format!(
                    "Path traversal detected: unexpected parent segment in '{}'.",
                    normalized.display()
                )));
            }
            Component::Normal(seg) => {
                current.push(seg);
                match fs::symlink_metadata(&current) {
                    Ok(meta) if meta.file_type().is_symlink() => {
                        if depth >= MAX_SYMLINK_DEPTH {
                            return Err(RunError::config(format!(
                                "Too many levels of symbolic links resolving '{}'.",
                                normalized.display()
                            )));
                        }
                        let link_target = fs::read_link(&current).map_err(RunError::Io)?;
                        let combined = if link_target.is_absolute() {
                            link_target
                        } else {
                            let parent = current.parent().unwrap_or_else(|| Path::new("/"));
                            parent.join(link_target)
                        };
                        let mut full = lexical_normalize(&combined)?;
                        for rem in comps {
                            full.push(rem.as_os_str());
                        }
                        return resolve_normalized_with_symlinks(&full, depth + 1);
                    }
                    Ok(_) => {}
                    Err(e)
                        if e.kind() == io::ErrorKind::NotFound
                            || e.kind() == io::ErrorKind::NotADirectory =>
                    {
                        for rem in comps {
                            current.push(rem.as_os_str());
                        }
                        break;
                    }
                    Err(e) => return Err(RunError::Io(e)),
                }
            }
        }
    }
    Ok(current)
}

/// Canonicalize `path` by lexically normalizing `..` and resolving every existing/dangling symlink prefix.
///
/// Defeats `nonexistent/../../escape` and dangling symlink escapes while supporting
/// paths whose leaf or intermediate directories do not yet exist on disk.
fn canonicalize_with_existing_ancestor(path: &Path) -> Result<PathBuf, RunError> {
    let normalized = lexical_normalize(path)?;
    resolve_normalized_with_symlinks(&normalized, 0)
}

/// Resolve `path` relative to `base` and verify it stays strictly within `base`.
fn resolve_contained_path(path: &Path, base: &Path) -> Result<(PathBuf, PathBuf), RunError> {
    let resolved_base = canonicalize_with_existing_ancestor(base)?;
    let candidate = if path.is_absolute() {
        path.to_path_buf()
    } else {
        resolved_base.join(path)
    };
    let resolved_target = canonicalize_with_existing_ancestor(&candidate)?;
    Ok((resolved_base, resolved_target))
}

/// Interpolate `{{name}}`, `{{filename}}`, `{{date}}`, and `{{year}}` placeholders.
fn interpolate(content: &str, target_path: &Path, date: LocalDate) -> String {
    let stem = target_path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    let filename = target_path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    content
        .replace("{{name}}", stem)
        .replace("{{filename}}", filename)
        .replace("{{date}}", &date.ymd())
        .replace("{{year}}", &date.year_str())
}

/// Load a template's raw text from inline content or an external `file` contained within `base`.
fn load_content_or_file(
    content: Option<&str>,
    file: Option<&str>,
    base_dir: &Path,
) -> Result<Option<String>, RunError> {
    if let Some(text) = content {
        return Ok(Some(text.to_string()));
    }
    if let Some(raw_path) = file {
        let (resolved_base, resolved_file) =
            resolve_contained_path(Path::new(raw_path), base_dir)?;
        if !resolved_file.starts_with(&resolved_base) {
            return Err(RunError::config(format!(
                "Template file '{raw_path}' resolves outside base directory."
            )));
        }
        if !resolved_file.exists() {
            return Err(RunError::config(format!(
                "Template file not found: {}",
                resolved_file.display()
            )));
        }
        let text = fs::read_to_string(&resolved_file).map_err(RunError::Io)?;
        return Ok(Some(text));
    }
    Ok(None)
}

/// Selected template source resolved from custom config or built-ins.
enum ResolvedTemplate<'a> {
    /// Inline single-file template body.
    Inline(&'a str),
    /// Structured `[templates.<name>]` table from `Run.toml`.
    Table(&'a TemplateTable),
}

/// Generates single-file and multi-file starter templates (`--new` / `--template`).
#[derive(Debug, Clone, Copy, Default)]
pub struct TemplateManager;

impl TemplateManager {
    /// Return sorted built-in template identifiers without leading `.` (e.g. `["c", "cc", "cpp", ...]`).
    pub fn builtin_names() -> Vec<String> {
        TemplatesMetadata::get()
            .builtins
            .keys()
            .map(|k| k.trim_start_matches('.').to_string())
            .collect()
    }

    /// Look up the raw built-in template text by extension (with or without leading `.`).
    pub fn builtin_template(name_or_ext: &str) -> Option<&'static str> {
        let key = if name_or_ext.starts_with('.') {
            name_or_ext.to_string()
        } else {
            format!(".{name_or_ext}")
        };
        TemplatesMetadata::get()
            .builtins
            .get(&key)
            .map(String::as_str)
    }

    /// Load template text from a [`TemplateTable`] while enforcing external `file` containment.
    pub fn load_table_content(
        table: &TemplateTable,
        base_dir: &Path,
    ) -> Result<Option<String>, RunError> {
        load_content_or_file(table.content.as_deref(), table.file.as_deref(), base_dir)
    }

    /// Load template text for a single [`TemplateFileEntry`].
    fn load_entry_content(
        entry: &TemplateFileEntry,
        base_dir: &Path,
    ) -> Result<String, RunError> {
        Ok(load_content_or_file(
            entry.content.as_deref(),
            entry.file.as_deref(),
            base_dir,
        )?
        .unwrap_or_default())
    }

    /// Generate single-file or multi-file scaffolding for `target_name`.
    pub fn generate(
        target_name: &str,
        template_name: Option<&str>,
        config: Option<&Config>,
        force: bool,
        base_dir: Option<&Path>,
    ) -> Result<bool, RunError> {
        Self::generate_with_date(
            target_name,
            template_name,
            config,
            force,
            base_dir,
            LocalDate::today(),
        )
    }

    /// Deterministic implementation of [`Self::generate`] accepting an explicit [`LocalDate`].
    pub fn generate_with_date(
        target_name: &str,
        template_name: Option<&str>,
        config: Option<&Config>,
        force: bool,
        base_dir: Option<&Path>,
        date: LocalDate,
    ) -> Result<bool, RunError> {
        let cwd_fallback = std::env::current_dir().map_err(RunError::Io)?;
        let base_input = base_dir.unwrap_or(&cwd_fallback);
        let target = Path::new(target_name);
        let (resolved_base, target_resolved) = resolve_contained_path(target, base_input)?;

        if !target_resolved.starts_with(&resolved_base) {
            return Err(RunError::config(format!(
                "Path traversal detected in target '{target_name}'. Destination must be inside current working directory '{}'.",
                resolved_base.display()
            )));
        }

        let builtins = &TemplatesMetadata::get().builtins;
        let custom_templates = config.map(|c| &c.templates);

        let selected: ResolvedTemplate<'_> = if let Some(tname) = template_name {
            if let Some(cfg_map) = custom_templates
                && let Some(tcfg) = cfg_map.get(tname)
            {
                match tcfg {
                    TemplateConfig::Inline(s) => ResolvedTemplate::Inline(s.as_str()),
                    TemplateConfig::Table(tbl) => ResolvedTemplate::Table(tbl),
                }
            } else {
                let ext = if tname.starts_with('.') {
                    tname.to_string()
                } else {
                    format!(".{tname}")
                };
                if let Some(body) = builtins.get(&ext) {
                    ResolvedTemplate::Inline(body.as_str())
                } else {
                    Printer::global().error(&format!(
                        "Template '{tname}' not found in Run.toml or built-in templates."
                    ));
                    return Ok(false);
                }
            }
        } else {
            let ext = target
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| format!(".{}", e.to_ascii_lowercase()))
                .unwrap_or_default();
            let ext_no_dot = ext.strip_prefix('.').unwrap_or("");
            let file_name = target.file_name().and_then(|n| n.to_str()).unwrap_or("");

            if !ext_no_dot.is_empty()
                && let Some(cfg_map) = custom_templates
                && let Some(tcfg) = cfg_map.get(ext_no_dot)
            {
                match tcfg {
                    TemplateConfig::Inline(s) => ResolvedTemplate::Inline(s.as_str()),
                    TemplateConfig::Table(tbl) => ResolvedTemplate::Table(tbl),
                }
            } else if !ext.is_empty()
                && let Some(body) = builtins.get(&ext)
            {
                ResolvedTemplate::Inline(body.as_str())
            } else if !file_name.is_empty()
                && let Some(cfg_map) = custom_templates
                && let Some(tcfg) = cfg_map.get(file_name)
            {
                match tcfg {
                    TemplateConfig::Inline(s) => ResolvedTemplate::Inline(s.as_str()),
                    TemplateConfig::Table(tbl) => ResolvedTemplate::Table(tbl),
                }
            } else {
                Printer::global().error(&format!(
                    "Cannot infer template for '{target_name}'. Specify --template <name>."
                ));
                return Ok(false);
            }
        };

        if let ResolvedTemplate::Table(tbl) = selected
            && !tbl.files.is_empty()
        {
            let dest_dir = if target.extension().is_none() || target_resolved.is_dir() {
                target_resolved.clone()
            } else {
                target_resolved
                    .parent()
                    .map(Path::to_path_buf)
                    .unwrap_or_else(|| resolved_base.clone())
            };

            // Pre-validate every file entry before creating directories or writing any file.
            let mut planned: Vec<(PathBuf, String)> = Vec::new();
            for entry in &tbl.files {
                if entry.name.is_empty() {
                    continue;
                }
                let (resolved_dest_dir, file_dest) =
                    resolve_contained_path(Path::new(&entry.name), &dest_dir)?;
                if !file_dest.starts_with(&resolved_dest_dir) {
                    return Err(RunError::config(format!(
                        "Path traversal detected in template file entry '{}'. Destination must be inside '{}'.",
                        entry.name,
                        dest_dir.display()
                    )));
                }
                if !force && file_dest.exists() {
                    Printer::global().error(&format!(
                        "File '{}' already exists. Use -f / --force to overwrite.",
                        file_dest.display()
                    ));
                    return Ok(false);
                }
                let raw = Self::load_entry_content(entry, &resolved_base)?;
                let rendered = interpolate(&raw, &file_dest, date);
                planned.push((file_dest, rendered));
            }

            fs::create_dir_all(&dest_dir).map_err(RunError::Io)?;
            for (file_dest, rendered) in planned {
                if let Some(parent) = file_dest.parent() {
                    fs::create_dir_all(parent).map_err(RunError::Io)?;
                }
                fs::write(&file_dest, rendered).map_err(RunError::Io)?;
                Printer::global().action(
                    "CREATE",
                    &format!("Generated {}", file_dest.display()),
                    TagColor::Green,
                );
            }
            return Ok(true);
        }

        let raw_content = match selected {
            ResolvedTemplate::Inline(s) => Some(s.to_string()),
            ResolvedTemplate::Table(tbl) => Self::load_table_content(tbl, &resolved_base)?,
        };
        let Some(raw) = raw_content else {
            Printer::global().error(&format!(
                "Could not load template content for '{}'.",
                template_name.unwrap_or(target_name)
            ));
            return Ok(false);
        };

        if target_resolved.exists() && !force {
            Printer::global().error(&format!(
                "File '{}' already exists. Use -f / --force to overwrite.",
                target_resolved.display()
            ));
            return Ok(false);
        }

        if let Some(parent) = target_resolved.parent() {
            fs::create_dir_all(parent).map_err(RunError::Io)?;
        }
        let interpolated = interpolate(&raw, &target_resolved, date);
        fs::write(&target_resolved, interpolated).map_err(RunError::Io)?;
        Printer::global().action(
            "CREATE",
            &format!("Generated {}", target_resolved.display()),
            TagColor::Green,
        );
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Create an isolated temporary directory for template tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_tmpl_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Port of `dev:tests/test_templates.py::test_builtin_templates_single_file` (covering all 11 built-ins).
    #[test]
    fn test_builtin_templates_single_file() {
        let tmp = make_temp_dir("builtins");
        assert_eq!(TemplateManager::builtin_names().len(), 11);

        let py_target = tmp.join("app.py");
        assert!(TemplateManager::generate(
            &py_target.to_string_lossy(),
            None,
            None,
            false,
            Some(&tmp)
        )
        .expect("generate py"));
        let py_content = fs::read_to_string(&py_target).expect("read app.py");
        assert!(py_content.contains("def main():"));
        assert!(py_content.contains("if __name__ == \"__main__\":"));

        let java_target = tmp.join("Solution.java");
        assert!(TemplateManager::generate(
            &java_target.to_string_lossy(),
            None,
            None,
            false,
            Some(&tmp)
        )
        .expect("generate java"));
        let java_content = fs::read_to_string(&java_target).expect("read Solution.java");
        assert!(java_content.contains("public class Solution {"));

        let cpp_target = tmp.join("main.cpp");
        assert!(TemplateManager::generate(
            &cpp_target.to_string_lossy(),
            None,
            None,
            false,
            Some(&tmp)
        )
        .expect("generate cpp"));
        assert!(fs::read_to_string(&cpp_target)
            .expect("read main.cpp")
            .contains("#include <iostream>"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_templates.py::test_custom_template_inline` + date/year interpolation.
    #[test]
    fn test_custom_template_inline_and_interpolation() {
        let tmp = make_temp_dir("custom_inline");
        let cfg = Config::from_toml_str(
            r#"
[templates.cp]
content = """// CP Template for {{name}} ({{filename}}) on {{date}} ({{year}})
#include <bits/stdc++.h>
using namespace std;
int main() { return 0; }
"""
"#,
            None,
            None,
        )
        .expect("parse cfg");

        let fixed_date = LocalDate {
            year: 2026,
            month: 11,
            day: 18,
        };
        assert!(TemplateManager::generate_with_date(
            "task_a.cpp",
            Some("cp"),
            Some(&cfg),
            false,
            Some(&tmp),
            fixed_date,
        )
        .expect("generate cp"));

        let text = fs::read_to_string(tmp.join("task_a.cpp")).expect("read task_a.cpp");
        assert!(text.contains("// CP Template for task_a (task_a.cpp) on 2026-11-18 (2026)"));
        assert!(text.contains("#include <bits/stdc++.h>"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_templates.py::test_multi_file_template`.
    #[test]
    fn test_multi_file_template() {
        let tmp = make_temp_dir("multifile");
        let cfg = Config::from_toml_str(
            r#"
[templates.leetcode_rs]
description = "LeetCode Rust starter"
[[templates.leetcode_rs.files]]
name = "main.rs"
content = "mod solve;\nuse solve::Solution;\nfn main() { let _ = Solution; }\n"
[[templates.leetcode_rs.files]]
name = "solve.rs"
content = "pub struct Solution;\n"
"#,
            None,
            None,
        )
        .expect("parse cfg");

        let ok = TemplateManager::generate("problem_01", Some("leetcode_rs"), Some(&cfg), false, Some(&tmp))
            .expect("generate multi-file");
        assert!(ok);
        let dir = tmp.join("problem_01");
        assert!(fs::read_to_string(dir.join("main.rs"))
            .expect("read main.rs")
            .contains("mod solve;"));
        assert!(fs::read_to_string(dir.join("solve.rs"))
            .expect("read solve.rs")
            .contains("pub struct Solution;"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_templates.py::test_overwrite_safety`.
    #[test]
    fn test_overwrite_safety() {
        let tmp = make_temp_dir("overwrite");
        let target = tmp.join("existing.py");
        fs::write(&target, "ORIGINAL CONTENT").expect("write existing");

        assert!(!TemplateManager::generate("existing.py", None, None, false, Some(&tmp))
            .expect("no overwrite"));
        assert_eq!(fs::read_to_string(&target).expect("read"), "ORIGINAL CONTENT");

        assert!(TemplateManager::generate("existing.py", None, None, true, Some(&tmp))
            .expect("force overwrite"));
        assert!(fs::read_to_string(&target)
            .expect("read")
            .contains("def main():"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_templates.py::test_multi_file_template_path_traversal_prevention`.
    #[test]
    fn test_multi_file_template_path_traversal_prevention() {
        let tmp = make_temp_dir("multi_traversal");
        let cfg = Config::from_toml_str(
            r#"
[templates.malicious]
[[templates.malicious.files]]
name = "safe.txt"
content = "should not be written due to pre-validation"
[[templates.malicious.files]]
name = "../escape.txt"
content = "pwned"
"#,
            None,
            None,
        )
        .expect("parse cfg");

        let dest = tmp.join("sandbox_dir");
        fs::create_dir_all(&dest).expect("mkdir dest");
        let err = TemplateManager::generate("sandbox_dir", Some("malicious"), Some(&cfg), true, Some(&tmp))
            .expect_err("traversal must fail");
        assert!(err.to_string().contains("Path traversal detected"));
        assert!(!tmp.join("escape.txt").exists());
        assert!(!dest.join("safe.txt").exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_templates.py::test_file_loader_path_traversal_prevention`.
    #[test]
    fn test_file_loader_path_traversal_prevention() {
        let tmp = make_temp_dir("file_traversal");
        fs::write(tmp.join("secret.txt"), "super_secret").expect("write secret");
        let sub_dir = tmp.join("project");
        fs::create_dir_all(&sub_dir).expect("mkdir sub_dir");

        let tbl = TemplateTable {
            file: Some("../secret.txt".to_string()),
            ..TemplateTable::default()
        };
        let err = TemplateManager::load_table_content(&tbl, &sub_dir)
            .expect_err("outside file must fail");
        assert!(err.to_string().contains("outside base directory"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_templates.py::test_single_file_template_path_traversal_prevention`.
    #[test]
    fn test_single_file_template_path_traversal_prevention() {
        let tmp = make_temp_dir("single_traversal");
        let workspace = tmp.join("workspace");
        fs::create_dir_all(&workspace).expect("mkdir workspace");

        let err = TemplateManager::generate("../../../escape.py", None, None, true, Some(&workspace))
            .expect_err("single file traversal must fail");
        assert!(err.to_string().contains("Path traversal detected"));
        assert!(!tmp.join("escape.py").exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: single-file target with `nonexistent/../../escape.py` must be rejected atomically.
    #[test]
    fn test_single_file_nonexistent_parent_traversal_rejected() {
        let tmp = make_temp_dir("nonexistent_single");
        let ws = tmp.join("workspace");
        fs::create_dir_all(&ws).expect("mkdir workspace");

        let err = TemplateManager::generate(
            "nonexistent/../../escape.py",
            None,
            None,
            true,
            Some(&ws),
        )
        .expect_err("nonexistent/../../escape.py must fail closed");
        assert!(matches!(err, RunError::Config(_)));
        assert!(!ws.join("nonexistent").exists());
        assert!(!tmp.join("escape.py").exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: multi-file template with valid first entry and `a/../../escape.txt` writes zero files.
    #[test]
    fn test_multi_file_nonexistent_parent_traversal_atomic_zero_writes() {
        let tmp = make_temp_dir("nonexistent_multi");
        let ws = tmp.join("workspace");
        fs::create_dir_all(&ws).expect("mkdir workspace");

        let cfg = Config::from_toml_str(
            r#"
[templates.atomic_escape]
[[templates.atomic_escape.files]]
name = "dir_one/first.txt"
content = "should never be created"
[[templates.atomic_escape.files]]
name = "a/../../escape.txt"
content = "escaped"
"#,
            None,
            None,
        )
        .expect("parse cfg");

        let err = TemplateManager::generate(
            "proj",
            Some("atomic_escape"),
            Some(&cfg),
            true,
            Some(&ws),
        )
        .expect_err("a/../../escape.txt must fail closed");
        assert!(matches!(err, RunError::Config(_)));
        assert!(!ws.join("proj").join("dir_one").join("first.txt").exists());
        assert!(!ws.join("escape.txt").exists());
        assert!(!tmp.join("escape.txt").exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: dangling symlink targeting outside `base_dir` must be rejected for both `force = false` and `force = true`.
    #[cfg(unix)]
    #[test]
    fn test_dangling_symlink_escape_rejected() {
        let tmp = make_temp_dir("dangling_symlink");
        let ws = tmp.join("workspace");
        let outside = tmp.join("outside");
        fs::create_dir_all(&ws).expect("mkdir workspace");
        fs::create_dir_all(&outside).expect("mkdir outside");

        let outside_target = outside.join("created.py");
        std::os::unix::fs::symlink(&outside_target, ws.join("dangling.py"))
            .expect("create dangling symlink");

        for force in [false, true] {
            let err = TemplateManager::generate("dangling.py", None, None, force, Some(&ws))
                .expect_err("dangling symlink outside base_dir must be rejected");
            assert!(matches!(err, RunError::Config(_)));
            assert!(!outside_target.exists());
        }

        let _ = fs::remove_dir_all(&tmp);
    }
}
