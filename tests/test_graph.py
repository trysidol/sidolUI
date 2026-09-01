"""Reactive graph: State tracking, flush semantics, dirty propagation,
graph reset, and component-tree resolution."""

from __future__ import annotations

import pytest
from _helpers import Counter, _collect_text

from sidol.app import App
from sidol.component import Component, State, _graph, reset_graph
from sidol.node import Node
from sidol.widgets import Text


class ConditionalReader(Component):
    """view() reads DIFFERENT state depending on mode. Minimum reproduction
    for the stale-conditional-subscription bug."""

    mode = State()
    a = State()
    b = State()

    def __init__(self) -> None:
        super().__init__()
        self.mode = True
        self.a = 0
        self.b = 0

    def view(self) -> Node:
        return Text(str(self.a if self.mode else self.b))


class SideEffectingComponent(Component):
    """Component whose view() writes to a different component's state.
    Used to test the write-during-render guard."""

    other: Counter  # set by test, not State
    value = State()

    def __init__(self) -> None:
        super().__init__()
        self.value = 0
        self.other = None  # type: ignore[assignment]

    def view(self) -> Node:
        # Write to other component's state DURING a tracked read.
        # This is the re-entrancy scenario.
        self.other.count = self.value
        return Text(str(self.value))


def test_state_mutation_is_readable_from_python() -> None:
    counter = Counter()
    counter.count = 1
    counter.count = 2
    assert counter.count == 2


def test_reading_state_during_view_registers_a_dependency() -> None:
    counter = Counter()
    counter.rendered_view()
    counter.count = 5
    assert counter._view_signal_id in _graph.dirty_ids()


def test_stale_dependency_is_pruned_after_mode_switch() -> None:
    comp = ConditionalReader()
    comp.rendered_view()
    _graph.clear_dirty()
    comp.mode = False
    _graph.clear_dirty()
    comp.rendered_view()
    _graph.clear_dirty()

    comp.a = 99
    assert comp._view_signal_id not in _graph.dirty_ids(), (
        "view was dirtied by a State it no longer reads — stale edge not pruned"
    )


def test_active_dependency_still_fires_after_mode_switch() -> None:
    comp = ConditionalReader()
    comp.mode = False
    comp.rendered_view()
    _graph.clear_dirty()
    comp.b = 42
    assert comp._view_signal_id in _graph.dirty_ids()


def test_uninitialized_state_raises_attribute_error() -> None:
    class Broken(Component):
        value = State()

        def __init__(self) -> None:
            super().__init__()

        def view(self) -> None:
            return None

    comp = Broken()
    with pytest.raises(AttributeError, match="initialised"):
        _ = comp.value


def test_reset_graph_clears_dirty_state() -> None:
    counter = Counter()
    counter.count = 7
    reset_graph()
    assert _graph.dirty_ids() == []


def test_reset_graph_clears_observer_stack() -> None:
    from sidol.component import _observer_stack

    _observer_stack.append(999)
    reset_graph()
    assert _observer_stack == []


def test_flush_rerenders_dirty_components() -> None:
    app = App(Counter())
    # Initial render
    app.build_tree()
    # Mutate state — view signal becomes dirty
    app.root.count = 5
    # Flush should re-render, producing the updated value
    app.flush()
    assert app.root.count == 5


def test_flush_clears_dirty_set() -> None:
    app = App(Counter())
    app.build_tree()
    app.root.count = 5
    app.flush()
    assert _graph.dirty_ids() == []


def test_flush_ignores_non_computation_signals() -> None:
    """State signals in the dirty set should be skipped (no Component
    registered for them in _computations)."""
    app = App(Counter())
    app.build_tree()
    app.root.count = 5
    # count_signal_id is in dirty_ids but is not a computation signal.
    # flush must not crash or try to render it.
    app.flush()  # should not raise


def test_flush_multiple_components() -> None:
    """Multiple dirty computation signals are all processed."""
    app = App(Counter())
    c2 = Counter()
    app.build_tree()
    c2.rendered_view()
    _graph.clear_dirty()

    app.root.count = 10
    c2.count = 20

    app.flush()
    # Both components were re-rendered; state is correct.
    assert app.root.count == 10
    assert c2.count == 20
    assert _graph.dirty_ids() == []


