# Plan: Fix ImportJobId rejection on on-prem (issue #140)

## Context

`crm solution import` sends `ImportJobId` in the `ImportSolutionAsync` payload.
On-prem D365 CE v9.1 (NTLM) rejects this:

```
ODataException: The parameter 'ImportJobId' in the request payload is not a valid parameter
for the operation 'ImportSolutionAsync'.
```

Dataverse online accepts `ImportJobId`. The fix must be transparent (same command, both targets).

**Key files:**
- `crm/core/solution.py` — `import_solution()` at line ~676, `_async_export_unavailable()` at line 503
- `crm/utils/d365_backend.py` — `poll_async_operation()` at line 774, returns the full asyncoperation row
- `crm/tests/test_core.py` — `TestImportSolutionAsync` at line ~1080
- `crm/tests/test_solution_import_result.py` — import result tests

**Out of scope (already implemented):** --wait flag, friendlymessage on failure, --json output, export changes.

---

## Task 1: Core fix — ImportJobId fallback in import_solution()

**Files:** `crm/core/solution.py`

**Approach (mirrors export fallback):**
1. Add `_import_job_id_rejected(exc: D365Error) -> bool` — True when "ImportJobId" appears in the error and message contains "not a valid parameter" or "invalid parameter".
2. In `import_solution()`, wrap the `backend.post("ImportSolutionAsync", json_body=body)` call in try/except:
   - On match: rebuild body without `ImportJobId`, set `import_job_id = None`, retry POST.
3. After `poll_async_operation` returns the completed asyncop row (`op`):
   - If `import_job_id is None`, recover from `op.get("_regardingobjectid_value")` (OData polymorphic lookup GUID).
   - If still None after recovery attempt, skip the post-import importjobs read (best-effort) and omit those fields from output.
4. All downstream code that uses `import_job_id` already guards safely: polling passes it as `None` (suppresses progress ticks from the importjobs side-channel), final GET is conditional.

**Tests to add (in `crm/tests/test_core.py` or `test_solution_import_result.py`):**
- POST raises ImportJobId-rejection error → retried without ImportJobId → `_regardingobjectid_value` extracted → poll and importjobs read use recovered id.
- POST raises ImportJobId-rejection error → recovery succeeds, output includes recovered `import_job_id`.
- Online path (ImportJobId accepted) still works unchanged.
- POST raises a different D365Error → not retried (re-raised as-is).

**Verify:** `pytest crm/tests/test_core.py crm/tests/test_solution_import_result.py -x`

---

## Task 2: Update SKILL.md + docs (if user-visible behavior changed)

**Files:** `crm/skills/SKILL.md`, `docs/how-to/solution.md`

No user-visible behavior change (same command works on both targets). No doc update needed unless a note about on-prem compatibility is warranted.

Check: does `docs/how-to/solution.md` mention any on-prem caveat for import? If so, remove/update it. Otherwise skip.
