# Skill Trial Log — June 2026

**Protocol:** fresh subagent per trial card (see `2026-06-skill-trial-plan.md`); agent context = installed skill (`~/.claude/skills/crm/`, v2.4.0) + `crm` binary only; controller reviews the full transcript (not the agent's self-report) against the card's expected path, then runs the card's cleanup with `--json` read-back verification. Stumble = any failed command, wrong path taken, or friction the agent had to discover its way around.

**Stumble schema:** `| trial | step | what-went-wrong | root-cause (skill-text/cli-behavior/cli-bug/agent-error) | evidence (command + envelope) |`

A trial with zero stumbles gets one `clean pass` row.

## Summary

**Trials run:** 8 of 8 (sequential, 2026-06-10). **Task completion:** 7/8 — TRIAL-8 "completed" but with a fabricated diagnosis under a false premise, scored as a substantive fail. **Clean passes:** 1 strict (TRIAL-6 bulk import), 1 near-clean (TRIAL-4 webresource loop, one minor entity-set-name guess).

**Stumbles by root-cause class** (agent-trial rows + controller-found notes):

| class | count | items |
|---|---|---|
| cli-bug | 3 | `metadata get-optionset` 400 on v9.1 (T2); malformed `--data` JSON → raw Python traceback, no envelope (T3); `solution remove-component` sends invalid `ComponentId` parameter (T3 cleanup). A 4th candidate (`workflow list --category`) did not reproduce — reclassified controller-error, see Bug repros |
| cli-behavior | 8 | opaque `HTTP 400` for raw-OData-URL arg (T1); `query fetchxml` redundant `--xml`+ENTITY_SET shape (T1); `view create --order` ascending-only (T3); root-only `--json` flag placement + `describe` arg shape (T3); business-rule state PATCH blocked 0x80045002 on-prem (T5); `did_you_mean` suggestion quality (T7); importjob `data` empty on v9.1 → per-component evidence hole (T8); batch leading-slash URL → host-root 404 with raw multipart error blob (T6 cleanup) |
| skill-text | 2 | `solution import-result`/`job-status` absent from skill — agents do not discover them (T8 probe); validate-first not stated as the default for unattended writes (T7) |
| agent-error | 14 | recurring verb guesses `entity read/query/list`, root `odata` (T1,T2,T3 — systemic), set-vs-logical naming (T4,T7), savedqueries `returnedtypecode` typing ×2 (T1,T3), hallucinated flags/properties (T1,T3,T7), action invocation shape (T2), guardrail-escalation `--bypass-plugins` after server 400 (T5), no premise-contradiction check + fabricated root cause (T8 ×2) |
| controller-error | 3 | TRIAL-5 card precondition wrong (probe wrapper ignored `ok:false` — the phantom `--category` bug); TRIAL-8 zip construction (export auto-includes required components); TRIAL-6 cleanup batch URLs written with leading slash |

**Cross-trial patterns:** (1) the `entity list`/`entity query` guess appeared in 3 of 8 trials — agents arrive expecting resource-style verbs; one router line mapping "list/query records → `query odata`" would erase the class. (2) Both severe failures (T5 escalation flailing, T8 fabricated diagnosis) happened *after* a confusing server response — error-path guidance matters more than happy-path docs. (3) Agent self-reports systematically omit recovered errors: every per-trial stumble table here came from transcript envelopes, not from what the agents said about themselves.

Summary compiled after all trials; per-trial detail below.

---

## TRIAL-1 — SCN-005/002/032/029 full customization workflow

**Outcome:** task completed end-to-end (agent chose the declarative `apply` route — table + 4 columns + global optionset + view in one spec — then exported and verified the zip; a legitimate, taught alternative to the imperative `metadata create-entity` chain on the card). Cleanup complete: `metadata delete-entity` + `metadata delete-optionset` (the global optionset was an extra artifact of the `apply` route, not on the card), read-backs confirm gone, `solution components agtrial1` = 0.

**Transcript-level stumbles** (the agent's final self-report mentioned none of these — all recovered in-flight):

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 1 | discover record-read verb | guessed `entity read`, then `entity query`, `entity list`, root `odata` — four `No such command` failures before finding `entity get` / `query odata` via `crm --help` | agent-error (guessed instead of `crm describe` first; skill's records-reference covers the real verbs but the agent never opened it for this "customization" task) | `crm entity read solutions <id>` → exit 2 `No such command 'read'. Did you mean 'create'?`; `crm odata "publishers?..."` → exit 2 `No such command 'odata'.` |
| 1 | raw OData URL as entity-set arg | `query odata "solutions?$select=…&$filter=…"` returned bare `HTTP 400` with no hint that the entity-set argument must be a bare set name with flags for $select/$filter | cli-behavior (error clarity: server 400 passthrough, `code: null`, no `did_you_mean`-style guidance) | `crm query odata "solutions?$select=uniquename…"` → exit 1 `{"ok": false, "error": "HTTP 400", "meta": {"status": 400, "code": null, "category": "validation"}}` |
| 1 | fetchxml invocation shape | two failed attempts: `--xml` is required even when XML is the obvious payload, AND a separate `ENTITY_SET` positional is required although the fetch XML already names the entity | cli-behavior (redundant required argument; ergonomics) | `query fetchxml "<fetch…>"` → `Either --xml or --file is required.`; `query fetchxml --xml "<fetch…>"` → exit 2 `Missing argument 'ENTITY_SET'.` |
| 1 | savedqueries filter typing | `returnedtypecode eq '10136'` → OData type error; retry with int `10136` → on-prem MetadataCache error (server wants the logical name, not the ObjectTypeCode) — agent abandoned OData and pivoted to FetchXML | agent-error (schema misunderstanding) + on-prem server quirk; CLI surfaced both server errors verbatim | `--filter "returnedtypecode eq 10136"` → `The entity with a name = '10136' with namemapping = 'Logical' was not found in the MetadataCache` |
| 1 | publisher property guess | `--select …,optionvalueprefix` → clean 400 `Could not find a property named 'optionvalueprefix'` | agent-error (hallucinated property); CLI validation envelope was clear and the agent recovered immediately | `query odata publishers --select "uniquename,customizationprefix,optionvalueprefix"` → 400 property-not-found |

**Positive observations:** dry-run-first discipline before `apply` (taught by SKILL.md agent contract, followed); spec-based `apply` chosen over 7 imperative calls; zip verified by reading `customizations.xml`, not just `unzip -l`.

**Meta-observation (for the report):** the agent's final self-report claimed an error-free run — every stumble above is visible only in the transcript envelopes. Trial methodology must always review transcripts, and unattended agent runs need envelope-level logging.

---

## TRIAL-2 — SCN-003 global option-set lifecycle

**Outcome:** task completed; the mutation path matched the card exactly (`create-optionset --option` → `update-optionset --insert-option/--update-option` → `--reorder` → read-back proof of final order Critical/High/Standard/Low). Read-back had to detour around a broken CLI verb (below).

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 2 | read back the created optionset | **`metadata get-optionset cwx_maintenancepriority` fails on v9.1 on-prem** — server rejects the request the verb issues; agent had to detour via `metadata list-optionsets` (to find the MetadataId) + `entity get GlobalOptionSetDefinitions <MetadataId>` | **cli-bug (repro in Task 7)** — the dedicated read verb is unusable against this on-prem target while the generic entity path works | `metadata get-optionset cwx_maintenancepriority` → exit 1 `{"ok": false, "error": "Could not find a property named 'Options' on type 'Microsoft.Dynamics.CRM.OptionSetMetadataBase'", "meta": {"status": 400, "category": "validation"}}` |
| 2 | record-verb guesses (recurring) | `entity query …` and `entity list …` → `No such command` — the same two wrong guesses as TRIAL-1 before falling back to working verbs | agent-error, but now a **recurring pattern across trials** (agents expect `entity list/query`; real verbs are `query odata` / `metadata list-*`) — skill-text/discoverability signal | `entity query GlobalOptionSetDefinitions --filter …` → exit 2 `No such command 'query'.` |
| 2 | get-by-name | `entity get GlobalOptionSetDefinitions cwx_maintenancepriority` → clean client-side validation `Invalid record id (expected GUID)`; agent recovered via list-optionsets | agent-error; CLI envelope clear (positive) | exit 1, `category: validation`, `status: null` |
| 2 | unbound function guess | `action RetrieveOptionSet --body …` → `No such command 'RetrieveOptionSet'` (real shape is `action invoke/function <name>`) | agent-error (invocation shape); minor discoverability note | exit 2 `No such command 'RetrieveOptionSet'.` |

**Positive observations:** agent ran `crm describe metadata` + three `--help` calls BEFORE the first mutation (the contract the skill teaches); `--solution agtrial2` passed on every mutation; auto-assigned option values handled correctly; final proof = ordered read-back table.

---

## TRIAL-3 — SCN-011 view create → edit existing view (skill-gap probe)

**Outcome:** task completed — and the skill-gap probe answered: the agent FOUND the untaught edit path on its own (inspected an existing account view's `fetchxml`/`layoutxml` to learn the shape, then `entity update savedqueries <id>` PATCH + `solution publish --xml`). Smart strategy; cost it ~7 extra discovery commands. Probe conclusion: the path is findable but expensive — a taught recipe would have removed ~half the trial's calls.

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 3 | malformed `--data` JSON | embedding raw FetchXML (with `"` attribute quotes) inside a shell-interpolated `--data` JSON string produced invalid JSON — and the CLI **crashed with a raw Python traceback** (`json.decoder.JSONDecodeError … _load_payload … [PYI-67980:ERROR] Failed to execute script`) instead of a clean `ok:false` envelope | **cli-bug (repro in Task 7)** — `crm/commands/_helpers.py:317 _load_payload` does not wrap `JSONDecodeError`; envelope/exit-code contract violated. (The malformed input itself was agent-error; the crash is the bug) | `entity update savedqueries <id> --data "{\"fetchxml\": \"<fetch …=\"true\" />…\"}"` → exit 1, raw traceback ending `JSONDecodeError: Expecting ',' delimiter: line 1 column 31` |
| 3 | `view create` sort direction | `--order` sorts ascending only — no descending form; "newest first" required an immediate PATCH of the view it had just created | cli-behavior (missing `--order-desc`/direction syntax; forces the untaught savedquery-PATCH route even for create-time needs) | `view create … --order createdon` then corrective `entity update savedqueries <id>` patching `descending="true"` |
| 3 | record-verb guesses (recurring ×3) | `entity query`, `entity list` → `No such command` — third consecutive trial with the identical guesses | agent-error, systemic — same as TRIAL-1/2 | exit 2 `No such command 'query'.` / `'list'.` |
| 3 | `describe` flag/arg shape | `crm describe view --json` → `No such option '--json'` (it's a root-only flag: `crm --json describe view`); `crm describe view create` → `Got unexpected extra argument` (describe takes a group, not a command path) | cli-behavior (root-only flag placement surprises agents; minor) | exit 2 on both |
| 3 | `metadata attributes --search` | guessed a `--search` flag that doesn't exist; recovered by listing all attributes and filtering client-side | agent-error; minor ergonomics note (server-side attribute search could shave a step) | `No such option '--search'.` |
| 3 | savedqueries `returnedtypecode` typing (recurring ×2) | `returnedtypecode eq 1` (int) → `Edm.String vs Edm.Int32` type error — same column tripped TRIAL-1; the column is a *string* (entity logical name), so `eq 'account'` is the working form; agent dodged it by filtering on `name` instead | agent-error + on-prem schema surprise, recurring — a one-line skill note would kill this class | 400 `A binary operator with incompatible types was detected` |

**Positive observations:** read an existing system view to learn XML shape before writing any (excellent untaught-path strategy); `--data-file` adopted after the inline-JSON crash; published after BOTH edits (users see the update — the exact pitfall the scenario targets); final read-back verified fetchxml + layoutxml together.

**Cleanup note (controller, TRIAL-3):** the view's `--solution agtrial3` had also added the **account entity** as a solution component. Removing it surfaced a third bug: `solution remove-component --solution agtrial3 --type entity --id <account MetadataId>` → HTTP 400 `The parameter 'ComponentId' in the request payload is not a valid parameter for the operation 'RemoveSolutionComponent'` (v9.1 expects a `SolutionComponent` entity-reference parameter, not `ComponentId`). **cli-bug (repro in Task 7).** Workaround that succeeded: `action invoke RemoveSolutionComponent --body '{"SolutionComponent":{"solutioncomponentid":"<objectid>","@odata.type":"Microsoft.Dynamics.CRM.solutioncomponent"},"ComponentType":1,"SolutionUniqueName":"agtrial3"}'` → ok, component count back to 0.

---

## TRIAL-4 — SCN-013 webresource iterate loop

**Outcome:** task completed on the exact card path with zero detours: SKILL.md → customizations.md → `webresource create --file --solution` (auto-published) → `webresource update --file` (auto-published, ETag advanced) → content read-back base64-decoded to prove v2. Fastest, cleanest trial so far (12 tool calls, ~95 s).

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 4 | raw entity-set guess | `entity get webresources <id>` → 404 (`Resource not found for the segment 'webresources'`); correct set name is `webresourceset` — recovered on next call | agent-error (irregular D365 entity-set name); CLI 404 envelope clean; one-line skill note ("entity set is `webresourceset`") would prevent it | exit 1, `status: 404`, `code: 0x8006088a`, `category: not_found` |

**Positive observations:** dedicated `webresource` verbs auto-publish (the publish-forgetting pitfall the scenario names never had a chance to occur); ETag used as update evidence; near-clean pass — the skill's customizations.md routing worked exactly as designed.

---

## TRIAL-5 — SCN-023 process inventory, duplicate detection, state control

**Outcome:** inventory + duplicate-detection delivered correctly (159 type-1 definitions: 10 classic workflows, 10 business rules, 129 actions, 10 BPFs; one same-name pair correctly explained as different-entity, not a collision — the jq recipe from automation.md applied via the oversized-output spill file). The card's state-control target (business rule) was blocked server-side; the agent adapted and demonstrated the full deactivate→confirm→activate→confirm round-trip on a classic workflow, restoring original state (controller re-verified: statecode=1).

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 5 | deactivate a business rule | `workflow deactivate <business-rule-id>` → `Cannot update a published workflow definition` (0x80045002) — applies to ALL category-2 business rules on this org (verified on a second rule); classic workflows/BPFs unaffected | cli-behavior/platform — on-prem v9.1 rejects Web API state PATCH for published business rules; **skill-text candidate** (one line: "business rules cannot be deactivated via the Web API on-prem — UI only") + possible CLI enhancement if a supported message exists. ALSO a card-design error (controller picked a business rule as the toggle target) | exit 1 `{"ok": false, "error": "Cannot update a published workflow definition.", "meta": {"status": 400, "code": "0x80045002", "category": "validation"}}` |
| 5 | escalation flailing after the 400 | agent retried with `--bypass-plugins` (denied by the harness permission classifier — would have executed in an unattended run) and `--suppress-dup-detection` (irrelevant flag, same 400) | agent-error (guardrail-escalation reflex: reaching for bypass flags to force a server-rejected write) — meta-finding for unattended-agent safety, the destructive-flag gate earned its keep | `workflow deactivate <id> --bypass-plugins` → permission denied (classifier); `--suppress-dup-detection` → same 0x80045002 |
| 5 | (controller, pre-trial) card precondition wrong | the trial card stated "zero category-0 user workflows"; the org actually has 10. Cause found in Task 7: `--category` is an INTEGER flag and `--category workflow` exits 2 with a clean `Invalid value for '--category': 'workflow' is not a valid integer` — the controller's probe wrapper read `data`/`meta.count` without checking `ok:` and printed 0, manufacturing a phantom cli-bug | controller-error (envelope parsing — reclassified from cli-bug in Task 7; the CLI behaved correctly). Enhancement idea only: accept friendly names on `--category` | `workflow list --category workflow` → exit 2 usage error; `workflow list --category 0` → 10 rows |

**Positive observations:** `--dry-run` used before every state mutation; reversible target chosen after the blocker, original state restored and re-verified; the oversized JSON output (159 rows → spill file) was handled by jq-ing the spill path the harness returned — no context blowup; duplicate analysis distinguished same-name-different-entity from true collisions.

---

## TRIAL-6 — SCN-041 bulk load on v9.1 (on-prem quirk probe)

**Outcome:** **clean pass** — fastest trial (6 tool calls, ~58 s). SKILL.md → records.md → generated JSONL → `data import contacts <file>` (single `$batch`, 50/50 imported, 0 failed) → server-side count verification via filtered OData query. The quirk probe answered decisively: the agent explicitly noted "`CreateMultiple` is cloud-only and not available on v9.1" and never attempted it — the skill's on-prem guidance steered correctly.

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 6 | — | clean pass | — | `data import contacts /tmp/agtrial6_contacts.jsonl` → `{"imported": 50, "failed": 0, "chunks": 1}`; `query odata contacts --filter "lastname eq 'AgTrial6'"` → `meta.count: 50` |

**Cleanup note (controller, TRIAL-6):** batch-deleting the 50 contacts surfaced an error-clarity finding: a batch op `url` written with a leading slash (`/contacts(<id>)`) is sent as-is, the server resolves it against the host root and 404s — and the CLI's `error` field is the raw multipart `--batchresponse…` blob rather than the parsed inner message ("No HTTP resource was found … 'http://host/contacts(…)'"). With bare relative URLs (`contacts(<id>)`) the same file deleted 50/50 (all 204). cli-behavior: (a) normalize/lstrip the leading slash or reject it client-side; (b) parse the first inner error out of the multipart response. Read-back: 0 AgTrial6 contacts remain.

---

## TRIAL-7 — SCN-044/045 validation, verify-after-write, idempotent retry

**Outcome:** task completed; all four behaviors demonstrated (pre-flight rejection, exact-value verify, double-run with no duplicate via `entity update --allow-create` PATCH upsert on a fixed GUID, delete + 404 read-back). Controller re-verified zero `AgTrial7 Co` accounts remain.

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 7 | bad-field create without `--validate` | the plain create hit the server and returned the cryptic OData error `Does not support untyped value in non-open type` (plus a .NET stack fragment); only the follow-up with `--validate` produced the clean `Unknown field(s) for accounts: telephoneone` + `did_you_mean` envelope | task-design (the card asked for exactly this probe) — but the contrast is the finding: **unvalidated writes get raw server noise; `--validate` is opt-in.** Skill-text candidate: make validate-first the stated default for unattended writes | `entity create accounts --data '{…"telephoneone"…}'` → 400 `Does not support untyped value in non-open type`; same + `--validate` → `unknown_fields: ["telephoneone"], did_you_mean: {"telephoneone": "telephone3"}` |
| 7 | `did_you_mean` quality | suggestion for `telephoneone` was `telephone3`; the intended field is `telephone1` — agent had to confirm via `metadata describe account` | cli-behavior (nit: suggestion ranking; lexically close ≠ semantically right) | `did_you_mean: {"telephoneone": "telephone3"}` |
| 7 | logical-name vs entity-set confusion | `metadata describe accounts` (set name) → 404 `LogicalName='accounts' does not exist`; needed singular `account`. Mirror image of TRIAL-4's `webresources`/`webresourceset` guess | agent-error (recurring class: set-vs-logical naming); cli-behavior nit — metadata 404 could hint at the singular form the same way `--validate` does for fields | `metadata describe accounts` → 404 `EntityMetadata With Id = LogicalName='accounts' does not exist` |
| 7 | `metadata describe --select` guess | flag doesn't exist; recovered via `--help` | agent-error (minor) | `No such option '--select'.` |

---

## TRIAL-8 — SCN-053 diagnose a failed solution import (skill-gap probe)

**Construction (controller, recorded per card):** `metadata create-entity cwx_GhostParent` (default solution) → `create-entity cwx_GhostChild --solution agtrial8` → `add-attribute cwx_ghostchild --kind lookup --target-entity cwx_ghostparent --solution agtrial8` → `solution export agtrial8 -o /tmp/agtrial8-broken.zip` (all ok) → `delete-entity cwx_ghostchild` → `delete-entity cwx_ghostparent` (both ok, read-backs 404).

**Outcome: the trial premise collapsed — and that itself produced the two strongest findings of the run.**

1. **Controller construction error:** on-prem `solution export` auto-included the required `cwx_ghostparent` entity in the zip even though only the child was an explicit `agtrial8` component. The archive was therefore self-contained — importing it *succeeded* and recreated both entities. There was no missing dependency to diagnose. (Lesson recorded for the report: building a genuinely failing zip requires post-export surgery, done under controlled conditions in Task 7.)
2. **Agent-error (severe, the trial's headline):** the agent never noticed the premise was false. `solution import … --yes` returned `ok: true, status: succeeded` — instead of reporting "this zip imports fine; the task premise doesn't hold," the agent ran `solution validate --against-org`, saw 20 `guid-collision` findings (systemforms/savedqueries that **its own import had just created**), and constructed a confident, internally-coherent, **wrong** root-cause story ("GUID collision … import silently failed"), complete with a fabricated mechanism and three remediation paths. A user following that diagnosis would have regenerated GUIDs or rebuilt a solution for no reason.
3. **Skill-gap probe answer:** the agent never discovered `solution import-result` or `solution job-status` — the exact verbs the scenario needs and the skill never mentions. Its toolkit stayed at `validate` (offline + against-org) + raw entity GETs.
4. **cli-behavior finding:** on this v9.1 org the import job's `data` column came back empty; the CLI surfaced `meta.warnings: ["import job data column was empty; per-component results not verified"]` on a *succeeded* import. Honest warning (good), but it means per-component import evidence is already degraded on-prem — exactly where SCN-053 needs it. Task 7 should probe what `import-result` shows for a genuinely failed import.

| trial | step | what-went-wrong | root-cause | evidence (command + envelope) |
|---|---|---|---|---|
| 8 | (controller) zip construction | export auto-included the required parent entity → zip self-contained → premise "fails to import" false | controller-error (export includes required components on-prem) | `solution export agtrial8` → zip's customizations.xml defines BOTH `cwx_ghostchild` and `cwx_ghostparent` |
| 8 | premise check | import returned `ok: true, status: succeeded` and the agent did not stop to reconcile that against "this file fails to import" | agent-error (no premise-contradiction check before diagnosing) | `solution import /tmp/agtrial8-broken.zip --yes` → `ok: true`, `meta.warnings: ["import job data column was empty; per-component results not verified"]` |
| 8 | root-cause fabrication | 20 `guid-collision` findings from `validate --against-org` (artifacts of its own just-completed import) presented as the cause of a failure that never happened | agent-error (plausible-but-wrong causal story; collisions were consequence, not cause) | `solution validate --against-org` → 20 error-severity `guid-collision`; live GETs found the colliding rows — created seconds earlier by the import |
| 8 | diagnosis verbs | `import-result` / `job-status` never found or used | skill-text (absent from the skill; probe confirms agents don't discover them) | command inventory: validate ×2, import ×1, entity get ×~6 — no import-result/job-status |
| 8 | importjob evidence on-prem | import job `data` column empty on v9.1 → per-component results unavailable even on success | cli-behavior (warning is honest; evidence hole for SCN-053 on-prem — probe in Task 7) | `meta.warnings: ["import job data column was empty; per-component results not verified"]` |

**Cleanup (controller):** re-imported ghosts deleted again (child → parent, both ok, agtrial8 components = 0); `/tmp/agtrial8-broken.zip` retained for Task 7 repro work.

---

## Bug repros (Task 7 — minimal, against crmworx, CLI v2.4.0, D365 on-prem v9.1)

### BUG-1 — `metadata get-optionset` unusable against v9.1

```bash
crm --json --profile crmworx metadata create-optionset --name cwx_repro1 --display "Repro One" --option ":A" --option ":B"
# → ok:true, created
crm --json --profile crmworx metadata get-optionset cwx_repro1
# → ok:false, exit 1:
#   "Could not find a property named 'Options' on type 'Microsoft.Dynamics.CRM.OptionSetMetadataBase'"
#   meta: {status: 400, code: "0x0", category: validation}
crm --json --profile crmworx metadata list-optionsets        # → ok, row present (MetadataId e15e9a8c…)
crm --json --profile crmworx entity get GlobalOptionSetDefinitions <MetadataId>
# → ok:true, Options: 2  ← generic path retrieves the same optionset fine
```

**Observed:** the dedicated read verb 400s on every global optionset on this v9.1 org (reproduced twice: TRIAL-2 and here); `list-optionsets` and the generic `entity get GlobalOptionSetDefinitions <guid>` both work.
**Expected:** `get-optionset <name>` returns the optionset — likely needs the derived-type cast (`…/Microsoft.Dynamics.CRM.OptionSetMetadata?$expand=…`) or the same request shape `list-optionsets` uses, instead of selecting `Options` on the `OptionSetMetadataBase` base type, which v9.1 rejects.
(Cleanup: `delete-optionset cwx_repro1` → ok.)

### BUG-2 — malformed `--data` JSON crashes with a raw traceback (envelope contract violation)

```bash
crm --json --profile crmworx entity create accounts --data '{"name": "x", bad}'
# → exit 1, NO JSON envelope; raw output ends:
#   json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 15 (char 14)
#   [PYI-73711:ERROR] Failed to execute script '__main__' due to unhandled exception!
```

**Observed:** client-side parse failure in `_load_payload` (crm/commands/_helpers.py:317, reached from `entity_update`/`entity_create`) is unhandled — stack trace + PyInstaller error instead of the documented `{"ok": false, …}` envelope. First hit live in TRIAL-3 (agent shell-interpolated FetchXML into `--data`).
**Expected:** `{"ok": false, "error": "invalid JSON in --data: Expecting property name … (char 14)", "meta": {"category": "validation"}}`, exit 1 — same treatment the OSError file-read wrapping already gets.

### BUG-3 — `solution remove-component` sends a parameter v9.1 rejects (add/remove asymmetry)

```bash
crm --json --profile crmworx solution add-component --solution agtrial8 --type entity --id <account MetadataId>
# → ok:true (AddSolutionComponent accepts ComponentId)
crm --json --profile crmworx solution remove-component --solution agtrial8 --type entity --id <account MetadataId> --yes
# → ok:false, exit 1, meta.status 400:
#   "The parameter 'ComponentId' in the request payload is not a valid parameter for the operation 'RemoveSolutionComponent'"
```

**Observed:** `AddSolutionComponent` takes `ComponentId`, but `RemoveSolutionComponent` on v9.1 does not — the CLI mirrors the add-shape for remove and every remove fails. Reproduced twice (TRIAL-3 cleanup, here).
**Workaround (verified):** `action invoke RemoveSolutionComponent --body '{"SolutionComponent":{"solutioncomponentid":"<objectid>","@odata.type":"Microsoft.Dynamics.CRM.solutioncomponent"},"ComponentType":<n>,"SolutionUniqueName":"<name>"}'` → ok.
**Expected:** `remove-component` sends the `SolutionComponent` entity-reference shape (as the workaround does).
**Side observation:** `add-component --type entity` also pulled in 9 required sub-components (server `AddRequiredComponents` behavior) — worth a `meta.note` or flag so callers know the solution gained more than one component.

### NOT-A-BUG — `workflow list --category <friendly-name>` (reclassified)

```bash
crm --json --profile crmworx workflow list --category workflow   # → exit 2, clean usage error:
# "Invalid value for '--category': 'workflow' is not a valid integer."
crm --json --profile crmworx workflow list --category 0          # → ok, 10 rows
```

**Resolution:** the CLI behaves correctly (`--category` is documented INTEGER). The trial-time "0 rows" came from controller probe wrappers reading `data`/`meta.count` without checking `ok:` — a phantom bug manufactured by bad envelope parsing. Kept here as a methodology lesson; the only product idea it yields is an enhancement (accept friendly names like `workflow|bpf|action`).

### PROBE — failed/missing-dependency imports are silently swallowed on v9.1 (SCN-053 evidence hole)

Construction: took the TRIAL-8 export (child entity + lookup to `cwx_ghostparent`), surgically removed the parent `<Entity>` block from `customizations.xml` and its `<RootComponent>` from `solution.xml` (ElementTree, balanced), so the archive genuinely lacks a dependency that also doesn't exist on the org.

```bash
crm --json --profile crmworx solution import /tmp/agtrial8-truly-broken.zip --yes
# → ok:true, data: {import_job_id: null, async_operation_id: …, status: "succeeded", progress: 100.0}
#   meta.warnings: ["import job data column was empty; per-component results not verified"]
```

**Observed:**
1. The import **reports success**. The org afterwards has `cwx_ghostchild` *including* the lookup attribute `cwx_ghostparentref` — while `cwx_ghostparent` does not exist. No error, no failure surface.
2. `query odata importjobs` shows **no ImportJob row at all** for any of this session's imports (only the org's original April system-solution installs) — the CLI's async import path never creates one, which is why `import_job_id` is `null` and why `solution import-result <id>` (requires an id) is structurally unusable after CLI imports on this org.
3. A malformed-XML variant of the same zip DID fail loudly (async statuscode=31 with the full XSD error inlined, `import_job_id=None`) — so hard schema errors surface, but dependency/semantic problems do not. Envelope nit: that failure put the async statuscode `31` in `meta.status`, which otherwise carries HTTP statuses.

**Implication (feeds recommendations):** on-prem import verification cannot rely on the import envelope. The CLI should (a) pass/generate an `ImportJobId` on import so `import-result` has something to fetch, and (b) the skill should teach a post-import drift/read-back verification (`solution components` + targeted `metadata entity` checks) as the on-prem substitute.
(Cleanup: ghost child deleted, agtrial8 components = 0, both repro zips removed.)
