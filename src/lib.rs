//! PyO3 bridge — the ONLY file that knows about PyO3 types.
//!
//! Everything below (graph.rs, eventually layout.rs, render/*.rs) is pure
//! Rust with zero Python dependency. This boundary lets us test the engine
//! with `cargo test` and contains PyO3 version churn to one file.
//!
//! # Mutex
//!
//! The GIL serialises Python calls, so in theory no Mutex is needed. But
//! PyO3 allows Rust to release the GIL (`Python::detach`), and a
//! future Phase-2 render loop might use that. An uncontended Mutex lock is
//! ~5ns — far below the noise floor.
//!
//! A poisoned lock (panic while held) is caught and converted to a Python
//! RuntimeError rather than letting the panic cross the FFI boundary, which
//! is undefined behaviour.
//!
//! # Layout snapshots
//!
//! `compute_layout` returns plain dicts for the headless/test API.
//! `compute_layout_snapshot` returns a `LayoutSnapshot` handle instead —
//! the TUI surface passes it straight back to `tui_render_frame`, so the
//! hot path never marshals per-rect dicts across the FFI twice.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::*;
use std::sync::{Arc, Mutex};

mod graph;
use graph::{Graph as CoreGraph, SignalId};

mod layout;
mod render;

#[pyclass(name = "Graph")]
struct PyGraph {
    inner: Mutex<CoreGraph>,
}

#[pymethods]
impl PyGraph {
    #[new]
    fn new() -> Self {
        Self {
            inner: Mutex::new(CoreGraph::new()),
        }
    }

    fn create_signal(&self) -> PyResult<usize> {
        let mut graph = self.lock()?;
        Ok(graph.create_signal().raw())
    }

    fn add_dependency(&self, source: usize, dependent: usize) -> PyResult<()> {
        let mut graph = self.lock()?;
        ensure_signal(&graph, source)?;
        ensure_signal(&graph, dependent)?;
        graph.add_dependency(SignalId::from_raw(source), SignalId::from_raw(dependent));
        Ok(())
    }

    fn mark_dirty(&self, id: usize) -> PyResult<()> {
        let mut graph = self.lock()?;
        ensure_signal(&graph, id)?;
        graph.mark_dirty(SignalId::from_raw(id));
        Ok(())
    }

    fn dirty_ids(&self) -> PyResult<Vec<usize>> {
        let graph = self.lock()?;
        Ok(graph.dirty_ids().into_iter().map(|id| id.raw()).collect())
    }

    fn clear_dirty(&self) -> PyResult<()> {
        self.lock()?.clear_dirty();
        Ok(())
    }

    /// Called by Component.rendered_view() before re-running view().
    /// Prunes stale conditional subscriptions.
    fn clear_observer(&self, observer: usize) -> PyResult<()> {
        let mut graph = self.lock()?;
        graph.clear_observer(SignalId::from_raw(observer));
        Ok(())
    }

    /// Remove a component signal and its dependency edges.
    fn remove_signal(&self, signal: usize) -> PyResult<()> {
        self.lock()?.remove_signal(SignalId::from_raw(signal));
        Ok(())
    }

    /// Snapshot the dirty set and clear it atomically. This is what
    /// the render-loop flush uses — never call dirty_ids() + clear_dirty()
    /// in sequence from Python; a re-entrant write between the two calls
    /// would drop a dirty signal. drain_dirty() is one FFI call.
    fn drain_dirty(&self) -> PyResult<Vec<usize>> {
        let mut graph = self.lock()?;
        Ok(graph.drain_dirty().into_iter().map(|id| id.raw()).collect())
    }

    /// Test isolation only.
    fn reset(&self) -> PyResult<()> {
        self.lock()?.reset();
        Ok(())
    }
}

fn ensure_signal(graph: &CoreGraph, id: usize) -> PyResult<()> {
    if graph.contains_signal(SignalId::from_raw(id)) {
        Ok(())
    } else {
        Err(PyValueError::new_err(format!("unknown signal ID: {id}")))
    }
}

