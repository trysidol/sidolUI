"""Leaf widgets — text labels and buttons.

Pure-data factory functions returning single-element Node trees.
on_click is a top-level Node field (not in props) so the render surface
can wire it into the event system without scanning props for callables.
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.node import Node
from sidol.theme import Style, get_theme, resolve_style


def Text(
    content: str,
    *,
    size: int | None = None,
    weight: str = "normal",
    fg: str | None = None,
    bg: str | None = None,
    on_key: dict[str, Callable[..., object]] | None = None,
    on_focus: Callable[..., object] | None = None,
) -> Node:
    theme = get_theme()
    resolved = resolve_style(theme, default_fg=theme.colors.text)
    return Node(
        kind="text",
        props={
            "content": content,
            "size": size if size is not None else theme.typography.size,
            "weight": weight,
            "fg": fg or resolved["color"],
            "bg": bg or resolved["bg"],
            "variant": "",
            "radius": resolved["radius"],
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
    resolved = resolve_style(
        theme,
        style,
        default_fg=theme.colors.primary,
        default_bg=theme.colors.surface,
        default_variant="filled",
        default_radius=6,
    )
    return Node(
        kind="button",
        props={
            "label": label,
            "disabled": disabled,
            "fg": fg or resolved["color"],
            "bg": bg or resolved["bg"],
            "variant": resolved["variant"],
            "radius": resolved["radius"],
        },
        on_click=on_click,
        on_key=on_key,
        on_focus=on_focus,
    )
