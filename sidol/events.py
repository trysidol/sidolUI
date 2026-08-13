"""Component-level event handling — keyboard and focus events.

``on_key`` and ``on_focus`` Node fields let widgets declare event
handlers without subclassing Component. The TUI render surface
dispatches events to the focused widget, then to the root node as an
app-level fallback.

Usage::

    def view(self):
        return Text("Press ESC to go back", on_key={"esc": self._go_back})

``on_key`` maps canonical key names to callables. Special keys: "esc",
"enter", "tab", "backtab", "up", "down", "left", "right", "backspace",
"delete", "home", "end", "pageup", "pagedown". Printable characters are
the character itself with case preserved ("a", "A", "@", "1"). The
wildcard ``"*"`` handler receives any printable character without an
explicit binding — text inputs use this instead of enumerating keys.
"""

from __future__ import annotations

from dataclasses import dataclass

# Key name → standardised label for the event map.
_KEY_ALIASES: dict[str, str] = {
    "escape": "esc",
    "return": "enter",
    "space": " ",
    "bs": "backspace",
    "del": "delete",
    "arrow_up": "up",
    "arrow_down": "down",
    "arrow_left": "left",
    "arrow_right": "right",
}


def normalise_key(name: str) -> str:
    """Convert a key label (e.g. 'escape', 'arrow_up') to its canonical form.

    Single printable characters keep their case — "A" and "a" are
    different keys. Only multi-character names are lowercased/aliased.
    """
    if len(name) == 1:
        return name
    return _KEY_ALIASES.get(name.lower(), name.lower())


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A keyboard event dispatched to the focused widget."""

    key: str
    """Canonical key name (printable chars keep their case)."""

    ctrl: bool = False
    alt: bool = False
    shift: bool = False


@dataclass(frozen=True, slots=True)
class FocusEvent:
    """Dispatched when a widget gains or loses keyboard focus."""

    kind: str  # "focus" or "blur"
    widget_id: str | None = None
