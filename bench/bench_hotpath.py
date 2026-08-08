"""Hot-path benchmark for the Sidol reactive core.

Measures ``build_tree``, ``compute_layout``, ``flush``, and raw graph
propagation on a realistic tree so the reactive hot path can be tracked
over time. Run with::

    uv run maturin develop --release
    uv run python bench/bench_hotpath.py
"""

from __future__ import annotations

import time

from sidol import App, Component, State
from sidol._sidol_core import Graph
from sidol.component import reset_graph
from sidol.widgets import Column, Text


def bench(label: str, fn, number: int = 200) -> None:
    fn()  # warmup
    start = time.perf_counter()
    for _ in range(number):
        fn()
    elapsed = (time.perf_counter() - start) / number
    print(f"{label:<42} {elapsed * 1000:9.3f} ms")


class Item(Component):
    label = State()

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def view(self):
        return Column(Text(self.label), Text(f"{self.label}!"), spacing=1)


class ListView(Component):
    count = State()

    def __init__(self, n: int) -> None:
        super().__init__()
        self.count = 0
        self._n = n

    def view(self):
        return Column(
            Text(f"Count: {self.count}"),
            *[Item(f"item-{i}").keyed(f"item-{i}") for i in range(self._n)],
            spacing=1,
        )


def main() -> None:
    reset_graph()
    n = 500
    app = App(ListView(n))
    app.build_tree()

    print(f"tree: {n} keyed items + root -> {len(app.build_tree().children)} children")
    print("-" * 60)
    bench("build_tree (500 items)", lambda: app.build_tree(), number=100)
    bench("compute_layout (800x600)", lambda: app.compute_layout(800, 600), number=100)
    bench("build_tree + compute_layout", lambda: app.compute_layout(800, 600), number=50)

    item0 = app.root._keyed_children["item-0"]

    def mutate_and_flush():
        item0.label = "changed"
        app.flush()

    bench("mutate + flush (1 signal)", mutate_and_flush, number=200)

    def mutate_all_and_flush():
        for i in range(n):
            app.root._keyed_children[f"item-{i}"].label = "x"
        app.flush()

    bench("mutate all + flush (500 signals)", mutate_all_and_flush, number=20)

    def graph_prop():
        g = Graph()
        ids = [g.create_signal() for _ in range(1000)]
        for src, dst in zip(ids, ids[1:]):
            g.add_dependency(src, dst)
        for _ in range(50):
            g.mark_dirty(ids[0])
            g.drain_dirty()

    bench("graph: 1000-chain mark_dirty x50", graph_prop, number=100)

    reset_graph()


if __name__ == "__main__":
    main()
