"""Layout FFI: taffy rects, constraints, theme colors, unicode text
sizing, and snapshot materialization."""

from __future__ import annotations

import pytest

from sidol.app import App
from sidol.component import Component
from sidol.node import Node
from sidol.widgets import Text


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


def test_button_radius_flows_through_layout() -> None:
    from sidol._sidol_core import compute_layout

    from sidol.theme import Style
    from sidol.widgets import Button

    def button_rect(node):
        return next(r for r in compute_layout(node, 200, 100) if r["kind"] == "button")

    assert button_rect(Button("Go"))["radius"] == 6.0
    assert button_rect(Button("Go", style=Style(radius=12)))["radius"] == 12.0


def test_layout_snapshot_materialises_dicts() -> None:
    """The snapshot handle carries the same data as the dict API."""
    from sidol._sidol_core import compute_layout, compute_layout_snapshot

    from sidol.widgets import Button, Column, Text

    tree = Column(Text("hi"), Button("go"), spacing=1)
    via_dicts = compute_layout(tree, 200.0, 100.0)
    snapshot = compute_layout_snapshot(tree, 200.0, 100.0)
    assert len(snapshot) == len(via_dicts)
    assert snapshot.to_dicts() == via_dicts
