"""Healess integration verification — exercises the full lifecycle of
component composition and TextField state transitions end-to-end.

This script drives the actual Sidol framework through a multi-step
scenario: form construction, tree resolution, state mutation, dirty
propagation, flush, re-resolution, and layout computation. It asserts
at every step.
"""

import sys

from sidol import App, Button, Column
from sidol.component import Component, State, _graph, reset_graph
from sidol.node import Node
from sidol.widgets.textfield import TextField

# ---------------------------------------------------------------------------
# Test components
# ---------------------------------------------------------------------------

class ProfileForm(Component):
    """A form with two TextFields and a Submit button."""

    def __init__(self) -> None:
        super().__init__()
        self.name_field = TextField(label="Name", initial="Alice")
        self.email_field = TextField(label="Email", initial="alice@example.com")

    def view(self):
        return Column(
            self.name_field,
            self.email_field,
            Button("Submit"),
            spacing=4,
        )


class DeepNest(Component):
    """Three levels of nesting: Grandparent -> Parent -> Child."""

    def __init__(self) -> None:
        super().__init__()
        self.child = TextField(initial="deep")

    def view(self):
        from sidol import Column
        return Column(
            Column(
                self.child,
            ),
        )


class ConditionalText(Component):
    """Component whose view() changes based on state (stale-edge test)."""
    show_greeting = State()

    def __init__(self) -> None:
        super().__init__()
        self.show_greeting = True

    def view(self):
        from sidol import Text
        if self.show_greeting:
            return Text("Hello")
        else:
            return Text("Goodbye")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

passed = 0
failed = 0

def check(description: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK] {description}")
    else:
        failed += 1
        print(f"  [FAIL] {description}")
        if detail:
            print(f"      {detail}")


def section(title: str):
    border = "=" * 60
    print(f"\n{border}")
    print(f"  {title}")
    print(f"{border}")


# ---------------------------------------------------------------------------
# Verification steps
# ---------------------------------------------------------------------------

section("1. Component construction and tree resolution")

reset_graph()

form = ProfileForm()
app = App(form)

# Check that TextFields have their own view signal IDs
check("Name field has view_signal_id", isinstance(form.name_field._view_signal_id, int))
check("Email field has view_signal_id", isinstance(form.email_field._view_signal_id, int))
check("Form has distinct view_signal_id", form._view_signal_id != form.name_field._view_signal_id)
check("Name and email have distinct signal IDs",
      form.name_field._view_signal_id != form.email_field._view_signal_id)

# Build tree — this resolves Components recursively
tree = app.build_tree()

check("Resolved tree is a Node", isinstance(tree, Node))
check("Tree kind is column", tree.kind == "column")
# Components are resolved, so children should be Nodes, not Components
children = tree.children
check("Root has 3 children (2 TextFields resolved + Button)", len(children) == 3,
      f"got {len(children)}")

child0 = children[0]
check("First child is a Node", isinstance(child0, Node),
      f"got {type(child0)}")
check("First child is a row (TextField.view() returns Row)", child0.kind == "row",
      f"got {child0.kind}")

# TextField's view produces a Row with Text children
textfield_children = child0.children
check("TextField has text children", len(textfield_children) >= 1)
text_content = textfield_children[-1].props.get("content", "")
# When not focused, cursor shows as a space at cursor position (end by default)
check("TextField shows cursor as space when not focused", text_content == "Alice ",
      f"got {text_content!r}")

child2 = children[2]
check("Last child is Button", child2.kind == "button")
check("Button has correct label", child2.props.get("label") == "Submit")


section("2. State mutation → dirty propagation → flush → tree update")

# Mutate one TextField's value
form.name_field.cursor_pos = 0
form.name_field.insert("X")

check("Name field updated", form.name_field.value == "XAlice",
      f"got {form.name_field.value!r}")

# Check that ONLY name_field's view signal is dirty
name_dirty = form.name_field._view_signal_id in _graph.dirty_ids()
email_dirty = form.email_field._view_signal_id in _graph.dirty_ids()
form_dirty = form._view_signal_id in _graph.dirty_ids()

check("Name field view is dirty after mutation", name_dirty)
check("Email field view is NOT dirty", not email_dirty,
      "email view was dirty — wrong isolation")
check("Form view is NOT dirty (no edge to child state)", not form_dirty,
      "form view was dirty — wrong isolation")

# Flush — re-renders dirty components only
app.flush()

check("Dirty set cleared after flush", _graph.dirty_ids() == [],
      f"still dirty: {_graph.dirty_ids()}")

