# CRM Developer Scenarios — Issue Backlog Draft

**Date:** 2026-06-10
**Provenance:** drafted from 2026-06-crm-dev-scenarios-report.md §6 — pending user review, not yet filed

**Tally:**

| bucket | P1 | P2 | P3 | total |
|--------|----|----|-----|-------|
| bug | 4 | 0 | 0 | 4 |
| skill | 2 | 6 | 0 | 8 |
| cli-build | 2 | 4 | 6 | 12 |
| cli-adopt | 0 | 2 | 0 | 2 |
| **total** | **8** | **12** | **6** | **26** |

---

<!-- ===== P1 entries (8) ===== -->

## P1 | bug | Fix `metadata get-optionset` 400 on v9.1 with derived-type cast

**Rec ID:** B1
**Labels:** needs-triage
**Evidence:** TRIAL-2 (cli-bug; reproduced twice — trial and BUG-1 minimal repro); BUG-1 repro: `metadata get-optionset cwx_repro1` → `Could not find a property named 'Options' on type 'Microsoft.Dynamics.CRM.OptionSetMetadataBase'` (status 400, code 0x0); `list-optionsets` and generic `entity get GlobalOptionSetDefinitions <MetadataId>` both succeed on the same org.
**Proposed change:** B1 — cast to the derived type in the `get-optionset` request (e.g. `.../Microsoft.Dynamics.CRM.OptionSetMetadata?$expand=Options`) or issue the same request shape `list-optionsets` uses, instead of selecting `Options` on the `OptionSetMetadataBase` base type. The dedicated read verb for global optionsets is currently unusable against on-prem v9.1.
**Acceptance criteria:**
- `crm --json metadata get-optionset <name>` exits 0 and returns an `Options` array on an on-prem v9.1 org
- `crm --json metadata get-optionset <name>` continues to work on Dataverse online (no regression)
- `metadata list-optionsets` behaviour unchanged

---

## P1 | bug | Wrap `_load_payload` JSON parse failure in the `{ok:false}` envelope

**Rec ID:** B2
**Labels:** needs-triage
**Evidence:** TRIAL-3 (cli-bug; first hit in trial, confirmed in BUG-2 minimal repro); BUG-2 repro: `entity create accounts --data '{"name": "x", bad}'` → raw Python traceback + `[PYI-73711:ERROR] Failed to execute script '__main__'` with no JSON envelope, exit 1; `crm/commands/_helpers.py:317 _load_payload` is the unhandled site. Every agent workflow depends on the `{ok: false}` envelope contract — a raw traceback breaks all downstream `ok:` checks. Overlaps C8 in the error-envelope-hygiene family; this entry covers the JSON-parse crash specifically.
**Proposed change:** B2 — wrap the `JSONDecodeError` in `_load_payload` (crm/commands/_helpers.py:317) and emit `{"ok": false, "error": "invalid JSON in --data: <message>", "meta": {"category": "validation"}}`, exit 1 — the same treatment OSError file-read wrapping already receives. Applies to all callers of `_load_payload` (`entity create`, `entity update`, `entity upsert`, etc.).
**Acceptance criteria:**
- `crm --json entity create accounts --data '{"bad json'` exits 1 and emits a valid JSON envelope with `ok: false`, `meta.category: "validation"`, and a human-readable error string
- No Python traceback or PyInstaller error block appears in stdout or stderr
- A well-formed `--data` payload continues to work correctly (no regression)

---

## P1 | bug | Fix `solution remove-component` invalid parameter shape on v9.1

**Rec ID:** B3
**Labels:** needs-triage
**Evidence:** TRIAL-3 cleanup (cli-bug; reproduced twice — cleanup and BUG-3 minimal repro); BUG-3 repro: `solution remove-component --solution <s> --type entity --id <id>` → HTTP 400 `The parameter 'ComponentId' in the request payload is not a valid parameter for the operation 'RemoveSolutionComponent'` on v9.1; verified workaround: `action invoke RemoveSolutionComponent --body '{"SolutionComponent":{"solutioncomponentid":"<objectid>","@odata.type":"Microsoft.Dynamics.CRM.solutioncomponent"},"ComponentType":<n>,"SolutionUniqueName":"<name>"}'` exits 0. The verb is completely broken on v9.1.
**Proposed change:** B3 — change `solution remove-component` to send the `SolutionComponent` entity-reference shape (matching the verified workaround), not the `ComponentId` shape that `AddSolutionComponent` accepts. Also add a `meta.note` on `add-component --type entity` informing callers that the server may silently include required sub-components (`AddRequiredComponents` behaviour).
**Acceptance criteria:**
- `crm --json solution remove-component --solution <s> --type entity --id <MetadataId> --yes` exits 0 on an on-prem v9.1 org
- `solution add-component` is unchanged (no regression)
- `solution add-component --type entity` response includes a `meta.note` if the server returned more components than the single requested one

