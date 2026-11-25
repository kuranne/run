//! `ratatui` widget layout and Node/Vite-inspired rendering for [`TuiApp`].

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, List, ListItem, Paragraph, Tabs, Wrap};
use ratatui::Frame;

use crate::util::output::TagColor;
use crate::util::spinner::BRAILLE_FRAMES;

use super::app::{MainTab, TuiApp, TuiFocus};

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
            format!(" · {cwd_label} · preset:{preset}{watch_badge}{metrics_badge}"),
            Style::default().fg(Color::DarkGray),
        ),
    ]);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(Color::DarkGray));
    frame.render_widget(Paragraph::new(line).block(block), area);
}

/// Render the left workspace files & tasks sidebar.
fn render_sidebar(frame: &mut Frame<'_>, area: Rect, app: &TuiApp) {
    let border_color = if app.focus == TuiFocus::Sidebar {
        Color::Cyan
    } else {
        Color::DarkGray
    };
    let block = Block::default()
        .title(" Workspace ")
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(border_color));

    let items: Vec<ListItem<'_>> = app
        .items
        .iter()
        .enumerate()
        .map(|(i, item)| {
            let selected = i == app.selected_item;
            let prefix = if selected { "▸ " } else { "  " };
            let style = if selected {
                Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::White)
            };
            ListItem::new(Line::from(Span::styled(
                format!("{prefix}{}", item.label()),
                style,
            )))
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
            if lines.is_empty() {
                lines.push(Line::from(Span::styled(
                    "Press 'r' or Enter to compile & run the selected target.",
                    Style::default().fg(Color::DarkGray),
                )));
            }
            let para = Paragraph::new(lines)
                .block(content_block)
                .wrap(Wrap { trim: false })
                .scroll((app.scroll, 0));
            frame.render_widget(para, chunks[1]);
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
            "r/Enter Run · Ctrl+O Toggle Test Output · Tab Focus · p Preset · w Watch · t/m Metrics · c Clear · q Quit",
            Style::default().fg(Color::DarkGray),
        ),
    ]);
    frame.render_widget(Paragraph::new(line), area);
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
