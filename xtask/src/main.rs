//! Cross-platform developer automation for `run` (`cargo xtask`).
//!
//! Replaces OS-specific `setup.sh` and `setup.ps1` installer scripts with a
//! single pure-Rust CLI built on `clap` supporting `install`, `uninstall`,
//! `test`, `lint`, and `completions` across Linux, macOS, and Windows.

#![warn(missing_docs)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

use clap::{Parser, Subcommand};

/// Top-level `cargo xtask` CLI parser.
#[derive(Debug, Parser)]
#[command(
    name = "xtask",
    about = "Cross-platform build, install, test, lint, and completion automation for run"
)]
struct Cli {
    /// Subcommand to execute.
    #[command(subcommand)]
    command: TaskCommand,
}

/// Available `cargo xtask` automation commands.
#[derive(Debug, Subcommand, PartialEq, Eq)]
enum TaskCommand {
    /// Build the release binary and install `run` (and its man page on Unix).
    Install {
        /// Installation prefix (defaults to $PREFIX, ~/.local, or %USERPROFILE%\.local).
        #[arg(long, value_name = "DIR")]
        prefix: Option<PathBuf>,
        /// Pass `--offline` to Cargo build invocations.
        #[arg(long)]
        offline: bool,
    },
    /// Remove the installed `run` binary and man page from the prefix.
    Uninstall {
        /// Installation prefix (defaults to $PREFIX, ~/.local, or %USERPROFILE%\.local).
        #[arg(long, value_name = "DIR")]
        prefix: Option<PathBuf>,
    },
    /// Run workspace Rust tests, build `--release`, and run the black-box pytest suite.
    Test {
        /// Pass `--offline` to Cargo invocations.
        #[arg(long)]
        offline: bool,
    },
    /// Run workspace `cargo clippy` and `cargo doc` with `-D warnings`.
    Lint {
        /// Pass `--offline` to Cargo invocations.
        #[arg(long)]
        offline: bool,
    },
    /// Build release binary and generate shell completions for zsh, bash, fish, and PowerShell.
    Completions {
        /// Output directory for generated completion files.
        #[arg(long, default_value = "target/completions", value_name = "DIR")]
        out_dir: PathBuf,
        /// Pass `--offline` to Cargo build invocations.
        #[arg(long)]
        offline: bool,
    },
}

/// Return the workspace root directory (parent of `xtask/`).
fn workspace_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("xtask crate must reside inside workspace root")
        .to_path_buf()
}

/// Return the platform binary filename (`run.exe` on Windows, `run` elsewhere).
fn binary_filename() -> &'static str {
    if cfg!(windows) { "run.exe" } else { "run" }
}

/// Resolve the user's home directory from `HOME` or `USERPROFILE`.
fn home_dir() -> Result<PathBuf, String> {
    env::var_os("HOME")
        .filter(|v| !v.is_empty())
        .or_else(|| env::var_os("USERPROFILE").filter(|v| !v.is_empty()))
        .map(PathBuf::from)
        .ok_or_else(|| "Could not determine HOME or USERPROFILE directory.".to_string())
}

/// Resolve installation prefix: `--prefix` > `PREFIX` env var > `<home>/.local`.
fn resolve_prefix_with(
    cli_prefix: Option<PathBuf>,
    env_prefix: Option<&str>,
    home: &Path,
) -> PathBuf {
    if let Some(p) = cli_prefix {
        return p;
    }
    if let Some(val) = env_prefix.map(str::trim).filter(|s| !s.is_empty()) {
        return PathBuf::from(val);
    }
    home.join(".local")
}

/// Return `true` if `dir` appears in the OS `PATH` environment variable.
fn path_contains_dir(path_env: Option<&str>, dir: &Path) -> bool {
    let Some(raw) = path_env else {
        return false;
    };
    let canon_dir = fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
    env::split_paths(raw).any(|entry| {
        entry == dir || fs::canonicalize(&entry).is_ok_and(|canon| canon == canon_dir)
    })
}

/// Spawn a subprocess inside `cwd`, streaming inherited stdio and failing on non-zero exit.
fn run_status(
    program: &str,
    args: &[&str],
    cwd: &Path,
    extra_env: &[(&str, &str)],
) -> Result<(), String> {
    let mut cmd = Command::new(program);
    cmd.args(args).current_dir(cwd);
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    let status = cmd
        .status()
        .map_err(|err| format!("Failed to spawn '{program}': {err}"))?;
    if !status.success() {
        return Err(format!(
            "Command '{program} {}' failed with status {status}",
            args.join(" ")
        ));
    }
    Ok(())
}

/// Build the `run` release binary via Cargo.
fn build_release(root: &Path, offline: bool) -> Result<PathBuf, String> {
    let cargo = env::var("CARGO").unwrap_or_else(|_| "cargo".to_string());
    let mut args = vec!["build", "--release"];
    if offline {
        args.push("--offline");
    }
    println!("Building run (release)...");
    run_status(&cargo, &args, root, &[])?;
    Ok(root.join("target").join("release").join(binary_filename()))
}

