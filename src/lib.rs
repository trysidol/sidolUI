//! PyO3 bridge — the ONLY file that knows about PyO3 types.
//!
//! Everything below (graph.rs, eventually layout.rs, render/*.rs) is pure
//! Rust with zero Python dependency. This boundary lets us test the engine
//! with `cargo test` and contains PyO3 version churn to one file.
//!
//! # Mutex
//!
//! The GIL serialises Python calls, so in theory no Mutex is needed. But
//! PyO3 allows Rust to release the GIL (`Python::allow_threads`), and a
//! future Phase-2 render loop might use that. An uncontended Mutex lock is
//! ~5ns — far below the noise floor.
//!
//! A poisoned lock (panic while held) is caught and converted to a Python
//! RuntimeError rather than letting the panic cross the FFI boundary, which
//! is undefined behaviour.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::*;
use std::sync::Mutex;

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
    let layout_root = py_node_to_layout(root)?;
    let entries = layout::compute_layout(&layout_root, viewport_w, viewport_h)
        .map_err(PyRuntimeError::new_err)?;
    let results = PyList::empty(py);
    for entry in &entries {
        let rect = PyDict::new(py);
        rect.set_item("kind", &entry.kind)?;
        rect.set_item("x", entry.x)?;
        rect.set_item("y", entry.y)?;
        rect.set_item("w", entry.w)?;
        rect.set_item("h", entry.h)?;
        rect.set_item("depth", entry.depth)?;
        rect.set_item("text", &entry.text)?;
        rect.set_item("fg", &entry.fg)?;
        rect.set_item("bg", &entry.bg)?;
        rect.set_item("variant", &entry.variant)?;
        rect.set_item("disabled", entry.disabled)?;
        rect.set_item("radius", entry.radius)?;
        rect.set_item("scroll_x", entry.scroll_x)?;
        rect.set_item("scroll_y", entry.scroll_y)?;
        results.append(rect)?;
    }
    Ok(results.into())
}

/// Recursively convert a Python Node tree into a pure-Rust LayoutNode.
fn py_node_to_layout(node: &Bound<PyAny>) -> PyResult<layout::LayoutNode> {
    let kind: String = node.getattr("kind")?.extract()?;
    let props_any = node.getattr("props")?;
    let props = props_any.cast::<PyDict>()?;
    let children_any = node.getattr("children")?;
    let children_tuple = children_any.cast::<PyTuple>()?;

    let spacing = extract_prop_f32(props, "spacing", 0.0)?;

    let text = match kind.as_str() {
        "text" => extract_prop_str(props, "content", true)?,
        "button" => extract_prop_str(props, "label", true)?,
        "row" | "column" | "spacer" | "scroll_view" => String::new(),
        _ => {
            return Err(PyValueError::new_err(format!(
                "unsupported node kind: {kind}"
            )));
        }
    };
    let fg = extract_prop_str(props, "fg", false)?;
    let bg = extract_prop_str(props, "bg", false)?;
    let variant = extract_prop_str(props, "variant", false)?;
    let disabled = extract_prop_bool(props, "disabled", false)?;

    let min_w = extract_opt_f32(props, "min_w")?;
    let min_h = extract_opt_f32(props, "min_h")?;
    let max_w = extract_opt_f32(props, "max_w")?;
    let max_h = extract_opt_f32(props, "max_h")?;
    let padding = extract_prop_f32(props, "padding", 0.0)?;
    let radius = extract_prop_f32(props, "radius", 0.0)?;
    let scroll_x = extract_prop_f32(props, "scroll_x", 0.0)?;
    let scroll_y = extract_prop_f32(props, "scroll_y", 0.0)?;

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
        fg,
        bg,
        variant,
        disabled,
        radius,
        scroll_x,
        scroll_y,
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

#[pyfunction]
fn tui_render_frame(py: Python<'_>, rects: &Bound<PyAny>, focused_idx: i32) -> PyResult<String> {
    let layout_rects = parse_rects(rects)?;
    py.detach(|| render::render_frame(&layout_rects, focused_idx))
        .map_err(PyRuntimeError::new_err)
}

/// Parse a Python list of layout dicts into a Vec<render::LayoutRect>.
fn parse_rects(rects: &Bound<PyAny>) -> PyResult<Vec<render::LayoutRect>> {
    let list = rects.cast::<PyList>()?;
    let mut result = Vec::with_capacity(list.len());
    for item in list.iter() {
        let d = item.cast::<PyDict>()?;
        let kind = required_rect_str(d, "kind")?;
        let x = required_rect_f32(d, "x")?;
        let y = required_rect_f32(d, "y")?;
        let w = required_rect_f32(d, "w")?;
        let h = required_rect_f32(d, "h")?;
        let depth = optional_rect_usize(d, "depth")?;
        let text = optional_rect_str(d, "text")?;
        let fg = optional_rect_str(d, "fg")?;
        let bg = optional_rect_str(d, "bg")?;
        let disabled = optional_rect_bool(d, "disabled")?;
        let scroll_x = optional_rect_f32(d, "scroll_x")?;
        let scroll_y = optional_rect_f32(d, "scroll_y")?;
        result.push(render::LayoutRect {
            kind,
            x,
            y,
            w,
            h,
            depth,
            text,
            fg,
            bg,
            disabled,
            scroll_x,
            scroll_y,
        });
    }
    Ok(result)
}

fn required_rect_str(dict: &Bound<PyDict>, key: &str) -> PyResult<String> {
    dict.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("layout rect is missing '{key}'")))?
        .extract::<String>()
        .map_err(|_| PyValueError::new_err(format!("layout rect '{key}' must be a string")))
}

