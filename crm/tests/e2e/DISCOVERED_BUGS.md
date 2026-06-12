# Product bugs surfaced by the live e2e suite

Four real product defects were found while building the live e2e coverage
(`crm/tests/e2e/`). None are test bugs — each reproduces through the normal CLI
against a live org. Each is documented in-suite as an `xfail(strict=False)` (or a
capability gate), so the suite stays green **and** auto-flips to a failure/xpass the
moment the underlying command is fixed.

Repro uses a saved profile directly (`--profile`); no e2e harness needed. Targets
referenced: an on-prem NTLM org and a cloud OAuth/Dataverse org.

| # | Bug | Tracking issue |
|---|-----|----------------|
| 1 | `metadata list-actions` / `list-functions` → HTTP 415 | Gharib89/crm#266 |
| 2 | `metadata update-relationship` → HTTP 405 | Gharib89/crm#267 |
| 3 | `form clone` reuses embedded formid (on-prem) | Gharib89/crm#268 |
| 4 | `ribbon add-button` / `remove` blocked by validation | Gharib89/crm#269 |

---

## 1. `metadata list-actions` / `metadata list-functions` → HTTP 415 (both targets)

**Symptom**
```
$ crm --profile crmworx --json metadata list-functions
{"ok": false,
 "error": "A supported MIME type could not be found that matches the acceptable MIME types for the request.",
 "meta": {"status": 415, "code": "0x80060888", "category": "validation"}}
```
Same 415 on cloud (v9.2). Affects both `list-actions` and `list-functions`.

**Root cause** — both verbs read the OData `$metadata` CSDL document to enumerate
actions/functions. That endpoint is served as **XML (CSDL)**, but the request is sent
with `Accept: application/json`, so Dataverse rejects it with 415.

**Fix** — send `Accept: application/xml` (and parse CSDL XML) for the `$metadata`
request path specifically; the rest of the Web API stays JSON.

**Test** — `crm/tests/e2e/test_metadata_read.py::test_metadata_list_actions` /
`::test_metadata_list_functions` (`xfail`, reason cites this 415).

**Repro**
```
crm --profile crmworx --json metadata list-actions      # → 415
crm --profile cloud    --json metadata list-functions   # → 415
```

---

## 2. `metadata update-relationship` → HTTP 405 (both targets)

**Symptom**
```
HTTP 405 "does not support operation"
on-prem v9.1 code 0x0 ; cloud v9.2 code 0x80060888
```

**Root cause** — the command issues a PUT to the typed **cast** segment
`RelationshipDefinitions(<id>)/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata`.
Dataverse rejects a write to that cast path with 405 on both targets.

**Fix** — update relationship metadata via the supported path (PUT/PATCH on
`RelationshipDefinitions(<id>)` with the `@odata.type` in the body, per the Web API
metadata-update contract), not a PUT to the type-cast URL segment.

**Test** — `crm/tests/e2e/test_metadata_write.py::test_update_relationship_cascade`
(`xfail`, reason cites the 405 cast-path write).

**Repro** — create a 1:N relationship (e.g. via `metadata create-one-to-many`), then:
```
crm --profile crmworx metadata update-relationship <relationship_schema> --cascade-assign cascade
# → HTTP 405
```

---

## 3. `form clone` reuses the embedded form id on on-prem (on-prem only)

**Symptom** — cloning the same source form a second time on on-prem v9.1 fails with a
duplicate-key error (`0x8004f658`). Cloud v9.2 is unaffected.

**Root cause** — `crm.core.forms.retarget_formxml` rewrites entity-name tokens in the
FormXML but does **not** strip/regenerate the `<form id="...">` attribute embedded in
the XML. On-prem v9.1 reuses that embedded id as the new `systemform` record's primary
key, so a second clone of the same source collides. Cloud assigns a fresh GUID
server-side and hides the problem.

**Fix** — strip or regenerate the `formid` inside the FormXML before POST (don't rely
on the server to reassign the PK).

**Test** — `crm/tests/e2e/test_form.py::test_form_clone_account_to_ephemeral`
(`@pytest.mark.requires_cloud` — verified on cloud, skipped on on-prem; docstring
documents the on-prem defect).

**Repro** (on-prem) — run twice against the same source form:
```
crm --profile crmworx form clone account <source_form_id> --target-entity <custom_entity>
crm --profile crmworx form clone account <source_form_id> --target-entity <custom_entity>   # 2nd → 0x8004f658
```

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

**Fix** — `apply_ribbon_change` should call the apply path with `validate=False`, or
`validate_solution` should accept a flag to skip the guid-collision check for
update-imports (vs. fresh installs).

**Test** — `crm/tests/e2e/test_ribbon.py::test_ribbon_add_and_remove_button`
(`xfail`, reason cites the validation false-positive).

**Repro**
```
crm --profile crmworx ribbon add-button <custom_entity> --label "E2E" --command-js "alert('x')"
# → aborts in validate_solution / _check_org_collisions
```

---

### Severity / notes
- #1 and #2 make their verbs **completely non-functional** on every target — highest priority.
- #4 makes ribbon **write** verbs non-functional on every target (ribbon read/export work).
- #3 is on-prem-only and intermittent (only collides on a repeat clone of the same source form).
- All four are in **product code** (`crm/core/*`), out of scope for the test-completeness
  branch that found them. The e2e suite encodes them as `xfail`/capability-gates so the
  fixes are detected automatically.
