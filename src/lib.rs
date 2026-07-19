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

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::sync::Mutex;

mod graph;
use graph::{Graph as CoreGraph, SignalId};

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
        graph.add_dependency(SignalId::from_raw(source), SignalId::from_raw(dependent));
        Ok(())
    }

    fn mark_dirty(&self, id: usize) -> PyResult<()> {
        let mut graph = self.lock()?;
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

impl PyGraph {
    /// Acquire the Mutex, converting a poisoned lock into a Python RuntimeError.
    /// Centralised so the error conversion is never accidentally missed.
    fn lock(&self) -> PyResult<std::sync::MutexGuard<'_, CoreGraph>> {
        self.inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("Sidol signal graph lock was poisoned"))
    }
}

#[pymodule]
fn _sidol_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGraph>()?;
    Ok(())
}
