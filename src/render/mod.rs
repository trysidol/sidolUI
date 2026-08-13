//! Terminal render surface — draws layout snapshots and transduces input.
//!
//! The engine is deliberately policy-free: it maps crossterm events to
//! structured `EventData` and nothing more. Quit bindings, focus
//! navigation, and activation keys are surface policy and live in Python
//! (`sidol/surfaces/tui.py`). Hardcoding them here once made it
//! impossible to type 'q' into a TextField — the engine swallowed the
//! key before Python ever saw it.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use crossterm::cursor::{Hide, Show};
use crossterm::event::{self, Event, KeyCode, KeyModifiers, MouseEventKind};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode, size,
};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::buffer::Buffer;
use ratatui::style::{Color, Style, Stylize};
use ratatui::widgets::Clear;
use unicode_width::UnicodeWidthStr;

use crate::layout::LayoutEntry;

static TERMINAL: Mutex<Option<Terminal<CrosstermBackend<std::io::Stdout>>>> = Mutex::new(None);

/// One terminal event, engine-policy-free. Converted to a Python dict at
/// the FFI boundary (see lib.rs `event_to_py`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EventData {
    /// No input arrived within the poll window. Lets the surface run
    /// periodic housekeeping (worker polling, file watching).
    Tick,
    /// A key press. `key` is the canonical name: special keys are
    /// lowercase words ("enter", "esc", "backtab", ...), printable
    /// characters are the character itself with case preserved ("a",
    /// "A", "@"). Modifiers are reported separately.
    Key {
        key: String,
        ctrl: bool,
        alt: bool,
        shift: bool,
    },
    /// Mouse button press at cell coordinates.
    Click { x: u16, y: u16 },
    /// Terminal was resized. The surface must re-layout and redraw.
    Resize,
}

/// Translate a crossterm event into `EventData`. Returns `None` for
/// events the surface does not consume (key release, mouse move, ...).
/// Pure function — unit-tested with `cargo test`.
pub fn translate_event(evt: &Event) -> Option<EventData> {
    match evt {
        Event::Key(key) => {
            let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
            let alt = key.modifiers.contains(KeyModifiers::ALT);
            let shift = key.modifiers.contains(KeyModifiers::SHIFT);
            let name = match key.code {
                // Preserve the exact character — case and symbols matter
                // for text input. Policy (quit keys, activation) is the
                // surface's job.
                KeyCode::Char(ch) => ch.to_string(),
                KeyCode::Enter => "enter".to_string(),
                KeyCode::Esc => "esc".to_string(),
                KeyCode::Backspace => "backspace".to_string(),
                KeyCode::Delete => "delete".to_string(),
                KeyCode::Home => "home".to_string(),
                KeyCode::End => "end".to_string(),
                KeyCode::Up => "up".to_string(),
                KeyCode::Down => "down".to_string(),
                KeyCode::Left => "left".to_string(),
                KeyCode::Right => "right".to_string(),
                KeyCode::Tab => "tab".to_string(),
                KeyCode::BackTab => "backtab".to_string(),
                KeyCode::PageUp => "pageup".to_string(),
                KeyCode::PageDown => "pagedown".to_string(),
                _ => return None,
            };
            Some(EventData::Key {
                key: name,
                ctrl,
                alt,
                shift,
            })
        }
        Event::Mouse(mouse) => match mouse.kind {
            MouseEventKind::Down(_) => Some(EventData::Click {
                x: mouse.column,
                y: mouse.row,
            }),
            _ => None,
        },
        Event::Resize(_, _) => Some(EventData::Resize),
        _ => None,
    }
}

/// Block (up to the 50 ms poll quantum) for the next consumable event.
/// Returns `Tick` when the quantum elapses with no input. Unrecognised
/// events are skipped within the same call.
pub fn read_event() -> Result<EventData, String> {
    loop {
        if !event::poll(Duration::from_millis(50)).map_err(|e| e.to_string())? {
            return Ok(EventData::Tick);
        }
        let evt = event::read().map_err(|e| e.to_string())?;
        if let Some(data) = translate_event(&evt) {
            return Ok(data);
        }
    }
}

/// A scroll viewport encountered while walking the flat rect list. Stores the
/// cumulative scroll offset inherited from ancestor scroll containers.
struct ScrollAncestor {
    depth: usize,
    x: f32,
    y: f32,
    w: f32,
    h: f32,
    cum_off_x: f32,
    cum_off_y: f32,
}

