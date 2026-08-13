"""Shared module re-execution for hot-reload (``sidol dev`` and DevServer).

Both watchers were duplicating this dance with subtle differences; it
exists exactly once, here. Change *detection* stays with the callers —
the TUI loop polls mtimes every frame (cheap), the DevServer hashes
contents on a slower background thread (precise).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from types import ModuleType
from typing import Any


def re_execute_module(module: ModuleType) -> Any | None:
    """Re-execute *module* from its original spec; return its ``app`` attribute.

    Returns ``None`` when the module has no usable loader or the reloaded
    module binds no ``app``. Bytecode caches are cleared first so
    ``exec_module`` re-reads the source file rather than a stale .pyc.
    Exceptions from the module's own code propagate to the caller.
    """
    spec = module.__spec__
    if spec is None or spec.loader is None:
        return None
    importlib.invalidate_caches()
    if spec.origin is not None:
        cached = importlib.util.cache_from_source(spec.origin)
        try:
            os.remove(cached)
        except OSError:
            pass
    spec.loader.exec_module(module)
    return getattr(module, "app", None)
