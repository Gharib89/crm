# CRMWorx Guide — Plan 2: Live CRMWorx Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to drive this plan in the current session (it is interactive and server-coupled — not subagent-friendly). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the CRMWorx ticketing + SLA customization live against the D365 server, capturing each real command and its output into `docs/guides/crmworx-walkthrough.md`, fixing small CLI defects inline and filing larger ones as issues.

**Architecture:** A driven runbook. Every mutating step is previewed with `--dry-run`, executed with `--json`, and its real output transcribed into the walkthrough. Metadata creates are idempotent (`--if-exists skip`) and target the `CRMWorx` solution / `cwx` prefix via the profile set in Task 0. Destructive verbs require `--yes` and explicit user confirmation. A capability-coverage table is assembled at the end.

**Tech Stack:** `crm` CLI, Dataverse Web API, D365 CE on-prem 9.x over VPN.

**Prerequisites (hard):**
- Plan 1 merged (walkthrough stub exists).
- VPN connected; `D365_URL` / `D365_USERNAME` / `D365_PASSWORD` / `D365_DOMAIN` exported in this session.
- **The `CRMWorx` unmanaged solution and its publisher (prefix `cwx`) exist on the server.** The CLI has no `solution create` / `publisher create` verb today (see Task 0 — this gap is filed as an enhancement issue). Create them once in the D365 web UI (Settings → Solutions / Publishers) before running, or via an imported empty solution.

---

## Known gaps to file as issues at the start

### Task 0: Establish connection and record the solution-create gap

**Files:**
- None (server + issue tracker)

- [ ] **Step 1: Confirm reachability and identity**

Run:
```bash
crm --json connection whoami
```
Expected: `{"ok": true, "data": {"UserId": "...", ...}}`. If exit ≠ 0, fix credentials before continuing (this is the bug loop's first gate — a `401` means `D365_DOMAIN\D365_USERNAME` is wrong).

- [ ] **Step 2: File the `solution create` / `publisher create` enhancement issue**

The walkthrough needs a solution + publisher but the CLI cannot create them. This is larger than a one-function fix → log it (hybrid policy). Use the issue-tracker skill / `gh`:

```bash
gh issue create --repo Gharib89/crm \
  --title "feat(solution): add 'solution create' and 'publisher create' verbs" \
  --label needs-triage \
  --body "$(cat <<'EOF'
The CRMWorx walkthrough requires a custom solution + publisher (prefix cwx) to
target metadata creates. The CLI has no way to create either — only list/info/
components/export/import/publish. Today this must be done in the web UI.

Proposed: `crm solution create --name CRMWorx --display "CRMWorx" --publisher <p>`
and `crm metadata create-publisher --name crmworx --prefix cwx`.

Workaround in the guide: create both in the D365 web UI as a documented prerequisite.
EOF
)"
```

- [ ] **Step 3: Save the targeting profile**

```bash
crm --json connection connect \
  --url "$D365_URL" --username "$D365_USERNAME" --domain "$D365_DOMAIN" \
  --default-solution CRMWorx --publisher-prefix cwx \
  --profile-name crmworx
crm --json connection profiles
```
Expected: profiles output shows `solution=CRMWorx prefix=cwx` for the `crmworx` profile.

- [ ] **Step 4: Transcribe pre-flight into the walkthrough**

Replace the "Pre-flight & connection" placeholder in `docs/guides/crmworx-walkthrough.md` with the real `whoami` + `profiles` commands and their captured (credential-redacted) output, plus the prerequisite note about creating the solution/publisher in the UI.

- [ ] **Step 5: Commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): transcribe pre-flight + connection step"
```

### Task 1: Create the four global option sets

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`

- [ ] **Step 1: Dry-run the first option set**

```bash
crm --json --dry-run metadata create-optionset \
  --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical \
  --if-exists skip
```
Expected: a previewed POST to the `GlobalOptionSetDefinitions` endpoint. If the request shape looks wrong, that is a bug → triage (small/large per hybrid).

- [ ] **Step 2: Create all four option sets**

