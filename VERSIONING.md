# Versioning & Stability Policy

Sidol follows **semantic versioning** (MAJOR.MINOR.PATCH) from the first
public 0.x release onward.

## The guarantee

- **0.x releases** may break APIs between MINOR versions, but every
  breaking change is documented in CHANGELOG.md with a migration note —
  never a silent break.

- **1.0** is the first API-stability commitment: no breaking changes
  without a MAJOR version bump, full stop. The 0.x period exists so the
  API can evolve based on real usage before being locked down.

- **Deprecated APIs** get at least one MINOR release carrying a
  `DeprecationWarning` before removal. No API is ever removed in the
  same release it's deprecated.

- **The roadmap** is visible in public GitHub Issues/Projects, not
  private notes. Direction changes are discussed before they land,
  not announced after.

## Why this exists

The most commonly reported reason Python GUI projects get abandoned
mid-adoption is a framework changing direction or disappearing without
warning, forcing users into a rewrite. This policy exists to directly
address that: Sidol commits to predictable deprecation, visible
roadmaps, and no silent breaks. Trust is a feature.

## What counts as a breaking change

- Removal or renaming of a public API.
- Change in function signature (parameter removed, required param added).
- Change in the return type or semantics of a public function.

Not breaking:
- Adding new public APIs (backward-compatible extension).
- Changing internal/private APIs (those prefixed with `_`).
- Performance improvements, bug fixes that don't change documented behaviour.
- Dependency version bumps that don't affect Sidol's public API.