/// Copy built binary (and Unix man page if present) into `prefix`.
fn install_artifacts(root: &Path, release_bin: &Path, prefix: &Path) -> Result<PathBuf, String> {
    let _ = root;
    if !release_bin.is_file() {
        return Err(format!(
            "Release binary not found at {}",
            release_bin.display()
        ));
    }
    let bin_dir = prefix.join("bin");
    fs::create_dir_all(&bin_dir)
        .map_err(|err| format!("Failed to create {}: {err}", bin_dir.display()))?;

    let dest_bin = bin_dir.join(binary_filename());
    fs::copy(release_bin, &dest_bin)
        .map_err(|err| format!("Failed to copy binary to {}: {err}", dest_bin.display()))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&dest_bin)
            .map_err(|err| format!("Failed to read metadata for {}: {err}", dest_bin.display()))?
            .permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&dest_bin, perms)
            .map_err(|err| format!("Failed to chmod {}: {err}", dest_bin.display()))?;

        let man_src = root.join("docs").join("man").join("run.1");
        if man_src.is_file() {
            let man_dir = prefix.join("share").join("man").join("man1");
            fs::create_dir_all(&man_dir)
                .map_err(|err| format!("Failed to create {}: {err}", man_dir.display()))?;
            let man_dest = man_dir.join("run.1");
            fs::copy(&man_src, &man_dest)
                .map_err(|err| format!("Failed to copy man page to {}: {err}", man_dest.display()))?;
            let mut mperms = fs::metadata(&man_dest)
                .map_err(|err| format!("Failed to read metadata for {}: {err}", man_dest.display()))?
                .permissions();
            mperms.set_mode(0o644);
            fs::set_permissions(&man_dest, mperms)
                .map_err(|err| format!("Failed to chmod {}: {err}", man_dest.display()))?;
        }
    }

    Ok(dest_bin)
}

/// Remove installed `run` binary and optional man page from `prefix`.
fn uninstall_artifacts(prefix: &Path) -> Result<(), String> {
    let dest_bin = prefix.join("bin").join(binary_filename());
    if dest_bin.exists() {
        fs::remove_file(&dest_bin)
            .map_err(|err| format!("Failed to remove {}: {err}", dest_bin.display()))?;
        println!("Removed {}", dest_bin.display());
    }
    let man_dest = prefix.join("share").join("man").join("man1").join("run.1");
    if man_dest.exists() {
        fs::remove_file(&man_dest)
            .map_err(|err| format!("Failed to remove {}: {err}", man_dest.display()))?;
        println!("Removed {}", man_dest.display());
    }
    Ok(())
}

/// Generate shell completion files into `out_dir` using `bin_path`.
fn generate_completions(bin_path: &Path, out_dir: &Path) -> Result<(), String> {
    fs::create_dir_all(out_dir)
        .map_err(|err| format!("Failed to create {}: {err}", out_dir.display()))?;

    let targets = [
        ("zsh", "_run"),
        ("bash", "run"),
        ("fish", "run.fish"),
        ("powershell", "run.ps1"),
    ];
    for (shell, filename) in targets {
        let output = Command::new(bin_path)
            .args(["--completion", shell])
            .output()
            .map_err(|err| format!("Failed to run {} --completion {shell}: {err}", bin_path.display()))?;
        if !output.status.success() {
            return Err(format!(
                "Completion generation failed for {shell}: {}",
                String::from_utf8_lossy(&output.stderr)
            ));
        }
        let dest = out_dir.join(filename);
        fs::write(&dest, &output.stdout)
            .map_err(|err| format!("Failed to write {}: {err}", dest.display()))?;
        println!("Generated {}", dest.display());
    }
    Ok(())
}

