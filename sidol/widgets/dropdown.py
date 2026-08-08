"""Dropdown — a select control that expands a list of options.

``Dropdown`` is a ``Component`` subclass with reactive state. It manages
its own open/close state and selection tracking.

Usage::

    from sidol.widgets.dropdown import Dropdown

    class SettingsForm(Component):
        theme = State()

        def __init__(self):
            super().__init__()
            self.theme = "light"
            self.dropdown = Dropdown(
                ["light", "dark", "system"],
                label="Theme",
            )

        def view(self):
            # Synchronise selection
            self.dropdown.select_by_value(self.theme)
            return Column(
                self.dropdown,
                Button("Apply", on_click=self._apply),
            )

        def _apply(self):
            self.theme = self.dropdown.selected_value
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.component import Component, State
from sidol.widgets.input import Text
from sidol.widgets.layout import Column, Row


class Dropdown(Component):
    selected = State()
    is_open = State()

    def __init__(
        self,
        options: list[str] | None = None,
        *,
        label: str = "",
        max_height: int = 5,
        on_select: Callable[[int, str], None] | None = None,
    ) -> None:
        super().__init__()
        self._options: list[str] = list(options or [])
        self._label = label
        self._max_height = max_height
        self._on_select_cb = on_select
        self.selected = -1
        self.is_open = False

    def set_options(self, items: list[str]) -> None:
        self._options = list(items)
        if self.selected >= len(self._options):
            self.selected = -1

    def toggle(self) -> None:
        self.is_open = not self.is_open

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def select(self, index: int) -> None:
        if 0 <= index < len(self._options):
            self.selected = index
            self.is_open = False
            if self._on_select_cb is not None:
                self._on_select_cb(index, self._options[index])

    def select_by_value(self, value: str) -> None:
        try:
            idx = self._options.index(value)
            self.selected = idx
        except ValueError:
            pass

    @property
    def selected_value(self) -> str | None:
        if 0 <= self.selected < len(self._options):
            return self._options[self.selected]
        return None

    @property
    def options(self) -> list[str]:
        return list(self._options)

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def view(self) -> Row | Column:
        current = (
            self._options[self.selected]
            if 0 <= self.selected < len(self._options)
            else "Select..."
        )
        display = Text(current)

        if not self.is_open:
            children: list = [Text(self._label)] if self._label else []
            children.append(display)
            return Row(*children, spacing=1)

        rows: list = []
        for i, opt in enumerate(self._options[: self._max_height]):
            prefix = "> " if i == self.selected else "  "
            color = "#0A84FF" if i == self.selected else "#000000"
            rows.append(Text(f"{prefix}{opt}", fg=color))

        content = Column(*rows)
        parts = [content]
        if self._label:
            parts.insert(0, Text(self._label))
        return Column(*parts)
