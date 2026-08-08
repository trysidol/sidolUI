use std::sync::Mutex;
use std::time::Duration;

use crossterm::event::{self, Event, KeyCode, KeyModifiers, MouseEventKind};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, size};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::buffer::Buffer;
use ratatui::style::{Color, Style, Stylize};
use ratatui::widgets::Clear;
use unicode_width::UnicodeWidthStr;

static TERMINAL: Mutex<Option<Terminal<CrosstermBackend<std::io::Stdout>>>> = Mutex::new(None);

#[derive(Debug, Clone)]
pub struct LayoutRect {
    pub kind: String,
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    pub depth: usize,
    pub text: String,
    pub fg: String,
    pub bg: String,
    pub disabled: bool,
}

pub fn init() -> Result<(), String> {
    let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Ok(()); // already initialised, no-op
    }
    enable_raw_mode().map_err(|e| e.to_string())?;
    let stdout = std::io::stdout();
    let backend = CrosstermBackend::new(stdout);
    let terminal = match Terminal::new(backend) {
        Ok(terminal) => terminal,
        Err(error) => {
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
    disable_raw_mode().map_err(|e| e.to_string())?;
    guard.take();
    Ok(())
}

pub fn get_size() -> Result<(u16, u16), String> {
    size().map_err(|e| e.to_string())
}

pub fn render_frame(rects: &[LayoutRect], focused_idx: i32) -> Result<String, String> {
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
            let mut scroll_ancestors: Vec<(usize, f32, f32, f32, f32)> = Vec::new();
            for (i, rect) in rects.iter().enumerate() {
                while scroll_ancestors
                    .last()
                    .is_some_and(|(depth, ..)| *depth >= rect.depth)
                {
                    scroll_ancestors.pop();
                }
                let clipped = scroll_ancestors.iter().any(|(_, x, y, w, h)| {
                    rect.x < *x || rect.y < *y || rect.x >= *x + *w || rect.y >= *y + *h
                });
                if rect.kind == "scroll_view" {
                    scroll_ancestors.push((rect.depth, rect.x, rect.y, rect.w, rect.h));
                }
                if clipped || rect.x >= area.right() as f32 || rect.y >= area.bottom() as f32 {
                    continue;
                }
                let x = rect.x as u16;
                let y = rect.y as u16;
                let fg_color = hex_to_color(&rect.fg);
                let bg_color = hex_to_color(&rect.bg);
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

    loop {
        if !event::poll(Duration::from_millis(50)).map_err(|e| e.to_string())? {
            return Ok("tick".to_string());
        }
        let evt = event::read().map_err(|e| e.to_string())?;
        match &evt {
            Event::Key(key) => match key.code {
                KeyCode::Char('q') => return Ok("quit".to_string()),
                KeyCode::Char('c') if key.modifiers == KeyModifiers::CONTROL => {
                    return Ok("quit".to_string());
                }
                KeyCode::Tab => return Ok("focus_next".to_string()),
                KeyCode::BackTab => return Ok("focus_prev".to_string()),
                KeyCode::Enter | KeyCode::Char(' ') => return Ok("activate".to_string()),
                KeyCode::Esc => return Ok("key@esc".to_string()),
                KeyCode::Up => return Ok("key@up".to_string()),
                KeyCode::Down => return Ok("key@down".to_string()),
                KeyCode::Left => return Ok("key@left".to_string()),
                KeyCode::Right => return Ok("key@right".to_string()),
                KeyCode::Backspace => return Ok("key@backspace".to_string()),
                KeyCode::Delete => return Ok("key@delete".to_string()),
                KeyCode::Home => return Ok("key@home".to_string()),
                KeyCode::End => return Ok("key@end".to_string()),
                KeyCode::Char(ch) => return Ok(format!("key@{}", ch.to_ascii_lowercase())),
                _ => {}
            },
            Event::Mouse(mouse) => {
                if let MouseEventKind::Down(_) = mouse.kind {
                    return Ok(format!("click@{}@{}", mouse.column, mouse.row));
                }
            }
            _ => {}
        }
    }
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
