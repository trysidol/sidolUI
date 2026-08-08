"""Sidol — a reactive Python UI framework backed by a Rust engine."""

from sidol.app import App
from sidol.component import Component, State, reset_graph
from sidol.events import FocusEvent, KeyEvent, normalise_key
from sidol.node import Node
from sidol.surfaces.html import export_html
from sidol.surfaces.tui import TuiSurface
from sidol.theme import Style, Theme, get_theme, set_theme
from sidol.widgets import (
    Button,
    Column,
    Dropdown,
    List,
    Row,
    ScrollView,
    Slider,
    Spacer,
    Text,
    TextField,
)

__all__ = [
    "App",
    "Component",
    "State",
    "reset_graph",
    "FocusEvent",
    "KeyEvent",
    "normalise_key",
    "Node",
    "Style",
    "Theme",
    "get_theme",
    "set_theme",
    "TuiSurface",
    "export_html",
    "Button",
    "Column",
    "Dropdown",
    "List",
    "Row",
    "ScrollView",
    "Slider",
    "Spacer",
    "Text",
    "TextField",
]
__version__ = "0.1.0"
