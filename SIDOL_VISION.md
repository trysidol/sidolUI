# Sidol Vision

This document is the product and architecture north star for Sidol. Agents and
contributors should read it before making architectural, API, or feature
decisions.

## Product Identity

Sidol is a native, Python-first, reactive application framework powered by a
Rust runtime.

Its purpose is to let Python developers build resource-efficient, beautiful
applications without requiring a browser, webview, HTML runtime, CSS, a DSL,
or a C++ toolchain.

Sidol should support applications ranging from small utilities and todo apps
to dashboards, database managers, editors, internal tools, and larger desktop
applications.

Sidol is licensed under the MIT License. The permissive license is a deliberate
product advantage: developers and companies should be able to adopt Sidol
without the GPL/commercial licensing uncertainty associated with some Python
GUI alternatives.

## Primary User Experience

The primary development workflow is:

```text
sidol dev app.py
-> start the actual native Sidol application
-> edit Python source code
-> reflect changes in the running application
```

`sidol dev` must not open a browser. Hot reload is a core feature, not merely
a convenience. It should reload changed code reliably, clean up obsolete
resources, and preserve application state where practical and well-defined.

The running application may initially be a terminal UI. The long-term primary
desktop surface is a native GPU-rendered window.

## Non-Negotiable Principles

- Python is the only authoring interface.
- Python owns application and signal values.
- Rust owns the reactive graph, dirty propagation, layout, and rendering work.
- Reading a `State` field inside `view()` automatically tracks a dependency.
- Do not replace fine-grained signal reactivity with coarse component diffing.
- Keep the core component, state, layout, style, and event model independent of
  any one rendering surface.
- Keep the hot path out of Python and measure before adding complexity.
- Prefer small, composable Python APIs over new declarative languages.
- Headless tree building and layout must remain testable without a screen.

## Customization Philosophy

Sidol follows option-driven design: strong, good-looking defaults at every
layer, with layered override paths underneath rather than large configuration
objects.

Every visual default, including spacing, themes, typography, and color, must
be overridable at the widget, subtree, and global level through composable
style primitives, not a single kwargs pile on each widget.

Adding more configuration options is not itself a goal. An option is worth
adding only when the default is insufficient for a real use case.

The reactive core, including dirty propagation, batching, and scheduling, is
Rust-owned and should expose a minimal tuning surface to Python authors. This
is an area for correct defaults, not application-level configuration.

A Python author should be able to ignore the customization system entirely and
still get a well-designed application.

## Accessibility Is Core Model

Accessibility is not a renderer-only concern. Because Sidol renders its own
surfaces instead of using native OS widgets, visual output is invisible to
operating-system accessibility APIs unless Sidol provides a parallel semantic
tree.

The shared component model must therefore support accessibility semantics from
the beginning. Built-in widgets should generate semantic nodes automatically;
custom components should have an explicit, documented hook for describing
their role, name, value, state, actions, and relationships. Native surfaces
must expose that semantic tree to platform accessibility systems as they mature.

Accessibility must not be postponed until after the widget set is established.

## Native Runtime Rule

Sidol is not a browser framework. HTML export or preview tooling, if retained,
is optional static or debugging functionality only. It must not become the
normal runtime, the hot-reload target, or a required application dependency.

Do not introduce an embedded browser, webview, or browser-based rendering path
as a shortcut for native functionality.

## Quality Goals

Sidol should provide:

- Low idle memory and no browser process.
- Fast startup and low interaction latency.
- Efficient rendering of large widget trees.
- Reliable state updates and predictable lifecycle behavior.
- Beautiful layouts, themes, typography, forms, tables, dialogs, menus,
  scrolling, charts, and editor-oriented controls.
- Strong keyboard, mouse, focus, selection, clipboard, and accessibility
  behavior as native surfaces mature.
- Clear errors and a productive Python developer experience.

Performance claims must be backed by measurements covering startup time, idle
memory, CPU usage, render latency, large-tree behavior, and hot-reload latency.

## Hot Reload Requirements

Hot reload must be designed as a lifecycle feature. Changes must not silently
leak:

- Components and reactive graph nodes.
- Background workers and tasks.
- File watchers and event handlers.
- Database connections or other external resources.
- Native windows, surfaces, and rendering resources.

The framework should define which state survives reload and should report
reload errors without destroying a usable running application when possible.

### Survival policy

`Component.dispose()` is the deterministic teardown contract: it removes a
component's view and state signals from the graph, unregisters it, and
disposes its retained/keyed children recursively. `App.dispose()` tears down
the whole tree. Surfaces must call `dispose()` on the old app before swapping
in a reloaded app, and on surface exit.

What survives a reload is the *module-level* `app` binding the developer
rebinds — the new `App` is built fresh from re-executed code. Component state
does not survive by default; developers persist across reloads via explicit
external stores (files, databases, `remember()`-retained singletons the new
code re-constructs). Writes to a disposed component are safe no-ops that store
the value without touching the graph.

Known limitation: in-flight `Worker`s are not cancelled by disposal — their
threads run to completion, but their completion callbacks are neutralized
because they write to disposed components. A `Worker.cancel()` primitive for
eager abort is future work.

## Surface Strategy

The shared framework core should define:

- Components and state.
- Reactive invalidation.
- Layout and constraints.
- Styles and themes.
- Events, commands, focus, and accessibility semantics.

Surfaces should implement rendering and platform integration:

- TUI: terminal-first native surface available now.
- GPU: long-term native desktop surface for beautiful applications.
- HTML: optional static/debugging utility only, never the product runtime.

GPU surface means GPU-presented output, such as CPU rasterization blitted to a
GPU-backed swapchain, not necessarily GPU-side rasterization. Any future move
to GPU-side rasterization must not require a rewrite of the shared core.

New widget behavior should not be designed in a way that makes a future native
GPU surface require a rewrite.

## TUI Rendering Invariants

The terminal renderer must follow these rules:

- Update existing terminal content by overwriting it; do not clear the screen
  as a normal frame strategy.
- Emit at most one write operation per rendered frame where the terminal
  protocol permits it.
- Use the Synchronized Output protocol when the terminal supports it, with a
  safe fallback when it does not.
- Use `Fraction` or fixed-point arithmetic instead of floating-point arithmetic
  for proportional terminal width and height calculations.

These rules exist to minimize flicker, reduce terminal I/O, and keep layout
rounding deterministic.

## Product Priorities

Prioritize work in this order unless there is a strong documented reason to
deviate:

1. Correct lifecycle behavior, hot reload, and resource cleanup.
2. Stable reactive semantics and headless testability.
3. Composable layout, styling, and core widgets.
4. Native GPU rendering and polished visual primitives.
5. Tables, forms, dialogs, menus, charts, editors, and database-oriented
   controls.
6. Keyboard, mouse, clipboard, accessibility, and platform integration.
7. Simple packaging and distribution for Python users.
8. Documentation, examples, benchmarks, CI, and regression coverage.

Do not expand feature breadth by weakening the foundations above.

Scope discipline is a project survival requirement. Do not add a new surface,
large widget family, or distribution target merely because it is interesting.
First make lifecycle behavior, hot reload, reactive semantics, rendering
correctness, resource cleanup, and headless testing boring and reliable. Delay
breadth and polish when they would weaken those foundations.

## Decision Rules For Agents

Before implementing a change, agents should ask:

1. Does this make native Python applications easier to build?
2. Does it support both simple apps and serious dashboard/editor/database apps?
3. Does it preserve low resource usage and measurable performance?
4. Does it preserve automatic signal tracking and the Rust-owned hot path?
5. Does it work with live native hot reload and correct resource cleanup?
6. Does it keep the core independent from a particular surface?
7. Is it the smallest change that advances the product rather than a speculative
   abstraction?

If a proposal conflicts with this document, identify the conflict explicitly
and ask before proceeding. Do not silently redirect Sidol toward a browser,
webview, DSL, or C++-based architecture.

## Current Maturity

Sidol is pre-alpha. APIs may change, and the project should be honest about
unfinished capabilities. Native hot reload, GPU rendering, packaging,
concurrency, lifecycle management, documentation, and platform integration
must mature before claiming production readiness.
