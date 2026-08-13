"""Application entry point.

build_tree() resolves the declarative tree for testing without a surface.
compute_layout() runs the taffy flexbox engine and returns positions.
    run() enters the TUI event loop (delegates to ``TuiSurface``).
flush() is the minimal render loop — call it after state changes to
re-render dirty components.
"""

from __future__ import annotations

from dataclasses import replace

from sidol._sidol_core import compute_layout as _rust_compute_layout
from sidol.component import Component, _computations, _graph
from sidol.node import Node


class App:
    def __init__(self, root: Component) -> None:
        self.root = root
        self._quit_requested = False

    def request_quit(self) -> None:
        """Ask the running surface to exit its event loop.

        This is how app-level key bindings quit — e.g. a root container
        with ``on_key={"q": lambda event: app.request_quit()}``. The TUI
        surface checks the flag after every dispatched event.
        """
        self._quit_requested = True

    def dispose(self) -> None:
        """Deterministically tear down the whole component tree.

        Calls ``dispose()`` on the root, which recursively disposes every
        retained/keyed child and removes all their signal nodes from the
        process-wide graph. Safe to call multiple times. Surfaces call this
        on hot-reload (before swapping in the new app) and on exit so old
        topologies never accumulate in the graph.
        """
        self.root.dispose()

    def build_tree(self) -> Node:
        """Resolve the full declarative tree, recursively.

        Calls ``rendered_view()`` on the root Component, then walks the
        resulting Node tree and replaces any ``Component`` child references
        with their resolved ``Node`` subtrees. Child Components get their
        own signal IDs and tracking contexts — they re-render independently
        of their parent.
        """
        return self._resolve_component_tree(self.root, set())

    def _resolve_component_tree(self, component: Component, active: set[int]) -> Node:
        """Resolve a Component and all Component references in its subtree
        into a flat Node tree.

        Calls ``rendered_view()`` on the component, then walks every level
        of the resulting Node tree, resolving Component children at any
        depth. This handles nested cases like ``Column(Column(TextField))``
        where an intermediate Node wraps a Component grandchild.
        """
        component_id = id(component)
        if component_id in active:
            raise RuntimeError(f"Cyclic component tree involving {type(component).__name__}")
        active.add(component_id)
        try:
            component._active_keyed_children.clear()
            node = component.rendered_view()
            if isinstance(node, Component):
                return self._resolve_component_tree(node, active)
            if not isinstance(node, Node):
                raise TypeError(
                    f"{type(component).__name__}.view() must return Node, "
                    f"got {type(node).__name__}"
                )
            resolved = self._resolve_node_children(node, active, component)
            component._keyed_children = {
                key: child
                for key, child in component._keyed_children.items()
                if key in component._active_keyed_children
            }
            return resolved
        finally:
            active.remove(component_id)

    def _resolve_node_children(
        self,
        node: Node,
        active: set[int],
        owner: Component,
    ) -> Node:
        """Walk a Node's children, resolving any Component references found
        at any depth. Returns the same Node if unchanged."""
        changed = False
        resolved: list[Node] = []
        for child in node.children:
            if isinstance(child, Component):
                if getattr(child, "key", None) is not None:
                    key = child.key
                    if key in owner._active_keyed_children:
                        raise ValueError(
                            f"duplicate child key {key!r} under "
                            f"{type(owner).__name__}"
                        )
                    owner._active_keyed_children.add(key)
                    existing = owner._keyed_children.get(key)
                    if existing is not None:
                        if type(existing) is not type(child):
                            raise TypeError(
                                f"key {key!r} changed component type from "
                                f"{type(existing).__name__} to {type(child).__name__}"
                            )
                        child = existing
                    else:
                        owner._keyed_children[key] = child
                resolved.append(self._resolve_component_tree(child, active))
                changed = True
            elif isinstance(child, Node):
                resolved_child = self._resolve_node_children(child, active, owner)
                resolved.append(resolved_child)
                if resolved_child is not child:
                    changed = True
            else:
                raise TypeError(
                    f"Node child must be Node or Component, got {type(child).__name__}"
                )
        if not changed:
            return node
        return replace(node, children=tuple(resolved))

    def compute_layout(
        self,
        viewport_w: float = 800,
        viewport_h: float = 600,
        tree: Node | None = None,
    ) -> list[dict]:
        """Run taffy flexbox layout and return rects.

        Returns a flat list of dicts in pre-order (parent before children):
            [{"kind": "row", "x": 0, "y": 0, "w": 800, "h": 600}, ...]

        If *tree* is provided (already built via ``build_tree()``), it is
        reused instead of rebuilding — avoids a redundant ``view()`` call
        per frame when the caller already has the tree.
        """
        if tree is None:
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

    def export_html(self, path: str, viewport_w: float = 800, viewport_h: float = 600) -> None:
        """Export the current component tree as a standalone HTML page.

        Builds the tree, computes layout via taffy, and writes a
        self-contained HTML file that renders the UI using absolute
        positioning matching the computed layout rects.

        See ``sidol/surfaces/html.py`` for the full implementation.
        """
        from sidol.surfaces.html import export_html

        export_html(self, path, viewport_w, viewport_h)

    def run(self) -> None:
        """Enter the TUI event loop. Blocks until the user quits.

        Delegates to ``TuiSurface`` (see ``sidol/surfaces/tui.py``).
        A future GPU surface would similarly provide its own ``run()``
        and be plugged in here via an optional parameter.
        """
        from sidol.surfaces.tui import TuiSurface

        TuiSurface(self).run()
