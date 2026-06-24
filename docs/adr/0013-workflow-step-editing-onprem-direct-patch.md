# Workflow step-editing is on-prem-only via direct xaml PATCH

`workflow update` splits along the **provenance wall** (see `CONTEXT.md`). Editing
a classic process's **metadata** (name, scope, triggers, on-demand) is not
provenance-gated and works on **both targets**. Editing its **logic** — the step
XAML — is provenance-gated, so it is **on-prem only**, performed by a direct PATCH
of the `xaml` field (`deactivate-if-active → PATCH → reactivate`), and **refuses on
cloud** with the provenance-wall explanation. No solution-round-trip machinery is
built. The verb is an opaque-body primitive that validates references and drives
the activation lifecycle; composing the step XAML lives in the crm skill.

## Why

A live spike on both orgs (2026-06-24, extending the feasibility doc
`docs/superpowers/specs/2026-06-24-workflow-create-update-feasibility.md`) settled
two things the design hinged on:

- **Hand-edited step XAML is feasible on on-prem and impossible on cloud.** On
  `agent-on-prem` (v9.1) a draft workflow's `xaml` was edited to add steps —
  proven both via direct PATCH (4 steps, activated, ran) and via solution import.
  On `agent-cloud` (Dataverse) the **same edit was rejected on both channels** with
  `0x80045040` ("created outside the Microsoft Dynamics 365 Web application") — the
  direct PATCH *and* the sanctioned solution-import path alike. The `Provenance
  wall` is provenance-sensitive, not target-sensitive: unmodified designer XAML
  (clone/import) passes; modified XAML is rejected. On-prem has a deployment-admin
  privilege escape (`0x80045041`); cloud has none.
- **The simpler mechanism suffices.** Direct PATCH of the `xaml` field adds steps
  with no zip/extract/pack/`pac` apparatus. The solution round-trip the first spike
  used was unnecessary on-prem and is blocked on cloud, so it earns no place.

On-prem is the priority: on-prem workflow automation is heavy and time-consuming
real work, so a CLI path that saves authoring time there is worth building even
though the cloud/CI target can never use it.

## How it works

`workflow update <id> --xaml-file <whole.xaml>` (logic path, on-prem only):

1. **Target gate** — refuse on a cloud/Dataverse profile up front with the
   provenance-wall reason; never leak the raw server fault.
2. **Resolve** a `type=2` activation record to its `type=1` parent (reuse
   `_resolve_parent_workflow_id`).
3. **Reference-validate the supplied blob** (deterministic, in the verb, pre-PATCH):
   well-formed (ElementTree, not minidom) → all used namespace prefixes declared →
   activities ∈ a known allowlist → referenced `EntityName`/`Attribute` exist (live
   metadata GET) → required-args present. Advisory `meta.warnings` by default;
   `--strict` promotes to an operational failure.
4. **Capture prior xaml**, then `deactivate-if-active → PATCH xaml → reactivate`.
5. **Server is the semantic authority** — the reactivate step compiles the XAML and
   is the only check for type resolution / privilege.
6. **Rollback by default** on reactivate failure: restore the prior xaml and report
   both errors; `--no-rollback` leaves the broken definition for inspection.
7. Error gates `0x80045040`/`0x80045041` surface as clean `D365Error` (preserve
   `status/code/response_body`); non-atomic outcomes are reported truthfully.

The **interface is whole-xaml replace**, so validation has one home: the verb
checks whatever it is handed, regardless of caller. The crm skill
(`crm/skills/reference/workflow-xaml.md`, new, routed from `SKILL.md`) owns
composition — a harvested snippet library (Update static, Update dynamic-from-field,
Create, Change Status), the `xmlns:s` injection gotcha, and the direct-PATCH
routine — never restating flags.

## Considered options

- **Solution round-trip machinery** (`export → extract → edit → pack → import`).
  Rejected: blocked on cloud (same `0x80045040`, proven), and unnecessary on-prem
  (direct PATCH is simpler and needs no `pac`/zip toolchain).
- **Embed `ActivityXamlServices`** (.NET) to parse/validate/author XAML. Rejected:
  .NET-only (cross-platform WF is the community CoreWF port), needs version-pinned
  CRM server assemblies, does **not** unlock create (provenance wall is orthogonal),
  and cannot replace the server activate-gate (privilege/DLP are server-only) — a
  heavy dependency for marginal gain in a lean Python CLI.
- **Full typed step-builder** (`--type update --attr … --value …` for all step
  types). Rejected: the step *catalog* and activity *classes* are documented but the
  XAML *serialization* is not, so every step type must be reverse-engineered from
  harvested designer output — reimplementing the designer serializer, version-fragile
  and unbounded. Authoring stays in the skill as snippets.
- **Fragment-splice interface** (verb takes a step fragment and splices it).
  Rejected: forces the verb to understand splice points and namespace injection —
  grammar-awareness creep, and two things to validate instead of one. Whole-xaml
  keeps the verb opaque and validation single-homed.
- **Skill-only, no verb.** Rejected given the on-prem priority and value: a validated
  verb is the deterministic trust boundary any caller hits; the skill carries the
  gotchas and snippets.

## Consequences

- Step/logic editing is **on-prem only**; cloud refuses with the provenance-wall
  message. Even on-prem it is **org-config-gated** — a deployment that does not
  permit non-UI XAML returns `0x80045041`, surfaced cleanly.
- The feasibility doc is corrected: cloud blocks hand-edited XAML on **update too,
  via both channels** (its §4/§5 described only *create* and called the solution
  path "both targets, sanctioned" — true only for *transporting unmodified* XAML).
- New work: the `workflow update` metadata path (both targets), the on-prem xaml
  path + reference-validator, and `reference/workflow-xaml.md` + one `SKILL.md`
  router row in the crm skill.
- E2E: the on-prem leg covers the xaml path; the cloud leg asserts the **refusal**.
- This carves the in-scope sliver out of #37 ("process XAML authoring", out of
  scope): editing an **existing provenanced** workflow on a permitted on-prem org is
  in scope; authoring a workflow's XAML from scratch remains blocked everywhere.
