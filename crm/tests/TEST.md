# Test Plan & Results — crm

## Test map

The offline suite under `crm/tests/` is named after what it exercises: a core
module `crm/core/<m>.py` is covered by `test_<m>.py` plus feature slices
`test_<m>_<feature>.py` (for example `test_query_paging.py`), and a command group
`crm/commands/<g>.py` by `test_<g>_cmd.py` where the group has its own command-layer
file. `test_core.py` is the original mixed file, one class per module. The modules
whose tests do not follow the naming:

| Module | Test files |
|---|---|
| `crm/utils/d365_backend.py` | `test_core.py::TestD365Backend`, `test_resilience.py` (retries, throttling, timeouts), `test_admin_headers.py`, `test_auth_scheme.py`, `test_error_taxonomy.py` (`classify_d365_error`) |
| `crm/utils/adfs.py` | `test_adfs_auth.py` |
| `crm/core/connection.py` | `test_core.py::TestApiVersionNegotiation`, `test_connection_core.py`, `test_oauth_auth.py` |
| `crm/core/session.py` | `test_core.py::TestSessionStore`, `test_session_audit.py`, `test_touch_session.py`, `test_plaintext_secret.py` (stored secrets) |
| `crm/core/entity.py` | `test_core.py::TestEntityCrud`, `::TestAlternateKeyPath`, `::TestResolveAlternateKey`, `::TestAssociate`, `test_entity_*.py` |
| `crm/core/query.py` | `test_core.py::TestQuery`, `::TestSavedAndUserQuery`, `test_query_*.py` |
| `crm/core/metadata.py` | `test_core.py::TestMetadata`, `::TestPicklistMetadata`, `::TestCreateEntity`, `::TestCreateVirtualEntity`, `::TestCreateEntityReadback`, `test_metadata_*.py` |
| `crm/core/export.py` | `test_core.py::TestExport`, `::TestOrderedKeys` |
| `crm/core/solution.py`, `solution_transfer.py` | `test_core.py::TestPublish`, `::TestExportSolutionAsync`, `::TestImportSolutionAsync`, `test_solution_*.py` |
| `crm/core/workflow.py` | `test_core.py::TestWorkflow`, `::TestWorkflowDelete`, `test_workflow_*.py` |
| `crm/core/hints.py` | `test_hints.py` |
| `crm/utils/repl_skin.py` | `test_repl_skin.py` |
| `crm/cli.py` (`CLIContext`, emit envelope) | `test_core.py::TestErrorEnvelope`, `::TestReplBackendCache`, `test_output_contract.py`, `test_exit_codes.py`, `test_cli_offline_smoke.py` |
| `crm/commands/_helpers/` | `test_core.py::TestLoadPayload`, `test_concise_render.py` (`rendering.py`), `test_helpers_option_groups.py`, `test_helpers_package_surface.py`, `test_d365_errors_enrich.py` |
| `crm/commands/profile.py` | `test_profile_cmd.py`, `test_profile_helpers.py`, `test_profile_name_sanitization.py`, `test_profile_url_normalization.py` |

Gates that span the whole CLI rather than one module: `test_e2e_coverage_gate.py`
(every D365-touching command has live e2e coverage or an `E2E_SKIP` reason),
`test_docs_command_examples.py` (every documented `crm` example parses),
`test_skill_bundle.py`, `test_skill_coverage_gate.py` and `test_skill_lint_gate.py`
(the shipped skill), and `test_sync_skills.py` (the vendored dev skills).

## Test seams

Every offline test fakes D365 at one of three seams (`conftest.py` holds the
fixtures and the canonical literals):

- **Core:** a real `D365Backend` with `requests_mock` at the wire, through the
  `backend` / `dry_backend` fixtures. Exercises URL building, headers, retries and
  error parsing.
- **Commands:** `FakeBackend` (`conftest.py`) injected at `CLIContext.backend`
  through `make_fake_backend` / `fake_backend` plus `inject_backend`. Bypasses the
  transport; for command-layer tests that care only about the parsed response.
- **CLI:** Click's `CliRunner` over the `crm` group, for parsing, exit codes and
  the emit envelope.

Live D365 is reached only by the opt-in e2e suite below (`-m e2e`, `D365_E2E=1`);
the default `addopts` deselects it.

## Live E2E suite (`crm/tests/e2e/`)

