"""Shared fixtures for the Sidol test suite."""

from __future__ import annotations

import gc

import pytest

from sidol.component import reset_graph


@pytest.fixture(autouse=True)
def isolated_graph():
    """Reset the global graph before/after every test so state never leaks.
    Added after debugging a "passes in isolation, fails in suite" issue.

    The teardown also runs a gc pass: component trees form reference cycles
    (cached views + ``_tree_parent``), so an abandoned App lingers in the
    live-app set until a collection runs — without this, the single-app
    warning fires nondeterministically in whichever test follows."""
    reset_graph()
    yield
    reset_graph()
    gc.collect()
