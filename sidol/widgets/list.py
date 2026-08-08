"""List widget — renders a dynamic collection of items via a builder.

``List`` is a ``Component`` subclass that maps a data sequence into
widgets using a builder callback. When the data changes, re-assign
it and call ``flush()`` — the list re-renders automatically.

Usage::

    from sidol.widgets.list import List

    class TodoApp(Component):
        todos = State()

        def __init__(self):
            super().__init__()
            self.todos = ["Buy milk", "Walk dog"]

        def view(self):
            return List(self.todos, builder=lambda item, index: Text(item))
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from sidol.component import Component, State
from sidol.widgets import Column, Text


class List(Component):
    items = State()

    def __init__(
        self,
        data: Iterable[Any] | None = None,
        *,
        builder: Callable[[Any, int], Any] | None = None,
    ) -> None:
        super().__init__()
        self.items = list(data or [])
        self._builder = builder or _default_builder

    def view(self) -> Column:
        children: list[Any] = []
        for i, item in enumerate(self.items):
            child = self._builder(item, i)
            if child is not None:
                children.append(child)
        return Column(*children)

    @property
    def data(self) -> list[Any]:
        return self.items

    @data.setter
    def data(self, value: list[Any]) -> None:
        self.items = list(value)


def _default_builder(item: Any, _index: int) -> Any:

    if isinstance(item, str):
        return Text(item)
    if isinstance(item, (int, float)):
        return Text(str(item))
    return Text(repr(item))
