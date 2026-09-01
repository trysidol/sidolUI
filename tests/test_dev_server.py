"""Dev server and HTML surface: HTTP endpoints, hot-reload, HTML
export, and theming."""

from __future__ import annotations

import os

from sidol.app import App, _multiple_apps_allowed
from sidol.component import Component, State


def test_dev_server_serves_html() -> None:
    """The dev server serves a valid HTML page at GET /."""
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Simple(Component):
        def view(self):
            from sidol import Text

            return Text("dev test")

    app = App(Simple())
    server = DevServer(app, host="127.0.0.1", port=0, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for the server thread to pick an ephemeral port.
    for _ in range(25):
        if server.port is not None:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not pick a port within 5 seconds")
    base = f"http://127.0.0.1:{server.port}"

    for _ in range(25):
        try:
            resp = urllib.request.urlopen(f"{base}/", timeout=2)
            html = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "dev test" in html
            assert "sidol-root" in html
            assert "EventSource" in html
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not start within 5 seconds")

    server.stop()


def test_dev_server_state_endpoint() -> None:
    """The dev server serves layout rects as JSON at GET /state."""
    import json
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Simple(Component):
        def view(self):
            from sidol import Text

            return Text("state")

    app = App(Simple())
    server = DevServer(app, host="127.0.0.1", port=0, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for the server thread to pick an ephemeral port.
    for _ in range(25):
        if server.port is not None:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not pick a port within 5 seconds")
    base = f"http://127.0.0.1:{server.port}"

    for _ in range(25):
        try:
            resp = urllib.request.urlopen(f"{base}/state", timeout=2)
            data = json.loads(resp.read().decode("utf-8"))
            assert isinstance(data, list)
            assert len(data) >= 1
            assert "kind" in data[0]
            assert "x" in data[0]
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server /state endpoint did not start within 5 seconds")

    server.stop()


def test_dev_server_health_endpoint() -> None:
    """The dev server responds with 200 at GET /health."""
    import json
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Simple(Component):
        def view(self):
            from sidol import Text
            return Text("ok")

    app = App(Simple())
    server = DevServer(app, host="127.0.0.1", port=0, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for the server thread to pick an ephemeral port.
    for _ in range(25):
        if server.port is not None:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not pick a port within 5 seconds")
    base = f"http://127.0.0.1:{server.port}"

    for _ in range(25):
        try:
            resp = urllib.request.urlopen(f"{base}/health", timeout=2)
            data = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert data["status"] == "ok"
            break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server /health endpoint did not start within 5 seconds")

    server.stop()


def test_dev_server_auto_rebuild_on_flush() -> None:
    """After flush(), the server rebuilds and pushes updated HTML."""
    import threading
    import time
    import urllib.error
    import urllib.request

    from sidol.dev_server import DevServer

    class Mutable(Component):
        label = State()
        def __init__(self):
            super().__init__()
            self.label = "before"
        def view(self):
            from sidol import Text
            return Text(self.label)

    comp = Mutable()
    app = App(comp)
    server = DevServer(app, host="127.0.0.1", port=0, verbosity=0)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for the server thread to pick an ephemeral port.
    for _ in range(25):
        if server.port is not None:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not pick a port within 5 seconds")
    base = f"http://127.0.0.1:{server.port}"

    for _ in range(25):
        try:
            resp = urllib.request.urlopen(f"{base}/", timeout=2)
            html = resp.read().decode("utf-8")
            if "before" in html:
                break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.2)
    else:
        raise AssertionError("Dev server did not start within 5 seconds")

    # Mutate state and flush — the server's hook should auto-rebuild.
    comp.label = "after"
    app.flush()

    # Fetch again — should show updated content
    resp = urllib.request.urlopen(f"{base}/", timeout=2)
    html = resp.read().decode("utf-8")
    assert "after" in html, f"Expected 'after' in HTML after flush, got: {html[:200]}"

    server.stop()


def test_dev_server_hot_reload() -> None:
    """Editing the app file triggers hot-reload via importlib.reload."""
    import importlib.util
    import sys
    import tempfile

    from sidol.dev_server import DevServer

    code = """from sidol import App
from sidol.component import Component

class TestComp(Component):
    def view(self):
        from sidol import Text
        return Text("version1")

app = App(TestComp())
"""

    tmpdir = tempfile.mkdtemp(prefix="sidol_hot_")
    try:
        tmp_path = os.path.join(tmpdir, "hot_app.py")
        with open(tmp_path, "w", newline="") as f:
            f.write(code)
            f.flush()
            os.fsync(f.fileno())

        abs_path = os.path.abspath(tmp_path)
        orig_path = list(sys.path)
        sys.path.insert(0, tmpdir)
        try:
            module_name = "hot_app"
            spec = importlib.util.spec_from_file_location(module_name, abs_path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = module.app

            server = DevServer(
                app, host="127.0.0.1", port=0,
                verbosity=0, watch=tmp_path, module=module,
            )

            # Verify initial state
            tree = server._app.build_tree()
            assert "version1" in repr(tree)

            # Rewrite the file
            new_code = code.replace("version1", "version2")
            with open(tmp_path, "w", newline="") as f:
                f.write(new_code)
                f.flush()
                os.fsync(f.fileno())

                # Trigger hot-reload
                server._hot_reload(tmp_path)

                # Verify reloaded state via server
                tree = server._app.build_tree()
            assert "version2" in repr(tree), f"Expected version2 in tree, got: {repr(tree)[:200]}"

            # Verify the module itself was reloaded. The verification module
            # constructs a second App alongside the live server app — a
            # legitimate multi-app window.
            importlib.invalidate_caches()
            # Clear compiled cache so SourceFileLoader re-reads the .py
            cached = importlib.util.cache_from_source(abs_path)
            try:
                os.remove(cached)
            except OSError:
                pass
            with _multiple_apps_allowed():
                spec_verify = importlib.util.spec_from_file_location("hot_app_verify", abs_path)
                m_verify = importlib.util.module_from_spec(spec_verify)
                assert spec_verify.loader is not None
                spec_verify.loader.exec_module(m_verify)
            verify_tree = m_verify.app.build_tree()
            assert "version2" in repr(verify_tree), (
                f"Module reload returned old content: {repr(verify_tree)[:200]}"
            )

            # Stop the server so its threads release the app — otherwise it
            # stays alive for the rest of the session and trips the
            # single-app invariant in later tests.
            server.stop()
            # Release the apps this test created: the original was replaced
            # by the swap, and the verification module's app was one-shot.
            app.dispose()
            m_verify.app.dispose()
        finally:
            sys.path = orig_path
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir)
        except OSError:
            pass


def test_export_html_simple_tree() -> None:
    """export_html produces a valid HTML string with the right structure."""
    from sidol.surfaces.html import export_tree_to_html

    class Simple(Component):
        def view(self):
            from sidol import Button, Column, Text

            return Column(
                Text("Hello"),
                Button("Click"),
                spacing=4,
            )

    app = App(Simple())
    html = export_tree_to_html(app, 400, 300)

    # Must be a complete HTML page
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html
    # Must contain the viewport size
    assert "width:400px" in html or "400" in html
    # Must contain widget text
    assert "Hello" in html
    assert "Click" in html


def test_export_html_renders_textfield() -> None:
    """export_html handles TextField content correctly."""
    from sidol.surfaces.html import export_tree_to_html
    from sidol.widgets.textfield import TextField

    class Form(Component):
        def __init__(self):
            super().__init__()
            self.tf = TextField(label="Name", initial="Alice")

        def view(self):
            from sidol import Button, Column

            return Column(self.tf, Button("Submit"), spacing=4)

    app = App(Form())
    html = export_tree_to_html(app, 500, 400)

    # Must be valid HTML
    assert html.startswith("<!DOCTYPE html>")
    # Must contain labels and button text
    assert "Alice" in html
    assert "Submit" in html
    # Must not contain Python object representations
    assert "<built-in" not in html
    assert "object at" not in html


def test_export_html_writes_file() -> None:
    """export_html writes a readable file to disk."""
    import tempfile

    from sidol.surfaces.html import export_html

    class Simple(Component):
        def view(self):
            from sidol import Text

            return Text("file test")

    app = App(Simple())
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        path = f.name

    try:
        export_html(app, path, 300, 200)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "file test" in content
        assert content.startswith("<!DOCTYPE html>")
    finally:
        os.unlink(path)


def test_html_uses_themed_radius_and_spacing() -> None:
    from sidol._sidol_core import compute_layout

    from sidol.surfaces.html import _nest_by_depth
    from sidol.theme import Colors, Spacing, Style, Theme, Typography, set_theme
    from sidol.widgets import Button

    set_theme(
        Theme(
            colors=Colors(primary="#123456"),
            spacing=Spacing(unit=8),
            typography=Typography(size=18),
        )
    )
    try:
        rects = compute_layout(Button("Go"), 200, 100)
        body = _nest_by_depth(rects)
        assert "border-radius:6px" in body
        assert "padding:8px" in body
        assert "font-size:18px" in body

        rounded = compute_layout(Button("Go", style=Style(radius=12)), 200, 100)
        assert "border-radius:12px" in _nest_by_depth(rounded)
    finally:
        set_theme(Theme())