---

## P1 | bug | Pass/generate ImportJobId on import so `import-result` is usable on-prem

**Rec ID:** B4
**Labels:** needs-triage
**Evidence:** PROBE repro (skill-trial-log.md §Bug repros): `solution import <file> --yes` on v9.1 returns `import_job_id: null`; `query odata importjobs` shows no row for any CLI import; a missing-dependency import reports `ok:true, status:succeeded` while leaving a broken state; `solution import-result <id>` is structurally unusable without an id. SCN-053 (skill-gap: import-result/job-status absent from skill). Cross-reference: skill recipe for the on-prem fallback verification path is addressed by S1.
**Proposed change:** B4 — ensure `solution import` passes or generates an `ImportJobId` so that `import-result` has something to fetch after a CLI import on on-prem. Investigate whether the async import path can be made to create an ImportJob row; if the platform cannot produce one on v9.1, emit a `meta.warning` explaining the gap and document the on-prem substitute path (pairs with S1). Hard XSD errors already surface loudly; only dependency/semantic failures are currently swallowed.
**Acceptance criteria:**
- After `solution import` on v9.1, either `import_job_id` is non-null and `solution import-result <id>` exits 0, OR `meta.warnings` contains a clear explanation of why per-component results are unavailable on this platform
- `solution import` on Dataverse online continues to produce a fetchable `import_job_id` (no regression)
- A missing-dependency import does not silently report `status:succeeded` without any warning

---

## P1 | skill | Add "Investigating a failed import" recipe to solutions.md

