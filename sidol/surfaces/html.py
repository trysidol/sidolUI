"""HTML export surface — renders a Sidol Component tree as a standalone HTML page.

Usage::

    from sidol.surfaces.html import export_html

    app = App(MyForm())
    export_html(app, "output.html", 800, 600)
    # -> writes a self-contained HTML file you can open in any browser
"""

from __future__ import annotations

from sidol.app import App
from sidol.theme import get_theme

__all__ = ["export_html", "export_tree_to_html"]


def export_html(
    app: App,
    path: str,
    viewport_w: float = 800,
    viewport_h: float = 600,
) -> None:
    """Build the tree, compute layout, and write a standalone HTML file."""
    html = export_tree_to_html(app, viewport_w, viewport_h)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def export_tree_to_html(
    app: App,
    viewport_w: float = 800,
    viewport_h: float = 600,
) -> str:
    """Build the tree, compute layout, and return the HTML string.

    The output uses absolute positioning matching the computed layout
    rects. Each widget gets a ``<div>`` at its exact ``(x, y)`` position
    with its computed ``(w, h)`` size. Styling (colors, fonts, borders)
    is inlined from the layout rect's ``fg``/``bg``/``variant`` fields.
    """
    rects = app.compute_layout(viewport_w, viewport_h)
    body = _nest_by_depth(rects)
    return _html_template(viewport_w, viewport_h, body)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


def _nest_by_depth(rects: list[dict]) -> str:
    """Build nested HTML divs from a flat pre-order list with depth info.

    Each rect becomes a ``<div>``. Container elements (row/column) open
    a div that encloses their children (depth increases then decreases
    in pre-order traversal). Leaf elements (text, button, spacer) are
    self-contained.
    """
    if not rects:
        return ""

    theme = get_theme()
    font_size = theme.typography.size
    spacing = theme.spacing.scale(1)

    lines: list[str] = []
    prev_depth = 0
    container_offsets: list[tuple[float, float]] = []

    for r in rects:
        depth = r["depth"]

        while prev_depth > depth:
            lines.append("</div>")
            prev_depth -= 1
            container_offsets.pop()

        indent = "  " * (depth + 1)
        kind = r["kind"]
        parent_x, parent_y = container_offsets[-1] if container_offsets else (0, 0)
        style = (
            f"position:absolute;"
            f"left:{_f(r['x'] - parent_x)}px;top:{_f(r['y'] - parent_y)}px;"
            f"width:{_f(r['w'])}px;height:{_f(r['h'])}px;"
        )

        fg = r.get("fg", "")
        bg = r.get("bg", "")
        if bg and bg.startswith("#"):
            style += f"background:{bg};"
        if fg and fg.startswith("#"):
            style += f"color:{fg};"

        if kind == "button":
            border_color = fg if fg.startswith("#") else "#0A84FF"
            radius = int(r.get("radius", 6))
            style += (
                f"border:2px solid {border_color};"
                f"border-radius:{radius}px;"
                f"padding:{spacing}px;"
                f"display:flex;align-items:center;justify-content:center;"
                f"font-family:monospace;font-size:{font_size}px;"
                f"cursor:pointer;"
            )
            lines.append(
                f'{indent}<div style="{style}">'
                f"{_escape(r.get('text', ''))}"
                f"</div>"
            )
        elif kind == "text":
            style += (
                f"font-family:monospace;font-size:{font_size}px;"
                "overflow:hidden;white-space:nowrap;"
            )
            lines.append(
                f'{indent}<div style="{style}">'
                f"{_escape(r.get('text', ''))}"
                f"</div>"
            )
        elif kind in ("row", "column", "scroll_view"):
            style += "box-sizing:border-box;"
            if kind == "scroll_view":
                style += "overflow:auto;"
            lines.append(f'{indent}<div style="{style}">')
        elif kind == "spacer":
            lines.append(f'{indent}<div style="{style}"></div>')
        else:
            lines.append(f'{indent}<div style="{style}"></div>')

        # Container elements increase depth for their children.
        # Leaf elements stay at the same depth (siblings handled via while loop).
        if kind in ("row", "column", "scroll_view"):
            container_offsets.append((r["x"], r["y"]))
            prev_depth = depth + 1
        else:
            prev_depth = depth

    # Close any remaining open container divs.
    while prev_depth > 0:
        lines.append("</div>")
        prev_depth -= 1
        container_offsets.pop()

    return "\n".join(lines)


def _escape(text: str) -> str:
    """Escape HTML entities."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_template(
    viewport_w: float,
    viewport_h: float,
    body: str,
    live_reload: bool = False,
    sse_url: str = "",
) -> str:
    """Wrap the body content in a standalone HTML page.

    If *live_reload* is True, embeds a JavaScript SSE client that listens
    for ``data:`` events at *sse_url* and replaces the ``#sidol-root``
    innerHTML on each push — no full page reload needed.
    """
    sse_script = (
        f"""
<script>
(function() {{
  var es = new EventSource("{sse_url}", {{ withCredentials: false }});
  es.onmessage = function(evt) {{
    var root = document.getElementById("sidol-root");
    if (root) {{ root.innerHTML = evt.data; }}
  }};
  es.onerror = function() {{
    // Reconnect after 1s on connection loss.
    setTimeout(function() {{ es.close(); }}, 1000);
  }};
}})();
</script>
"""
        if live_reload
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sidol UI</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #f5f5f5;
    display: flex;
    justify-content: center;
    padding: 20px;
    font-family: monospace;
  }}
  #sidol-root {{
    position: relative;
    width: {_f(viewport_w)}px;
    height: {_f(viewport_h)}px;
    background: #ffffff;
    border: 1px solid #ddd;
    border-radius: 8px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.1);
    overflow: hidden;
  }}
</style>
{sse_script}</head>
<body>
<div id="sidol-root">
{body}
</div>
</body>
</html>"""


def _f(v: float) -> str:
    """Format a float as an integer string if it's a whole number."""
    if v == int(v):
        return str(int(v))
    return f"{v:.1f}"
