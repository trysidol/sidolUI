"""ScrollView — a constrained container for overflow content.

The current surfaces compute the constrained layout but do not yet expose a
scroll offset or wheel gesture API.

Usage::

    from sidol.widgets.scroll import ScrollView

    class MyList(Component):
        def view(self):
            return ScrollView(
                Column(
                    Text("Line 1"),
                    Text("Line 2"),
                    *[Text(f"Item {i}") for i in range(50)],
                ),
                max_h=200,
            )
"""

from __future__ import annotations

from sidol.node import Node


def ScrollView(
    *children: Node,
    max_w: int | None = None,
    max_h: int | None = None,
    min_w: int | None = None,
    min_h: int | None = None,
) -> Node:
    props: dict = {}
    if max_w is not None:
        props["max_w"] = float(max_w)
    if max_h is not None:
        props["max_h"] = float(max_h)
    if min_w is not None:
        props["min_w"] = float(min_w)
    if min_h is not None:
        props["min_h"] = float(min_h)
    return Node(kind="scroll_view", props=props, children=children)
