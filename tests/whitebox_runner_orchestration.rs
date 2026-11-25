//! Cross-module white-box Rust integration tests using `mockall::mock!`.
//!
//! Exercises orchestration across [`CompilerRunner`], [`Config`], [`CacheManager`],
//! [`TestcasesRunner`], [`TaskRunner`], [`ProjectRunner`], and [`SandboxEnv`]
//! without spawning external compilers, runtimes, or container daemons.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use mockall::mock;
use mockall::Sequence;

use run::runner::{
    CommandExecutor, CompilerRunner, ExecOutcome, ExecSpec, ProjectRunner, RunnerOptions,
    TaskRunner, TestcasesRunner,
};
use run::util::config::Config;
use run::model::error::RunError;
use run::util::output::{Colors, Printer, Verbosity};
use run::util::sandbox::{ContainerSandbox, ExecOutput, HostOs, SandboxEnv};

mock! {
    #[derive(Debug)]
    pub CmdExec {}
    impl CommandExecutor for CmdExec {
        fn execute(&self, spec: &ExecSpec) -> Result<ExecOutcome, RunError>;
    }
}

mock! {
    pub SbEnv {}
    impl SandboxEnv for SbEnv {
        fn os(&self) -> HostOs;
        fn which(&self, bin: &str) -> Option<PathBuf>;
        fn cwd(&self) -> Result<PathBuf, RunError>;
        fn uid_gid(&self) -> Option<(u32, u32)>;
        fn current_pid(&self) -> u32;
        fn is_pid_alive(&self, pid: u32) -> bool;
        fn xdg_cache_home(&self) -> Option<PathBuf>;
        fn home_dir(&self) -> Option<PathBuf>;
        fn run_capture(
            &self,
            program: &str,
            args: &[String],
            timeout: Option<Duration>,
        ) -> Result<ExecOutput, RunError>;
        fn warn(&self, msg: &str);
    }
}

struct TempWorkspace(PathBuf);