Every D365-touching CLI verb has a live e2e test under `crm/tests/e2e/` (one file per
command group). Tests are **opt-in** and excluded from the default `pytest` run by
`addopts = -m 'not e2e'`; everything collected under `crm/tests/e2e/` is auto-marked
`e2e`.

**Opt-in:** set `D365_E2E=1` plus a target. The `live_profile` fixture seeds a throwaway
profile under an isolated `CRM_HOME` and activates it; the CLI resolves from that throwaway
profile only. Two credential sources (#273):

- **Named profile** — `D365_E2E_PROFILE=<name>` selects an existing profile (created via
  `crm profile add`). Its definition + secret are read **read-only** from your real
  `CRM_HOME` and re-seeded into the isolated home, so a run never mutates your real
  profiles/session. The **target is inferred from the profile's auth scheme** (OAuth →
  cloud, NTLM → on-prem) — there is no separate target flag. A missing profile / missing
  secret fails loudly at setup. Prefer a cloud profile for general local runs (no VPN).
- **Flat `D365_*` env** (used when `D365_E2E_PROFILE` is unset — the CI path):
  - on-prem (NTLM): `D365_URL`, `D365_USERNAME`, `D365_PASSWORD` (+ optional `D365_DOMAIN`).
  - cloud (OAuth): `D365_AUTH=oauth`, `D365_URL`, `D365_CLIENT_ID`, `D365_PASSWORD` (the
    client secret, or `D365_CLIENT_SECRET`), `D365_TENANT_ID`. `D365_USERNAME` is **not**
    required for OAuth.

A production-host guard (`_assert_not_production`) refuses to run against hosts matching
`prod`/`live`/`.crm.dynamics.com` (whether the URL came from a profile or env) unless
`D365_E2E_ALLOW_HOST` names the target host.

**Reachability (VPN):** at session setup the fixture issues one short-timeout GET to the
service root. A connection-level failure (DNS/TCP/timeout — host unreachable, e.g. on-prem
with the VPN down) **skips the whole session** with a "VPN down?" message instead of
cascading errors across every test. **Any HTTP response — including 401/403 — counts as
reachable**, so auth/server failures surface normally rather than being masked as
"unreachable". (Edge case: a *cloud* target whose AAD authority is itself unreachable
surfaces as an OAuth/auth error rather than a skip — an MSAL failure carries a synthetic
401, not a transport failure. Cloud is the no-VPN target, so this is rare; the skip path
is aimed at on-prem/VPN.)

