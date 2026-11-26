//! `ratatui` widget layout and Node/Vite-inspired rendering for [`TuiApp`].

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear, List, ListItem, Paragraph, Sparkline, Tabs, Wrap};
use ratatui::Frame;

use crate::core::BenchRunner;
use crate::util::output::TagColor;
use crate::util::spinner::BRAILLE_FRAMES;

use super::app::{InputModal, MainTab, SidebarItem, TuiApp, TuiFocus};

/// Map [`TagColor`] to a `ratatui::style::Color`.
const fn to_ratatui_color(color: TagColor) -> Color {
    match color {
        TagColor::Green => Color::Green,
        TagColor::Cyan => Color::Cyan,
        TagColor::Yellow => Color::Yellow,
        TagColor::Red => Color::Red,
        TagColor::Gray => Color::DarkGray,
        TagColor::Reset => Color::Reset,
    }
}

/// Render the full interactive workspace TUI frame.
pub fn render_ui(frame: &mut Frame<'_>, app: &TuiApp, cwd_label: &str) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(2),
        ])
        .split(frame.area());

    render_header(frame, outer[0], app, cwd_label);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(28), Constraint::Percentage(72)])
        .split(outer[1]);

    render_sidebar(frame, body[0], app);
    render_main_pane(frame, body[1], app);
    render_footer(frame, outer[2], app);

    match app.modal {
        InputModal::StdinEditor => render_stdin_modal(frame, app),
        InputModal::TestDirPicker => render_test_dir_modal(frame, app),
        InputModal::None | InputModal::Filter => {}
    }
}

/// Render the top Vite-style status header bar with animated Braille spinner when running.
fn render_header(frame: &mut Frame<'_>, area: Rect, app: &TuiApp, cwd_label: &str) {
    let spin_span = if app.running {
        let glyph = BRAILLE_FRAMES[app.tick % BRAILLE_FRAMES.len()];
        Span::styled(
            format!(" {glyph} RUNNING "),
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )
    } else {
        Span::styled(
            " ◆ READY ",
            Style::default().fg(Color::Green).add_modifier(Modifier::BOLD),
        )
    };

    let preset = app.active_preset().unwrap_or("default");
    let watch_badge = if app.watch_enabled { " · watch:on" } else { "" };
    let stdin_badge = if !app.stdin_buffer.is_empty() {
        format!(" · stdin:{}B", app.stdin_buffer.len())
    } else {
        String::new()
    };
    let test_dir_badge = app
        .test_dir
        .as_deref()
        .map(|d| format!(" · tests:{d}"))
        .unwrap_or_default();
    let metrics_badge = match (app.time_enabled, app.mem_enabled) {
        (true, true) => " · time+mem",
        (true, false) => " · time",
        (false, true) => " · mem",
        (false, false) => "",
    };

    let line = Line::from(vec![
        spin_span,
        Span::styled("│ ", Style::default().fg(Color::DarkGray)),
        Span::styled(
            format!("run v{}", env!("CARGO_PKG_VERSION")),
            Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!(
                " · {cwd_label} · preset:{preset}{watch_badge}{stdin_badge}{test_dir_badge}{metrics_badge}"
            ),
            Style::default().fg(Color::DarkGray),
        ),
    ]);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(Color::DarkGray));
    frame.render_widget(Paragraph::new(line).block(block), area);
}

