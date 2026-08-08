"""Terminal UI render surface — keyboard-driven event loop.

Extracts the TUI-specific concerns from ``App.run()`` into a dedicated
surface class. The ``TuiSurface`` owns the terminal lifecycle (init,
render loop, cleanup), focus navigation, callback dispatch, and optional
hot-reload when watching app source files.

``App.run(surface=TuiSurface(app))`` delegates to this class.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from sidol._sidol_core import (
    tui_cleanup,
    tui_init,
    tui_render_frame,
    tui_size,
)
from sidol.app import App
from sidol.events import FocusEvent, KeyEvent, normalise_key
from sidol.node import Node


class TuiSurface:
    """Keyboard-driven terminal event loop.

    Usage::

        from sidol.surfaces.tui import TuiSurface
        TuiSurface(app).run()

    When *watch* paths are provided, the loop polls them on every tick and
    calls *reloader* when one changes. The reloader returns a replacement
    ``App`` (or ``None`` to keep the current one), which is swapped in for
    the next frame.
    """

    def __init__(
        self,
        app: App,
        *,
        watch: str | list[str] | None = None,
        reloader: Callable[[str], App | None] | None = None,
    ) -> None:
        self._app = app
        self._watch_paths = [watch] if isinstance(watch, str) else (watch or [])
        self._reloader = reloader
        self._last_mtimes: dict[str, float] = {}

    def run(self) -> None:
        """Enter the TUI event loop. Blocks until the user quits."""
        tui_init()
        try:
            viewport_w, viewport_h = tui_size()
            focused_idx: int = -1  # index into buttons-only list (0..n-1), -1 = none
            while True:
                viewport_w, viewport_h = tui_size()
                self._app.flush()
                tree = self._app.build_tree()
                rects = self._app.compute_layout(
                    float(viewport_w), float(viewport_h), tree=tree
                )
                callbacks = self._focus_callbacks(tree)
                button_callbacks = self._button_callbacks(tree)
                targets = self._focus_targets(tree)
                focus_rects = self._focus_rect_indices(tree)
                rect_focused = (
                    focus_rects[focused_idx]
                    if 0 <= focused_idx < len(focus_rects)
                    else -1
                )
                event = tui_render_frame(rects, rect_focused)
                focused_idx, quit = self._dispatch(
                    event,
                    focused_idx,
                    callbacks=callbacks,
                    button_callbacks=button_callbacks,
                    targets=targets,
                    rects=rects,
                )
                if quit:
                    break
                if self._watch_paths:
                    new_app = self._maybe_reload()
                    if new_app is not None and new_app is not self._app:
                        self._app = new_app
                        focused_idx = -1
        finally:
            tui_cleanup()

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def _maybe_reload(self) -> App | None:
        """Poll watched files; on a change, ask the reloader for a new App."""
        for path in self._watch_paths:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            previous = self._last_mtimes.get(path)
            if previous is None:
                self._last_mtimes[path] = mtime
                continue
            if mtime == previous:
                continue
            self._last_mtimes[path] = mtime
            return self._reload(path)
        return None

    def _reload(self, path: str) -> App | None:
        if self._reloader is None:
            return None
        new_app = self._reloader(path)
        if new_app is not None and new_app is not self._app:
            print(
                f"[sidol] reloaded after change to {os.path.basename(path)}",
                file=sys.stderr,
                flush=True,
            )
        return new_app

    def _dispatch(
        self,
        event: str,
        focused_idx: int,
        *,
        callbacks: list[Callable[[], None] | None],
        button_callbacks: list[Callable[[], None] | None],
        targets: list[Node],
        rects: list[dict],
    ) -> tuple[int, bool]:
        """Handle one terminal event string. Returns ``(focused_idx, quit)``.

        Pure dispatch — no terminal IO — so the event loop is testable with
        simulated event strings.
        """
        if event == "quit":
            return focused_idx, True
        if event == "focus_next":
            next_idx = self._next_button(focused_idx, len(callbacks))
            return self._change_focus(focused_idx, next_idx, targets), False
        if event == "focus_prev":
            previous_idx = self._prev_button(focused_idx, len(callbacks))
            return self._change_focus(focused_idx, previous_idx, targets), False
        if event == "activate":
            if 0 <= focused_idx < len(callbacks):
                cb = callbacks[focused_idx]
                if cb is not None:
                    cb()
            return focused_idx, False
        if event.startswith("click@"):
            self._handle_click(event, rects, button_callbacks)
            return focused_idx, False
        if event.startswith("key@") and 0 <= focused_idx < len(targets):
            key = normalise_key(event[4:])
            handler = (targets[focused_idx].on_key or {}).get(key)
            if handler is not None:
                handler(KeyEvent(key))
        return focused_idx, False

    # ------------------------------------------------------------------
    # Focus navigation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _button_rect_index(rects: list[dict], button_idx: int) -> int:
        """Return the index in *rects* for the *button_idx*-th button (0-based)."""
        if button_idx < 0:
            return -1
        count = 0
        for i, r in enumerate(rects):
            if r["kind"] == "button" and not r.get("disabled", False):
                if count == button_idx:
                    return i
                count += 1
        return -1

    @staticmethod
    def _next_button(current: int, total: int) -> int:
        """Cyclic next: advance the button index, wrapping to first."""
        if total == 0:
            return -1
        if current < 0:
            return 0
        return (current + 1) % total

    @staticmethod
    def _prev_button(current: int, total: int) -> int:
        """Cyclic previous: step back the button index, wrapping to last."""
        if total == 0:
            return -1
        if current <= 0:
            return total - 1
        return current - 1

    def _handle_click(
        self,
        event: str,
        rects: list[dict],
        callbacks: list[Callable[[], None] | None],
    ) -> None:
        """Handle a mouse click event. Event format: ``click@{col}@{row}``.

        Hit-tests the click coordinates against all rects (last-to-first to
        respect z-order), and dispatches the button's ``on_click`` callback
        if a button was clicked.
        """
        parts = event.split("@")
        if len(parts) != 3:
            return
        try:
            click_x = float(parts[1])
            click_y = float(parts[2])
        except ValueError:
            return

        button_indices = {
            index: callback_index
            for callback_index, index in enumerate(
                i
                for i, rect in enumerate(rects)
                if rect["kind"] == "button" and not rect.get("disabled", False)
            )
        }
        # Walk rects in reverse so topmost (last-drawn) elements win.
        for i in range(len(rects) - 1, -1, -1):
            r = rects[i]
            rx, ry = r["x"], r["y"]
            rw, rh = r["w"], r["h"]
            if rx <= click_x < rx + rw and ry <= click_y < ry + rh:
                if r["kind"] == "button" and not r.get("disabled", False):
                    callback_index = button_indices.get(i)
                    if callback_index is not None and callback_index < len(callbacks):
                        cb = callbacks[callback_index]
                        if cb is not None:
                            cb()
                    return
                break

    def _button_callbacks(self, root: Node) -> list[Callable[[], None] | None]:
        """Collect button callbacks in pre-order (same order as rects).

        Returns a list with one entry per button. Entries are None for
        buttons without an ``on_click`` handler, so the list index
        matches the button index in the rect list.
        """
        callbacks: list[Callable[[], None] | None] = []

        def walk(node: Node) -> None:
            if node.kind == "button":
                if not node.props.get("disabled", False):
                    callbacks.append(node.on_click)
            for child in node.children:
                walk(child)

        walk(root)
        return callbacks

    def _focus_targets(self, root: Node) -> list[Node]:
        """Collect enabled buttons and nodes with keyboard/focus handlers."""
        targets: list[Node] = []

        def walk(node: Node) -> None:
            enabled_button = node.kind == "button" and not node.props.get("disabled", False)
            if enabled_button or node.on_key is not None or node.on_focus is not None:
                targets.append(node)
            for child in node.children:
                if isinstance(child, Node):
                    walk(child)

        walk(root)
        return targets

    def _focus_callbacks(self, root: Node) -> list[Callable[[], None] | None]:
        return [node.on_click for node in self._focus_targets(root)]

    @staticmethod
    def _focus_rect_indices(root: Node) -> list[int]:
        indices: list[int] = []
        rect_index = 0

        def walk(node: Node) -> None:
            nonlocal rect_index
            enabled_button = node.kind == "button" and not node.props.get("disabled", False)
            if enabled_button or node.on_key is not None or node.on_focus is not None:
                indices.append(rect_index)
            rect_index += 1
            for child in node.children:
                if isinstance(child, Node):
                    walk(child)

        walk(root)
        return indices

    @staticmethod
    def _change_focus(old: int, new: int, targets: list[Node]) -> int:
        if old == new:
            return new
        if 0 <= old < len(targets) and targets[old].on_focus is not None:
            targets[old].on_focus(FocusEvent("blur", _widget_id(targets[old])))
        if 0 <= new < len(targets) and targets[new].on_focus is not None:
            targets[new].on_focus(FocusEvent("focus", _widget_id(targets[new])))
        return new


def _widget_id(node: Node) -> str | None:
    return str(node.key) if node.key is not None else None
