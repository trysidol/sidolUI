"""Leaf widgets — text labels and buttons.

Pure-data factory functions returning single-element Node trees.
on_click is a top-level Node field (not in props) so the render surface
can wire it into the event system without scanning props for callables.
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.node import Node
from sidol.theme import Style, get_theme


def Text(
    content: str,
    *,
    size: int = 14,
    weight: str = "normal",
    fg: str | None = None,
    bg: str | None = None,
    on_key: dict[str, Callable[..., object]] | None = None,
    on_focus: Callable[..., object] | None = None,
) -> Node:
    theme = get_theme()
    return Node(
        kind="text",
        props={
            "content": content,
            "size": size,
            "weight": weight,
            "fg": fg or theme.colors.text,
            "bg": bg or "",
            "variant": "",
        },
        on_key=on_key,
        on_focus=on_focus,
    )


def Button(
    label: str,
    *,
    on_click: Callable[[], None] | None = None,
    style: Style | None = None,
    disabled: bool = False,
    fg: str | None = None,
    bg: str | None = None,
    on_key: dict[str, Callable[..., object]] | None = None,
    on_focus: Callable[..., object] | None = None,
) -> Node:
    theme = get_theme()
    resolved_fg = fg or (style.color if style else None) or theme.colors.primary
    resolved_bg = bg or (style.bg if style else None) or theme.colors.surface
    variant = (style.variant if style else None) or "filled"
    return Node(
        kind="button",
        props={
            "label": label,
            "disabled": disabled,
            "fg": resolved_fg,
            "bg": resolved_bg,
            "variant": variant,
        },
        on_click=on_click,
        on_key=on_key,
        on_focus=on_focus,
    )