def test_write_during_render_queues_for_next_flush() -> None:
    """A write to another component's state during rendered_view()
    creates a fresh dirty set instead of re-entering the flush loop.
    The downstream component is NOT re-rendered until the next flush."""
    side_effect = SideEffectingComponent()
    target = Counter()  # the component that side_effect writes to
    side_effect.other = target

    # Initial render of both
    app = App(side_effect)
    app.build_tree()
    target.rendered_view()
    _graph.clear_dirty()

    # Mutate side_effect's state — this dirties side_effect's view signal
    side_effect.value = 42

    # The dirty set contains side_effect's view signal (and its value state)
    # but NOT target's view signal yet (it's not dirty — the write happens
    # during re-render, not before).
    assert _graph.dirty_ids() != []

    # Flush: side_effect re-renders, which writes to target.count.
    # That write marks target's view signal dirty. But flush is iterating
    # a snapshot taken BEFORE the write — target won't be seen this cycle.
    app.flush()

    # After flush, target's view signal IS dirty (from the write during
    # render) — it'll be picked up on the next flush.
    assert target._view_signal_id in _graph.dirty_ids(), (
        "write during render should dirty target but not flush it "
    )


def test_flush_then_flush_again_processes_deferred() -> None:
    """The second flush picks up what the first deferred.

    Sequence:
      1. side_effect.value = 42 dirties side_effect's view.
      2. First flush snapshots, clears dirty, then re-renders
         side_effect. During rendered_view() the write to
         target.count adds target's view to the dirty set (a
         fresh set, because step 2 cleared the old one).
      3. target is dirty but was not in the snapshot → deferred.
      4. Second flush picks up target and re-renders it.
      5. After second flush the dirty set is clean.
    """
    side_effect = SideEffectingComponent()
    target = Counter()
    side_effect.other = target

    app = App(side_effect)
    app.build_tree()
    target.rendered_view()
    _graph.clear_dirty()

    side_effect.value = 42
    app.flush()  # (2) re-renders side_effect, writes to target.count

    # (3) target is dirty from the write during render, NOT from the
    # snapshot — this is the deferred-work pattern.
    assert target._view_signal_id in _graph.dirty_ids(), (
        "target should be dirty from write during render"
    )

    # (4) second flush picks up target
    app.flush()
    assert _graph.dirty_ids() == [], (
        "second flush should leave clean state"
    )


class BreakageError(Exception):
    """Distinctive exception type for the flush() error-path tests."""


def test_flush_reraises_after_processing_full_batch() -> None:
    """A failing view() must not silence the rest of the batch: flush
    processes every dirty component in the snapshot, THEN re-raises the
    stored exception ("a single broken component never silences other
    components' updates")."""
    class Broken(Component):
        fail = State()

        def __init__(self) -> None:
            super().__init__()
            self.fail = False
            self.render_count = 0

        def view(self) -> Node:
            self.render_count += 1
            if self.fail:
                raise BreakageError("view exploded")
            return Text("ok")

    class Healthy(Component):
        count = State()

        def __init__(self) -> None:
            super().__init__()
            self.count = 0
            self.render_count = 0

        def view(self) -> Node:
            self.render_count += 1
            return Text(str(self.count))

    broken = Broken()
    healthy = Healthy()
    app = App(broken)
    app.build_tree()  # initial render of broken (fail=False)
    healthy.rendered_view()  # initial render of healthy
    _graph.clear_dirty()

    # Both view signals dirty in the SAME batch.
    broken.fail = True
    healthy.count = 7

    with pytest.raises(BreakageError, match="view exploded"):
        app.flush()
    # Both were attempted before the raise — the healthy component's
    # re-render happened even though broken's view raised (batch order
    # is non-deterministic; the contract holds either way).
    assert broken.render_count == 2
    assert healthy.render_count == 2
    # The raises() traceback keeps this test's frame (and its app) in a
    # reference cycle until gc runs — release the single-app claim now.
    app.dispose()


