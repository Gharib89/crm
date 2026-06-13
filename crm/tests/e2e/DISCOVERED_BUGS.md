# Product bugs surfaced by the live e2e suite

Four real defects were surfaced while building the live e2e coverage
(`crm/tests/e2e/`). None are test bugs — each reproduces through the normal CLI
against a live org. #1, #2, and #3
turned out to be **client-side** bugs in the CLI and are now fixed; **#4 is now FIXED
(#269)** — its `xfail` is removed and the test passes live on both targets (see below).
All four are now fixed.

Repro uses a saved profile directly (`--profile`); no e2e harness needed. Targets
referenced: an on-prem NTLM org and a cloud OAuth/Dataverse org.

| # | Bug | Tracking issue |
|---|-----|----------------|
| 1 | `metadata list-actions` / `list-functions` → HTTP 415 → ✅ fixed (client-side) | Gharib89/crm#266 (FIXED) |
| 2 | `metadata update-relationship` → HTTP 405 → ✅ fixed (client-side) | Gharib89/crm#267 (FIXED) |
| 3 | `form clone` reused internal form ids on on-prem → ✅ fixed (client-side) | Gharib89/crm#268 |
| 4 | `ribbon add-button` / `remove` blocked by validation | Gharib89/crm#269 (FIXED) |

---

## 1. `metadata list-actions` / `metadata list-functions` → HTTP 415 — ✅ FIXED (client-side, #266)

**This was a client-side bug in the CLI, not a product defect** — the command, not the
server, requested the wrong content type. Now fixed; the entry is kept as a record.

**Symptom**
```
$ crm --profile <org> --json metadata list-functions
{"ok": false,
 "error": "A supported MIME type could not be found that matches the acceptable MIME types for the request.",
 "meta": {"status": 415, "code": "0x80060888", "category": "validation"}}
```
Same 415 on cloud (v9.2). Affected both `list-actions` and `list-functions`.

**Root cause** — both verbs read the OData `$metadata` CSDL document to enumerate
actions/functions. That endpoint is served only as **XML (CSDL)**, but `_fetch_csdl`
issued the GET with the backend default `Accept: application/json`, so Dataverse
rejected it with 415 on every target.

**Fix (shipped)** — `crm.core.metadata._fetch_csdl` now passes
`extra_headers={"Accept": "application/xml"}` on the `$metadata` GET only; all other
Web API traffic stays JSON. The CSDL parse side already returned XML bodies as text.

**Test** — `crm/tests/e2e/test_metadata_read.py::test_metadata_list_actions` /
`::test_metadata_list_functions` (`xfail` removed; assert `ok: true` live on both
targets). Offline header coverage:
`test_metadata_actions_functions.py::TestAcceptHeader`.

---

## 2. `metadata update-relationship` → HTTP 405 — ✅ FIXED (client-side, #267)

**This was a client-side bug in the CLI, not a product defect** — the command
targeted the wrong URL and Dataverse correctly rejected the unsupported operation.

**Symptom**
```
HTTP 405 "does not support operation"
on-prem v9.1 code 0x0 ; cloud v9.2 code 0x80060888
```

**Root cause** — the command did its retrieve-merge-write entirely through the typed
**cast** segment `RelationshipDefinitions(<id>)/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata`,
PUTting the merged definition back to that cast path. Dataverse rejects a PUT to a
relationship cast segment with 405 on both targets.

**Fix (shipped)** — `update_relationship` now reads its merge base from the cast path
(the only projection that carries `CascadeConfiguration`/`AssociatedMenuConfiguration`)
but PUTs the full merged definition to the **un-cast** `RelationshipDefinitions(<id>)`
entity-set path, with the `@odata.type` discriminator in the body (injected if a
minimal-metadata GET omitted it). PUT-replace only — Dataverse rejects PATCH for
metadata. `MSCRM.MergeLabels`/`MSCRM.SolutionUniqueName` headers and the dry-run
preview are preserved (the dry-run path now reflects the un-cast PUT target). The
attribute (column) update path is **unchanged** — its cast-path PUT is required and
correct.

**Test** — `crm/tests/e2e/test_metadata_write.py::test_update_relationship_cascade`
(`xfail` removed; changes one cascade key, then reads the relationship back via
`export-spec` to assert the change applied and an untouched cascade key survived).
Offline coverage: `test_metadata_update.py::TestUpdateRelationship` +
`::TestDryRun::test_dry_run_relationship_resolves_id_gets_no_put`.

**Repro (pre-fix)** — create a 1:N (e.g. via `metadata create-one-to-many`), then:
```
crm metadata update-relationship <relationship_schema> --cascade-assign Cascade
# → HTTP 405
```

---

## 3. `form clone` reused internal form ids on on-prem — ✅ FIXED (client-side, #268)

**This was a client-side bug in the CLI, not a product defect** — it is now fixed on
the `form clone` path; the entry is kept as a record of the corrected diagnosis.

**Symptom** — cloning a form into a custom entity on on-prem v9.x failed with
`0x8004f658`, e.g. *"The label '…', id '…' already exists. Supply unique labelid
values."* Repeat clones of the same source were guaranteed to collide. Cloud v9.2 was
unaffected.

**Root cause** — the original brief blamed an embedded root `<form id>` PK, but the
real form's `<form>` root carries **no** id (injecting one fails on-prem with schema
error `0x80048425`). The actual collision is on the form's **internal registration
GUIDs** — `labelid`, layout element `id`, `uniqueid`, `handlerUniqueId`,
`libraryUniqueId` — which on-prem enforces as org-unique. Cloning reused the source's
values verbatim; Dataverse online silently reassigns them, hiding the problem.

