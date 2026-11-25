//! Interactive TUI state machine, keybinding dispatcher, and `Ctrl+O` collapsible test cases.

use std::path::PathBuf;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::model::options::BenchStats;
use crate::util::output::{TagColor, UiEvent};

/// Active focus region in the TUI layout.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TuiFocus {
    /// Left sidebar (workspace files and `Run.toml` tasks).
    #[default]
    Sidebar,
    /// Right main output/diff/tests pane.
    Main,
}

/// Active tab inside the right main pane.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum MainTab {
    /// Captured stdout/stderr and compiler/runner status logs.
    #[default]
    Output,
    /// Unified diff from `--expect`.
    Diff,
    /// Collapsible `--test-dir` test case list (`Ctrl+O` to expand/collapse).
    Tests,
}

/// Item displayed in the left workspace sidebar.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SidebarItem {
    /// Workspace source file path.
    File(PathBuf),
    /// Configured `Run.toml` `[tasks.<name>]` entry.
    Task(String),
}

impl SidebarItem {
    /// Human-readable label shown in the sidebar list.
    pub fn label(&self) -> String {
        match self {
            Self::File(p) => p
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_else(|| p.display().to_string()),
            Self::Task(name) => format!("task:{name}"),
        }
    }
}

/// Single `--test-dir` case entry rendered with agent-CLI-style `Ctrl+O` collapse/expand.
#[derive(Debug, Clone, PartialEq)]
pub struct TestCaseEntry {
    /// Case display name (e.g. `1.in`).
    pub name: String,
    /// Whether the test case passed.
    pub passed: bool,
    /// Elapsed wall-clock duration in seconds.
    pub elapsed_secs: Option<f64>,
    /// Peak resident memory in bytes.
    pub memory_bytes: Option<u64>,
    /// Captured actual output (collapsed by default).
    pub actual_output: String,
    /// Expected output (collapsed by default).
    pub expected_output: String,
    /// Whether this case's output details are expanded (`false` by default, toggled via `Ctrl+O`).
    pub expanded: bool,
}

/// Active modal popup or inline input mode in the TUI.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum InputModal {
    /// Normal navigation mode.
    #[default]
    None,
    /// Built-in fuzzy sidebar filter (`/`).
    Filter,
    /// Interactive multi-line stdin editor (`i`).
    StdinEditor,
    /// Interactive `--test-dir` directory picker (`d`).
    TestDirPicker,
}

/// Case-insensitive fuzzy subsequence scorer (`fzf`-style) returning `Some(score)` on match.
pub fn fuzzy_score(query: &str, candidate: &str) -> Option<i32> {
    if query.is_empty() {
        return Some(0);
    }
    let q_chars: Vec<char> = query.to_ascii_lowercase().chars().collect();
    let c_chars: Vec<char> = candidate.to_ascii_lowercase().chars().collect();

    let mut q_idx = 0usize;
    let mut score: i32 = 0;
    let mut prev_match: Option<usize> = None;

    for (c_idx, &ch) in c_chars.iter().enumerate() {
        if q_idx < q_chars.len() && ch == q_chars[q_idx] {
            score += 1;
            if prev_match.is_some_and(|p| p + 1 == c_idx) {
                score += 4;
            }
            if c_idx == 0
                || matches!(
                    c_chars[c_idx - 1],
                    '/' | '\\' | '_' | '-' | '.' | ':'
                )
            {
                score += 3;
            }
            prev_match = Some(c_idx);
            q_idx += 1;
        }
    }

    if q_idx == q_chars.len() {
        Some(score)
    } else {
        None
    }
}

/// Action returned by [`TuiApp::handle_key`] to the event loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TuiAction {
    /// No external action needed.
    None,
    /// Compile and run the currently selected sidebar item.
    RunSelected,
    /// Micro-benchmark the currently selected sidebar item (`b`).
    BenchSelected,
    /// Exit the TUI cleanly.
    Quit,
}

