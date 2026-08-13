"""Slider — a range control with a visual bar indicator.

``Slider`` is a ``Component`` subclass with reactive state. It renders
a filled bar showing the current position within the range. Focus it
with Tab and adjust with Left/Right (Home/End jump to the bounds).

Usage::

    from sidol.widgets.slider import Slider

    class VolumeControl(Component):
        volume = State()

        def __init__(self):
            super().__init__()
            self.volume = 50.0
            self.slider = Slider(min_val=0.0, max_val=100.0, value=self.volume)

        def view(self):
            return Column(
                Text(f"Volume: {self.slider.value:.0f}"),
                self.slider,
                Row(
                    Button("-", on_click=self.slider.decrement),
                    Button("+", on_click=self.slider.increment),
                ),
            )
"""

from __future__ import annotations

from collections.abc import Callable

from sidol.component import Component, State
from sidol.events import FocusEvent
from sidol.theme import get_theme
from sidol.widgets import Row, Text


class Slider(Component):
    value = State()
    is_focused = State()

    def __init__(
        self,
        min_val: float = 0.0,
        max_val: float = 100.0,
        step: float = 1.0,
        value: float | None = None,
        width: int = 20,
    ) -> None:
        super().__init__()
        self._min = min_val
        self._max = max_val
        self._step = step
        self._width = width
        self.value = value if value is not None else min_val
        self.is_focused = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def min_val(self) -> float:
        return self._min

    @property
    def max_val(self) -> float:
        return self._max

    @property
    def step(self) -> float:
        return self._step

    @property
    def ratio(self) -> float:
        span = self._max - self._min
        if span == 0.0:
            return 0.0
        return (self.value - self._min) / span

    def increment(self) -> None:
        new_val = self.value + self._step
        if new_val <= self._max:
            self.value = round(new_val, 6)

    def decrement(self) -> None:
        new_val = self.value - self._step
        if new_val >= self._min:
            self.value = round(new_val, 6)

    def set_range(self, min_val: float, max_val: float) -> None:
        self._min = min_val
        self._max = max_val
        if self.value < min_val:
            self.value = min_val
        elif self.value > max_val:
            self.value = max_val

    # ------------------------------------------------------------------
    # Keyboard interaction
    # ------------------------------------------------------------------

    def _key_handlers(self) -> dict[str, Callable[..., object]]:
        return {
            "left": lambda event: self.decrement(),
            "right": lambda event: self.increment(),
            "home": lambda event: self._set(self._min),
            "end": lambda event: self._set(self._max),
        }

    def _set(self, value: float) -> None:
        self.value = value

    def _handle_focus(self, event: FocusEvent) -> None:
        self.is_focused = event.kind == "focus"

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def view(self) -> Row:
        ratio = self.ratio
        filled = max(0, min(self._width, int(round(ratio * self._width))))
        empty = self._width - filled
        bar = "█" * filled + "░" * empty
        fg = get_theme().colors.primary if self.is_focused else None
        return Row(
            Text(f"[{bar}]", fg=fg),
            on_key=self._key_handlers(),
            on_focus=self._handle_focus,
        )
