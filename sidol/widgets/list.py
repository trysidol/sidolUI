"""List — maps a data sequence into widgets via a builder.

``List`` is a factory function (like ``Row``/``Column``) that renders a
dynamic collection. Give items stable identity with ``key`` — a callable
extracting a key from each item. Stateful components built for the same
key are reused across renders, so their local state survives reorder,
add, and remove without manual ``.keyed()`` calls.

The parent owns the data: reading it inside ``view()`` registers the
dependency, so re-assigning it re-renders the list automatically.

Usage::

    from sidol.widgets.list import List

    class TodoApp(Component):
        todos = State()

        def __init__(self):
            super().__init__()
            self.todos = [{"id": 1, "text": "Buy milk"}]

        def view(self):
            return List(
                self.todos,
                key=lambda todo: todo["id"],
                builder=lambda todo, index: TodoItem(todo),
            )
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from sidol.component import Component
from sidol.widgets import Column, Text


def List(
    data: Iterable[Any] | None = None,
    *,
    builder: Callable[[Any, int], Any] | None = None,
    key: Callable[[Any], Any] | None = None,
) -> Column:
    """Render *data* into a Column using *builder* (item, index) -> widget.

    When *key* is provided, Component children are keyed with ``key(item)``
    so the reconciliation reuses them across renders (state preserved).
    Without *key*, each render builds fresh children. Keys must be unique
    within one render — duplicates raise.
    """
    builder = builder or _default_builder
    children: list[Any] = []
    for i, item in enumerate(data or []):
        child = builder(item, i)
        if child is None:
            continue
        if key is not None and isinstance(child, Component):
            child = child.keyed(key(item))
        children.append(child)
    return Column(*children)


def _default_builder(item: Any, _index: int) -> Any:
    if isinstance(item, str):
        return Text(item)
    if isinstance(item, (int, float)):
        return Text(str(item))
    return Text(repr(item))
