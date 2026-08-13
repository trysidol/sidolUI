"""Stateful text input widget — an example of Component composition.

TextField is a ``Component`` subclass with its own ``State`` fields
(value, cursor_pos). It demonstrates how child Components enable
reusable stateful widgets that were impossible before component
composition.

Usage::

    from sidol.widgets.textfield import TextField

    class LoginForm(Component):
        def view(self):
            return Column(
                self.username,
                self.password,
                Button("Login"),
            )

        def __init__(self):
            super().__init__()
            self.username = TextField(label="Username")
            self.password = TextField(label="Password")
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.component import Component, State
from sidol.events import FocusEvent, KeyEvent
from sidol.theme import get_theme
from sidol.widgets import Row, Text


class TextField(Component):
    """A text input field with cursor position and editing controls.

    State:
        value (str): The current text content.
        cursor_pos (int): Index into ``value`` where the cursor sits
            (0 = before first char, len(value) = after last char).
        is_focused (bool): Whether this field is the active keyboard target.
    """

    value = State()
    cursor_pos = State()
    is_focused = State()

    def __init__(self, label: str = "", initial: str = "") -> None:
        super().__init__()
        self.label = label
        self.value = initial
        self.cursor_pos = len(initial)
        self.is_focused = False

    # ------------------------------------------------------------------
    # Public editing API
    # ------------------------------------------------------------------

    def insert(self, char: str) -> None:
        """Insert a character at the cursor position."""
        if not char:
            return
        pos = self.cursor_pos
        self.value = self.value[:pos] + char + self.value[pos:]
        self.cursor_pos = pos + 1

    def backspace(self) -> None:
        """Delete the character before the cursor."""
        if self.cursor_pos <= 0:
            return
        pos = self.cursor_pos
        self.value = self.value[: pos - 1] + self.value[pos:]
        self.cursor_pos = pos - 1

    def delete(self) -> None:
        """Delete the character after the cursor."""
        if self.cursor_pos >= len(self.value):
            return
        pos = self.cursor_pos
        self.value = self.value[:pos] + self.value[pos + 1 :]

    def move_left(self) -> None:
        """Move cursor one position left."""
        self.cursor_pos = max(0, self.cursor_pos - 1)

    def move_right(self) -> None:
        """Move cursor one position right."""
        self.cursor_pos = min(len(self.value), self.cursor_pos + 1)

    def move_home(self) -> None:
        """Move cursor to the start of the text."""
        self.cursor_pos = 0

    def move_end(self) -> None:
        """Move cursor to the end of the text."""
        self.cursor_pos = len(self.value)

    def clear(self) -> None:
        """Clear all text and reset cursor."""
        self.value = ""
        self.cursor_pos = 0

    def focus(self) -> None:
        """Gain keyboard focus."""
        self.is_focused = True

    def blur(self) -> None:
        """Lose keyboard focus."""
        self.is_focused = False

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def view(self) -> Row:
        """Render the text field as a labelled row with cursor indicator."""
        display = self.value
        pos = self.cursor_pos

        # Insert a cursor marker (|) at the cursor position
        if self.is_focused:
            marked = display[:pos] + "|" + display[pos:]
        else:
            # Show cursor as a space when not focused
            marked = display[:pos] + " " + display[pos:]

        theme = get_theme()
        label_part = Text(self.label, fg=theme.colors.muted) if self.label else None

        children = []
        if label_part is not None:
            children.append(label_part)
        children.append(
            Text(
                marked,
                fg=theme.colors.text,
                on_key=self._key_handlers(),
                on_focus=self._handle_focus,
            )
        )

        return Row(*children, spacing=1)

    def _key_handlers(self) -> dict[str, Callable[..., object]]:
        return {
            "backspace": lambda event: self.backspace(),
            "delete": lambda event: self.delete(),
            "left": lambda event: self.move_left(),
            "right": lambda event: self.move_right(),
            "home": lambda event: self.move_home(),
            "end": lambda event: self.move_end(),
            # Wildcard: any printable character without an explicit binding
            # is inserted verbatim — case and symbols included. The surface
            # only routes unmodified characters here (ctrl/alt combos skip
            # the wildcard), so 'q' inserts text instead of quitting.
            "*": self._insert_char,
        }

    def _insert_char(self, event: KeyEvent) -> None:
        self.insert(event.key)

    def _handle_focus(self, event: FocusEvent) -> None:
        if event.kind == "focus":
            self.focus()
        else:
            self.blur()
