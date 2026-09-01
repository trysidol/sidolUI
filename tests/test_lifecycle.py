"""Lifecycle: dispose() teardown, keyed-list reconciliation and
eviction, and App disposal."""

from __future__ import annotations

import os

import pytest
from _helpers import Counter, _collect_text, _key_event

from sidol.app import App, _multiple_apps_allowed
from sidol.component import Component, State, _graph, reset_graph
from sidol.node import Node
from sidol.widgets import Text


def test_remember_preserves_stateful_child_identity() -> None:
    from sidol.widgets import Column, TextField

    class Parent(Component):
        version = State()

        def __init__(self) -> None:
            super().__init__()
            self.version = 0

        def view(self) -> Node:
            child = self.remember("field", lambda: TextField(initial="start"))
            return Column(Text(str(self.version)), child)

    parent = Parent()
    app = App(parent)
    app.build_tree()
    child = parent._retained_children["field"]
    assert isinstance(child, TextField)
    child.value = "edited"
    parent.version = 1
    app.flush()
    app.build_tree()
    assert parent._retained_children["field"] is child
    assert child.value == "edited"


def test_keyed_children_reconcile_when_reordered() -> None:
    from sidol.widgets import Column

    class Item(Component):
        value = State()

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        def view(self) -> Node:
            return Text(self.value)

    class Parent(Component):
        reverse = State()

        def __init__(self) -> None:
            super().__init__()
            self.reverse = False

        def view(self) -> Node:
            items = [Item("a").keyed("a"), Item("b").keyed("b")]
            if self.reverse:
                items.reverse()
            return Column(*items)

    parent = Parent()
    app = App(parent)
    app.build_tree()
    first = dict(parent._keyed_children)
    first["a"].value = "edited"
    parent.reverse = True
    app.flush()
    app.build_tree()
    assert parent._keyed_children["a"] is first["a"]
    assert parent._keyed_children["b"] is first["b"]
    assert parent._keyed_children["a"].value == "edited"


def test_list_key_preserves_item_identity_on_reorder() -> None:
    """List(key=...) auto-keys stateful items, so local state survives
    reorders without manual .keyed() calls."""
    from sidol.widgets import Text
    from sidol.widgets.list import List

    class Item(Component):
        value = State()

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        def view(self) -> Node:
            return Text(self.value)

    class TodoApp(Component):
        todos = State()
        reverse = State()

        def __init__(self) -> None:
            super().__init__()
            self.todos = ["a", "b"]
            self.reverse = False

        def view(self) -> Node:
            todos = list(self.todos)
            if self.reverse:
                todos.reverse()
            return List(
                todos,
                key=lambda item: item,
                builder=lambda item, _i: Item(item),
            )

    app = App(TodoApp())
    app.build_tree()
    root = app.root
    first = dict(root._keyed_children)
    first["a"].value = "edited"
    root.reverse = True
    app.flush()
    app.build_tree()
    assert root._keyed_children["a"] is first["a"]
    assert root._keyed_children["b"] is first["b"]
    assert root._keyed_children["a"].value == "edited"


def test_list_key_preserves_identity_across_add_and_remove() -> None:
    from sidol.widgets import Text
    from sidol.widgets.list import List

    class Item(Component):
        value = State()

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        def view(self) -> Node:
            return Text(self.value)

    class TodoApp(Component):
        todos = State()

        def __init__(self) -> None:
            super().__init__()
            self.todos = ["a", "b"]

        def view(self) -> Node:
            return List(
                self.todos,
                key=lambda item: item,
                builder=lambda item, _i: Item(item),
            )

    app = App(TodoApp())
    root = app.root
    app.build_tree()
    before = dict(root._keyed_children)
    before["b"].value = "edited-b"

    # Add "c", remove "a" — "b" must keep its instance and state.
    root.todos = ["b", "c"]
    app.flush()
    app.build_tree()
    assert set(root._keyed_children) == {"b", "c"}
    assert root._keyed_children["b"] is before["b"]
    assert root._keyed_children["b"].value == "edited-b"
    assert "a" not in root._keyed_children


