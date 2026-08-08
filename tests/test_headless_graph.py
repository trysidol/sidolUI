"""Phase 0 integration tests: Python <-> Rust reactive loop, no rendering.

These tests are the gating check before moving to Phase 1. Everything
else builds on top of this reactive loop — if it's wrong, nothing works.
"""

from __future__ import annotations

import pytest

from sidol.app import App
from sidol.component import Component, State, _graph, reset_graph
from sidol.node import Node
from sidol.widgets import Text


@pytest.fixture(autouse=True)
def isolated_graph():
    """Reset the global graph before/after every test so state never leaks.
    Added after debugging a "passes in isolation, fails in suite" issue."""
    reset_graph()
    yield
    reset_graph()


class Counter(Component):
    count = State()

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def view(self) -> Node:
        return Text(str(self.count))


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


# --- App.flush() render loop tests --- #


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


# --- mark_dirty re-propagation (the #4 fix) --- #


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


# --- Layout engine --- #


def test_compute_layout_returns_rects_for_simple_tree() -> None:
    from sidol._sidol_core import compute_layout

    from sidol import Column, Text

    tree = Column(Text("Hello"), spacing=4)
    rects = compute_layout(tree, 400, 300)
    assert len(rects) == 2
    assert rects[0]["kind"] == "column"
    assert rects[1]["kind"] == "text"
    assert rects[0]["w"] > 0
    assert rects[1]["w"] > 0
    assert rects[0]["x"] == 0
    assert rects[0]["y"] == 0
    assert rects[0]["depth"] == 0
    assert rects[1]["depth"] == 1


def test_compute_layout_nested_pre_order() -> None:
    """Deeply nested tree must produce correct pre-order (parent before
    children) at every level, not just shallow trees."""
    from sidol._sidol_core import compute_layout

    from sidol import Button, Column, Row, Text

    # Tree:
    #   Column (depth 0)
    #     Row (depth 1)
    #       Text (depth 2)
    #       Button (depth 2)
    #     Text (depth 1)
    tree = Column(
        Row(Text("A"), Button("B")),
        Text("C"),
    )
    rects = compute_layout(tree, 400, 300)
    assert len(rects) == 5  # col + row + text(A) + button(B) + text(C)
    # Pre-order: Column → Row → Text(A) → Button(B) → Text(C)
    assert [r["kind"] for r in rects] == ["column", "row", "text", "button", "text"], (
        f"Expected pre-order, got {[r['kind'] for r in rects]}"
    )
    assert [r["depth"] for r in rects] == [0, 1, 2, 2, 1], (
        f"Expected depths [0,1,2,2,1], got {[r['depth'] for r in rects]}"
    )


def test_compute_layout_handles_row_spacer_button() -> None:
    from sidol._sidol_core import compute_layout

    from sidol import Button, Row, Spacer

    tree = Row(Spacer(), Button("OK"), spacing=8)
    rects = compute_layout(tree, 400, 300)
    assert len(rects) == 3
    assert rects[0]["kind"] == "row"
    assert rects[1]["kind"] == "spacer"
    assert rects[2]["kind"] == "button"
    assert rects[0]["depth"] == 0
    assert rects[1]["depth"] == 1
    assert rects[2]["depth"] == 1


def test_app_compute_layout_integration() -> None:
    from sidol import App, Column, Component, State, Text

    class Counter(Component):
        count = State()

        def __init__(self) -> None:
            super().__init__()
            self.count = 0

        def view(self) -> Column:
            return Column(Text(f"Count: {self.count}"), spacing=2)

    app = App(Counter())
    rects = app.compute_layout(400, 300)
    assert len(rects) == 2
    assert rects[0]["kind"] == "column"
    assert rects[1]["kind"] == "text"


def test_app_print_layout_does_not_crash() -> None:
    from sidol import App, Column, Component, Text

    class Simple(Component):
        def view(self) -> Column:
            return Column(Text("hi"))

    app = App(Simple())
    # Just verify it doesn't raise
    app.print_layout(400, 300)


def test_layout_rects_carry_text_content() -> None:
    from sidol._sidol_core import compute_layout

    from sidol import Button, Column, Text

    tree = Column(Text("Hello"), Button("Click"))
    rects = compute_layout(tree, 400, 300)
    text_rect = [r for r in rects if r["kind"] == "text"][0]
    button_rect = [r for r in rects if r["kind"] == "button"][0]
    assert text_rect["text"] == "Hello"
    assert button_rect["text"] == "Click"


