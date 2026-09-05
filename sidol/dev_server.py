"""Optional live HTML preview server with hot-reload.

Write your app, save the file, and the browser updates instantly.
No restarting the server.

Usage::

    from sidol.dev_server import DevServer

    app = App(MyForm())
    DevServer(app, watch="my_app.py").run()

The normal ``sidol dev`` command launches the native application surface;
use ``DevServer`` explicitly when an HTML preview is useful.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from typing import Any

from sidol._dev_http import _Server, _SseHub, make_request_handler
from sidol._reload import re_execute_module
from sidol.app import App, _multiple_apps_allowed
from sidol.concurrency import cancel_all_workers
from sidol.surfaces.html import _nest_by_depth

# Default port — 7888 doesn't conflict with any major dev server:
#   Vite 5173, Next 3000, Webpack 8080, CRA 3000, Angular 4200,
#   Flask 5000, Rails 3000, Django 8000, Nuxt 3000, Remix 3000.
_DEFAULT_PORT = 7888
_MAX_PORT_ATTEMPTS = 10
_POLL_INTERVAL = 0.05  # SSE queue poll (seconds)
_WATCH_INTERVAL = 0.3  # file-watch poll (seconds)


# ---------------------------------------------------------------------------
# DevServer
# ---------------------------------------------------------------------------


class DevServer:
    """Live-reloading HTML preview server for a Sidol ``App``.

    When *watch* is set to a file path (or a list of paths), the server
    polls those files for modification time changes and hot-reloads the
    module — replacing the ``App`` instance without restarting the HTTP
    server. All connected browser tabs update automatically.

    Hooks into ``App.flush()`` via a post-flush listener
    (``App.add_flush_listener``) so every state change pushes to the
    browser.
    """

    def __init__(
        self,
        app: App,
        host: str = "localhost",
        port: int | None = None,
        viewport_w: float = 800,
        viewport_h: float = 600,
        verbosity: int = 1,
        watch: str | list[str] | None = None,
        module: Any = None,
    ) -> None:
        self._app = app
        self._host = host
        # port=0 means "pick an ephemeral port" (see _find_port); None
        # falls back to the default.
        self._port = _DEFAULT_PORT if port is None else port
        self._viewport_w = viewport_w
        self._viewport_h = viewport_h
        self._verbosity = verbosity

        # File watching
        self._watch_paths: list[str] = (
            [watch] if isinstance(watch, str) else (watch or [])
        )
        self._module = module  # the loaded module, re-executed on hot-reload

        # Published page state + one SSE queue per connected browser, guarded
        # by the same lock that guards the app swap (topology unchanged from
        # the pre-split monolith). See sidol/_dev_http.py.
        self._lock = threading.Lock()
        self._hub = _SseHub(self._lock)
        # Whether the server is shutting down.
        self._shutdown = threading.Event()
        # The underlying HTTP server (set during run()).
        self._server: _Server | None = None
        # The actual port the server is listening on (set during run()).
        self._actual_port: int | None = None

        # Push updated HTML to browsers after every state change: a
        # post-flush listener on the app (see App.add_flush_listener).
        app.add_flush_listener(self.rebuild)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the server to shut down. Safe to call multiple times."""
        self._shutdown.set()
        self._hub.broadcast(None)
        if self._server:
            self._server.shutdown()
        self._app.remove_flush_listener(self.rebuild)
        self._app.dispose()
        cancel_all_workers()

    @property
    def port(self) -> int | None:
        """The actual port the server is listening on, or None before run()."""
        return self._actual_port

    def rebuild(self) -> str:
        """Recompute layout, generate HTML body, publish + push to SSE clients."""
        with self._lock:
            app = self._app
        rects = app.compute_layout(self._viewport_w, self._viewport_h)
        body = _nest_by_depth(rects)
        self._hub.publish(body)
        self._hub.broadcast(body)
        return body

    def run(self) -> None:
        """Start the server and file watcher. Blocks until Ctrl+C."""
        # Install signal handlers (main thread only).
        original_sigint: Any = None
        original_sigterm: Any = None
        if threading.current_thread() is threading.main_thread():
            original_sigint = signal.getsignal(signal.SIGINT)
            original_sigterm = signal.getsignal(signal.SIGTERM)

            def _handle_signal(sig: int, frame: Any) -> None:
                self._log("Shutting down...")
                self.stop()

            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)

        # Find an available port.
        port = self._find_port()
        if port is None:
            self._log(
                f"Could not find an available port after {_MAX_PORT_ATTEMPTS} "
                f"attempts. Specify a different port with --port.",
                err=True,
            )
            sys.exit(1)

        if self._port != 0 and port != self._port:
            self._log(f"Port {self._port} was in use, using port {port} instead.")

        self._actual_port = port

        self._server = _Server(
            (self._host, port),
            make_request_handler(self),
        )

        url = f"http://{self._host}:{port}"
        self._log(f"Sidol dev server ready → {url}")
        self._log("Press Ctrl+C to stop")

        # Initial render.
        self.rebuild()

        # Start the file watcher thread if paths are configured.
        if self._watch_paths:
            watcher = threading.Thread(
                target=self._watch_loop,
                daemon=True,
                name="sidol-watcher",
            )
            watcher.start()
            self._log(
                f"Watching {len(self._watch_paths)} file(s) for changes"
            )

        try:
            if self._verbosity:
                _try_open_browser(url)
            self._server.serve_forever(poll_interval=_POLL_INTERVAL)
        finally:
            if original_sigint is not None:
                signal.signal(signal.SIGINT, original_sigint)
            if original_sigterm is not None:
                signal.signal(signal.SIGTERM, original_sigterm)
            self._hub.broadcast(None)
            if self._server:
                self._server.server_close()
            self._app.remove_flush_listener(self.rebuild)
            cancel_all_workers()
            self._app.dispose()

    # ------------------------------------------------------------------
    # File watching + hot-reload
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        """Poll watched files for content changes.

        Uses file content hashing to avoid spurious reloads caused by
        ``st_mtime`` noise (filesystem rounding, startup races). When
        a file's content actually changes, triggers a hot-reload.
        Runs in a daemon thread.
        """

        _hashes: dict[str, str] = {}
        for p in self._watch_paths:
            _hashes[p] = self._file_hash(p)

        # Small startup delay to allow file writes to settle.
        time.sleep(_WATCH_INTERVAL)

        while not self._shutdown.is_set():
            time.sleep(_WATCH_INTERVAL)
            for p in self._watch_paths:
                try:
                    new_hash = self._file_hash(p)
                except OSError:
                    continue
                if new_hash != _hashes.get(p):
                    _hashes[p] = new_hash
                    self._hot_reload(p)

    @staticmethod
    def _file_hash(path: str) -> str:
        """Return an MD5 hex digest of the file's full contents.

        The whole file is hashed — a partial hash would silently miss
        changes below the cutoff. Missing/unreadable files return "".
        """
        import hashlib
        try:
            with open(path, "rb") as f:
                data = f.read()
            return hashlib.md5(data).hexdigest()
        except OSError:
            return ""

    def _hot_reload(self, changed_path: str) -> None:
        """Reload the app module and swap the internal ``App`` reference.

        1. Re-executes the module via ``re_execute_module``.
        2. Extracts the new ``app`` variable.
        3. Disposes the old app, swaps to the new app, attaches the
           post-flush listener to it.
        4. Rebuilds and pushes the fresh body to all SSE clients.

        All app mutation is done under ``_lock`` so HTTP handler threads
        never see a partially-swapped ``App``.
        """
        if self._module is None:
            return

        try:
            # The re-executed module constructs the replacement App while
            # the old one is still alive — the supported swap window (the
            # old app is disposed right after), so the single-app warning
            # is suppressed.
            with _multiple_apps_allowed():
                new_app: App | None = re_execute_module(self._module)
            if new_app is None:
                self._log(
                    "Hot-reload: no usable loader or no `app` variable in "
                    "reloaded module — keeping old app",
                    err=True,
                )
                return

            # Swap app references under the lock so handler threads are safe.
            with self._lock:
                self._app.dispose()
                cancel_all_workers()
                self._app = new_app
                new_app.add_flush_listener(self.rebuild)

            # Rebuild and push.
            self.rebuild()
            self._log(f"Hot-reloaded after change to {os.path.basename(changed_path)}")

        except Exception as exc:
            self._log(f"Hot-reload failed: {exc}", err=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_port(self) -> int | None:
        """Find an available port starting from ``self._port``.

        A requested port of 0 asks the OS for an ephemeral port: bind
        once with port 0, read back the assigned port, and return it.
        """
        import socket

        if self._port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((self._host, 0))
                return s.getsockname()[1]

        for offset in range(_MAX_PORT_ATTEMPTS):
            candidate = self._port + offset
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((self._host, candidate))
                    return candidate
                except OSError:
                    continue
        return None

    def _log(self, message: str, *, err: bool = False) -> None:
        """Print a timestamped log line to stderr.

        Error messages are always displayed, regardless of verbosity.
        Info messages respect the *verbosity* setting.
        """
        if not self._verbosity and not err:
            return
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {message}", file=sys.stderr, flush=True)


def _try_open_browser(url: str) -> None:
    """Open *url* in the default browser. Non-blocking, non-fatal."""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
