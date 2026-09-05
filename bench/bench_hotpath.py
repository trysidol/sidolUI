"""Hot-path benchmark for the Sidol reactive core.

Measures ``build_tree``, ``compute_layout``, ``flush``, and raw graph
propagation on a realistic tree so the reactive hot path can be tracked
over time. Run with::

    uv run maturin develop --release
    uv run python bench/bench_hotpath.py

Modes:
  (default)  print the numbers.
  --check    compare against ``bench/baselines.json`` and exit nonzero if
             any metric regresses >50% over its baseline. Without a
             baselines file, records the current numbers as the baseline
             and exits 0.
  --update   deliberately re-record ``bench/baselines.json``.
  --deterministic  check only the two deterministic view-call metrics
             (view calls: full build_tree == 500, view calls: frame ==
             1). Noise-free and suitable for CI on every push.

The gate is a smoke check, not a micro-benchmark: the threshold is
generous (CI machines are noisy) and every recorded metric is the median
of several measurement passes, so a single GC pause or scheduler hiccup
cannot fail the run.

The comparison assumes the SAME machine in a similar performance state:
run on a quiet machine, and re-record with ``--update`` after hardware,
build-profile, or sustained-load changes. For before/after comparisons
(e.g. validating an optimization), record and check back-to-back.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sidol._sidol_core import Graph

from sidol import App, Component, State
from sidol.component import _graph, reset_graph
from sidol.widgets import Column, Text

BASELINES = Path(__file__).resolve().parent / "baselines.json"
# Fail when a metric exceeds 1.5x its baseline. Generous on purpose — this
# gates order-of-magnitude regressions, not noise.
REGRESSION_THRESHOLD = 1.5
# Measurement passes per metric; the median pass mean is recorded.
REPEATS = 7

# Deterministic view-call expectations — gated by --deterministic in CI.
DETERMINISTIC_EXPECTED: dict[str, float] = {
    "view calls: full build_tree": 500.0,
    "view calls: frame (1 signal)": 1.0,
}


def build_profile() -> str:
    """Best-effort debug/release detection from the loaded extension path.

    ``maturin develop`` installs the extension into the venv under a
    profile-neutral name, so this usually reports ``unknown``. The field
    exists so a comparison across profiles can at least warn when the
    path does reveal the build directory.
    """
    import sidol._sidol_core as core

    path = (getattr(core, "__file__", "") or "").lower()
    if "release" in path:
        return "release"
    if "debug" in path:
        return "debug"
    return "unknown"


def bench(label: str, fn, number: int = 200, repeats: int = REPEATS) -> float:
    """Print and return the median per-call time of *fn* in milliseconds.

    One warmup call, then *repeats* measurement passes of *number* calls
    each. The median of the per-pass means is reported — a single outlier
    pass (GC storm, scheduler stall) cannot skew the recorded number.
    """
    fn()  # warmup
    pass_means = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(number):
            fn()
        pass_means.append((time.perf_counter() - start) / number)
    median_ms = statistics.median(pass_means) * 1000
    print(f"{label:<42} {median_ms:9.3f} ms")
    return median_ms


class Item(Component):
    label = State()
    # Probe counter: incremented by every view() call so the bench can
    # assert how many components a frame actually re-renders.
    view_calls = 0

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def view(self):
        Item.view_calls += 1
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


def run_bench() -> dict[str, float]:
    """Run every scenario and return ``{metric name: median ms}``."""
    reset_graph()
    n = 500
    app = App(ListView(n))
    app.build_tree()

    print(f"tree: {n} keyed items + root -> {len(app.build_tree().children)} children")
    print("-" * 60)
    metrics: dict[str, float] = {}
    metrics["build_tree (500 items)"] = bench(
        "build_tree (500 items)", lambda: app.build_tree(), number=100
    )
    metrics["compute_layout (800x600)"] = bench(
        "compute_layout (800x600)", lambda: app.compute_layout(800, 600), number=100
    )
    metrics["build_tree + compute_layout"] = bench(
        "build_tree + compute_layout", lambda: app.compute_layout(800, 600), number=50
    )

    item0 = app.root._keyed_children["item-0"]

    def mutate_and_flush():
        item0.label = "changed"
        app.flush()

    metrics["mutate + flush (1 signal)"] = bench(
        "mutate + flush (1 signal)", mutate_and_flush, number=200
    )

    def mutate_all_and_flush():
        for i in range(n):
            app.root._keyed_children[f"item-{i}"].label = "x"
        app.flush()

    metrics["mutate all + flush (500 signals)"] = bench(
        "mutate all + flush (500 signals)", mutate_all_and_flush, number=20
    )

    # The surface's per-frame path: drain the dirty set, then build_tree
    # with it — re-renders only dirty components, splices cached subtrees.
    frame_gen = itertools.count()

    def mutate_and_frame():
        item0.label = f"gen-{next(frame_gen)}"
        app.build_tree(set(_graph.drain_dirty()))

    metrics["mutate + frame (1 signal)"] = bench(
        "mutate + frame (1 signal)", mutate_and_frame, number=200
    )

    # Deterministic probe (P1.1): how many components a frame re-renders.
    # The full rebuild renders every component; the incremental frame with
    # one dirty signal must render only that component.
    Item.view_calls = 0
    app.build_tree()
    metrics["view calls: full build_tree"] = float(Item.view_calls)
    print(f"{'view calls: full build_tree':<42} {Item.view_calls:9.0f}")

    Item.view_calls = 0
    item0.label = "probe-unique"
    app.build_tree(set(_graph.drain_dirty()))
    metrics["view calls: frame (1 signal)"] = float(Item.view_calls)
    print(f"{'view calls: frame (1 signal)':<42} {Item.view_calls:9.0f}")

    def graph_prop():
        g = Graph()
        ids = [g.create_signal() for _ in range(1000)]
        for src, dst in zip(ids, ids[1:], strict=False):
            g.add_dependency(src, dst)
        for _ in range(50):
            g.mark_dirty(ids[0])
            g.drain_dirty()

    metrics["graph: 1000-chain mark_dirty x50"] = bench(
        "graph: 1000-chain mark_dirty x50", graph_prop, number=100
    )

    reset_graph()
    return metrics


def write_baselines(metrics: dict[str, float], profile: str) -> None:
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "build_profile": profile,
        "metrics": metrics,
    }
    BASELINES.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def check(metrics: dict[str, float], profile: str, threshold: float) -> None:
    """Gate the run against ``baselines.json`` (recording it if missing)."""
    if not BASELINES.exists():
        write_baselines(metrics, profile)
        print(f"\nno baselines file found — baseline recorded: {BASELINES}")
        print("re-run with --check to gate against it.")
        return

    data = json.loads(BASELINES.read_text(encoding="utf-8"))
    baseline_metrics: dict[str, float] = data.get("metrics", {})
    recorded_profile = data.get("build_profile", "unknown")
    if recorded_profile != profile:
        print(
            f"\nWARNING: baselines were recorded with build profile "
            f"{recorded_profile!r}, current profile is {profile!r}. Debug "
            "vs release numbers differ by an order of magnitude, so this "
            "comparison may not be meaningful — re-record with --update "
            "if the profile changed."
        )

    regressions: list[tuple[str, float, float]] = []
    improvements: list[tuple[str, float, float]] = []
    checked = 0
    for name, baseline in baseline_metrics.items():
        if name not in metrics:
            print(f"note: baseline metric {name!r} was not measured in this run")
            continue
        checked += 1
        current = metrics[name]
        if current > baseline * threshold:
            regressions.append((name, baseline, current))
        elif current < baseline / threshold:
            improvements.append((name, baseline, current))
    for name in metrics:
        if name not in baseline_metrics:
            print(f"note: new metric {name!r} has no baseline to gate against")

    for name, baseline, current in improvements:
        print(f"improved: {name:<42} {baseline:9.3f} -> {current:9.3f} ms")

    if regressions:
        print(f"\nFAIL: {len(regressions)} metric(s) regressed >50% over baseline:")
        for name, baseline, current in regressions:
            pct = (current / baseline - 1) * 100 if baseline > 0 else float("inf")
            print(f"  {name:<42} {baseline:9.3f} -> {current:9.3f} ms (+{pct:.0f}%)")
        sys.exit(1)
    print(
        f"\nOK: {checked} metric(s) checked, all within the "
        f"{(threshold - 1) * 100:.0f}% threshold."
    )


def check_deterministic(metrics: dict[str, float]) -> None:
    """Gate ONLY the deterministic integer metrics — noise-free, CI-safe."""
    failures: list[tuple[str, float, float]] = []
    checked = 0
    for name, expected in DETERMINISTIC_EXPECTED.items():
        checked += 1
        actual = metrics.get(name)
        if actual is None:
            print(f"FAIL: deterministic metric {name!r} missing")
            failures.append((name, expected, float("nan")))
            continue
        if actual != expected:
            failures.append((name, expected, actual))
            print(f"FAIL: {name} expected {expected:.0f}, got {actual:.0f}")
        else:
            print(f"OK: {name} == {expected:.0f}")

    if failures:
        print(f"\nFAIL: {len(failures)} deterministic metric(s) mismatched.")
        sys.exit(1)
    print(f"\nOK: {checked} deterministic metric(s) checked, all exact.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hot-path benchmark for the Sidol reactive core."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="gate against bench/baselines.json (recording it first if missing)",
    )
    group.add_argument(
        "--update", action="store_true", help="re-record bench/baselines.json"
    )
    group.add_argument(
        "--deterministic",
        action="store_true",
        help="gate only the two deterministic view-call metrics "
        "(view calls: full build_tree == 500, frame == 1); "
        "noise-free and suitable for CI on every push",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=REGRESSION_THRESHOLD,
        help="regression multiplier over baseline at which --check fails "
        f"(default: {REGRESSION_THRESHOLD})",
    )
    args = parser.parse_args()

    profile = build_profile()
    metrics = run_bench()

    if args.update:
        write_baselines(metrics, profile)
        print(f"\nbaselines updated: {BASELINES} (build profile: {profile})")
        return
    if args.deterministic:
        check_deterministic(metrics)
        return
    if args.check:
        check(metrics, profile, args.threshold)


if __name__ == "__main__":
    main()