**Fix (shipped)** — `crm.core.forms.regenerate_form_clone_ids` regenerates each
internal-registration GUID per clone (consistent old→new mapping) while preserving
external references (`classid` control types, `<Role Id>` security roles,
`<ViewId>`/`<QuickFormId>` lookups); a guard refuses to POST if any non-target GUID
changed. Verified live: the account form clones twice on **both** on-prem and cloud
with distinct formids.

**Test** — `crm/tests/e2e/test_form.py::test_form_clone_account_to_ephemeral`
(now runs on both targets — no `@requires_cloud` gate — and clones twice to assert
the repeat-collision is gone; CI runs the cloud leg, the maintainer the on-prem leg).

---

## 4. `ribbon add-button` / `ribbon remove` blocked by new-install validation (both targets)

**Symptom** — every ribbon write fails during pre-import validation on both on-prem
v9.1 and cloud v9.2 (no button is ever applied).

**Root cause** — `apply_ribbon_change` calls `validate_solution(backend=...)`, which runs
`_check_org_collisions`. That collision check is designed for **new-solution installs**:
it flags any `systemform` id in the exported solution as a collision because it already
exists on the org. But a ribbon edit is a **round-trip update** (export the entity's
solution → mutate `RibbonDiffXml` → re-import); the entity's existing form ids are
expected state, not new components. The check produces false-positive collisions and
aborts the import.

**Fix (FIXED, #269)** — two root causes, fixed together (the second was masked
behind the first until it was lifted):
1. **Collision false-positive.** `validate_solution` gained `check_collisions: bool
   = True`; `apply_ribbon_change` calls it with `check_collisions=False`, skipping
   only `_check_org_collisions` + `_check_xaml_stage_collisions` (existing form GUIDs
   are expected state on a round-trip update) while keeping `_check_webresource_refs`
   + `_check_optionset_bindings` (and `backend`). Default `True` leaves fresh-install
   validation unchanged.
2. **importjobs progress-read race (cloud-async only).** With collisions skipped the
   import proceeded but `poll_async_operation`'s *progress* read of `importjobs(<id>)`
   could 404 (`0x80040217`) before Dataverse committed the row, aborting a healthy
   import. The progress read is cosmetic — the asyncoperation statecode is
   authoritative — so a transient 404 there is now tolerated (tick skipped); other
   errors still propagate.

**Test** — `crm/tests/e2e/test_ribbon.py::test_ribbon_add_and_remove_button`
(`xfail` removed; passes live on on-prem v9.1 and cloud v9.2). Offline coverage:
`test_solution_validate.py::TestCheckCollisionsFlag` and
`test_resilience.py::TestPollAsyncOperation::test_progress_read_tolerates_transient_missing_importjob`.

**Repro (pre-fix)**
```
crm --profile crmworx ribbon add-button <custom_entity> --label "E2E" --command-js "alert('x')"
# → aborted in validate_solution / _check_org_collisions
```

---

### Severity / notes
- #1 made `list-actions`/`list-functions` non-functional on every target — **FIXED (#266)**.
- #2 made `update-relationship` non-functional on every target — **FIXED (#267)**; the `xfail` is removed and the e2e passes live on both targets (and asserts an untouched cascade key survives the round-trip).
- #4 made ribbon **write** verbs non-functional on every target (ribbon read/export worked) — **FIXED (#269)**; the `xfail` is removed and the e2e lifecycle passes live on both targets.
- #3 is on-prem-only and intermittent (only collides on a repeat clone of the same source form).
- All four are in **product code** (`crm/core/*`), out of scope for the test-completeness
  branch that found them. The e2e suite encodes them as `xfail`/capability-gates so the
  fixes are detected automatically.
