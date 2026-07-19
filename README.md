<div align="center">

# sidol

**Python-native reactive UI. Rust engine. No browser, no C++ toolchain.**

A reactive Python UI framework backed by a Rust core.

[![GitHub](https://img.shields.io/badge/github-mitayan0/sidol-8A2BE2)](https://github.com/mitayan0/sidol)
[![Status](https://img.shields.io/badge/status-pre--alpha-yellow)]()

</div>

---

Python writes state. Rust runs the engine — reactive signal graph, layout, and rendering outside the GIL, with no browser runtime and no C++ toolchain.

- **Auto-tracked reactive state** — reading a `State` field inside `view()` registers the dependency; no manual wiring required
- **`State` descriptor + observer stack** — fine-grained reactivity (like SolidJS), not coarse component diffing
- **Rust core via PyO3** — dirty propagation never touches the GIL; zero serialization overhead between Python and Rust
- **Phased render surfaces** — TUI (`ratatui`) first, GPU (`wgpu`) second, same reactive core throughout

---

## How sidol compares

**PyQt / PySide**

- ✅ Similar: declarative widgets, event-driven, cross-platform.
- 🔁 No C++ toolchain to install or distribute. The engine is pure Rust, compiled once into a single `.pyd`.
- 🔁 The render loop runs outside the GIL — heavy Python logic won't freeze your UI.

**Flet / NiceGUI**

- ✅ Similar: Python-first API, reactive state, cross-platform.
- 🔁 No embedded browser. No Chromium, no WebSocket bridge, no process-per-window overhead.
- 🔁 Sidol renders to a terminal (Phase 1) or GPU surface (Phase 2), not a webview.

**Slint**

- ✅ Same architectural neighborhood: Rust core, GPU + software renderers, multi-language bindings.
- 🔁 Python is sidol's primary — and only — interface. There is no DSL. Everything is Python.
- 🔁 sidol starts with a TUI surface, not a GPU surface. Terminal-first, GPU later.

---

## Current state

```python
from sidol import App, Component, State, Text, Column, Button

class Counter(Component):
    count = State()

    def __init__(self):
        super().__init__()
        self.count = 0

    def view(self):
        return Column(
            Text(f"Count: {self.count}"),
            Button("+1", on_click=lambda: setattr(self, "count", self.count + 1)),
        )

app = App(Counter())
tree = app.build_tree()   # ✅ declarative Node tree
# app.run()               # ❌ NotImplementedError — no render surface yet
```

## What works today

- [x] Reactive signal graph (Rust) — create signals, declare dependencies, propagate dirty
- [x] Auto-tracking — reading `State` during `view()` registers the dependency automatically
- [x] Stale-edge pruning — conditional dependencies are cleaned up before every re-render
- [x] PyO3 bridge — Python calls into Rust; GIL is never held in the engine hot path
- [x] Test isolation — `reset_graph()` for clean per-test state

## What's next

| Phase | Surface | Status |
|-------|---------|--------|
| 0 | Headless box-tree dump | 🔜 Next |
| 1 | TUI (`ratatui`) | ⬜ |
| 2 | GPU (`wgpu` + `tiny-skia`) | ⬜ |

---

## Installation

From source (the only option while pre-alpha):

```bash
git clone https://github.com/mitayan0/sidol.git
cd sidol

uv sync                  # create .venv and install dev dependencies
uv run maturin develop   # compile Rust extension into .venv
uv run pytest            # run Python tests
cargo test               # run Rust unit tests (no Python required)
```

Requires: Rust 1.85+, [uv](https://docs.astral.sh/uv/), Python 3.12+.

---

## Contributing

Bug fixes with a regression test are always welcome.

For new features, open an issue first — the build order is deliberate and jumping phases creates debt. For bugs, a focused PR with a failing test that your fix makes pass is the ideal contribution.

**What gets merged:**
- Regression tests for confirmed bugs
- Small, focused PRs (large diffs will be asked to split)
- Refactors with a clear readability win, not just churn

**What won't be reviewed:**
- Features that skip ahead of the current phase
- Whitespace-only or formatting-only changes
- PRs that rewrite docs without having contributed code first
