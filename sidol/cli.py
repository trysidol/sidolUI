"""CLI entry points — `sidol build` and `sidol dev`.

Both are structural stubs that raise NotImplementedError rather than
silently doing nothing. Defining the command shape early means app
authors' pyproject.toml scripts and CI pipelines won't need to change
when bundling is actually implemented.

Mobile targets intentionally excluded — bundling for iOS/Android is
roughly the same scope as the rest of the framework combined. Added
when Phase 2 ships.
"""

from __future__ import annotations

import argparse
import sys


def _build(args: argparse.Namespace) -> None:
    raise NotImplementedError(
        f"`sidol build --target={args.target}` isn't implemented yet. "
        "Desktop bundling/signing is scoped for after the GPU render "
        "surface (Phase 2) ships — see the architecture doc, Section 3."
    )


def _dev(args: argparse.Namespace) -> None:
    raise NotImplementedError(
        "`sidol dev` (file-watching / hot reload) isn't implemented yet. "
        "For now: re-run your script for Python-only changes (no rebuild "
        "needed), and `maturin develop` after Rust-side changes."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sidol")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Bundle an app for distribution")
    build_parser.add_argument("--target", choices=["windows", "macos", "linux"], required=True)
    build_parser.set_defaults(func=_build)

    dev_parser = subparsers.add_parser("dev", help="Run with file-watching")
    dev_parser.set_defaults(func=_dev)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