pub fn init() -> Result<(), String> {
    let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Ok(()); // already initialised, no-op
    }
    enable_raw_mode().map_err(|e| e.to_string())?;
    let mut stdout = std::io::stdout();
    if let Err(error) = execute!(stdout, EnterAlternateScreen, Hide) {
        let _ = disable_raw_mode();
        return Err(error.to_string());
    }
    let backend = CrosstermBackend::new(stdout);
    let terminal = match Terminal::new(backend) {
        Ok(terminal) => terminal,
        Err(error) => {
            let mut stdout = std::io::stdout();
            let _ = execute!(stdout, LeaveAlternateScreen, Show);
            let _ = disable_raw_mode();
            return Err(error.to_string());
        }
    };
    *guard = Some(terminal);
    Ok(())
}

pub fn cleanup() -> Result<(), String> {
    let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
    if guard.is_none() {
        return Ok(());
    }
    let terminal = guard.as_mut().expect("terminal checked above");
    terminal.show_cursor().map_err(|e| e.to_string())?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen, Show).map_err(|e| e.to_string())?;
    disable_raw_mode().map_err(|e| e.to_string())?;
    guard.take();
    Ok(())
}

pub fn get_size() -> Result<(u16, u16), String> {
    size().map_err(|e| e.to_string())
}

/// Draw one frame from a layout snapshot, then block for the next event.
/// The terminal lock is dropped before event polling so the draw and the
/// wait are independently callable (see `read_event` for wait-only steps).
pub fn render_frame(rects: &[LayoutEntry], focused_idx: i32) -> Result<EventData, String> {
    {
        let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
        let terminal = guard.as_mut().ok_or("TUI not initialised")?;

        // Guard negative values (the "no focus" sentinel) before the usize cast.
        let focused_idx = if focused_idx < 0 {
            usize::MAX
        } else {
            focused_idx as usize
        };

        terminal
            .draw(|frame| {
                let area = frame.area();
                frame.render_widget(Clear, area);
                let buf = frame.buffer_mut();
                // Colour strings repeat across rects (theme tokens); parse
                // each distinct value once per frame instead of per rect.
                let mut color_cache: HashMap<&str, Color> = HashMap::new();
                // Each entry tracks a scroll viewport and the cumulative
                // scroll offset inherited from its ancestors.
                let mut scroll_ancestors: Vec<ScrollAncestor> = Vec::new();
                for (i, rect) in rects.iter().enumerate() {
                    while scroll_ancestors
                        .last()
                        .is_some_and(|a| a.depth >= rect.depth)
                    {
                        scroll_ancestors.pop();
                    }

                    let (off_x, off_y) = match scroll_ancestors.last() {
                        Some(a) => (a.cum_off_x, a.cum_off_y),
                        None => (0.0, 0.0),
                    };

                    let clipped = scroll_ancestors.iter().any(|a| {
                        rect.x - a.cum_off_x >= a.x + a.w
                            || rect.x - a.cum_off_x + rect.w <= a.x
                            || rect.y - a.cum_off_y >= a.y + a.h
                            || rect.y - a.cum_off_y + rect.h <= a.y
                    });

                    if rect.kind == "scroll_view" {
                        scroll_ancestors.push(ScrollAncestor {
                            depth: rect.depth,
                            x: rect.x,
                            y: rect.y,
                            w: rect.w,
                            h: rect.h,
                            cum_off_x: off_x + rect.scroll_x,
                            cum_off_y: off_y + rect.scroll_y,
                        });
                    }

                    let draw_x = rect.x - off_x;
                    let draw_y = rect.y - off_y;
                    if clipped
                        || draw_x < 0.0
                        || draw_y < 0.0
                        || draw_x >= area.right() as f32
                        || draw_y >= area.bottom() as f32
                    {
                        continue;
                    }
                    let x = draw_x as u16;
                    let y = draw_y as u16;
                    let fg_color = *color_cache
                        .entry(rect.fg.as_str())
                        .or_insert_with(|| hex_to_color(&rect.fg));
                    let bg_color = *color_cache
                        .entry(rect.bg.as_str())
                        .or_insert_with(|| hex_to_color(&rect.bg));
                    let base_style = Style::default().fg(fg_color).bg(bg_color);
                    let style = if rect.disabled {
                        base_style.dim()
                    } else if i == focused_idx {
                        base_style.reversed()
                    } else {
                        base_style
                    };
                    match rect.kind.as_str() {
                        "text" => {
                            buf.set_string(x, y, &rect.text, style);
                        }
                        "button" => {
                            draw_button_border(buf, x, y, &rect.text, style);
                        }
                        _ => {}
                    }
                }
            })
            .map_err(|e| e.to_string())?;
    }

    read_event()
}

