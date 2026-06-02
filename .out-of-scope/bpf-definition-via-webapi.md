# Authoring a Business Process Flow *definition* via the Web API

This CLI does not support creating a business process flow (BPF) **definition** through the Dataverse Web API. There is no `crm bpf create` verb, and there won't be one, because the target platform does not offer a supported API path for it.

To be precise about scope: this rejection is only about **authoring the definition** — the `workflow` row plus its `clientdata` and the associated `processstage` rows. BPF *process instances* (records created against an already-defined, activated BPF) and BPF *activation* (a state change on the `workflow` row) remain in scope and are Web-API-supported; the CLI already references BPFs for listing and app-module assembly (`crm/core/appmodule.py` maps `bpf → workflow`, `crm/commands/workflow.py` filters category 4).

## Why this is out of scope

The target platform is **Dynamics 365 Customer Engagement on-premises 9.1**. Microsoft's own on-prem 9.1 developer documentation, *"Work with business process flows using code"*, documents only the **visual designer** as the way to author a BPF definition:

> Use the visual business process flow designer to define a business process flow.
> A business process flow definition is stored in the `workflow` entity, and the stage information for the business process flow is stored in the `processstage` entity.

— https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/model-business-process-flows?view=op-9-1

There is no documented "POST a BPF definition to `/workflows`" recipe. Two hard blockers make a reliable implementation impossible:

1. **`clientdata` has no hand-authorable schema.** Reverse-engineering an existing on-org BPF shows `clientdata` is a multi-kilobyte serialization of internal `Microsoft.Crm.Workflow.ObjectModel` classes (`WorkflowStep → EntityStep → StageStep → StepStep → ControlStep`) carrying internal IDs, `labelId`s, and `classId`s that must align with platform-generated `processstage` rows. The designer generates this; it is not a documented, stable, or supported input format.

2. **The prerequisite metadata flip is irreversible.** A table must have `IsBusinessProcessEnabled == true` to host a BPF, and Microsoft states:

   > Enabling an entity for business process flow is a one way process. You can't reverse it.

   Forcing this via the API to enable a brittle, unsupported authoring path is not a tradeoff this CLI will make.

A direct POST attempt with a minimal definition returns:

```json
{ "ok": false, "error": "An unexpected error occurred.",
  "meta": { "status": 500, "code": "0x80040216" } }
```

## Supported alternative

Author BPF definitions in the portal designer (**Settings → Processes → New → Business Process Flow**), which also performs the one-way `IsBusinessProcessEnabled` flip on the host table. This manual step is captured in the CRMWorx walkthrough §10. If Microsoft ever publishes a supported on-prem Web API recipe for definition authoring, this rejection can be revisited.

## Prior requests

- #37 — "BPF creation via Web API on D365 CE on-prem 9.1"
