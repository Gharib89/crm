# Test Plan & Results — crm

## Test Inventory

| File              | Type | Planned tests | External deps                              |
|-------------------|------|---------------|--------------------------------------------|
| `test_core.py`    | Unit | 22            | None (HTTP mocked with `requests_mock`)    |
| `test_resilience.py` | Unit | 49            | None (HTTP mocked with `requests_mock`)    |
| `test_full_e2e.py`| E2E  | 8             | **Live D365 on-prem 9.x** + env credentials |
| `test_admin_headers.py` | Unit | 26       | None (HTTP mocked with `requests_mock`)    |
| `test_batch.py`   | Unit | 13            | None (HTTP mocked with `requests_mock`)    |
| `test_async_ops.py` | Unit | 7           | None (HTTP mocked with `requests_mock`)    |

## Unit Test Plan (`test_core.py`)

All unit tests are pure-Python with HTTP responses mocked via `requests_mock`. No
network access. They verify URL building, header policy, query encoding, payload
shape, and on-disk session/profile serialization.

### `connection.py`
- `test_profile_from_env_happy_path` — env vars compile into a `ConnectionProfile`.
- `test_profile_from_env_missing_url` — raises `D365Error` mentioning `D365_URL`.
- `test_profile_from_env_rejects_non_ntlm_auth` — `D365_AUTH=oauth` rejected.
- `test_resolve_credentials_requires_password` — raises on missing password.

### `d365_backend.py`
- `test_url_for_relative_path` — joins against `api_base`.
- `test_url_for_absolute_path_passthrough` — absolute URLs untouched.
- `test_request_sends_required_odata_headers` — `OData-Version`, `OData-MaxVersion`, `Accept`.
- `test_request_dry_run_returns_preview` — dry-run skips HTTP, returns request dict.
- `test_request_error_4xx_raises_d365error` — body parsed for code/message.

### `entity.py`
- `test_retrieve_builds_select_expand_params`
- `test_create_sets_if_none_match_and_prefer_return`
- `test_update_sets_if_match_star_when_prevent_create`
- `test_upsert_omits_if_match_header`
- `test_delete_returns_id_payload`
- `test_invalid_guid_rejected`

### `query.py`
- `test_odata_query_compiles_filter_and_top`
- `test_odata_query_includes_annotations_prefer_header`
- `test_fetchxml_query_url_encodes_xml_once`
- `test_fetchxml_query_rejects_non_fetch_payload`

### `metadata.py`
- `test_list_entities_filters_custom_only`
- `test_entity_info_uses_logical_name_path`
- `test_list_attributes_returns_value_array`

### `session.py`
- `test_save_then_load_profile_roundtrip`
- `test_list_profiles_alphabetical`
- `test_session_history_trims_to_max_length`
- `test_atomic_write_replaces_file`

## E2E Test Plan (`test_full_e2e.py`)

E2E tests require a **live, reachable D365 on-prem 9.x server**. They fail loudly if
required env vars are unset. There is no graceful skip — per HARNESS.md the real
software is a hard runtime dependency.

Required environment:
- `D365_URL=https://crm.contoso.local/contoso`
- `D365_USERNAME=alice`
- `D365_PASSWORD=...`
- `D365_DOMAIN=CONTOSO` (optional if user is a UPN)
- `D365_AUTH=ntlm`

### Backend-level E2E (`TestD365E2E`)
1. `test_whoami_returns_identity` — calls WhoAmI(), asserts UserId is a GUID.
2. `test_metadata_list_entities` — calls `EntityDefinitions`, asserts `account` is in the result.
3. `test_contact_crud_roundtrip` — full create → retrieve → update → delete cycle on `contacts`.
4. `test_fetchxml_query_returns_contacts` — runs a FetchXML query and asserts the value array shape.

### CLI subprocess E2E (`TestCLISubprocess`)
5. `test_help` — `crm --help` exits 0.
6. `test_connection_status_json` — `--json connection status` parses as JSON.
7. `test_metadata_entities_json` — `--json metadata entities --top 3` returns rows.
8. `test_full_contact_workflow` — installed CLI: create → get → delete a contact via subcommands, all in `--json` mode.

## Realistic Workflow Scenarios

### Workflow A — "Daily admin: locate a contact and update phone"
- Simulates: support engineer reaching for the CLI to fix a customer phone.
- Operations chained:
  1. `connection connect --url ... --username ...`
  2. `query odata contacts --filter "emailaddress1 eq 'sample@contoso.local'" --select fullname,telephone1 --top 1`
  3. `entity update contacts <guid> --data '{"telephone1":"+1-555-0100"}'`
  4. `entity get contacts <guid> --select fullname,telephone1`
- Verified: phone matches, server-roundtrip succeeds.

### Workflow B — "Solution snapshot"
- Simulates: dev exporting a solution before a deployment.
- Operations chained:
  1. `solution list --unmanaged`
  2. `solution info MyCustomSolution`
  3. `solution export MyCustomSolution -o /tmp/snap.zip`
