use std::sync::Mutex;

use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, size};
use ratatui::backend::CrosstermBackend;
use ratatui::buffer::Buffer;
use ratatui::style::{Style, Stylize};
use ratatui::widgets::Clear;
use ratatui::Terminal;

static TERMINAL: Mutex<Option<Terminal<CrosstermBackend<std::io::Stdout>>>> = Mutex::new(None);

#[derive(Debug, Clone)]
pub struct LayoutRect {
    pub kind: String,
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    pub text: String,
}

pub fn init() -> Result<(), String> {
    let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Ok(()); // already initialised, no-op
    }
    enable_raw_mode().map_err(|e| e.to_string())?;
    let stdout = std::io::stdout();
    let backend = CrosstermBackend::new(stdout);
    let terminal = Terminal::new(backend).map_err(|e| e.to_string())?;
    *guard = Some(terminal);
    Ok(())
}

pub fn cleanup() -> Result<(), String> {
    // Disable raw mode BEFORE dropping the terminal — if disable_raw_mode
    // fails, this order still restores the terminal first. Also, if the
    // Mutex is poisoned (panic during draw), this still restores normal
    // mode even though we can't reach the guard.
    disable_raw_mode().map_err(|e| e.to_string())?;
    let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
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
    let focused_idx = if focused_idx < 0 { usize::MAX } else { focused_idx as usize };

    terminal
        .draw(|frame| {
            let area = frame.area();
            frame.render_widget(Clear, area);
            let buf = frame.buffer_mut();
            for (i, rect) in rects.iter().enumerate() {
                let x = rect.x as u16;
                let y = rect.y as u16;
                let style = if i == focused_idx {
                    Style::default().reversed()
                } else {
                    Style::default()
                };
                match rect.kind.as_str() {
                    "text" => {
                        buf.set_string(x, y, &rect.text, Style::default());
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
        match event::read().map_err(|e| e.to_string())? {
            Event::Key(key) => match key.code {
                KeyCode::Char('q') => return Ok("quit".to_string()),
                KeyCode::Char('c') if key.modifiers == KeyModifiers::CONTROL => {
                    return Ok("quit".to_string());
                }
                KeyCode::Tab => return Ok("focus_next".to_string()),
                KeyCode::BackTab => return Ok("focus_prev".to_string()),
                KeyCode::Enter | KeyCode::Char(' ') => return Ok("activate".to_string()),
                _ => {}
            },
            _ => {}
        }
    }
}

fn draw_button_border(buf: &mut Buffer, x: u16, y: u16, label: &str, style: Style) {
    let inner = if label.is_empty() {
        1
    } else {
        label.chars().count()
    };
    let dash = "─".repeat(inner);
    buf.set_string(x, y, format!("┌{}┐", dash), style);
    buf.set_string(x, y + 1, format!("│{}│", label), style);
    buf.set_string(x, y + 2, format!("└{}┘", dash), style);
}
