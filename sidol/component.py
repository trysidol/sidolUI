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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

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
TComponent = TypeVar("TComponent", bound="Component")


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

    Mutable values (lists, dicts) must be *replaced*, not mutated in place:
    ``self.items.append(x)`` never invokes ``__set__``, and re-assigning an
    equal value (``self.items = list(self.items)``) is a deliberate no-op.
    Assign a new, non-equal object to trigger a re-render.
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
            # Re-assigning an equal value is a no-op for the graph —
            # otherwise idempotent writes (e.g. scroll_by clamped at 0)
            # would re-dirty the component forever. If equality can't be
            # decided (exotic __eq__), conservatively treat it as changed.
            try:
                unchanged = bool(instance._state_values[self._name] == value)
            except Exception:
                unchanged = False
            if not unchanged:
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
        self.key: object | None = None
        self._retained_children: dict[object, Component] = {}
        self._keyed_children: dict[object, Component] = {}
        self._active_keyed_children: set[object] = set()
        # View computation signal — dirty means view() output is stale.
        self._view_signal_id = _graph.create_signal()
        _computations[self._view_signal_id] = self

    def __del__(self) -> None:
        """Release this component's graph nodes when it is no longer used.

        Components created during a render can otherwise leave orphaned
        topology in the process-wide graph after Python collects them.
        Destructors are best-effort because interpreter shutdown and the
        test-only graph reset can invalidate these IDs first.
        """
        try:
            _graph.remove_signal(self._view_signal_id)
            for signal_id in self._signal_ids.values():
                _graph.remove_signal(signal_id)
        except Exception:
            pass

    def view(self) -> Any:
        """Override to return a declarative Node tree. Pure function of self
        and its State fields — side effects (I/O, API calls) belong in event
        handlers, not here."""
        raise NotImplementedError

    def remember(
        self,
        key: object,
        factory: Callable[[], TComponent],
    ) -> TComponent:
        """Return one stable child component for ``key``.

        Stateful children created directly inside ``view()`` are new objects
        on every render. Use ``remember`` when a child owns reactive state so
        its identity and graph signals survive parent rerenders.
        """
        child = self._retained_children.get(key)
        if child is None:
            child = factory()
            if not isinstance(child, Component):
                raise TypeError("remember() factory must return a Component")
            self._retained_children[key] = child
        return child  # type: ignore[return-value]

    def keyed(self, key: object) -> Component:
        """Assign an explicit identity used when reconciling a child."""
        hash(key)
        self.key = key
        return self

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
