"""Widget behavior: TextField, ScrollView, List, Dropdown, Slider,
and style resolution."""

from __future__ import annotations

from _helpers import _collect_text, _key_event, _tui_step

from sidol.app import App
from sidol.component import Component, State, _graph
from sidol.node import Node
from sidol.widgets import Text


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


def test_scrollview_builds_tree() -> None:
    """ScrollView is a container node with scroll_view kind."""
    from sidol.widgets.scroll import ScrollView

    node = ScrollView(Text("content"), max_h=50).rendered_view()
    assert node.kind == "scroll_view"
    assert node.props["max_h"] == 50.0
    assert len(node.children) == 1
    assert node.props["scroll_x"] == 0
    assert node.props["scroll_y"] == 0


def test_scrollview_scroll_state_clamps_at_zero() -> None:
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
    from sidol.widgets import Column
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
    from sidol.widgets import Column
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
        surface, _key_event("down"), 0,
        button_callbacks={}, targets=targets,
    )
    assert sv.scroll_y == 1
    _tui_step(
        surface, _key_event("down"), 0,
        button_callbacks={}, targets=targets,
    )
    assert sv.scroll_y == 2
    _tui_step(
        surface, _key_event("up"), 0,
        button_callbacks={}, targets=targets,
    )
    assert sv.scroll_y == 1


def test_scrollview_layout_produces_rects() -> None:
    """ScrollView participates in layout as a container."""
    from sidol.widgets import Column
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


def test_list_renders_items() -> None:
    """List renders a builder for each item."""
    from sidol.widgets.list import List

    class Root(Component):
        def view(self) -> Node:
            return List(["a", "b", "c"], builder=lambda item, i: Text(f"{i}:{item}"))

    tree = App(Root()).build_tree()
    assert _collect_text(tree) == ["0:a", "1:b", "2:c"]


def test_list_reacts_to_data_change() -> None:
    """Reassigning the parent's State re-renders the List."""
    from sidol.widgets.list import List

    class Root(Component):
        data = State()

        def __init__(self) -> None:
            super().__init__()
            self.data = ["x"]

        def view(self) -> Node:
            return List(self.data, builder=lambda item, _i: Text(item))

    root = Root()
    app = App(root)
    tree = app.build_tree()
    assert _collect_text(tree) == ["x"]

    root.data = ["y", "z"]
    app.flush()
    tree = app.build_tree()
    assert _collect_text(tree) == ["y", "z"]


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


def test_resolve_style_precedence() -> None:
    from sidol.theme import Colors, Style, Theme, Typography, resolve_style

    theme = Theme(
        colors=Colors(primary="#111111", text="#222222"),
        typography=Typography(size=20),
    )
    defaults = resolve_style(
        theme,
        default_fg=theme.colors.primary,
        default_bg=theme.colors.surface,
        default_radius=6,
    )
    assert defaults["fg"] == "#111111"
    assert defaults["bg"] == "#FFFFFF"
    assert defaults["variant"] == "filled"
    assert defaults["radius"] == 6
    assert defaults["font_size"] == 20

    overridden = resolve_style(
        theme,
        Style(fg="#FF0000", radius=3, variant="ghost"),
        default_fg=theme.colors.primary,
        default_radius=6,
    )
    assert overridden["fg"] == "#FF0000"
    assert overridden["variant"] == "ghost"
    assert overridden["radius"] == 3


# ---------------------------------------------------------------------------
# Accessibility semantics (P4.1): role/name on the shared Node model,
# surfaced headlessly via App.semantic_tree().
# ---------------------------------------------------------------------------


def test_button_exposes_button_semantics() -> None:
    from sidol.widgets import Button

    node = Button("Save", on_click=lambda: None)
    assert node.role == "button"
    assert node.name == "Save"


def test_text_exposes_text_semantics() -> None:
    node = Text("hello")
    assert node.role == "text"
    assert node.name == "hello"


def test_textfield_exposes_textbox_semantics() -> None:
    from sidol.widgets.textfield import TextField

    labelled = TextField(label="Username", initial="x")
    node = labelled.rendered_view()
    assert node.role == "textbox"
    assert node.name == "Username"

    # No label → the field keeps its role but has no accessible name.
    unlabelled = TextField(initial="x")
    node = unlabelled.rendered_view()
    assert node.role == "textbox"
    assert node.name is None


