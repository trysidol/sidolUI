"""Application entry point.

build_tree() resolves the declarative tree for testing without a surface.
run() raises NotImplementedError until a render surface (TUI/GPU) is wired.
flush() is the minimal render loop — call it after state changes to
re-render dirty components.
"""

from __future__ import annotations

from sidol.component import Component, _computations, _graph
from sidol.node import Node


class App:
    def __init__(self, root: Component) -> None:
        self.root = root

    def build_tree(self) -> Node:
        return self.root.rendered_view()

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
                    # Re-queue this component for the next flush so it
                    # isn't orphaned (clear_observer already pruned its
                    # incoming edges before view() threw, so no future
                    # state change would naturally re-trigger it).
                    _graph.mark_dirty(signal_id)
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error

    def run(self) -> None:
        raise NotImplementedError(
            "App.run() needs a render surface (headless/TUI/GPU) to exist "
            "first — see build order steps 5+ in the architecture doc. "
            "Use build_tree() to inspect the declarative tree in the meantime."
        )