/// Complete state of the interactive `run --tui` dashboard.
#[derive(Debug)]
pub struct TuiApp {
    /// Workspace files and tasks shown in the sidebar.
    pub items: Vec<SidebarItem>,
    /// Index of the selected sidebar item.
    pub selected_item: usize,
    /// Available `Run.toml` preset names.
    pub presets: Vec<String>,
    /// Currently active preset index (`None` means no preset).
    pub selected_preset: Option<usize>,
    /// Whether `--time` metrics are enabled.
    pub time_enabled: bool,
    /// Whether `--mem` metrics are enabled.
    pub mem_enabled: bool,
    /// Whether `--watch` auto-rerun is enabled.
    pub watch_enabled: bool,
    /// Currently focused pane (`Sidebar` or `Main`).
    pub focus: TuiFocus,
    /// Active tab in the main pane (`Output`, `Diff`, `Tests`).
    pub tab: MainTab,
    /// Structured status/log lines emitted by [`crate::util::output::Printer`].
    pub logs: Vec<(String, String, TagColor)>,
    /// Raw stdout/stderr output lines from executed programs.
    pub output_lines: Vec<String>,
    /// Unified diff lines from `--expect`.
    pub diff_lines: Vec<String>,
    /// Test case entries from `--test-dir` (collapsed by default).
    pub test_cases: Vec<TestCaseEntry>,
    /// Currently selected test case index in the `Tests` tab.
    pub selected_test_case: usize,
    /// Vertical scroll offset for `Output` / `Diff` tabs.
    pub scroll: u16,
    /// `true` while a background compile/run worker is active.
    pub running: bool,
    /// Animation tick counter for Braille spinner rendering.
    pub tick: usize,
    /// Last recorded execution duration in seconds.
    pub last_duration: Option<f64>,
    /// Last recorded peak memory in bytes.
    pub last_memory: Option<u64>,
    /// Optional `--test-dir` path passed via CLI.
    pub test_dir: Option<String>,
    /// Most recent micro-benchmark summary (`--bench` or `b` hotkey).
    pub last_bench: Option<BenchStats>,
    /// Active modal popup or inline input mode.
    pub modal: InputModal,
    /// Live fuzzy filter query for the sidebar (`/`).
    pub filter_query: String,
    /// Multi-line stdin text edited inside the TUI (`i`).
    pub stdin_buffer: String,
    /// Candidate subdirectories under `cwd` for the `--test-dir` picker (`d`).
    pub test_dir_candidates: Vec<String>,
    /// Selected index in the `--test-dir` picker (`0` = Disabled, `1..` = `test_dir_candidates[i - 1]`).
    pub selected_test_dir_idx: usize,
}

impl TuiApp {
    /// Construct a [`TuiApp`] from discovered sidebar items, presets, and initial CLI options.
    pub fn new(
        items: Vec<SidebarItem>,
        presets: Vec<String>,
        initial_preset: Option<&str>,
        time_enabled: bool,
        mem_enabled: bool,
        watch_enabled: bool,
        test_dir: Option<String>,
    ) -> Self {
        let selected_preset = initial_preset.and_then(|p| presets.iter().position(|x| x == p));
        let tab = if test_dir.is_some() {
            MainTab::Tests
        } else {
            MainTab::Output
        };
        Self {
            items,
            selected_item: 0,
            presets,
            selected_preset,
            time_enabled,
            mem_enabled,
            watch_enabled,
            focus: TuiFocus::Sidebar,
            tab,
            logs: Vec::new(),
            output_lines: Vec::new(),
            diff_lines: Vec::new(),
            test_cases: Vec::new(),
            selected_test_case: 0,
            scroll: 0,
            running: false,
            tick: 0,
            last_duration: None,
            last_memory: None,
            test_dir,
            last_bench: None,
            modal: InputModal::None,
            filter_query: String::new(),
            stdin_buffer: String::new(),
            test_dir_candidates: Vec::new(),
            selected_test_dir_idx: 0,
        }
    }

