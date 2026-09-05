"""TUI surface: key/mouse dispatch, focus, the run loop, hot-reload,
render-failure logging, and the event protocol."""

from __future__ import annotations

import pytest
from _helpers import _click_event, _collect_text, _key_event, _tui_step

from sidol.app import App
from sidol.component import Component
from sidol.node import Node
from sidol.widgets import Text


def test_normalise_key_aliases() -> None:
    from sidol.events import normalise_key

    assert normalise_key("escape") == "esc"
    assert normalise_key("Arrow_Up") == "up"
    assert normalise_key("ENTER") == "enter"
    assert normalise_key("a") == "a"


def test_key_event_dataclass() -> None:
    from sidol.events import KeyEvent

    evt = KeyEvent("enter", ctrl=True)
    assert evt.key == "enter"
    assert evt.ctrl is True
    assert evt.alt is False


def test_focus_event_dataclass() -> None:
    from sidol.events import FocusEvent

    evt = FocusEvent("focus", widget_id="btn1")
    assert evt.kind == "focus"
    assert evt.widget_id == "btn1"


def test_tui_skips_disabled_buttons() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    called: list[str] = []
    tree = Column(
        Button("disabled", disabled=True, on_click=lambda: called.append("disabled")),
        Button("enabled", on_click=lambda: called.append("enabled")),
    )
    surface = TuiSurface(None)  # type: ignore[arg-type]
    callbacks = surface._button_callback_map(tree)
    # Rect 0 is the column, rect 1 the disabled button (no entry — it is
    # filtered out), rect 2 the enabled button.
    assert set(callbacks) == {2}
    callbacks[2]()
    assert called == ["enabled"]


def test_tui_dispatch_focus_navigation_and_activation() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    called: list[str] = []
    tree = Column(
        Button("a", on_click=lambda: called.append("a")),
        Button("b", on_click=lambda: called.append("b")),
    )
    surface = TuiSurface(None)  # type: ignore[arg-type]
    button_callbacks = surface._button_callback_map(tree)
    targets = surface._focus_targets(tree)

    focused = -1
    focused, quit = _tui_step(
        surface, _key_event("tab"), focused,
        button_callbacks=button_callbacks, targets=targets,
    )
    assert (focused, quit) == (0, False)
    _tui_step(
        surface, _key_event("enter"), focused,
        button_callbacks=button_callbacks, targets=targets,
    )
    assert called == ["a"]

    focused, _ = _tui_step(
        surface, _key_event("tab"), focused,
        button_callbacks=button_callbacks, targets=targets,
    )
    assert focused == 1
    _tui_step(
        surface, _key_event(" "), focused,
        button_callbacks=button_callbacks, targets=targets,
    )
    assert called == ["a", "b"]

    focused, _ = _tui_step(
        surface, _key_event("backtab"), focused,
        button_callbacks=button_callbacks, targets=targets,
    )
    assert focused == 0

    _, quit = _tui_step(
        surface, _key_event("c", ctrl=True), focused,
        button_callbacks=button_callbacks, targets=targets,
    )
    assert quit is True


def test_tui_dispatch_keyboard_typing_to_textfield() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column, TextField

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.field = TextField(initial="")

        def view(self) -> Node:
            return Column(self.field)

    form = Form()
    tree = App(form).build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)
    assert len(targets) == 1
    focused = 0

    for char in "hi":
        focused, _ = _tui_step(
            surface, _key_event(char), focused,
            button_callbacks={}, targets=targets,
        )
    assert form.field.value == "hi"

    focused, _ = _tui_step(
        surface, _key_event("backspace"), focused,
        button_callbacks={}, targets=targets,
    )
    assert form.field.value == "h"

    focused, _ = _tui_step(
        surface, _key_event("home"), focused,
        button_callbacks={}, targets=targets,
    )
    focused, _ = _tui_step(
        surface, _key_event("a"), focused,
        button_callbacks={}, targets=targets,
    )
    assert form.field.value == "ah"


