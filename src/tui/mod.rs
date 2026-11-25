//! Interactive workspace TUI (`--tui`) powered by `ratatui` and `crossterm`.

pub mod app;
pub mod ui;

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use crossterm::event::{self, Event};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

use crate::core::{BenchRunner, CompilerRunner, TaskRunner, TestcasesRunner};
use crate::model::config::Config;
use crate::model::error::RunError;
use crate::util::args::CliArgs;
use crate::util::output::{set_tui_sink, Colors, Printer, UiEvent, Verbosity};

use self::app::{SidebarItem, TuiAction, TuiApp};
use self::ui::render_ui;

/// Discover workspace source files and `Run.toml` tasks for the TUI sidebar.
fn discover_sidebar_items(cli: &CliArgs, config: &Config, cwd: &Path) -> Vec<SidebarItem> {
    let mut items = Vec::new();
    for f in &cli.files {
        let p = PathBuf::from(f);
        if p.exists() {
            items.push(SidebarItem::File(p));
        } else if TaskRunner::is_task(f, config) {
            items.push(SidebarItem::Task(f.clone()));
        }
    }

    let exclude = config.get_exclude();
    if let Ok(entries) = fs::read_dir(cwd) {
        let mut files: Vec<PathBuf> = entries
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.is_file()
                    && p.file_name().is_some_and(|n| {
                        let s = n.to_string_lossy();
                        !s.starts_with('.')
                            && !exclude.files.iter().any(|ef| ef == s.as_ref())
                            && p.extension()
                                .and_then(|e| e.to_str())
                                .is_none_or(|ext| !matches!(ext, "toml" | "lock" | "md" | "txt"))
                    })
            })
            .collect();
        files.sort();
        for p in files {
            if !items.iter().any(|existing| matches!(existing, SidebarItem::File(ep) if ep == &p))
            {
                items.push(SidebarItem::File(p));
            }
        }
    }

    for task_name in config.tasks.keys() {
        if !items
            .iter()
            .any(|existing| matches!(existing, SidebarItem::Task(t) if t == task_name))
        {
            items.push(SidebarItem::Task(task_name.clone()));
        }
    }
    items
}

/// Launch the interactive full-screen TUI dashboard (`--tui`).
pub fn run_tui(cli: &CliArgs, config: Config, cwd: &Path) -> Result<i32, RunError> {
    let items = discover_sidebar_items(cli, &config, cwd);
    let presets: Vec<String> = config.presets.keys().cloned().collect();
    let mut app = TuiApp::new(
        items,
        presets,
        cli.preset.as_deref(),
        cli.time,
        cli.memory,
        cli.watch,
        cli.test_dir.clone(),
    );

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let cwd_label = cwd
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| cwd.display().to_string());

    let (tx, rx) = mpsc::channel::<UiEvent>();
    let (done_tx, done_rx) = mpsc::channel::<()>();
    set_tui_sink(Some(tx));

    let res = run_event_loop(
        &mut terminal,
        &mut app,
        &cwd_label,
        cli,
        &config,
        &rx,
        &done_tx,
        &done_rx,
    );

    set_tui_sink(None);
    let _ = disable_raw_mode();
    let _ = execute!(terminal.backend_mut(), LeaveAlternateScreen);
    let _ = terminal.show_cursor();

    res.map(|()| 0)
}

/// Main 20 FPS TUI event loop draining keyboard input and background runner events.
#[allow(clippy::too_many_arguments)]
fn run_event_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut TuiApp,
    cwd_label: &str,
    cli: &CliArgs,
    config: &Config,
    rx: &mpsc::Receiver<UiEvent>,
    done_tx: &mpsc::Sender<()>,
    done_rx: &mpsc::Receiver<()>,
) -> Result<(), RunError> {
    loop {
        while let Ok(ev) = rx.try_recv() {
            app.apply_event(ev);
        }
        if done_rx.try_recv().is_ok() {
            app.running = false;
        }
        if app.running {
            app.tick = app.tick.wrapping_add(1);
        }

        terminal.draw(|f| render_ui(f, app, cwd_label))?;

        if event::poll(Duration::from_millis(50))?
            && let Event::Key(key) = event::read()?
        {
            match app.handle_key(key) {
                TuiAction::Quit => break,
                TuiAction::RunSelected if !app.running => {
                    if let Some(item) = app.items.get(app.selected_item).cloned() {
                        app.clear_run_output();
                        app.running = true;
                        spawn_worker(item, app, cli, config.clone(), false, done_tx.clone());
                    }
                }
                TuiAction::BenchSelected if !app.running => {
                    if let Some(item) = app.items.get(app.selected_item).cloned() {
                        app.clear_run_output();
                        app.running = true;
                        spawn_worker(item, app, cli, config.clone(), true, done_tx.clone());
                    }
                }
                TuiAction::RunSelected | TuiAction::BenchSelected | TuiAction::None => {}
            }
        }
    }
    Ok(())
}

/// Spawn a background worker thread to execute or benchmark the selected file or `[tasks]` entry.
fn spawn_worker(
    item: SidebarItem,
    app: &TuiApp,
    cli: &CliArgs,
    config: Config,
    bench_mode: bool,
    done_tx: mpsc::Sender<()>,
) {
    let mut opts = cli.to_runner_options();
    opts.preset = app.active_preset().map(ToString::to_string);
    opts.time = app.time_enabled;
    opts.memory = app.mem_enabled;
    let test_dir = app.test_dir.clone();
    let flags_str = cli.flags_str();
    let args_str = cli.args_str();
    let multi = cli.multi;
    let quiet = cli.quiet;
    let bench_runs = cli.bench.unwrap_or(10);
    let warmup = cli.warmup.unwrap_or(2);

    thread::spawn(move || {
        let printer = Printer::with_colors(
            if quiet { Verbosity::Quiet } else { Verbosity::Info },
            Colors::new(false),
        );
        if let Ok(mut runner) = CompilerRunner::new(
            opts,
            flags_str.as_deref(),
            args_str.as_deref(),
            config.clone(),
            printer,
        ) {
            match item {
                SidebarItem::File(path) => {
                    if bench_mode {
                        let _ = BenchRunner::run_bench_targets(
                            &mut runner,
                            &[path],
                            multi,
                            bench_runs,
                            warmup,
                        );
                    } else if let Some(ref tdir) = test_dir {
                        let _ = TestcasesRunner::run_tests(
                            &mut runner,
                            Path::new(tdir),
                            &[path],
                            multi,
                        );
                    } else {
                        let _ = runner.compile_and_run(&[path], multi);
                    }
                }
                SidebarItem::Task(name) => {
                    let run_args = runner.base.run_args.clone();
                    let _ = TaskRunner::run_task(&name, &config, &run_args, &mut runner.base);
                }
            }
            runner.cleanup();
        }
        let _ = done_tx.send(());
    });
}