    /// Return sidebar items matching `filter_query`, ranked by [`fuzzy_score`].
    pub fn filtered_items(&self) -> Vec<(usize, &SidebarItem)> {
        if self.filter_query.is_empty() {
            return self.items.iter().enumerate().collect();
        }
        let mut scored: Vec<(i32, usize, &SidebarItem)> = self
            .items
            .iter()
            .enumerate()
            .filter_map(|(idx, item)| {
                fuzzy_score(&self.filter_query, &item.label()).map(|s| (s, idx, item))
            })
            .collect();
        scored.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
        scored.into_iter().map(|(_, idx, item)| (idx, item)).collect()
    }

    /// Return the currently highlighted sidebar item (respecting any active fuzzy filter).
    pub fn current_item(&self) -> Option<&SidebarItem> {
        let filtered = self.filtered_items();
        filtered.get(self.selected_item).map(|(_, item)| *item)
    }

    /// Return the currently active preset name, if any.
    pub fn active_preset(&self) -> Option<&str> {
        self.selected_preset
            .and_then(|idx| self.presets.get(idx))
            .map(String::as_str)
    }

    /// Clear previous run outputs/logs before starting a new execution.
    pub fn clear_run_output(&mut self) {
        self.logs.clear();
        self.output_lines.clear();
        self.diff_lines.clear();
        self.test_cases.clear();
        self.selected_test_case = 0;
        self.scroll = 0;
        self.last_bench = None;
    }

    /// Apply an incoming [`UiEvent`] emitted by the background runner worker.
    pub fn apply_event(&mut self, event: UiEvent) {
        match event {
            UiEvent::Log {
                tag,
                message,
                color,
            } => {
                self.logs.push((tag, message, color));
            }
            UiEvent::OutputLine(line) => {
                self.output_lines.push(line);
            }
            UiEvent::Diff { lines, .. } => {
                self.diff_lines = lines;
                if self.tab != MainTab::Tests {
                    self.tab = MainTab::Diff;
                }
            }
            UiEvent::Metrics {
                seconds,
                memory_bytes,
            } => {
                if seconds.is_some() {
                    self.last_duration = seconds;
                }
                if memory_bytes.is_some() {
                    self.last_memory = memory_bytes;
                }
            }
            UiEvent::TestCaseResult {
                name,
                passed,
                elapsed_secs,
                memory_bytes,
                actual_output,
                expected_output,
            } => {
                // Agent-CLI style: collapsed by default until user presses Ctrl+O.
                self.test_cases.push(TestCaseEntry {
                    name,
                    passed,
                    elapsed_secs,
                    memory_bytes,
                    actual_output,
                    expected_output,
                    expanded: false,
                });
                self.tab = MainTab::Tests;
            }
            UiEvent::BenchSummary(stats) => {
                self.last_duration = Some(stats.median_secs);
                if stats.peak_memory_bytes.is_some() {
                    self.last_memory = stats.peak_memory_bytes;
                }
                self.last_bench = Some(stats);
                self.tab = MainTab::Output;
            }
            UiEvent::Stderr(text) => {
                for line in text.lines() {
                    self.output_lines.push(line.to_string());
                }
            }
            UiEvent::CompileResult { .. }
            | UiEvent::RunResult { .. }
            | UiEvent::StressResult { .. } => {}
        }
    }

    /// Toggle expand/collapse on the selected test case (`Ctrl+O`).
    pub fn toggle_selected_test_expand(&mut self) {
        if let Some(entry) = self.test_cases.get_mut(self.selected_test_case) {
            entry.expanded = !entry.expanded;
        }
    }

