"""Layout primitives — containers that arrange their children.

Pure-data factory functions, not classes. Each returns a frozen Node with
a kind tag the render surface dispatches on: row, column, spacer.

Same model as SwiftUI's VStack/HStack/Spacer and Flutter's Row/Column/Expanded.
Flexbox via the `taffy` crate (Phase 1) provides the underlying layout engine.
"""

from __future__ import annotations

from sidol.node import Node


def Row(*children: Node, spacing: int = 0) -> Node:
    return Node(kind="row", props={"spacing": spacing}, children=children)


def Column(*children: Node, spacing: int = 0) -> Node:
    return Node(kind="column", props={"spacing": spacing}, children=children)


def Spacer() -> Node:
    return Node(kind="spacer")