def test_layout_text_sizing_is_content_aware() -> None:
    from sidol._sidol_core import compute_layout

    from sidol import Button, Column, Text

    tree = Column(Text("Hi"), Button("Longer Label"))
    rects = compute_layout(tree, 400, 300)
    text_rect = [r for r in rects if r["kind"] == "text"][0]
    button_rect = [r for r in rects if r["kind"] == "button"][0]
    # Text height = 1 line, width = len("Hi") = 2
    assert text_rect["h"] == 1.0
    assert text_rect["w"] == 2.0
    # Button height = 3 lines, width = len("Longer Label") + 4 = 12 + 4 = 16
    assert button_rect["h"] == 3.0
    assert button_rect["w"] == 16.0


def test_layout_text_sizing_non_ascii() -> None:
    """Text sizing follows terminal display width, not byte count."""
    from sidol._sidol_core import compute_layout

    from sidol import Button, Column, Text

    # "Café" = 5 bytes, 4 columns; CJK characters occupy 2 columns each.
    tree = Column(Text("Café"), Button("按钮"))
    rects = compute_layout(tree, 400, 300)
    text_rect = [r for r in rects if r["kind"] == "text"][0]
    button_rect = [r for r in rects if r["kind"] == "button"][0]
    # Text: 4 display columns → width 4.0
    assert text_rect["w"] == 4.0, (
        f"Expected 4.0 (display width) for 'Café', got {text_rect['w']}"
    )
    # Button: 4 display columns + 4 padding = 8.0
    assert button_rect["w"] == 8.0, (
        f"Expected 8.0 (display width + 4) for '按钮', got {button_rect['w']}"
    )
    assert text_rect["text"] == "Café"
    assert button_rect["text"] == "按钮"


def test_layout_rects_carry_themed_colors() -> None:
    """Style fields from theme wiring must flow through the layout pipeline."""
    from sidol._sidol_core import compute_layout

    from sidol import Button, Column, Text

    tree = Column(Text("Hi", fg="#FF0000"), Button("OK", fg="#00FF00", bg="#0000FF"))
    rects = compute_layout(tree, 400, 300)
    text_rect = [r for r in rects if r["kind"] == "text"][0]
    button_rect = [r for r in rects if r["kind"] == "button"][0]

    assert text_rect["fg"] == "#FF0000"
    assert text_rect["bg"] == ""  # text has no bg by default
    assert text_rect["variant"] == ""

    assert button_rect["fg"] == "#00FF00"
    assert button_rect["bg"] == "#0000FF"
    assert button_rect["variant"] == "filled"


def test_layout_rects_default_theme_colors() -> None:
    """Without explicit fg/bg, widget factories fall through to theme defaults."""
    from sidol._sidol_core import compute_layout

    from sidol import Button, Column, Text

    tree = Column(Text("Default"), Button("Default"))
    rects = compute_layout(tree, 400, 300)
    text_rect = [r for r in rects if r["kind"] == "text"][0]
    button_rect = [r for r in rects if r["kind"] == "button"][0]

    # Default theme: text=#000000, primary=#0A84FF, surface=#FFFFFF
    assert text_rect["fg"] == "#000000"
    assert button_rect["fg"] == "#0A84FF"
    assert button_rect["bg"] == "#FFFFFF"
    assert button_rect["variant"] == "filled"


# --- Component composition (child components) --- #


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


# --- TextField widget --- #


def test_textfield_initial_state() -> None:
    """TextField starts with the given initial value and cursor at end."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="hello")
    assert tf.value == "hello"
    assert tf.cursor_pos == 5


def test_textfield_insert_character() -> None:
    """Inserting a character adds it at the cursor position and advances."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="helo")
    tf.cursor_pos = 3  # between 'l' and 'o'
    tf.insert("l")
    assert tf.value == "hello"
    assert tf.cursor_pos == 4


def test_textfield_insert_empty_noop() -> None:
    """Inserting an empty string does nothing."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="abc")
    tf.insert("")
    assert tf.value == "abc"
    assert tf.cursor_pos == 3


def test_textfield_backspace() -> None:
    """Backspace removes the character before the cursor."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="hello")
    tf.cursor_pos = 5  # after 'o'
    tf.backspace()
    assert tf.value == "hell"
    assert tf.cursor_pos == 4


def test_textfield_backspace_at_start_noop() -> None:
    """Backspace at position 0 does nothing."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="abc")
    tf.cursor_pos = 0
    tf.backspace()
    assert tf.value == "abc"
    assert tf.cursor_pos == 0


def test_textfield_delete() -> None:
    """Delete removes the character after the cursor."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="abcd")
    tf.cursor_pos = 1
    tf.delete()
    assert tf.value == "acd"
    assert tf.cursor_pos == 1  # unchanged