def test_tui_mouse_click_dispatches_button() -> None:
    from sidol._sidol_core import compute_layout_snapshot

    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    called: list[str] = []
    tree = Column(
        Button("one", on_click=lambda: called.append("one")),
        Button("two", on_click=lambda: called.append("two")),
    )
    snapshot = compute_layout_snapshot(tree, 200.0, 100.0)
    rects = snapshot.to_dicts()
    button_rects = [r for r in rects if r["kind"] == "button"]
    assert len(button_rects) >= 2

    surface = TuiSurface(None)  # type: ignore[arg-type]
    button_callbacks = surface._button_callback_map(tree)

    second = button_rects[1]
    _tui_step(
        surface,
        _click_event(second["x"] + second["w"] / 2, second["y"] + second["h"] / 2),
        -1,
        button_callbacks=button_callbacks,
        targets=[],
        snapshot=snapshot,
    )
    assert called == ["two"]

    called.clear()
    _tui_step(
        surface, _click_event(999, 999), -1,
        button_callbacks=button_callbacks, targets=[], snapshot=snapshot,
    )
    assert called == []


def test_click_dispatch_uses_rect_index_and_kind() -> None:
    """The engine returns ``(rect_index, kind)`` for the topmost visible
    rect; dispatch maps the index through the callback map and ignores
    non-button hits and misses."""
    from sidol.surfaces.tui import TuiSurface

    class FakeSnapshot:
        def __init__(self, hit):
            self._hit = hit

        def hit_test(self, x, y):
            return self._hit

    called: list[str] = []
    surface = TuiSurface(None)  # type: ignore[arg-type]
    callbacks = {2: lambda: called.append("hit")}

    _tui_step(
        surface, _click_event(4, 1), -1,
        button_callbacks=callbacks, targets=[],
        snapshot=FakeSnapshot((2, "button")),
    )
    assert called == ["hit"]

    called.clear()
    _tui_step(
        surface, _click_event(4, 1), -1,
        button_callbacks=callbacks, targets=[],
        snapshot=FakeSnapshot((1, "text")),
    )
    assert called == []

    called.clear()
    _tui_step(
        surface, _click_event(4, 1), -1,
        button_callbacks=callbacks, targets=[],
        snapshot=FakeSnapshot(None),
    )
    assert called == []


def test_tui_run_loop_quits_and_cleans_up(monkeypatch) -> None:
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    class Root(Component):
        def view(self) -> Node:
            return Column(Button("x", on_click=lambda: None))

    init_calls: list[int] = []
    cleanup_calls: list[int] = []
    events = iter([_key_event("c", ctrl=True)])

    monkeypatch.setattr(tui_module, "tui_init", lambda: init_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: cleanup_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda snapshot, idx: next(events)
    )
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(events))

    TuiSurface(App(Root())).run()
    assert init_calls == [1]
    assert cleanup_calls == [1]


def test_resize_event_forces_full_rerender(monkeypatch) -> None:
    """A resize event forces a FULL rebuild on the next frame: the probe's
    view() runs again even though nothing is dirty (an incremental splice
    would reuse the cached subtree), the new viewport dimensions reach the
    layout engine, and focus survives the resize (only a hot-reload swap
    resets it)."""
    from sidol._sidol_core import compute_layout_snapshot as real_snapshot

    import sidol.surfaces.tui as tui_module
    from sidol.component import State
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    view_calls: list[int] = []
    layout_sizes: list[tuple[float, float]] = []
    focused: list[int] = []

    class Probe(Component):
        count = State()

        def __init__(self) -> None:
            super().__init__()
            self.count = 0

        def view(self) -> Node:
            view_calls.append(1)
            return Column(Button("probe"), Text(str(self.count)))

    def fake_size() -> tuple[int, int]:
        # Fresh dimensions after the resize; polled once per render frame.
        return (80, 24) if not layout_sizes else (100, 30)

    def recording_snapshot(tree, w, h):
        layout_sizes.append((w, h))
        return real_snapshot(tree, w, h)

    def fake_render_frame(snapshot, idx):
        focused.append(idx)
        return next(events)

    # Frame 1 renders; its render_frame hands back Tab (focus the button,
    # leaving the loop idle); the idle wait then yields the resize; frame 2
    # renders at the new size; Ctrl+C quits.
    events = iter([_key_event("tab"), _key_event("c", ctrl=True)])
    waits = iter([{"type": "resize"}])

    monkeypatch.setattr(tui_module, "tui_init", lambda: None)
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: None)
    monkeypatch.setattr(tui_module, "tui_size", fake_size)
    monkeypatch.setattr(tui_module, "compute_layout_snapshot", recording_snapshot)
    monkeypatch.setattr(tui_module, "tui_render_frame", fake_render_frame)
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(waits))

    TuiSurface(App(Probe())).run()

    # Full rebuild on the resize frame: view() ran again with no State
    # writes in between — nothing was dirty.
    assert len(view_calls) == 2
    # The new viewport reached the layout engine, re-polled per frame.
    assert layout_sizes == [(80.0, 24.0), (100.0, 30.0)]
    # Focus survived the resize: focused_idx stayed 0 (not reset to -1)
    # and was re-resolved to the button's rect index in the rebuilt tree
    # (pre-order rects: column=0, button=1, text=2).
    assert focused == [-1, 1]


