"""Application entry point.

build_tree() resolves the declarative tree for testing without a surface.
compute_layout() runs the taffy flexbox engine and returns positions.
run() raises NotImplementedError until a render surface (TUI/GPU) is wired.
flush() is the minimal render loop — call it after state changes to
re-render dirty components.
"""

from __future__ import annotations

from collections.abc import Callable

from sidol._sidol_core import (
    compute_layout as _rust_compute_layout,
    tui_cleanup,
    tui_init,
    tui_render_frame,
    tui_size,
)
from sidol.component import Component, _computations, _graph
from sidol.node import Node


class App:
    def __init__(self, root: Component) -> None:
        self.root = root

    def build_tree(self) -> Node:
        return self.root.rendered_view()

    def compute_layout(self, viewport_w: float = 800, viewport_h: float = 600) -> list[dict]:
        """Run taffy flexbox layout on the current tree and return rects.

        Returns a flat list of dicts in pre-order (parent before children):
            [{"kind": "row", "x": 0, "y": 0, "w": 800, "h": 600}, ...]

        The first entry is always the root. Use tree-walk logic to map
        positions back to components.
        """
        tree = self.build_tree()
        return _rust_compute_layout(tree, viewport_w, viewport_h)

    def print_layout(self, viewport_w: float = 800, viewport_h: float = 600) -> None:
        """Print the computed layout tree as indented text (headless surface).

        Uses the ``depth`` field from each rect (computed by the Rust layout
        engine in pre-order traversal) for correct indentation.
        """
        rects = self.compute_layout(viewport_w, viewport_h)
        for r in rects:
            indent = "  " * r["depth"]
            line = f"{indent}{r['kind']} @ ({r['x']:.0f}, {r['y']:.0f}) {r['w']:.0f}x{r['h']:.0f}"
            print(line)

    def flush(self) -> None:
        """Process all dirty computation signals (the minimal render loop).

        Snapshots the current dirty set via ``drain_dirty()`` (which clears
        it atomically), then walks every computation signal in the snapshot
        and calls ``rendered_view()`` on its corresponding Component.

        Writes during re-render (e.g. event handlers that set state mid-
        flush) produce a fresh dirty set that will be processed on the
        *next* ``flush()`` call — no re-entrancy, no infinite loops.

        If a component's ``rendered_view()`` raises, its view signal is
        re-queued (so it gets retried next flush) and the exception is
        stored. All remaining dirty components in the batch still get
        processed. The first stored exception is re-raised after the
        batch completes — a single broken component never silences other
        components' updates.

        This is what a future animation-frame loop would call each tick.
        For now, call it explicitly after mutating state.
        """
        first_error: BaseException | None = None
        for signal_id in _graph.drain_dirty():
            if component := _computations.get(signal_id):
                try:
                    component.rendered_view()
                except Exception as exc:
                    _graph.mark_dirty(signal_id)
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error

    def run(self) -> None:
        tui_init()
        try:
            viewport_w, viewport_h = tui_size()
            focused_idx: int = -1  # index into buttons-only list (0..n-1), -1 = none
            while True:
                self.flush()
                tree = self.build_tree()
                rects = _rust_compute_layout(tree, float(viewport_w), float(viewport_h))
                callbacks = self._button_callbacks(tree)
                # Convert button index → rect index for rendering highlight
                rect_focused = self._button_rect_index(rects, focused_idx)
                event = tui_render_frame(rects, rect_focused)
                if event == "quit":
                    break
                elif event == "focus_next":
                    focused_idx = self._next_button(focused_idx, len(callbacks))
                elif event == "focus_prev":
                    focused_idx = self._prev_button(focused_idx, len(callbacks))
                elif event == "activate":
                    if 0 <= focused_idx < len(callbacks):
                        cb = callbacks[focused_idx]
                        if cb is not None:
                            cb()
        finally:
            tui_cleanup()

    @staticmethod
    def _button_rect_index(rects: list[dict], button_idx: int) -> int:
        """Return the index in *rects* for the *button_idx*-th button (0-based)."""
        if button_idx < 0:
            return -1
        count = 0
        for i, r in enumerate(rects):
            if r["kind"] == "button":
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

    def _button_callbacks(self, root: Node) -> list[Callable[[], None] | None]:
        """Collect button callbacks in pre-order (same order as rects).

        Returns a list with one entry per button. Entries are None for
        buttons without an ``on_click`` handler, so the list index
        matches the button index in the rect list.
        """
        callbacks: list[Callable[[], None] | None] = []
        def walk(node: Node) -> None:
            if node.kind == "button":
                callbacks.append(node.on_click)
            for child in node.children:
                walk(child)
        walk(root)
        return callbacks
