# Raw HTTP passthrough verb (`crm http GET|POST|... <path>`)

This CLI does not ship a generic raw-request verb that forwards an arbitrary
method + path to the Web API. The escape hatches are the two surfaces that
already exist: the `query odata` positional (bare entity sets **and**
bound-function/metadata paths) and `crm batch` (arbitrary GET/POST/PATCH/DELETE
operations from a spec file).

## Why this is out of scope

**The motivating gaps were closed instead.** The request (#235) was driven by
two metadata routes with no dedicated command — alternate keys and navigation
property names. Both got first-class answers in the same triage round:
`metadata keys` (#232) and the corrected `bind_key` / `metadata relationships`
nav-property output (#228).

**The "accident of URL construction" premise was wrong.** `query odata`'s
input validation deliberately admits path-shaped args; it rejects only args
carrying `?` or `$` (params belong in `--select`/`--filter`/... flags). The
core comment reasons about bound-function paths explicitly — passthrough is a
design decision, not a leak. What was genuinely missing was *documentation* of
that contract plus a regression test locking it, which is filed as its own
issue rather than solved by adding a third surface.

**A raw verb undermines the per-verb safety model.** Destructive-operation
gating (permission hooks, `--dry-run` semantics, mutation confirmation) is
keyed on command verbs. `crm http DELETE accounts(<guid>)` would route a
mutation through a verb the gates don't recognize. A GET-only variant avoids
that but — as the request itself anticipated ("POST/PATCH support optional and
gated") — the pressure to add write methods means re-implementing per-method
gating inside one command, permanently.

**Standing cost for redundant capability.** A new command module means
PyInstaller hiddenimports, docs, skill sync, catalogue/serialization, and
two-target tests — maintenance forever, for requests `query odata` and `batch`
can already make.

## Prior requests

- #235 — "Feature: raw http verb for metadata paths not covered by dedicated commands"
