"""CLI entry points — ``sidol dev`` and ``sidol build``.

- ``sidol dev <app.py>`` — launch the native app surface with hot-reload
- ``sidol build`` — structural stub (bundling ships after GPU surface)
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

from sidol._reload import re_execute_module


def _build(args: argparse.Namespace) -> None:
    raise NotImplementedError(
        f"`sidol build --target={args.target}` isn't implemented yet. "
        "Desktop bundling/signing is scoped for after the GPU render "
        "surface (Phase 2) ships."
    )


def _reloader(module):
    """Return a hot-reload callable for the loaded app module."""

    def reload(_path: str):
        try:
            return re_execute_module(module)
        except Exception as exc:
            print(f"[sidol] reload failed: {exc}", file=sys.stderr, flush=True)
            return None

    return reload


def _dev(args: argparse.Namespace) -> None:
    """Import the user's app module and launch its native app surface."""
    app_path = args.app_path
    if not os.path.exists(app_path):
        print(f"Error: file not found: {app_path}", file=sys.stderr)
        sys.exit(1)

    # Add the parent directory to sys.path so the module can be imported.
    abs_path = os.path.abspath(app_path)
    parent = os.path.dirname(abs_path)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    module_name = os.path.splitext(os.path.basename(app_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        print(f"Error: could not load module: {app_path}", file=sys.stderr)
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Required for re-execution during hot-reload.
    sys.modules[module.__name__] = module

    # Look for an ``app`` variable in the module.
    app = getattr(module, "app", None)
    if app is None:
        print(
            f"Error: no ``app`` variable found in {app_path}.\n"
            "Create an App instance named ``app`` at module level:\n"
            "    from sidol import App\n"
            "    app = App(MyComponent())",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.watch:
        from sidol.surfaces.tui import TuiSurface

        TuiSurface(
            app,
            watch=[abs_path],
            reloader=_reloader(module),
        ).run()
    else:
        app.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sidol")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Bundle an app for distribution")
    build_parser.add_argument("--target", choices=["windows", "macos", "linux"], required=True)
    build_parser.set_defaults(func=_build)

    dev_parser = subparsers.add_parser("dev", help="Launch the native app surface with hot-reload")
    dev_parser.add_argument("app_path", help="Path to the Python file containing your App")
    dev_parser.add_argument(
        "--no-watch",
        dest="watch",
        action="store_false",
        help="Disable hot-reload on file change",
    )
    dev_parser.set_defaults(watch=True, func=_dev)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
