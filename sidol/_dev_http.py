"""HTTP/SSE transport for the dev server (``sidol.dev_server``).

The threaded HTTP server, the request handler (page / SSE stream / state
JSON / health), the per-client SSE hub, and the SSE frame writer live
here; ``DevServer`` keeps the orchestration (app swap, hot-reload, port
logic, logging). The handler reads a few private attributes of the
``DevServer`` instance it is constructed for — the two modules are
defined together and change together.
"""

from __future__ import annotations

import json
import queue
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from sidol.surfaces.html import _html_template

if TYPE_CHECKING:
    from sidol.dev_server import DevServer


class _Server(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server — one thread per connection.

    ``ThreadingMixIn`` must come before ``HTTPServer`` in the MRO for
    proper ``socketserver`` initialisation.
    """

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class _SseHub:
    """Published page state plus one SSE queue per connected browser.

    Every connected browser receives every update via its own queue
    instead of competing for items from one shared queue. Body and queues
    are guarded by *lock* — the DevServer's app-swap lock, so the locking
    topology is unchanged from the pre-split monolith.
    """

    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock
        self._latest_body = ""
        self._client_queues: set[queue.Queue[str | None]] = set()

    def publish(self, body: str) -> None:
        """Store the latest rendered page body."""
        with self._lock:
            self._latest_body = body

    def latest(self) -> str:
        """Return the latest rendered page body."""
        with self._lock:
            return self._latest_body

    def register(self) -> queue.Queue[str | None]:
        """Add a client queue and return it."""
        client_queue: queue.Queue[str | None] = queue.Queue()
        with self._lock:
            self._client_queues.add(client_queue)
        return client_queue

    def unregister(self, client_queue: queue.Queue[str | None]) -> None:
        """Remove a client queue (missing ones are ignored)."""
        with self._lock:
            self._client_queues.discard(client_queue)

    def broadcast(self, item: str | None) -> None:
        """Put *item* on every client queue (``None`` = disconnect)."""
        with self._lock:
            queues = tuple(self._client_queues)
        for client_queue in queues:
            client_queue.put(item)


def make_request_handler(server: DevServer) -> type[BaseHTTPRequestHandler]:
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
            body = server._hub.latest()
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
            client_queue = server._hub.register()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                initial = server._hub.latest()
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
                server._hub.unregister(client_queue)
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


def _sse_write(wfile: Any, data: str) -> None:
    """Write one SSE ``data:`` frame, splitting newlines across lines."""
    for line in data.split("\n"):
        wfile.write(f"data:{line}\n".encode())
    wfile.write(b"\n\n")
    wfile.flush()