def test_app_run_accepts_injected_surface(monkeypatch) -> None:
    """App.run(surface=...) delegates to the given surface's run() — the
    seam a second surface plugs into (CONTRIBUTING.md Recipe 3) — and the
    default path still constructs TuiSurface(self) and calls its run()."""
    import sidol.surfaces.tui as tui_module

    class Root(Component):
        def view(self) -> Node:
            return Text("hi")

    class FakeSurface:
        def __init__(self, app: App) -> None:
            self.app = app
            self.run_calls = 0

        def run(self) -> None:
            self.run_calls += 1

    constructed: list[object] = []

    class RecordingTuiSurface:
        def __init__(self, app: App) -> None:
            constructed.append(app)

        def run(self) -> None:
            constructed.append("run")

    monkeypatch.setattr(tui_module, "TuiSurface", RecordingTuiSurface)

    app = App(Root())
    fake = FakeSurface(app)
    app.run(surface=fake)
    assert fake.run_calls == 1
    assert fake.app is app
    assert constructed == []  # the default surface was never built

    # Default path: no surface → TuiSurface(self).run(), unchanged.
    app.dispose()  # done with it — release the single-app claim
    default_app = App(Root())
    default_app.run()
    assert constructed == [default_app, "run"]


def test_tui_cleanup_runs_when_rendering_raises(monkeypatch) -> None:
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Text

    class Root(Component):
        def view(self) -> Node:
            return Text("hi")

    cleanup_calls: list[int] = []

    monkeypatch.setattr(tui_module, "tui_init", lambda: None)
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: cleanup_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))

    def boom(snapshot, idx) -> dict:
        raise RuntimeError("render failed")

    monkeypatch.setattr(tui_module, "tui_render_frame", boom)

    with pytest.raises(RuntimeError, match="render failed"):
        TuiSurface(App(Root())).run()
    assert cleanup_calls == [1]


def test_tui_loop_survives_view_error(monkeypatch, capsys) -> None:
    """A broken view() must not kill the loop — it logs, keeps the last good
    frame, and continues until quit (so a developer can hot-reload a fix)."""
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface

    class Boom(Component):
        def view(self) -> Node:
            raise RuntimeError("boom")

    init_calls: list[int] = []
    cleanup_calls: list[int] = []
    events = iter([_key_event("c", ctrl=True)])

    app = App(Boom())
    monkeypatch.setattr(tui_module, "tui_init", lambda: init_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: cleanup_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(events))

    def broken_build_tree() -> Node:
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "build_tree", broken_build_tree)

    TuiSurface(app).run()
    assert init_calls == [1]
    assert cleanup_calls == [1]
    assert "render failed" in capsys.readouterr().err