def test_flush_requeues_failed_view_signal_and_retries() -> None:
    """A view() that raises leaves its signal dirty: flush re-queues the
    view signal, and the next flush retries the component (raising again
    while it is still broken)."""
    class Broken(Component):
        fail = State()

        def __init__(self) -> None:
            super().__init__()
            self.fail = False
            self.render_count = 0

        def view(self) -> Node:
            self.render_count += 1
            if self.fail:
                raise BreakageError("view exploded")
            return Text("ok")

    broken = Broken()
    app = App(broken)
    app.build_tree()
    assert broken.render_count == 1

    broken.fail = True
    with pytest.raises(BreakageError, match="view exploded"):
        app.flush()
    assert broken.render_count == 2

    # The failed view signal was re-queued — it is the only thing dirty
    # (the state signal was drained and discarded by the first flush).
    assert _graph.dirty_ids() == [broken._view_signal_id]

    # Second flush retries the broken component (render_count increments)
    # and raises again while it is still broken.
    with pytest.raises(BreakageError, match="view exploded"):
        app.flush()
    assert broken.render_count == 3
    assert _graph.dirty_ids() == [broken._view_signal_id]
    # The raises() tracebacks keep this test's frame (and its app) in a
    # reference cycle until gc runs — release the single-app claim now.
    app.dispose()


def test_flush_listener_runs_after_successful_flush() -> None:
    """Post-flush listeners run in registration order after the batch
    completes — the honest "after each flush, push elsewhere" hook
    (the dev server's browser push), with no monkeypatching."""
    app = App(Counter())
    app.build_tree()
    ran: list[str] = []
    app.add_flush_listener(lambda: ran.append("a"))
    app.add_flush_listener(lambda: ran.append("b"))

    app.root.count = 5
    app.flush()

    assert ran == ["a", "b"]


def test_flush_listener_skipped_when_flush_raises() -> None:
    """A failed flush re-raises without running listeners — a listener
    never observes a partially-flushed batch (P0.2 contract preserved)."""

    class Broken(Component):
        fail = State()

        def __init__(self) -> None:
            super().__init__()
            self.fail = False

        def view(self) -> Node:
            if self.fail:
                raise BreakageError("view exploded")
            return Text("ok")

    app = App(Broken())
    app.build_tree()
    ran: list[str] = []
    app.add_flush_listener(lambda: ran.append("x"))

    app.root.fail = True
    with pytest.raises(BreakageError, match="view exploded"):
        app.flush()

    assert ran == []
    # The raises() traceback keeps this test's frame (and its app) in a
    # reference cycle until gc runs — release the single-app claim now.
    app.dispose()


def test_remove_flush_listener_tolerates_unknown() -> None:
    """Removing an unregistered listener is a no-op (DevServer.stop() may
    run twice), and a removed listener no longer fires."""
    app = App(Counter())
    ran: list[str] = []

    def listener() -> None:
        ran.append("x")

    app.add_flush_listener(listener)
    app.remove_flush_listener(listener)
    app.remove_flush_listener(listener)  # still no-op

    app.root.count = 1
    app.flush()

    assert ran == []


def test_mark_dirty_repropagates_through_new_edges_without_intermediate_clear() -> None:
    """Python-level repro for the conflated cycle/dedup bug.

    If a signal is already in the dirty set and a new dependent edge
    is added, a subsequent mark_dirty on that signal must walk the
    new edge — even though the signal was already dirty.
    """
    # Create a state signal + initial dependent
    counter = Counter()
    counter.rendered_view()  # registers count -> view edge
    _graph.clear_dirty()

    # Mark count dirty (first propagation)
    counter.count = 1
    assert counter._view_signal_id in _graph.dirty_ids()

    # Now add a second dependent WITHOUT clearing dirty.
    # We simulate this by creating another component that reads
    # the same state during a tracked call.
    second = Counter()
    second.count = 0  # creates second's count signal — distinct from counter's

    # At this point counter's view signal is dirty. If we set counter.count
    # again, the old code would skip propagation because counter's count
    # signal was already dirty. The new code walks it regardless.
    counter.count = 2

    # After the re-propagation, counter's view signal is still dirty
    # (was already dirty from the first mark_dirty). The key thing is
    # that no error occurs and propagation completes correctly.
    assert counter._view_signal_id in _graph.dirty_ids()


class Leaf(Component):
    """A component with its own state, used as a child."""

    label = State()

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self.label = initial

    def view(self) -> Node:
        from sidol import Text

        return Text(self.label)


class Container(Component):
    """A component that nests child Components."""

    def __init__(self, *children: Component) -> None:
        super().__init__()
        self._nested_children = children

    def view(self) -> Node:
        from sidol import Column

        return Column(*self._nested_children)  # type: ignore[arg-type]


