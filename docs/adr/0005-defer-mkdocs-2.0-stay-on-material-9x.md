---
status: accepted
---

# Defer mkdocs 2.0; cap docs deps and stay on Material 9.x

`mkdocs build --strict` began printing an upstream banner announcing **mkdocs 2.0**
(https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/). On
inspection the "2.0" in that banner is the **core `mkdocs` package** rewrite by the
mkdocs org — not a new release of `mkdocs-material` (there is no Material 2.0). The
banner is Material's author warning users that core mkdocs 2.0 is incompatible with
Material. We do **not** upgrade to mkdocs 2.0, and we cap the `[docs]` extra so CI
cannot silently pull it.

mkdocs 2.0 is a teardown, not an upgrade, of everything our docs build on:

- **Incompatible with Material for MkDocs** — our theme stops working entirely.
- **Removes the plugin system** — kills `mkdocs-click`,
  `mkdocs-include-markdown-plugin`, `mkdocs-llmstxt`, and Material itself.
- **Navigation passed to themes as pre-rendered HTML** — makes `navigation.tabs`,
  `navigation.sections`, and `navigation.indexes` (all used in `mkdocs.yml`)
  technically impossible.
- **TOML config**, replacing our `mkdocs.yml`.
- **Currently unlicensed** — unsuitable for production use.

There is no migration path and no feature benefit; it is negative value.

## Considered options

- **Cap `mkdocs<2` (core) — chosen as the load-bearing cap.** The original issue
  (#223) emphasised capping `mkdocs-material`, but the existential threat is core
  `mkdocs` 2.0, since that is what severs Material compatibility and removes plugins.
  We cap both (`mkdocs>=1.6,<2`, `mkdocs-material>=9.5,<10`) but core mkdocs is the
  one that guts the build. We leave the three plugin pins open-ended — they are not
  the threat and follow their own versioning.
- **Upgrade to mkdocs 2.0 — rejected.** Breaks the entire stack with no migration
  path; removes features rather than adding them.
- **Migrate to Zensical now — out of scope here.** Material's own team has moved to
  **Zensical** (a from-scratch successor that reads `mkdocs.yml`). Our current stack
  is winding down — core mkdocs 1.x is effectively unmaintained (last release Aug
  2024) and Material 9.x entered maintenance mode (Nov 2025). A real
  stay-vs-Zensical-vs-fork decision is a separate, larger evaluation tracked in its
  own issue; it is not this defensive cap.

## Conditions to revisit

Reconsider only if the picture materially changes: mkdocs 2.0 ships a license **and**
a Material-compatible migration path, **or** the stack's maintenance status forces a
move — at which point the destination is most likely Zensical, not mkdocs 2.0.