def test_render_failure_logs_once_per_error_state(monkeypatch, capsys) -> None:
    """The loop re-attempts a failing render on every dirty/resize tick —
    stderr must show each DISTINCT failure once, not once per tick. A
    successful render clears the tracker, so the same failure is logged
    again if it returns later."""
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface

    phase = {"mode": "fail", "msg": "boom"}

    class Flaky(Component):
        def view(self) -> Node:
            if phase["mode"] == "ok":
                return Text("ok")
            raise RuntimeError(phase["msg"])

    init_calls: list[int] = []
    cleanup_calls: list[int] = []

    def event_stream():
        # Ticks 2/4: re-render twice with the SAME error — must stay silent.
        yield {"type": "resize"}
        yield {"type": "resize"}
        # Tick 6: change the message — the new failure state prints.
        phase["msg"] = "changed"
        yield {"type": "resize"}
        # Tick 8: render successfully — clears the tracked error...
        phase["mode"] = "ok"
        yield {"type": "resize"}
        # Tick 10: ...so the same "changed" failure prints again when the
        # view breaks once more...
        phase["mode"] = "fail"
        yield {"type": "resize"}
        # ...and then quit.
        yield _key_event("c", ctrl=True)

    events = event_stream()

    app = App(Flaky())
    monkeypatch.setattr(tui_module, "tui_init", lambda: init_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: cleanup_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda snapshot, idx: {"type": "tick"}
    )
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(events))

    TuiSurface(app).run()
    assert init_calls == [1]
    assert cleanup_calls == [1]
    failure_lines = [
        line
        for line in capsys.readouterr().err.splitlines()
        if "render failed" in line
    ]
    # Three "boom" renders → one line; the changed message prints once;
    # after a successful render the same "changed" failure prints again.
    assert failure_lines == [
        "[sidol] render failed: boom",
        "[sidol] render failed: changed",
        "[sidol] render failed: changed",
    ]


def test_tui_hot_reload_swaps_app_on_file_change(monkeypatch, tmp_path) -> None:
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Text

    watched = tmp_path / "app.py"
    watched.write_text("app = 1\n")

    class Root(Component):
        def view(self) -> Node:
            return Text("one")

    new_root = Root()
    reloader_calls: list[str] = []

    def reloader(path: str):
        reloader_calls.append(path)
        return App(new_root)

    monkeypatch.setattr(tui_module, "tui_init", lambda: None)
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: None)
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    events = iter([{"type": "tick"}, _key_event("c", ctrl=True)])
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda snapshot, idx: next(events)
    )
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(events))

    surface = TuiSurface(App(Root()), watch=[str(watched)], reloader=reloader)
    # Force a change to be detected on the first tick.
    surface._last_mtimes[str(watched)] = 0
    surface.run()

    assert reloader_calls == [str(watched)]
    assert surface._app.root is new_root


def test_tui_hot_reload_keeps_app_when_reloader_returns_none(
    monkeypatch, tmp_path
) -> None:
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Text

    watched = tmp_path / "app.py"
    watched.write_text("app = 1\n")

    class Root(Component):
        def view(self) -> Node:
            return Text("one")

    reloader_calls: list[str] = []

    def reloader(path: str):
        reloader_calls.append(path)
        return None

    monkeypatch.setattr(tui_module, "tui_init", lambda: None)
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: None)
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    events = iter([{"type": "tick"}, _key_event("c", ctrl=True)])
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda snapshot, idx: next(events)
    )
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(events))

    original = App(Root())
    surface = TuiSurface(original, watch=[str(watched)], reloader=reloader)
    surface._last_mtimes[str(watched)] = 0
    surface.run()

    assert reloader_calls == [str(watched)]
    assert surface._app is original


def test_cli_reloader_re_executes_module(tmp_path) -> None:
    import importlib.util

    from sidol.cli import _reloader

    app_file = tmp_path / "app.py"
    app_file.write_text("app = 1\n")
    spec = importlib.util.spec_from_file_location("reload_mod", str(app_file))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    reload = _reloader(module)
    assert module.app == 1

    app_file.write_text("app = 2\n")
    assert reload(str(app_file)) == 2
    assert module.app == 2


