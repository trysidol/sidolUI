# Contributing to Sidol

Build and test first (see AGENTS.md for the full contract):

```bash
uv sync
uv run maturin develop   # required before any Python import works
uv run pytest            # Python suite
cargo test               # Rust engine, no Python needed
```

Architecture in one line: Python owns signal values and the declarative
`Node` tree; Rust owns the dependency graph, flexbox layout, and painting.
Python is the only interface — no DSL, no markup (AGENTS.md).

## Recipe 1: Add a widget

Two paths. Pick based on what the widget *is*.

### Composed widget (most widgets — no Rust)

A `Component` subclass whose `view()` composes existing primitives.
`Slider` (sidol/widgets/slider.py) and `Dropdown` (sidol/widgets/dropdown.py)
required exactly:

1. The component file: `class Slider(Component)` with `State` fields,
   keyboard handlers returned as `on_key`, focus via `on_focus`.
2. One import + `__all__` entry in `sidol/widgets/__init__.py`.
3. Tests in `tests/test_widgets.py` using the factories in
   `tests/_helpers.py` (`_tui_step`, `_key_event`, `_click_event`).

Zero Rust. If the widget holds per-item state inside a list, give the
node a stable identity with `Component.keyed(key)`. A Component instance
occupies exactly one position in the tree — embedding the same instance
under two parents raises on build; create separate instances instead.

### New primitive kind (rare — a new `kind` string the engine handles)

Precedents: `button`, `scroll_view`. The compiler walks you through the
Rust side — every dispatch site is an exhaustive `match`, so adding the
`NodeKind` variant without handling it fails the build:

1. Python factory returning `Node(kind="<wire_name>", props={...})`
   (sidol/widgets/ — `input.py` and `layout.py` are the models) plus the
   export in `sidol/widgets/__init__.py`.
2. `NodeKind` variant + arms in `FromStr` and `as_str` (src/layout.rs).
   The wire string is the props-dict `kind` value — case-exact, must
   round-trip.
3. Taffy arm in `create_taffy_node` (src/layout.rs): sizing, flex, gap,
   overflow.
4. Paint arm in `render_frame` (src/render/mod.rs). Containers join the
   explicit no-op arm; leaves draw into the ratatui `Buffer`.
5. If interactive: `hit_test` (src/render/mod.rs) and the surface's
   focus/click collection (`_is_focusable`, `_button_callback_map` in
   sidol/surfaces/tui.py).
6. If it needs HTML export: an arm in `_nest_by_depth`
   (sidol/surfaces/html.py).
7. Tests: layout rects in `tests/test_layout_ffi.py`, behavior in
   `tests/test_widgets.py`, key/click dispatch in
   `tests/test_surface_dispatch.py`, Rust units next to the code
   (src/layout.rs, src/render/mod.rs).

Worked example — `scroll_view`: the `ScrollView` component
(sidol/widgets/scroll.py) emits `Node(kind="scroll_view", props={"scroll_x",
"scroll_y", ...})` from `view()`; `NodeKind::ScrollView` + parse/round-trip
arms; the taffy arm sets `Overflow::Scroll` on both axes; paint is the
container no-op arm; scroll-viewport culling lives in `screen_geometry` and
`hit_test` (src/render/mod.rs). Tests pin container layout
(test_layout_ffi.py), keyboard scrolling (test_widgets.py), and
click-through-scroll (test_surface_dispatch.py, plus
`hit_test_accounts_for_scroll_offset` in src/render/mod.rs).

## Recipe 2: Add a style property

The style payload lives in one struct and one schema table:

1. One field in `NodeStyle` (src/layout.rs).
2. One row in the `node_style_schema!` table (src/lib.rs), e.g.
   `radius: "radius" => extract_prop_f32(0.0),`.
3. One Python site: the widget factory adds it to `props`.

That covers all three structs (`LayoutNode`, `InternalEntry`,
`LayoutEntry`) and both conversions — extraction and dict materialisation
are generated from the table. Compiler enforcement: forgetting the schema
row is a missing-field compile error in `extract_style`, so a dropped dict
key can no longer happen silently. Extraction reuses the `extract_prop_*`
helpers, so type checking and finite/non-negative validation come with the
existing error messages. Acceptance proof (P2.2 in
PLAN_10_OUT_OF_10.md): `border_style` end-to-end in 2 Rust sites + 1
Python site. Paint/HTML consumers are extra sites only if the property
changes rendering.

## Recipe 3: Surface contract

What a second surface must implement. The TUI surface
(sidol/surfaces/tui.py) is the reference.

**Event dicts.** The engine is policy-free; `event_to_py` (src/lib.rs)
emits exactly:

- `{"type": "tick"}` — poll window elapsed with no input
- `{"type": "resize"}`
- `{"type": "key", "key": str, "ctrl": bool, "alt": bool, "shift": bool}`
- `{"type": "click", "x": int, "y": int}`

Quit keys, Tab order, and activation are surface policy
(tui.py `_dispatch_key`), not engine policy — hardcoding them in the
engine once made typing into a TextField impossible.

**Layout protocol.** Two entry points, one rule each:

- Dict API: `_sidol_core.compute_layout`, wrapped by `App.compute_layout`,
  returns validated rect dicts — for headless tests, HTML export
  (sidol/surfaces/html.py), and the dev server (sidol/dev_server.py).
- `compute_layout_snapshot` returns a `LayoutSnapshot` handle — the hot
  path passes it straight to `tui_render_frame` and `snapshot.hit_test`
  (which returns `(rect_index, kind)` for the topmost visible rect) with
  no per-rect marshalling. `to_dicts()` materialises on demand.

**Frame path (post-P1.1).** Per frame: drain the dirty set
(`_graph.drain_dirty()`), call `App.build_tree(dirty)` — dirty components
re-render, clean subtrees splice from cache — then snapshot and draw.
Full rebuild (`dirty=None`) only on the first frame, on resize, and on
hot-reload swap. Idle frames block in `tui_wait_event`: no resolve, no
layout, no paint.

**Dispose-on-swap.** On hot-reload swap and on exit the surface calls
`app.dispose()` then `cancel_all_workers()` — the swap branch and the
`finally` block in tui.py `run()`; sidol/dev_server.py does the same on
its shutdown paths. Never rely on `__del__` (lifecycle contract in
AGENTS.md).

## Test map

tests/ is split by subsystem: `test_graph.py` (reactive core, flush,
tree resolution), `test_layout_ffi.py` (taffy rects, FFI errors),
`test_surface_dispatch.py` (key/mouse/focus, run loop, hot-reload),
`test_lifecycle.py` (dispose, keyed eviction), `test_concurrency.py`
(Worker/run_async), `test_dev_server.py`, `test_widgets.py`,
`test_example_app.py`. Shared factories live in `tests/_helpers.py`;
`conftest.py` resets the global graph around every test.

Roadmap context: PLAN_10_OUT_OF_10.md. Product direction:
SIDOL_VISION.md.
