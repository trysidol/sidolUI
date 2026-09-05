"""Concurrency: Worker, pump_workers, run_async, and cancellation."""

from __future__ import annotations

import pytest
from _helpers import _collect_text

from sidol.app import App
from sidol.component import Component, State, _graph
from sidol.node import Node
from sidol.widgets import Text


def test_background_state_read_registers_no_edge() -> None:
    """A State read from a worker thread while the main thread is inside
    rendered_view() must create NO dependency edge: the worker's later
    write must not dirty the main thread's view signal. With the old
    process-global observer stack, the worker's read attached to the main
    thread's in-flight computation, so its write re-rendered the component
    (the DevServer renders from watcher/HTTP threads — a latent heisenbug)."""
    import threading

    main_inside_view = threading.Event()
    worker_done = threading.Event()

    class Blocking(Component):
        x = State()
        y = State()

        def __init__(self) -> None:
            super().__init__()
            self.x = 0
            self.y = 0

        def view(self) -> Node:
            _ = self.x  # tracked read on the main thread
            main_inside_view.set()
            assert worker_done.wait(timeout=5), "worker never signalled done"
            return Text(str(self.x))

    comp = Blocking()

    def background_read_and_write() -> None:
        # Runs only once the main thread is inside view() with its observer
        # pushed — with the old global stack this read attached to it.
        assert main_inside_view.wait(timeout=5), "main never entered view"
        _ = comp.y  # the read under test
        comp.y = 1  # must not propagate to comp's view signal
        worker_done.set()

    worker = threading.Thread(target=background_read_and_write)
    worker.start()
    comp.rendered_view()
    worker.join(timeout=5)
    assert not worker.is_alive(), "worker never finished"

    # mark_dirty always marks the WRITTEN signal itself, so the dirty set
    # can never be empty after the write — the pinned contract is that it
    # contains y's signal and NOTHING else. With the old process-global
    # stack the worker's read added a y -> view edge, so the view signal
    # was dirty here too and this assertion failed.
    y_signal = comp._signal_ids["y"]
    assert comp._view_signal_id not in _graph.dirty_ids(), (
        "worker's write dirtied the main thread's view signal — "
        "the background read registered an edge"
    )
    assert set(_graph.dirty_ids()) == {y_signal}


def test_worker_returns_result() -> None:
    from sidol.concurrency import Worker

    w = Worker(lambda: 21 * 2)
    w.start()
    assert w.join() == 42


def test_worker_reraises_exception() -> None:
    from sidol.concurrency import Worker

    def boom() -> None:
        raise ValueError("boom")

    w = Worker(boom)
    w.start()
    try:
        w.join()
        raise AssertionError("expected exception")
    except ValueError as exc:
        assert str(exc) == "boom"


def test_worker_poll() -> None:
    import threading

    from sidol.concurrency import Worker

    # Gate the worker on an Event so "not yet done" is a state the test
    # controls — asserting poll() mid-flight is only deterministic when
    # the worker cannot possibly have finished yet.
    gate = threading.Event()
    w = Worker(gate.wait)
    w.start()
    assert w.poll() is False  # fn is blocked on the gate — cannot finish
    gate.set()
    assert w.join() is True  # Event.wait() returns True once set
    assert w.poll() is True


def test_worker_commit_pattern() -> None:
    from sidol.concurrency import Worker

    class AsyncComp(Component):
        data = State()

        def __init__(self) -> None:
            super().__init__()
            self.data = "pending"

        def view(self) -> Node:
            return Text(self.data)

    comp = AsyncComp()
    app = App(comp)

    def task() -> str:
        return "loaded"

    w = Worker(task, on_done=lambda r: setattr(comp, "data", r))
    w.start()
    w.join()
    app.flush()
    tree = app.build_tree()
    assert _collect_text(tree) == ["loaded"]


def test_worker_join_requires_start() -> None:
    from sidol.concurrency import Worker

    with pytest.raises(RuntimeError, match="started before join"):
        Worker(lambda: None).join()


def test_pump_workers_delivers_completion() -> None:
    import time

    from sidol.concurrency import Worker, pump_workers

    done: list[str] = []
    worker = Worker(lambda: "result", on_done=lambda r: done.append(r))
    worker.start()
    while not worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert done == ["result"]
    assert pump_workers() == 0  # collected workers are not re-delivered


def test_pump_workers_reports_errors(capsys) -> None:
    import time

    from sidol.concurrency import Worker, pump_workers

    def boom() -> None:
        raise ValueError("nope")

    worker = Worker(boom)
    worker.start()
    while not worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert "nope" in capsys.readouterr().err


def test_pump_workers_collects_worker_dropped_after_completion() -> None:
    """Regression: a Worker's completion callback must still fire when the
    caller drops its reference before pump_workers() runs. The registry is
    a strong set — the worker stays alive until collected."""
    import gc
    import time

    from sidol.concurrency import Worker, pump_workers

    done: list[str] = []
    worker = Worker(lambda: "result", on_done=lambda r: done.append(r))
    worker.start()
    while not worker.poll():
        time.sleep(0.01)
    # Simulate abandonment after the thread has finished; under a WeakSet
    # registry this would be garbage-collected before the next pump.
    del worker
    gc.collect()
    assert pump_workers() == 1
    assert done == ["result"]