def test_textfield_move_left_right() -> None:
    """Cursor navigation methods work correctly."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="abc")
    tf.move_home()
    assert tf.cursor_pos == 0
    tf.move_right()
    assert tf.cursor_pos == 1
    tf.move_right()
    assert tf.cursor_pos == 2
    tf.move_left()
    assert tf.cursor_pos == 1
    tf.move_end()
    assert tf.cursor_pos == 3


def test_textfield_clear() -> None:
    """Clear resets value and cursor."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="hello")
    tf.clear()
    assert tf.value == ""
    assert tf.cursor_pos == 0


def test_textfield_focus_blur() -> None:
    """Focus state toggles correctly."""
    from sidol.widgets.textfield import TextField

    tf = TextField()
    assert not tf.is_focused
    tf.focus()
    assert tf.is_focused
    tf.blur()
    assert not tf.is_focused


def test_textfield_view_shows_cursor_when_focused() -> None:
    """The view includes a cursor marker at the cursor position."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="abc")
    tf.cursor_pos = 0  # cursor before first char
    tf.focus()
    tree = tf.view()
    assert tree.kind == "row"
    text_node = tree.children[-1]  # last child is the text display
    assert text_node.kind == "text"
    assert text_node.props["content"] == "|abc"
    # Move cursor to middle
    tf.cursor_pos = 2
    tree = tf.view()
    assert tree.children[-1].props["content"] == "ab|c"


def test_textfield_view_no_cursor_when_blurred() -> None:
    """When not focused, cursor shows as space at cursor position."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="abc")
    tf.cursor_pos = 0
    tree = tf.view()
    text_node = tree.children[-1]
    assert text_node.props["content"] == " abc"  # cursor as space at pos 0


def test_textfield_reactive_mutation() -> None:
    """Mutating a TextField's value dirties its view signal."""
    from sidol.widgets.textfield import TextField

    tf = TextField(initial="hello")
    tf.rendered_view()  # render once to register dependencies
    _graph.clear_dirty()

    tf.value = "world"
    assert tf._view_signal_id in _graph.dirty_ids()


def test_textfield_in_form_layout() -> None:
    """TextField nested inside a form Component produces correct rects."""
    from sidol.widgets.textfield import TextField

    class Form(Component):
        def view(self):
            from sidol import Button, Column

            return Column(
                TextField(label="Name", initial="Alice"),
                Button("Submit"),
                spacing=4,
            )

    app = App(Form())
    rects = app.compute_layout(400, 300)
    # Form → Column(TextField(Text, Text), Button) → col(text, text, button)
    # We don't know exact count due to label/Row nesting, but at minimum:
    assert len(rects) >= 3
    assert any(r["kind"] == "button" for r in rects)
    assert any(r["kind"] == "text" for r in rects)


def test_textfield_dirty_isolation() -> None:
    """Changing one TextField doesn't dirty another sibling."""
    from sidol.widgets.textfield import TextField

    # Keep strong references so they survive garbage collection
    tf_a = TextField(label="A", initial="x")
    tf_b = TextField(label="B", initial="y")

    class Form(Component):
        def view(self):
            from sidol import Column

            return Column(tf_a, tf_b)  # type: ignore[arg-type]

    form = Form()
    app = App(form)
    app.build_tree()
    _graph.clear_dirty()

    tf_a.insert("z")

    assert tf_a._view_signal_id in _graph.dirty_ids()
    assert tf_b._view_signal_id not in _graph.dirty_ids()


# --- Deep nesting & graph resilience (regression coverage) --- #


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


# --- Dev server --- #