fn optional_rect_str(dict: &Bound<PyDict>, key: &str) -> PyResult<String> {
    match dict.get_item(key)? {
        Some(value) => value
            .extract::<String>()
            .map_err(|_| PyValueError::new_err(format!("layout rect '{key}' must be a string"))),
        None => Ok(String::new()),
    }
}

fn required_rect_f32(dict: &Bound<PyDict>, key: &str) -> PyResult<f32> {
    let value = dict
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("layout rect is missing '{key}'")))?
        .extract::<f32>()
        .map_err(|_| PyValueError::new_err(format!("layout rect '{key}' must be a number")))?;
    if !value.is_finite() || value < 0.0 {
        return Err(PyValueError::new_err(format!(
            "layout rect '{key}' must be finite and non-negative"
        )));
    }
    Ok(value)
}

fn optional_rect_f32(dict: &Bound<PyDict>, key: &str) -> PyResult<f32> {
    match dict.get_item(key)? {
        Some(value) => {
            let parsed = value.extract::<f32>().map_err(|_| {
                PyValueError::new_err(format!("layout rect '{key}' must be a number"))
            })?;
            if !parsed.is_finite() || parsed < 0.0 {
                return Err(PyValueError::new_err(format!(
                    "layout rect '{key}' must be finite and non-negative"
                )));
            }
            Ok(parsed)
        }
        None => Ok(0.0),
    }
}

fn optional_rect_bool(dict: &Bound<PyDict>, key: &str) -> PyResult<bool> {
    match dict.get_item(key)? {
        Some(value) => value
            .extract::<bool>()
            .map_err(|_| PyValueError::new_err(format!("layout rect '{key}' must be a boolean"))),
        None => Ok(false),
    }
}

fn optional_rect_usize(dict: &Bound<PyDict>, key: &str) -> PyResult<usize> {
    match dict.get_item(key)? {
        Some(value) => value
            .extract::<usize>()
            .map_err(|_| PyValueError::new_err(format!("layout rect '{key}' must be an integer"))),
        None => Ok(0),
    }
}

#[pymodule]
fn _sidol_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGraph>()?;
    m.add_function(wrap_pyfunction!(compute_layout, m)?)?;
    m.add_function(wrap_pyfunction!(tui_init, m)?)?;
    m.add_function(wrap_pyfunction!(tui_cleanup, m)?)?;
    m.add_function(wrap_pyfunction!(tui_size, m)?)?;
    m.add_function(wrap_pyfunction!(tui_render_frame, m)?)?;
    Ok(())
}
