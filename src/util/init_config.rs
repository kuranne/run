//! Project inspection and tailored `Run.toml` initialization (`--init`).
//!
//! Adapted from Python's `src/util/init_config.py`. Reads ignored discovery
//! directories, excluded extensions, filename/extension technology rules, and
//! debug/release preset defaults from `Cargo.toml` (`[package.metadata.run.init]`
//! and [`crate::util::env::PackageMetadata::get`]) via `serde` + `toml`.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::Path;
use std::sync::OnceLock;

use serde::Deserialize;

use crate::util::env::PackageMetadata;
use crate::util::output::{Printer, TagColor};

/// Embedded `Cargo.toml` used to parse `[package.metadata.run.init]` defaults.
const EMBEDDED_CARGO_TOML: &str = include_str!("../../Cargo.toml");

/// Cached singleton of `[package.metadata.run.init]`.
static INIT_METADATA: OnceLock<InitMetadata> = OnceLock::new();

/// Top-level `Cargo.toml` wrapper for extracting `[package.metadata.run.init]`.
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

/// `[package.metadata]` subset containing `run.init`.
#[derive(Debug, Deserialize)]
struct CargoMetadata {
    /// `[package.metadata.run]` subset.
    run: RunInitSection,
}

/// `[package.metadata.run]` subset holding `[package.metadata.run.init]`.
#[derive(Debug, Deserialize)]
struct RunInitSection {
    /// Init configuration section.
    init: InitMetadata,
}

/// Technology detection and preset defaults from `[package.metadata.run.init]`.
#[derive(Debug, Clone, Deserialize)]
pub struct InitMetadata {
    /// Exact manifest filename -> technology identifier mappings.
    pub filename_techs: BTreeMap<String, String>,
    /// Lowercased file extension -> technology identifier mappings.
    pub extension_techs: BTreeMap<String, String>,
    /// Language -> `[presets.debug]` flags.
    pub debug_presets: BTreeMap<String, Vec<String>>,
    /// Language -> `[presets.release]` flags.
    pub release_presets: BTreeMap<String, Vec<String>>,
}

impl InitMetadata {
    /// Return the cached [`InitMetadata`] deserialized from embedded `Cargo.toml`.
    pub fn get() -> &'static Self {
        INIT_METADATA.get_or_init(|| {
            let manifest: CargoManifest = toml::from_str(EMBEDDED_CARGO_TOML)
                .expect("embedded Cargo.toml must contain valid [package.metadata.run.init]");
            manifest.package.metadata.run.init
        })
    }
}