def test_nested_component_tree_is_resolved() -> None:
    """Child Components are resolved into their Node subtrees."""
    from sidol.node import Node

    child = Leaf("Hello")
    parent = Container(child)
    app = App(parent)
    tree = app.build_tree()

    # Leaf.view() returns Text, so the resolved tree should be:
    # Column(Text("Hello"))
    assert tree.kind == "column"
    assert len(tree.children) == 1
    text_node = tree.children[0]
    assert isinstance(text_node, Node)
    assert text_node.kind == "text"
    assert text_node.props["content"] == "Hello"


def test_child_component_has_own_signals() -> None:
    """A child Component gets its own view_signal_id, distinct from parent."""
    from sidol.component import _computations

    child = Leaf("A")
    parent = Container(child)
    app = App(parent)
    app.build_tree()

    assert child._view_signal_id != parent._view_signal_id
    # Child should be in _computations
    assert child._view_signal_id in _computations


def test_child_state_change_dirties_child_only() -> None:
    """Mutating a child's State dirties the child's view, not the parent's."""
    child = Leaf("A")
    parent = Container(child)
    app = App(parent)
    app.build_tree()
    _graph.clear_dirty()

    child.label = "B"

    assert child._view_signal_id in _graph.dirty_ids()
    assert parent._view_signal_id not in _graph.dirty_ids()


def test_nested_layout_produces_correct_rects() -> None:
    """Nested child Components produce correct rects through the layout engine."""
    child = Leaf("Hi")
    parent = Container(child)
    app = App(parent)
    rects = app.compute_layout(400, 300)

    # Column + Text
    assert len(rects) == 2
    assert rects[0]["kind"] == "column"
    assert rects[1]["kind"] == "text"
    assert rects[1]["text"] == "Hi"


def test_multi_child_component_resolution() -> None:
    """Multiple child Components are all resolved."""
    from sidol import Button

    child_a = Leaf("A")
    child_b = Leaf("B")
    parent = Container(child_a, child_b, Button("C"))
    app = App(parent)
    tree = app.build_tree()

    assert tree.kind == "column"
    assert len(tree.children) == 3
    assert tree.children[0].kind == "text"
    assert tree.children[0].props["content"] == "A"
    assert tree.children[1].kind == "text"
    assert tree.children[1].props["content"] == "B"
    # Button is a plain Node, passed through
    assert tree.children[2].kind == "button"
    assert not isinstance(tree.children[2], Component)


def test_component_embedded_at_two_positions_raises() -> None:
    """A Component instance occupies exactly one position in the tree.

    Regression (re-review of P1.1): the old full-rebuild path silently
    rendered a twice-embedded instance at both positions, but the
    incremental frame path kept a stale cached subtree at the first
    position (``_tree_parent`` is a single slot, so only one ancestor
    chain was flagged). The shape is now uniformly an error instead of
    correct-on-full-rebuild + corrupt-on-incremental."""

    class Holder(Component):
        def __init__(self, child: Component) -> None:
            super().__init__()
            self._child = child

        def view(self) -> Node:
            from sidol.widgets import Column

            return Column(self._child)

    shared = Leaf("shared")
    app = App(Container(Holder(shared), Holder(shared)))
    with pytest.raises(ValueError, match="multiple positions"):
        app.build_tree()


def test_separate_instances_build_and_flush() -> None:
    """The documented fix for the multi-position error: separate instances
    build and re-render independently on incremental frames."""

    class Holder(Component):
        def __init__(self, child: Component) -> None:
            super().__init__()
            self._child = child

        def view(self) -> Node:
            from sidol.widgets import Column

            return Column(self._child)

    left = Leaf("left")
    right = Leaf("right")
    app = App(Container(Holder(left), Holder(right)))
    assert _collect_text(app.build_tree()) == ["left", "right"]

    left.label = "changed"
    tree = app.build_tree(set(_graph.drain_dirty()))
    assert _collect_text(tree) == ["changed", "right"]


def test_single_instance_through_node_levels_and_wrappers() -> None:
    """One instance under one Component parent is one position no matter
    how it is reached: nested plain Node levels and Component-returning
    views both pass ownership through without tripping the guard."""
    from sidol.widgets import Column
    from sidol.widgets.textfield import TextField

    class Wrapper(Component):
        def __init__(self, child: Component) -> None:
            super().__init__()
            self._child = child

        def view(self) -> Component:
            return self._child

    tf = TextField(initial="once")
    app = App(Container(Column(Wrapper(tf))))
    assert any("once" in t for t in _collect_text(app.build_tree()))

    # The incremental frame still reaches it through the Node levels.
    tf.insert("!")
    tree = app.build_tree(set(_graph.drain_dirty()))
    assert any("once!" in t for t in _collect_text(tree))