impl PyGraph {
    /// Acquire the Mutex, converting a poisoned lock into a Python RuntimeError.
    /// Centralised so the error conversion is never accidentally missed.
    fn lock(&self) -> PyResult<std::sync::MutexGuard<'_, CoreGraph>> {
        self.inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("Sidol signal graph lock was poisoned"))
    }
}

/// A computed layout held in Rust. The TUI surface passes this back to
/// `tui_render_frame` directly — no per-rect dict round-trip on the hot
/// path. `to_dicts()` materialises the Python representation on demand
/// (hit-testing, headless inspection, tests).
#[pyclass(name = "LayoutSnapshot")]
struct PyLayoutSnapshot {
    entries: Arc<Vec<layout::LayoutEntry>>,
}

#[pymethods]
impl PyLayoutSnapshot {
    /// Materialise the snapshot as a list of `{kind, x, y, w, h, ...}`
    /// dicts in pre-order traversal.
    fn to_dicts(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        entries_to_dicts(py, &self.entries)
    }

    fn __len__(&self) -> usize {
        self.entries.len()
    }

    /// Hit-test a click at cell (x, y). Returns `(rect_index, kind)` for
    /// the topmost visible rect — the index into the snapshot's rect list
    /// plus the rect's kind — or `None`. The surface maps rect indices to
    /// callbacks; the engine does not filter by kind.
    fn hit_test(&self, x: f32, y: f32) -> Option<(usize, &'static str)> {
        render::hit_test(&self.entries, x, y).map(|(i, kind)| (i, kind.as_str()))
    }
}

fn entries_to_dicts(py: Python<'_>, entries: &[layout::LayoutEntry]) -> PyResult<Py<PyAny>> {
    let results = PyList::empty(py);
    for entry in entries {
        let rect = PyDict::new(py);
        rect.set_item("kind", entry.kind.as_str())?;
        rect.set_item("x", entry.x)?;
        rect.set_item("y", entry.y)?;
        rect.set_item("w", entry.w)?;
        rect.set_item("h", entry.h)?;
        rect.set_item("depth", entry.depth)?;
        rect.set_item("text", &entry.text)?;
        set_style_fields(&rect, &entry.style)?;
        results.append(rect)?;
    }
    Ok(results.into())
}

/// Shared conversion: Python Node tree -> validated layout entries.
fn build_layout_entries(
    root: &Bound<PyAny>,
    viewport_w: f32,
    viewport_h: f32,
) -> PyResult<Vec<layout::LayoutEntry>> {
    let layout_root = py_node_to_layout(root)?;
    layout::compute_layout(&layout_root, viewport_w, viewport_h).map_err(PyRuntimeError::new_err)
}

/// Compute flexbox layout for a Python Node tree.
/// Returns a list of `{kind, x, y, w, h}` dicts in pre-order traversal.
///
/// Conversion from Python types to pure-Rust `LayoutNode` happens here;
/// the actual taffy computation is in `layout::compute_layout` which has
/// zero PyO3 dependency and is testable with `cargo test`.
#[pyfunction]
fn compute_layout(
    py: Python<'_>,
    root: &Bound<PyAny>,
    viewport_w: f32,
    viewport_h: f32,
) -> PyResult<Py<PyAny>> {
    let entries = build_layout_entries(root, viewport_w, viewport_h)?;
    entries_to_dicts(py, &entries)
}

/// Compute layout and keep the result in Rust as a `LayoutSnapshot`.
/// This is the render-loop entry point — the snapshot goes straight back
/// to `tui_render_frame` without dict marshalling.
#[pyfunction]
fn compute_layout_snapshot(
    root: &Bound<PyAny>,
    viewport_w: f32,
    viewport_h: f32,
) -> PyResult<PyLayoutSnapshot> {
    let entries = build_layout_entries(root, viewport_w, viewport_h)?;
    Ok(PyLayoutSnapshot {
        entries: Arc::new(entries),
    })
}