```bash
crm --json metadata create-optionset --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical --if-exists skip
crm --json metadata create-optionset --name cwx_severity --display "CRMWorx Severity" \
  --option 1:Minor --option 2:Major --option 3:Critical --if-exists skip
crm --json metadata create-optionset --name cwx_ticketcategory --display "CRMWorx Category" \
  --option 1:Hardware --option 2:Software --option 3:Network --option 4:Access --if-exists skip
crm --json metadata create-optionset --name cwx_slatier --display "CRMWorx SLA Tier" \
  --option 1:Bronze --option 2:Silver --option 3:Gold --if-exists skip
```
Expected: each `{"ok": true}`. Re-running must report skip/no-op (idempotency check happens in Task 7).

- [ ] **Step 3: Verify**

```bash
crm --json metadata list-optionsets --custom-only | grep -o 'cwx_[a-z]*'
```
Expected: all four names present.

- [ ] **Step 4: Transcribe + commit**

Replace the option-set portion of the "Metadata build" section with the real commands/output.
```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): transcribe option-set creation"
```

### Task 2: Create the two custom entities

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`

- [ ] **Step 1: Create `cwx_sla`**

```bash
crm --json metadata create-entity \
  --schema-name cwx_SLA --display "SLA Policy" --display-collection "SLA Policies" \
  --primary-attr cwx_Name --primary-label "Policy Name" \
  --ownership OrganizationOwned --has-notes --if-exists skip
```
Expected: `{"ok": true}` with the created entity metadata id.

- [ ] **Step 2: Create `cwx_ticket`**

```bash
crm --json metadata create-entity \
  --schema-name cwx_Ticket --display "Support Ticket" --display-collection "Support Tickets" \
  --primary-attr cwx_Name --primary-label "Ticket Title" \
  --ownership UserOwned --has-notes --has-activities --if-exists skip
```
Expected: `{"ok": true}`.

- [ ] **Step 3: Verify**

```bash
crm --json metadata entities --custom-only | grep -oE 'cwx_(sla|ticket)'
```
Expected: both logical names present.

- [ ] **Step 4: Transcribe + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): transcribe entity creation"
```

### Task 3: Add attributes to both entities (all kinds)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`

- [ ] **Step 1: Add `cwx_sla` attributes**

```bash
crm --json metadata add-attribute cwx_sla --kind integer \
  --schema-name cwx_ResponseHours --display "Response Hours" --min 0 --max 720 --if-exists skip
crm --json metadata add-attribute cwx_sla --kind integer \
  --schema-name cwx_ResolutionHours --display "Resolution Hours" --min 0 --max 2160 --if-exists skip
crm --json metadata add-attribute cwx_sla --kind picklist \
  --schema-name cwx_Tier --display "Tier" --optionset-name cwx_slatier --if-exists skip
crm --json metadata add-attribute cwx_sla --kind boolean \
  --schema-name cwx_Active --display "Active" --true-label Yes --false-label No --if-exists skip
```
Expected: each `{"ok": true}`.

- [ ] **Step 2: Add `cwx_ticket` scalar + picklist attributes**

```bash
crm --json metadata add-attribute cwx_ticket --kind memo \
  --schema-name cwx_Description --display "Description" --max-length 4000 --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Priority --display "Priority" --optionset-name cwx_priority --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Severity --display "Severity" --optionset-name cwx_severity --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Category --display "Category" --optionset-name cwx_ticketcategory --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_OpenedOn --display "Opened On" --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_ResolvedOn --display "Resolved On" --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_DueBy --display "Due By" --if-exists skip
```
Expected: each `{"ok": true}`. (Lookups `cwx_customerid` and `cwx_sla` are created by the relationships in Task 4, not here.)

- [ ] **Step 3: Verify a representative attribute**

```bash
crm --json metadata attribute cwx_ticket cwx_priority | grep -i optionset
```
Expected: the picklist binds to the `cwx_priority` global option set.

- [ ] **Step 4: Transcribe + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): transcribe attribute creation (all kinds)"
```

### Task 4: Create relationships (1:N + 1:N + N:N)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`

- [ ] **Step 1: SLA → Ticket (1:N, creates the `cwx_sla` lookup on ticket)**

```bash
crm --json metadata create-one-to-many \
  --schema-name cwx_sla_cwx_ticket \
  --referenced-entity cwx_sla --referencing-entity cwx_ticket \
  --lookup-schema cwx_SLA --lookup-display "SLA Policy" \
  --if-exists skip
```
Expected: `{"ok": true}`.