def test_typing_q_into_focused_textfield_does_not_quit() -> None:
    """Regression: the engine once mapped 'q' to "quit" before Python
    dispatch, making the letter untypable in text inputs."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column, TextField

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.field = TextField(initial="")

        def view(self) -> Node:
            return Column(self.field)

    form = Form()
    tree = App(form).build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)

    _, quit = _tui_step(
        surface, _key_event("q"), 0,
        button_callbacks={}, targets=targets, root=tree,
    )
    assert quit is False
    assert form.field.value == "q"


def test_textfield_types_uppercase_and_symbols() -> None:
    """Regression: the engine once lowercased every character and dropped
    symbols, making e.g. email addresses untypable."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column, TextField

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.field = TextField(initial="")

        def view(self) -> Node:
            return Column(self.field)

    form = Form()
    tree = App(form).build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)

    for char in "A@b.1":
        _tui_step(
            surface, _key_event(char, shift=char in "A@"), 0,
            button_callbacks={}, targets=targets, root=tree,
        )
    assert form.field.value == "A@b.1"


def test_ctrl_modified_char_does_not_insert_text() -> None:
    """Ctrl/alt combos are commands, not text input — the wildcard
    handler must not swallow them."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column, TextField

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.field = TextField(initial="")

        def view(self) -> Node:
            return Column(self.field)

    form = Form()
    tree = App(form).build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)

    _, quit = _tui_step(
        surface, _key_event("x", ctrl=True), 0,
        button_callbacks={}, targets=targets, root=tree,
    )
    assert quit is False
    assert form.field.value == ""


def test_ctrl_c_quits_at_surface_level() -> None:
    from sidol.surfaces.tui import TuiSurface

    surface = TuiSurface(None)  # type: ignore[arg-type]
    _, quit = _tui_step(
        surface, _key_event("c", ctrl=True), -1,
        button_callbacks={}, targets=[],
    )
    assert quit is True


def test_root_on_key_fallback_and_request_quit() -> None:
    """App-level bindings live on the root node; they fire when the
    focused widget (or no widget) doesn't handle the key."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    class Root(Component):
        def view(self) -> Node:
            return Column(
                Button("x", on_click=lambda: None),
                on_key={"q": lambda event: app.request_quit()},
            )

    app = App(Root())
    tree = app.build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)

    # Focused button doesn't bind "q" → root fallback fires.
    _, quit = _tui_step(
        surface, _key_event("q"), 0,
        button_callbacks={}, targets=targets, root=tree,
    )
    assert quit is False  # dispatch itself doesn't quit...
    assert app._quit_requested is True  # ...the app flag does

    # Unrelated keys hit no binding and change nothing.
    app.dispose()  # done with it — release the single-app claim
    app2_root = Root()
    app2 = App(app2_root)
    tree2 = app2.build_tree()
    _tui_step(
        surface, _key_event("z"), -1,
        button_callbacks={}, targets=surface._focus_targets(tree2), root=tree2,
    )
    assert app2._quit_requested is False