/// Recursively convert a Python Node tree into a pure-Rust LayoutNode.
fn py_node_to_layout(node: &Bound<PyAny>) -> PyResult<layout::LayoutNode> {
    let kind_str: String = node.getattr("kind")?.extract()?;
    let props_any = node.getattr("props")?;
    let props = props_any.cast::<PyDict>()?;
    let children_any = node.getattr("children")?;
    let children_tuple = children_any.cast::<PyTuple>()?;

    let spacing = extract_prop_f32(props, "spacing", 0.0)?;

    // Parsing IS validation: an unknown kind is rejected here at the FFI
    // boundary and cannot exist downstream (LayoutNode carries the enum).
    // Parsed after `spacing` so the error precedence is unchanged.
    let kind: layout::NodeKind = kind_str.parse().map_err(PyValueError::new_err)?;
    let text = match kind {
        layout::NodeKind::Text => extract_prop_str(props, "content", true)?,
        layout::NodeKind::Button => extract_prop_str(props, "label", true)?,
        layout::NodeKind::Row
        | layout::NodeKind::Column
        | layout::NodeKind::Spacer
        | layout::NodeKind::ScrollView => String::new(),
    };
    let style = extract_style(props)?;

    let min_w = extract_opt_f32(props, "min_w")?;
    let min_h = extract_opt_f32(props, "min_h")?;
    let max_w = extract_opt_f32(props, "max_w")?;
    let max_h = extract_opt_f32(props, "max_h")?;
    let padding = extract_prop_f32(props, "padding", 0.0)?;

    let mut children = Vec::with_capacity(children_tuple.len());
    for child in children_tuple.iter() {
        children.push(py_node_to_layout(&child)?);
    }

    Ok(layout::LayoutNode {
        kind,
        spacing,
        min_w,
        min_h,
        max_w,
        max_h,
        padding,
        text,
        style,
        children,
    })
}

fn extract_prop_str(props: &Bound<PyDict>, key: &str, required: bool) -> PyResult<String> {
    match props.get_item(key)? {
        Some(val) => val
            .extract::<String>()
            .map_err(|_| PyValueError::new_err(format!("node property '{key}' must be a string"))),
        None if required => Err(PyValueError::new_err(format!(
            "node is missing required property '{key}'"
        ))),
        None => Ok(String::new()),
    }
}

fn extract_prop_f32(props: &Bound<PyDict>, key: &str, default: f32) -> PyResult<f32> {
    match props.get_item(key)? {
        Some(val) => {
            let value = val.extract::<f32>().map_err(|_| {
                PyValueError::new_err(format!("node property '{key}' must be a number"))
            })?;
            if !value.is_finite() || value < 0.0 {
                return Err(PyValueError::new_err(format!(
                    "node property '{key}' must be finite and non-negative"
                )));
            }
            Ok(value)
        }
        None => Ok(default),
    }
}

fn extract_opt_f32(props: &Bound<PyDict>, key: &str) -> PyResult<Option<f32>> {
    match props.get_item(key)? {
        Some(val) => {
            let value = val.extract::<f32>().map_err(|_| {
                PyValueError::new_err(format!("node property '{key}' must be a number"))
            })?;
            if !value.is_finite() || value < 0.0 {
                return Err(PyValueError::new_err(format!(
                    "node property '{key}' must be finite and non-negative"
                )));
            }
            Ok(Some(value))
        }
        None => Ok(None),
    }
}

fn extract_prop_bool(props: &Bound<PyDict>, key: &str, default: bool) -> PyResult<bool> {
    match props.get_item(key)? {
        Some(val) => val
            .extract::<bool>()
            .map_err(|_| PyValueError::new_err(format!("node property '{key}' must be a boolean"))),
        None => Ok(default),
    }
}