impl TempWorkspace {
    fn new(label: &str) -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let path = std::env::temp_dir().join(format!(
            "run_wb_integ_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&path).expect("create temp workspace");
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempWorkspace {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn ok_outcome(stdout: &str) -> Result<ExecOutcome, RunError> {
    Ok(ExecOutcome {
        captured_stdout: stdout.to_string(),
        elapsed_secs: 0.01,
        memory_bytes: Some(2 * 1024 * 1024),
    })
}

/// Verify custom `[[languages]]` overrides built-in `.py`, merges preset + CLI flags,
/// and invokes exact argv via `MockCmdExec`.
#[test]
fn test_cross_module_custom_language_override_and_presets() {
    let ws = TempWorkspace::new("custom_override");
    let script = ws.path().join("app.py");
    fs::write(&script, "print('orig')\n").unwrap();

    let cfg = Config::from_toml_str(
        r#"
        [presets.fast]
        custom_py = ["-O"]

        [[languages]]
        name = "custom_py"
        extensions = [".py"]
        command = "mypython {file}"
        "#,
        Some(ws.path().join("Run.toml")),
        Some(ws.path()),
    )
    .expect("parse config");

    let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
    let mut runner = CompilerRunner::with_cache_base(
        RunnerOptions {
            preset: Some("fast".to_string()),
            ..Default::default()
        },
        Some("-u"),
        Some("--arg1"),
        cfg,
        printer,
        ws.path(),
        &ws.path().join("cache"),
    )
    .expect("runner");

    let mut mock = MockCmdExec::new();
    mock.expect_execute()
        .times(1)
        .withf(|spec| {
            !spec.compiling
                && spec.cmd[0] == "mypython"
                && spec.cmd.contains(&"-u".to_string())
                && spec.cmd.contains(&"-O".to_string())
                && spec.cmd.last().map(String::as_str) == Some("--arg1")
        })
        .returning(|_| ok_outcome(""));
    runner.base.set_executor(Arc::new(mock));

    assert!(runner.compile_and_run(&[script], false));
}

/// Verify `TestcasesRunner::run_tests` compiles once, runs N testcases with `--expect`,
/// reports partial failure cleanly, and restores runner state.
#[test]
fn test_cross_module_batch_testcase_compile_once_and_mixed_results() {
    let ws = TempWorkspace::new("batch_mixed");
    let cases = ws.path().join("cases");
    fs::create_dir_all(&cases).unwrap();
    fs::write(cases.join("1.in"), "10\n").unwrap();
    fs::write(cases.join("1.out"), "20\n").unwrap();
    fs::write(cases.join("2.in"), "5\n").unwrap();
    fs::write(cases.join("2.out"), "10\n").unwrap();

    let src = ws.path().join("double.c");
    fs::write(&src, "int main(void){return 0;}\n").unwrap();
    let out_dir = ws.path().join("bin");

    let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
    let mut runner = CompilerRunner::with_cache_base(
        RunnerOptions {
            out_dir: Some(out_dir.to_string_lossy().into_owned()),
            ..Default::default()
        },
        None,
        None,
        Config::default(),
        printer,
        ws.path(),
        &ws.path().join("cache"),
    )
    .expect("runner");

    let expected_bin = runner.get_executable_path(&src).expect("bin path");
    let bin_for_compile = expected_bin.clone();

    let mut seq = Sequence::new();
    let mut mock = MockCmdExec::new();
    // Phase 1: compile once (create dummy executable so Phase 2 detects `bin_path.exists()`).
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| spec.compiling && spec.cmd[0] == "gcc")
        .returning(move |_| {
            fs::write(&bin_for_compile, b"FAKE_BIN").unwrap();
            ok_outcome("")
        });
    // Phase 2 case 1: matches expected "20\n".
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| !spec.compiling && spec.capture_stdout)
        .returning(|_| ok_outcome("20\n"));
    // Phase 2 case 2: mismatches expected "10\n".
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| !spec.compiling && spec.capture_stdout)
        .returning(|_| ok_outcome("999\n"));
    runner.base.set_executor(Arc::new(mock));

    let all_passed = TestcasesRunner::run_tests(&mut runner, &cases, &[src], false);
    assert!(!all_passed);
    assert_eq!(runner.base.options.stdin, None);
    assert_eq!(runner.base.options.expect, None);
    assert_eq!(runner.base.options.test_dir, None);
}

/// Verify `TaskRunner`, `ProjectRunner`, and `ContainerSandbox` integration via `mockall`.
#[test]
fn test_cross_module_project_task_and_container_sandbox_mock() {
    let ws = TempWorkspace::new("proj_sb");
    let manifest = ws.path().join("app.manifest");
    fs::write(&manifest, "v1\n").unwrap();

    let cfg = Config::from_toml_str(
        r#"
        [projects.demo]
        file = "*.manifest"
        build = "build_tool {filename}"
        run = "run_tool"

        [tasks]
        check = "linter --strict"
        "#,
        Some(ws.path().join("Run.toml")),
        Some(ws.path()),
    )
    .expect("cfg");

    let detected = ProjectRunner::detect_project(ws.path(), &cfg, None).expect("detect");
    let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
    let mut runner = CompilerRunner::with_cache_base(
        RunnerOptions::default(),
        None,
        None,
        cfg.clone(),
        printer,
        ws.path(),
        &ws.path().join("cache"),
    )
    .expect("runner");

    let mut seq = Sequence::new();
    let mut mock = MockCmdExec::new();
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| spec.compiling && spec.cmd == vec!["build_tool", "app.manifest", "-j4"])
        .returning(|_| ok_outcome(""));
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| !spec.compiling && spec.cmd == vec!["run_tool", "--port", "8080"])
        .returning(|_| ok_outcome(""));
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| !spec.compiling && spec.cmd == vec!["linter", "--strict", "--fix"])
        .returning(|_| ok_outcome(""));
    runner.base.set_executor(Arc::new(mock));

    assert!(
        ProjectRunner::run_project(
            &detected,
            &mut runner.base,
            &["-j4".to_string()],
            &["--port".to_string(), "8080".to_string()],
        )
        .unwrap()
    );
    assert!(TaskRunner::run_task("check", &cfg, &["--fix".to_string()], &mut runner.base).unwrap());

    // Verify ContainerSandbox engine selection falls back from missing docker to podman.
    let mut sb_env = MockSbEnv::new();
    sb_env
        .expect_which()
        .with(mockall::predicate::eq("docker"))
        .times(1)
        .returning(|_| None);
    sb_env
        .expect_which()
        .with(mockall::predicate::eq("podman"))
        .times(1)
        .returning(|_| Some(PathBuf::from("/usr/bin/podman")));
    sb_env
        .expect_run_capture()
        .times(1)
        .returning(|prog, args, _| {
            assert_eq!(prog, "podman");
            assert_eq!(args, &["info".to_string()]);
            Ok(ExecOutput {
                code: 0,
                stdout: "ok".to_string(),
                stderr: String::new(),
            })
        });
    assert_eq!(
        ContainerSandbox::get_engine_with(&sb_env).expect("podman"),
        "podman"
    );
}
