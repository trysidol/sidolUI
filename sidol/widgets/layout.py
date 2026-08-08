"""Layout primitives — containers that arrange their children.

Pure-data factory functions, not classes. Each returns a frozen Node with
a kind tag the render surface dispatches on: row, column, spacer.

Same model as SwiftUI's VStack/HStack/Spacer and Flutter's Row/Column/Expanded.
Flexbox via the `taffy` crate (Phase 1) provides the underlying layout engine.
"""

from __future__ import annotations

from sidol.node import Node


def Row(
    *children: Node,
    spacing: int = 0,
    min_w: int | None = None,
    min_h: int | None = None,
    max_w: int | None = None,
    max_h: int | None = None,
    padding: int = 0,
) -> Node:
    props: dict = {"spacing": spacing, "padding": padding}
    if min_w is not None:
        props["min_w"] = float(min_w)
    if min_h is not None:
        props["min_h"] = float(min_h)
    if max_w is not None:
        props["max_w"] = float(max_w)
    if max_h is not None:
        props["max_h"] = float(max_h)
    return Node(kind="row", props=props, children=children)


def Column(
    *children: Node,
    spacing: int = 0,
    min_w: int | None = None,
    min_h: int | None = None,
    max_w: int | None = None,
    max_h: int | None = None,
    padding: int = 0,
) -> Node:
    props: dict = {"spacing": spacing, "padding": padding}
    if min_w is not None:
        props["min_w"] = float(min_w)
    if min_h is not None:
        props["min_h"] = float(min_h)
    if max_w is not None:
        props["max_w"] = float(max_w)
    if max_h is not None:
        props["max_h"] = float(max_h)
    return Node(kind="column", props=props, children=children)


def Spacer() -> Node:
    return Node(kind="spacer")
