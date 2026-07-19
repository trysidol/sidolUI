"""Inert declarative tree nodes produced by Component.view().

A Node describes "what to draw" — just data, no methods, no behaviour.
The layout engine measures it, the render surface paints it. Neither
modifies the tree; it's a pure input rebuilt on every view() call.

Frozen + slots: immutable snapshots save ~65 bytes/node (650KB for 10k
nodes). Slots also prevent accidental typos from creating new attributes.

`key` is reserved for future reconciliation (stable identity during list
re-rendering). Retrofitting it later would touch every widget call site.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Node:
    kind: str
    props: dict[str, Any] = field(default_factory=dict)
    children: tuple[Node, ...] = ()
    on_click: Callable[[], None] | None = None
    key: str | int | None = None