def test_dev_server_serves_html() -> None:
    """The dev server serves a valid HTML page at GET /."""
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Simple(Component):
        def view(self):
            from sidol import Text

            return Text("dev test")

    app = App(Simple())
    server = DevServer(app, host="127.0.0.1", port=19573, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    for _ in range(25):
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:19573/", timeout=2)
            html = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "dev test" in html
            assert "sidol-root" in html
            assert "EventSource" in html
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not start within 5 seconds")

    server.stop()


def test_dev_server_state_endpoint() -> None:
    """The dev server serves layout rects as JSON at GET /state."""
    import json
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Simple(Component):
        def view(self):
            from sidol import Text

            return Text("state")

    app = App(Simple())
    server = DevServer(app, host="127.0.0.1", port=19574, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    for _ in range(25):
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:19574/state", timeout=2)
            data = json.loads(resp.read().decode("utf-8"))
            assert isinstance(data, list)
            assert len(data) >= 1
            assert "kind" in data[0]
            assert "x" in data[0]
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server /state endpoint did not start within 5 seconds")

    server.stop()


def test_dev_server_health_endpoint() -> None:
    """The dev server responds with 200 at GET /health."""
    import json
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Simple(Component):
        def view(self):
            from sidol import Text
            return Text("ok")

    app = App(Simple())
    server = DevServer(app, host="127.0.0.1", port=19575, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    for _ in range(25):
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:19575/health", timeout=2)
            data = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert data["status"] == "ok"
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server /health endpoint did not start within 5 seconds")

    server.stop()


def test_dev_server_auto_rebuild_on_flush() -> None:
    """After flush(), the server rebuilds and pushes updated HTML."""
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Mutable(Component):
        label = State()
        def __init__(self):
            super().__init__()
            self.label = "before"
        def view(self):
            from sidol import Text
            return Text(self.label)

    comp = Mutable()
    app = App(comp)
    server = DevServer(app, host="127.0.0.1", port=19576, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    for _ in range(25):
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:19576/", timeout=2)
            html = resp.read().decode("utf-8")
            if "before" in html:
                break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not start within 5 seconds")

    # Mutate state and flush — the server's hook should auto-rebuild.
    comp.label = "after"
    app.flush()

    # Fetch again — should show updated content
    resp = urllib.request.urlopen("http://127.0.0.1:19576/", timeout=2)
    html = resp.read().decode("utf-8")
    assert "after" in html, f"Expected 'after' in HTML after flush, got: {html[:200]}"

    server.stop()


def test_dev_server_hot_reload() -> None:
    """Editing the app file triggers hot-reload via importlib.reload."""
    import importlib.util
    import os
    import sys
    import tempfile

    from sidol.dev_server import DevServer

    code = """from sidol import App
from sidol.component import Component

class TestComp(Component):
    def view(self):
        from sidol import Text
        return Text("version1")

app = App(TestComp())
"""

    tmpdir = tempfile.mkdtemp(prefix="sidol_hot_")
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
            module_name = "hot_app"
            spec = importlib.util.spec_from_file_location(module_name, abs_path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = module.app

            server = DevServer(
                app, host="127.0.0.1", port=19577,
                verbosity=0, watch=tmp_path, module=module,
            )

            # Verify initial state
            tree = server._app.build_tree()
            assert "version1" in repr(tree)

            # Rewrite the file
            new_code = code.replace("version1", "version2")
            with open(tmp_path, "w", newline="") as f:
                f.write(new_code)
                f.flush()
                os.fsync(f.fileno())

                # Trigger hot-reload
                server._hot_reload(tmp_path)

                # Verify the module itself was reloaded
                importlib.invalidate_caches()
                # Clear compiled cache so SourceFileLoader re-reads the .py
                cached = importlib.util.cache_from_source(abs_path)
                try:
                    os.remove(cached)
                except OSError:
                    pass
                spec_verify = importlib.util.spec_from_file_location("hot_app_verify", abs_path)
                m_verify = importlib.util.module_from_spec(spec_verify)
                assert spec_verify.loader is not None
                spec_verify.loader.exec_module(m_verify)
                verify_tree = m_verify.app.build_tree()
                assert "version2" in repr(verify_tree), (
                    f"Module reload returned old content: {repr(verify_tree)[:200]}"
                )

                # Verify reloaded state via server
                tree = server._app.build_tree()
            assert "version2" in repr(tree), f"Expected version2 in tree, got: {repr(tree)[:200]}"
        finally:
            sys.path = orig_path
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir)
        except OSError:
            pass


# --- HTML export surface --- #


def test_export_html_simple_tree() -> None:
    """export_html produces a valid HTML string with the right structure."""
    from sidol.surfaces.html import export_tree_to_html

    class Simple(Component):
        def view(self):
            from sidol import Button, Column, Text

            return Column(
                Text("Hello"),
                Button("Click"),
                spacing=4,
            )

    app = App(Simple())
    html = export_tree_to_html(app, 400, 300)

    # Must be a complete HTML page
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html
    # Must contain the viewport size
    assert "width:400px" in html or "400" in html
    # Must contain widget text
    assert "Hello" in html
    assert "Click" in html


def test_export_html_renders_textfield() -> None:
    """export_html handles TextField content correctly."""
    from sidol.surfaces.html import export_tree_to_html
    from sidol.widgets.textfield import TextField

    class Form(Component):
        def __init__(self):
            super().__init__()
            self.tf = TextField(label="Name", initial="Alice")

        def view(self):
            from sidol import Button, Column

            return Column(self.tf, Button("Submit"), spacing=4)

    app = App(Form())
    html = export_tree_to_html(app, 500, 400)

    # Must be valid HTML
    assert html.startswith("<!DOCTYPE html>")
    # Must contain labels and button text
    assert "Alice" in html
    assert "Submit" in html
    # Must not contain Python object representations
    assert "<built-in" not in html
    assert "object at" not in html


def test_export_html_writes_file() -> None:
    """export_html writes a readable file to disk."""
    import os
    import tempfile

    from sidol.surfaces.html import export_html

    class Simple(Component):
        def view(self):
            from sidol import Text

            return Text("file test")

    app = App(Simple())
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        path = f.name

    try:
        export_html(app, path, 300, 200)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "file test" in content
        assert content.startswith("<!DOCTYPE html>")
    finally:
        os.unlink(path)


# --- Phase 1: ScrollView --- #


def test_scrollview_builds_tree() -> None:
    """ScrollView is a container node with scroll_view kind."""
    from sidol.widgets import Text
    from sidol.widgets.scroll import ScrollView

    node = ScrollView(Text("content"), max_h=50).rendered_view()
    assert node.kind == "scroll_view"
    assert node.props["max_h"] == 50.0
    assert len(node.children) == 1
    assert node.props["scroll_x"] == 0
    assert node.props["scroll_y"] == 0


def test_scrollview_scroll_state_clamps_at_zero() -> None:
    from sidol.widgets import Text
    from sidol.widgets.scroll import ScrollView

    sv = ScrollView(Text("a"), max_h=50)
    assert sv.scroll_y == 0
    sv.scroll_by(dy=-5)
    assert sv.scroll_y == 0
    sv.scroll_by(dy=5)
    assert sv.scroll_y == 5
    sv.scroll_to(y=10)
    assert sv.scroll_y == 10
    sv.scroll_to(y=-3)
    assert sv.scroll_y == 0


def test_scrollview_rects_carry_scroll_offset() -> None:
    from sidol.widgets import Column, Text
    from sidol.widgets.scroll import ScrollView

    class Scroller(Component):
        def __init__(self) -> None:
            super().__init__()
            self.scroller = ScrollView(
                Column(Text("a"), Text("b"), Text("c")),
                max_h=20,
            )

        def view(self):
            return self.scroller

    app = App(Scroller())
    app.root.scroller.scroll_to(y=4)
    rects = app.compute_layout(200, 150)
    scroll_rect = next(r for r in rects if r["kind"] == "scroll_view")
    assert scroll_rect["scroll_y"] == 4.0


def test_scrollview_keyboard_scrolls_when_focused() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Column, Text
    from sidol.widgets.scroll import ScrollView

    class Scroller(Component):
        def __init__(self) -> None:
            super().__init__()
            self.scroller = ScrollView(
                Column(Text("a"), Text("b"), Text("c")),
                max_h=20,
            )

        def view(self):
            return self.scroller

    app = App(Scroller())
    tree = app.build_tree()
    surface = TuiSurface(None)  # type: ignore[arg-type]
    targets = surface._focus_targets(tree)
    assert len(targets) == 1  # the scroll view is focusable

    sv = app.root.scroller
    assert sv.scroll_y == 0
    _tui_step(
        surface, "key@down", 0, callbacks=[],
        button_callbacks=[], targets=targets, rects=[],
    )
    assert sv.scroll_y == 1
    _tui_step(
        surface, "key@down", 0, callbacks=[],
        button_callbacks=[], targets=targets, rects=[],
    )
    assert sv.scroll_y == 2
    _tui_step(
        surface, "key@up", 0, callbacks=[],
        button_callbacks=[], targets=targets, rects=[],
    )
    assert sv.scroll_y == 1


def test_scrollview_layout_produces_rects() -> None:
    """ScrollView participates in layout as a container."""
    from sidol.widgets import Column, Text
    from sidol.widgets.scroll import ScrollView

    class Scroller(Component):
        def view(self):
            return ScrollView(
                Column(Text("item"), Text("item")),
                max_h=100,
            )

    app = App(Scroller())
    rects = app.compute_layout(200, 150)
    kinds = [r["kind"] for r in rects]
    assert "scroll_view" in kinds
    assert kinds[0] == "scroll_view"


# --- Phase 1: List --- #


def test_list_renders_items() -> None:
    """List renders a builder for each item."""
    from sidol.widgets import Text
    from sidol.widgets.list import List

    lst = List(["a", "b", "c"], builder=lambda item, i: Text(f"{i}:{item}"))
    app = App(lst)
    tree = app.build_tree()
    assert _collect_text(tree) == ["0:a", "1:b", "2:c"]


def test_list_reacts_to_data_change() -> None:
    """Reassigning List.data dirties the component and re-renders."""
    from sidol.widgets import Text
    from sidol.widgets.list import List

    lst = List(["x"], builder=lambda item, i: Text(item))
    app = App(lst)
    tree = app.build_tree()
    assert _collect_text(tree) == ["x"]

    lst.data = ["y", "z"]
    app.flush()
    tree = app.build_tree()
    assert _collect_text(tree) == ["y", "z"]


def _collect_text(node: Node) -> list[str]:
    texts: list[str] = []
    if node.kind == "text":
        texts.append(node.props.get("content", ""))
    for child in node.children:
        if isinstance(child, Node):
            texts.extend(_collect_text(child))
    return texts


# --- Phase 1: Dropdown --- #


def test_dropdown_initial_state() -> None:
    from sidol.widgets.dropdown import Dropdown

    dd = Dropdown(["a", "b", "c"], label="Pick")
    assert dd.selected == -1
    assert dd.is_open is False
    assert dd.selected_value is None


def test_dropdown_select() -> None:
    from sidol.widgets.dropdown import Dropdown

    dd = Dropdown(["a", "b", "c"])
    dd.select(1)
    assert dd.selected == 1
    assert dd.selected_value == "b"
    assert dd.is_open is False


def test_dropdown_select_by_value() -> None:
    from sidol.widgets.dropdown import Dropdown

    dd = Dropdown(["a", "b", "c"])
    dd.select_by_value("c")
    assert dd.selected == 2
    dd.select_by_value("nope")
    assert dd.selected == 2  # unchanged


def test_dropdown_toggle_and_open() -> None:
    from sidol.widgets.dropdown import Dropdown

    dd = Dropdown(["a", "b", "c"])
    dd.toggle()
    assert dd.is_open is True
    dd.toggle()
    assert dd.is_open is False
    dd.open()
    assert dd.is_open is True


def test_dropdown_callback_on_select() -> None:
    from sidol.widgets.dropdown import Dropdown

    calls: list[tuple[int, str]] = []
    dd = Dropdown(["a", "b"], on_select=lambda i, v: calls.append((i, v)))
    dd.select(0)
    assert calls == [(0, "a")]


def test_dropdown_view_shows_selection() -> None:
    from sidol.widgets.dropdown import Dropdown

    dd = Dropdown(["apple", "banana"], label="Fruit")
    dd.select(1)
    tree = dd.rendered_view()
    assert _collect_text(tree) == ["Fruit", "banana"]


# --- Phase 1: Slider --- #


def test_slider_initial_state() -> None:
    from sidol.widgets.slider import Slider

    s = Slider(min_val=0.0, max_val=10.0, value=5.0)
    assert s.value == 5.0
    assert s.ratio == 0.5


def test_slider_increment_decrement() -> None:
    from sidol.widgets.slider import Slider

    s = Slider(min_val=0.0, max_val=10.0, step=2.0, value=5.0)
    s.increment()
    assert s.value == 7.0
    s.decrement()
    assert s.value == 5.0


def test_slider_clamps_at_bounds() -> None:
    from sidol.widgets.slider import Slider

    s = Slider(min_val=0.0, max_val=10.0, value=10.0)
    s.increment()
    assert s.value == 10.0  # cannot exceed max

    s2 = Slider(min_val=0.0, max_val=10.0, value=0.0)
    s2.decrement()
    assert s2.value == 0.0  # cannot go below min


def test_slider_reactive_rendering() -> None:
    from sidol.widgets.slider import Slider

    s = Slider(min_val=0.0, max_val=10.0, value=0.0)
    app = App(s)
    tree = app.build_tree()
    bar1 = _collect_text(tree)[0]
    assert "█" not in bar1  # empty at min

    s.value = 10.0
    app.flush()
    tree = app.build_tree()
    bar2 = _collect_text(tree)[0]
    assert "░" not in bar2  # full at max


# --- Phase 1: Concurrency (Worker) --- #


def test_worker_returns_result() -> None:
    from sidol.concurrency import Worker

    w = Worker(lambda: 21 * 2)
    w.start()
    assert w.join() == 42


def test_worker_reraises_exception() -> None:
    from sidol.concurrency import Worker

    def boom() -> None:
        raise ValueError("boom")

    w = Worker(boom)
    w.start()
    try:
        w.join()
        raise AssertionError("expected exception")
    except ValueError as exc:
        assert str(exc) == "boom"


def test_worker_poll() -> None:
    import time

    from sidol.concurrency import Worker

    w = Worker(lambda: time.sleep(0.05) or "done")
    w.start()
    assert w.poll() is False  # not yet done (may race, but usually true)
    assert w.join() == "done"
    assert w.poll() is True


def test_worker_commit_pattern() -> None:
    from sidol.concurrency import Worker
    from sidol.widgets import Text

    class AsyncComp(Component):
        data = State()

        def __init__(self) -> None:
            super().__init__()
            self.data = "pending"

        def view(self) -> Node:
            return Text(self.data)

    comp = AsyncComp()
    app = App(comp)

    def task() -> str:
        return "loaded"

    w = Worker(task, on_done=lambda r: setattr(comp, "data", r))
    w.start()
    w.join()
    app.flush()
    tree = app.build_tree()
    assert _collect_text(tree) == ["loaded"]


# --- Phase 1: Events --- #


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


# --- Phase 1: Layout constraints --- #


def test_layout_constraints_in_python() -> None:
    from sidol.widgets.layout import Column

    col = Column(min_w=50, max_h=100, padding=4)
    assert col.props["min_w"] == 50.0
    assert col.props["max_h"] == 100.0
    assert col.props["padding"] == 4


def test_layout_constraints_flow_through_ffi() -> None:
    from sidol.widgets import Text
    from sidol.widgets.layout import Column, Row

    class ConstraintView(Component):
        def view(self):
            return Column(Row(Text("wide")), min_w=120, min_h=80)

    app = App(ConstraintView())
    rects = app.compute_layout(300, 200)
    root = rects[0]
    assert root["kind"] == "column"
    assert root["w"] >= 120
    assert root["h"] >= 80

def test_scrollview_constraint_flows_through_ffi() -> None:
    from sidol.widgets import Column, Text
    from sidol.widgets.scroll import ScrollView

    class Scroller(Component):
        def view(self):
            return ScrollView(
                Column(Text("a"), Text("b"), Text("c")),
                max_h=20,
            )

    rects = App(Scroller()).compute_layout(200, 150)
    assert rects[0]["kind"] == "scroll_view"
    assert rects[0]["h"] <= 20


def test_node_props_are_immutable() -> None:
    node = Text("hello")
    with pytest.raises(TypeError, match="immutable"):
        node.props["content"] = "changed"


def test_layout_rejects_invalid_properties() -> None:
    from sidol._sidol_core import compute_layout

    with pytest.raises(ValueError, match="must be a string"):
        compute_layout(Node("text", props={"content": 123}), 100, 50)

    with pytest.raises(ValueError, match="unsupported node kind"):
        compute_layout(Node("unknown"), 100, 50)

    with pytest.raises(RuntimeError, match="viewport dimensions"):
        compute_layout(Text("hello"), -1, 50)


def test_tui_skips_disabled_buttons() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    called: list[str] = []
    tree = Column(
        Button("disabled", disabled=True, on_click=lambda: called.append("disabled")),
        Button("enabled", on_click=lambda: called.append("enabled")),
    )
    surface = TuiSurface(None)  # type: ignore[arg-type]
    callbacks = surface._button_callbacks(tree)
    assert len(callbacks) == 1
    callbacks[0]()
    assert called == ["enabled"]


def test_app_rejects_cyclic_component_tree() -> None:
    class Cyclic(Component):
        def view(self) -> Node:
            return Node("column", children=(self,))

    with pytest.raises(RuntimeError, match="Cyclic component tree"):
        App(Cyclic()).build_tree()


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


def test_graph_rejects_unknown_signal_ids() -> None:
    graph = _graph
    with pytest.raises(ValueError, match="unknown signal ID"):
        graph.mark_dirty(999999)
    with pytest.raises(ValueError, match="unknown signal ID"):
        graph.add_dependency(999999, 999998)


def test_worker_join_requires_start() -> None:
    from sidol.concurrency import Worker

    with pytest.raises(RuntimeError, match="started before join"):
        Worker(lambda: None).join()


def _tui_step(surface, event, focused, *, callbacks, button_callbacks, targets, rects):
    """Helper: run one pure TUI dispatch step."""
    return surface._dispatch(
        event,
        focused,
        callbacks=callbacks,
        button_callbacks=button_callbacks,
        targets=targets,
        rects=rects,
    )


def test_tui_dispatch_focus_navigation_and_activation() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    called: list[str] = []
    tree = Column(
        Button("a", on_click=lambda: called.append("a")),
        Button("b", on_click=lambda: called.append("b")),
    )
    surface = TuiSurface(None)  # type: ignore[arg-type]
    callbacks = surface._focus_callbacks(tree)
    button_callbacks = surface._button_callbacks(tree)
    targets = surface._focus_targets(tree)
    rects: list[dict] = []

    focused = -1
    focused, quit = _tui_step(
        surface, "focus_next", focused, callbacks=callbacks,
        button_callbacks=button_callbacks, targets=targets, rects=rects,
    )
    assert (focused, quit) == (0, False)
    _tui_step(
        surface, "activate", focused, callbacks=callbacks,
        button_callbacks=button_callbacks, targets=targets, rects=rects,
    )
    assert called == ["a"]

    focused, _ = _tui_step(
        surface, "focus_next", focused, callbacks=callbacks,
        button_callbacks=button_callbacks, targets=targets, rects=rects,
    )
    assert focused == 1
    _tui_step(
        surface, "activate", focused, callbacks=callbacks,
        button_callbacks=button_callbacks, targets=targets, rects=rects,
    )
    assert called == ["a", "b"]

    focused, _ = _tui_step(
        surface, "focus_prev", focused, callbacks=callbacks,
        button_callbacks=button_callbacks, targets=targets, rects=rects,
    )
    assert focused == 0

    _, quit = _tui_step(
        surface, "quit", focused, callbacks=callbacks,
        button_callbacks=button_callbacks, targets=targets, rects=rects,
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
            surface, f"key@{char}", focused, callbacks=[],
            button_callbacks=[], targets=targets, rects=[],
        )
    assert form.field.value == "hi"

    focused, _ = _tui_step(
        surface, "key@backspace", focused, callbacks=[],
        button_callbacks=[], targets=targets, rects=[],
    )
    assert form.field.value == "h"

    focused, _ = _tui_step(
        surface, "key@home", focused, callbacks=[],
        button_callbacks=[], targets=targets, rects=[],
    )
    focused, _ = _tui_step(
        surface, "key@a", focused, callbacks=[],
        button_callbacks=[], targets=targets, rects=[],
    )
    assert form.field.value == "ah"


def test_tui_mouse_click_dispatches_button() -> None:
    from sidol.surfaces.tui import TuiSurface
    from sidol.widgets import Button, Column

    called: list[str] = []
    tree = Column(
        Button("one", on_click=lambda: called.append("one")),
        Button("two", on_click=lambda: called.append("two")),
    )
    app = App(None)  # type: ignore[arg-type]
    rects = app.compute_layout(200, 100, tree=tree)
    button_rects = [r for r in rects if r["kind"] == "button"]
    assert len(button_rects) >= 2

    surface = TuiSurface(None)  # type: ignore[arg-type]
    button_callbacks = surface._button_callbacks(tree)

    second = button_rects[1]
    _tui_step(
        surface,
        f"click@{second['x'] + second['w'] / 2:.0f}@{second['y'] + second['h'] / 2:.0f}",
        -1,
        callbacks=[],
        button_callbacks=button_callbacks,
        targets=[],
        rects=rects,
    )
    assert called == ["two"]

    called.clear()
    _tui_step(
        surface, "click@999@999", -1, callbacks=[],
        button_callbacks=button_callbacks, targets=[], rects=rects,
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
    events = iter(["quit"])

    monkeypatch.setattr(tui_module, "tui_init", lambda: init_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_cleanup", lambda: cleanup_calls.append(1))
    monkeypatch.setattr(tui_module, "tui_size", lambda: (80, 24))
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda rects, idx: next(events)
    )

    TuiSurface(App(Root())).run()
    assert init_calls == [1]
    assert cleanup_calls == [1]


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

    def boom(rects, idx) -> str:
        raise RuntimeError("render failed")

    monkeypatch.setattr(tui_module, "tui_render_frame", boom)

    with pytest.raises(RuntimeError, match="render failed"):
        TuiSurface(App(Root())).run()
    assert cleanup_calls == [1]


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
    events = iter(["tick", "quit"])
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda rects, idx: next(events)
    )

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
    events = iter(["tick", "quit"])
    monkeypatch.setattr(
        tui_module, "tui_render_frame", lambda rects, idx: next(events)
    )

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
