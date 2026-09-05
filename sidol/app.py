"""Application entry point.

build_tree() resolves the declarative tree for testing without a surface;
passed a drained dirty set it becomes the incremental frame path — only
dirty components re-render, clean ones splice cached subtrees.
compute_layout() runs the taffy flexbox engine and returns positions.
    run() enters the TUI event loop (delegates to ``TuiSurface``).
flush() is the minimal render loop — call it after state changes to
re-render dirty components.
"""

from __future__ import annotations

import warnings
import weakref
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from sidol._sidol_core import compute_layout as _rust_compute_layout
from sidol.component import Component, _computations, _graph
from sidol.node import Node

# One App at a time is the supported contract: the signal graph, the dirty
# set, and flush() are process-global, so two live Apps re-render each
# other's components. Constructing a second App while one is alive warns;
# dispose() releases the claim. The hot-reload swap paths construct the
# replacement App inside _multiple_apps_allowed() and dispose the old app
# immediately after — that window is the one supported exception.
_live_apps: weakref.WeakSet[App] = weakref.WeakSet()
_allow_multiple_apps = False


@contextmanager
def _multiple_apps_allowed() -> Iterator[None]:
    """Suppress the single-app warning for the hot-reload swap window: the
    replacement App is constructed while the old one is still alive, then
    the old one is disposed. Reentrant — nested windows restore the
    previous state."""
    global _allow_multiple_apps
    previous = _allow_multiple_apps
    _allow_multiple_apps = True
    try:
        yield
    finally:
        _allow_multiple_apps = previous


