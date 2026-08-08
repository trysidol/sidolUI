"""Design tokens — colours and per-widget style overrides.

Resolution order for the current TUI and HTML surfaces:
  1. Per-widget Style override (if non-None).
  2. Active Theme default.
  3. Hardcoded engine fallback (last resort).

Widgets never hardcode raw colour/radius values; they always go through
this token system. Composition (not flat dict) makes tokens discoverable
via autocomplete: theme.colors.primary, not theme["colors.primary"].

Frozen dataclasses prevent accidental mutation of the global theme —
call set_theme() to swap, don't mutate in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class Colors:
    primary: str = "#0A84FF"
    danger: str = "#FF3B30"
    surface: str = "#FFFFFF"
    text: str = "#000000"


@dataclass(frozen=True, slots=True)
class Theme:
    colors: Colors = field(default_factory=Colors)


@dataclass(frozen=True, slots=True)
class Style:
    """Per-widget override. None = inherit from Theme."""

    variant: Literal["filled", "outline", "ghost"] | None = None
    color: str | None = None
    bg: str | None = None


_active_theme = Theme()


def set_theme(theme: Theme) -> None:
    global _active_theme
    _active_theme = theme


def get_theme() -> Theme:
    return _active_theme
