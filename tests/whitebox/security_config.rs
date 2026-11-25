//! Cross-module white-box tests for [`CliArgs`], [`Config`], and [`SecurityManager`] integration.

use std::fs;
use std::sync::Arc;

use run::core::CompilerRunner;
use run::model::config::Config;
use run::model::options::RunnerOptions;
use run::util::args::CliArgs;
use run::util::output::{Colors, Printer, Verbosity};

use super::helpers::{MockCmdExec, TempWorkspace};

/// Verify `CliArgs::to_runner_options` propagates flags cleanly and `SecurityManager`
/// strips suspicious compiler flags from `Run.toml` presets unless `--unsafe` is set.
#[test]
fn test_cli_args_and_preset_suspicious_flag_filtering() {
    let ws = TempWorkspace::new("sec_cfg");
    let src = ws.path().join("main.c");
    fs::write(&src, "int main(void) { return 0; }\n").unwrap();

    let cfg = Config::from_toml_str(
        r#"
        [presets.sketchy]
        c = ["-O2", "-Wl,-rpath,/tmp/evil"]
        "#,
        Some(ws.path().join("Run.toml")),
        Some(ws.path()),
    )
    .expect("cfg");

    let args = CliArgs::parse_from_raw([
        "run",
        "-p",
        "sketchy",
        "--no-cache",
        "-q",
        &src.to_string_lossy(),
    ])
    .expect("parse cli args");
    let opts: RunnerOptions = args.to_runner_options();
    assert_eq!(opts.preset.as_deref(), Some("sketchy"));
    assert!(opts.no_cache);
    assert!(opts.quiet);

    let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
    let mut runner = CompilerRunner::with_cache_base(
        opts,
        None,
        None,
        cfg,
        printer,
        ws.path(),
        &ws.path().join("cache"),
    )
    .expect("runner");

    let mut mock = MockCmdExec::new();
    mock.expect_execute().times(0);
    runner.base.set_executor(Arc::new(mock));

    // SecurityManager rejects suspicious `-Wl,-rpath` preset flag without `--unsafe`.
    assert!(!runner.compile_and_run(&[src], false));
}