def test_slider_exposes_slider_semantics() -> None:
    from sidol.widgets.slider import Slider

    node = Slider(min_val=0.0, max_val=10.0, value=4.0).rendered_view()
    assert node.role == "slider"
    assert node.name is None  # Slider has no label parameter


def test_dropdown_exposes_combobox_semantics() -> None:
    from sidol.widgets.dropdown import Dropdown

    labelled = Dropdown(["a", "b"], label="Theme")
    node = labelled.rendered_view()
    assert node.role == "combobox"
    assert node.name == "Theme"

    unlabelled = Dropdown(["a", "b"])
    node = unlabelled.rendered_view()
    assert node.role == "combobox"
    assert node.name is None


def test_containers_carry_no_semantics() -> None:
    """Row/Column/Spacer/ScrollView are presentation, not semantics —
    their role stays None so semantic_tree() splices through them."""
    from sidol.widgets import Column, Row, Spacer
    from sidol.widgets.scroll import ScrollView

    assert Row().role is None
    assert Column().role is None
    assert Spacer().role is None
    assert ScrollView(Column(Text("x"))).rendered_view().role is None


def test_semantic_tree_skips_roleless_containers() -> None:
    """The semantic tree answers "what does this UI mean": roleless
    containers contribute no entry; their semantic children splice into
    the nearest semantic ancestor, in pre-order."""
    from sidol.widgets import Button, Column, Row

    class Root(Component):
        def view(self) -> Node:
            return Column(Row(Button("-"), Button("+")), Text("done"))

    app = App(Root())
    assert app.semantic_tree() == [
        {"role": "button", "name": "-", "children": []},
        {"role": "button", "name": "+", "children": []},
        {"role": "text", "name": "done", "children": []},
    ]


def test_semantic_tree_nests_widget_internals() -> None:
    """A semantic node's own subtree nests inside it — the TextField's
    label and value texts hang off its textbox entry."""
    from sidol.widgets import Column
    from sidol.widgets.textfield import TextField

    class Form(Component):
        def __init__(self) -> None:
            super().__init__()
            self.field = TextField(label="Username", initial="ada")

        def view(self) -> Node:
            return Column(self.field)

    app = App(Form())
    assert app.semantic_tree() == [
        {
            "role": "textbox",
            "name": "Username",
            "children": [
                {"role": "text", "name": "Username", "children": []},
                {"role": "text", "name": "ada ", "children": []},
            ],
        },
    ]


def test_semantic_tree_of_counter_example() -> None:
    """The plan's P4.1 criterion, pinned against examples/counter.py:
    button roles + labels present, and the count text exposed."""
    import importlib.util
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "examples" / "counter.py"
    spec = importlib.util.spec_from_file_location("sidol_counter_example", example)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.app.semantic_tree() == [
        {"role": "text", "name": "Count: 0", "children": []},
        {"role": "button", "name": "-", "children": []},
        {"role": "button", "name": "+", "children": []},
    ]


def test_theme_change_needs_full_rebuild() -> None:
    """P1.1 narrowed semantics: set_theme() mid-run is NOT picked up by
    clean components on incremental frames (cached spliced subtree), but
    IS picked up on a full rebuild. This pins exactly what the code does,
    not what an ideal theme propagation would do."""
    from sidol.component import _graph
    from sidol.theme import Colors, Theme, get_theme, set_theme

    original = get_theme()
    try:

        class Themed(Component):
            def view(self) -> Node:
                # Text resolves fg from the current theme when view() runs;
                # caching the resolved Node is what makes the narrowed
                # semantics observable.
                return Text("pin", fg=get_theme().colors.text)

        comp = Themed()
        app = App(comp)
        # Full build establishes the cache with the original theme.
        tree_full_initial = app.build_tree()
        assert tree_full_initial.props["fg"] == original.colors.text
        # Theme change is NOT a signal write — no dirty signals appear.
        new_text = "#112233"
        set_theme(Theme(colors=Colors(text=new_text)))
        assert _graph.dirty_ids() == []
        # Incremental frame (the surface's per-frame path) splices the
        # cached subtree for the clean component — still the old colour.
        dirty = set(_graph.drain_dirty())
        tree_incremental = app.build_tree(dirty)
        assert tree_incremental.props["fg"] == original.colors.text
        # Full rebuild re-runs every view() and sees the new theme.
        tree_full_after = app.build_tree()
        assert tree_full_after.props["fg"] == new_text
        app.dispose()
    finally:
        set_theme(original)
