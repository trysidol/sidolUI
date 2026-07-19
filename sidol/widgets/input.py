"""Leaf widgets — text labels and buttons.

Pure-data factory functions returning single-element Node trees.
on_click is a top-level Node field (not in props) so the render surface
can wire it into the event system without scanning props for callables.
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.node import Node
from sidol.theme import Style


def Text(content: str, *, size: int = 14, weight: str = "normal") -> Node:
    return Node(
        kind="text",
        props={"content": content, "size": size, "weight": weight},
    )


def Button(
    label: str,
    *,
    on_click: Callable[[], None] | None = None,
    style: Style | None = None,
    disabled: bool = False,
) -> Node:
    return Node(
        kind="button",
        props={"label": label, "style": style, "disabled": disabled},
        on_click=on_click,
    )
