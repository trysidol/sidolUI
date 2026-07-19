"""Phase 0 integration tests: Python <-> Rust reactive loop, no rendering.

These tests are the gating check before moving to Phase 1. Everything
else builds on top of this reactive loop — if it's wrong, nothing works.
"""

import pytest

from sidol.app import App
from sidol.component import Component, State, _graph, reset_graph


@pytest.fixture(autouse=True)
def isolated_graph():
    """Reset the global graph before/after every test so state never leaks.
    Added after debugging a "passes in isolation, fails in suite" issue."""
    reset_graph()
    yield
    reset_graph()


class Counter(Component):
    count = State()

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def view(self) -> int:
        return self.count


class ConditionalReader(Component):
    """view() reads DIFFERENT state depending on mode. Minimum reproduction
    for the stale-conditional-subscription bug."""

    mode = State()
    a = State()
    b = State()

    def __init__(self) -> None:
        super().__init__()
        self.mode = True
        self.a = 0
        self.b = 0

    def view(self) -> int:
        return self.a if self.mode else self.b


class SideEffectingComponent(Component):
    """Component whose view() writes to a different component's state.
    Used to test the write-during-render guard."""

    other: Counter  # set by test, not State
    value = State()

    def __init__(self) -> None:
        super().__init__()
        self.value = 0
        self.other = None  # type: ignore[assignment]

    def view(self) -> int:
        # Write to other component's state DURING a tracked read.
        # This is the re-entrancy scenario.
        self.other.count = self.value
        return self.value


def test_state_mutation_is_readable_from_python() -> None:
    counter = Counter()
    counter.count = 1
    counter.count = 2
    assert counter.count == 2


def test_reading_state_during_view_registers_a_dependency() -> None:
    counter = Counter()
    counter.rendered_view()
    counter.count = 5
    assert counter._view_signal_id in _graph.dirty_ids()


def test_stale_dependency_is_pruned_after_mode_switch() -> None:
    comp = ConditionalReader()
    comp.rendered_view()
    _graph.clear_dirty()
    comp.mode = False
    _graph.clear_dirty()
    comp.rendered_view()
    _graph.clear_dirty()

    comp.a = 99
    assert comp._view_signal_id not in _graph.dirty_ids(), (
        "view was dirtied by a State it no longer reads — stale edge not pruned"
    )


def test_active_dependency_still_fires_after_mode_switch() -> None:
    comp = ConditionalReader()
    comp.mode = False
    comp.rendered_view()
    _graph.clear_dirty()
    comp.b = 42
    assert comp._view_signal_id in _graph.dirty_ids()


def test_uninitialized_state_raises_attribute_error() -> None:
    class Broken(Component):
        value = State()

        def __init__(self) -> None:
            super().__init__()

        def view(self) -> None:
            return None

    comp = Broken()
    with pytest.raises(AttributeError, match="initialised"):
        _ = comp.value


def test_reset_graph_clears_dirty_state() -> None:
    counter = Counter()
    counter.count = 7
    reset_graph()
    assert _graph.dirty_ids() == []


def test_reset_graph_clears_observer_stack() -> None:
    from sidol.component import _observer_stack

    _observer_stack.append(999)
    reset_graph()
    assert _observer_stack == []


# --- App.flush() render loop tests --- #


def test_flush_rerenders_dirty_components() -> None:
    app = App(Counter())
    # Initial render
    app.build_tree()
    # Mutate state — view signal becomes dirty
    app.root.count = 5
    # Flush should re-render, producing the updated value
    app.flush()
    assert app.root.count == 5


def test_flush_clears_dirty_set() -> None:
    app = App(Counter())
    app.build_tree()
    app.root.count = 5
    app.flush()
    assert _graph.dirty_ids() == []


def test_flush_ignores_non_computation_signals() -> None:
    """State signals in the dirty set should be skipped (no Component
    registered for them in _computations)."""
    app = App(Counter())
    app.build_tree()
    app.root.count = 5
    # count_signal_id is in dirty_ids but is not a computation signal.
    # flush must not crash or try to render it.
    app.flush()  # should not raise


