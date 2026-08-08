"""Component-level event handling — keyboard and focus events.

``on_key`` and ``on_focus`` Node fields let widgets declare event
handlers without subclassing Component. The TUI render surface
dispatches events to the focused widget.

Usage::

    from sidol.events import on_key

    def view(self):
        return Text("Press ESC to quit", on_key={"esc": self._handle_esc})

``on_key`` accepts a dict mapping key names to callables. Supported
keys: "esc", "enter", "tab", "up", "down", "left", "right", "backspace",
"delete", "home", "end", plus any single-character string like "a", "1".
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
    """Convert a key label (e.g. 'escape', 'arrow_up') to its canonical form."""
    return _KEY_ALIASES.get(name.lower(), name.lower())


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A keyboard event dispatched to the focused widget."""

    key: str
    """Canonical key name (lowercase, normalised)."""

    ctrl: bool = False
    alt: bool = False
    shift: bool = False


@dataclass(frozen=True, slots=True)
class FocusEvent:
    """Dispatched when a widget gains or loses keyboard focus."""

    kind: str  # "focus" or "blur"
    widget_id: str | None = None