class App:
    """Root of a component tree and owner of the render loop.

    One App at a time is the supported contract: the signal graph, the
    dirty set, and ``flush()`` are process-global, so two live Apps
    re-render each other's components. Constructing a second App while
    one is alive warns; ``dispose()`` releases the claim (garbage
    collection of an undisposed App releases it too). The hot-reload
    swap paths construct the replacement inside
    ``_multiple_apps_allowed()`` and dispose the old app immediately —
    that window is the one supported exception.
    """

    def __init__(self, root: Component) -> None:
        if _live_apps and not _allow_multiple_apps:
            warnings.warn(
                "another App is still alive: the signal graph and flush() "
                "are process-global, so two live Apps re-render each "
                "other's components. Dispose the old App first (the "
                "hot-reload swap does this automatically).",
                stacklevel=2,
            )
        _live_apps.add(self)
        self.root = root
        self._quit_requested = False
        # Post-flush listeners (see add_flush_listener).
        self._flush_listeners: list[Callable[[], None]] = []

    def add_flush_listener(self, listener: Callable[[], None]) -> None:
        """Register *listener* to run after each successful ``flush()``.

        Listeners run in registration order after the flush batch completes.
        A failed flush re-raises its error WITHOUT running listeners — a
        listener never observes a partially-flushed batch. This is the
        honest hook for "after each flush, push elsewhere" (the dev server's
        browser push); it replaces the old instance-attribute monkeypatch.
        """
        self._flush_listeners.append(listener)

    def remove_flush_listener(self, listener: Callable[[], None]) -> None:
        """Unregister a listener added by ``add_flush_listener``. Removing
        an unknown listener is a no-op (``DevServer.stop()`` may run twice)."""
        try:
            self._flush_listeners.remove(listener)
        except ValueError:
            pass

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
        topologies never accumulate in the graph. Also releases this App's
        single-app claim, so constructing a replacement no longer warns.
        """
        self.root.dispose()
        _live_apps.discard(self)

    def build_tree(self, dirty: Iterable[int] | None = None) -> Node:
        """Resolve the declarative tree, recursively.

        Without *dirty* this is the full rebuild: ``rendered_view()`` runs
        on every component. This is the first-frame, test, and headless
        path — it never consults the render caches.

        With *dirty* (the signal-ID set drained from the reactive graph),
        only dirty components re-render; clean components splice their
        cached resolved subtrees, so per-frame Python work is proportional
        to the changed subtree, not the whole tree. Keyed reconciliation
        (child-set changes, eviction of removed keys) runs for every
        component whose children are walked — i.e. exactly the components
        that re-rendered or embed a re-rendered descendant.
        """
        if dirty is None:
            return self._resolve(self.root, set(), set(), None)
        dirty_components = {
            component
            for signal_id in dirty
            if (component := _computations.get(signal_id)) is not None
        }
        for component in dirty_components:
            self._mark_splice_pending(component)
        return self._resolve(self.root, set(), set(), dirty_components)

    def _mark_splice_pending(self, component: Component) -> None:
        """Flag *component* and its ancestors for re-splicing.

        A re-render makes the component's cached resolved subtree stale, and
        every ancestor's cached subtree embeds the old one — each must be
        re-spliced before reuse. Flagging stops at an ancestor that is
        already flagged: flagged components always have flagged ancestors
        (marking invariant), so repeated marking is O(depth).
        """
        if component._splice_pending:
            return
        component._splice_pending = True
        parent = component._tree_parent
        while parent is not None and not parent._splice_pending:
            parent._splice_pending = True
            parent = parent._tree_parent

    def _resolve(
        self,
        component: Component,
        active: set[int],
        seen: set[int],
        dirty: set[Component] | None,
    ) -> Node:
        """Resolve a Component and all Component references in its subtree
        into a flat Node tree.

        *active* is the current recursion path (cycle detection); *seen*
        is every component encountered in this walk. A component on the
        current path raises ``RuntimeError`` (cycle); one met at a second,
        disjoint position raises ``ValueError`` — an instance occupies
        exactly one position in the tree (see the ``Component`` docstring),
        and the incremental frame path cannot keep a multi-position
        instance coherent, so the shape is rejected on every path.

        *dirty* is ``None`` for a full rebuild (every component re-renders)
        or the set of components whose view must re-render this frame. A
        component re-renders when it is dirty or has never rendered; its
        children are (re)walked when it re-renders or is flagged
        splice-pending — that walk IS the keyed-reconciliation pass, so a
        re-rendered component's child set (List add/remove, conditionals)
        is reconciled and evicted children disposed right here. Clean,
        unflagged components return their cached subtree without calling
        ``view()``.

        The returned Node is cached on the component; parents embed it by
        reference, so an unchanged subtree is shared across frames (Nodes
        are frozen — identity means content equality).
        """
        component_id = id(component)
        if component_id in active:
            raise RuntimeError(f"Cyclic component tree involving {type(component).__name__}")
        if component_id in seen:
            raise ValueError(
                f"{type(component).__name__} component is embedded at multiple "
                "positions in the tree. A Component instance occupies exactly "
                "one position; create separate instances, or use "
                "Component.keyed(key) for repeated items in a List."
            )
        seen.add(component_id)
        active.add(component_id)
        try:
            needs_render = (
                dirty is None or component in dirty or component._cached_view is None
            )
            if (
                not needs_render
                and not component._splice_pending
                and component._cached_resolved is not None
            ):
                # Clean component, nothing pending: the cached subtree
                # already reflects its view and all children — reuse it
                # wholesale, without calling view().
                return component._cached_resolved
            component._active_keyed_children.clear()
            if needs_render:
                if dirty is not None:
                    try:
                        raw = component.rendered_view()
                    except Exception:
                        # Same contract as flush(): a failed view signal is
                        # re-queued so a later frame retries it.
                        _graph.mark_dirty(component._view_signal_id)
                        raise
                else:
                    raw = component.rendered_view()
            else:
                raw = component._cached_view
            if isinstance(raw, Component):
                # view() returned another Component — resolve through it.
                raw._tree_parent = component
                resolved = self._resolve(raw, active, seen, dirty)
            elif isinstance(raw, Node):
                resolved = self._resolve_node_children(raw, active, seen, component, dirty)
                for key, child in list(component._keyed_children.items()):
                    if key not in component._active_keyed_children:
                        child.dispose()
                        del component._keyed_children[key]
            else:
                raise TypeError(
                    f"{type(component).__name__}.view() must return Node, "
                    f"got {type(raw).__name__}"
                )
            component._cached_resolved = resolved
            component._splice_pending = False
            return resolved
        finally:
            active.remove(component_id)

    def _resolve_node_children(
        self,
        node: Node,
        active: set[int],
        seen: set[int],
        owner: Component,
        dirty: set[Component] | None,
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
                child._tree_parent = owner
                resolved.append(self._resolve(child, active, seen, dirty))
                changed = True
            elif isinstance(child, Node):
                resolved_child = self._resolve_node_children(
                    child, active, seen, owner, dirty
                )
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

    def semantic_tree(self) -> list[dict]:
        """Return the accessibility semantics of the resolved tree (headless).

        Builds the tree (``build_tree()``) and walks it in pre-order,
        returning nested semantic-node dicts::

            [{"role": str, "name": str | None, "children": [...]}, ...]

        Nodes without a ``role`` (layout containers: Row, Column, Spacer,
        ScrollView) carry no semantics and are skipped — their semantic
        children splice into the nearest semantic ancestor, so the result
        answers "what does this UI mean", not "how is it laid out". This
        is the shared-model accessor a future platform bridge would
        consume; the TUI surface does not use it (no platform bridge in
        Phase 1).
        """
        return _semantic_entries(self.build_tree())

    def flush(self) -> None:
        """Process all dirty computation signals (the minimal render loop).

        The dirty set is process-global: this re-renders every dirty
        component in the process, which is why only one App may be live at
        a time (see the class docstring).

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

        Each successful re-render also flags the component and its
        ancestors splice-pending, so the next incremental frame splices
        the flushed content instead of reusing a stale cached subtree.

        After a fully successful batch, the registered post-flush
        listeners run in registration order (see ``add_flush_listener``);
        a failed batch re-raises without running them.

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
                else:
                    # rendered_view() refreshed the component's cached raw
                    # view, so its cached resolved subtree is stale — and
                    # every ancestor's embeds it. Flag them for re-splice.
                    self._mark_splice_pending(component)
        if first_error is not None:
            raise first_error
        for listener in self._flush_listeners:
            listener()

    def export_html(self, path: str, viewport_w: float = 800, viewport_h: float = 600) -> None:
        """Export the current component tree as a standalone HTML page.

        Builds the tree, computes layout via taffy, and writes a
        self-contained HTML file that renders the UI using absolute
        positioning matching the computed layout rects.

        See ``sidol/surfaces/html.py`` for the full implementation.
        """
        from sidol.surfaces.html import export_html

        export_html(self, path, viewport_w, viewport_h)

    def run(self, surface: Any = None) -> None:
        """Enter the surface's event loop. Blocks until the user quits.

        Delegates to ``TuiSurface(self).run()`` by default. Pass a surface
        constructed with this app and exposing ``run()`` to plug in a
        different one — see CONTRIBUTING.md Recipe 3 for the surface
        contract (a future GPU surface plugs in here).
        """
        if surface is None:
            # Deferred import: surfaces import App, so a module-level
            # import here would be circular.
            from sidol.surfaces.tui import TuiSurface

            surface = TuiSurface(self)
        surface.run()


def _semantic_entries(node: Node) -> list[dict]:
    """Semantic entries for one resolved Node, pre-order. Nodes without a
    ``role`` are presentation-only: they contribute no entry of their own
    and their children splice through to the nearest semantic ancestor."""
    children: list[dict] = []
    for child in node.children:
        children.extend(_semantic_entries(child))
    if node.role is None:
        return children
    return [{"role": node.role, "name": node.name, "children": children}]
