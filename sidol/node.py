"""Inert declarative tree nodes produced by Component.view().

A Node describes "what to draw" — just data, no methods, no behaviour.
The layout engine measures it, the render surface paints it. Neither
modifies the tree; it's a pure input rebuilt on every view() call.

Frozen + slots: immutable snapshots save ~65 bytes/node (650KB for 10k
nodes). Slots also prevent accidental typos from creating new attributes.

`key` identifies a Node snapshot. Stateful Components use
``Component.keyed(key)`` for stable identity during parent re-renders.

Children can be a mix of Node and Component references. When the App
resolves the tree (``build_tree``), each Component child is replaced
with its ``rendered_view()`` output, recursively.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sidol.component import Component


class FrozenDict(dict[str, Any]):
    """A dict-compatible mapping that cannot be changed after construction."""

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("Node props are immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("Node props are immutable")

    def clear(self) -> None:
        raise TypeError("Node props are immutable")

    def pop(self, key: str, default: Any = None) -> Any:
        raise TypeError("Node props are immutable")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("Node props are immutable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        raise TypeError("Node props are immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("Node props are immutable")

    def __ior__(self, other: object) -> FrozenDict:
        raise TypeError("Node props are immutable")


@dataclass(frozen=True, slots=True)
class Node:
    kind: str
    props: dict[str, Any] = field(default_factory=dict)
    children: tuple[Node | Component, ...] = ()
    on_click: Callable[[], None] | None = None
    on_key: dict[str, Callable[..., Any]] | None = None
    on_focus: Callable[..., Any] | None = None
    focusable: bool = False
    key: str | int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.props, FrozenDict):
            object.__setattr__(self, "props", FrozenDict(self.props))
