# Skill Trial Plan — June 2026

> **Formalized (2026-06-25, #571):** all eight trials below are now machine-runnable
> task specs under `evals/skill/tasks/` (TRIAL-3 → `customizations-view-edit`,
> TRIAL-7 → `records-validate-write`; the other six keep the `trial-` prefix). Run the
> whole set with `python -m evals.skill.set_runner`. This document remains the design
> record of the original manual trials.

**Date:** 2026-06-10
**CLI under trial:** `crm` v2.4.0 (release binary, SHA256-verified install; `~/.local/bin/crm`)
**Skill under trial:** the bundled skill shipped inside the v2.4.0 binary, installed via `crm skill install --target claude --force` to `~/.claude/skills/crm/` — verified byte-identical to the repo tree at the assessed commit (`diff -rq` → `SKILL-IN-SYNC`).
**Org:** `crmworx` profile — local on-prem D365 CE v9.1 test environment (full-mutation sandbox). `crm --json --profile crmworx connection whoami` → `ok: true`, expected host + OrganizationId confirmed before trials.
**Trial protocol:** fresh subagent per trial; agent sees ONLY the installed skill + the `crm` binary on PATH (no repo access); trials run sequentially; transcript reviewed against the card's expected path; stumbles logged to `2026-06-skill-trial-log.md`.

**Preconditions (created 2026-06-10, controller session):** eight throwaway unmanaged solutions `agtrial1`…`agtrial8`, publisher `crmworx` (prefix `cwx`), via:

```bash
crm --json --profile crmworx solution create --name agtrial<N> --display "Agent Trial <N>" \
  --publisher crmworx --if-exists skip --no-set-default
```

All eight returned `ok: true, created: true`, version `1.0.0.0`, publisherid `663136c5…` (solutionids `0786b21c…`, `0986b21c…`, `0b86b21c…`, `0d86b21c…`, `0f86b21c…`, `1186b21c…`, `1386b21c…`, `9c2d4323…`).

---

## TRIAL-1 — SCN-005 + SCN-002 + SCN-032 + SCN-029: full customization workflow

**User story:** As a customizer, I want to stand up a new tracked table with fields and a view, packaged in a solution and exported, so that the customization can move to another environment.
**Task given to agent (verbatim):** "Create a new custom table for tracking equipment loans, inside solution `agtrial1` on profile `crmworx`: it needs a name, a borrower (plain text is fine), a loan date, a return-due date, and a status choice with options Out / Returned / Overdue. Add a view showing loans with name, borrower, and return-due date. Then export solution `agtrial1` as an unmanaged zip to `/tmp/agtrial1.zip` and confirm the new table actually made it into the zip."
**Preconditions:** solution `agtrial1` exists (empty).
**Expected competent path:** `metadata create-entity` (or `scaffold table`) with `--solution agtrial1` → `metadata add-attribute` ×4 (string, datetime ×2, local optionset/picklist) → `view create` → `solution export agtrial1 -o /tmp/agtrial1.zip` → inspect zip (`unzip -l` / read customizations.xml) for the `cwx_` entity.
**Cleanup:** `crm --json --profile crmworx entity delete savedqueries <view-guid>` (if the view is not removed with the entity); `crm --json --profile crmworx metadata delete-entity <cwx_logicalname>`; `rm -f /tmp/agtrial1.zip`. Verify: entity gone via `metadata entity <name>` read-back (expect error), `solution components agtrial1` empty.
**Gated verbs expected:** `metadata delete-entity`, `entity delete` (cleanup only — user presence needed).

---

## TRIAL-2 — SCN-003: global option-set lifecycle

**User story:** As a customizer, I want to create and then evolve a global choice list without touching the UI, so that pick-list maintenance is scriptable.
**Task given to agent (verbatim):** "On profile `crmworx`, working in solution `agtrial2`: create a global choice (option set) for 'maintenance priority' with options Low, Medium, High. Then evolve it: add a 'Critical' option at the top, rename 'Medium' to 'Standard', and make the final order Critical, High, Standard, Low. Prove the final state is correct."
**Preconditions:** solution `agtrial2` exists (empty).
**Expected competent path:** `metadata create-optionset --solution agtrial2` → `metadata update-optionset --insert-option … --update-option … --reorder …` → `metadata get-optionset` read-back to verify order/labels.
**Cleanup:** `crm --json --profile crmworx metadata delete-optionset <cwx_name>`; verify via `metadata get-optionset` (expect error).
**Gated verbs expected:** `metadata delete-optionset` (cleanup — user presence needed).

---

## TRIAL-3 — SCN-011: create a view, then edit it (skill-gap probe)

**User story:** As a customizer, I want to adjust an existing saved view's columns and sorting, so that iterating on views doesn't mean delete-and-recreate.
**Task given to agent (verbatim):** "On profile `crmworx`, in solution `agtrial3`: create a view on the account table named 'CWX Trial Accounts' showing account name and main phone, newest accounts first. After it exists, change that same view: add the city column and flip the sort to account name descending — and make sure users would actually see the updated definition."
**Preconditions:** solution `agtrial3` exists (empty).
**Expected competent path:** `view create` → then the **untaught** edit path: `entity update savedqueries <guid>` PATCHing `fetchxml`/`layoutxml` → publish (`solution publish-all` or entity publish) → read-back. The skill teaches view *create* only; this probes whether the agent finds the savedquery PATCH route.
**Cleanup:** `crm --json --profile crmworx entity delete savedqueries <guid>`; verify read-back 404.
**Gated verbs expected:** `entity delete` (cleanup — user presence needed).

---

## TRIAL-4 — SCN-013: JavaScript web resource iterate loop

**User story:** As a pro-code dev, I want to push a JS web resource and ship an updated version, so that form-script iteration is scriptable end to end.
**Task given to agent (verbatim):** "On profile `crmworx`, in solution `agtrial4`: create a JavaScript web resource named `cwx_/agtrial4/hello.js` whose content logs 'hello v1' to the console, make it live, then ship a second version that logs 'hello v2' and prove the server now serves the v2 content."
**Preconditions:** solution `agtrial4` exists (empty).
**Expected competent path:** `webresource create --solution agtrial4` → publish → `webresource update` (new content) → publish → `webresource get` and decode content to confirm v2.
**Cleanup:** `crm --json --profile crmworx entity delete webresourceset <guid>`; verify read-back 404.
**Gated verbs expected:** `entity delete` (cleanup — user presence needed).

---

## TRIAL-5 — SCN-023: process inventory, duplicate detection, reversible state control

**User story:** As a customizer doing a governance review, I want a trustworthy inventory of real process definitions and proof that state changes are safe and reversible.
**Task given to agent (verbatim):** "On profile `crmworx`: produce an inventory of the org's real workflow/process definitions — counts by category and state, definitions only (not activation copies) — and check whether any duplicate definitions share a name. Then demonstrate safe state control: take the business rule named 'Error Code Visibility' offline, confirm it is off, bring it back online, and confirm it is active again."
**Preconditions:** none beyond org state (159 definitions exist; zero category-0 user workflows — expect the inventory to say so).
**Expected competent path:** `workflow list --all` (+ per-category) → duplicate-name detection via the skill's jq recipe → `workflow deactivate <id>` → read-back state → `workflow activate <id>` → read-back. If the agent passes an activation-copy (type=2) GUID anywhere, v2.4.0 auto-resolves to the parent definition with a `meta.note` — log whether that fired.
**Cleanup:** none (state restored within the trial; verify final `statecode=1` read-back).
**Gated verbs expected:** none expected to be gated (`deactivate`/`activate` are reversible state ops — confirm against the gate's verb list at run time).

---

## TRIAL-6 — SCN-041: bulk-load records on v9.1 (on-prem quirk probe)

**User story:** As a pro-code dev seeding test data, I want to load dozens of records as fast as the platform allows, so that environment seeding isn't a manual chore.
**Task given to agent (verbatim):** "On profile `crmworx`: load 50 throwaway contact records — all with last name 'AgTrial6', varied first names — as efficiently as the platform supports, then verify the exact number that landed."
**Preconditions:** solution `agtrial6` exists (unused — data records are not solution components; kept for symmetry).
**Expected competent path:** generate JSONL/CSV locally → `data import contacts <file>` (which batches under the hood) → count verify via `query` with filter `lastname eq 'AgTrial6'`. **Quirk probe:** v9.1 has no `CreateMultiple`; an agent reaching for it (or assuming cloud bulk APIs) should be steered by the skill toward `data import`/`batch`.
**Cleanup:** query the 50 GUIDs → delete via `crm batch` DELETE ops file (or `entity delete` loop); read-back count = 0.
**Gated verbs expected:** `entity delete` / batch deletes (cleanup — user presence needed).

---

## TRIAL-7 — SCN-044 + SCN-045: validation, verify-after-write, idempotent retry

**User story:** As an agent operator, I want bad payloads rejected before they hit the org, writes verified, and retries that don't duplicate, so that unattended record automation is trustworthy.
**Task given to agent (verbatim):** "On profile `crmworx`: you're automating account creation. First try creating an account 'AgTrial7 Co' with a field name you suspect is wrong — use `telephoneone` for the phone — and report exactly what the tooling tells you. Then create it correctly (phone `0100000000`), verify the phone value landed exactly as sent, re-run the same create in a way that cannot produce a duplicate if executed twice, and finally remove the record."
**Preconditions:** solution `agtrial7` exists (unused — records are not solution components).
**Expected competent path:** `entity create --validate` (expect pre-flight rejection + `did_you_mean: telephone1`, no write) → `entity create` → `entity get --expect telephone1=…` → idempotent re-run via `entity upsert` keyed by GUID → `entity delete` → read-back 404.
**Cleanup:** within-trial delete; controller re-verifies `AgTrial7 Co` absent.
**Gated verbs expected:** `entity delete` (user presence needed).

---

## TRIAL-8 — SCN-053: diagnose a failed solution import (skill-gap probe)

**User story:** As a customizer whose import failed, I want the precise failure reason from the CLI, so that I don't have to open the UI import log.
**Task given to agent (verbatim):** "On profile `crmworx`: the file `/tmp/agtrial8-broken.zip` is a solution archive that fails to import. Attempt the import, then produce a precise diagnosis of why it failed — component, reason, and what a fix would be — using only the CLI."
**Preconditions:** solution `agtrial8` exists; controller constructs `/tmp/agtrial8-broken.zip` immediately before the trial with a genuine missing dependency: create a scratch entity `cwx_ghostparent` plus a second entity in `agtrial8` carrying a lookup to it, export `agtrial8` (zip contains the lookup dependency), delete `cwx_ghostparent` from the org, keep the exported zip — importing it now fails with a real `<MissingDependency>` import-job result. Construction commands + envelopes recorded in the trial log at run time.
**Expected competent path:** `solution import /tmp/agtrial8-broken.zip` (fails or returns failed job) → `solution import-result --formatted` / `solution job-status` to extract the failure detail. The skill does not teach `import-result`/`job-status` — probing whether the agent discovers them via `crm describe`/`--help`.
**Cleanup:** none on failure; verify `solution list` shows no new solution and `agtrial8` unchanged; `rm -f /tmp/agtrial8-broken.zip`.
**Gated verbs expected:** none (import of a failing zip; no deletes).

---

## Spec-constraint check

- 8 trials, all from class `covered` or `skill-gap` (TRIAL-3, TRIAL-8 are the two deliberate skill-gap probes; majority `covered`) ✓ — 7 of 8 from the matrix shortlist; TRIAL-6 (SCN-041, class `covered`) is off-shortlist because no shortlist row exercises bulk data, which the spec's on-prem-quirk constraint requires ✓
- ≥1 on-prem quirk exercised: TRIAL-6 (no `CreateMultiple` on v9.1); entire org is v9.1 ✓
- ≥1 full customization workflow: TRIAL-1 (table → columns → view → solution → export) ✓
- Cleanup tractable per card; gated verbs flagged per card ✓

---

## Results (appended after Task 6)

| trial | outcome | headline |
|---|---|---|
| TRIAL-1 | completed | took the `apply` declarative route; 5 transcript stumbles, all recovered |
| TRIAL-2 | completed | `metadata get-optionset` broken on v9.1 (cli-bug); mutation path exact |
| TRIAL-3 | completed | found the untaught savedquery-PATCH edit route; `--data` JSON crash (cli-bug) |
| TRIAL-4 | completed | near-clean pass; webresource verbs auto-publish |
| TRIAL-5 | completed (adapted) | business rules un-deactivatable via Web API on-prem; `--category` filter cli-bug invalidated this card's precondition |
| TRIAL-6 | **clean pass** | `data import` chosen, `CreateMultiple` explicitly rejected — quirk probe passed |
| TRIAL-7 | completed | validate/`--expect`/upsert/delete loop all demonstrated; `did_you_mean` quality nit |
| TRIAL-8 | failed (substantively) | premise false (controller construction error) and agent fabricated a wrong diagnosis instead of detecting it; `import-result`/`job-status` never discovered |

Full stumble tables: `2026-06-skill-trial-log.md`.

---

## Cleanup confirmation (Task 10, 2026-06-10)

Org sweep on `crmworx` after filing the backlog:

- All 8 `agtrial*` solutions verified **component-free** (`solution components` count 0 each), then deleted via `entity delete solutions <solutionid>`; read-back filter `startswith(uniquename,'agtrial')` returns **0 rows**.
- No trial-created stragglers: `savedqueries`, `userqueries`, `webresourceset`, `workflows`, `accounts`, `contacts` all return 0 rows for trial-name filters; trial entities (`cwx_ghostchild`/`cwx_ghostparent`), optionsets (`cwx_maintenancepriority`, `cwx_repro1`) confirmed absent from metadata.
- Kept intentionally: the pre-existing `crmworx` demo schema (`cwx_ticket`/`cwx_sla` family + its optionsets, referenced by `docs/guides/crmworx-walkthrough.md` and the how-to docs) and the `crmworx` publisher.