**Rec ID:** S1
**Labels:** needs-triage
**Evidence:** SCN-053 (skill-gap: `import-result`/`job-status` absent from every reference file — only `job-cancel` appears in SKILL.md's destructive table); TRIAL-8 (agent never discovered either verb and fabricated a wrong diagnosis instead; most severe trial failure). Cross-reference: B4 addresses the ImportJobId gap that makes `import-result` structurally unusable on-prem.
**Proposed change:** S1 — add an "Investigating a failed import" section to solutions.md covering: (1) `solution import-result <jobid> [--formatted]` to re-fetch and parse a prior ImportJob; (2) `solution job-status` and `async get/list` for in-progress monitoring; (3) `solution validate --against-org` as the pre-import gate; (4) on-prem caveat — CLI imports may not create an ImportJob row on v9.1, so teach `solution components --diff` + targeted `metadata entity` checks as the fallback verification path.
**Acceptance criteria:**
- solutions.md contains a section covering `import-result`, `job-status`, and `validate --against-org` for import investigation
- The section includes an explicit on-prem caveat for the ImportJob evidence hole (pairs with B4)
- An agent reading only the installed skill can discover `import-result` and `job-status` without relying on `validate --against-org` alone
- No flags or defaults already in `--help` are restated

---

## P1 | skill | Add one-line records-verb router to SKILL.md

**Rec ID:** S4
**Labels:** needs-triage
**Evidence:** TRIAL-1, TRIAL-2, TRIAL-3 — the identical wrong-verb guess (`entity list`, `entity query`, root `odata`) appeared in 3 of 8 trials, the single largest agent-error cluster. Root cause: agents arrive expecting resource-style verbs; the real verbs are `query odata` / `metadata list-*`.
**Proposed change:** S4 — add a one-line router mapping to SKILL.md §Command discovery or §Agent contract: "list/query records → `query odata`; browse metadata → `metadata list-*` / `metadata entities` / `metadata attributes`". Workflow routing only, not a flag restatement.
**Acceptance criteria:**
- SKILL.md contains a router line mapping "list/query records" to `query odata` and "browse metadata" to `metadata list-*`
- The entry does not restate flag syntax already in `--help`
- A fresh agent following only the skill does not emit `entity list` or `entity query` as its first record-read attempt

---

## P1 | cli-build | Return clean validation error instead of bare HTTP 400 for OData URL args

**Rec ID:** C8 — Error clarity
**Labels:** needs-triage
**Evidence:** TRIAL-1 (cli-behavior): `query odata "solutions?$select=…"` → bare `HTTP 400`, `code: null`, no hint that the entity-set arg must be a bare set name. The server 400 passthrough gives agents no recovery signal. Overlaps B2 in the error-envelope-hygiene family; this entry covers the OData-URL passthrough case specifically.
**Proposed change:** C8 — when an entity-set argument to `query odata` contains `?` or `$`, return a client-side validation error before hitting the server: `{"ok": false, "error": "entity-set arg must be a bare set name (e.g. `solutions`); use --select/--filter for OData parameters", "meta": {"category": "validation"}}`, exit 1. Do not pass through the server 400 with `code: null`.
**Acceptance criteria:**
- `crm --json query odata "solutions?$select=uniquename"` exits 1 with `ok: false`, `meta.category: "validation"`, and a non-null hint explaining bare-name requirement
- `crm --json query odata solutions --select uniquename` continues to work (no regression)
- The `meta.code` field is not `null` for this client-side validation path

---

## P1 | cli-build | Parse inner error from multipart blob for batch leading-slash 404s

**Rec ID:** C11 — Error clarity
**Labels:** needs-triage
**Evidence:** TRIAL-6 cleanup (cli-behavior): a batch op `url` beginning with `/` resolves against the host root and 404s; the CLI surfaces the raw `--batchresponse…` multipart blob as the `error` field instead of the parsed inner message. With bare relative URLs the same batch file deleted 50/50 successfully.
**Proposed change:** C11 — (a) normalize or lstrip the leading slash from batch op `url` fields before sending (or reject client-side with a clear message); (b) parse the first inner error message out of any `--batchresponse…` multipart blob before placing it in the `error` field, so agents see a human-readable message instead of raw MIME content.
**Acceptance criteria:**
- A batch file with op `url` values beginning with `/` either works (if lstripped) or exits 1 with a JSON envelope explaining the issue — never surfaces a raw `--batchresponse_` boundary string in `error`
- A batch file with bare relative URLs (`entity(<id>)`) continues to work (no regression)
- Any multipart parse failure surfaces the first human-readable inner message

---

<!-- ===== P2 entries (12) ===== -->

## P2 | skill | Add plugin trace-debugging recipe to automation.md or troubleshooting.md

**Rec ID:** S2
**Labels:** needs-triage
**Evidence:** SCN-052 (skill-gap: automation.md stops at registration; neither automation.md nor troubleshooting.md names `plugintracelogs` or the `plugintracelogsetting` org setting); Plugin Trace Viewer 608k+ lifetime downloads proxies the daily frequency of this loop.
**Proposed change:** S2 — add a plugin trace-debugging recipe to automation.md (or troubleshooting.md) covering: (1) enable logging via `entity update organizations <orgid> --data '{"plugintracelogsetting":2}'`; (2) read traces via `query odata plugintracelogs --filter "typename eq '…'"` with a useful filter example; (3) disable after debugging (set back to 0). Closes the daily debug loop without PRT.
**Acceptance criteria:**
- automation.md (or troubleshooting.md) contains a trace-debugging recipe naming `plugintracelogs` and `plugintracelogsetting`
- The recipe includes a working `query odata plugintracelogs --filter` example targeting a plugin type name
- The recipe includes the org-setting enable/disable cycle
- No flags or defaults already in `--help` are restated

---

## P2 | skill | Add edit-existing-view recipe to authoring.md

**Rec ID:** S3
**Labels:** needs-triage
**Evidence:** SCN-011 (skill-gap: `view create` taught; edit-existing path absent from every reference file); TRIAL-3 (agent found the untaught path autonomously but cost ~7 extra discovery commands; a taught recipe would halve the call count); community: https://community.dynamics.com/blogs/post/?postid=ef44c4f2-0e6a-45ac-8464-5a8bdd3312cc.
**Proposed change:** S3 — add an edit-existing-view recipe to authoring.md in ~3 lines: `query odata savedqueries --filter "name eq '<name>'"` to locate the GUID → `entity update savedqueries <guid> --data '{"fetchxml":"…","layoutxml":"…"}'` → `solution publish`. Include a note that `returnedtypecode` is a string (entity logical name), not an int.
**Acceptance criteria:**
- authoring.md contains an edit-existing-view recipe naming `savedqueries`, `entity update savedqueries`, and the publish step
- The recipe includes the `returnedtypecode` typing note (string, not int — filter by `name` or logical entity name)
- An agent following the recipe can edit a view's fetchxml/layoutxml without additional discovery commands

---

## P2 | skill | Document `crm batch` standalone command in records.md

**Rec ID:** S5
**Labels:** needs-triage
**Evidence:** SCN-043 (covered but skill-incomplete: `crm batch` standalone command absent from the skill; records.md mentions `$batch` only as data-import internals); TRIAL-6 cleanup (leading-slash 404 — the exact gotcha the recipe must warn about; cross-reference C11).
**Proposed change:** S5 — add a `crm batch` section to records.md covering: (1) the file format (JSON array of op objects with `method`, `url`, `body`); (2) the gotcha that `url` values must be bare relative paths — a leading slash resolves against the host root and produces a 404 (overlaps C11); (3) a minimal bulk-delete example.
**Acceptance criteria:**
- records.md contains a `crm batch` section distinct from the `data import` internals reference
- The section explicitly warns that `url` must be a bare relative path (no leading slash)
- A minimal bulk-delete example is present

---

## P2 | skill | Add two gotchas to solutions.md for cross-env role moves

**Rec ID:** S6
**Labels:** needs-triage
**Evidence:** SCN-047 (covered; two gotchas untaught: `role` not named in `add-component --type` friendly-name examples; managed-import privilege-stripping silent); community: https://github.com/MicrosoftDocs/power-platform/blob/main/power-platform/alm/how-managed-solutions-merged.md.
**Proposed change:** S6 — add two one-liners to solutions.md: (1) name `role` in the `add-component --type` friendly-name examples so the cross-env role move is self-documenting; (2) warn that managed-solution import silently strips manually-added privileges — always re-use the same solution for all role updates rather than adding privileges directly on the target org.
**Acceptance criteria:**
- solutions.md `add-component` examples include `role` as a named `--type` value
- solutions.md contains a warning about managed-import privilege-stripping with the "re-use the same solution" mitigation

---

## P2 | skill | State validate-first as the default for unattended writes

**Rec ID:** S7
**Labels:** needs-triage
**Evidence:** TRIAL-7 (skill-text candidate): plain create returned cryptic `Does not support untyped value in non-open type`; `--validate` produced the clean `unknown_fields`/`did_you_mean` envelope; `--validate` is currently opt-in. SCN-044.
**Proposed change:** S7 — update records.md and SKILL.md §Agent contract to state that `--validate` is the recommended default for agent-driven writes. Suggested wording: "For unattended writes, use `--validate` (optionally combined with `--dry-run`); without it, unknown fields return raw OData server noise." Workflow recommendation only — no flag syntax restated.
**Acceptance criteria:**
- records.md and/or SKILL.md §Agent contract contain guidance that validate-first is the recommended default for unattended writes
- The guidance references the raw OData noise agents receive without `--validate`
- No `--validate` flag syntax already in `--help` is restated

---

## P2 | skill | Document on-prem caveat: business rules cannot be deactivated via the Web API

**Rec ID:** S8
**Labels:** needs-triage
**Evidence:** TRIAL-5 (verified on two rules: `workflow deactivate <business-rule-id>` → 0x80045002 `Cannot update a published workflow definition` on v9.1; classic workflows and BPFs unaffected); SCN-021/023.
**Proposed change:** S8 — add a one-line on-prem caveat near `activate`/`deactivate` in automation.md §Workflows: "On on-prem v9.1, published business rules (category 2) cannot be deactivated via the Web API (0x80045002) — use the classic UI. `deactivate` works for classic workflows (category 0)."
**Acceptance criteria:**
- automation.md contains a note that category-2 business rules cannot be deactivated via the Web API on on-prem v9.1
- The note names the error code 0x80045002 for agent pattern-matching
- The note clarifies that `deactivate` continues to work for category-0 classic workflows

---

## P2 | cli-build | Build plugin step-image registration verbs

**Rec ID:** C4 — ergonomics
**Labels:** needs-triage
**Evidence:** SCN-018 (covered for assembly/step; images remainder — no `register-image` verb, keyword scan zero hits in codebase); Plugin Registration Tool covers images via GUI only (on-prem + online); step images are required for pre/post-image access in plugins; high harvest frequency (plugin iteration loop is the primary pro-code developer daily cycle).
**Proposed change:** C4 — build `plugin register-image` and `plugin unregister-image` verbs alongside the existing `register-step`/`unregister-step` pair. Minimum flags: `--step <step-id>`, `--type pre|post`, `--alias`, `--attributes` (optional filter list). Makes the full plugin lifecycle (assembly → step → image → trace) scriptable without PRT.
**Acceptance criteria:**
- `crm plugin register-image --step <id> --type pre --alias <alias>` exits 0 and creates a `sdkmessageprocessingstepimage` record on both v9.1 on-prem and Dataverse online
- `crm plugin unregister-image <image-id>` exits 0 and deletes the image
- `crm describe plugin` documents the new verbs
- automation.md is updated with an image-registration example

---

## P2 | cli-adopt | Document or thin-wrap early-bound class generation tools

**Rec ID:** C5 — adopt/wrap
**Labels:** needs-triage
**Evidence:** SCN-019 (cli-gap: no codegen verbs; dual-target tools exist — XrmToolBox Early Bound Generator (SDK, on-prem + online), CrmSvcUtil.exe (.NET Full Framework, both); `pac modelbuilder` is online-only and cannot help on-prem).
**Proposed change:** C5 — document the dual-target codegen tools (XrmToolBox Early Bound Generator, CrmSvcUtil.exe) in automation.md or a dedicated how-to page. Include invocation examples pairing with `crm profile` for org-URL retrieval. Explicitly note that `pac modelbuilder` is online-only. Optionally add a thin `crm codegen` wrapper that shells out to CrmSvcUtil if on PATH.
**Acceptance criteria:**
- The skill or docs reference XrmToolBox Early Bound Generator and CrmSvcUtil.exe by name with a usage note
- The reference explains that `pac modelbuilder` is online-only
- (optional, only if wrapper verb is built): `crm codegen --output-dir <dir>` exits 0 and produces classes when CrmSvcUtil is on PATH

---

## P2 | cli-adopt | Document or thin-wrap display-label export/translate/import

**Rec ID:** C6 — adopt/wrap
**Labels:** needs-triage
**Evidence:** SCN-008 (cli-gap: no translation verbs; dual-target tools exist — XrmToolBox Easy Translator (SDK, on-prem + online), native Export Translations XML (both); the `action invoke ExportTranslation/ImportTranslation` escape hatch is a base64-zip-in-action-body trap, same shape as SCN-035's agent-infeasible hatch).
**Proposed change:** C6 — document XrmToolBox Easy Translator and the native Export Translations XML workflow in a skill reference section or how-to page. Explicitly warn that `action invoke ExportTranslation` returns a base64-zipped XML blob requiring manual decode + extraction — agent-infeasible without a wrapper. Optionally build a thin `crm translation export/import` wrapper that handles the base64/zip plumbing.
**Acceptance criteria:**
- The skill or docs name XrmToolBox Easy Translator and native Export Translations XML as dual-target tools
- If the `action invoke` escape hatch is mentioned, the base64-zip trap is documented with a warning
- (optional, only if wrapper verb is built): `crm translation export --entity account -o labels.zip` exits 0 and writes a usable zip

---

## P2 | cli-build | Build managed-lifecycle verbs (clone-as-patch, stage-and-upgrade, uninstall)

**Rec ID:** C7
**Labels:** needs-triage
**Evidence:** SCN-035 (cli-gap, partial→trap: no holding/StageAndUpgrade/CloneAsPatch/uninstall verbs; `action invoke CloneAsPatch/DeleteAndPromote` escape hatch is agent-infeasible; matrix explicitly flags this as not an out-of-scope challenge because these are supported server actions, distinct from the rejected offline zip-clone GUID-regen #166); `pac solution import --stage-and-upgrade` is online-only; on-prem has only the classic UI.
**Proposed change:** C7 — build first-class verbs for the managed-solution lifecycle: `solution clone-as-patch`, `solution stage-and-upgrade` (holding import), `solution uninstall`, wrapping the `CloneAsPatch`, `StageAndUpgrade`/`DeleteAndPromote` server actions. Closes the on-prem cliff for managed upgrades.
**Acceptance criteria:**
- `crm solution clone-as-patch --solution <name>` exits 0 and creates a patch solution on both v9.1 on-prem and Dataverse online
- `crm solution stage-and-upgrade <zipfile>` exits 0 and imports a solution in holding mode on both targets
- `crm solution uninstall --solution <name> --yes` exits 0 and removes the solution
- solutions.md is updated with a managed-upgrade recipe
- Each verb composes with `--dry-run` and `--json`

---

## P2 | cli-build | Add descending-order support to `view create --order`

**Rec ID:** C10 — ergonomics
**Labels:** needs-triage
**Evidence:** TRIAL-3 (cli-behavior): `--order` on `view create` is ascending-only; "newest first" forced an immediate savedquery PATCH of a view that had just been created, requiring the untaught edit path even at create time. SCN-011.
**Proposed change:** C10 — add descending-order support to `view create --order`. Options: (a) `--order-desc <column>` alongside `--order <column>`; (b) direction-suffix syntax `--order 'createdon desc'`; (c) separate `--order-direction asc|desc` flag. Any form eliminates the forced post-create PATCH for "newest first" views.
**Acceptance criteria:**
- `view create --order createdon desc` (or equivalent flag form) creates a view with `descending="true"` in the layoutxml without requiring a subsequent `entity update savedqueries` PATCH
- `view create --order createdon` (ascending) continues to work (no regression)
- authoring.md documents the descending-order syntax

---

## P2 | cli-build | Improve `did_you_mean` suggestion ranking for field-name typos

**Rec ID:** C12 — ergonomics
**Labels:** needs-triage
**Evidence:** TRIAL-7 (cli-behavior nit): `--validate` suggestion for `telephoneone` was `telephone3`; intended field was `telephone1`; agent had to confirm via `metadata describe account`. Lexically close ≠ semantically right.
**Proposed change:** C12 — improve the `did_you_mean` ranking in `--validate` to prefer numeric-suffix-adjacent suggestions (e.g. `telephone1` over `telephone3` for input `telephoneone`) and/or use a semantic distance heuristic that weights common field-naming patterns (numbered families, common prefix/suffix patterns). Edit-distance alone is insufficient.
**Acceptance criteria:**
- `entity create accounts --validate --data '{"telephoneone": "…"}'` suggests `telephone1` (not `telephone3`) in `did_you_mean`
- Suggestions for genuinely unique field-name typos are not regressed

---

<!-- ===== P3 entries (6) ===== -->

## P3 | cli-build | Build workflow-to-flow migration inventory and readiness assessment

**Rec ID:** C1 — build
**Labels:** needs-triage
**Evidence:** SCN-022 (cli-gap: best external tool — Power Automate designer + MS migration tooling — is online-only; cloud flows do not exist on-prem; realistic CLI scope is migration inventory + readiness assessment, not authoring). `workflow list --category 5` can already inventory modern flows (online).
**Proposed change:** C1 — build a workflow-to-flow migration assist that (1) inventories classic workflows eligible for cloud-flow migration (`workflow list --category 0 --entity <entity>` + a readiness-check heuristic), and (2) outputs a machine-readable readiness report. Authoring cloud flows via API is out of scope; the on-prem value is planning intelligence.
**Acceptance criteria:**
- `crm workflow migration-assess` (or `workflow list --migration-report`) exits 0 and produces a machine-readable readiness list on Dataverse online
- On an on-prem profile, the command exits 0 with a clear "cloud flows are not available on on-prem" message (no error)
- automation.md documents the migration-assess workflow

---

## P3 | cli-build | Build unmanaged-layer conflict detection for on-prem

**Rec ID:** C2 — build
**Labels:** needs-triage
**Evidence:** SCN-054 (cli-gap: best external tool — XrmToolBox Solution Layers Explorer — needs `msdyn_componentlayer` which does not exist on-prem; on-prem users have zero detection path; community frequency: every long-lived org accumulates unmanaged-layer drift). On-prem cliff: strongest build candidate for the no-scriptable-path-anywhere class.
**Proposed change:** C2 — build `solution layer-conflicts` that compares component lists across two exported solutions to identify overlapping components (potential unmanaged-layer conflicts). For on-prem: `solution extract` + XML comparison. For online: query `msdyn_componentlayer` via OData if available. Output: list of components present in both managed and unmanaged layers with solution names.
**Acceptance criteria:**
- `crm solution layer-conflicts --solution <managed> --unmanaged-solution <unmanaged>` exits 0 and lists overlapping components on both v9.1 on-prem and Dataverse online
- Exit 0 with an explicit "no conflicts found" message (not a silent empty list)
- solutions.md documents the `layer-conflicts` command

---

## P3 | cli-build | Build environment admin verbs (online-only gap, no dual-target tool)

**Rec ID:** C3 — build (lower priority)
**Labels:** needs-triage
**Evidence:** SCN-037 (cli-gap: `pac admin create/copy/backup/restore` is online-only; on-prem uses Deployment Manager / PowerShell snap-in / SQL backups — on-prem only; no dual tool covers both targets).
**Proposed change:** C3 — build first-class `environment` verbs wrapping the Dataverse online environment-admin server actions (`create`, `copy`, `backup`, `restore`). On-prem: emit a clear "environment admin is not available on on-prem; use Deployment Manager" message. Lower priority (admin persona noted in passing) but the only CLI path for scripted environment management without `pac admin`.
**Acceptance criteria:**
- `crm environment list` exits 0 and returns a list of environments on a Dataverse online profile
- `crm environment copy --source <env-id> --display-name <name>` exits 0 and initiates an environment copy on Dataverse online
- On an on-prem profile, each environment verb exits 0 with a clear message naming Deployment Manager as the on-prem alternative (no error traceback)
- `crm describe environment` documents the new verbs

---

## P3 | cli-build | Build `query fetchxml` optional ENTITY_SET positional

**Rec ID:** C9 — ergonomics
**Labels:** needs-triage
**Evidence:** TRIAL-1 (cli-behavior): `query fetchxml` requires both `--xml` (even when XML is the only payload) AND a separate `ENTITY_SET` positional even though the fetch XML already names the entity via `<fetch><entity name="…">`. Two failed attempts before discovering the correct shape.
**Proposed change:** C9 — make the `ENTITY_SET` positional optional when `--xml` or `--file` is provided. Parse the entity name from the `<fetch entity="…">` attribute and use it if the positional is omitted; fall back to requiring the positional only if the fetch XML contains multiple entity elements or the attribute is absent.
**Acceptance criteria:**
- `crm query fetchxml --xml '<fetch><entity name="accounts">…</entity></fetch>'` (no ENTITY_SET positional) exits 0 and returns results
- `crm query fetchxml accounts --xml '<fetch>…</fetch>'` continues to work (backward compat)
- A fetch XML with a missing or ambiguous entity attribute still prompts for the positional with a clear error

---

## P3 | cli-build | Hint singular logical name when metadata 404 receives a set name

**Rec ID:** C13 — ergonomics (minor)
**Labels:** needs-triage
**Evidence:** TRIAL-7 (cli-behavior nit): `metadata describe accounts` (set name) → 404 `LogicalName='accounts' does not exist`; singular `account` required. Mirrors TRIAL-4's `webresources`/`webresourceset` confusion. Agent-error class seen in 2 of 8 trials.
**Proposed change:** C13 — when `metadata describe <name>` returns a 404, check if `<name>` matches a known entity-set name or common pluralization pattern and emit a `hint` or `did_you_mean` field: "Did you mean `account`? `metadata describe` takes the logical name (singular), not the entity-set name." Mirror the `--validate` `did_you_mean` pattern.
**Acceptance criteria:**
- `crm --json metadata describe accounts` exits 1 with a `hint` or `did_you_mean` field suggesting `account`
- `crm --json metadata describe account` continues to return entity metadata (no regression)
- The hint fires for at least common pluralization patterns (append `s`, append `set`)

---

## P3 | cli-build | Accept friendly names on `workflow list --category`

**Rec ID:** C14 — ergonomics (minor)
**Labels:** needs-triage
**Evidence:** NOT-A-BUG repro (skill-trial-log.md §Bug repros): `workflow list --category workflow` exits 2 with `Invalid value for '--category': 'workflow' is not a valid integer`. The CLI is correct; this is the only product idea the phantom bug yielded. Lower priority: usability improvement only, no broken behaviour.
**Proposed change:** C14 — extend `--category` on `workflow list` to accept both integers and friendly names: `workflow` (0), `businessrule` (2), `action` (3), `bpf` (4), `dialog` (1), `flow` (5). Integers continue to work unchanged.
**Acceptance criteria:**
- `crm workflow list --category workflow` exits 0 and returns the same rows as `crm workflow list --category 0`
- `crm workflow list --category bpf` exits 0 and returns BPF definitions
- `crm workflow list --category 0` continues to work (backward compat)
- `crm workflow list --category invalid` exits 2 with a usage error naming accepted friendly names and integers