/// Parse a `#RRGGBB` hex string into a ratatui Color.
/// Returns `Color::Reset` for empty/invalid strings.
fn hex_to_color(hex: &str) -> Color {
    if hex.len() != 7 || !hex.starts_with('#') {
        return Color::Reset;
    }
    let Ok(r) = u8::from_str_radix(&hex[1..3], 16) else {
        return Color::Reset;
    };
    let Ok(g) = u8::from_str_radix(&hex[3..5], 16) else {
        return Color::Reset;
    };
    let Ok(b) = u8::from_str_radix(&hex[5..7], 16) else {
        return Color::Reset;
    };
    Color::Rgb(r, g, b)
}

fn draw_button_border(buf: &mut Buffer, x: u16, y: u16, label: &str, style: Style) {
    let inner = if label.is_empty() {
        1
    } else {
        UnicodeWidthStr::width(label)
    };
    let dash = "─".repeat(inner);
    let bottom = buf.area().bottom();
    if y < bottom {
        buf.set_string(x, y, format!("┌{}┐", dash), style);
    }
    if y.saturating_add(1) < bottom {
        buf.set_string(x, y + 1, format!("│{}│", label), style);
    }
    if y.saturating_add(2) < bottom {
        buf.set_string(x, y + 2, format!("└{}┘", dash), style);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyEvent, MouseButton, MouseEvent};

    fn key(code: KeyCode, modifiers: KeyModifiers) -> Event {
        Event::Key(KeyEvent::new(code, modifiers))
    }

    #[test]
    fn char_key_preserves_case_and_reports_shift() {
        let evt = key(KeyCode::Char('Q'), KeyModifiers::SHIFT);
        assert_eq!(
            translate_event(&evt),
            Some(EventData::Key {
                key: "Q".to_string(),
                ctrl: false,
                alt: false,
                shift: true,
            })
        );
    }

    #[test]
    fn plain_char_has_no_modifiers() {
        let evt = key(KeyCode::Char('q'), KeyModifiers::NONE);
        assert_eq!(
            translate_event(&evt),
            Some(EventData::Key {
                key: "q".to_string(),
                ctrl: false,
                alt: false,
                shift: false,
            })
        );
    }

    #[test]
    fn symbol_characters_pass_through() {
        // "@" is needed to type an email address — the old protocol
        // lowercased and dropped everything outside a hardcoded set.
        let evt = key(KeyCode::Char('@'), KeyModifiers::SHIFT);
        assert_eq!(
            translate_event(&evt),
            Some(EventData::Key {
                key: "@".to_string(),
                ctrl: false,
                alt: false,
                shift: true,
            })
        );
    }

    #[test]
    fn ctrl_combination_reports_modifier() {
        let evt = key(KeyCode::Char('c'), KeyModifiers::CONTROL);
        assert_eq!(
            translate_event(&evt),
            Some(EventData::Key {
                key: "c".to_string(),
                ctrl: true,
                alt: false,
                shift: false,
            })
        );
    }

    #[test]
    fn special_keys_use_canonical_names() {
        for (code, name) in [
            (KeyCode::Enter, "enter"),
            (KeyCode::Esc, "esc"),
            (KeyCode::Tab, "tab"),
            (KeyCode::BackTab, "backtab"),
            (KeyCode::Up, "up"),
            (KeyCode::Down, "down"),
            (KeyCode::Left, "left"),
            (KeyCode::Right, "right"),
            (KeyCode::Backspace, "backspace"),
            (KeyCode::Delete, "delete"),
            (KeyCode::Home, "home"),
            (KeyCode::End, "end"),
            (KeyCode::PageUp, "pageup"),
            (KeyCode::PageDown, "pagedown"),
        ] {
            let evt = key(code, KeyModifiers::NONE);
            assert_eq!(
                translate_event(&evt),
                Some(EventData::Key {
                    key: name.to_string(),
                    ctrl: false,
                    alt: false,
                    shift: false,
                }),
                "wrong mapping for {name}"
            );
        }
    }

    #[test]
    fn mouse_down_becomes_click_with_cell_coords() {
        let evt = Event::Mouse(MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 12,
            row: 7,
            modifiers: KeyModifiers::NONE,
        });
        assert_eq!(
            translate_event(&evt),
            Some(EventData::Click { x: 12, y: 7 })
        );
    }

    #[test]
    fn mouse_move_is_ignored() {
        let evt = Event::Mouse(MouseEvent {
            kind: MouseEventKind::Moved,
            column: 1,
            row: 1,
            modifiers: KeyModifiers::NONE,
        });
        assert_eq!(translate_event(&evt), None);
    }

    #[test]
    fn resize_is_reported() {
        assert_eq!(
            translate_event(&Event::Resize(120, 40)),
            Some(EventData::Resize)
        );
    }

    #[test]
    fn focus_events_are_ignored() {
        assert_eq!(translate_event(&Event::FocusGained), None);
        assert_eq!(translate_event(&Event::FocusLost), None);
    }
}
