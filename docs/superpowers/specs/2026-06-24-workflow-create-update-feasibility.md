# Creating & updating D365 processes ("workflows") safely — feasibility & design

**Date:** 2026-06-24
**Status:** Research / design doc (no implementation in this change)
**Scope:** All process categories (0–6) across both targets — on-prem CE v9.x (NTLM) and Dataverse online (OAuth).
**Goal:** Establish what Microsoft supports vs. doesn't for programmatic workflow create/update, and how we can build or reuse capability so an agent (Claude Code) can create/update workflows **safely**.

---

## 1. TL;DR

1. **"Workflow" is six different things.** The `workflow` table holds six `category` values: classic workflows (0), dialogs (1), business rules (2), custom process actions (3), business process flows (4), modern/cloud flows (5), desktop flows (6). Each has a *different* create/update story. Treating them as one feature is the first mistake.

2. **Hand-authored XAML create is blocked on BOTH targets** — not just cloud. We verified live: a raw `POST workflows` carrying foreign/hand-written XAML is rejected with `0x80045040 NonCrmUIWorkflowsNotSupported` ("created outside the Microsoft Dynamics 365 Web application") on **both** on-prem v9.1 and Dataverse online. The MS docs' "on-prem supports XAML-code create" refers to the SOAP SDK with genuine designer XAML, not arbitrary hand-authored XAML over the Web API.

