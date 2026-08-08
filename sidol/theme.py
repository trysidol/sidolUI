"""Design tokens — colours, spacing, typography, and style resolution.

Resolution order for the current TUI and HTML surfaces:
  1. Per-widget Style override (if non-None).
  2. Active Theme default.
  3. Hardcoded engine fallback (last resort).

Widgets resolve visuals through ``resolve_style()`` — they never hardcode
raw colour/radius values. Composition (not flat dict) makes tokens
discoverable via autocomplete: theme.colors.primary, not
theme["colors.primary"].

Frozen dataclasses prevent accidental mutation of the global theme —
call set_theme() to swap, don't mutate in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class Colors:
    primary: str = "#0A84FF"
    danger: str = "#FF3B30"
    surface: str = "#FFFFFF"
    text: str = "#000000"


@dataclass(frozen=True, slots=True)
class Spacing:
    """Base spacing unit. Use ``scale`` for multiples of the rhythm."""

    unit: int = 4

    def scale(self, factor: int) -> int:
        return self.unit * factor


@dataclass(frozen=True, slots=True)
class Typography:
    size: int = 14
    family: str = "monospace"


@dataclass(frozen=True, slots=True)
class Theme:
    colors: Colors = field(default_factory=Colors)
    spacing: Spacing = field(default_factory=Spacing)
    typography: Typography = field(default_factory=Typography)


@dataclass(frozen=True, slots=True)
class Style:
    """Per-widget override. None = inherit from Theme."""

    variant: Literal["filled", "outline", "ghost"] | None = None
    color: str | None = None
    bg: str | None = None
    radius: int | None = None


_active_theme = Theme()


def set_theme(theme: Theme) -> None:
    global _active_theme
    _active_theme = theme


def get_theme() -> Theme:
    return _active_theme


def resolve_style(
    theme: Theme,
    style: Style | None = None,
    *,
    default_fg: str | None = None,
    default_bg: str | None = None,
    default_variant: str = "filled",
    default_radius: int = 0,
) -> dict[str, Any]:
    """Resolve a per-widget ``Style`` against ``theme``.

    Precedence is: explicit style value, then the widget's default, then the
    theme fallback. Returns concrete values for ``color``, ``bg``,
    ``variant``, ``radius``, and ``font_size``.
    """
    return {
        "color": (
            style.color
            if style and style.color is not None
            else (default_fg or theme.colors.text)
        ),
        "bg": (
            style.bg
            if style and style.bg is not None
            else (default_bg or "")
        ),
        "variant": (
            style.variant
            if style and style.variant is not None
            else default_variant
        ),
        "radius": (
            style.radius
            if style and style.radius is not None
            else default_radius
        ),
        "font_size": theme.typography.size,
    }