def test_evicted_keyed_child_is_disposed() -> None:
    from sidol.widgets.list import List

    class Item(Component):
        value = State()

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        def view(self) -> Node:
            return Text(self.value)

    class TodoApp(Component):
        todos = State()

        def __init__(self) -> None:
            super().__init__()
            self.todos = ["a", "b"]

        def view(self) -> Node:
            return List(
                self.todos,
                key=lambda item: item,
                builder=lambda item, _i: Item(item),
            )

    app = App(TodoApp())
    root = app.root
    app.build_tree()
    evicted = root._keyed_children["a"]
    evicted_state_signal = evicted._signal_ids["value"]

    # Remove "a" — its component must be disposed deterministically during
    # reconciliation, not left to the best-effort __del__ safety net.
    root.todos = ["b"]
    app.flush()
    app.build_tree()
    assert set(root._keyed_children) == {"b"}
    assert evicted._disposed is True
    # Its graph nodes are gone: mark_dirty validates registration and
    # raises for removed signal IDs (see test_graph_rejects_unknown_signal_ids).
    with pytest.raises(ValueError, match="unknown signal ID"):
        _graph.mark_dirty(evicted._view_signal_id)
    with pytest.raises(ValueError, match="unknown signal ID"):
        _graph.mark_dirty(evicted_state_signal)


def test_incremental_frame_evicts_disposed_keyed_children() -> None:
    """P0.1 semantics on the incremental frame path: a List whose item set
    changes re-renders the owning component, reconciles keys, and disposes
    evicted children — no stale subtrees survive the splice."""
    from sidol.widgets.list import List

    class Item(Component):
        value = State()

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        def view(self) -> Node:
            return Text(self.value)

    class TodoApp(Component):
        todos = State()

        def __init__(self) -> None:
            super().__init__()
            self.todos = ["a", "b"]

        def view(self) -> Node:
            return List(
                self.todos,
                key=lambda item: item,
                builder=lambda item, _i: Item(item),
            )

    app = App(TodoApp())
    root = app.root
    app.build_tree()
    evicted = root._keyed_children["a"]
    evicted_state_signal = evicted._signal_ids["value"]

    # Remove "a" and drive the frame path (not the full rebuild): the
    # owning component re-renders, "a" is evicted and disposed during
    # reconciliation, and the spliced tree shows only "b".
    root.todos = ["b"]
    tree = app.build_tree(set(_graph.drain_dirty()))
    assert set(root._keyed_children) == {"b"}
    assert evicted._disposed is True
    # Its graph nodes are gone (see test_evicted_keyed_child_is_disposed).
    with pytest.raises(ValueError, match="unknown signal ID"):
        _graph.mark_dirty(evicted._view_signal_id)
    with pytest.raises(ValueError, match="unknown signal ID"):
        _graph.mark_dirty(evicted_state_signal)
    assert _collect_text(tree) == ["b"]


def test_duplicate_child_keys_are_rejected() -> None:
    from sidol.widgets import Column

    class Item(Component):
        def view(self) -> Node:
            return Text("x")

    class Dup(Component):
        def view(self) -> Node:
            return Column(Item().keyed("k"), Item().keyed("k"))

    with pytest.raises(ValueError, match="duplicate child key 'k'"):
        App(Dup()).build_tree()


def test_dispose_removes_signals_and_unregisters_computation() -> None:
    """dispose() must remove the view + state signals from the graph and
    drop the component from _computations, deterministically."""
    from sidol.component import _computations

    counter = Counter()
    counter.count = 5
    view_id = counter._view_signal_id

    assert _computations.get(view_id) is counter
    counter.dispose()
    assert counter._disposed is True
    assert _computations.get(view_id) is None
    # State writes after dispose must not create fresh graph signals.
    assert counter._signal_ids == {}
    counter.count = 6
    assert counter._signal_ids == {}
    assert _graph.dirty_ids() == []


