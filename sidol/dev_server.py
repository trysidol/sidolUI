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

import json
import os
import queue
import signal
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from sidol._reload import re_execute_module
from sidol.app import App
from sidol.surfaces.html import _html_template, _nest_by_depth

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

    Hooks into ``App.flush()`` so every state change pushes to the browser.
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
        self._port = port or _DEFAULT_PORT
        self._viewport_w = viewport_w
        self._viewport_h = viewport_h
        self._verbosity = verbosity

        # File watching
        self._watch_paths: list[str] = (
            [watch] if isinstance(watch, str) else (watch or [])
        )
        self._module = module  # the loaded module, re-executed on hot-reload

        # One queue per SSE client so every connected browser receives every
        # update instead of competing for items from one shared queue.
        self._client_queues: set[queue.Queue[str | None]] = set()
        # Latest body HTML — guarded by lock.
        self._lock = threading.Lock()
        self._latest_body = ""
        # Whether the server is shutting down.
        self._shutdown = threading.Event()
        # The underlying HTTP server (set during run()).
        self._server: _Server | None = None
        # The actual port the server is listening on (set during run()).
        self._actual_port: int | None = None

        # Hook into App.flush() so every state change auto-pushes.
        self._orig_flush = app.flush
        app.flush = self._flush_and_rebuild  # type: ignore[method-assign]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the server to shut down. Safe to call multiple times."""
        self._shutdown.set()
        with self._lock:
            queues = tuple(self._client_queues)
        for client_queue in queues:
            client_queue.put(None)
        if self._server:
            self._server.shutdown()
        self._restore_flush()

    @property
    def port(self) -> int | None:
        """The actual port the server is listening on, or None before run()."""
        return self._actual_port

    def rebuild(self) -> str:
        """Recompute layout, generate HTML body, push to all SSE clients."""
        with self._lock:
            app = self._app
        rects = app.compute_layout(self._viewport_w, self._viewport_h)
        body = _nest_by_depth(rects)
        with self._lock:
            self._latest_body = body
        self._broadcast(body)
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

        if port != self._port:
            self._log(f"Port {self._port} was in use, using port {port} instead.")

        self._actual_port = port

        self._server = _Server(
            (self._host, port),
            _make_handler(self),
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
            self._broadcast(None)
            if self._server:
                self._server.server_close()
            self._restore_flush()

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
        3. Restores the old app's flush, swaps to the new app, hooks the new flush.
        4. Rebuilds and pushes the fresh body to all SSE clients.

        All app mutation is done under ``_lock`` so HTTP handler threads
        never see a partially-swapped ``App``.
        """
        if self._module is None:
            return

        try:
            # Restore the old app's flush before reloading (no lock needed —
            # this touches the old app, not our references).
            self._restore_flush()

            new_app: App | None = re_execute_module(self._module)
            if new_app is None:
                self._log(
                    "Hot-reload: no usable loader or no `app` variable in "
                    "reloaded module — keeping old app",
                    err=True,
                )
                self._rehook_flush()
                return

            # Swap app references under the lock so handler threads are safe.
            with self._lock:
                self._app = new_app
                self._orig_flush = new_app.flush
                new_app.flush = self._flush_and_rebuild  # type: ignore[method-assign]

            # Rebuild and push.
            self.rebuild()
            self._log(f"Hot-reloaded after change to {os.path.basename(changed_path)}")

        except Exception as exc:
            self._log(f"Hot-reload failed: {exc}", err=True)
            try:
                self._rehook_flush()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush_and_rebuild(self) -> None:
        """Call the original flush, then rebuild and push to browser."""
        self._orig_flush()
        self.rebuild()

    def _restore_flush(self) -> None:
        """Restore the original ``App.flush()``, undoing the hook."""
        try:
            self._app.flush = self._orig_flush  # type: ignore[method-assign]
        except Exception:
            pass

    def _rehook_flush(self) -> None:
        """Re-install the flush hook on ``self._app``."""
        with self._lock:
            self._orig_flush = self._app.flush
            self._app.flush = self._flush_and_rebuild  # type: ignore[method-assign]

    def _register_client(self) -> queue.Queue[str | None]:
        client_queue: queue.Queue[str | None] = queue.Queue()
        with self._lock:
            self._client_queues.add(client_queue)
        return client_queue

    def _unregister_client(self, client_queue: queue.Queue[str | None]) -> None:
        with self._lock:
            self._client_queues.discard(client_queue)

    def _broadcast(self, item: str | None) -> None:
        with self._lock:
            queues = tuple(self._client_queues)
        for client_queue in queues:
            client_queue.put(item)

    def _find_port(self) -> int | None:
        """Find an available port starting from ``self._port``."""
        import socket

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


# ---------------------------------------------------------------------------
# Threaded HTTP server
# ---------------------------------------------------------------------------


class _Server(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server — one thread per connection.

    ``ThreadingMixIn`` must come before ``HTTPServer`` in the MRO for
    proper ``socketserver`` initialisation.
    """

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


def _make_handler(server: DevServer) -> type[BaseHTTPRequestHandler]:
    """Create a per-instance request handler class."""

    class DevRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            if server._verbosity > 1:
                super().log_message(format, *args)

        # ------------------------------------------------------------------
        # Routing
        # ------------------------------------------------------------------

        def do_GET(self) -> None:
            try:
                if self.path == "/":
                    self._serve_page()
                elif self.path == "/events":
                    self._serve_sse()
                elif self.path == "/state":
                    self._serve_state()
                elif self.path == "/health":
                    self._serve_health()
                else:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"404 Not Found")
            except (BrokenPipeError, ConnectionResetError):
                pass

        # ------------------------------------------------------------------
        # GET /  —  Full HTML page with embedded SSE JS
        # ------------------------------------------------------------------

        def _serve_page(self) -> None:
            with server._lock:
                body = server._latest_body
            html = _html_template(
                server._viewport_w,
                server._viewport_h,
                body,
                live_reload=True,
                sse_url="/events",
            )
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(raw)

        # ------------------------------------------------------------------
        # GET /events  —  Server-Sent Events stream
        # ------------------------------------------------------------------

        def _serve_sse(self) -> None:
            server._log("Browser connected (SSE)")
            client_queue = server._register_client()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                with server._lock:
                    initial = server._latest_body
                if initial:
                    _sse_write(self.wfile, initial)

                while not server._shutdown.is_set():
                    try:
                        item = client_queue.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if item is None:
                        break
                    _sse_write(self.wfile, item)
            finally:
                server._unregister_client(client_queue)
            server._log("Browser disconnected (SSE)")

        # ------------------------------------------------------------------
        # GET /state  —  Layout rects as JSON
        # ------------------------------------------------------------------

        def _serve_state(self) -> None:
            with server._lock:
                app = server._app
            rects = app.compute_layout(
                server._viewport_w, server._viewport_h
            )
            payload = json.dumps(rects, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)

        # ------------------------------------------------------------------
        # GET /health  —  Health check
        # ------------------------------------------------------------------

        def _serve_health(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

    return DevRequestHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_write(wfile: Any, data: str) -> None:
    """Write one SSE ``data:`` frame, splitting newlines across lines."""
    for line in data.split("\n"):
        wfile.write(f"data:{line}\n".encode())
    wfile.write(b"\n\n")
    wfile.flush()


def _try_open_browser(url: str) -> None:
    """Open *url* in the default browser. Non-blocking, non-fatal."""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
