---
status: accepted
---

# Stay on Material 9.x for now; ProperDocs is the destination when a move is forced

[ADR 0005](0005-defer-mkdocs-2.0-stay-on-material-9x.md) ruled out the hostile core
`mkdocs` 2.0 rewrite and capped the `[docs]` extra (`mkdocs<2`, `mkdocs-material<10`).
It left open the larger question it could not yet answer: the stack is winding down
(core mkdocs 1.x last released Aug 2024; Material 9.x entered maintenance mode
2025-11-05, security/critical fixes committed only **through November 2026**), so
where do we go — stay frozen, migrate to **Zensical** (Material author's from-scratch
successor), or adopt a **fork** (MaterialX theme fork / ProperDocs core fork)? This
ADR records that decision (issue #224).

**Decision: stay on Material 9.x + mkdocs 1.x now. When a move is forced, migrate to
ProperDocs; MaterialX is the fallback.**

## Why stay

There is no forcing trigger. As of 2026-06-11: no CVE against mkdocs 1.6.x or Material
9.x core, no broken transitive dependency, and none of our load-bearing plugins
(`mkdocs-click`, `mkdocs-include-markdown-plugin`, `mkdocs-llmstxt`) have dropped 9.x
support. Material security fixes run through Nov 2026, giving real runway. Moving now
would be speculative work against zero pressure.

## Why not Zensical (yet)

Zensical is the obvious long-term successor but is disqualified today: it has **no
third-party plugin API** (it opens "early 2026", Spark-members-first), and our two
highest-risk plugins — `mkdocs-click` (generates `docs/reference/cli.md`) and
`mkdocs-llmstxt` (generates `llms-full.txt`, our agent-facing contract) — appear in
neither its supported nor backlog tiers. Migrating now means rebuilding both, with no
confirmed native equivalent. Revisit Zensical only if it ships a plugin API (or native
modules) covering both before another trigger fires.

## Why ProperDocs as the pre-committed destination

Pre-deciding turns a future scramble into a roughly one-day port:

- **ProperDocs** (oprypin — the last active MkDocs maintainer) is a *core* mkdocs fork
  with full backward compat: our entire plugin set and the Material theme keep working.
  Only CI delta is `mkdocs build --strict` → `properdocs build` in `.github/workflows/docs.yml`,
  plus installing the Material theme explicitly (ProperDocs no longer bundles it). Still
  emits `site/` → Cloudflare Pages deploy unchanged.
- **MaterialX** (fork of Material 9.7.1) is the fallback if ProperDocs stalls: a drop-in
  `theme: material` → `theme: materialx` swap, full plugin compat, CI command unchanged —
  but single-maintainer bus-factor.

## Conditions to revisit

Reconsider before the natural deadline if: a CVE or broken transitive dep lands against
the 9.x stack, or a plugin we use drops 9.x support, or Zensical ships click+llmstxt
parity. Otherwise revisit by **October 2026** (one month before Material's Nov-2026
security EOL).