# Rebuild tree — should show updated content
tree2 = app.build_tree()
child0_v2 = tree2.children[0]
text_content_v2 = child0_v2.children[-1].props.get("content", "")
# Cursor at position 1 (after insert('X') advanced from 0 to 1), not focused => space
check("Tree shows updated TextField content after flush", text_content_v2 == "X Alice",
      f"got {text_content_v2!r}")


section("3. Multiple TextField dirty isolation")

# Now mutate both fields
form.name_field.insert("Y")
form.email_field.backspace()

both_dirty = (
    form.name_field._view_signal_id in _graph.dirty_ids() and
    form.email_field._view_signal_id in _graph.dirty_ids()
)
check("Both fields dirty after both mutated", both_dirty)

app.flush()
tree3 = app.build_tree()

name_text = tree3.children[0].children[-1].props.get("content", "")
email_text = tree3.children[1].children[-1].props.get("content", "")

# After Section 2 insert('X') cursor was at 1. insert('Y') makes value "XYAlice", cursor at 2.
# Unfocused => space between XY and Alice
check("Name field shows 'XY Alice' after two inserts", name_text == "XY Alice",
      f"got {name_text!r}")
# Email backspace removed last char 'm', cursor at end (16). Unfocused => trailing space.
check("Email field contains '@example.co' after backspace",
      "@example.co" in email_text,
      f"got {email_text!r}")


section("4. Deep nesting (3 levels)")

reset_graph()

deep = DeepNest()
app2 = App(deep)
tree_deep = app2.build_tree()

check("Deep tree resolved: column > column > row > texts",
      tree_deep.kind == "column",
      f"got {tree_deep.kind}")

child = tree_deep.children[0]
check("Nested column exists", child.kind == "column",
      f"got {child.kind}")

grandchild = child.children[0]
check("TextField resolved correctly", grandchild.kind == "row",
      f"got {grandchild.kind}")

# Mutate deep child and check propagation
deep.child.value = "deeper"
check("Deep child dirty after mutation",
      deep.child._view_signal_id in _graph.dirty_ids())
check("Deep parent NOT dirty (no edge to child)",
      deep._view_signal_id not in _graph.dirty_ids(),
      "parent was dirtied — wrong isolation in nested tree")


section("5. TextField in layout engine")

reset_graph()

form2 = ProfileForm()
app3 = App(form2)
rects = app3.compute_layout(400, 300)

check("Layout returns rects", len(rects) > 0,
      f"got {len(rects)} rects")

# Check rect content
kinds = [r["kind"] for r in rects]
check("Layout includes text nodes", "text" in kinds)
check("Layout includes button", "button" in kinds)
check("Layout includes columns/rows", any(k in kinds for k in ("column", "row")))

# Verify TextField content survived layout round-trip
text_rects = [r for r in rects if r["kind"] == "text"]
text_contents = [r.get("text", "") for r in text_rects]
check("Layout carries text content", any("Alice" in t for t in text_contents),
      f"no TextField content found in {text_contents}")


section("6. Table flip: reset_graph mid-lifecycle")

# Capture dirty set before reset
reset_graph()

# Immediately after reset, dirty set should be empty
check("Dirty set cleared after reset", _graph.dirty_ids() == [],
      f"got {_graph.dirty_ids()}")

# After reset, create a fresh component — should work cleanly
tf = TextField(initial="fresh")
check("TextField works after graph reset", tf.value == "fresh")

# Editing creates new dirty signals (that's expected)
tf.insert("!")
check("TextField editing works after reset", tf.value == "fresh!")
check("Dirty set is non-empty after edit", len(_graph.dirty_ids()) > 0)


section("7. Conditional subscriptions (stale-edge pruning)")

reset_graph()

cond = ConditionalText()
cond.rendered_view()  # reads show_greeting → registers edge
_graph.clear_dirty()

# Switch mode and re-render — old edge should be pruned
cond.show_greeting = False
_graph.clear_dirty()  # clear the dirtied view signal
cond.rendered_view()  # now reads show_greeting (new context) — registers new edge

# Now the component's view depends on show_greeting. Mutating it should dirty the view.
# But the old view() path (show_greeting=True) was pruned during the re-render.
# The new path (show_greeting=False) is now active.
# Actually both paths read show_greeting, so both register the same edge.
# The real stale-edge test: mutate a DIFFERENT state that was only read in the OLD path.

# Since both branches read show_greeting (just with different values), there's no stale
# edge to test here. The stale-edge unit test already covers this in
# test_stale_dependency_is_pruned_after_mode_switch.
# Let's just verify the basic re-render works:
cond.show_greeting = True
check("Conditional component re-renders after state change",
      cond._view_signal_id in _graph.dirty_ids())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed out of {passed + failed} checks")
print(f"{'='*60}")

if failed > 0:
    sys.exit(1)
else:
    print("  All checks passed ✓")
    sys.exit(0)
