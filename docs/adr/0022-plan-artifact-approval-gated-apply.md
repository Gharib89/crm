---
status: proposed
---

# Plan artifact: approval-gated `apply`

An agent-driven `apply` has an approval gap: the operator (or human behind an
agent) approves a `--dry-run` drift report, but nothing guarantees the later
real run does exactly what was approved — the spec or a referenced payload can
change on disk, and the org can drift between preview and apply. This ADR
introduces the **plan** — a saved, self-contained drift report that is the unit
of approval — and an execution mode that runs a plan **only if it is still
exactly true**.

## Decision

- **Surface: flags on `apply`, no new verb.** `crm --dry-run apply -f spec.yaml
  -o/--plan-out plan.json` serializes the existing dry-run drift report (#550)
  as a plan; `crm apply --from-plan plan.json` executes it (mutually exclusive
  with `-f`, exit 2 if both). `--dry-run --from-plan` re-verifies a plan
  without executing — the CI pre-check. A Terraform-style `crm plan` verb was
  rejected: it would be a second name for `--dry-run apply` with no new
  capability.
- **A plan is self-contained.** It embeds the resolved spec verbatim, pins every
  referenced file payload (web resource bodies, plug-in DLLs) by `sha256` —
  present-and-matching required at apply time — and records a header: target
  Web API URL and `organizationid` (WhoAmI), solution `unique_name`,
  plan-format version, CLI version, timestamp, and the **plan intent**
  (`prune`, `allow_data_loss`, `stage_only`). Per component it records kind,
  name, verdict (`planned` / `updated` + changed-field set / `replace_blocked`
  / `pruned` / `skipped`). Referencing the spec by path was rejected — content
  that can change after approval reopens the hole the plan closes; full base64
  inlining of payloads was rejected as review- and token-hostile.
- **`--from-plan` re-reconciles; it never blind-executes.** The convergent
  stance (ADR 0014) holds: execution recomputes the verdict set from live reads
  and compares it to the plan at the *action* level — component set, verdict,
  and changed-field set must match exactly (live field values need no byte
  equality). **Any divergence → a stale plan: zero writes, `ok=false` exit 1**,
  reported per component as "plan said X, live now computes Y"; the remedy is
  always re-plan and re-approve. Per-component soft execution (apply what still
  matches) was rejected: it lands the org in a state nobody approved. Plain
  `apply -f` keeps its ADR 0014 per-component behavior for operators who want
  it.
- **Only a clean plan is executable.** `-o` always writes the plan, even when
  the dry-run exits 1 — it doubles as the drift-report artifact for a PR or
  ticket. But `--from-plan` refuses a plan containing `replace_blocked` or
  `failed` entries (usage refusal): approving one would approve an outcome
  `apply` will never converge to.
- **Plan intent is fixed at plan time and replayed, never re-specified.**
  `--prune`/`--allow-data-loss`/`--stage-only` are passed when planning;
  `--from-plan` takes no such flags — re-specifying them at apply time would
  reintroduce "what runs ≠ what was approved". The destructive confirmation
  gate (`--yes` / TTY prompt) still applies at execution. Under prune intent
  the `pruned` entries are approved deletions and participate in the
  divergence gate; without it they are informational and a stray new solution
  component does not invalidate the plan — it changes no approved action.
- **Identity checks:** `organizationid` mismatch → refuse; URL or CLI-version
  mismatch alone → `meta.warnings` (aliased hostnames are legitimate; pinning
  CLI versions would let every release invalidate every pending plan);
  unknown/newer plan-format version → refuse.
- **No `crm diff` command.** Spec-vs-live drift is already
  `export-spec` → `--dry-run apply` (ADR 0019, #611), and the glossary bans
  "diff" in favour of **drift report** (CONTEXT.md). Whole-org diffing beyond
  the apply spec surface stays out of scope, as ADR 0019 fenced it.

## Why record this

The plan format is a contract — once plan files circulate through approval
workflows, its shape and its refuse-on-divergence semantics are hard to
reverse. The whole-run gate deliberately *diverges* from `apply`'s
per-component soft stance, which is surprising without the approval-integrity
context, and every piece above (self-containment, clean-plan rule, intent
fixing, verb-less surface) chose against a plausible alternative.

## Consequences

- "What was approved is what runs" becomes a checkable guarantee — the missing
  piece for unattended/agent-driven apply pipelines, and the plan file is a
  reviewable artifact (PR, ticket) for free.
- A residual TOCTOU window survives between the verification pass and the
  writes; metadata writes are not transactional, so this is documented honestly
  rather than engineered around. The gate shrinks the window from
  "preview-to-apply" to "verify-to-write".
- A stale plan is a loud, common failure on busy orgs: any concurrent
  customization invalidates pending plans. That is the intended trade — the
  fix is a cheap re-plan, not a weaker gate.
