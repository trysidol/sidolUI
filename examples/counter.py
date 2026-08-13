"""Minimal counter app.

Run it in a terminal:

    uv run maturin develop
    uv run sidol dev examples/counter.py

Focus the buttons with Tab, activate with Enter/Space, and quit with 'q'
or Ctrl+C. The 'q' binding lives on the root Column — app-level key
bindings are app policy, not engine built-ins.
"""

from sidol import App, Component, State
from sidol.widgets import Button, Column, Row, Text


class Counter(Component):
    count = State()

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def increment(self) -> None:
        self.count += 1

    def decrement(self) -> None:
        self.count -= 1

    def view(self):
        return Column(
            Text(f"Count: {self.count}"),
            Row(
                Button("-", on_click=self.decrement),
                Button("+", on_click=self.increment),
            ),
            on_key={"q": lambda event: app.request_quit()},
        )


app = App(Counter())