3. **Create from *real designer XAML* works on BOTH targets.** Also verified live: `crm workflow clone` (which copies an existing workflow's real XAML via upsert) successfully created a new draft classic workflow on **both** on-prem **and** cloud. The `0x80045040` wall is **XAML-provenance-sensitive, not target-sensitive**: the platform accepts XAML it recognizes as designer-produced and rejects XAML it doesn't — on both targets.

4. **This falsifies our own test suite's assumption.** `crm/tests/e2e/coverage.py` skips `workflow clone`/`import`/(create) on the premise that cloud rejects them with the "created outside the Web application" error. Cloud accepted the clone. The `E2E_SKIP` entries are wrong/outdated and should be re-validated (→ file an issue).

5. **Cloud flows (cat 5) are the one place an agent genuinely *authors* automation.** Microsoft documents `POST workflows` with `clientdata` (a Logic Apps definition + connection references) as fully supported for cat-5 modern flows. This is online-only — **cat 5 does not exist on on-prem** (no flow runtime).

6. **Custom API is the clean, fully-supported "automation message" path on both targets** — a separate `customapi` table, no workflow-designer dependency, no XAML. If the real goal is "a callable custom operation," this beats custom process actions.

7. **Solution import is the sanctioned, portable create/update path for classic processes** (`ImportSolution` + `PublishWorkflows`). The repo already has the entire substrate (`solution export/import/extract/pack`, `retarget_xaml`).

**Recommendation (detail in §8):** Don't build a "write XAML from scratch" feature — the platform blocks it. Build three thin, safe capabilities on top of machinery we already have: (a) **classic workflow create-from-template/clone** (both targets, extends existing `clone`), (b) **cloud-flow create/update from a `clientdata` spec** (cloud-only, net-new, the real "agent authors a flow" path), (c) **solution round-trip edit** for portable classic-process changes. Gate all three behind the §7 safety checklist and target-aware routing.

---

## 2. The category map — what "workflow" actually means

| `category` | Name | Logic stored as | Code-create supported? | Lives on |
|---|---|---|---|---|
| 0 | Classic workflow (background/real-time) | `xaml` (WF XAML) | Designer XAML only (see §3/§4) | on-prem + cloud |
| 1 | Dialog | `xaml` | No (deprecated, removed Dec 2020) | on-prem only (vestigial) |
| 2 | Business rule | `xaml` | Designer XAML only | on-prem + cloud |
| 3 | Custom process action | `xaml` | Designer XAML only (use **Custom API** instead) | on-prem + cloud |
| 4 | Business process flow (BPF) | `clientdata` (JSON) | Definition: designer-only. Instances: full CRUD | on-prem + cloud |
| 5 | Modern / cloud flow (Power Automate) | `clientdata` (Logic Apps JSON) | **Yes — fully supported** | **cloud only** |
| 6 | Desktop flow | binary modules | No (list + trigger only) | cloud only |

`type` column: `1` = Definition (editable draft), `2` = Activation (platform-generated read-only running copy), `3` = Template. `statecode`: `0` = Draft/Off, `1` = Activated/On, `2` = Suspended.

---

## 3. What Microsoft documents (supported vs. not)

Authoritative quote ([workflow-operations]):
> "In Dataverse, workflows must be created and updated using the Workflow designer. With Dynamics 365 Customer Engagement on-premises you can create Workflows using the XAML definitions with code. **This is not supported with Dataverse.**"

Enforced by two error codes ([web-service-error-codes]):
- `0x80045040 NonCrmUIWorkflowsNotSupported` — cat 0/2/3 records the platform deems "created outside the Web application."
- `0x80045044 NonCrmUIInteractiveWorkflowNotSupported` — cat 1 dialogs.

**Per-operation support (from the docs):**

| Operation | On-prem v9.x | Dataverse online |
|---|---|---|
| Create cat-0/2/3 with **hand-authored** XAML via code | Documented as supported **via SOAP SDK** with genuine XAML | Not supported |
| Update `xaml`/`clientdata` of a classic process via code | Supported (SDK) | Not supported |
| Activate / deactivate (`statecode`/`statuscode` PATCH, `SetStateRequest`) | Supported | Supported |
| `ExecuteWorkflow` (run on-demand) | Supported | Supported |
| Delete workflow record | Supported | Supported |
| `CreateWorkflowFromTemplate` | Supported | Supported |
| Create cat-5 cloud flow (`clientdata`) | N/A (no flow runtime) | **Supported** |
| Create Custom API (`customapi` table) | Supported | Supported |
| BPF definition create/update | Designer-only | Designer-only |
| BPF *instance* CRUD (auto-generated entity) | Supported | Supported |

**Schema vs. platform-block nuance:** the `workflow.xaml` column is `IsValidForCreate=true` at the OData metadata layer, so a POST is *structurally* accepted and routed — the rejection happens in the Create/Update **business-logic** handler (hence a named business error, not an OData 400). This is why "the schema says writable" and "the platform rejects it" are both true.

---

## 4. Live empirical findings (verified on both real orgs, 2026-06-24)

We tested against `agent-on-prem` (CE v9.1, NTLM) and `agent-cloud` (Dataverse online). Every probe record was created as a draft and **deleted afterward** (both orgs verified clean).

| Create path | on-prem v9.1 | cloud (Dataverse online) |
|---|---|---|
| Raw `entity create workflows` with **foreign/fake XAML** | ❌ `0x80045040` | ❌ `0x80045040` |
| `workflow clone` of a **system** workflow (real XAML) | ⚠️ `0x80040216` (500 — got *past* the gate, failed on system-component duplication) | not retested |
| `workflow clone` of a **custom** workflow (real XAML) | ✅ created draft, deleted clean | ✅ **created draft, persisted, deleted clean** |

**Conclusions:**
1. The `0x80045040` wall is **XAML-provenance-sensitive, identical on both targets** — not a cloud-only block. Genuine designer XAML (what clone/import reuse) passes; arbitrary/hand XAML is rejected.
2. **`crm workflow clone`/`import` are viable on cloud, not just on-prem.** This contradicts `crm/tests/e2e/coverage.py` (the `E2E_SKIP` reason claims cloud rejects them). **Action:** re-validate and likely un-skip those e2e tests; file an issue. *(Not isolated: whether a bare `POST` with *real* XAML also succeeds — vs. only the upsert/clone channel — was not separated, because every realistic create path we'd ship reuses real designer XAML anyway, so this doesn't change the design. Worth a one-line follow-up probe.)*
3. The system-workflow clone 500 (`0x80040216`) is a separate, known clone limitation (internal-GUID collisions when duplicating system-managed XAML), **not** the create gate.

---

## 5. The feasible create/update paths

There are exactly three mechanisms that actually work. None of them is "write XAML from scratch."

### Path A — Classic workflow create-from-template / clone (cat 0, 2; both targets)
Start from genuine designer XAML — an existing workflow, an exported definition, or a template (`type=3`) — and create a new draft via upsert, optionally retargeting the primary entity. **This is proven live on both targets and already implemented** as `crm workflow clone` / `import` (`crm/core/workflow.py:134`, `:239`; `retarget_xaml` at `:68`). Gap: no first-class `create`/`create-from-template` verb, and no `update` (deactivate→edit→reactivate) verb.

### Path B — Cloud flow create/update from a `clientdata` spec (cat 5; cloud only) — *the real "agent authors automation" path*
`POST workflows` with `category=5, type=1, name, primaryentity="none", clientdata=<string-encoded JSON>`. `clientdata` = `{ properties: { connectionReferences, definition } }` where `definition` is the Logic Apps Workflow Definition Language (triggers + actions). Created as `statecode=0` (Off); enable is a separate PATCH. **This is genuinely code-authorable** — an agent can compose the JSON definition. Net-new in the repo. Hard part is connection-reference binding (§7). On-prem has no cat-5 runtime, so this is cloud-only.

Minimal `clientdata` skeleton:
```jsonc
{
  "properties": {
    "connectionReferences": {
      "shared_commondataserviceforapps": {
        "runtimeSource": "embedded",
        "connection": { "connectionReferenceLogicalName": "<prefix>_dataverseref" },
        "api": { "name": "shared_commondataserviceforapps" }
      }
    },
    "definition": {
      "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
      "contentVersion": "1.0.0.0",
      "triggers": { "manual": { "type": "Request", "kind": "Button", "inputs": {} } },
      "actions": { "List_rows": { "type": "OpenApiConnection", "inputs": { /* connector op */ } } }
    }
  },
  "schemaVersion": "1.0.0.0"
}
```

### Path C — Solution round-trip (classic processes; both targets; portable & sanctioned)
`solution export → extract → edit Workflows/<name>-<guid>.xaml (or .json) → pack → import (PublishWorkflows=true)`. This is Microsoft's documented engineering pattern and the only sanctioned way to *change classic logic* (vs. clone, which copies). **Entire substrate already exists** (`solution_transfer.export_solution`/`import_solution`, `solutionpackager.extract_solution`/`pack_solution`). Editing raw XAML by hand remains fragile (undocumented format) — best used to *transport* designer-built changes, not to author logic blindly.

---

## 6. Reuse vs. build

| Tool | Covers | Gap for our headless/agent CLI |
|---|---|---|
| **`crm` (this repo)** | Solution lifecycle; workflow activate/deactivate/run/clone/export/import/delete | No `workflow create`/`update`; no cloud-flow `clientdata` authoring; no `custom-api` create |
| Power Platform CLI (`pac`) | Full solution ALM, pack/unpack | External binary; separate auth; no JSON mode; subprocess-only |
| XrmToolBox | GUI workflow CRUD/migrate | GUI-only, not scriptable |
| Power Automate mgmt connectors | List/enable/disable/run flows | Must run *inside* a flow; no create/XAML; not headless |
| `api.flow.microsoft.com` | Unofficial flow REST | Explicitly unsupported; breaking changes |
| Dataverse Web API (direct) | Cat-5 create/update; activate all types | Cannot create classic XAML from scratch (provenance wall) |

**Verdict:** we already own the substrate. The build is *thin glue + safety*, not new infrastructure. Reuse `clone`/`retarget_xaml`/solution machinery; add (1) a classic `create-from-template`/`update` verb, (2) a cloud-flow `clientdata` create/update verb, (3) optionally a `custom-api create` verb.

---

## 7. Safety model — guardrails a safe create/update must implement

Tagged `[both]` / `[cloud]` / `[on-prem]`. Most map directly onto patterns already in `crm/core/workflow.py`.

1. **[both]** Create-as-draft; activation is always a separate explicit step (mirror `import_workflow` → `set_workflow_state` split).
2. **[both]** Refuse to mutate a `type=2` activation record — auto-resolve to the `type=1` parent (reuse `_resolve_parent_workflow_id`, `0x80045003` handling at `workflow.py:413-422`); clean-error if no live parent.
3. **[both]** Reject in-place edits to an *activated* Definition — require deactivate → edit → reactivate; surface that path, don't leak the server error.
4. **[both]** Validate-before-backend: required fields (`category/name/type/primaryentity`, +`clientdata` for cloud), GUID normalize, force `type=Definition` on create. (No `validate_workflow` helper exists today — net-new.)
5. **[cloud]** Parse `clientdata` as well-formed JSON locally before POST/PATCH (fail as `D365Error`, not a server 400).
6. **[cloud]** Before enabling, verify every `connectionReference` is bound to a usable connection — never flip `statecode=1` with unresolved/forbidden connections (guards `ConnectionAuthorizationFailed` / Suspended).
7. **[cloud]** `primaryentity="none"` for automated/instant/scheduled flows; for classic, verify `primaryentity` exists on the **target** org.
8. **[both]** Validate `triggeronupdateattributelist` against real attributes of `primaryentity` (server only checks at activation).
9. **[both]** Route by category & target: refuse `category=5` on on-prem; refuse hand-XAML create on both (provenance wall) — accept only template/clone/solution-sourced XAML.
10. **[on-prem]** Real-time (sync) classic workflows: block wait/delay activities, require activation privilege, match stage to trigger.
11. **[both]** Warn (or block on a flag) when an incoming classic workflow carries a cloud-migration blocker (`real_time`/`wait_condition`/`custom_activity`) — reuse `assess_workflow_migration` (`workflow.py:307-332`).
12. **[both]** Emit the standard dry-run envelope (`{_dry_run, would_create|would_update|would_activate, …}`); GET checks run live, only writes short-circuit.
13. **[both]** Wrap rejections as `D365Error` (preserve `status/code/response_body`); report non-atomic outcomes truthfully (record created but activation failed = no rollback).
14. **[both]** After activation, re-read `statecode` (cloud flows can land Suspended via DLP even when the activate call returns 2xx).

---

## 8. Recommendation & proposed shape

**Do not** build "author classic workflow XAML from scratch" — the platform blocks it on both targets, and hand-editing XAML is fragile and unsupported.

**Build, in priority order:**

- **Tier 1 — `crm workflow create` (from template/clone) + `crm workflow update` (cat 0/2, both targets).** Thin extension of existing `clone`: `--from-template <id>` / `--from-file <exported.json>`, retarget entity, create as draft, explicit `--activate`. `update` = deactivate→patch→reactivate with the type-2 guard. Reuses proven machinery; works on both targets. Highest value-to-effort.
- **Tier 2 — `crm flow create` / `crm flow update` (cat 5, cloud).** Accept a `clientdata` JSON (or a higher-level trigger+actions spec we compile to it). This is where an agent genuinely *authors* automation. Requires the connection-reference binding workflow (§7.6) — the main new complexity. Cloud-only; refuse on-prem with a clear message.
- **Tier 3 — `crm custom-api create` (both targets).** If the underlying intent is "a callable custom operation/message," Custom API is the clean, fully-supported, XAML-free path. Separate `customapi` table; no workflow designer.
- **Cross-cutting — solution round-trip helper.** A `crm workflow edit-via-solution` convenience that wraps export→extract→(edit)→pack→import→publish for portable classic-process changes.

Every tier ships behind the §7 guardrails, target-aware routing, dry-run previews, and the standard error envelope. "Enable Claude to create workflows" is realized mainly through **Tier 1 (templates/clone) + Tier 2 (cloud-flow authoring)**.

---

## 9. Open questions / validate next

1. **Re-validate the `E2E_SKIP` for `workflow clone`/`import`/create** (coverage.py) — cloud accepted the clone; the skip premise is false. Likely un-skip and add live coverage. *(File an issue.)*
2. **Isolate POST-vs-upsert with real XAML** — does a bare `POST workflows` with genuine designer XAML succeed on cloud, or only the upsert/clone channel? One probe. Immaterial to the recommended design (we reuse clone/import machinery either way) but tidies the model.
3. **Connection-reference binding for code-created cloud flows** — design the exact flow for binding/sharing connections so a created flow can be enabled headlessly (service-principal scenario).
4. **System-workflow clone 500 (`0x80040216`)** — confirm it's the internal-GUID-collision class and whether `retarget_xaml` should regenerate embedded GUIDs.

---

## Appendix — citations

- Work with cloud flows using code — https://learn.microsoft.com/power-automate/manage-flows-with-code
- Sample: Workflow operations (the "not supported with Dataverse" note) — https://learn.microsoft.com/power-apps/developer/data-platform/org-service/samples/workflow-operations
- Process (Workflow) table reference — https://learn.microsoft.com/power-apps/developer/data-platform/reference/entities/workflow
- Web service error codes (`0x80045040`/`0x80045044`) — https://learn.microsoft.com/power-apps/developer/data-platform/reference/web-service-error-codes
- Create real-time workflows (on-prem XAML-code create) — https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/create-real-time-workflows?view=op-9-1
- Process categories (on-prem XAML support) — https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/process-categories?view=op-9-1
- Create your own messages / Custom API — https://learn.microsoft.com/power-apps/developer/data-platform/custom-actions ; https://learn.microsoft.com/power-apps/developer/data-platform/custom-api
- Use Custom Process Actions with code — https://learn.microsoft.com/power-apps/developer/data-platform/workflow-custom-actions
- Work with business process flows using code — https://learn.microsoft.com/power-automate/developer/business-process-flows-code
- ImportSolution action / `PublishWorkflows` — https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/importsolution
- Connection references — https://learn.microsoft.com/power-apps/maker/data-platform/create-connection-reference
- Logic Apps Workflow Definition Language — https://learn.microsoft.com/azure/logic-apps/logic-apps-workflow-definition-language
- pac solution reference — https://learn.microsoft.com/power-platform/developer/cli/reference/solution
- Power Automate FAQ (cloud-only) — https://learn.microsoft.com/power-automate/frequently-asked-questions

*Repo references throughout cite `crm/core/workflow.py`, `crm/core/solution_transfer.py`, `crm/core/solutionpackager.py`, and `crm/tests/e2e/coverage.py` as of 2026-06-24.*
