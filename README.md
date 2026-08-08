# sidol

Reactive Python UI framework with a Rust engine (PyO3). No browser, no C++ toolchain.

## Requirements

- Python 3.12+
- Rust 1.85+
- [uv](https://docs.astral.sh/uv/)

## Install

```bash
git clone https://github.com/mitayan0/sidol.git
cd sidol

uv sync                  # .venv + dev dependencies
uv run maturin develop   # compile Rust extension into .venv
```

## Test

```bash
uv run pytest
uv run ruff check sidol tests
cargo test        # Rust tests, no Python needed
```

## Quick start

```python
from sidol import App, Component, State, Text, Column

class Counter(Component):
    count = State()

    def __init__(self):
        super().__init__()
        self.count = 0

    def view(self):
        return Column(
            Text(f"Count: {self.count}"),
        )

app = App(Counter())
app.build_tree()               # Node tree
app.compute_layout(400, 300)   # computed positions
app.flush()                    # re-render after state changes
# app.run()                    # TUI event loop (quits on 'q')
```

## How it works

Python holds signal values, Rust holds the dependency graph. Reading a `State` field inside `view()` registers a dependency automatically — no manual wiring, no diffing. Rust owns graph propagation and layout; Python currently remains involved at the FFI boundary and the graph calls are GIL-bound.

Current surface is a keyboard-driven TUI (ratatui/crossterm). A GPU surface is planned, not started.

## Status: pre-alpha

Working:
- Reactive signal graph: auto-tracking, dirty propagation, stale-edge pruning
- Taffy flexbox layout (`Row`, `Column`, `Spacer`)
- Widgets: `Text`, `Button`, `TextField`
- Headless mode: `build_tree()` + `compute_layout()` for tests without a screen
- TUI event loop with focus navigation
- Mouse click dispatch for buttons
- `sidol dev` native application launcher
- `Worker` background tasks
- `List`, `Dropdown`, and `Slider` widgets
- `ScrollView` with keyboard scrolling and viewport clipping
- Stable child identity via `Component.remember()` and `Component.keyed()`

Not yet:
- `sidol build` bundling (the command is an explicit stub)
- Async coroutine helpers
- GPU surface (wgpu)
- Mouse-wheel gestures (arrow-key scrolling is available)
- Automatic keyed list diffing beyond explicit `Component.keyed()` children

HTML export and `DevServer` remain explicit development APIs, not the default
`sidol dev` surface.

## License

MIT
