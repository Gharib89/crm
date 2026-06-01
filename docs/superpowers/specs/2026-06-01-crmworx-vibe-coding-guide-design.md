# CRMWorx — Vibe-Coding CRM Customizations Guide (Design)

**Date:** 2026-06-01
**Status:** Approved (design)
**Author:** Ahmed Gharib (with Claude Code)

## Summary

Produce an end-to-end, reproducible guide for building Dynamics 365 CE on-prem
customizations with the `crm` CLI and Claude Code, using a single worked
scenario — **CRMWorx**, an IT-company ticketing platform with SLA. One guided
live session yields three things:

1. A **MkDocs Material** documentation site (getting-started, the CRMWorx
   walkthrough, per-group how-tos, an auto-generated CLI reference).
2. The **live CRMWorx customization** deployed to the server, exported as a
   committed solution artifact, and built to replay safely.
3. **Tooling improvements** — defects found mid-run are fixed inline (with a
   test) or filed as triaged issues, and the agent skill is resynced to the CLI.

The customization itself is the vehicle; the guide is the deliverable.

## Goals

- Demonstrate every `crm` command group against a real server, end to end.
- Capture real command output into narrative docs (no invented transcripts).
- Make the run idempotent (`--if-exists skip`) and fully tearable so it replays
  from a clean server.
- Tighten the CLI and the agent skill from what the run exposes.
- Ship a hostable docs site.

## Non-Goals

- OAuth / IFD / certificate auth (NTLM only, per existing scope).
- Plugin / workflow source-code deployment.
- D365 online (Dataverse cloud).
- A generic "how to use D365" tutorial — this is CLI- and agent-centric.

## Scenario: CRMWorx data model

Publisher prefix `cwx`, dedicated solution `CRMWorx`.

### Entities

**`cwx_sla` — SLA Policy**

| Logical | Kind | Notes |
| --- | --- | --- |
| `cwx_name` | primary string | policy name |
| `cwx_responsehours` | integer | response target |
| `cwx_resolutionhours` | integer | resolution target |
| `cwx_tier` | picklist → `cwx_slatier` | Bronze/Silver/Gold |
| `cwx_active` | boolean | policy enabled |

**`cwx_ticket` — Support Ticket**

| Logical | Kind | Notes |
| --- | --- | --- |
| `cwx_name` | primary string | ticket title/number |
| `cwx_description` | memo | free text |
| `cwx_priority` | picklist → `cwx_priority` | Low/Normal/High/Critical |
| `cwx_severity` | picklist → `cwx_severity` | Minor/Major/Critical |
| `cwx_category` | picklist → `cwx_ticketcategory` | Hardware/Software/Network/Access |
| `cwx_openedon` | datetime | created time |
| `cwx_resolvedon` | datetime | resolution time |
| `cwx_dueby` | datetime | SLA-derived deadline |
| `cwx_customerid` | lookup → account | OOB account |
| `cwx_sla` | lookup → `cwx_sla` | applied policy |

### Global option sets

`cwx_priority`, `cwx_severity`, `cwx_ticketcategory`, `cwx_slatier`.

### Relationships

- `cwx_sla` 1:N `cwx_ticket` (a ticket references one SLA).
- account 1:N `cwx_ticket` (customer; lookup to OOB `account`).
- `cwx_ticket` N:N `systemuser` ("watchers") — exercises `create-many-to-many`.

This forces coverage of string/memo/integer/datetime/boolean/picklist/lookup
attribute kinds, both relationship types, option-set CRUD, entity CRUD, and the
read paths below.

## Approach

Chosen: **scaffold docs first, live run fills it** (vs. run-first-docs-after, or
interleaved-per-group). Build the MkDocs skeleton + CLI reference offline for a
fast win and a locked deliverable shape, then drive the live run and transcribe
real output into the prepared slots, fixing/logging bugs as they hit, finishing
the how-tos last.

## Live run phases

Each step is a real command whose captured output is transcribed into
`guides/crmworx-walkthrough.md`. Destructive verbs are surfaced for explicit
`--yes` confirmation.

1. **Pre-flight** — `connection whoami` and reachability; create publisher +
   `CRMWorx` solution; set profile default solution + prefix.
