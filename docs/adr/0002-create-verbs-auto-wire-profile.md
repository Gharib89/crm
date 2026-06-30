---
status: partially superseded by ADR 0020
---

# `solution create-publisher` / `create` auto-wire the active profile

**Partial supersession (ADR 0020).** The `default_solution` auto-wire from
`solution create` is **removed** — the profile `default_solution` field no longer
exists and `solution create --set-default` is gone. The `publisher_prefix` auto-wire
from `solution create-publisher --set-default` **remains** (it is a schema-name aid,
not a solution-targeting mechanism). The narrative below is preserved for history; the
`default_solution` portions no longer reflect current behaviour.

The new `solution create-publisher` and `solution create` verbs (issue #34) exist so
the CRMWorx prerequisite — a custom `cwx` publisher and `CRMWorx` solution — needs
zero web-UI. But creating them is only half the job: every later metadata command
(`metadata create-entity`, `add-attribute`, …) still needs `--solution CRMWorx` and a
`cwx_` schema prefix to target them. To make the headline flow actually zero-touch, on
a successful create we write the new value back to the **active named connection
profile**: `create-publisher` sets `publisher_prefix = <customizationprefix>`, `create`
sets `default_solution = <uniquename>`. Later commands then default to them via the
existing `_resolve_solution` / `_resolve_schema_name` helpers.

The write is **guarded**: default ON with a `--no-set-default` escape, **command-layer
only**, performed **only when a named (on-disk) profile is active**, and **never under
`--dry-run`**. The outcome is reported in the envelope (`data.profile_updated={…}`, or
`data.profile_update="skipped: no named profile"`).

## Considered options

- **No auto-wire (issue scope only).** The issue asked only for the two verbs.
  Rejected: re-typing `--solution CRMWorx` and the `cwx_` prefix on every subsequent
  metadata command defeats the "zero web-UI prerequisite" goal that motivated the
  verbs. The auto-wire is beyond the literal scope but is the point of the feature.
- **Unconditional write (always overwrite the profile).** Rejected as surprising: it
  would clobber a `default_solution` / `publisher_prefix` the user set deliberately,
  with no opt-out. Gating behind the create action plus a `--set-default` default
  (escapable with `--no-set-default`) keeps the mutation predictable and consensual.
- **Core writes the config.** Rejected. `crm/core/solution.py` is pyright-strict pure
  D365 — it must stay testable with a mocked backend and no profile store, and it must
  not import `session`/config. Persisting a *preference* is a CLI-UX concern, so the
  write lives in the command layer; the core functions return plain dicts and never
  touch `~/.crm`.
- **Write even for an env/dotenv connection.** Rejected: there is no named profile
  file to persist into — an env/dotenv connection surfaces as a synthetic, ephemeral
  profile. We detect a real profile via `_active_profile` (loads from
  `~/.crm/profiles`); when absent we no-op and report `skipped: no named profile`
  rather than inventing a profile to mutate.
- **Write under `--dry-run`.** Rejected: dry-run must have zero side effects, and a
  profile write is a real persisted mutation. The guard checks both `ctx.dry_run` and
  the result's `_dry_run` sentinel.

## The CRM_DOTENV tension

ADR-adjacent to #36 (`CRM_DOTENV` is authoritative for connection credentials, no
`./.env` fallback): auto-wire writes to the **profile store** (`~/.crm/profiles`), a
different surface from the dotenv **credential** source. When the connection is built
from env/dotenv (the `CRM_DOTENV` path), there is no named profile, so auto-wire
correctly no-ops — the two mechanisms never fight. This is recorded so a future reader
does not "fix" auto-wire to also patch the dotenv file: dotenv is connection truth, not
a mutable preferences store.

## Consequences

- A named profile is mutated as a side effect of a create. This is surfaced in the
  envelope (`profile_updated`) and disabled with `--no-set-default`; re-running a
  create overwrites the same field with the same value (idempotent in practice).
- `profile_updated` / `profile_update` are implementation detail of these commands,
  not contract vocabulary — [CONTEXT.md](../../CONTEXT.md) is intentionally left
  untouched.
- Agents scripting the prerequisite get the zero-touch flow:
  `solution create-publisher … && solution create … && metadata create-entity …`
  with no repeated `--solution` / prefix flags, provided a named profile is active.
