"""Terminal UI render surface — keyboard-driven event loop.

Extracts the TUI-specific concerns from ``App.run()`` into a dedicated
surface class. The ``TuiSurface`` owns the terminal lifecycle (init,
render loop, cleanup), focus navigation, callback dispatch, and optional
hot-reload when watching app source files.

The loop is dirty-gated: it only rebuilds the tree, recomputes layout,
and redraws when the reactive graph holds dirty signals, the terminal
was resized, or the app was hot-reloaded. Idle frames block in
``tui_wait_event`` — no rebuild, no layout, no paint.

Surface policy (kept here, not in the engine):
  - Ctrl+C quits. Everything else is dispatched: focused widget first,
    then the root node's ``on_key`` as an app-level fallback.
  - Tab / Shift+Tab move focus; Enter/Space activate the focused widget.
  - ``App.request_quit()`` (e.g. from a root key binding) exits the loop.

``App.run(surface=TuiSurface(app))`` delegates to this class.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from sidol._sidol_core import (
    compute_layout_snapshot,
    tui_cleanup,
    tui_init,
    tui_render_frame,
    tui_size,
    tui_wait_event,
)
from sidol.app import App
from sidol.component import _graph
from sidol.concurrency import pump_workers
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
            focused_idx: int = -1  # index into the focus-target list, -1 = none
            need_render = True
            tree: Node | None = None
            snapshot = None
            targets: list[Node] = []
            button_callbacks: list[Callable[[], None] | None] = []
            focus_rects: list[int] = []
            while True:
                if need_render:
                    viewport_w, viewport_h = tui_size()
                    tree = self._app.build_tree()
                    snapshot = compute_layout_snapshot(
                        tree, float(viewport_w), float(viewport_h)
                    )
                    # The full rebuild consumed every dirty signal; a clean
                    # graph is what gates the next rebuild.
                    _graph.clear_dirty()
                    targets = self._focus_targets(tree)
                    button_callbacks = self._button_callbacks(tree)
                    focus_rects = self._focus_rect_indices(tree)
                    rect_focused = (
                        focus_rects[focused_idx]
                        if 0 <= focused_idx < len(focus_rects)
                        else -1
                    )
                    event = tui_render_frame(snapshot, rect_focused)
                else:
                    event = tui_wait_event()
                pump_workers()
                focused_idx, quit = self._dispatch(
                    event,
                    focused_idx,
                    button_callbacks=button_callbacks,
                    targets=targets,
                    snapshot=snapshot,
                    root=tree,
                )
                if quit or self._app._quit_requested:
                    break
                swapped = False
                if self._watch_paths:
                    new_app = self._maybe_reload()
                    if new_app is not None and new_app is not self._app:
                        self._app = new_app
                        focused_idx = -1
                        swapped = True
                need_render = (
                    swapped
                    or event.get("type") == "resize"
                    or bool(_graph.dirty_ids())
                )
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
        event: dict,
        focused_idx: int,
        *,
        button_callbacks: list[Callable[[], None] | None],
        targets: list[Node],
        snapshot,
        root: Node | None,
    ) -> tuple[int, bool]:
        """Handle one terminal event dict. Returns ``(focused_idx, quit)``.

        Pure dispatch — no terminal IO — so the event loop is testable with
        simulated event dicts.
        """
        event_type = event.get("type")
        if event_type == "key":
            return self._dispatch_key(
                event, focused_idx, targets=targets, root=root
            )
        if event_type == "click" and snapshot is not None:
            self._handle_click(event, snapshot, button_callbacks)
        # tick / resize carry no dispatch work; the loop reacts to them.
        return focused_idx, False

    def _dispatch_key(
        self,
        event: dict,
        focused_idx: int,
        *,
        targets: list[Node],
        root: Node | None,
    ) -> tuple[int, bool]:
        key = normalise_key(str(event.get("key", "")))
        ctrl = bool(event.get("ctrl"))
        alt = bool(event.get("alt"))
        shift = bool(event.get("shift"))

        # Surface-level policy: Ctrl+C quits. All other keys dispatch.
        if ctrl and key == "c":
            return focused_idx, True

        if not ctrl and not alt:
            if key == "tab":
                next_idx = self._next_target(focused_idx, len(targets))
                return self._change_focus(focused_idx, next_idx, targets), False
            if key == "backtab":
                previous_idx = self._prev_target(focused_idx, len(targets))
                return self._change_focus(focused_idx, previous_idx, targets), False

        key_event = KeyEvent(key, ctrl=ctrl, alt=alt, shift=shift)
        target = targets[focused_idx] if 0 <= focused_idx < len(targets) else None
        if target is not None:
            handler = (target.on_key or {}).get(key)
            if handler is None and self._is_printable(key, ctrl, alt):
                handler = (target.on_key or {}).get("*")
            if handler is not None:
                handler(key_event)
                return focused_idx, False
            if key in ("enter", " ") and target.on_click is not None:
                target.on_click()
                return focused_idx, False

        # App-level fallback: bindings on the root node (e.g. "q" to quit).
        if root is not None:
            root_handler = (root.on_key or {}).get(key)
            if root_handler is None and self._is_printable(key, ctrl, alt):
                root_handler = (root.on_key or {}).get("*")
            if root_handler is not None:
                root_handler(key_event)
        return focused_idx, False

    @staticmethod
    def _is_printable(key: str, ctrl: bool, alt: bool) -> bool:
        """Single-character keys without ctrl/alt are text input candidates."""
        return len(key) == 1 and not ctrl and not alt

    # ------------------------------------------------------------------
    # Focus navigation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _next_target(current: int, total: int) -> int:
        """Cyclic next: advance the focus-target index, wrapping to first."""
        if total == 0:
            return -1
        if current < 0:
            return 0
        return (current + 1) % total

    @staticmethod
    def _prev_target(current: int, total: int) -> int:
        """Cyclic previous: step back the focus-target index, wrapping to last."""
        if total == 0:
            return -1
        if current <= 0:
            return total - 1
        return current - 1

    def _handle_click(
        self,
        event: dict,
        snapshot,
        button_callbacks: list[Callable[[], None] | None],
    ) -> None:
        """Dispatch a mouse click to the topmost button under the cursor.

        Rects are materialised from the layout snapshot and translated
        into screen coordinates with the same scroll-offset logic the
        renderer uses — a scrolled ScrollView's children are hit-tested
        where they are drawn, not where they were laid out.
        """
        click_x = float(event["x"])
        click_y = float(event["y"])
        rects = snapshot.to_dicts()
        geometry = self._screen_geometry(rects)

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
            draw_x, draw_y, clipped = geometry[i]
            if clipped:
                continue
            if draw_x <= click_x < draw_x + r["w"] and draw_y <= click_y < draw_y + r["h"]:
                if r["kind"] == "button" and not r.get("disabled", False):
                    callback_index = button_indices.get(i)
                    if callback_index is not None and callback_index < len(
                        button_callbacks
                    ):
                        cb = button_callbacks[callback_index]
                        if cb is not None:
                            cb()
                    return
                break

    @staticmethod
    def _screen_geometry(rects: list[dict]) -> list[tuple[float, float, bool]]:
        """Translate layout rects to screen coordinates.

        Returns ``(draw_x, draw_y, clipped)`` per rect, mirroring the
        scroll-viewport logic in the Rust renderer (``render_frame``):
        children of a scroll view are drawn at their layout position
        minus the cumulative scroll offset of their ancestors, and
        children outside a viewport are clipped. Keep in sync with
        ``src/render/mod.rs``.
        """
        # Each stack entry: [depth, x, y, w, h, cumulative_offset_x, cumulative_offset_y]
        stack: list[list[float]] = []
        geometry: list[tuple[float, float, bool]] = []
        for r in rects:
            while stack and stack[-1][0] >= r["depth"]:
                stack.pop()
            off_x, off_y = (stack[-1][5], stack[-1][6]) if stack else (0.0, 0.0)
            clipped = any(
                r["x"] - a[5] >= a[1] + a[3]
                or r["x"] - a[5] + r["w"] <= a[1]
                or r["y"] - a[6] >= a[2] + a[4]
                or r["y"] - a[6] + r["h"] <= a[2]
                for a in stack
            )
            if r["kind"] == "scroll_view":
                stack.append(
                    [
                        r["depth"],
                        r["x"],
                        r["y"],
                        r["w"],
                        r["h"],
                        off_x + r.get("scroll_x", 0.0),
                        off_y + r.get("scroll_y", 0.0),
                    ]
                )
            geometry.append((r["x"] - off_x, r["y"] - off_y, clipped))
        return geometry

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
