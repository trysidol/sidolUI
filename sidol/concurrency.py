"""Concurrency primitives for background work and async operations.

``Worker`` runs a function in a daemon thread. Completion callbacks are
invoked by ``join`` on the joining thread, which is the safe place to mutate
reactive UI state.

Async coroutine helpers are intentionally not included yet; use ``Worker``
for background thread work.

Usage::

    import time
    from sidol.concurrency import Worker

    class DataFetcher(Component):
        status = State()

        def __init__(self):
            super().__init__()
            self.status = "idle"

        def fetch(self):
            worker = Worker(self._do_fetch, commit=self._on_done)
            worker.start()

        def _do_fetch(self):
            time.sleep(2)
            return "loaded"

        def _on_done(self):
            self.status = "done"

        def view(self):
            return Text(self.status)
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# Workers that have been started and not yet collected. A strong set: an
# in-flight worker must stay alive until pump_workers()/join() collects
# it. Relying on the thread's bound-method reference is not enough —
# CPython drops the target once the thread finishes, so an abandoned
# worker would otherwise be garbage-collected before its completion
# callback fires. The TUI surface drains this on every event-loop tick.
_active_workers: set[Worker] = set()


class Worker:
    """Run a callable in a background thread.

    The callable runs once and its return value is passed to *on_done* when
    ``join`` collects the result. The callback runs on the joining thread.

    If *commit* is provided, it is called by ``join`` after the function
    succeeds. In the TUI event loop, ``pump_workers()`` collects finished
    workers automatically on every tick; headless callers use ``join()``
    or ``pump_workers(flush=app.flush)``.
    """

    def __init__(
        self,
        fn: Callable[[], T],
        *,
        on_done: Callable[[T], Any] | None = None,
        commit: Callable[[], Any] | None = None,
    ) -> None:
        self._fn = fn
        self._on_done = on_done
        self._commit = commit
        self._result: T | None = None
        self._error: BaseException | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._started = False
        self._completion_delivered = False

    def start(self) -> None:
        """Launch the background thread. Non-blocking.

        Registers the worker for automatic collection — the TUI event
        loop calls ``pump_workers()`` on every tick, so completion
        callbacks fire without manual ``join`` calls.
        """
        with self._lock:
            if self._started:
                raise RuntimeError("Worker can only be started once")
            self._started = True
        _active_workers.add(self)
        self._thread = threading.Thread(target=self._run, daemon=True, name="sidol-worker")
        self._thread.start()

    def _run(self) -> None:
        try:
            result = self._fn()
            with self._lock:
                self._result = result
        except BaseException as exc:
            with self._lock:
                self._error = exc
        finally:
            self._done.set()

    def join(self, flush: Callable[[], Any] | None = None) -> T:
        """Block until the worker finishes. Reraises any exception.

        If *flush* is provided (e.g. ``app.flush``), it is called after
        ``on_done`` so any state changes in the callback propagate.
        """
        if not self._started:
            raise RuntimeError("Worker must be started before join")
        self._done.wait()
        with self._lock:
            if self._error is not None:
                raise self._error
            result = self._result
            deliver = not self._completion_delivered
            self._completion_delivered = True
        if deliver:
            if self._commit is not None:
                self._commit()
            if self._on_done is not None:
                self._on_done(result)  # type: ignore[arg-type]
        _active_workers.discard(self)
        if flush is not None:
            flush()
        return result  # type: ignore[return-value]

    def poll(self) -> bool:
        """Return True if the worker has finished (non-blocking)."""
        return self._done.is_set()

    @property
    def result(self) -> T | None:
        """The return value, or None if not yet finished."""
        with self._lock:
            return self._result

    @property
    def error(self) -> BaseException | None:
        """The exception, or None if the worker succeeded."""
        with self._lock:
            return self._error


def pump_workers(flush: Callable[[], Any] | None = None) -> int:
    """Collect finished workers, delivering their completion callbacks.

    Returns the number of workers collected. The TUI surface calls this
    on every event-loop tick; headless code calls it manually (pass
    ``flush=app.flush`` to propagate state changes made by callbacks).
    Worker exceptions are reported to stderr and swallowed — a failed
    background task must not kill the event loop.
    """
    delivered = 0
    for worker in list(_active_workers):
        if not worker.poll():
            continue
        try:
            worker.join(flush=flush)
        except BaseException as exc:
            # Catch BaseException (not Exception) so a worker that raised
            # SystemExit/KeyboardInterrupt also can't kill the loop.
            print(f"[sidol] worker failed: {exc}", file=sys.stderr)
        finally:
            _active_workers.discard(worker)
        delivered += 1
    return delivered