def test_pump_workers_swallows_systemexit(capsys) -> None:
    """A worker raising SystemExit/KeyboardInterrupt must not kill the loop."""
    import time

    from sidol.concurrency import Worker, pump_workers

    def boom() -> None:
        raise SystemExit(1)

    worker = Worker(boom)
    worker.start()
    while not worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert "1" in capsys.readouterr().err


def test_run_async_delivers_result_via_pump_workers() -> None:
    """run_async runs a coroutine and delivers its result like a Worker."""
    import asyncio
    import time

    from sidol.concurrency import pump_workers, run_async

    async def compute() -> int:
        await asyncio.sleep(0.01)
        return 42

    done: list[int] = []
    worker = run_async(compute(), on_done=lambda r: done.append(r))
    while not worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert done == [42]


def test_run_async_reports_coroutine_error(capsys) -> None:
    """A failing coroutine is reported and cannot kill the loop."""
    import asyncio
    import time

    from sidol.concurrency import pump_workers, run_async

    async def boom() -> None:
        await asyncio.sleep(0.01)
        raise ValueError("async nope")

    worker = run_async(boom())
    while not worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert "async nope" in capsys.readouterr().err


def test_worker_cancel_suppresses_delivery() -> None:
    """After cancel(), a finished worker must not deliver on_done/commit —
    even if the function already completed before cancel was called."""
    import time

    from sidol.concurrency import Worker, pump_workers

    delivered: list[str] = []
    committed: list[int] = []

    def fn() -> str:
        time.sleep(0.02)
        return "result"

    worker = Worker(
        fn,
        on_done=lambda r: delivered.append(r),
        commit=lambda: committed.append(1),
    )
    worker.start()
    while not worker.poll():
        time.sleep(0.01)
    worker.cancel()  # after completion, before pump
    assert pump_workers() == 1
    assert delivered == []
    assert committed == []
    assert worker.is_cancelled() is True


def test_worker_cancel_is_cooperative() -> None:
    """A function polling is_cancelled() can exit early."""
    import time

    from sidol.concurrency import Worker

    holder: dict = {}
    observed_cancelled: list[bool] = []

    def fn() -> str:
        for _ in range(100):
            if holder["worker"].is_cancelled():
                observed_cancelled.append(True)
                return "aborted"
            time.sleep(0.01)
        return "finished"

    worker = Worker(fn)
    holder["worker"] = worker
    worker.start()
    time.sleep(0.03)
    worker.cancel()
    assert worker.join() == "aborted"
    assert observed_cancelled == [True]


def test_worker_cancel_before_start_blocks_start() -> None:
    from sidol.concurrency import Worker

    worker = Worker(lambda: 1)
    worker.cancel()
    with pytest.raises(RuntimeError, match="cancelled before start"):
        worker.start()


def test_cancel_all_workers_suppresses_delivery() -> None:
    import time

    from sidol.concurrency import Worker, cancel_all_workers, pump_workers

    delivered: list[int] = []
    workers = [
        Worker(lambda: 1, on_done=lambda r: delivered.append(r)),
        Worker(lambda: 2, on_done=lambda r: delivered.append(r)),
    ]
    for w in workers:
        w.start()
    for w in workers:
        while not w.poll():
            time.sleep(0.01)
    assert cancel_all_workers() == 2
    assert pump_workers() == 2
    assert delivered == []


def test_cancel_all_workers_after_hot_reload_pattern() -> None:
    """The surface reload pattern: dispose app, cancel workers, swap app.
    A worker started by the old app must not deliver into the new app."""
    import time

    from sidol.concurrency import Worker, cancel_all_workers, pump_workers

    delivered: list[str] = []

    def old_app_worker() -> str:
        time.sleep(0.02)
        return "old"

    old_worker = Worker(old_app_worker, on_done=lambda r: delivered.append(r))
    old_worker.start()
    time.sleep(0.01)

    # Simulate hot-reload teardown while the worker is still in flight.
    cancel_all_workers()
    # New app comes up; pump collects the (cancelled) old worker.
    while not old_worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert delivered == []


def test_cancel_while_running_join_returns_result() -> None:
    """A non-cooperative worker cancelled mid-flight: join() returns its
    eventual result but suppresses on_done delivery."""
    import time

    from sidol.concurrency import Worker

    delivered: list[str] = []
    worker = Worker(
        lambda: time.sleep(0.02) or "done",
        on_done=lambda r: delivered.append(r),
    )
    worker.start()
    time.sleep(0.005)
    worker.cancel()
    assert worker.join() == "done"
    assert delivered == []


def test_cancel_then_error_join_reraises() -> None:
    """A cancelled worker whose function also raised: join() re-raises the
    error — cancellation suppresses delivery, not error reporting."""
    import time

    from sidol.concurrency import Worker

    def boom() -> None:
        time.sleep(0.02)
        raise ValueError("boom")

    worker = Worker(boom)
    worker.start()
    time.sleep(0.005)
    worker.cancel()
    with pytest.raises(ValueError, match="boom"):
        worker.join()


def test_run_async_cancel_suppresses_delivery() -> None:
    import asyncio
    import time

    from sidol.concurrency import pump_workers, run_async

    async def compute() -> int:
        await asyncio.sleep(0.02)
        return 42

    delivered: list[int] = []
    worker = run_async(compute(), on_done=lambda r: delivered.append(r))
    time.sleep(0.005)
    worker.cancel()
    while not worker.poll():
        time.sleep(0.01)
    assert pump_workers() == 1
    assert delivered == []
