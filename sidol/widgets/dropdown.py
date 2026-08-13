"""Dropdown — a select control that expands a list of options.

``Dropdown`` is a ``Component`` subclass with reactive state. It manages
its own open/close state and selection tracking, and is keyboard-operable
on the TUI surface: focus it with Tab, open with Enter/Space/Down, move
with Up/Down, commit with Enter, cancel with Esc.

Usage::

    from sidol.widgets.dropdown import Dropdown

    class SettingsForm(Component):
        theme_name = State()

        def __init__(self):
            super().__init__()
            self.theme_name = "light"
            self.dropdown = Dropdown(
                ["light", "dark", "system"],
                label="Theme",
                on_select=lambda i, value: setattr(self, "theme_name", value),
            )

        def view(self):
            return Column(
                self.dropdown,
                Text(f"Theme: {self.theme_name}"),
            )
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.component import Component, State
from sidol.events import FocusEvent
from sidol.theme import get_theme
from sidol.widgets.input import Text
from sidol.widgets.layout import Column, Row


class Dropdown(Component):
    selected = State()
    is_open = State()
    is_focused = State()

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
        self.is_focused = False

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
    # Keyboard interaction
    # ------------------------------------------------------------------

    def _key_handlers(self) -> dict[str, Callable[..., object]]:
        return {
            "enter": lambda event: self._commit_or_open(),
            " ": lambda event: self._commit_or_open(),
            "esc": lambda event: self.close(),
            "up": lambda event: self._move(-1),
            "down": lambda event: self._move(1),
        }

    def _commit_or_open(self) -> None:
        if not self.is_open:
            self.open()
        elif 0 <= self.selected < len(self._options):
            self.select(self.selected)
        else:
            # Nothing highlighted — Enter just closes.
            self.close()

    def _move(self, delta: int) -> None:
        if not self.is_open:
            self.open()
            return
        if not self._options:
            return
        if self.selected < 0:
            self.selected = 0 if delta > 0 else len(self._options) - 1
        else:
            self.selected = max(0, min(len(self._options) - 1, self.selected + delta))

    def _handle_focus(self, event: FocusEvent) -> None:
        self.is_focused = event.kind == "focus"

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def view(self) -> Row | Column:
        theme = get_theme()
        current = (
            self._options[self.selected]
            if 0 <= self.selected < len(self._options)
            else "Select..."
        )

        if not self.is_open:
            fg = theme.colors.primary if self.is_focused else theme.colors.text
            children: list = (
                [Text(self._label, fg=theme.colors.muted)] if self._label else []
            )
            children.append(Text(current, fg=fg))
            return Row(
                *children,
                spacing=1,
                on_key=self._key_handlers(),
                on_focus=self._handle_focus,
            )

        # Windowed option list — the highlight stays visible when the
        # selection moves past the first ``max_height`` options.
        count = len(self._options)
        start = 0
        if self.selected >= self._max_height:
            start = min(
                self.selected - self._max_height + 1,
                max(0, count - self._max_height),
            )
        rows: list = []
        for i in range(start, min(start + self._max_height, count)):
            prefix = "> " if i == self.selected else "  "
            color = theme.colors.primary if i == self.selected else theme.colors.text
            rows.append(Text(f"{prefix}{self._options[i]}", fg=color))

        parts = [Column(*rows)]
        if self._label:
            parts.insert(0, Text(self._label, fg=theme.colors.muted))
        return Column(
            *parts,
            on_key=self._key_handlers(),
            on_focus=self._handle_focus,
        )