def test_dispose_is_idempotent() -> None:
    counter = Counter()
    counter.dispose()
    counter.dispose()  # must not raise


def test_dispose_recurses_into_remembered_children() -> None:
    from sidol.widgets import Column, TextField

    class Parent(Component):
        def view(self) -> Node:
            child = self.remember("field", lambda: TextField(initial="start"))
            return Column(Text("parent"), child)

    parent = Parent()
    app = App(parent)
    app.build_tree()
    child = parent._retained_children["field"]
    assert child._disposed is False

    parent.dispose()
    assert parent._disposed is True
    assert child._disposed is True
    assert parent._retained_children == {}


def test_dispose_recurses_into_keyed_children() -> None:
    from sidol.widgets import Column

    class Item(Component):
        value = State()

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        def view(self) -> Node:
            return Text(self.value)

    class Parent(Component):
        def view(self) -> Node:
            return Column(Item("a").keyed("a"), Item("b").keyed("b"))

    parent = Parent()
    app = App(parent)
    app.build_tree()
    children = list(parent._keyed_children.values())
    assert children and all(c._disposed is False for c in children)

    parent.dispose()
    assert all(c._disposed is True for c in children)
    assert parent._keyed_children == {}


def test_dispose_via_app_disposes_root_tree() -> None:
    counter = Counter()
    app = App(counter)
    app.dispose()
    assert counter._disposed is True
    app.dispose()  # idempotent


def test_second_app_while_first_alive_warns() -> None:
    """The single-app invariant: the signal graph and flush() are
    process-global, so constructing a second App while one is alive warns
    (raise would break the hot-reload swap's construct-then-dispose
    window — the warning is the pre-alpha price, per DO-NOT #2)."""
    app = App(Counter())
    with pytest.warns(UserWarning, match="another App is still alive"):
        App(Counter())
    app.dispose()


def test_second_app_after_dispose_is_clean() -> None:
    """dispose() releases the single-app claim: constructing a replacement
    after disposal does not warn."""
    import warnings

    app = App(Counter())
    app.dispose()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        replacement = App(Counter())  # must not warn
    replacement.dispose()


def test_second_app_after_gc_release_is_clean() -> None:
    """An undisposed App that becomes garbage releases the claim too —
    the live-set is a WeakSet, so collection is enough."""
    import gc
    import warnings

    app = App(Counter())
    del app
    gc.collect()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        replacement = App(Counter())  # must not warn
    replacement.dispose()


def test_swap_window_hatch_suppresses_warning() -> None:
    """The hot-reload swap window (construct the replacement while the old
    app is still alive, then dispose it) is the one supported exception —
    _multiple_apps_allowed() suppresses the warning inside it."""
    import warnings

    app = App(Counter())
    with _multiple_apps_allowed():
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            replacement = App(Counter())  # must not warn
    app.dispose()
    replacement.dispose()


def test_disposed_state_read_still_returns_last_value() -> None:
    """A worker callback landing on a disposed component must be able to read
    the last value without registering new dependencies."""
    counter = Counter()
    counter.count = 3
    counter.dispose()
    assert counter.count == 3


def test_dispose_after_reset_graph_is_safe() -> None:
    """reset_graph() leaves live components with dangling signal IDs; dispose
    must tolerate that (remove_signal is a no-op for missing IDs)."""
    counter = Counter()
    counter.count = 5
    reset_graph()
    counter.dispose()  # must not raise
    assert counter._disposed is True