/// The style schema — the single per-property listing for `NodeStyle`.
/// `extract_style` (props dict → struct) and `set_style_fields` (struct →
/// rect dict) are both generated from this one table, so a new style
/// property is one row here plus its field in `NodeStyle` (layout.rs).
/// Forgetting the row is a missing-field compile error in `extract_style`,
/// never a silently dropped dict key.
macro_rules! node_style_schema {
    ( $( $field:ident : $key:literal => $extract:ident($($arg:expr),*) ),* $(,)? ) => {
        /// Extract the style payload from a Python props dict. Uses the
        /// same `extract_prop_*` helpers (and therefore the same error
        /// messages and defaults) as the per-property extraction it replaced.
        fn extract_style(props: &Bound<PyDict>) -> PyResult<layout::NodeStyle> {
            Ok(layout::NodeStyle {
                $( $field: $extract(props, $key, $($arg),*)?, )*
            })
        }

        /// Materialise the style payload into a Python rect dict — the
        /// keys are the wire names Python reads (`to_dicts`, dev server).
        fn set_style_fields(rect: &Bound<PyDict>, style: &layout::NodeStyle) -> PyResult<()> {
            $( rect.set_item($key, style.$field.clone())?; )*
            Ok(())
        }
    };
}

node_style_schema! {
    fg: "fg" => extract_prop_str(false),
    bg: "bg" => extract_prop_str(false),
    variant: "variant" => extract_prop_str(false),
    disabled: "disabled" => extract_prop_bool(false),
    radius: "radius" => extract_prop_f32(0.0),
    scroll_x: "scroll_x" => extract_prop_f32(0.0),
    scroll_y: "scroll_y" => extract_prop_f32(0.0),
}

/// Convert one engine event to the Python dict protocol:
///   {"type": "tick"} / {"type": "resize"}
///   {"type": "key", "key": str, "ctrl": b, "alt": b, "shift": b}
///   {"type": "click", "x": int, "y": int}
fn event_to_py(py: Python<'_>, event: &render::EventData) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    match event {
        render::EventData::Tick => d.set_item("type", "tick")?,
        render::EventData::Resize => d.set_item("type", "resize")?,
        render::EventData::Key {
            key,
            ctrl,
            alt,
            shift,
        } => {
            d.set_item("type", "key")?;
            d.set_item("key", key)?;
            d.set_item("ctrl", ctrl)?;
            d.set_item("alt", alt)?;
            d.set_item("shift", shift)?;
        }
        render::EventData::Click { x, y } => {
            d.set_item("type", "click")?;
            d.set_item("x", x)?;
            d.set_item("y", y)?;
        }
    }
    Ok(d.into())
}

#[pyfunction]
fn tui_init() -> PyResult<()> {
    render::init().map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn tui_cleanup() -> PyResult<()> {
    render::cleanup().map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn tui_size() -> PyResult<(u16, u16)> {
    render::get_size().map_err(PyRuntimeError::new_err)
}

/// Draw one frame from a layout snapshot, then block for the next event.
/// The GIL is released for the whole call so worker threads keep running
/// while the surface waits for input.
#[pyfunction]
fn tui_render_frame(
    py: Python<'_>,
    snapshot: PyRef<'_, PyLayoutSnapshot>,
    focused_rect: i32,
) -> PyResult<Py<PyAny>> {
    let entries = Arc::clone(&snapshot.entries);
    let event = py
        .detach(move || render::render_frame(&entries, focused_rect))
        .map_err(PyRuntimeError::new_err)?;
    event_to_py(py, &event)
}

/// Block for the next event WITHOUT drawing. Used by the surface when
/// nothing is dirty — the dirty graph gates the rebuild, so idle frames
/// skip tree resolution, layout, and painting entirely.
#[pyfunction]
fn tui_wait_event(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let event = py
        .detach(render::read_event)
        .map_err(PyRuntimeError::new_err)?;
    event_to_py(py, &event)
}

#[pymodule]
fn _sidol_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGraph>()?;
    m.add_class::<PyLayoutSnapshot>()?;
    m.add_function(wrap_pyfunction!(compute_layout, m)?)?;
    m.add_function(wrap_pyfunction!(compute_layout_snapshot, m)?)?;
    m.add_function(wrap_pyfunction!(tui_init, m)?)?;
    m.add_function(wrap_pyfunction!(tui_cleanup, m)?)?;
    m.add_function(wrap_pyfunction!(tui_size, m)?)?;
    m.add_function(wrap_pyfunction!(tui_render_frame, m)?)?;
    m.add_function(wrap_pyfunction!(tui_wait_event, m)?)?;
    Ok(())
}
