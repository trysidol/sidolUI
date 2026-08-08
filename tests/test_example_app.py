"""End-to-end smoke test for the example app.

Exercises the same path the TUI takes (build_tree -> compute_layout -> flush
after an event handler mutates state) without requiring a terminal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sidol.app import App
from sidol.component import reset_graph
from sidol.node import Node

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _load_example(name: str):
    spec = importlib.util.spec_from_file_location(
        f"example_{name}", EXAMPLES / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_text(node: Node) -> list[str]:
    texts: list[str] = []
    if node.kind == "text":
        texts.append(node.props.get("content", ""))
    for child in node.children:
        if isinstance(child, Node):
            texts.extend(_collect_text(child))
    return texts


def test_counter_example_runs_headless() -> None:
    reset_graph()
    try:
        module = _load_example("counter")
        app = module.app
        assert isinstance(app, App)

        tree = app.build_tree()
        assert _collect_text(tree) == ["Count: 0"]

        rects = app.compute_layout(200, 100)
        assert rects
        assert {r["kind"] for r in rects} >= {"column", "row", "text", "button"}

        app.root.increment()
        app.flush()
        assert _collect_text(app.build_tree()) == ["Count: 1"]

        app.root.decrement()
        app.root.decrement()
        app.flush()
        assert _collect_text(app.build_tree()) == ["Count: -1"]
    finally:
        reset_graph()