def test_focusable_flag_controls_container_focus() -> None:
    """``on_key`` alone is an app-level fallback, not focus; an explicit
    ``focusable=True`` opts a container into Tab focus."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    surface = TuiSurface(None)  # type: ignore[arg-type]

    plain = Column(Button("x", on_click=lambda: None), on_key={"q": lambda e: None})
    assert [n.kind for n in surface._focus_targets(plain)] == ["button"]

    marked = Column(
        Button("x", on_click=lambda: None),
        on_key={"q": lambda e: None},
        focusable=True,
    )
    assert [n.kind for n in surface._focus_targets(marked)] == ["column", "button"]


def test_normalise_key_preserves_single_char_case() -> None:
    from sidol.events import normalise_key

    assert normalise_key("A") == "A"
    assert normalise_key("@") == "@"
    assert normalise_key("ESCAPE") == "esc"
    assert normalise_key("Arrow_Up") == "up"


def test_click_hit_test_accounts_for_scroll_offset() -> None:
    """Regression: clicks were tested against layout coordinates while
    the renderer draws scrolled content offset — scrolled buttons were
    hit where they *were*, not where they're *drawn*."""
    from sidol._sidol_core import compute_layout_snapshot

    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column
    from sidol.widgets.scroll import ScrollView

    called: list[str] = []

    class Scroller(Component):
        def __init__(self) -> None:
            super().__init__()
            self.scroller = ScrollView(
                Column(
                    Button("top", on_click=lambda: called.append("top")),
                    Button("mid", on_click=lambda: called.append("mid")),
                    Button("bot", on_click=lambda: called.append("bot")),
                ),
                max_h=3,
            )
            # Scroll so "top" leaves the viewport and "mid" slides into place.
            self.scroller.scroll_to(y=3)

        def view(self) -> Node:
            return self.scroller

    app = App(Scroller())
    tree = app.build_tree()
    snapshot = compute_layout_snapshot(tree, 40.0, 10.0)
    rects = snapshot.to_dicts()
    buttons = [r for r in rects if r["kind"] == "button"]
    assert len(buttons) == 3

    surface = TuiSurface(None)  # type: ignore[arg-type]
    button_callbacks = surface._button_callback_map(tree)

    # "mid" is laid out 3 rows below "top" (each button is 3 rows tall);
    # scrolled by 3, it is DRAWN where "top" was laid out.
    mid = buttons[1]
    _tui_step(
        surface,
        _click_event(mid["x"] + 1, mid["y"] - 3 + 1),
        -1,
        button_callbacks=button_callbacks,
        targets=[],
        snapshot=snapshot,
        root=tree,
    )
    assert called == ["mid"]

    # Clicking "top"'s layout position must not hit it — it's clipped
    # away; the visible button there is "mid".
    called.clear()
    top = buttons[0]
    _tui_step(
        surface,
        _click_event(top["x"] + 1, top["y"] + 1),
        -1,
        button_callbacks=button_callbacks,
        targets=[],
        snapshot=snapshot,
        root=tree,
    )
    assert called == ["mid"]


def test_dropdown_keyboard_interaction() -> None:
    """Dropdown is focusable and fully keyboard-operable."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column
    from sidol.widgets.dropdown import Dropdown

    selections: list[str] = []

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.dropdown = Dropdown(
                ["a", "b", "c"],
                on_select=lambda i, v: selections.append(v),
            )

        def view(self) -> Node:
            return Column(self.dropdown)

    form = Form()
    tree = App(form).build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)
    assert len(targets) == 1  # the dropdown is a focus target

    def press(key: str) -> None:
        _tui_step(
            surface, _key_event(key), 0,
            button_callbacks={}, targets=targets, root=tree,
        )

    press("enter")  # opens
    assert form.dropdown.is_open is True
    press("down")  # highlights first option
    assert form.dropdown.selected == 0
    press("down")
    assert form.dropdown.selected == 1
    press("enter")  # commits
    assert form.dropdown.is_open is False
    assert selections == ["b"]

    press("down")  # re-opens
    assert form.dropdown.is_open is True
    press("esc")  # cancels without selecting
    assert form.dropdown.is_open is False
    assert selections == ["b"]


def test_dropdown_windows_long_option_lists() -> None:
    """The highlight stays visible when selection moves past max_height."""
    from sidol.widgets.dropdown import Dropdown

    dd = Dropdown([f"opt-{i}" for i in range(10)], max_height=3)
    dd.open()
    dd.selected = 7
    texts = _collect_text(dd.rendered_view())
    assert any("> opt-7" in t for t in texts)
    assert not any("opt-0" in t for t in texts)  # scrolled out of the window


def test_slider_keyboard_adjusts_value() -> None:
    """Slider is focusable and keyboard-operable."""
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column
    from sidol.widgets.slider import Slider

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.slider = Slider(min_val=0.0, max_val=10.0, step=2.0, value=4.0)

        def view(self) -> Node:
            return Column(self.slider)

    form = Form()
    tree = App(form).build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)
    assert len(targets) == 1

    def press(key: str) -> None:
        _tui_step(
            surface, _key_event(key), 0,
            button_callbacks={}, targets=targets, root=tree,
        )

    press("right")
    assert form.slider.value == 6.0
    press("left")
    assert form.slider.value == 4.0
    press("end")
    assert form.slider.value == 10.0
    press("home")
    assert form.slider.value == 0.0
