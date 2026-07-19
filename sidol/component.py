"""Reactive state system — the Python half of Sidol's reactivity.

Architecture
  Reactivity is split across the FFI boundary:
    RUST (graph.rs): owns the dependency graph — which signals exist,
      which depend on which, which are dirty.
    PYTHON: owns signal VALUES, the auto-tracking stack, the developer API.

  The State descriptor + observer stack implement fine-grained reactivity
  (SolidJS calls this auto-tracking, Vue calls it a tracking scope).
  Dependencies are recorded as a SIDE EFFECT of reading a State field
  during a tracked computation (view()), never declared imperatively.

Auto-tracking in detail
  1. rendered_view() pushes the component's view_signal_id onto the
     global _observer_stack, then calls view().
  2. State.__get__ sees the non-empty stack and calls
     _graph.add_dependency(count_signal_id, view_signal_id).
  3. When self.count = 5 fires later, State.__set__ calls
     _graph.mark_dirty(count_signal_id), propagating to the view signal.

Stale conditional subscriptions
  Before every rendered_view(), clear_observer() removes all existing
  incoming edges for the view signal. As view() executes, only the
  State fields it CURRENTLY reads get wired up. If view() reads self.a
  on frame 1 and self.b on frame 2, the self.a edge is gone on frame 2.
  Without this, edges only accumulate — the view re-renders when ANY
  state it EVER read is written. This is a correctness bug.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sidol._sidol_core import Graph

# One graph per process. Multi-window apps would need isolated graphs —
# revisit when multi-window scenarios are real.
_graph = Graph()

# Stack of computation signal IDs currently in progress. Non-empty during
# rendered_view(). Stack (not single slot) because tracked computations
# can nest — a helper called from view() should attribute deps to the
# outer computation, not create a new one.
_observer_stack: list[int] = []

# Reverse map: computation signal ID -> Component instance.
# WeakValueDictionary so orphaned components are collected.
_computations: weakref.WeakValueDictionary[int, Component] = weakref.WeakValueDictionary()


@contextmanager
def _tracking(computation_signal_id: int) -> Iterator[None]:
    """Register `computation_signal_id` as the active observer. State reads
    inside the with-block auto-register as dependencies of this computation."""
    _observer_stack.append(computation_signal_id)
    try:
        yield
    finally:
        _observer_stack.pop()


def reset_graph() -> None:
    """Reset the graph + all Python tracking state. Test isolation only.
    Leaves live Components with dangling signal IDs."""
    global _graph
    _graph.reset()
    _observer_stack.clear()
    _computations.clear()


class State:
    """Descriptor marking an attribute as reactive.

    Only names declared with ``State()`` become signals. Everything else
    assigned on a Component works as a normal Python attribute.

    Why a descriptor and not __getattr__/__setattr__?
      Descriptors are per-attribute, per-object — they fire only for
      declared State fields, not every attribute access. __setattr__ is
      all-or-nothing; the first version of this framework used it and it
      broke everything non-State (internal attrs, helpers, caches).
      Descriptors compose: you can mix State, @property, regular attrs,
      and slots on the same class.

    Same pattern as Django model fields, dataclasses.field(), SQLAlchemy columns.
    """

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, instance: Component | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        if self._name not in instance._signal_ids:
            raise AttributeError(
                f"State field '{self._name}' on {type(instance).__name__} "
                "was read before being initialised. "
                "Set an initial value in __init__."
            )
        signal_id = instance._signal_ids[self._name]
        if _observer_stack:
            _graph.add_dependency(signal_id, _observer_stack[-1])
        return instance._state_values[self._name]

    def __set__(self, instance: Component, value: Any) -> None:
        signal_ids = instance._signal_ids
        if self._name not in signal_ids:
            signal_ids[self._name] = _graph.create_signal()
        else:
            _graph.mark_dirty(signal_ids[self._name])
        instance._state_values[self._name] = value

    _name: str


class Component:
    """Base class for Sidol components.

    Subclasses declare reactive state with ``State()`` and override
    ``view()`` to return a declarative node tree::

        class MyWidget(Component):
            title = State()

            def __init__(self):
                super().__init__()
                self.title = "Hello"

            def view(self):
                return Text(self.title)
    """

    def __init__(self) -> None:
        # Populated by State.__set__ on first assignment.
        self._signal_ids: dict[str, int] = {}
        self._state_values: dict[str, Any] = {}
        # View computation signal — dirty means view() output is stale.
        self._view_signal_id = _graph.create_signal()
        _computations[self._view_signal_id] = self

    def view(self) -> Any:
        """Override to return a declarative Node tree. Pure function of self
        and its State fields — side effects (I/O, API calls) belong in event
        handlers, not here."""
        raise NotImplementedError

    def rendered_view(self) -> Any:
        """Call view() inside a tracking context, with stale-edge pruning.

        This is what the render loop calls. Never call view() directly — you
        lose auto-tracking and stale-edge cleanup.

        Does two things:
          1. clear_observer removes all existing dependency edges for this
             component's view signal (prevents stale conditional subscriptions).
          2. _tracking pushes view_signal_id onto the observer stack so
             State reads during view() auto-register as dependencies.
        """
        _graph.clear_observer(self._view_signal_id)
        with _tracking(self._view_signal_id):
            return self.view()