/// Render the left workspace files & tasks sidebar (with built-in `/` fuzzy filter).
fn render_sidebar(frame: &mut Frame<'_>, area: Rect, app: &TuiApp) {
    let border_color = if app.focus == TuiFocus::Sidebar || app.modal == InputModal::Filter {
        Color::Cyan
    } else {
        Color::DarkGray
    };
    let title = if app.modal == InputModal::Filter {
        format!(" Filter: /{}▌ ", app.filter_query)
    } else if !app.filter_query.is_empty() {
        format!(" Workspace (/{}) ", app.filter_query)
    } else if !app.marked_files.is_empty() {
        format!(" Workspace ({} marked) ", app.marked_files.len())
    } else {
        " Workspace ".to_string()
    };
    let block = Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(border_color));

    let filtered = app.filtered_items();
    let any_marked = !app.marked_files.is_empty();
    let items: Vec<ListItem<'_>> = filtered
        .iter()
        .enumerate()
        .map(|(i, (_, item))| {
            let selected = i == app.selected_item;
            let marked = app.is_marked(item);
            let prefix = if selected { "▸ " } else { "  " };
            let style = if selected {
                Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)
            } else if marked {
                Style::default().fg(Color::Green)
            } else {
                Style::default().fg(Color::White)
            };
            let mut spans = vec![Span::styled(prefix, style)];
            if any_marked {
                let (box_str, box_color) = match item {
                    SidebarItem::File(_) if marked => ("[●] ", Color::Green),
                    SidebarItem::File(_) => ("[ ] ", Color::DarkGray),
                    SidebarItem::Task(_) => ("    ", Color::DarkGray),
                };
                spans.push(Span::styled(box_str, Style::default().fg(box_color)));
            }
            spans.push(Span::styled(item.label(), style));
            ListItem::new(Line::from(spans))
        })
        .collect();

    frame.render_widget(List::new(items).block(block), area);
}

