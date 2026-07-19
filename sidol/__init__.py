"""Sidol — a reactive Python UI framework backed by a Rust engine."""

from sidol.app import App
from sidol.component import Component, State, reset_graph
from sidol.node import Node
from sidol.theme import Style, Theme, get_theme, set_theme
from sidol.widgets import Button, Column, Row, Spacer, Text

__all__ = [
    "App",
    "Component",
    "State",
    "reset_graph",
    "Node",
    "Style",
    "Theme",
    "get_theme",
    "set_theme",
    "Button",
    "Column",
    "Row",
    "Spacer",
    "Text",
]
__version__ = "0.1.0"