- [ ] **Step 2: Account → Ticket (1:N, creates the `cwx_customerid` lookup on ticket)**

```bash
crm --json metadata create-one-to-many \
  --schema-name cwx_account_cwx_ticket \
  --referenced-entity account --referencing-entity cwx_ticket \
  --lookup-schema cwx_CustomerId --lookup-display "Customer" \
  --if-exists skip
```
Expected: `{"ok": true}`.

- [ ] **Step 3: Ticket ↔ SystemUser (N:N watchers)**

```bash
crm --json metadata create-many-to-many \
  --schema-name cwx_ticket_systemuser \
  --entity1 cwx_ticket --entity2 systemuser \
  --intersect-entity cwx_ticket_systemuser \
  --if-exists skip
```
Expected: `{"ok": true}`.

- [ ] **Step 4: Verify relationships + publish**

```bash
crm --json metadata relationships cwx_ticket | grep -oE 'cwx_(sla|account|ticket)_[a-z_]+'
crm --json solution publish-all
```
Expected: all three relationship schema names present; publish `{"ok": true}`.

- [ ] **Step 5: Transcribe + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): transcribe relationship creation + publish"
```

### Task 5: Seed data (SLA policies, accounts, tickets)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`

- [ ] **Step 1: Create two SLA policies, capture their GUIDs**

```bash
crm --json entity create cwx_slas --data '{"cwx_name":"Gold 4h/24h","cwx_responsehours":4,"cwx_resolutionhours":24,"cwx_tier":3,"cwx_active":true}'
crm --json entity create cwx_slas --data '{"cwx_name":"Bronze 24h/120h","cwx_responsehours":24,"cwx_resolutionhours":120,"cwx_tier":1,"cwx_active":true}'
```
Expected: each returns `{"ok": true, "data": {"cwx_slaid": "<guid>"}}`. Record the GUIDs.
(The entity set name is the plural of the logical name — confirm via `crm --json service-document | grep cwx`. If the pluralisation differs, that is the value to use.)

- [ ] **Step 2: Create a customer account, capture its GUID**

```bash
crm --json entity create accounts --data '{"name":"Contoso IT Dept"}'
```
Expected: `{"ok": true, "data": {"accountid": "<guid>"}}`.

- [ ] **Step 3: Create tickets binding the lookups (use `@odata.bind`)**

```bash
crm --json entity create cwx_tickets --data '{
  "cwx_name":"Laptop won'\''t boot",
  "cwx_description":"Dell 5420 no POST after update",
  "cwx_priority":3, "cwx_severity":2, "cwx_category":1,
  "cwx_customerid_account@odata.bind":"/accounts(<ACCOUNT_GUID>)",
  "cwx_sla@odata.bind":"/cwx_slas(<GOLD_SLA_GUID>)"
}'
```
Expected: `{"ok": true}`. Substitute the GUIDs from Steps 1–2.
(If the `@odata.bind` navigation property name is rejected, read the real name from
`crm --json metadata attribute cwx_ticket cwx_customerid` — note the bug loop trigger.)

- [ ] **Step 4: Demonstrate `update` and `upsert`**

```bash
crm --json entity update cwx_tickets <TICKET_GUID> --data '{"cwx_resolvedon":"2026-06-01T12:00:00Z"}'
```
Expected: `{"ok": true}`. Pick one record to show `upsert` with an alternate key if one is configured; otherwise note upsert by id.

- [ ] **Step 5: Transcribe + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): transcribe data seeding (create/update/upsert)"
```

### Task 6: Read & verify (odata, fetchxml, count, csv, action)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`
- Create: `docs/artifacts/crmworx-tickets.csv` (committed sample export)

- [ ] **Step 1: OData query**

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --top 10
```
Expected: the high-priority ticket(s).

- [ ] **Step 2: FetchXML query**

```bash
crm --json query fetchxml cwx_tickets --xml '
<fetch top="20">
  <entity name="cwx_ticket">
    <attribute name="cwx_name"/>
    <attribute name="cwx_priority"/>
    <order attribute="cwx_name"/>
  </entity>
</fetch>'
```
Expected: `{"ok": true}` with rows.

- [ ] **Step 3: Bulk CSV export (committed artifact)**

```bash
crm data export cwx_tickets -o docs/artifacts/crmworx-tickets.csv \
  --select cwx_name,cwx_priority,cwx_severity,cwx_category
