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

use crossterm::SynchronizedUpdate;
use crossterm::cursor::{Hide, Show};
use crossterm::event::{self, Event, KeyCode, KeyModifiers, MouseEventKind};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode, size,
};
use ratatui::Terminal;
#[cfg(test)]
use ratatui::backend::TestBackend;
use ratatui::backend::{Backend, ClearType, CrosstermBackend, WindowSize};
use ratatui::buffer::{Buffer, Cell};
use ratatui::layout::{Position, Size};
use ratatui::style::{Color, Style, Stylize};
use ratatui::widgets::Clear;
use unicode_width::UnicodeWidthStr;

use crate::layout::{LayoutEntry, NodeKind};

/// The backend behind the process-global terminal. `ratatui::backend::Backend`
/// is not object-safe (`draw` and `set_cursor_position` are generic), so the
/// prod crossterm backend and the test-only `TestBackend` share this enum
/// instead of `Box<dyn Backend>`. The test variant exists only in test builds.
enum AnyBackend {
    Crossterm(CrosstermBackend<std::io::Stdout>),
    #[cfg(test)]
    Test(TestBackend),
}

impl Backend for AnyBackend {
    fn draw<'a, I>(&mut self, content: I) -> std::io::Result<()>
    where
        I: Iterator<Item = (u16, u16, &'a Cell)>,
    {
        match self {
            AnyBackend::Crossterm(backend) => backend.draw(content),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.draw(content),
        }
    }

    fn hide_cursor(&mut self) -> std::io::Result<()> {
        match self {
            AnyBackend::Crossterm(backend) => backend.hide_cursor(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.hide_cursor(),
        }
    }

    fn show_cursor(&mut self) -> std::io::Result<()> {
        match self {
            AnyBackend::Crossterm(backend) => backend.show_cursor(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.show_cursor(),
        }
    }

    fn get_cursor_position(&mut self) -> std::io::Result<Position> {
        match self {
            AnyBackend::Crossterm(backend) => backend.get_cursor_position(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.get_cursor_position(),
        }
    }

    fn set_cursor_position<P: Into<Position>>(&mut self, position: P) -> std::io::Result<()> {
        match self {
            AnyBackend::Crossterm(backend) => backend.set_cursor_position(position),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.set_cursor_position(position),
        }
    }

    fn clear(&mut self) -> std::io::Result<()> {
        match self {
            AnyBackend::Crossterm(backend) => backend.clear(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.clear(),
        }
    }

    fn clear_region(&mut self, clear_type: ClearType) -> std::io::Result<()> {
        match self {
            AnyBackend::Crossterm(backend) => backend.clear_region(clear_type),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.clear_region(clear_type),
        }
    }

    fn size(&self) -> std::io::Result<Size> {
        match self {
            AnyBackend::Crossterm(backend) => backend.size(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.size(),
        }
    }

    fn window_size(&mut self) -> std::io::Result<WindowSize> {
        match self {
            AnyBackend::Crossterm(backend) => backend.window_size(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.window_size(),
        }
    }

    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            AnyBackend::Crossterm(backend) => backend.flush(),
            #[cfg(test)]
            AnyBackend::Test(backend) => backend.flush(),
        }
    }
}

static TERMINAL: Mutex<Option<Terminal<AnyBackend>>> = Mutex::new(None);

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

/// A scroll viewport encountered while walking the flat rect list. The
/// viewport origin is stored in draw space (laid-out position minus the
/// scroll accumulated above it) so clip checks compare drawn spans —
/// correct at any nesting depth. `cum_off` is this ancestor's scroll plus
/// all its ancestors', subtracted from laid-out positions to get draw
/// positions.
struct ScrollAncestor {
    depth: usize,
    draw_x: f32,
    draw_y: f32,
    w: f32,
    h: f32,
    cum_off_x: f32,
    cum_off_y: f32,
}

/// Per-rect screen position after scroll offsetting, plus a clip flag.
/// Produced once per frame by `screen_geometry` and shared by painting
/// (`render_frame`) and click dispatch (`hit_test`) so the two can never
/// drift apart.
struct ScreenRect {
    draw_x: f32,
    draw_y: f32,
    clipped: bool,
}

/// Compute per-rect draw coordinates and a scroll-clipped flag, mirroring
/// the scroll-viewport culling walk. A rect is clipped when any ancestor
/// scroll view's drawn viewport no longer contains the rect's drawn span.
fn screen_geometry(rects: &[LayoutEntry]) -> Vec<ScreenRect> {
    let mut scroll_ancestors: Vec<ScrollAncestor> = Vec::new();
    let mut geometry = Vec::with_capacity(rects.len());
    for rect in rects {
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
        let (draw_x, draw_y) = (rect.x - off_x, rect.y - off_y);
        // Both spans in draw space: comparing the rect against an
        // ancestor's laid-out extent is only correct for root-level
        // scroll views — nested windows must account for every level's
        // own offset.
        let clipped = scroll_ancestors.iter().any(|a| {
            draw_x >= a.draw_x + a.w
                || draw_x + rect.w <= a.draw_x
                || draw_y >= a.draw_y + a.h
                || draw_y + rect.h <= a.draw_y
        });
        if rect.kind == NodeKind::ScrollView {
            scroll_ancestors.push(ScrollAncestor {
                depth: rect.depth,
                draw_x,
                draw_y,
                w: rect.w,
                h: rect.h,
                cum_off_x: off_x + rect.style.scroll_x,
                cum_off_y: off_y + rect.style.scroll_y,
            });
        }
        geometry.push(ScreenRect {
            draw_x,
            draw_y,
            clipped,
        });
    }
    geometry
}

/// Hit-test a click at cell (x, y) against the topmost visible rect.
///
/// Returns the rect's index in `rects` (pre-order) plus its kind, or
/// `None` when no unclipped rect contains the point. Reverse walk so the
/// last-drawn (topmost) rect containing the point wins — occlusion
/// semantics, not kind filtering: what a hit means (button activation
/// now, drag targets later) is the surface's policy, keyed by the rect
/// index. Scroll clipping is respected via `screen_geometry`.
pub fn hit_test(rects: &[LayoutEntry], x: f32, y: f32) -> Option<(usize, NodeKind)> {
    let geometry = screen_geometry(rects);
    for (i, rect) in rects.iter().enumerate().rev() {
        let g = &geometry[i];
        if g.clipped {
            continue;
        }
        if g.draw_x <= x && x < g.draw_x + rect.w && g.draw_y <= y && y < g.draw_y + rect.h {
            return Some((i, rect.kind));
        }
    }
    None
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
    let backend = AnyBackend::Crossterm(CrosstermBackend::new(stdout));
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
    match terminal.backend_mut() {
        AnyBackend::Crossterm(backend) => {
            execute!(backend, LeaveAlternateScreen, Show).map_err(|e| e.to_string())?;
        }
        // Nothing to restore for the test backend.
        #[cfg(test)]
        AnyBackend::Test(_) => {}
    }
    disable_raw_mode().map_err(|e| e.to_string())?;
    guard.take();
    Ok(())
}

pub fn get_size() -> Result<(u16, u16), String> {
    size().map_err(|e| e.to_string())
}

/// Small seam for Synchronized Output wiring left testable without
/// touching the buffer path: the trait `SynchronizedUpdate` is on
/// `Write`, so a `Vec<u8>` can stand in for stdout. The prod path
/// passes `std::io::stdout()`. The helper guarantees `End` is emitted
/// even when `paint` fails — crossterm's `sync_update` does this.
fn with_synchronized<W: std::io::Write>(
    writer: &mut W,
    paint: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    writer.sync_update(|_| paint()).map_err(|e| e.to_string())?
}

/// Draw one frame from a layout snapshot into the installed terminal.
/// The terminal-lock half of `render_frame`, split out so tests can paint
/// into an injected `TestBackend` without blocking on event polling.
fn draw_frame(rects: &[LayoutEntry], focused_rect: i32) -> Result<(), String> {
    let mut guard = TERMINAL.lock().map_err(|e| e.to_string())?;
    let terminal = guard.as_mut().ok_or("TUI not initialised")?;

    // Guard negative values (the "no focus" sentinel) before the usize cast.
    let focused_rect = if focused_rect < 0 {
        usize::MAX
    } else {
        focused_rect as usize
    };

    // Synchronized Output (DECSET 2026): the prod terminal paints the
    // frame atomically — no tearing mid-diff. Terminals without support
    // ignore the sequences and render unsynchronized (the safe fallback
    // the vision requires), and the test backend must not emit escape
    // codes to the runner's stdout — so only the crossterm variant syncs.
    // `with_synchronized`/`sync_update` emits End even when the paint
    // fails, so a broken frame can never leave the screen frozen.
    if matches!(terminal.backend_mut(), AnyBackend::Crossterm(_)) {
        with_synchronized(&mut std::io::stdout(), || {
            paint_frame(terminal, rects, focused_rect)
        })
    } else {
        paint_frame(terminal, rects, focused_rect)
    }
}

/// Paint one frame's diff into the terminal buffer and flush it.
fn paint_frame(
    terminal: &mut Terminal<AnyBackend>,
    rects: &[LayoutEntry],
    focused_rect: usize,
) -> Result<(), String> {
    terminal
        .draw(|frame| {
            let area = frame.area();
            frame.render_widget(Clear, area);
            let buf = frame.buffer_mut();
            // Colour strings repeat across rects (theme tokens); parse
            // each distinct value once per frame instead of per rect.
            let mut color_cache: HashMap<&str, Color> = HashMap::new();
            let geometry = screen_geometry(rects);
            for (i, rect) in rects.iter().enumerate() {
                let g = &geometry[i];
                if g.clipped
                    || g.draw_x < 0.0
                    || g.draw_y < 0.0
                    || g.draw_x >= area.right() as f32
                    || g.draw_y >= area.bottom() as f32
                {
                    continue;
                }
                let x = cell(g.draw_x);
                let y = cell(g.draw_y);
                let fg_color = *color_cache
                    .entry(rect.style.fg.as_str())
                    .or_insert_with(|| hex_to_color(&rect.style.fg));
                let bg_color = *color_cache
                    .entry(rect.style.bg.as_str())
                    .or_insert_with(|| hex_to_color(&rect.style.bg));
                let base_style = Style::default().fg(fg_color).bg(bg_color);
                let style = if rect.style.disabled {
                    base_style.dim()
                } else if i == focused_rect {
                    base_style.reversed()
                } else {
                    base_style
                };
                match rect.kind {
                    NodeKind::Text => {
                        buf.set_string(x, y, &rect.text, style);
                    }
                    NodeKind::Button => {
                        draw_button_border(buf, x, y, &rect.text, style);
                    }
                    // Containers paint nothing themselves; their
                    // children are separate rects.
                    NodeKind::Row | NodeKind::Column | NodeKind::Spacer | NodeKind::ScrollView => {}
                }
            }
        })
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Draw one frame from a layout snapshot, then block for the next event.
/// The terminal lock is dropped before event polling so the draw and the
/// wait are independently callable (see `read_event` for wait-only steps).
pub fn render_frame(rects: &[LayoutEntry], focused_rect: i32) -> Result<EventData, String> {
    draw_frame(rects, focused_rect)?;
    read_event()
}

/// Convert a layout coordinate to a buffer cell index — the ONE place
/// f32 becomes u16 at the paint boundary.
///
/// Rounding is half-up (`f32::round`, ties away from zero): 10.5 paints
/// at 11. Chosen over round-ties-even for user intuition; layout stays
/// f32 (taffy-native) and rounding never feeds back into layout, so
/// there is no cumulative drift to avoid — and the choice is pinned by
/// tests either way.
///
/// Saturation is explicit and tested, never relied on from bare `as`:
///   - negative → 0 (paint coords are non-negative; `draw_frame` skips
///     negative draws first, so this is a safety net),
///   - NaN → 0 (must not panic — `f32::clamp` panics on NaN — or wrap),
///   - beyond u16::MAX → u16::MAX (clamp, no wrap).
///
/// Rect spans (w/h) never convert here: clip comparisons stay in f32
/// (`draw_x >= area.right() as f32` — u16→f32 widening is exact) and
/// painting anchors at the rounded origin.
fn cell(v: f32) -> u16 {
    if v.is_nan() {
        return 0;
    }
    v.round().clamp(0.0, f32::from(u16::MAX)) as u16
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
    use crate::layout::NodeStyle;
    use crossterm::event::{KeyEvent, MouseButton, MouseEvent};
    use ratatui::style::Modifier;

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

    fn rect(kind: NodeKind, x: f32, y: f32, w: f32, h: f32, depth: usize) -> LayoutEntry {
        LayoutEntry {
            kind,
            x,
            y,
            w,
            h,
            depth,
            text: String::new(),
            style: NodeStyle::default(),
        }
    }

    #[test]
    fn hit_test_returns_topmost_rect_index_and_kind() {
        let rects = vec![
            rect(NodeKind::Row, 0.0, 0.0, 20.0, 3.0, 0),
            rect(NodeKind::Button, 0.0, 0.0, 8.0, 3.0, 1),
            rect(NodeKind::Button, 9.0, 0.0, 8.0, 3.0, 1),
        ];
        assert_eq!(hit_test(&rects, 4.0, 1.0), Some((1, NodeKind::Button)));
        assert_eq!(hit_test(&rects, 13.0, 1.0), Some((2, NodeKind::Button)));
        assert_eq!(hit_test(&rects, 20.0, 1.0), None);
    }

    #[test]
    fn hit_test_topmost_rect_wins_regardless_of_kind() {
        // A text rect drawn last (topmost) occludes the button beneath
        // it — the hit reports the text rect's index and kind; acting on
        // the kind is the surface's policy.
        let rects = vec![
            rect(NodeKind::Row, 0.0, 0.0, 20.0, 3.0, 0),
            rect(NodeKind::Button, 0.0, 0.0, 8.0, 3.0, 1),
            rect(NodeKind::Text, 0.0, 0.0, 8.0, 3.0, 1),
        ];
        assert_eq!(hit_test(&rects, 4.0, 1.0), Some((2, NodeKind::Text)));
    }

    #[test]
    fn hit_test_reports_disabled_button_rect() {
        // A disabled button still occludes — the rect is reported; the
        // surface's callback map simply has no entry for it.
        let mut button = rect(NodeKind::Button, 0.0, 0.0, 8.0, 3.0, 1);
        button.style.disabled = true;
        let rects = vec![rect(NodeKind::Row, 0.0, 0.0, 20.0, 3.0, 0), button];
        assert_eq!(hit_test(&rects, 4.0, 1.0), Some((1, NodeKind::Button)));
    }

    #[test]
    fn hit_test_accounts_for_scroll_offset() {
        // scroll_view scrolled down by 3: a button laid out at y=3 draws at
        // y=0 and is hit there, but misses at its laid-out coordinates.
        let mut sv = rect(NodeKind::ScrollView, 0.0, 0.0, 10.0, 3.0, 0);
        sv.style.scroll_y = 3.0;
        let button = rect(NodeKind::Button, 0.0, 3.0, 8.0, 3.0, 1);
        let rects = vec![sv, button];
        assert_eq!(hit_test(&rects, 4.0, 1.0), Some((1, NodeKind::Button)));
        assert_eq!(hit_test(&rects, 4.0, 5.0), None);
    }

    #[test]
    fn hit_test_resolves_nested_scroll_cumulative_offset() {
        // scroll_view A (scroll_y=3) contains scroll_view B (scroll_y=2),
        // which contains a button laid out at y=5. The cumulative offset
        // is 3+2=5: the button draws at y=0 and is hit there; a click at
        // its laid-out position misses everything.
        let mut outer = rect(NodeKind::ScrollView, 0.0, 0.0, 10.0, 4.0, 0);
        outer.style.scroll_y = 3.0;
        let mut inner = rect(NodeKind::ScrollView, 0.0, 3.0, 10.0, 3.0, 1);
        inner.style.scroll_y = 2.0;
        let button = rect(NodeKind::Button, 0.0, 5.0, 8.0, 3.0, 2);
        let rects = vec![outer, inner, button];
        assert_eq!(hit_test(&rects, 4.0, 1.0), Some((2, NodeKind::Button)));
        // Beside the button (x=9 past its width) the inner scroll view is
        // hit — also via the cumulative offset (laid out at y=3, drawn
        // at y=0).
        assert_eq!(hit_test(&rects, 9.0, 1.0), Some((1, NodeKind::ScrollView)));
        assert_eq!(hit_test(&rects, 4.0, 5.0), None);
    }

    // ------------------------------------------------------------------
    // Paint path — injected TestBackend, buffer-cell assertions
    // ------------------------------------------------------------------

    /// Serialises tests that touch the process-global TERMINAL static —
    /// cargo runs tests on parallel threads. Poison-tolerant: a panicking
    /// test must not cascade lock failures into the others.
    static TERMINAL_LOCK: Mutex<()> = Mutex::new(());

    fn lock_terminal_serial() -> std::sync::MutexGuard<'static, ()> {
        TERMINAL_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn install_test_terminal(width: u16, height: u16) {
        let backend = AnyBackend::Test(TestBackend::new(width, height));
        let terminal = Terminal::new(backend).expect("test terminal");
        *TERMINAL.lock().unwrap() = Some(terminal);
    }

    fn uninstall_terminal() {
        *TERMINAL.lock().unwrap() = None;
    }

    /// Snapshot the injected TestBackend's buffer. Cloned so the static
    /// lock is released before the assertions run.
    fn test_buffer() -> Buffer {
        let mut guard = TERMINAL.lock().unwrap();
        let terminal = guard.as_mut().expect("test terminal installed");
        match terminal.backend_mut() {
            AnyBackend::Test(backend) => backend.buffer().clone(),
            AnyBackend::Crossterm(_) => unreachable!("tests install a TestBackend"),
        }
    }

    /// A button rect sized the way `create_taffy_node` sizes buttons
    /// (label width + 4), with the label set.
    fn button(label: &str, x: f32, disabled: bool) -> LayoutEntry {
        let w = UnicodeWidthStr::width(label) as f32 + 4.0;
        let mut entry = rect(NodeKind::Button, x, 0.0, w, 3.0, 0);
        entry.text = label.to_string();
        entry.style.disabled = disabled;
        entry
    }

    #[test]
    fn hex_to_color_parses_theme_values() {
        // The exact tokens from sidol/theme.py Colors.
        assert_eq!(hex_to_color("#0A84FF"), Color::Rgb(0x0A, 0x84, 0xFF));
        assert_eq!(hex_to_color("#FF3B30"), Color::Rgb(0xFF, 0x3B, 0x30));
        assert_eq!(hex_to_color("#FFFFFF"), Color::Rgb(0xFF, 0xFF, 0xFF));
        assert_eq!(hex_to_color("#000000"), Color::Rgb(0x00, 0x00, 0x00));
        assert_eq!(hex_to_color("#888888"), Color::Rgb(0x88, 0x88, 0x88));
    }

    #[test]
    fn hex_to_color_rejects_invalid_input() {
        assert_eq!(hex_to_color(""), Color::Reset);
        assert_eq!(hex_to_color("0A84FF"), Color::Reset); // missing #
        assert_eq!(hex_to_color("#0A84"), Color::Reset); // too short
        assert_eq!(hex_to_color("#0A84FF0"), Color::Reset); // too long
        assert_eq!(hex_to_color("#GGGGGG"), Color::Reset); // not hex
    }

    #[test]
    fn draw_frame_requires_initialised_terminal() {
        let _serial = lock_terminal_serial();
        uninstall_terminal();
        let error = draw_frame(&[], -1).unwrap_err();
        assert_eq!(error, "TUI not initialised");
    }

    #[test]
    fn synchronized_update_sequences_are_decset_2026() {
        // The sequences draw_frame emits around the prod paint (crossterm
        // variant only). They go to stdout — not the backend buffer — so
        // buffer-level testing does not apply; this pins the exact bytes
        // crossterm writes, and the wiring (sync around the crossterm
        // variant only) is gated in draw_frame.
        use crossterm::Command;
        use crossterm::terminal::{BeginSynchronizedUpdate, EndSynchronizedUpdate};

        let mut begin = String::new();
        BeginSynchronizedUpdate
            .write_ansi(&mut begin)
            .expect("in-memory write");
        let mut end = String::new();
        EndSynchronizedUpdate
            .write_ansi(&mut end)
            .expect("in-memory write");
        assert_eq!(begin, "\x1B[?2026h");
        assert_eq!(end, "\x1B[?2026l");
    }

    #[test]
    fn with_synchronized_emits_begin_before_paint_and_end_after() {
        // Seam: with_synchronized takes an impl Write for the sync
        // sequences (crossterm's SynchronizedUpdate trait is on Write),
        // testable with a Vec<u8>; prod passes stdout(). The draw path
        // uses the same helper, so this pins Begin before paint and End
        // after without touching the buffer path.
        let mut buf = Vec::new();
        let mut paint_called = false;
        let res = with_synchronized(&mut buf, || {
            paint_called = true;
            Ok::<(), String>(())
        });
        assert!(res.is_ok());
        assert!(paint_called);
        let s = String::from_utf8(buf).expect("utf8");
        assert_eq!(s, "\x1B[?2026h\x1B[?2026l");
    }

    #[test]
    fn with_synchronized_emits_end_even_when_paint_fails() {
        // crossterm's sync_update guarantees End even when the paint
        // closure errs — a broken frame never leaves the screen frozen.
        let mut buf = Vec::new();
        let res = with_synchronized(&mut buf, || Err::<(), String>("boom".to_string()));
        assert_eq!(res.unwrap_err(), "boom");
        let s = String::from_utf8(buf).expect("utf8");
        assert_eq!(s, "\x1B[?2026h\x1B[?2026l");
    }

    #[test]
    fn render_frame_paints_button_border() {
        let _serial = lock_terminal_serial();
        install_test_terminal(12, 6);
        let rects = vec![button("Click", 1.0, false)];
        let result = draw_frame(&rects, -1);
        let buf = test_buffer();
        uninstall_terminal();
        result.expect("draw succeeds");

        // Border glyphs hug the label (5 chars): 7 cells wide, 3 tall,
        // starting at the rect's origin (1, 0).
        assert_eq!(buf[(1, 0)].symbol(), "┌");
        assert_eq!(buf[(2, 0)].symbol(), "─");
        assert_eq!(buf[(6, 0)].symbol(), "─");
        assert_eq!(buf[(7, 0)].symbol(), "┐");
        assert_eq!(buf[(1, 1)].symbol(), "│");
        assert_eq!(buf[(2, 1)].symbol(), "C");
        assert_eq!(buf[(6, 1)].symbol(), "k");
        assert_eq!(buf[(7, 1)].symbol(), "│");
        assert_eq!(buf[(1, 2)].symbol(), "└");
        assert_eq!(buf[(7, 2)].symbol(), "┘");
        // The border is label-sized, not rect-sized (rect is 9 wide):
        // the cell right of the border stays empty.
        assert_eq!(buf[(8, 1)].symbol(), " ");
    }

    #[test]
    fn render_frame_disabled_button_is_dimmed() {
        let _serial = lock_terminal_serial();
        install_test_terminal(16, 4);
        let rects = vec![button("Ok", 0.0, true), button("No", 8.0, false)];
        let result = draw_frame(&rects, -1);
        let buf = test_buffer();
        uninstall_terminal();
        result.expect("draw succeeds");

        let disabled = buf[(0, 1)].style().add_modifier;
        let enabled = buf[(8, 1)].style().add_modifier;
        assert!(disabled.contains(Modifier::DIM));
        assert!(!disabled.contains(Modifier::REVERSED));
        assert!(!enabled.contains(Modifier::DIM));
        assert!(!enabled.contains(Modifier::REVERSED));
    }

    #[test]
    fn render_frame_focused_button_is_reversed() {
        let _serial = lock_terminal_serial();
        install_test_terminal(16, 4);
        let rects = vec![button("Ok", 0.0, false), button("No", 8.0, false)];
        let result = draw_frame(&rects, 1); // focus the second rect
        let buf = test_buffer();
        uninstall_terminal();
        result.expect("draw succeeds");

        let focused = buf[(8, 1)].style().add_modifier;
        let unfocused = buf[(0, 1)].style().add_modifier;
        assert!(focused.contains(Modifier::REVERSED));
        assert!(!focused.contains(Modifier::DIM));
        assert!(!unfocused.contains(Modifier::REVERSED));
    }

    #[test]
    fn render_frame_disabled_wins_over_focus() {
        // A disabled button pointed at by focused_rect: the dim branch
        // takes precedence over the focus reversal.
        let _serial = lock_terminal_serial();
        install_test_terminal(8, 4);
        let rects = vec![button("Ok", 0.0, true)];
        let result = draw_frame(&rects, 0);
        let buf = test_buffer();
        uninstall_terminal();
        result.expect("draw succeeds");

        let style = buf[(0, 1)].style().add_modifier;
        assert!(style.contains(Modifier::DIM));
        assert!(!style.contains(Modifier::REVERSED));
    }

    #[test]
    fn render_frame_clips_content_outside_scroll_viewport() {
        // The P3.3 nested geometry: outer scroll_view (scroll_y=3) around
        // inner scroll_view (scroll_y=2). "IN" is laid out at y=5 and
        // draws at y=0 via the cumulative offset; "OUT" at y=8 would draw
        // at y=3 — one row past the inner viewport — and must not paint.
        let _serial = lock_terminal_serial();
        install_test_terminal(12, 6);
        let mut outer = rect(NodeKind::ScrollView, 0.0, 0.0, 10.0, 4.0, 0);
        outer.style.scroll_y = 3.0;
        let mut inner = rect(NodeKind::ScrollView, 0.0, 3.0, 10.0, 3.0, 1);
        inner.style.scroll_y = 2.0;
        let mut visible = rect(NodeKind::Text, 0.0, 5.0, 2.0, 1.0, 2);
        visible.text = "IN".to_string();
        let mut hidden = rect(NodeKind::Text, 0.0, 8.0, 3.0, 1.0, 2);
        hidden.text = "OUT".to_string();
        let rects = vec![outer, inner, visible, hidden];
        let result = draw_frame(&rects, -1);
        let buf = test_buffer();
        uninstall_terminal();
        result.expect("draw succeeds");

        assert_eq!(buf[(0, 0)].symbol(), "I");
        assert_eq!(buf[(1, 0)].symbol(), "N");
        // Clipped: the cells stay empty.
        assert_eq!(buf[(0, 3)].symbol(), " ");
        assert_eq!(buf[(2, 3)].symbol(), " ");
    }

    #[test]
    fn cell_rounds_half_up() {
        assert_eq!(cell(10.4), 10);
        assert_eq!(cell(10.6), 11);
        // The pinned tie direction: half-up (ties away from zero).
        assert_eq!(cell(10.5), 11);
    }

    #[test]
    fn cell_saturates_edge_cases() {
        assert_eq!(cell(-1.0), 0);
        assert_eq!(cell(-0.6), 0); // rounds to -1, then saturates
        assert_eq!(cell(f32::NAN), 0);
        assert_eq!(cell(f32::NEG_INFINITY), 0);
        assert_eq!(cell(f32::INFINITY), u16::MAX);
        assert_eq!(cell(1.0e9), u16::MAX);
    }

    #[test]
    fn render_frame_paints_fractional_coords_at_rounded_position() {
        // x=10.5 → cell 11 (half-up), y=2.4 → cell 2: the glyph lands at
        // (11, 2) deterministically — never truncated to (10, 2).
        let _serial = lock_terminal_serial();
        install_test_terminal(20, 6);
        let mut glyph = rect(NodeKind::Text, 10.5, 2.4, 1.0, 1.0, 0);
        glyph.text = "X".to_string();
        let result = draw_frame(&[glyph], -1);
        let buf = test_buffer();
        uninstall_terminal();
        result.expect("draw succeeds");

        assert_eq!(buf[(11, 2)].symbol(), "X");
        assert_eq!(buf[(10, 2)].symbol(), " ");
        assert_eq!(buf[(12, 2)].symbol(), " ");
        assert_eq!(buf[(11, 1)].symbol(), " ");
        assert_eq!(buf[(11, 3)].symbol(), " ");
    }
}
