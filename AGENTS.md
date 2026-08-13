# AGENTS.md

## Purpose

Sidol is a reactive Python UI framework backed by a Rust engine (PyO3).
The problem it solves: Python apps get UI frameworks, but the good ones
drag in a browser (Flet, NiceGUI) or a C++ toolchain (PyQt). Sidol gives
Python developers a declarative, reactive UI with no browser and no C++
toolchain — the engine is Rust, compiled once into a single `.pyd`.

## Core design, do not undermine

- Python owns signal values; Rust owns the dependency graph topology.
- Reactivity is automatic: reading a `State` field inside `view()`
  registers the dependency. No manual wiring, no component diffing.
- Dirty propagation and layout run in Rust, but the current PyO3 entry points
  remain GIL-bound. Keep the hot loop out of Python and measure before
  introducing GIL release.
- Signal IDs cross FFI as plain integers; layout snapshots currently cross as
  validated Python dictionaries for the surface boundary.
- Python is the only interface. No DSL, no markup language.
- Phase 1: TUI surface. Phase 2: GPU surface (wgpu). Terminal-first.

## Goals (always advance these)

1. Keep Python the only surface — ergonomic, declarative, reactive.
2. Keep the reactive hot path (graph, layout, render) in Rust.
3. Grow the widget set without breaking the auto-tracking model.
4. Headless testability: `build_tree()` + `compute_layout()` without a screen.

## Non-goals (do not add these without asking)

- A DSL, markup language, or non-Python authoring surface.
- An embedded browser / webview renderer.
- A C++ toolchain or native widgets (Qt, etc.).
- Moving layout, graph, or dirty propagation into Python.
- Coarse component diffing instead of the signal-graph model.

## Build

```bash
uv sync
uv run maturin develop   # compile Rust ext into .venv — required before any Python import works
```

## Test

```bash
uv run pytest
cargo test        # Rust tests, no Python needed
```

## Lint

```bash
uv run ruff check sidol/ tests/
cargo clippy
```

## Benchmark

```bash
uv run maturin develop --release
uv run python bench/bench_hotpath.py
```

## Structure

- `sidol/` — Python framework: `State` descriptor, `Component`, `App`,
  widgets, TUI surface, HTML preview, and CLI.
- `src/` — Rust engine: `graph.rs` (reactive signals), `layout.rs`
  (Taffy flexbox), `render/` (ratatui/crossterm). PyO3 cdylib `_sidol_core`.
- `tests/` — Python tests.

## Notes

- Pre-alpha 0.1.0. API can change between MINOR versions.
- Status is deliberate: `build` bundling, GPU, and mouse-wheel gestures
  remain on the roadmap. Native `sidol dev` hot-reload, focused `ScrollView`
  scrolling, explicit and auto-keyed list children (`List(key=...)`),
  `Worker`/`run_async` concurrency, and the programmatic design system
  (`Theme`/`Style`/`resolve_style`) exist now.