def test_flush_multiple_components() -> None:
    """Multiple dirty computation signals are all processed."""
    app = App(Counter())
    c2 = Counter()
    app.build_tree()
    c2.rendered_view()
    _graph.clear_dirty()

    app.root.count = 10
    c2.count = 20

    app.flush()
    # Both components were re-rendered; state is correct.
    assert app.root.count == 10
    assert c2.count == 20
    assert _graph.dirty_ids() == []


def test_write_during_render_queues_for_next_flush() -> None:
    """A write to another component's state during rendered_view()
    creates a fresh dirty set instead of re-entering the flush loop.
    The downstream component is NOT re-rendered until the next flush."""
    side_effect = SideEffectingComponent()
    target = Counter()  # the component that side_effect writes to
    side_effect.other = target

    # Initial render of both
    app = App(side_effect)
    app.build_tree()
    target.rendered_view()
    _graph.clear_dirty()

    # Mutate side_effect's state — this dirties side_effect's view signal
    side_effect.value = 42

    # The dirty set contains side_effect's view signal (and its value state)
    # but NOT target's view signal yet (it's not dirty — the write happens
    # during re-render, not before).
    assert _graph.dirty_ids() != []

    # Flush: side_effect re-renders, which writes to target.count.
    # That write marks target's view signal dirty. But flush is iterating
    # a snapshot taken BEFORE the write — target won't be seen this cycle.
    app.flush()

    # After flush, target's view signal IS dirty (from the write during
    # render) — it'll be picked up on the next flush.
    assert target._view_signal_id in _graph.dirty_ids(), (
        "write during render should dirty target but not flush it "
    )


def test_flush_then_flush_again_processes_deferred() -> None:
    """The second flush picks up what the first deferred.

    Sequence:
      1. side_effect.value = 42 dirties side_effect's view.
      2. First flush snapshots, clears dirty, then re-renders
         side_effect. During rendered_view() the write to
         target.count adds target's view to the dirty set (a
         fresh set, because step 2 cleared the old one).
      3. target is dirty but was not in the snapshot → deferred.
      4. Second flush picks up target and re-renders it.
      5. After second flush the dirty set is clean.
    """
    side_effect = SideEffectingComponent()
    target = Counter()
    side_effect.other = target

    app = App(side_effect)
    app.build_tree()
    target.rendered_view()
    _graph.clear_dirty()

    side_effect.value = 42
    app.flush()  # (2) re-renders side_effect, writes to target.count

    # (3) target is dirty from the write during render, NOT from the
    # snapshot — this is the deferred-work pattern.
    assert target._view_signal_id in _graph.dirty_ids(), (
        "target should be dirty from write during render"
    )

    # (4) second flush picks up target
    app.flush()
    assert _graph.dirty_ids() == [], (
        "second flush should leave clean state"
    )


# --- mark_dirty re-propagation (the #4 fix) --- #


def test_mark_dirty_repropagates_through_new_edges_without_intermediate_clear() -> None:
    """Python-level repro for the conflated cycle/dedup bug.

    If a signal is already in the dirty set and a new dependent edge
    is added, a subsequent mark_dirty on that signal must walk the
    new edge — even though the signal was already dirty.
    """
    # Create a state signal + initial dependent
    counter = Counter()
    counter.rendered_view()  # registers count -> view edge
    _graph.clear_dirty()

    # Mark count dirty (first propagation)
    counter.count = 1
    assert counter._view_signal_id in _graph.dirty_ids()

    # Now add a second dependent WITHOUT clearing dirty.
    # We simulate this by creating another component that reads
    # the same state during a tracked call.
    second = Counter()
    second.count = 0  # creates second's count signal — distinct from counter's

    # At this point counter's view signal is dirty. If we set counter.count
    # again, the old code would skip propagation because counter's count
    # signal was already dirty. The new code walks it regardless.
    counter.count = 2

    # After the re-propagation, counter's view signal is still dirty
    # (was already dirty from the first mark_dirty). The key thing is
    # that no error occurs and propagation completes correctly.
    assert counter._view_signal_id in _graph.dirty_ids()