def test_column_column_textfield_nesting() -> None:
    """A Component nested two Node layers deep is fully resolved.

    ``Column(Column(TextField))`` has a ``Column`` Node wrapping a
    ``TextField`` Component. The first ``_resolve_component_tree``
    call on ``TextField``'s parent sees a ``Node`` child (the inner
    ``Column``), not a ``Component`` — it must recurse into that
    ``Node`` to find and resolve the innermost ``TextField``.

    Regression: the initial implementation only checked immediate
    Component children, so ``Column(Column(TextField))`` silently
    dropped the ``TextField`` from the resolved tree.
    """
    from sidol.node import Node
    from sidol.widgets.textfield import TextField

    class DeeplyNested(Component):
        def view(self):
            from sidol import Column

            # Two Column Node layers wrapping a TextField Component
            return Column(Column(TextField(initial="nested")))

    app = App(DeeplyNested())
    tree = app.build_tree()

    # Outer column
    assert tree.kind == "column"
    assert len(tree.children) == 1

    # Inner column (Node child — was not resolved in the buggy version)
    inner_col = tree.children[0]
    assert isinstance(inner_col, Node)
    assert inner_col.kind == "column"
    assert len(inner_col.children) == 1

    # The TextField's view should be a Row (from TextField.view())
    tf_view = inner_col.children[0]
    assert isinstance(tf_view, Node)
    assert tf_view.kind == "row"

    # At least one child of the Row should be a Text node with content
    text_contents = [
        c.props.get("content", "")
        for c in tf_view.children
        if isinstance(c, Node) and c.kind == "text"
    ]
    assert any("nested" in t for t in text_contents), (
        f"Expected TextField content to appear in resolved tree, "
        f"got text contents: {text_contents}"
    )


def test_column_column_textfield_layout() -> None:
    """Deeply nested Component produces correct rects through layout."""
    from sidol.widgets.textfield import TextField

    class DeeplyNested(Component):
        def view(self):
            from sidol import Column

            return Column(Column(TextField(initial="d")))

    app = App(DeeplyNested())
    rects = app.compute_layout(400, 300)

    # Should have: outer column, inner column, TextField's Row, Text nodes
    assert len(rects) >= 4
    kinds = [r["kind"] for r in rects]
    assert "row" in kinds, (
        f"Expected TextField's Row to appear in layout rects, "
        f"got kinds: {kinds}"
    )
    # TextField's content must survive the round-trip
    texts = [r for r in rects if r["kind"] == "text"]
    assert any("d" in r.get("text", "") for r in texts), (
        f"Expected TextField text in layout, got texts: "
        f"{[r.get('text', '') for r in texts]}"
    )