    /// Handle a single keyboard event and return the resulting [`TuiAction`].
    pub fn handle_key(&mut self, key: KeyEvent) -> TuiAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            return TuiAction::Quit;
        }

        match self.modal {
            InputModal::Filter => return self.handle_filter_key(key),
            InputModal::StdinEditor => return self.handle_stdin_key(key),
            InputModal::TestDirPicker => return self.handle_test_dir_key(key),
            InputModal::None => {}
        }

        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('o') {
            if !self.test_cases.is_empty() {
                self.tab = MainTab::Tests;
                self.toggle_selected_test_expand();
            }
            return TuiAction::None;
        }

        match key.code {
            KeyCode::Char('q') => TuiAction::Quit,
            KeyCode::Char('/') => {
                self.focus = TuiFocus::Sidebar;
                self.modal = InputModal::Filter;
                TuiAction::None
            }
            KeyCode::Char('i') => {
                self.modal = InputModal::StdinEditor;
                TuiAction::None
            }
            KeyCode::Char('d') => {
                self.selected_test_dir_idx = self
                    .test_dir
                    .as_deref()
                    .and_then(|cur| {
                        self.test_dir_candidates
                            .iter()
                            .position(|c| c == cur)
                            .map(|idx| idx + 1)
                    })
                    .unwrap_or(0);
                self.modal = InputModal::TestDirPicker;
                TuiAction::None
            }
            KeyCode::Tab => {
                self.focus = match self.focus {
                    TuiFocus::Sidebar => TuiFocus::Main,
                    TuiFocus::Main => TuiFocus::Sidebar,
                };
                TuiAction::None
            }
            KeyCode::Char('1') => {
                self.tab = MainTab::Output;
                TuiAction::None
            }
            KeyCode::Char('2') => {
                self.tab = MainTab::Diff;
                TuiAction::None
            }
            KeyCode::Char('3') => {
                self.tab = MainTab::Tests;
                TuiAction::None
            }
            KeyCode::Up | KeyCode::Char('k') => {
                self.move_up();
                TuiAction::None
            }
            KeyCode::Down | KeyCode::Char('j') => {
                self.move_down();
                TuiAction::None
            }
            KeyCode::Enter | KeyCode::Char('r') => TuiAction::RunSelected,
            KeyCode::Char('b') => TuiAction::BenchSelected,
            KeyCode::Char('p') => {
                self.cycle_preset();
                TuiAction::None
            }
            KeyCode::Char('t') => {
                self.time_enabled = !self.time_enabled;
                TuiAction::None
            }
            KeyCode::Char('m') => {
                self.mem_enabled = !self.mem_enabled;
                TuiAction::None
            }
            KeyCode::Char('w') => {
                self.watch_enabled = !self.watch_enabled;
                TuiAction::None
            }
            KeyCode::Char('c') => {
                self.clear_run_output();
                TuiAction::None
            }
            _ => TuiAction::None,
        }
    }

    /// Handle keystrokes while the built-in `/` fuzzy sidebar filter is active.
    fn handle_filter_key(&mut self, key: KeyEvent) -> TuiAction {
        match key.code {
            KeyCode::Esc => {
                self.filter_query.clear();
                self.selected_item = 0;
                self.modal = InputModal::None;
            }
            KeyCode::Enter => {
                self.modal = InputModal::None;
            }
            KeyCode::Backspace => {
                self.filter_query.pop();
                self.selected_item = 0;
            }
            KeyCode::Up => {
                self.selected_item = self.selected_item.saturating_sub(1);
            }
            KeyCode::Down => {
                let len = self.filtered_items().len();
                if len > 0 && self.selected_item + 1 < len {
                    self.selected_item += 1;
                }
            }
            KeyCode::Char(c) => {
                self.filter_query.push(c);
                self.selected_item = 0;
            }
            _ => {}
        }
        TuiAction::None
    }

    /// Handle keystrokes while the `i` interactive stdin editor popup is active.
    fn handle_stdin_key(&mut self, key: KeyEvent) -> TuiAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('s') {
            self.modal = InputModal::None;
            return TuiAction::None;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('x') {
            self.stdin_buffer.clear();
            return TuiAction::None;
        }
        match key.code {
            KeyCode::Esc => {
                self.modal = InputModal::None;
            }
            KeyCode::Enter => {
                self.stdin_buffer.push('\n');
            }
            KeyCode::Backspace => {
                self.stdin_buffer.pop();
            }
            KeyCode::Char(c) => {
                self.stdin_buffer.push(c);
            }
            _ => {}
        }
        TuiAction::None
    }

    /// Handle keystrokes while the `d` `--test-dir` picker popup is active.
    fn handle_test_dir_key(&mut self, key: KeyEvent) -> TuiAction {
        let total = self.test_dir_candidates.len() + 1;
        match key.code {
            KeyCode::Esc => {
                self.modal = InputModal::None;
            }
            KeyCode::Up | KeyCode::Char('k') => {
                self.selected_test_dir_idx = self.selected_test_dir_idx.saturating_sub(1);
            }
            KeyCode::Down | KeyCode::Char('j') => {
                if self.selected_test_dir_idx + 1 < total {
                    self.selected_test_dir_idx += 1;
                }
            }
            KeyCode::Enter => {
                if self.selected_test_dir_idx == 0 {
                    self.test_dir = None;
                } else if let Some(dir) =
                    self.test_dir_candidates.get(self.selected_test_dir_idx - 1)
                {
                    self.test_dir = Some(dir.clone());
                    self.tab = MainTab::Tests;
                }
                self.modal = InputModal::None;
            }
            _ => {}
        }
        TuiAction::None
    }

    /// Move selection or scroll upward based on active focus/tab.
    fn move_up(&mut self) {
        match (self.focus, self.tab) {
            (TuiFocus::Sidebar, _) => {
                self.selected_item = self.selected_item.saturating_sub(1);
            }
            (TuiFocus::Main, MainTab::Tests) => {
                self.selected_test_case = self.selected_test_case.saturating_sub(1);
            }
            (TuiFocus::Main, _) => {
                self.scroll = self.scroll.saturating_sub(1);
            }
        }
    }

    /// Move selection or scroll downward based on active focus/tab.
    fn move_down(&mut self) {
        match (self.focus, self.tab) {
            (TuiFocus::Sidebar, _) => {
                let len = self.filtered_items().len();
                if len > 0 && self.selected_item + 1 < len {
                    self.selected_item += 1;
                }
            }
            (TuiFocus::Main, MainTab::Tests) => {
                if !self.test_cases.is_empty()
                    && self.selected_test_case + 1 < self.test_cases.len()
                {
                    self.selected_test_case += 1;
                }
            }
            (TuiFocus::Main, _) => {
                self.scroll = self.scroll.saturating_add(1);
            }
        }
    }

    /// Cycle through available `Run.toml` presets (`None -> 0 -> ... -> None`).
    fn cycle_preset(&mut self) {
        if self.presets.is_empty() {
            self.selected_preset = None;
            return;
        }
        self.selected_preset = match self.selected_preset {
            None => Some(0),
            Some(i) if i + 1 < self.presets.len() => Some(i + 1),
            Some(_) => None,
        };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verify test cases default to collapsed and `Ctrl+O` toggles expand/collapse.
    #[test]
    fn test_ctrl_o_toggles_test_case_collapse() {
        let mut app = TuiApp::new(vec![], vec![], None, false, false, false, None);
        app.apply_event(UiEvent::TestCaseResult {
            name: "1.in".to_string(),
            passed: false,
            elapsed_secs: Some(0.02),
            memory_bytes: None,
            actual_output: "wrong".to_string(),
            expected_output: "right".to_string(),
        });
        assert_eq!(app.tab, MainTab::Tests);
        assert!(!app.test_cases[0].expanded);

        let action = app.handle_key(KeyEvent::new(KeyCode::Char('o'), KeyModifiers::CONTROL));
        assert_eq!(action, TuiAction::None);
        assert!(app.test_cases[0].expanded);

        app.handle_key(KeyEvent::new(KeyCode::Char('o'), KeyModifiers::CONTROL));
        assert!(!app.test_cases[0].expanded);
    }

    /// Verify preset cycling, flag toggles, and quit/run actions.
    #[test]
    fn test_keybindings_and_preset_cycling() {
        let mut app = TuiApp::new(
            vec![SidebarItem::File(PathBuf::from("main.c"))],
            vec!["debug".to_string(), "release".to_string()],
            None,
            false,
            false,
            false,
            None,
        );
        assert_eq!(app.active_preset(), None);
        app.handle_key(KeyEvent::from(KeyCode::Char('p')));
        assert_eq!(app.active_preset(), Some("debug"));
        app.handle_key(KeyEvent::from(KeyCode::Char('p')));
        assert_eq!(app.active_preset(), Some("release"));
        app.handle_key(KeyEvent::from(KeyCode::Char('p')));
        assert_eq!(app.active_preset(), None);

        app.handle_key(KeyEvent::from(KeyCode::Char('t')));
        app.handle_key(KeyEvent::from(KeyCode::Char('m')));
        app.handle_key(KeyEvent::from(KeyCode::Char('w')));
        assert!(app.time_enabled && app.mem_enabled && app.watch_enabled);

        assert_eq!(
            app.handle_key(KeyEvent::from(KeyCode::Char('r'))),
            TuiAction::RunSelected
        );
        assert_eq!(
            app.handle_key(KeyEvent::from(KeyCode::Char('q'))),
            TuiAction::Quit
        );
    }

    /// Verify fuzzy filter (`/`), stdin editor (`i`), and test-dir picker (`d`).
    #[test]
    fn test_fuzzy_filter_stdin_editor_and_test_dir_picker() {
        assert!(fuzzy_score("mc", "main.c").is_some());
        assert!(fuzzy_score("zzz", "main.c").is_none());

        let mut app = TuiApp::new(
            vec![
                SidebarItem::File(PathBuf::from("alpha.py")),
                SidebarItem::File(PathBuf::from("main.c")),
            ],
            vec![],
            None,
            false,
            false,
            false,
            None,
        );
        app.test_dir_candidates = vec!["cases".to_string(), "tests".to_string()];

        // Fuzzy filter `/`
        app.handle_key(KeyEvent::from(KeyCode::Char('/')));
        assert_eq!(app.modal, InputModal::Filter);
        app.handle_key(KeyEvent::from(KeyCode::Char('m')));
        app.handle_key(KeyEvent::from(KeyCode::Char('c')));
        assert_eq!(app.filtered_items().len(), 1);
        assert_eq!(app.current_item().unwrap().label(), "main.c");
        app.handle_key(KeyEvent::from(KeyCode::Enter));
        assert_eq!(app.modal, InputModal::None);

        // Stdin editor `i`
        app.handle_key(KeyEvent::from(KeyCode::Char('i')));
        assert_eq!(app.modal, InputModal::StdinEditor);
        app.handle_key(KeyEvent::from(KeyCode::Char('5')));
        app.handle_key(KeyEvent::from(KeyCode::Enter));
        app.handle_key(KeyEvent::from(KeyCode::Esc));
        assert_eq!(app.modal, InputModal::None);
        assert_eq!(app.stdin_buffer, "5\n");

        // Test-dir picker `d`
        app.handle_key(KeyEvent::from(KeyCode::Char('d')));
        assert_eq!(app.modal, InputModal::TestDirPicker);
        app.handle_key(KeyEvent::from(KeyCode::Down));
        app.handle_key(KeyEvent::from(KeyCode::Enter));
        assert_eq!(app.modal, InputModal::None);
        assert_eq!(app.test_dir.as_deref(), Some("cases"));
        assert_eq!(app.tab, MainTab::Tests);
    }
}