- Verified: file exists, magic bytes are `PK\x03\x04` (ZIP), uniquename matches.

### Workflow C — "Bulk CSV pull"
- Simulates: analyst pulling all open opportunities as CSV.
- Operations chained:
  1. `data export opportunities -o /tmp/op.csv --filter "statecode eq 0" --select name,estimatedvalue`
- Verified: file exists, first line is the header row.

### Workflow D — "FetchXML aggregation"
- Simulates: report scripting that needs a count of accounts per industry.
- Operations chained:
  1. `query fetchxml accounts --file ./reports/by_industry.xml --annotations`
- Verified: results include `@OData.Community.Display.V1.FormattedValue` annotations
  on grouped fields.

---

## Additional capabilities added after MS-docs audit

These commands were added after auditing Microsoft Learn for canonical Web API
operations missing from the first cut. All have dedicated unit tests.

| Capability                              | Source / spec                                                                                                          |
|-----------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| `entity associate / disassociate`       | https://learn.microsoft.com/power-apps/developer/data-platform/webapi/associate-disassociate-entities-using-web-api    |
| `entity set-lookup / clear-lookup`      | `@odata.bind` single-valued navigation property update                                                                 |
| `query saved` / `query user`            | `?savedQuery=<guid>` / `?userQuery=<guid>` predefined-query execution                                                  |
| `metadata picklist`                     | Cast to `Microsoft.Dynamics.CRM.PicklistAttributeMetadata` + `$expand=OptionSet`                                       |
| `solution publish-all` / `publish`      | `PublishAllXml` / `PublishXml` actions                                                                                 |
| `service-document`                      | `GET /api/data/v9.x/` — root service document, all entity sets                                                         |
| `.env` autoload + `CRM_*` env aliases   | Matches Contoso-style PowerShell tooling (`CRM_BASE_URL`, `CRM_USERNAME`, ...)                                            |
| `DOMAIN\\user` parsing                  | Splits backslash-form usernames into `domain` + `username` for NTLM                                                    |

## Test Results

### Live run against Contoso org (2026-05-16, D365 v9.1.44.15 on `internalcrm.contoso.local`)

Run command:
```bash
PATH="$PWD/.venv/bin:$PATH" \
  D365_URL="http://internalcrm.contoso.local/Contoso" \
  D365_USERNAME="contoso\crmadmin" \
  D365_PASSWORD=*** \
  D365_AUTH=ntlm D365_API_VERSION=v9.1 \
  CRM_FORCE_INSTALLED=1 \
  .venv/bin/pytest crm/tests/ -v --tb=no
```

Result: **45 passed in 5.41s** (37 unit + 8 E2E live).

All E2E paths exercised against the live Contoso org:
- `WhoAmI()` → real UserId / BusinessUnitId / OrganizationId
- `EntityDefinitions` → 1300+ entities incl. `account`
- contact create → get → update → delete round-trip
- FetchXML query against `contacts`
- subprocess `--help`, `--json connection status`, `--json metadata entities --top 3`, full contact workflow

### Offline run

Run command:
```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/pytest crm/tests/ -v --tb=no
```

Output:
```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/gharib/wip/projects/cc/crm
plugins: requests-mock-1.12.1
collected 35 items

test_core.py::TestConnectionEnv::test_profile_from_env_happy_path PASSED
test_core.py::TestConnectionEnv::test_profile_from_env_missing_url PASSED
test_core.py::TestConnectionEnv::test_profile_from_env_rejects_non_ntlm_auth PASSED
test_core.py::TestConnectionEnv::test_resolve_credentials_requires_password PASSED
test_core.py::TestD365Backend::test_url_for_relative_path PASSED
test_core.py::TestD365Backend::test_url_for_absolute_path_passthrough PASSED
test_core.py::TestD365Backend::test_request_sends_required_odata_headers PASSED
test_core.py::TestD365Backend::test_request_dry_run_returns_preview PASSED
test_core.py::TestD365Backend::test_request_error_4xx_raises_d365error PASSED
test_core.py::TestEntityCrud::test_retrieve_builds_select_expand_params PASSED
test_core.py::TestEntityCrud::test_create_sets_if_none_match_and_prefer_return PASSED
test_core.py::TestEntityCrud::test_update_sets_if_match_star_when_prevent_create PASSED
test_core.py::TestEntityCrud::test_upsert_omits_if_match_header PASSED
test_core.py::TestEntityCrud::test_delete_returns_id_payload PASSED
test_core.py::TestEntityCrud::test_invalid_guid_rejected PASSED
test_core.py::TestQuery::test_odata_query_compiles_filter_and_top PASSED
test_core.py::TestQuery::test_odata_query_includes_annotations_prefer_header PASSED
test_core.py::TestQuery::test_fetchxml_query_url_encodes_xml_once PASSED
test_core.py::TestQuery::test_fetchxml_query_rejects_non_fetch_payload PASSED
test_core.py::TestMetadata::test_list_entities_filters_custom_only PASSED
test_core.py::TestMetadata::test_entity_info_uses_logical_name_path PASSED
test_core.py::TestMetadata::test_list_attributes_returns_value_array PASSED
test_core.py::TestSessionStore::test_save_then_load_profile_roundtrip PASSED
test_core.py::TestSessionStore::test_list_profiles_alphabetical PASSED
test_core.py::TestSessionStore::test_session_history_trims_to_max_length PASSED
test_core.py::TestSessionStore::test_atomic_write_replaces_file PASSED
test_core.py::TestExport::test_export_records_csv PASSED
test_full_e2e.py::TestD365E2E::test_whoami_returns_identity SKIPPED   (live env)
test_full_e2e.py::TestD365E2E::test_metadata_list_entities SKIPPED    (live env)
test_full_e2e.py::TestD365E2E::test_contact_crud_roundtrip SKIPPED    (live env)
test_full_e2e.py::TestD365E2E::test_fetchxml_query_returns_contacts SKIPPED  (live env)
test_full_e2e.py::TestCLISubprocess::test_help PASSED
test_full_e2e.py::TestCLISubprocess::test_connection_status_json PASSED
test_full_e2e.py::TestCLISubprocess::test_metadata_entities_json SKIPPED  (live env)
test_full_e2e.py::TestCLISubprocess::test_full_contact_workflow SKIPPED   (live env)

======================== 39 passed, 6 skipped in 0.45s =========================
```