/// Execute the parsed `cargo xtask` command.
fn run_task(cmd: TaskCommand) -> Result<(), String> {
    let root = workspace_root();
    let cargo = env::var("CARGO").unwrap_or_else(|_| "cargo".to_string());

    match cmd {
        TaskCommand::Install { prefix, offline } => {
            let home = home_dir()?;
            let env_prefix = env::var("PREFIX").ok();
            let resolved_prefix = resolve_prefix_with(prefix, env_prefix.as_deref(), &home);
            let release_bin = build_release(&root, offline)?;
            let dest_bin = install_artifacts(&root, &release_bin, &resolved_prefix)?;
            println!("Installed run to {}", dest_bin.display());

            let bin_dir = resolved_prefix.join("bin");
            let path_env = env::var("PATH").ok();
            if !path_contains_dir(path_env.as_deref(), &bin_dir) {
                println!("Add '{}' to your PATH to invoke 'run' globally.", bin_dir.display());
            }
            Ok(())
        }
        TaskCommand::Uninstall { prefix } => {
            let home = home_dir()?;
            let env_prefix = env::var("PREFIX").ok();
            let resolved_prefix = resolve_prefix_with(prefix, env_prefix.as_deref(), &home);
            uninstall_artifacts(&resolved_prefix)
        }
        TaskCommand::Test { offline } => {
            let mut test_args = vec!["test", "--workspace"];
            if offline {
                test_args.push("--offline");
            }
            run_status(&cargo, &test_args, &root, &[])?;

            let release_bin = build_release(&root, offline)?;
            let pytest = env::var("PYTEST").unwrap_or_else(|_| "pytest".to_string());
            let pythonpath = format!(
                "{}{}{}",
                root.display(),
                if cfg!(windows) { ";" } else { ":" },
                root.join("tests").display()
            );
            let bin_str = release_bin.to_string_lossy().into_owned();
            run_status(
                &pytest,
                &["-p", "no:cacheprovider", "tests/"],
                &root,
                &[("PYTHONPATH", &pythonpath), ("RUN_BIN", &bin_str)],
            )
        }
        TaskCommand::Lint { offline } => {
            let mut clippy_args = vec!["clippy", "--workspace", "--all-targets"];
            if offline {
                clippy_args.push("--offline");
            }
            clippy_args.extend(["--", "-D", "warnings"]);
            run_status(&cargo, &clippy_args, &root, &[])?;

            let mut doc_args = vec!["doc", "--workspace", "--no-deps"];
            if offline {
                doc_args.push("--offline");
            }
            run_status(&cargo, &doc_args, &root, &[("RUSTDOCFLAGS", "-D warnings")])
        }
        TaskCommand::Completions { out_dir, offline } => {
            let release_bin = build_release(&root, offline)?;
            let resolved_out = if out_dir.is_absolute() {
                out_dir
            } else {
                root.join(out_dir)
            };
            generate_completions(&release_bin, &resolved_out)
        }
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    if let Err(err) = run_task(cli.command) {
        eprintln!("xtask error: {err}");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let dir = env::temp_dir().join(format!(
            "run_xtask_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn test_cli_parses_all_subcommands() {
        let c1 = Cli::try_parse_from(["xtask", "install", "--prefix", "/opt/run", "--offline"])
            .expect("parse install");
        assert_eq!(
            c1.command,
            TaskCommand::Install {
                prefix: Some(PathBuf::from("/opt/run")),
                offline: true,
            }
        );

        let c2 = Cli::try_parse_from(["xtask", "uninstall"]).expect("parse uninstall");
        assert_eq!(c2.command, TaskCommand::Uninstall { prefix: None });

        let c3 = Cli::try_parse_from(["xtask", "test", "--offline"]).expect("parse test");
        assert_eq!(c3.command, TaskCommand::Test { offline: true });

        let c4 = Cli::try_parse_from(["xtask", "lint"]).expect("parse lint");
        assert_eq!(c4.command, TaskCommand::Lint { offline: false });

        let c5 = Cli::try_parse_from(["xtask", "completions", "--out-dir", "dist/comp"])
            .expect("parse completions");
        assert_eq!(
            c5.command,
            TaskCommand::Completions {
                out_dir: PathBuf::from("dist/comp"),
                offline: false,
            }
        );
    }

    #[test]
    fn test_resolve_prefix_precedence() {
        let home = Path::new("/tmp/fake_home");
        assert_eq!(
            resolve_prefix_with(Some(PathBuf::from("/custom")), Some("/env_dir"), home),
            PathBuf::from("/custom")
        );
        assert_eq!(
            resolve_prefix_with(None, Some("/env_dir"), home),
            PathBuf::from("/env_dir")
        );
        assert_eq!(
            resolve_prefix_with(None, Some("   "), home),
            PathBuf::from("/tmp/fake_home/.local")
        );
        assert_eq!(
            resolve_prefix_with(None, None, home),
            PathBuf::from("/tmp/fake_home/.local")
        );
    }

    #[test]
    fn test_install_and_uninstall_artifacts_roundtrip() {
        let dir = make_temp_dir("artifacts");
        let fake_root = dir.join("repo");
        let man_dir = fake_root.join("docs").join("man");
        fs::create_dir_all(&man_dir).unwrap();
        fs::write(man_dir.join("run.1"), ".TH RUN 1\n").unwrap();

        let fake_bin = dir.join(binary_filename());
        fs::write(&fake_bin, b"BINARY").unwrap();
        let prefix = dir.join("prefix");

        let installed = install_artifacts(&fake_root, &fake_bin, &prefix).expect("install");
        assert_eq!(installed, prefix.join("bin").join(binary_filename()));
        assert!(installed.is_file());

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&installed).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o755);
            assert!(prefix.join("share/man/man1/run.1").is_file());
        }

        uninstall_artifacts(&prefix).expect("uninstall");
        assert!(!installed.exists());
        assert!(!prefix.join("share/man/man1/run.1").exists());

        // Idempotent second uninstall succeeds cleanly.
        uninstall_artifacts(&prefix).expect("second uninstall");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_path_contains_dir() {
        let dir = make_temp_dir("path_chk");
        let other = dir.join("other");
        let joined = env::join_paths([&other, &dir]).unwrap();
        let joined_str = joined.to_string_lossy();
        assert!(path_contains_dir(Some(&joined_str), &dir));
        assert!(!path_contains_dir(Some(&other.to_string_lossy()), &dir));
        assert!(!path_contains_dir(None, &dir));
        let _ = fs::remove_dir_all(&dir);
    }
}