```
Expected: CSV written.

- [ ] **Step 4: Action call**

```bash
crm --json action function RetrieveCurrentOrganization --params '{"AccessType":"Default"}'
```
Expected: `{"ok": true}` with org info.

- [ ] **Step 5: Transcribe + commit**

```bash
git add docs/guides/crmworx-walkthrough.md docs/artifacts/crmworx-tickets.csv
git commit -m "docs(crmworx): transcribe read/verify + commit CSV sample"
```

### Task 7: Idempotency check + package the solution

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`
- Create: `docs/artifacts/crmworx.zip` (committed solution export)

- [ ] **Step 1: Re-run the metadata creates to prove idempotency**

Re-run the Task 1 option-set and Task 2 entity commands verbatim.
Expected: every one reports a skip / no-op (because of `--if-exists skip`), exit 0, no duplicate created. If any command errors or duplicates, that is a defect → bug loop.

- [ ] **Step 2: Export the solution**

```bash
crm solution export CRMWorx -o docs/artifacts/crmworx.zip
```
Expected: `{"output": ".../crmworx.zip", "bytes": <n>, "managed": false}`.

- [ ] **Step 3: Verify the solution contents**

```bash
crm --json solution components CRMWorx | grep -oiE 'cwx_(sla|ticket|priority|severity|ticketcategory|slatier)'
```
Expected: both entities and all four option sets appear as solution components.

- [ ] **Step 4: Transcribe + commit**

```bash
git add docs/guides/crmworx-walkthrough.md docs/artifacts/crmworx.zip
git commit -m "docs(crmworx): idempotency check + commit solution export"
```

### Task 8: Capability-coverage table + teardown appendix

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md`

- [ ] **Step 1: Append the coverage table**

Add a table mapping each command group to the step that exercised it:

```markdown
## Capability coverage

| Group | Exercised by |
| --- | --- |
| connection | Pre-flight (whoami, connect, profiles) |
| metadata | Option sets, entities, attributes, relationships, publish |
| entity | Seed data (create/update), lookups via @odata.bind |
| query | OData + FetchXML reads |
| data | CSV export |
| action | RetrieveCurrentOrganization |
| solution | components + export |
| session | (see how-to; session info/history) |
```

If any group is unexercised, add a step that exercises it before finishing.

- [ ] **Step 2: Write the teardown appendix (gated deletes)**

Add a clearly-marked "Teardown (optional)" section with the exact reverse order
(records → relationships → attributes → entities → option sets), each using `--yes`,
e.g.:

```markdown
## Teardown (optional — full reset for a clean replay)

> Destructive. Each command requires `--yes`; the PreToolUse hook blocks them otherwise.

```bash
crm --json metadata delete-entity cwx_ticket --yes   # drops the table + all rows
crm --json metadata delete-entity cwx_sla --yes
crm --json metadata delete-optionset cwx_priority --yes
crm --json metadata delete-optionset cwx_severity --yes
crm --json metadata delete-optionset cwx_ticketcategory --yes
crm --json metadata delete-optionset cwx_slatier --yes
```
```

- [ ] **Step 3: Verify teardown once against the server**

Run the teardown commands (with user confirmation), then:
```bash
crm --json metadata entities --custom-only | grep -c 'cwx_' || true
```
Expected: `0`. Then **re-run Tasks 1–2 to confirm the whole guide replays from clean**. (This validates the "leave deployed + option to full teardown, repeatable" requirement.) Re-deploy after, leaving CRMWorx live as the final state.

- [ ] **Step 4: Build docs strict + commit**

```bash
mkdocs build --strict
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): add coverage table + teardown appendix"
```

## Self-review notes

- Entity-set plural names (`cwx_slas`, `cwx_tickets`) and `@odata.bind` navigation
  property names are **assumptions verified at runtime** (Task 5 notes). Mismatches are
  expected bug-loop triggers, not plan errors.
- Every destructive command carries `--yes` and is gated by `destructive_op_gate.py`.
- Small defects: fix inline with a test in `crm/tests/` and re-run. Larger: file via
  the issue-tracker skill, work around, continue (hybrid policy).