/// Format a string slice as a TOML array literal (`["a", "b"]`).
fn format_toml_string_array(items: &[String]) -> String {
    let quoted: Vec<String> = items
        .iter()
        .map(|s| format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")))
        .collect();
    format!("[{}]", quoted.join(", "))
}

/// Scaffolds and generates a tailored `Run.toml` based on repository inspection.
#[derive(Debug, Clone, Copy, Default)]
pub struct ConfigInitializer;

impl ConfigInitializer {
    /// Inspect directory structure and file extensions under `root` to detect active technologies.
    ///
    /// Skips directories listed in `excludes.discovery_ignore_dirs` from [`PackageMetadata::get`]
    /// and classifies files via `[package.metadata.run.init]`.
    pub fn detect_technologies(root: &Path) -> BTreeSet<String> {
        let mut detected = BTreeSet::new();
        let ignore_dirs = &PackageMetadata::get().excludes.discovery_ignore_dirs;
        let init_meta = InitMetadata::get();
        Self::scan_dir(root, ignore_dirs, init_meta, &mut detected);
        detected
    }

    /// Recursive directory scanner skipping ignored directory names.
    fn scan_dir(
        dir: &Path,
        ignore_dirs: &[String],
        init_meta: &InitMetadata,
        detected: &mut BTreeSet<String>,
    ) {
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let Ok(ft) = entry.file_type() else {
                continue;
            };
            let path = entry.path();
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if ft.is_dir() {
                if ignore_dirs.iter().any(|ignored| ignored == name_str.as_ref()) {
                    continue;
                }
                Self::scan_dir(&path, ignore_dirs, init_meta, detected);
            } else if ft.is_file() || (ft.is_symlink() && path.is_file()) {
                if let Some(tech) = init_meta.filename_techs.get(name_str.as_ref()) {
                    detected.insert(tech.clone());
                }
                if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                    let dotted = format!(".{}", ext.to_ascii_lowercase());
                    if let Some(tech) = init_meta.extension_techs.get(&dotted) {
                        detected.insert(tech.clone());
                    }
                }
            }
        }
    }

    /// Generate modern `Run.toml` content tailored to `technologies`.
    pub fn generate_config_content(technologies: &BTreeSet<String>) -> String {
        let pkg_meta = PackageMetadata::get();
        let init_meta = InitMetadata::get();
        let init_exts = format_toml_string_array(&pkg_meta.excludes.init_extensions);

        let mut lines = vec![
            "# ==============================================================================".to_string(),
            "# Auto-Generated Run.toml Configuration".to_string(),
            "# ==============================================================================".to_string(),
            String::new(),
            "[core]".to_string(),
            "exclude_files = []".to_string(),
            "exclude_dirs = []".to_string(),
            format!("exclude_extensions = {init_exts}"),
            String::new(),
            "[tasks]".to_string(),
        ];

        if technologies.contains("python") {
            lines.push("test = \"pytest tests/ -v\"".to_string());
        } else if technologies.contains("rust") || technologies.contains("rust_cargo") {
            lines.push("test = \"cargo test\"".to_string());
            lines.push("build = \"cargo build --release\"".to_string());
        } else if technologies.contains("cmake") {
            lines.push("build = \"cmake -B build && cmake --build build\"".to_string());
            lines.push("clean = \"rm -rf build\"".to_string());
        } else {
            lines.push("# test = \"pytest tests/\"".to_string());
            lines.push("# build = \"gcc -O3 main.c -o app\"".to_string());
        }
        lines.push(String::new());

        if technologies.contains("cmake") {
            lines.push("[projects.cmake]".to_string());
            lines.push("file = \"CMakeLists.txt\"".to_string());
            lines.push("build = \"cmake -B build && cmake --build build\"".to_string());
            lines.push("run = \"./build/app\"".to_string());
            lines.push(String::new());
        } else if technologies.contains("node") {
            lines.push("[projects.node]".to_string());
            lines.push("file = \"package.json\"".to_string());
            lines.push("command = \"npm start\"".to_string());
            lines.push(String::new());
        }

        let ordered_langs = ["cpp", "c", "rust", "java"];

        lines.push("[presets.debug]".to_string());
        let mut any_preset_lang = false;
        for lang in ordered_langs {
            if technologies.contains(lang)
                && let Some(flags) = init_meta.debug_presets.get(lang)
            {
                any_preset_lang = true;
                lines.push(format!("{lang} = {}", format_toml_string_array(flags)));
            }
        }
        if !any_preset_lang {
            lines.push("# c = [\"-g\", \"-Wall\", \"-O0\"]".to_string());
            lines.push("# cpp = [\"-g\", \"-Wall\", \"-std=c++20\", \"-O0\"]".to_string());
        }
        lines.push(String::new());

        lines.push("[presets.release]".to_string());
        let mut any_release_lang = false;
        for lang in ordered_langs {
            if technologies.contains(lang)
                && let Some(flags) = init_meta.release_presets.get(lang)
            {
                any_release_lang = true;
                lines.push(format!("{lang} = {}", format_toml_string_array(flags)));
            }
        }
        if !any_release_lang {
            lines.push("# c = [\"-O3\", \"-DNDEBUG\"]".to_string());
            lines.push("# cpp = [\"-O3\", \"-std=c++20\", \"-DNDEBUG\"]".to_string());
        }
        lines.push(String::new());

        lines.join("\n")
    }

    /// Create a new `Run.toml` in `target_dir`, prompting on stdin unless `force` is `true`.
    pub fn init_config(target_dir: &Path, force: bool) -> bool {
        let stdin = io::stdin();
        let mut reader = stdin.lock();
        let mut stdout = io::stdout();
        Self::init_config_with_io(
            target_dir,
            force,
            Printer::global(),
            &mut reader,
            &mut stdout,
        )
    }

    /// Pure-testable [`Self::init_config`] accepting explicit reader/writer streams.
    pub fn init_config_with_io<R: BufRead, W: Write>(
        target_dir: &Path,
        force: bool,
        printer: Printer,
        reader: &mut R,
        writer: &mut W,
    ) -> bool {
        let dest = target_dir.join(&PackageMetadata::get().config_file);
        if dest.exists() && !force {
            printer.warning(&format!("{} already exists.", dest.display()));
            let _ = write!(writer, "Overwrite existing Run.toml? [y/N]: ");
            let _ = writer.flush();
            let mut answer = String::new();
            if reader.read_line(&mut answer).is_err() {
                printer.info("Initialization cancelled.");
                return false;
            }
            let normalized = answer.trim().to_ascii_lowercase();
            if normalized != "y" && normalized != "yes" {
                printer.info("Initialization cancelled.");
                return false;
            }
        }

        let techs = Self::detect_technologies(target_dir);
        let content = Self::generate_config_content(&techs);

        match fs::write(&dest, content) {
            Ok(()) => {
                let detected_str = if techs.is_empty() {
                    "generic".to_string()
                } else {
                    techs.into_iter().collect::<Vec<_>>().join(", ")
                };
                printer.action(
                    "INIT",
                    &format!("Generated Run.toml for detected environment: [{detected_str}]"),
                    TagColor::Green,
                );
                true
            }
            Err(err) => {
                printer.error(&format!("Failed to write Run.toml: {err}"));
                false
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::config::Config;
    use crate::util::output::{Colors, Verbosity};
    use std::io::Cursor;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Create an isolated temporary directory for init_config tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_init_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Port of `dev:tests/test_init_config.py::test_detect_technologies`.
    #[test]
    fn test_detect_technologies_and_ignored_dirs() {
        let tmp = make_temp_dir("detect");
        fs::write(tmp.join("main.cpp"), "int main() {}").expect("write main.cpp");
        fs::write(
            tmp.join("CMakeLists.txt"),
            "cmake_minimum_required(VERSION 3.20)",
        )
        .expect("write CMakeLists.txt");
        fs::write(tmp.join("script.py"), "print('hi')").expect("write script.py");

        // Files inside ignored dirs like `target` or `.git` must not trigger detection.
        let ignored_target = tmp.join("target");
        fs::create_dir_all(&ignored_target).expect("mkdir target");
        fs::write(ignored_target.join("ignored.rs"), "fn main() {}").expect("write target/ignored.rs");

        let techs = ConfigInitializer::detect_technologies(&tmp);
        assert!(techs.contains("cpp"));
        assert!(techs.contains("cmake"));
        assert!(techs.contains("python"));
        assert!(!techs.contains("rust"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_init_config.py::test_init_config_generates_valid_toml`.
    #[test]
    fn test_init_config_generates_valid_toml() {
        let tmp = make_temp_dir("gen_toml");
        fs::write(tmp.join("main.cpp"), "int main() {}").expect("write main.cpp");
        fs::write(tmp.join("helper.hpp"), "#pragma once").expect("write helper.hpp");

        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let mut reader = Cursor::new(Vec::<u8>::new());
        let mut writer = Vec::new();
        let ok = ConfigInitializer::init_config_with_io(&tmp, true, printer, &mut reader, &mut writer);
        assert!(ok);

        let toml_path = tmp.join("Run.toml");
        assert!(toml_path.exists());
        let raw = fs::read_to_string(&toml_path).expect("read Run.toml");
        let cfg = Config::from_toml_str(&raw, Some(toml_path.clone()), Some(&tmp))
            .expect("parse generated Run.toml");

        assert_eq!(
            cfg.get_preset_flags(Some("debug"), "cpp"),
            vec!["-g", "-Wall", "-Wextra", "-std=c++20", "-O0"]
        );
        assert!(cfg.projects.iter().all(|(k, _)| k != "runner"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_init_config.py::test_init_config_existing_abort`.
    #[test]
    fn test_init_config_existing_abort_and_confirm() {
        let tmp = make_temp_dir("existing");
        let toml_path = tmp.join("Run.toml");
        fs::write(&toml_path, "[core]\n").expect("write Run.toml");

        let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let mut no_reader = Cursor::new(b"n\n".to_vec());
        let mut prompt_out = Vec::new();
        assert!(!ConfigInitializer::init_config_with_io(
            &tmp,
            false,
            printer,
            &mut no_reader,
            &mut prompt_out
        ));
        assert_eq!(fs::read_to_string(&toml_path).expect("read"), "[core]\n");
        assert!(String::from_utf8(prompt_out)
            .expect("utf8")
            .contains("Overwrite existing Run.toml? [y/N]:"));

        let mut yes_reader = Cursor::new(b"y\n".to_vec());
        let mut prompt_out2 = Vec::new();
        assert!(ConfigInitializer::init_config_with_io(
            &tmp,
            false,
            printer,
            &mut yes_reader,
            &mut prompt_out2
        ));
        assert!(fs::read_to_string(&toml_path)
            .expect("read")
            .contains("[tasks]"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: cyclic directory symlinks must not cause infinite recursion or stack overflow.
    #[cfg(unix)]
    #[test]
    fn test_detect_technologies_ignores_cyclic_directory_symlinks() {
        let tmp = make_temp_dir("cyclic_symlink");
        fs::write(tmp.join("main.rs"), "fn main() {}\n").expect("write main.rs");
        std::os::unix::fs::symlink(&tmp, tmp.join("self_loop")).expect("create self loop symlink");
        std::os::unix::fs::symlink("..", tmp.join("parent_loop"))
            .expect("create parent loop symlink");

        let techs = ConfigInitializer::detect_technologies(&tmp);
        assert!(techs.contains("rust"));

        let _ = fs::remove_dir_all(&tmp);
    }
}