**Shared-fixture artifact marker (#769).** The shared metadata fixtures — `ephemeral_entity`
(session-scoped custom entity) and `ephemeral_solution` (module-scoped publisher + solution) —
stamp every artifact name/display with the `E2E_MARKER` constant (`conftest.py`, currently
`"ae2e"`), plus a unique per-run suffix. On the long-lived shared cloud org (#760) this makes
an automated e2e leak greppable and safe to sweep. The marker doubles as the publisher
customization **prefix** (`marker_prefix`), so it is kept DB-legal — 2–8 alphanumerics,
letter-start, ≤8 chars (`crm.core.solution.validate_customization_prefix`); the offline
`crm/tests/test_e2e_marker.py` pins those rules. Per-test literals (`E2E WR`, `E2E View`, …)
are already suffixed + self-cleaning and are deliberately left unmarked.

### Running all three targets at once (`scripts/e2e_all.py`)

`python scripts/e2e_all.py` runs the suite across all three standing profiles in one go —
on-prem (`-m e2e`), cloud and cs-trial (`-m 'e2e and requires_cloud'`) — each with its own
profile, host-guard override, and the dotnet/pac PATH the plugin/packager tests need. It
writes `logs/e2e/<ts>/<leg>.{log,xml}` and a `summary.txt` scored on **union coverage**: a
test is a GAP only if it skipped on *every* leg (ran nowhere); a skip that's covered on
another leg is counted, not chased. `rerun <logdir>` re-runs just the failures and genuine
gaps, each on its original leg. See the script's docstring; `selftest` self-checks the parser.

### Dedicated CS cloud target — provisioning checklist (ADR 0012)

Several verbs need a **Customer-Service-provisioned, pollution-tolerant** Dataverse org
the general `agent-cloud` org can't host (`sla create`/`add-kpi` need CS; `audit detail`
needs org auditing + an audited row; `workflow run` needs a seeded on-demand workflow).
The interim target is a **self-service Customer Service trial** reached through an
**ephemeral `agent-cs-trial`** profile — a *duplicate* of `agent-cloud` with the URL
re-pointed (same tenant → reuse the Entra app registration). It does **not** replace
`agent-cloud`, and **CI is not pointed at it** (the trial expires ≤60 days; see the ADR
0012 addendum). The CS-verb tests **skip-with-instructions** when it's absent, so they run
only on a local `--profile agent-cs-trial` pass while the trial lives.

One-time maintainer setup (each time a trial is stood up):

1. **CS-provisioned Dataverse env.** A self-service CS trial (sign up at the Dynamics 365
   Customer Service product page) clears the license/capacity wall at $0 and ships CS
   preinstalled; it expires in 30 days (one self-service extension → 60).
2. **S2S application user.** In PPAC → the trial env → Application users, add the
   `agent-cloud` Entra app registration (same tenant) with **System Administrator**; only
   the profile URL differs from `agent-cloud`. Without it, `whoami` returns *"the user is
   not a member of the organization."*
3. **Auditing on.** PPAC → Security → Compliance → Auditing → the env → **Turn on
   auditing** → **Common entities across Dynamics 365 apps** (flips org `IsAuditEnabled`
   and audits Account/etc. + columns in one toggle). Unblocks `audit detail`.
4. **No-op on-demand workflow.** In **make.powerapps.com**, create a classic workflow
   process on the **Account** table: **background** (`mode=0`) **and** *Available to Run →
   As an on-demand process* (`ondemand=true`), activated, stepless. The Web API cannot
   create a workflow definition, so this is web-app-only. `mode` is fixed at creation — a
   real-time workflow can't be flipped to background, so recreate it if wrong. Unblocks
   `workflow run` dispatch.
5. **Host guard.** Set `D365_E2E_ALLOW_HOST=<trial host>` for the local run (the trial's
   `*.dynamics.com` host changes per provisioning; kept in local memory, not committed).

**Run forms:**
- Full sweep:   `pytest -m e2e`
- Quick pass:   `pytest -m "e2e and not slow"`  (skips publish/import-heavy tests)
- One group:    `pytest -m e2e crm/tests/e2e/test_entity.py`  (the `-m e2e` is required —
                a bare path is deselected by the default filter and exits 5)
- `pytest -m slow` overrides the default filter and WILL select the slow e2e tests.

**Coverage gate (offline, runs in normal CI):** `crm/tests/test_e2e_coverage_gate.py`
walks the lazy Click command tree and fails unless every D365-touching verb has a
`@covers("<group> <verb>")` test **or** an `E2E_SKIP` entry (with a reason) in
`crm/tests/e2e/coverage.py`. Local/meta groups (`profile`, `session`, `skill`,
`self-update`, `repl`, `scaffold`) are out of scope (`LOCAL_GROUPS`).

**Doc-examples gate (offline, runs in normal CI):** `crm/tests/test_docs_command_examples.py`
extracts every `crm …` example from the docs + README (fenced code blocks and inline
`` `code spans` ``) and validates each against the real CLI tree from `crm --json
describe` — failing when a command **path** doesn't resolve, a **global option** is
placed after the command (and the leaf doesn't redefine it), or an **unknown flag**
is used. It is fully offline (in-process `CliRunner`, no network). Prose/placeholder
references are filtered structurally (a non-prefix word before `crm`, or a bare
`crm <group>` with nothing trailing, is a reference, not an example); genuine
hypotheticals and diagrams (`crm codegen`, `crm bpf`, the README architecture box)
live in a small commented `ALLOWLIST` keyed by the normalized command string. The
gate **fails closed**: a new unrecognized broken example is a failure, not a skip.

**Test classification — which bucket does a test belong to?** The single criterion is:
*does the verb's **observable** behavior (returned fields, error codes, defaults, feature
existence) depend on the backend?* Transport differences (NTLM vs OAuth) do **not** count.
These four buckets reuse existing markers — none is bucket-specific (the one
standalone marker is `offline`, below):

| Bucket | Criterion | Mechanism |
|--------|-----------|-----------|
| **any** | Identical OData semantics; only transport differs (the majority — entity CRUD, query, metadata read, data import/export) | **default, no marker.** One reachable target suffices; prefer cloud (no VPN). |
| **on-prem-only** | Only works/behaves on NTLM/on-prem | `@pytest.mark.requires_onprem` |
| **cloud-only** | Only works on Dataverse | `@pytest.mark.requires_cloud` |
| **both / divergent** | Works on both but **asserts different values** per target | **no marker** — branch on the `target` fixture (`"cloud"`/`"onprem"`) and assert per-target; runs on both union legs |

**`offline` marker** — orthogonal to the four buckets above (which all describe
*live* tests). `@pytest.mark.offline` tags a test that needs **no** live org at all,
only a local binary — e.g. `solution pack`/`extract` shelling out to `pac`. It is
exempt from the live opt-in/reachability gate (it bypasses `live_profile` via the
autouse `_enforce_capability` gate), so it runs in plain CI with `D365_E2E` unset (#529).

**Capability gating & target divergence:** mark a test `@pytest.mark.requires_cloud` /
`requires_onprem` when a verb only works on one target; the marker skips it on the other.
The AD FS (IFD) sign-in test (`connection whoami`, #978) is `requires_onprem` and also skips
unless `D365_E2E_PROFILE` names an `adfs`-scheme profile, so a plain on-prem run reports it skipped.
For a verb that works on both but returns different values, take the `target` fixture and
branch the assertion (e.g. `expected = "v9.2" if target == "cloud" else "v9.1"`) — it then
runs meaningfully on each union leg. Full coverage = the **union** of an on-prem run and a
cloud run. `E2E_SKIP` is now **empty** — every D365-touching verb has live coverage.
(`solution extract`/`pack` are now covered live: an offline pac `pack → extract` roundtrip
(#529) packs a committed minimal solution fixture and re-extracts it, asserting the envelope and
that the solution's UniqueName survives the roundtrip — it needs only `pac` on PATH (provisioned
in CI on every push, no D365 org/creds/VPN) and skips with an install hint when `pac` is absent.
`solution stage-and-upgrade`/`apply-upgrade` are now covered: one single-org managed-upgrade
lifecycle test (#512) builds a managed v1+v2 of an empty throwaway solution via
`export --managed`, drops the unmanaged author copy, imports the managed base, stages the v2
holding solution, then promotes it separately via `apply-upgrade` — asserting the installed
base version flips 1.0.0.0→2.0.0.0; teardown uninstalls and leaves the org clean. `audit detail`
is now covered: its test generates an audit row inline (create + audited update) and
skips-with-instructions where auditing is off, so it runs on a CS-target leg and skips on
the general cloud org. `theme publish` is now covered too: its test captures the active
theme, publishes a throwaway, then re-publishes the captured original to restore the org.
`workflow run` is now covered too: a dispatch-only `requires_cloud` test resolves a seeded,
activated, background on-demand workflow, dispatches it against a throwaway record, and asserts
a non-null async operation id — skipping with instructions where no such workflow is seeded, so
it runs on a CS-target leg and skips on the general cloud org. `sla create`/`add-kpi` are now
covered by one CS-provisioned lifecycle test (#511): it ensures the target `incident` entity is
SLA-enabled (publish-requiring), creates the SLA and attaches a KPI item with valid per-KPI
FetchXML, then deletes the SLA and restores the flag — skipping with setup instructions when
Customer Service is absent, so it runs on a `--profile agent-cs-trial` leg and skips on the
general cloud org.) The
plugin assembly lifecycle (`register-assembly`/`unregister-assembly`/`unregister-step`) is
covered by one live test that builds a signed no-op IPlugin from committed C# source via
`dotnet build` (#506), skipping with instructions when the .NET SDK is absent. The convergent
`apply` plug-in kind (assembly + types + steps + images, #552) is covered by one
`@requires_onprem` lifecycle test that reuses that built assembly — on-prem metadata writes are
synchronous, so a single apply registers the whole plug-in in one pass; on cloud a new
plug-in type's read-after-write lag would flake a single-shot apply, so the weekly cloud e2e
gates it out. `workflow update --xaml-file` (on-prem XAML step-editing, #540) adds a
target-divergent pair: a `@requires_onprem` test replaces a draft clone's genuine designer XAML
wholesale (never activating it, to avoid undeletable type=2 activation residue), and a
`@requires_cloud` test asserts the provenance-wall refusal before any write. The `apply` apps
reconcile pass (component-set converge + whole-document sitemap converge, #796) is covered by one
lifecycle test that drives every verdict (unchanged → `skipped`, add/drop a component →
`updated`, sitemap drift → `updated`) and now runs on **both** targets (the `requires_onprem`
gate is removed, #809): apply app-publishes the created app (app-scoped `PublishXml`) and
reconcile reads it back through `RetrieveUnpublishedMultiple`, so the create→reconcile
round-trip converges on Dataverse online as well as on-prem v9.x — see `DISCOVERED_BUGS.md` #5
(now FIXED, Gharib89/crm#809). Tests that document
a live product defect are
marked `xfail(strict=False)` so they auto-flip to xpass when the command is fixed.

### Live run record

| Date | Target | Suite | Passed | Skipped | xfailed | Duration |
|------|--------|-------|--------|---------|---------|----------|
| 2026-06-12 | on-prem (NTLM, v9.1) | `pytest -m e2e` (full) | 83 | 7 | 5 | 8m32s |
| 2026-06-12 | cloud (OAuth, v9.2)  | `pytest -m e2e` (full) | 85 | 5 | 4 (+1 xpass) | 14m48s |
| 2026-06-22 | on-prem (NTLM, v9.1) | `pytest -m e2e` (full) | 171 | 7 | 1 | 59m21s |
| 2026-06-22 | cloud (OAuth, v9.2)  | `pytest -m e2e` (full) | 174 | 6 | 0 (+1 xpass) | 1h51m |

The 2026-06-22 on-prem run surfaced **2 failures**, both on-prem-only product bugs (cloud
passed them): `app create --if-exists skip` not swallowing the on-prem SQL-duplicate fault
`0x80040216`/500 (#496, fixed by #499) and `clone-entity` re-creating uncreatable lookup
`…Name`/`…YomiName` companion columns (#497, fixed by #501). Both fixes were verified live on
on-prem (the tests pass against the merged code); they are no longer failing on `main`.

Full coverage = the **union** of the two runs. Capability-gated tests skip on the
non-matching target (e.g. `plugin register-image` is on-prem-only;
`solution layer-conflicts` are cloud-only). The `bigint` attribute test xfails on
on-prem (system-managed) and xpasses on cloud. The 5/4 xfails are documented product
defects — see `crm/tests/e2e/DISCOVERED_BUGS.md`. `sla activate` skips are data-gated:
that record isn't present on either test org. `workflow activate`/`deactivate` (both
targets) and `workflow clone` (cloud) are no longer data-gated — a durable draft
**background on-demand** unmanaged classic workflow on Account ("E2E Seed: On-Demand
Draft") is seeded on both orgs (#732). Recreate it without the web app by round-tripping
genuine designer XAML: `workflow export` any classic workflow → edit `name`/`ondemand` →
`workflow import` (cloud) or `workflow clone` + `workflow update --on-demand` (where the
id already exists); the Web API accepts genuine XAML even though it can't author it (#534).
`workflow run` (cloud, `requires_cloud`) is likewise seeded — a second, **activated**
on-demand copy on Account ("E2E Seed: On-Demand Activated"; the draft one must stay draft
for activate/deactivate, so `workflow run` needs its own). The `$count`-clamp assertion
(`test_diagnostics.py`) has a durable >5000-row table on both orgs (5001 marker-tagged
contacts, `jobtitle="E2E-BULK-SEED"`, bulk-deletable), but its gate reads
`RetrieveTotalRecordCount`, which the platform **caches ~12–24h** — the live collection
`$count` already clamps at 5000+`has_more`, yet the test stays skipped until the cached
count refreshes past 5000. (`query saved`/`query user` now self-seed a throwaway view, so
they no longer skip.) `audit detail` stays skipped (org auditing off, deliberately
un-seeded). `test_apply_reconciles_app_components_and_sitemap` (#796) also stays skipped on
both available orgs: the appmodule it creates is not GET-retrievable, so the reconcile
round-trip it drives can't run (`DISCOVERED_BUGS.md` #5, Gharib89/crm#809). Hostnames omitted
(Contoso placeholders only).

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
| `metadata picklist`                     | Type-aware cast: `PicklistAttributeMetadata` / `StateAttributeMetadata` / `StatusAttributeMetadata` (#229)             |
| `solution publish-all` / `publish`      | `PublishAllXml` / `PublishXml` actions                                                                                 |
| `service-document`                      | `GET /api/data/v9.x/` — root service document, all entity sets                                                         |
| `DOMAIN\\user` parsing                  | Splits backslash-form usernames into `domain` + `username` for NTLM                                                    |

## Manual smoke test — Spec B async solution flow

Pre-req: a saved profile (`crm profile add`) against a
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