2. **Metadata build** — option sets → entities → attributes → relationships,
   each with `--if-exists skip`, each added to the `CRMWorx` solution.
3. **Seed data** — create SLA policies, a couple of accounts, and tickets;
   demonstrate `upsert` and `update`.
4. **Read / verify** — `query odata`, `query fetchxml`, a count, `data export`
   to CSV, and one `action` call.
5. **Package** — `solution export CRMWorx -o docs/artifacts/crmworx.zip`; commit.
6. **Teardown appendix** — gated deletes for a full reset so the guide replays
   from zero.

## Bug loop (hybrid, runs across all phases)

- **Trivial / single-function defect:** write a failing test in `crm/tests/`,
  fix in `crm/`, re-run, continue — the fix cycle appears in the guide.
- **Larger defect:** file a triaged GitHub issue (existing tracker + triage
  skills) with a minimal repro, apply a workaround, continue.
- A capability-coverage table at the end of the walkthrough maps each command
  group → the step that proved it, so gaps are visible.

## Skill + CLI sync

Source of truth: `crm/skills/SKILL.md`. After fixes: update it, run
`crm skill install --target claude --force`, and reconcile the existing drift
(the active `.claude/skills/crm/SKILL.md` is missing the destructive-ops
section). Documented on a `contributing/skill-and-cli.md` page.

## Documentation: MkDocs Material site

```
mkdocs.yml                      # material theme, mkdocs-click plugin
docs/
  index.md
  getting-started/
    install.md                  # thin; links to README to avoid drift
    configure.md                # thin; links to README
  guides/
    crmworx-walkthrough.md      # hero doc, transcribed from the live run
  how-to/
    connection.md
    entity.md
    query.md
    metadata.md
    solution.md
    data.md
    action.md
  reference/
    cli.md                      # mkdocs-click auto-generated from crm.cli
  contributing/
    skill-and-cli.md
  artifacts/
    crmworx.zip                 # committed solution export
```

- New dev dependencies (extras group): `mkdocs`, `mkdocs-material`,
  `mkdocs-click`.
- Getting-started pages stay thin and link to `README.md` / `D365.md` rather
  than duplicating install/configure content (avoids drift; surgical).
- **GH Pages workflow** `.github/workflows/docs.yml` building on push to `main`
  is **included** (cuttable later if undesired).

## Error handling / safety

- Destructive verbs (`delete-entity`, `delete-optionset`, `entity delete`,
  etc.) require `--yes`; the `destructive_op_gate.py` PreToolUse hook hard-blocks
  them otherwise. The agent surfaces intent before each.
- The run is idempotent via `--if-exists skip`; re-running must be a no-op.
- Teardown is verified exactly once against the server to confirm a clean reset.

## Success criteria

- `mkdocs build --strict` completes with no warnings.
- Server has the `CRMWorx` solution: 2 custom entities, 4 global option sets,
  ≥1 one-to-many + ≥1 many-to-many relationship, and seeded tickets; the
  exported zip is committed under `docs/artifacts/`.
- Every command in the walkthrough matches real captured output.
- Re-running the walkthrough with `--if-exists skip` is a verified no-op.
- The teardown appendix returns the server to a clean state (verified once).
- `crm/skills/SKILL.md` is regenerated, drift with the active copy reconciled,
  and the coverage table shows all 8 command groups exercised.
- Each surfaced defect is either fixed with a test or filed as a triaged issue.

## Phasing (for the implementation plan)

1. **Plan 1 — Docs scaffold (offline):** MkDocs structure, mkdocs-click CLI
   reference, getting-started pages, `docs.yml` workflow. No server needed.
2. **Plan 2 — Live CRMWorx run:** drive phases 1–6, transcribe the walkthrough,
   commit the artifact, apply inline fixes / file issues.
3. **Plan 3 — How-tos + sync:** per-group how-to pages (derived from the run),
   `contributing/skill-and-cli.md`, skill resync + drift reconcile, coverage
   table.

## Open items resolved

- GH Pages `docs.yml` workflow: **included** (cuttable).
- N:N watchers (`cwx_ticket` ↔ `systemuser`): **kept** for broader coverage.