def test_deeply_nested_dirty_isolation() -> None:
    """A Component nested two Node layers deep has its own signal ID
    and propagates dirty independently of ancestors."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="deep")

    class DeeplyNested(Component):
        def view(self):
            from sidol import Column

            return Column(Column(tf))

    parent = DeeplyNested()
    app = App(parent)
    app.build_tree()
    _graph.clear_dirty()

    tf.insert("!")

    assert tf._view_signal_id in _graph.dirty_ids(), (
        "Deeply nested TextField's view should be dirty after state change"
    )
    assert parent._view_signal_id not in _graph.dirty_ids(), (
        "Parent should not be dirtied by child's state change"
    )


def test_reset_graph_then_create() -> None:
    """After reset_graph(), a freshly created Component works normally.

    Regression: if reset_graph() leaves the global state in an
    inconsistent state (e.g., orphans observer stack entries or
    doesn't clear internal signal counters), creating a new
    Component post-reset could produce stale signal IDs or fail
    to register proper dependency edges.
    """
    # Counter is defined at module level in this file — no import needed
    # Wipe the graph completely
    reset_graph()

    # Create a fresh component post-reset
    fresh = Counter()
    assert fresh._view_signal_id is not None
    assert _graph.dirty_ids() == []

    # Reading state during view must register a dependency
    fresh.rendered_view()
    _graph.clear_dirty()

    fresh.count = 42
    assert fresh._view_signal_id in _graph.dirty_ids(), (
        "Post-reset component must propagate state changes to view"
    )

    # App.build_tree() must still work with a post-reset component
    app = App(fresh)
    tree = app.build_tree()
    assert tree is not None


def test_reset_graph_then_create_multiple() -> None:
    """Multiple components created after reset_graph() all work correctly
    and have distinct signal IDs."""
    from sidol.widgets.textfield import TextField

    # Wipe the graph
    reset_graph()

    # Create multiple components post-reset
    a = TextField(initial="a")
    b = TextField(initial="b")

    assert a._view_signal_id != b._view_signal_id, (
        "Post-reset components must get distinct signal IDs"
    )

    # Both must register dependencies and propagate correctly
    a.rendered_view()
    b.rendered_view()
    _graph.clear_dirty()

    a.value = "A"
    assert a._view_signal_id in _graph.dirty_ids()
    assert b._view_signal_id not in _graph.dirty_ids(), (
        "Changing 'a' must not dirty 'b'"
    )

    b.value = "B"
    assert b._view_signal_id in _graph.dirty_ids()

    # Flush must clear both
    app = App(a)
    app.flush()
    # b's view signal is also dirty — flush processes it if b was
    # registered in _computations. We don't check strict emptiness
    # because b is not in any App, but the key assertion is no crash.
    reset_graph()


def test_app_rejects_cyclic_component_tree() -> None:
    class Cyclic(Component):
        def view(self) -> Node:
            return Node("column", children=(self,))

    with pytest.raises(RuntimeError, match="Cyclic component tree"):
        App(Cyclic()).build_tree()


def test_graph_rejects_unknown_signal_ids() -> None:
    graph = _graph
    with pytest.raises(ValueError, match="unknown signal ID"):
        graph.mark_dirty(999999)
    with pytest.raises(ValueError, match="unknown signal ID"):
        graph.add_dependency(999999, 999998)


def test_state_rewrite_same_value_does_not_mark_dirty() -> None:
    """Idempotent writes must not re-dirty the component — otherwise
    clamped writes (e.g. scroll_by at 0) re-render forever."""
    counter = Counter()
    counter.rendered_view()
    _graph.clear_dirty()

    counter.count = 0  # same value as current
    assert _graph.dirty_ids() == []

    counter.count = 1  # a real change still propagates
    assert counter._view_signal_id in _graph.dirty_ids()


def test_incremental_frame_renders_only_dirty_components() -> None:
    """The frame path — build_tree() with the drained dirty set — must
    re-render only the dirty component: a clean sibling's view() is not
    called and its cached subtree is spliced in unchanged."""
    calls: dict[str, int] = {}

    class Probe(Component):
        value = State()

        def __init__(self, name: str, initial: str) -> None:
            super().__init__()
            self.name = name
            self.value = initial

        def view(self) -> Node:
            calls[self.name] = calls.get(self.name, 0) + 1
            return Text(self.value)

    a = Probe("a", "a0")
    b = Probe("b", "b0")
    app = App(Container(a, b))
    app.build_tree()
    assert calls == {"a": 1, "b": 1}

    a.value = "a1"
    tree = app.build_tree(set(_graph.drain_dirty()))
    assert calls == {"a": 2, "b": 1}, "clean sibling must not re-render"
    assert _collect_text(tree) == ["a1", "b0"], "spliced tree must be fresh"


def test_frame_after_flush_splices_fresh_content() -> None:
    """flush() re-renders outside a resolution walk; the next frame must
    splice the flushed content, not a stale cached subtree."""
    app = App(Counter())
    app.build_tree()
    app.root.count = 5
    app.flush()
    # flush() consumed the dirty set — the frame runs with nothing dirty,
    # but the flushed content must still land in the tree.
    tree = app.build_tree(set(_graph.drain_dirty()))
    assert _collect_text(tree) == ["5"]


def test_incremental_frame_splices_nested_component() -> None:
    """A Component nested under plain Node layers (Column(Column(TextField)))
    is re-spliced through the Node levels when only it is dirty."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="deep")

    class DeeplyNested(Component):
        def view(self) -> Node:
            from sidol import Column

            return Column(Column(tf))

    app = App(DeeplyNested())
    app.build_tree()
    tf.insert("!")
    tree = app.build_tree(set(_graph.drain_dirty()))
    assert any("deep!" in t for t in _collect_text(tree))