/// Render the right main pane (`Output`, `Diff`, and collapsible `Tests`).
fn render_main_pane(frame: &mut Frame<'_>, area: Rect, app: &TuiApp) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(4)])
        .split(area);

    let border_color = if app.focus == TuiFocus::Main {
        Color::Cyan
    } else {
        Color::DarkGray
    };

    let tab_titles = vec![
        Line::from("1 Output"),
        Line::from("2 Diff"),
        Line::from(format!("3 Tests ({})", app.test_cases.len())),
    ];
    let selected_tab = match app.tab {
        MainTab::Output => 0,
        MainTab::Diff => 1,
        MainTab::Tests => 2,
    };
    let tabs = Tabs::new(tab_titles)
        .select(selected_tab)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(border_color)),
        )
        .highlight_style(Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD));
    frame.render_widget(tabs, chunks[0]);

    let content_block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(border_color));

    match app.tab {
        MainTab::Output => {
            let (bench_area, log_area) = if app.last_bench.is_some() {
                let split = Layout::default()
                    .direction(Direction::Vertical)
                    .constraints([Constraint::Length(5), Constraint::Min(3)])
                    .split(chunks[1]);
                (Some(split[0]), split[1])
            } else {
                (None, chunks[1])
            };

            if let (Some(b_area), Some(stats)) = (bench_area, app.last_bench.as_ref()) {
                let title = format!(
                    " Bench ({} runs, {} warmup) · min {} · med {} · mean {} · p95 {} · max {} ",
                    stats.runs,
                    stats.warmup,
                    BenchRunner::format_duration(stats.min_secs),
                    BenchRunner::format_duration(stats.median_secs),
                    BenchRunner::format_duration(stats.mean_secs),
                    BenchRunner::format_duration(stats.p95_secs),
                    BenchRunner::format_duration(stats.max_secs),
                );
                let data: Vec<u64> = stats
                    .samples_secs
                    .iter()
                    .map(|&s| ((s * 1_000_000.0).round() as u64).max(1))
                    .collect();
                let spark = Sparkline::default()
                    .block(
                        Block::default()
                            .title(title)
                            .borders(Borders::ALL)
                            .border_type(BorderType::Rounded)
                            .border_style(Style::default().fg(Color::Green)),
                    )
                    .data(&data)
                    .style(Style::default().fg(Color::Cyan));
                frame.render_widget(spark, b_area);
            }

            let mut lines: Vec<Line<'_>> = app
                .logs
                .iter()
                .map(|(tag, msg, col)| {
                    Line::from(vec![
                        Span::styled("│ ", Style::default().fg(Color::DarkGray)),
                        Span::styled(
                            format!("[{tag}] "),
                            Style::default()
                                .fg(to_ratatui_color(*col))
                                .add_modifier(Modifier::BOLD),
                        ),
                        Span::raw(msg.as_str()),
                    ])
                })
                .collect();
            for out in &app.output_lines {
                lines.push(Line::from(out.as_str()));
            }
            if lines.is_empty() && app.last_bench.is_none() {
                lines.push(Line::from(Span::styled(
                    "Press 'r' to run or 'b' to benchmark the selected target.",
                    Style::default().fg(Color::DarkGray),
                )));
            }
            let para = Paragraph::new(lines)
                .block(content_block)
                .wrap(Wrap { trim: false })
                .scroll((app.scroll, 0));
            frame.render_widget(para, log_area);
        }
        MainTab::Diff => {
            let lines: Vec<Line<'_>> = if app.diff_lines.is_empty() {
                vec![Line::from(Span::styled(
                    "No --expect diff recorded.",
                    Style::default().fg(Color::DarkGray),
                ))]
            } else {
                app.diff_lines
                    .iter()
                    .map(|l| {
                        let c = if l.starts_with('+') && !l.starts_with("+++") {
                            Color::Green
                        } else if l.starts_with('-') && !l.starts_with("---") {
                            Color::Red
                        } else if l.starts_with("@@") {
                            Color::Cyan
                        } else {
                            Color::DarkGray
                        };
                        Line::from(Span::styled(l.as_str(), Style::default().fg(c)))
                    })
                    .collect()
            };
            let para = Paragraph::new(lines)
                .block(content_block)
                .scroll((app.scroll, 0));
            frame.render_widget(para, chunks[1]);
        }
        MainTab::Tests => {
            let mut lines: Vec<Line<'_>> = Vec::new();
            if app.test_cases.is_empty() {
                lines.push(Line::from(Span::styled(
                    "No batch test cases yet. Run with --test-dir <dir>.",
                    Style::default().fg(Color::DarkGray),
                )));
            } else {
                for (i, tc) in app.test_cases.iter().enumerate() {
                    let is_sel = i == app.selected_test_case;
                    let arrow = if tc.expanded { "▾" } else { "▸" };
                    let (status_glyph, status_color) = if tc.passed {
                        ("✔", Color::Green)
                    } else {
                        ("✖", Color::Red)
                    };
                    let dur = tc
                        .elapsed_secs
                        .map(|s| format!(" ({:.0}ms)", s * 1000.0))
                        .unwrap_or_default();
                    let hint = if tc.expanded {
                        " [Ctrl+O collapse]"
                    } else {
                        " [Ctrl+O expand]"
                    };
                    let row_style = if is_sel {
                        Style::default().bg(Color::DarkGray).add_modifier(Modifier::BOLD)
                    } else {
                        Style::default()
                    };
                    lines.push(Line::from(vec![
                        Span::styled(format!("{arrow} "), row_style.fg(Color::Cyan)),
                        Span::styled(format!("{status_glyph} "), row_style.fg(status_color)),
                        Span::styled(format!("{}{dur}", tc.name), row_style.fg(Color::White)),
                        Span::styled(hint, row_style.fg(Color::DarkGray)),
                    ]));

                    if tc.expanded {
                        lines.push(Line::from(Span::styled(
                            "  │ Expected:",
                            Style::default().fg(Color::Yellow),
                        )));
                        for exp_line in tc.expected_output.lines() {
                            lines.push(Line::from(Span::styled(
                                format!("  │   {exp_line}"),
                                Style::default().fg(Color::Green),
                            )));
                        }
                        lines.push(Line::from(Span::styled(
                            "  │ Actual:",
                            Style::default().fg(Color::Yellow),
                        )));
                        for act_line in tc.actual_output.lines() {
                            lines.push(Line::from(Span::styled(
                                format!("  │   {act_line}"),
                                Style::default().fg(if tc.passed { Color::White } else { Color::Red }),
                            )));
                        }
                    }
                }
            }
            let para = Paragraph::new(lines)
                .block(content_block)
                .wrap(Wrap { trim: false });
            frame.render_widget(para, chunks[1]);
        }
    }
}