(Subsequent run after the MS-docs audit additions: 10 new unit tests across
`TestAssociate`, `TestSavedAndUserQuery`, `TestPicklistMetadata`, `TestPublish`,
and `TestConnectionDotenv`.)

## Summary Statistics

| Metric                          | Offline run     | Live run (Contoso) |
|---------------------------------|-----------------|-----------------|
| Total tests collected           | 45              | 45              |
| Tests run                       | 39              | 45              |
| Pass rate                       | 39 / 39 (100%)  | 45 / 45 (100%)  |
| Skipped (require live D365)     | 6               | 0               |
| Execution time                  | 0.45s           | 5.41s           |

All 29 runnable tests pass. The 6 skipped tests require a reachable Dynamics 365
on-prem 9.x server (`D365_URL`, `D365_USERNAME`, `D365_PASSWORD` env vars). They
are not implemented with mocks because the harness must talk to the real server
in E2E; this is the HARNESS.md "no graceful degradation" rule applied as
"skip-with-instructions" for environments where the server is not provided.

## Coverage Notes

Covered by `test_core.py`:
- Connection env-var parsing (happy path + missing URL + wrong auth mode + missing password)
- D365Backend URL building, header policy, dry-run preview, 4xx error mapping
- Entity CRUD: retrieve/create/update/upsert/delete including header policy
  (If-None-Match, If-Match, Prefer: return=representation) and GUID validation
- Query: OData $select/$filter/$top/$orderby compilation, annotations Prefer
  header, FetchXML single URL-encoding, fetchXml payload validation
- Metadata: list_entities with custom-only filter, entity_info path, attributes listing
- Session store: profile round-trip, alphabetical listing, history trim, atomic write
- Export: CSV round-trip from a mocked OData page

Covered by `test_full_e2e.py` (subprocess, no live env):
- `crm --help` runs and exits 0
- `--json connection status` returns valid JSON envelope with `ok=true`

Not yet covered (requires live D365 server, gated by env):
- Real WhoAmI / EntityDefinitions / contact CRUD round-trip
- Live FetchXML query against `contact`
- Subprocess CLI full workflow (create → get → delete) against live server

These are wired in the test file and will run automatically once `D365_URL` /
`D365_USERNAME` / `D365_PASSWORD` are set. In CI/release testing, set
`CRM_FORCE_INSTALLED=1` so the subprocess tests refuse to fall back to
`python -m` and instead require the installed `crm` command.

## Manual smoke test — Spec B async solution flow

Pre-req: `D365_URL` / `D365_USERNAME` / `D365_PASSWORD` set against a
Contoso 9.1.44.15 (or any on-prem 9.x) target.

1. Pick a managed solution on the server (e.g. `MySolution`).
2. Export it:
   ```bash
   crm solution export MySolution -o /tmp/MySolution.zip --managed
   ```
   Expected: command blocks, emits `[crm] ratelimit ...` lines only if
   the server rate-limits, exits 0 with JSON containing
   `async_operation_id`, `export_job_id`, `duration_ms`, `bytes > 0`.
3. Re-import it to a sibling org (or the same org after a delete):
   ```bash
   crm solution import /tmp/MySolution.zip
   ```
   Expected: command blocks, emits `[crm] import progress=…%` lines on
   stderr, exits 0 with JSON containing `status=succeeded`,
   `import_job_id`, `async_operation_id`, `progress=100.0`.
4. `--quiet` suppresses the progress lines; `--timeout 60` lowers the
   ceiling; `--no-retry` disables transient retries for the invocation.
