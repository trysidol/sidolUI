"""Design tokens — colours, spacing, radii, and per-widget style overrides.

Resolution order (not yet wired — no render surface exists):
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
class Radius:
    sm: int = 8
    md: int = 14
    lg: int = 20


@dataclass(frozen=True, slots=True)
class Spacing:
    unit: int = 4


@dataclass(frozen=True, slots=True)
class Theme:
    colors: Colors = field(default_factory=Colors)
    radius: Radius = field(default_factory=Radius)
    spacing: Spacing = field(default_factory=Spacing)


@dataclass(frozen=True, slots=True)
class Style:
    """Per-widget override. None = inherit from Theme."""

    variant: Literal["filled", "outline", "ghost"] | None = None
    color: str | None = None
    bg: str | None = None
    corner_radius: int | None = None
    haptic: bool = False


_active_theme = Theme()


def set_theme(theme: Theme) -> None:
    global _active_theme
    _active_theme = theme


def get_theme() -> Theme:
    return _active_theme