/// Render the bottom metrics and keybind hints footer.
fn render_footer(frame: &mut Frame<'_>, area: Rect, app: &TuiApp) {
    let time_str = app
        .last_duration
        .map(|s| format!("⏱ {s:.3}s  "))
        .unwrap_or_default();
    let mem_str = app
        .last_memory
        .filter(|&b| b > 0)
        .map(|b| format!("◆ {:.2} MB  ", b as f64 / (1024.0 * 1024.0)))
        .unwrap_or_default();

    let line = Line::from(vec![
        Span::styled(format!("{time_str}{mem_str}"), Style::default().fg(Color::Yellow)),
        Span::styled(
            "Space Multi · r Run · b Bench · / Filter · i Stdin · d TestDir · w Watch · Ctrl+O Test · p Preset · q Quit",
            Style::default().fg(Color::DarkGray),
        ),
    ]);
    frame.render_widget(Paragraph::new(line), area);
}

/// Compute a centered popup rectangle of `(percent_x, percent_y)` inside `area`.
fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let v = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(v[1])[1]
}

/// Render the interactive multi-line stdin editor modal (`i`).
fn render_stdin_modal(frame: &mut Frame<'_>, app: &TuiApp) {
    let popup = centered_rect(64, 50, frame.area());
    frame.render_widget(Clear, popup);
    let block = Block::default()
        .title(" Stdin Editor (Esc / Ctrl+S save · Ctrl+X clear) ")
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(Color::Cyan));
    let display = if app.stdin_buffer.is_empty() {
        "Type stdin lines here...▌".to_string()
    } else {
        format!("{}▌", app.stdin_buffer)
    };
    let para = Paragraph::new(display)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(para, popup);
}

/// Render the `--test-dir` directory picker modal (`d`).
fn render_test_dir_modal(frame: &mut Frame<'_>, app: &TuiApp) {
    let popup = centered_rect(50, 50, frame.area());
    frame.render_widget(Clear, popup);
    let block = Block::default()
        .title(" Select --test-dir (Enter select · Esc cancel) ")
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(Color::Cyan));

    let mut rows = vec!["[None / Disable --test-dir]".to_string()];
    rows.extend(app.test_dir_candidates.iter().cloned());

    let items: Vec<ListItem<'_>> = rows
        .iter()
        .enumerate()
        .map(|(i, label)| {
            let selected = i == app.selected_test_dir_idx;
            let prefix = if selected { "▸ " } else { "  " };
            let style = if selected {
                Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::White)
            };
            ListItem::new(Line::from(Span::styled(format!("{prefix}{label}"), style)))
        })
        .collect();

    frame.render_widget(List::new(items).block(block), popup);
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    use crate::tui::app::SidebarItem;
    use crate::util::output::UiEvent;

    use super::*;

    /// Verify TUI renders cleanly onto `TestBackend` with collapsed test cases by default.
    #[test]
    fn test_render_ui_with_test_backend() {
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::new(backend).expect("test terminal");
        let mut app = TuiApp::new(
            vec![SidebarItem::File(PathBuf::from("main.c"))],
            vec!["debug".to_string()],
            None,
            true,
            false,
            false,
            None,
        );
        app.apply_event(UiEvent::TestCaseResult {
            name: "1.in".to_string(),
            passed: true,
            elapsed_secs: Some(0.015),
            memory_bytes: None,
            actual_output: "42".to_string(),
            expected_output: "42".to_string(),
        });

        terminal
            .draw(|f| render_ui(f, &app, "demo"))
            .expect("draw frame");
        let buf = terminal.backend().buffer().clone();
        let text: String = buf.content().iter().map(|c| c.symbol()).collect();
        assert!(text.contains("main.c"));
        assert!(text.contains("[Ctrl+O expand]"));
        assert!(!text.contains("Expected:"));
    }
}
