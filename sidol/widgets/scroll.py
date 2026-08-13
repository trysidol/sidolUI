"""ScrollView — a scrollable container for overflow content.

A stateful ``Component`` that clips overflow and scrolls its children.
Focus it in the TUI (Tab) and use the up/down arrow keys to scroll. The
scroll offset is reactive state, so re-renders reflect the current position.

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

from collections.abc import Callable

from sidol.component import Component, State
from sidol.node import Node


class ScrollView(Component):
    """Scrollable container. Focus it and use up/down to scroll."""

    scroll_x = State()
    scroll_y = State()

    def __init__(
        self,
        *children: Node,
        max_w: int | None = None,
        max_h: int | None = None,
        min_w: int | None = None,
        min_h: int | None = None,
        step: int = 1,
    ) -> None:
        super().__init__()
        self._children: tuple[Node, ...] = children
        self._max_w = max_w
        self._max_h = max_h
        self._min_w = min_w
        self._min_h = min_h
        self._step = step
        self.scroll_x = 0
        self.scroll_y = 0

    def scroll_by(self, dx: int = 0, dy: int = 0) -> None:
        """Scroll by an offset, clamped at zero."""
        self.scroll_x = max(0, self.scroll_x + dx)
        self.scroll_y = max(0, self.scroll_y + dy)

    def scroll_to(self, x: int = 0, y: int = 0) -> None:
        """Scroll to an absolute offset, clamped at zero."""
        self.scroll_x = max(0, x)
        self.scroll_y = max(0, y)

    def _key_handlers(self) -> dict[str, Callable[..., object]]:
        return {
            "up": lambda event: self.scroll_by(dy=-self._step),
            "down": lambda event: self.scroll_by(dy=self._step),
        }

    def view(self) -> Node:
        props: dict = {"scroll_x": self.scroll_x, "scroll_y": self.scroll_y}
        if self._max_w is not None:
            props["max_w"] = float(self._max_w)
        if self._max_h is not None:
            props["max_h"] = float(self._max_h)
        if self._min_w is not None:
            props["min_w"] = float(self._min_w)
        if self._min_h is not None:
            props["min_h"] = float(self._min_h)
        return Node(
            kind="scroll_view",
            props=props,
            children=self._children,
            on_key=self._key_handlers(),
            focusable=True,
        )
