# sidol

**The Python GUI framework that stays fast — styled in Python, powered by Rust.**

Build declarative, reactive UIs with a simple Python API and style them in
Python — no CSS, no DSL. The Rust engine owns the signal graph, layout, and
rendering, so your apps stay light and fast. `sidol dev` runs your app
natively and hot-reloads as you edit.

## Quick start

```python
from sidol import App, Component, State
from sidol.widgets import Button, Column, Row, Text

class Counter(Component):
    count = State()

    def __init__(self):
        super().__init__()
        self.count = 0

    def increment(self):
        self.count += 1

    def view(self):
        return Column(
            Text(f"Count: {self.count}"),
            Row(
                Button("-", on_click=lambda: setattr(self, "count", self.count - 1)),
                Button("+", on_click=self.increment),
            ),
        )

app = App(Counter())
```

Run it natively with hot-reload:

```bash
uv run sidol dev counter.py     # edit, save, and the running UI updates
```

Or headless, without a screen:

```python
app.build_tree()               # resolve the declarative tree
app.compute_layout(400, 300)   # computed positions
app.flush()                    # re-render after state changes
```

## Why sidol

- **Declarative and reactive.** Reading a `State` field inside `view()`
  registers the dependency automatically. No manual wiring, no component
  diffing.
- **Styled in Python.** Styling is a programmatic design system
  (`Theme`, `Style`, `resolve_style`) — more flexibility than CSS, without
  ever leaving Python.
- **Fast by architecture.** Rust owns the reactive graph, flexbox layout
  (Taffy), and rendering. Layout of a 500-widget tree runs in ~12 ms.
- **A real dev loop.** `sidol dev` launches your app natively and
  hot-reloads on save.
- **Headless by design.** `build_tree()` and `compute_layout()` run without a
  screen, so you can test your UI like any other Python code.

## How it works

Python holds signal values; Rust owns the dependency graph. Reading a `State`
field inside `view()` records an edge automatically, and stale conditional
subscriptions are pruned on every re-render. Rust drives graph propagation
and layout; the current PyO3 entry points remain GIL-bound while the hot loop
stays in Rust.

The current surface is a keyboard-driven TUI (ratatui/crossterm), with mouse
clicks, focus navigation, and scrollable containers. A GPU surface (wgpu) is
planned.

## Install

Requires Python 3.12+ and Rust 1.85+.

```bash
uv sync                  # .venv + dev dependencies
uv run maturin develop   # compile Rust extension into .venv
```

## Test

```bash
uv run pytest
uv run ruff check sidol tests
cargo test        # Rust tests, no Python needed
```

## Status: pre-alpha

Working:
- Reactive signal graph: auto-tracking, dirty propagation, stale-edge pruning
- Taffy flexbox layout (`Row`, `Column`, `Spacer`) with constraints and padding
- Widgets: `Text`, `Button`, `TextField`, `List`, `Dropdown`, `Slider`
- `ScrollView` with keyboard scrolling and viewport clipping
- TUI event loop with focus navigation, mouse clicks, and disabled controls
- `sidol dev` native launcher with hot-reload (`--no-watch` to disable)
- Stable child identity via `Component.remember()` and `Component.keyed()`
- `List` with auto-keyed reconciliation (`key=` keeps item state across reorders)
- Programmatic design system: `Theme`, `Style`, `resolve_style()`
- `Worker` background tasks
- Headless mode for tests without a screen

Not yet:
- `sidol build` bundling (the command is an explicit stub)
- GPU surface (wgpu)
- Async coroutine helpers
- Mouse-wheel gestures (arrow-key scrolling is available)

HTML export and `DevServer` remain explicit development APIs, not the default
`sidol dev` surface.

## License

MIT
