"""Shared test helpers: components and event factories used by more
than one test module. Imported as ``from _helpers import ...``
(pytest's default import mode puts tests/ on sys.path)."""

from __future__ import annotations

from sidol.component import Component, State
from sidol.node import Node
from sidol.widgets import Text


class Counter(Component):
    count = State()

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def view(self) -> Node:
        return Text(str(self.count))


def _collect_text(node: Node) -> list[str]:
    texts: list[str] = []
    if node.kind == "text":
        texts.append(node.props.get("content", ""))
    for child in node.children:
        if isinstance(child, Node):
            texts.extend(_collect_text(child))
    return texts


def _key_event(
    key: str, *, ctrl: bool = False, alt: bool = False, shift: bool = False
) -> dict:
    """Build a structured key event matching the engine's FFI protocol."""
    return {"type": "key", "key": key, "ctrl": ctrl, "alt": alt, "shift": shift}


def _click_event(x: float, y: float) -> dict:
    return {"type": "click", "x": x, "y": y}


def _tui_step(surface, event, focused, *, button_callbacks, targets, snapshot=None, root=None):
    """Helper: run one pure TUI dispatch step."""
    return surface._dispatch(
        event,
        focused,
        button_callbacks=button_callbacks,
        targets=targets,
        snapshot=snapshot,
        root=root,
    )