def test_dispose_during_flush_skips_unregistered_computation() -> None:
    """A component disposed between drain_dirty() and rendered_view() must be
    skipped by flush() — _computations no longer maps its view signal."""
    counter = Counter()
    app = App(counter)
    app.build_tree()
    counter.count = 5
    counter.dispose()
    app.flush()  # must not raise, must not re-render the disposed component
    assert counter._disposed is True
    assert _graph.dirty_ids() == []


def test_worker_callback_after_dispose_is_safe() -> None:
    """A Worker completion callback that lands after the component was disposed
    must store the value without touching the graph (no new signals, no dirty
    marks) — so hot-reload never crashes on late callbacks."""
    from sidol.concurrency import Worker

    counter = Counter()
    counter.count = 1

    def on_done(result: int) -> None:
        counter.count = result

    worker = Worker(lambda: 42, on_done=on_done)
    worker.start()
    worker.join()
    assert counter.count == 42
    count_id = counter._signal_ids["count"]
    assert count_id is not None

    counter.dispose()
    _graph.clear_dirty()
    worker2 = Worker(lambda: 99, on_done=on_done)
    worker2.start()
    worker2.join()
    assert counter.count == 99
    assert _graph.dirty_ids() == []


def test_tui_hot_reload_disposes_old_app(monkeypatch, tmp_path) -> None:
    import sidol.surfaces.tui as tui_module
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Text

    watched = tmp_path / "app.py"
    watched.write_text("app = 1\n")

    class Root(Component):
        def view(self) -> Node:
            return Text("one")

    old_root = Root()
    old_app = App(old_root)
    new_root = Root()
    # Both apps exist before the swap runs — the same construct-then-dispose
    # window the real swap uses, so the single-app warning is suppressed.
    with _multiple_apps_allowed():
        new_app = App(new_root)

    def reloader(path: str):
        return new_app

    monkeypatch.setattr(tui_module, "tui_init", lambda: None)
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: None)
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    events = iter([{"type": "tick"}, _key_event("c", ctrl=True)])
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda snapshot, idx: next(events)
    )
    monkeypatch.setattr(tui_module, "tui_wait_event", lambda: next(events))

    surface = TuiSurface(old_app, watch=[str(watched)], reloader=reloader)
    surface._last_mtimes[str(watched)] = 0
    surface.run()

    # The old app must be deterministically disposed on swap.
    assert old_root._disposed is True
    assert surface._app is new_app
    # The running (new) app is disposed on surface teardown.
    assert new_root._disposed is True


def test_dev_server_hot_reload_disposes_old_app() -> None:
    import importlib.util
    import sys
    import tempfile

    from sidol.dev_server import DevServer

    code = """from sidol import App
from sidol.component import Component

class TestComp(Component):
    def view(self):
        from sidol import Text
        return Text("v1")

app = App(TestComp())
"""
    tmpdir = tempfile.mkdtemp(prefix="sidol_dispose_")
    try:
        tmp_path = os.path.join(tmpdir, "hot_app.py")
        with open(tmp_path, "w", newline="") as f:
            f.write(code)
            f.flush()
            os.fsync(f.fileno())

        abs_path = os.path.abspath(tmp_path)
        orig_path = list(sys.path)
        sys.path.insert(0, tmpdir)
        try:
            spec = importlib.util.spec_from_file_location("hot_app_d", abs_path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            old_app = module.app

            server = DevServer(
                old_app,
                host="127.0.0.1",
                port=0,
                verbosity=0,
                watch=tmp_path,
                module=module,
            )
            old_root = old_app.root
            assert old_root._disposed is False

            with open(tmp_path, "w", newline="") as f:
                f.write(code.replace("v1", "v2"))
                f.flush()
                os.fsync(f.fileno())
                server._hot_reload(tmp_path)

            assert old_root._disposed is True
            assert server._app is not old_app

            # Stop the server and release the swapped-in app — otherwise
            # the flush-hook cycle keeps it alive past this test and trips
            # the single-app invariant in later tests.
            server.stop()
        finally:
            sys.path = orig_path
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
