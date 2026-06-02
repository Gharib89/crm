# CRMWorx interface — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (live-server work; not subagent-parallelizable) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the CRMWorx walkthrough to a fully-working model-driven UI (views, forms, charts, dashboard, BPF, sitemap, model-driven app) built live against D365 CE on-prem 9.1, then promote views + app/sitemap to first-class `crm` CLI commands with tests.

**Architecture:** Two phases on one branch (`docs/crmworx-interface`, cut from `main`). **Phase A** builds every interface artifact *live* using only existing primitives (`crm entity create <set>`, `crm action invoke`, `crm query`, `crm solution publish-all`) with Claude-authored XML, transcribing real commands + output into `docs/guides/crmworx-walkthrough.md`. **Phase B** promotes the small-predictable-XML, high-reuse pieces (views, appmodule, sitemap, AddAppComponents) into new `crm view` / `crm app` command groups with `requests_mock` unit tests, mirroring the established `crm/core/optionsets.py` + `crm/commands/metadata.py` pattern.

**Tech Stack:** Python 3.11+, Click, `requests`/NTLM (`crm.utils.d365_backend.D365Backend`), Dataverse Web API (OData v4) on D365 CE on-prem 9.1, pytest + `requests_mock`, pyright (strict on `crm/core/*`), mkdocs-material (`mkdocs build --strict`).

**Hybrid policy (from Plan 2, carried forward):** Small CLI defects exposed live get fixed inline with a regression test + a walkthrough admonition. Anything the Web API genuinely cannot do on 9.1 (prime suspect: BPF) gets a documented manual-portal fallback + a filed GitHub issue — never a faked "done." Every mutating live step: preview where the verb supports `--dry-run` → execute `--json` → transcribe real output → read-back proof → publish.

**Preconditions (verify before Task A0):**
- On branch cut from `main`: `git checkout main && git pull && git checkout -b docs/crmworx-interface`.
- CRMWorx model from #35 is **deployed** on the server (entities `cwx_ticket`, `cwx_sla`; option sets `cwx_priority`, `cwx_severity`, `cwx_ticketcategory`, `cwx_slatier`; relationships + seed data). If torn down, re-run walkthrough §2–§3 first (every create is idempotent).
- `.env` present with live creds; `crm --json connection whoami` returns ok. Profile has `default_solution=CRMWorx`, `publisher_prefix=cwx`.
- `.venv` active; `pyright` on PATH (`pyright --version` works); `mkdocs` available (`pip install -e '.[docs]'`).

---

## Phase A — build the interface live

> Phase A tasks are **live-server execution + transcription**, not TDD code tasks. There is no unit test to write first — the live 9.1 server is the oracle (as in Plan 2). Each task: author XML → POST via existing primitive → read back → on server error, iterate the XML against the quoted error (consult MS Learn via the `microsoft_docs_search` / `microsoft_docs_fetch` MCP tools) → transcribe the *working* command + real output into the walkthrough. Capture **real** output; never invent it.

### Task A0: Branch + capture the entity ObjectTypeCode

**Files:**
- Modify (workspace only this task): none — setup + fact-gathering.

- [ ] **Step 1: Cut the branch from main**

```bash
git checkout main && git pull --ff-only
git checkout -b docs/crmworx-interface
git branch --show-current   # -> docs/crmworx-interface
```

- [ ] **Step 2: Confirm the model is deployed**

```bash
crm --json metadata entities --custom-only | python -c "import sys,json; d=json.load(sys.stdin)['data']; print(sorted(e['LogicalName'] for e in d if e['LogicalName'].startswith('cwx_')))"
```
Expected: includes `cwx_ticket` and `cwx_sla`. If empty, re-run walkthrough §2–§3 first.

- [ ] **Step 3: Capture ObjectTypeCode for both entities** (LayoutXml `object=` needs the integer OTC)

```bash
crm --json metadata entity cwx_ticket | python -c "import sys,json; d=json.load(sys.stdin)['data']; print('cwx_ticket OTC =', d.get('ObjectTypeCode'))"
crm --json metadata entity cwx_sla    | python -c "import sys,json; d=json.load(sys.stdin)['data']; print('cwx_sla OTC =', d.get('ObjectTypeCode'))"
```
Record both OTC integers — used in every LayoutXml below. (On-prem OTCs for custom entities are typically ≥ 10000 and are **org-specific**; do not hardcode a guess.)

- [ ] **Step 4: Capture the real attribute logical names** (verify the lookup/picklist column names exist before referencing them in FetchXml)

```bash
crm --json metadata attributes cwx_ticket | python -c "import sys,json; d=json.load(sys.stdin)['data']; print(sorted(a['LogicalName'] for a in d if a['LogicalName'].startswith('cwx_') or a['LogicalName'] in ('createdon','statecode','statuscode')))"
```
Expected: confirms `cwx_name`, `cwx_priority`, `cwx_severity`, `cwx_slatier`?, the SLA lookup (`cwx_sla` — verify exact logical name), `cwx_customerid`, `cwx_ticketcategory`. **Use the exact logical names this returns** in all FetchXml; do not assume casing.

- [ ] **Step 5: No commit** (no files changed yet).

---

### Task A1: Custom views (`savedquery`)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (insert new `## 6. Views` after §5 Export, before `## Capability coverage`; later tasks renumber the tail).

**Reference:** savedquery fields — `name`, `returnedtypecode` (entity **logical name** string, e.g. `"cwx_ticket"`), `fetchxml`, `layoutxml`, `querytype` (`0` = public/main view), `isdefault`, `isquickfindquery`. Created via `crm entity create savedqueries --data '<json>'`. Published via `crm solution publish-all`.

- [ ] **Step 1: Author + dry-run the "Active Tickets" public view**

LayoutXml template (substitute the cwx_ticket OTC from A0 into `object="<OTC>"`; substitute real attribute logical names from A0):

```xml
<grid name="resultset" object="<OTC>" jump="cwx_name" select="1" icon="1" preview="1">
  <row name="result" id="cwx_ticketid">
    <cell name="cwx_name" width="220" />
    <cell name="cwx_priority" width="120" />
    <cell name="cwx_severity" width="120" />
    <cell name="cwx_customerid" width="180" />
    <cell name="createdon" width="140" />
    <cell name="statuscode" width="120" />
  </row>
</grid>
```

FetchXml template (no `<order>` on a non-displayed attr; filter to active):

```xml
<fetch version="1.0" output-format="xml-platform" mapping="logical">
  <entity name="cwx_ticket">
    <attribute name="cwx_ticketid" />
    <attribute name="cwx_name" />
    <attribute name="cwx_priority" />
    <attribute name="cwx_severity" />
    <attribute name="cwx_customerid" />
    <attribute name="createdon" />
    <attribute name="statuscode" />
    <order attribute="cwx_name" descending="false" />
    <filter type="and">
      <condition attribute="statecode" operator="eq" value="0" />
    </filter>
  </entity>
</fetch>
```

Build the create payload as a JSON file (avoids shell-quoting the XML). Write `/tmp/cwx_view_active_tickets.json`:

```json
{
  "name": "Active Tickets",
  "returnedtypecode": "cwx_ticket",
  "querytype": 0,
  "isdefault": false,
  "fetchxml": "<fetch ...></fetch>",
  "layoutxml": "<grid ...></grid>"
}
```
(Embed the two XML blocks above as single-line escaped strings.)

Dry-run preview:
```bash
crm --json --dry-run entity create savedqueries --data-file /tmp/cwx_view_active_tickets.json
```
Expected: a dry-run envelope echoing the POST to `savedqueries`. Transcribe it.

- [ ] **Step 2: Idempotency guard — check the view doesn't already exist**

`savedqueries` has no `--if-exists`; guard with a query first:
```bash
crm --json query odata savedqueries --filter "name eq 'Active Tickets' and returnedtypecode eq 'cwx_ticket'" --select name,savedqueryid
```
If it returns a row, skip the create (record the existing `savedqueryid`); else proceed.

- [ ] **Step 3: Execute the create**

```bash
crm --json entity create savedqueries --data-file /tmp/cwx_view_active_tickets.json
```
Expected: `{ "ok": true, "data": { ... "savedqueryid": "<guid>" ... } }`. Capture the GUID. **If the server rejects** (e.g. bad `object=` OTC, unknown attribute, malformed FetchXml) — quote the exact error, fix the XML, retry. Transcribe the *working* command + real success output.

- [ ] **Step 4: Repeat for two more views**

- `cwx_ticket` **"Tickets by Priority"** (public; FetchXml ordered by `cwx_priority`, same active filter; LayoutXml leads with `cwx_priority` then `cwx_name`).
- `cwx_sla` **"Active SLAs"** (`returnedtypecode": "cwx_sla"`, cwx_sla OTC; cells: `cwx_name` + the SLA's tier/target columns confirmed in A0; `statecode=0`).

Same guard → create → capture-GUID flow for each.

- [ ] **Step 5: Publish + read back**

```bash
crm --json solution publish-all
crm --json query odata savedqueries --filter "returnedtypecode eq 'cwx_ticket' and querytype eq 0" --select name,savedqueryid,querytype
```
Expected: lists the two new cwx_ticket public views. Transcribe.

- [ ] **Step 6: Write the walkthrough §6 Views section**

Insert `## 6. Views` (real commands + real output + the LayoutXml/FetchXml that worked). Note the JSON-file approach for XML payloads. If any defect was fixed inline, add an admonition (`!!! note "..."`).

- [ ] **Step 7: Commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §6 custom views (savedquery) — live"
```

---

### Task A2: Forms — clone-and-augment main form + quick-create (`systemform`)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (new `## 7. Forms`).

**Reference:** systemform fields — `objecttypecode` (logical name string), `type` (`2`=Main, `7`=QuickCreate), `formxml`, `name`, `formid`. The auto-generated main form already exists from entity creation; **clone-and-augment** edits its FormXml rather than authoring from scratch (far lower 9.1 rejection rate).

- [ ] **Step 1: Fetch the existing main form**

```bash
crm --json query odata systemforms --filter "objecttypecode eq 'cwx_ticket' and type eq 2" --select name,formid,formxml > /tmp/cwx_ticket_mainform.json
python -c "import json;d=json.load(open('/tmp/cwx_ticket_mainform.json'))['data']['value'];print([(f['name'],f['formid']) for f in d])"
```
Capture `formid` and the current `formxml`. Transcribe the command + the form's identity (not the full FormXml dump — note its length instead).

- [ ] **Step 2: Augment the FormXml**

Inject a `<tab>`/`<section>` (or add a section to the existing first tab) laying out the custom fields: `cwx_priority`, `cwx_severity`, the SLA lookup, `cwx_customerid`, `cwx_ticketcategory`. Keep all existing FormXml intact — only add controls for fields not already present. Each control needs a unique `id` (GUID) and a `<cell>` wrapper; follow the shapes already present in the fetched FormXml (copy an existing `<cell>`/`<control>` block and swap `datafieldname` + `classid` for the field type).

- [ ] **Step 3: PATCH the form back + publish**

Write the augmented form to `/tmp/cwx_ticket_mainform_patch.json` as `{ "formxml": "<augmented xml>" }`:
```bash
crm --json --dry-run entity update systemforms <formid> --data-file /tmp/cwx_ticket_mainform_patch.json   # preview
crm --json entity update systemforms <formid> --data-file /tmp/cwx_ticket_mainform_patch.json
crm --json solution publish-all
```
Expected: update succeeds; publish ok. **If FormXml is rejected** — quote the error (common: missing required attribute, duplicate cell id, invalid classid), fix, retry. This is the highest-iteration step; budget for it. Transcribe the working result.

- [ ] **Step 4: Read back**

```bash
crm --json query odata systemforms --filter "formid eq <formid>" --select name,formxml | python -c "import sys,json;f=json.load(sys.stdin)['data']['value'][0];print('cwx_priority' in f['formxml'], 'cwx_customerid' in f['formxml'])"
```
Expected: `True True` (custom fields now in the FormXml).

- [ ] **Step 5: Create a quick-create form** (`type`: 7)

Author a minimal QuickCreate FormXml (name + priority + customer). Guard (`query odata systemforms --filter "objecttypecode eq 'cwx_ticket' and type eq 7"`), create via `entity create systemforms`, publish, read back. If 9.1 rejects QuickCreate-via-API, apply the hybrid fallback (document manual steps + file an issue) and say so plainly in the guide.

- [ ] **Step 6: Write the walkthrough §7 Forms section + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §7 forms — clone-and-augment main + quick-create — live"
```

---

### Task A3: Chart (`savedqueryvisualization`)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (new `## 8. Charts`).

**Reference:** savedqueryvisualization fields — `name`, `primaryentitytypecode` (logical name string), `datadescription` (a `<datadefinition>` XML with a FetchXML collection containing a groupby + aggregate), `presentationdescription` (a `<Chart>` XML defining series/axes), `savedqueryvisualizationid`.

- [ ] **Step 1: Author the "Tickets by Priority" bar chart**

`datadescription` — FetchXML grouped by `cwx_priority` with a `count` aggregate on `cwx_ticketid`:
```xml
<datadefinition>
  <fetchcollection>
    <fetch aggregate="true" mapping="logical">
      <entity name="cwx_ticket">
        <attribute name="cwx_ticketid" alias="count_col" aggregate="count" />
        <attribute name="cwx_priority" alias="grp_priority" groupby="true" />
      </entity>
    </fetch>
  </fetchcollection>
  <categorycollection>
    <categories>
      <category><measurecollection><measure alias="count_col" /></measurecollection></category>
    </categories>
  </categorycollection>
</datadefinition>
```
`presentationdescription` — a minimal bar `<Chart>` (copy the canonical bar-chart presentation shape from MS Learn `microsoft_docs_search "savedqueryvisualization presentationdescription bar chart"` at execution time; the exact `<Series ChartType="Column">` block is verbose — fetch it rather than guessing).

- [ ] **Step 2: Guard → dry-run → create → publish → read back**

```bash
crm --json query odata savedqueryvisualizations --filter "name eq 'Tickets by Priority' and primaryentitytypecode eq 'cwx_ticket'" --select name,savedqueryvisualizationid
crm --json --dry-run entity create savedqueryvisualizations --data-file /tmp/cwx_chart_priority.json
crm --json entity create savedqueryvisualizations --data-file /tmp/cwx_chart_priority.json
crm --json solution publish-all
crm --json query odata savedqueryvisualizations --filter "primaryentitytypecode eq 'cwx_ticket'" --select name,savedqueryvisualizationid
```
Capture the `savedqueryvisualizationid` (needed by the dashboard in A4). Iterate on any rejection; transcribe the working flow.

- [ ] **Step 3: Write §8 Charts + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §8 'Tickets by Priority' chart — live"
```

---

### Task A4: Dashboard (`systemform` type=Dashboard)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (new `## 9. Dashboard`).

**Reference:** dashboard = a `systemforms` row with `type` = `0` (Dashboard), `formxml` describing a grid of components. Components reference the chart `savedqueryvisualizationid` (A3) and a view `savedqueryid` (A1, the "Active Tickets" view).

- [ ] **Step 1: Author the "CRMWorx SLA Overview" dashboard FormXml**

A 2-cell dashboard layout: one chart component (binds the A3 chart GUID + the A1 "Active Tickets" view GUID as its data source) + one list/grid component (binds the A1 "Active Tickets" view). Fetch the canonical dashboard FormXml shape from MS Learn at execution time (`microsoft_docs_search "model-driven dashboard formxml systemform type 0"`) — the `<Dashboard>`/`<Cell>`/`<Control>` shape is verbose; use the documented template and substitute the two GUIDs.

- [ ] **Step 2: Guard → create → publish → read back**

```bash
crm --json query odata systemforms --filter "name eq 'CRMWorx SLA Overview' and type eq 0" --select name,formid
crm --json entity create systemforms --data-file /tmp/cwx_dashboard.json
crm --json solution publish-all
crm --json query odata systemforms --filter "type eq 0 and name eq 'CRMWorx SLA Overview'" --select name,formid
```
Capture the dashboard `formid` (needed by the app in A6). Iterate on rejection; transcribe.

- [ ] **Step 3: Write §9 Dashboard + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §9 SLA-overview dashboard — live"
```

---

### Task A5: Business process flow (`workflow` category=4) — time-boxed + honest fallback

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (new `## 10. Business process flow`).

**Reference:** BPF = a `workflows` row with `category` = `4` (BusinessProcessFlow), `type` = `1` (Definition), `primaryentity` = `cwx_ticket`, `uniquename` = `cwx_ticketresolution`, plus `clientdata` (JSON describing stages/steps) and an auto-generated entity. This is the **riskiest** artifact on 9.1's Web API.

- [ ] **Step 1: Research the 9.1 BPF-via-Web-API shape**

`microsoft_docs_search "create business process flow workflow web api category 4 clientdata"` and `microsoft_docs_fetch` the most relevant page. Determine whether on-prem 9.1 accepts a `workflows` POST with `clientdata` (stages New → In Progress → Resolved) followed by activate (`statecode`=1, `statuscode`=2), or whether it requires the legacy XAML / portal designer.

- [ ] **Step 2: Time-boxed live attempt (max ~4 iterations)**

Attempt: `entity create workflows --data-file /tmp/cwx_bpf.json` (category 4, type 1, primaryentity cwx_ticket, uniquename cwx_ticketresolution, clientdata with the 3 stages) → activate via `crm workflow activate <id>` (or `entity update workflows <id> --data '{"statecode":1,"statuscode":2}'`) → publish-all → read back (`workflow list --category 4` or `query odata workflows --filter "uniquename eq 'cwx_ticketresolution'"`).

- [ ] **Step 3: Branch on outcome (honest, no faking)**

- **If it works:** transcribe the working command + real output; note the stage definitions.
- **If 9.1's Web API cannot create the BPF after the time-box:** quote the exact blocking error, write §10 documenting the **manual portal steps** (Settings → Processes → New → Business Process Flow → cwx_ticket → stages New/In Progress/Resolved → Activate), and **file a GitHub issue**:
  ```bash
  gh issue create --title "BPF creation via Web API on D365 CE on-prem 9.1" \
    --body "<exact error + what was attempted + the manual fallback documented in the walkthrough §10>"
  ```
  State plainly in the guide which path was taken. **Do not** claim the BPF was created via CLI if it wasn't.

- [ ] **Step 4: Commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §10 ticket-resolution BPF — live (or documented fallback)"
```

---

### Task A6: Sitemap + model-driven app (`appmodule` + `sitemap` + `AddAppComponents`)

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (new `## 11. Sitemap & model-driven app`).

**Reference:** `appmodules` fields — `name`, `uniquename` (`cwx_crmworx`), `description`, `clienttype` (UCI), `navigationtype`, `webresourceid`?, `appmoduleid`. `sitemaps` — `sitemapname`, `sitemapxml`, associated to the app. `AddAppComponents` — unbound action, body `{ "AppId": "<appmoduleid>", "Components": [ { "Type": <componenttype-int>, "Id": "<guid>" }, ... ] }`. Component type ints: entity=1, view(savedquery)=26, form=60, chart(savedqueryvisualization)=59, dashboard(systemform)=also via its formid, BPF(workflow)=29. **Verify these ints at execution time** via MS Learn (`microsoft_docs_search "AddAppComponents componenttype values"`) — they are the load-bearing magic numbers.

- [ ] **Step 1: Create the app module**

```bash
crm --json query odata appmodules --filter "uniquename eq 'cwx_crmworx'" --select name,appmoduleid   # guard
crm --json --dry-run entity create appmodules --data-file /tmp/cwx_app.json                          # preview
crm --json entity create appmodules --data-file /tmp/cwx_app.json
```
`/tmp/cwx_app.json`: `{ "name": "CRMWorx", "uniquename": "cwx_crmworx", "description": "CRMWorx IT ticketing", "clienttype": 4, "navigationtype": 0 }` (confirm `clienttype` for UCI via MS Learn). Capture `appmoduleid`. Iterate on rejection.

- [ ] **Step 2: Create + associate the sitemap**

Author SiteMapXml: Area "Service" → Group "CRMWorx" → SubAreas Tickets (`Entity="cwx_ticket"`), SLAs (`Entity="cwx_sla"`), Dashboard (`Url`/dashboard ref). Create the `sitemaps` row and associate it to the app (either via `appmoduleidunique`/the app's sitemap relationship, or by including the sitemap in AddAppComponents — confirm the on-prem 9.1 mechanism via MS Learn). Transcribe.

- [ ] **Step 3: Bind all components via AddAppComponents**

```bash
crm --json action invoke AddAppComponents --body-file /tmp/cwx_addcomponents.json
```
Body binds: both entities (type 1), the 3 views (type 26), the augmented main form + quick-create (type 60), the chart (type 59), the dashboard, and the BPF if A5 created it. Use the GUIDs captured in A1/A2/A3/A4/A5. Iterate on rejection (missing dependency → add it and re-invoke).

- [ ] **Step 4: Publish + validate the app**

```bash
crm --json solution publish-all
crm --json action invoke ValidateApp --body '{"AppModuleId":"<appmoduleid>"}'   # confirm action name/shape via MS Learn
```
Expected: validation reports the app is valid (or lists missing components → add them via AddAppComponents and re-validate). Read back the app's components (`RetrieveAppComponents` or `query odata appmodulecomponents --filter "_appmoduleidunique_value eq ..."`). Transcribe.

- [ ] **Step 5: Write §11 + commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §11 sitemap + model-driven app + AddAppComponents — live"
```

---

### Task A7: Launch & verify in browser (Playwright) — with read-back fallback

**Files:**
- Create: `docs/guides/images/crmworx-tickets-list.png`, `docs/guides/images/crmworx-ticket-form.png`, `docs/guides/images/crmworx-dashboard.png` (if browser proof succeeds).
- Modify: `docs/guides/crmworx-walkthrough.md` (new `## 12. Launch & verify`).

> **Note:** The Playwright MCP server was connected earlier this session but may be disconnected in the clean execution session. If its `browser_*` tools are unavailable, or the host cannot reach the on-prem app URL (NTLM/network), go straight to the read-back fallback (Step 4) — do not block.

- [ ] **Step 1: Construct the app URL**

`<CRM_BASE_URL>/main.aspx?appid=<appmoduleid>` (or the org's `/apps/` launcher). Print it.

- [ ] **Step 2: Navigate + screenshot (if Playwright + reachability available)**

Use `browser_navigate` to the app URL, then `browser_take_screenshot` for: the **Tickets list** (the "Active Tickets" view), a **ticket form** (open a seeded ticket — custom fields priority/severity/SLA/customer visible), and the **dashboard**. Save the three PNGs to `docs/guides/images/`. NTLM auth in-browser may prompt; if it blocks, fall back to Step 4.

- [ ] **Step 3: Embed the screenshots in §12** (`![Tickets list](images/crmworx-tickets-list.png)` etc.) as the headline visual proof.

- [ ] **Step 4: Read-back fallback (always run; it's the CLI proof regardless)**

```bash
crm --json query odata appmodules --filter "uniquename eq 'cwx_crmworx'" --select name,appmoduleid,uniquename
crm --json action invoke RetrieveAppComponents --body '{"AppModuleId":"<appmoduleid>"}'   # or appmodulecomponents query
```
Transcribe — this proves the app + its bound components exist on the server. If screenshots were captured, they + this read-back together are the proof; if not, §12 states the browser path was unavailable (with the reason) and the read-back is the proof.

- [ ] **Step 5: Commit**

```bash
git add docs/guides/crmworx-walkthrough.md docs/guides/images/ 2>/dev/null
git commit -m "docs(crmworx): §12 launch & verify (screenshots + app read-back)"
```

---

### Task A8: Update coverage table + teardown appendix; build docs

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (Capability-coverage table; teardown appendix; intro build-order line).

- [ ] **Step 1: Renumber + extend the Capability-coverage table**

Add rows for the new artifacts and update the build-order sentence in the intro (§ near line 8) to include `→ views → forms → charts → dashboard → BPF → app`. Update the coverage table `entity`/`action`/`solution` rows to cite the new sections (`entity create savedqueries/systemforms/savedqueryvisualizations/appmodules`, `action invoke AddAppComponents/ValidateApp`).

- [ ] **Step 2: Extend the teardown appendix**

Interface artifacts must be deleted **before** their entities (deleting the entity cascades most, but the appmodule + global sitemap are independent). Add, in correct order, ahead of the existing `delete-entity` calls:
```bash
# Interface teardown (before dropping the tables)
crm --json entity delete appmodules <appmoduleid> --yes
# views/forms/charts/dashboard are deleted with their entity; the app + sitemap are not
```
Note which artifacts cascade with the entity vs. need explicit deletion. Keep the existing entity/optionset teardown intact.

- [ ] **Step 3: Build the docs strictly**

```bash
pip install -e '.[docs]' >/dev/null 2>&1 || true
mkdocs build --strict 2>&1 | tail -20
```
Expected: `Documentation built` with no warnings. Fix any broken links/missing images (a referenced-but-missing screenshot fails `--strict`).

- [ ] **Step 4: Commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): coverage table + interface teardown + strict docs build"
```

---

## Phase B — promote views + app/sitemap to CLI commands

> Phase B is **TDD code**. Each command's XML generator emits the exact shape the live server accepted in Phase A. Mirror the `crm/core/optionsets.py` + `crm/commands/metadata.py` pattern: core helper returns a result dict (`{created, ...}`), command wires `_solution_option` / `_resolve_solution` / `_resolve_publish` / `_emit_with_warning`, read-back-on-create is non-fatal. pyright is **strict** on `crm/core/*` (no `# pyright: basic` in new core modules).

### Task B1: `crm view create` core (`crm/core/views.py`)

**Files:**
- Create: `crm/core/views.py`
- Test: `crm/tests/test_views.py`

- [ ] **Step 1: Write the failing test**

`crm/tests/test_views.py`:
```python
"""Unit tests for crm.core.views."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_VIEW_ID = "55555555-5555-5555-5555-555555555555"


def _post_body(m):
    for r in m.request_history:
        if r.method == "POST":
            return r.json()
    raise AssertionError("no POST recorded")


class TestCreateView:
    def test_builds_layout_and_fetch_and_posts(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            # existence guard: no view with that name yet
            m.get(backend.url_for("savedqueries"), json={"value": []})
            view_url = backend.url_for(f"savedqueries({_VIEW_ID})")
            m.post(backend.url_for("savedqueries"), status_code=204,
                   headers={"OData-EntityId": view_url})
            m.get(view_url, json={"savedqueryid": _VIEW_ID, "name": "Active Tickets"})
            out = views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="Active Tickets",
                columns=[("cwx_name", 220), ("cwx_priority", 120)],
                order_by="cwx_name", filter_active=True,
            )
        assert out["created"] is True
        assert out["savedqueryid"] == _VIEW_ID
        body = _post_body(m)
        assert body["returnedtypecode"] == "cwx_ticket"
        assert body["querytype"] == 0
        # LayoutXml carries the OTC and the columns in order
        assert 'object="10042"' in body["layoutxml"]
        assert body["layoutxml"].index("cwx_name") < body["layoutxml"].index("cwx_priority")
        # FetchXml carries the active filter + order
        assert 'attribute="statecode"' in body["fetchxml"]
        assert 'value="0"' in body["fetchxml"]
        assert 'attribute="cwx_name"' in body["fetchxml"]

    def test_existing_view_skips(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"),
                  json={"value": [{"savedqueryid": _VIEW_ID, "name": "Active Tickets"}]})
            out = views.create_view(
                backend, entity="cwx_ticket", object_type_code=10042,
                name="Active Tickets", columns=[("cwx_name", 220)],
                if_exists="skip",
            )
        assert out["skipped"] is True
        assert not any(r.method == "POST" for r in m.request_history)

    def test_existing_view_errors_by_default(self, backend):
        from crm.core import views
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("savedqueries"),
                  json={"value": [{"savedqueryid": _VIEW_ID, "name": "Active Tickets"}]})
            with pytest.raises(D365Error, match="already exists"):
                views.create_view(
                    backend, entity="cwx_ticket", object_type_code=10042,
                    name="Active Tickets", columns=[("cwx_name", 220)],
                )

    def test_requires_columns(self, backend):
        from crm.core import views
        with pytest.raises(D365Error, match="at least one column"):
            views.create_view(backend, entity="cwx_ticket", object_type_code=10042,
                              name="X", columns=[])
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/bin/python -m pytest crm/tests/test_views.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'crm.core.views'`.

- [ ] **Step 3: Implement `crm/core/views.py`**

```python
"""Custom view (savedquery) creation.

Generates LayoutXml (grid columns) + FetchXml (columns, order, optional
active-state filter) and POSTs a public view (querytype 0). Read-back on
create is non-fatal, matching the metadata-write precedent.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import quoteattr

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import maybe_publish


def _build_layoutxml(entity: str, object_type_code: int,
                     columns: list[tuple[str, int]]) -> str:
    id_attr = f"{entity}id"
    cells = "".join(
        f'<cell name={quoteattr(name)} width="{width}" />'
        for name, width in columns
    )
    jump = columns[0][0]
    return (
        f'<grid name="resultset" object="{object_type_code}" '
        f'jump={quoteattr(jump)} select="1" icon="1" preview="1">'
        f'<row name="result" id={quoteattr(id_attr)}>{cells}</row></grid>'
    )


def _build_fetchxml(entity: str, columns: list[tuple[str, int]],
                    order_by: str | None, filter_active: bool) -> str:
    id_attr = f"{entity}id"
    attrs = f'<attribute name={quoteattr(id_attr)} />' + "".join(
        f'<attribute name={quoteattr(name)} />' for name, _ in columns
    )
    order = (
        f'<order attribute={quoteattr(order_by)} descending="false" />'
        if order_by else ""
    )
    filt = (
        '<filter type="and"><condition attribute="statecode" '
        'operator="eq" value="0" /></filter>'
        if filter_active else ""
    )
    return (
        '<fetch version="1.0" output-format="xml-platform" mapping="logical">'
        f'<entity name={quoteattr(entity)}>{attrs}{order}{filt}</entity></fetch>'
    )


def create_view(
    backend: D365Backend,
    *,
    entity: str,
    object_type_code: int,
    name: str,
    columns: list[tuple[str, int]],
    order_by: str | None = None,
    filter_active: bool = False,
    is_default: bool = False,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a public system view (savedquery). Returns `{created, savedqueryid, ...}`."""
    if not name:
        raise D365Error("name is required.")
    if not columns:
        raise D365Error("at least one column is required.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    # Existence guard — savedqueries has no alternate key, so query by name+type.
    name_lit = name.replace("'", "''")
    existing = as_dict(backend.get(
        "savedqueries",
        params={
            "$filter": f"name eq '{name_lit}' and returnedtypecode eq '{entity}'",
            "$select": "savedqueryid,name",
        },
    )).get("value", [])
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"View {name!r} on {entity} already exists.",
                            code="AlreadyExists")
        return {"skipped": True, "exists": True, "name": name,
                "savedqueryid": existing[0].get("savedqueryid")}

    body: dict[str, Any] = {
        "name": name,
        "returnedtypecode": entity,
        "querytype": 0,
        "isdefault": is_default,
        "layoutxml": _build_layoutxml(entity, object_type_code, columns),
        "fetchxml": _build_fetchxml(entity, columns, order_by, filter_active),
    }
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("savedqueries", json_body=body,
                                  extra_headers=headers))
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    sqid = entity_id_url.split("savedqueries(")[-1].rstrip(")") if "savedqueries(" in entity_id_url else None
    out: dict[str, Any] = {
        "created": True, "name": name, "entity": entity,
        "savedqueryid": sqid, "solution": solution,
    }
    if sqid:
        try:
            rb = as_dict(backend.get(f"savedqueries({sqid})",
                                     params={"$select": "name,savedqueryid"}))
            out["name"] = rb.get("name", name)
        except D365Error as exc:
            out["view_lookup_error"] = f"Read-back failed: {exc}"
    maybe_publish(backend, out, publish)
    return out
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest crm/tests/test_views.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: pyright (strict on core)**

Run: `pyright crm/core/views.py`
Expected: `0 errors`. (If `maybe_publish`'s signature needs an import tweak, match `crm/core/optionsets.py`'s usage exactly.)

- [ ] **Step 6: Commit**

```bash
git add crm/core/views.py crm/tests/test_views.py
git commit -m "feat(views): crm.core.views.create_view — savedquery generator + tests"
```

---

### Task B2: `crm view create` command (`crm/commands/view.py`)

**Files:**
- Create: `crm/commands/view.py`
- Modify: `crm/cli.py` (register `view_group`)
- Test: `crm/tests/test_views.py` (add a CLI-level test via `CliRunner`)

- [ ] **Step 1: Write the failing CLI test** (append to `crm/tests/test_views.py`)

```python
class TestViewCommand:
    def test_view_create_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_create_view(backend, **kw):
            captured.update(kw)
            return {"created": True, "savedqueryid": _VIEW_ID, "name": kw["name"]}

        monkeypatch.setattr("crm.core.views.create_view", fake_create_view)
        # Avoid a real backend/publish:
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "view", "create", "cwx_ticket",
            "--name", "Active Tickets", "--otc", "10042",
            "--column", "cwx_name:220", "--column", "cwx_priority:120",
            "--order", "cwx_name", "--filter-active", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["entity"] == "cwx_ticket"
        assert captured["object_type_code"] == 10042
        assert captured["columns"] == [("cwx_name", 220), ("cwx_priority", 120)]
        assert captured["order_by"] == "cwx_name"
        assert captured["filter_active"] is True
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/bin/python -m pytest crm/tests/test_views.py::TestViewCommand -q`
Expected: FAIL — no `view` command.

- [ ] **Step 3: Implement `crm/commands/view.py`**

```python
"""View (savedquery) creation command."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import views as views_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _resolve_publish, _solution_option,
    _require_solution, _resolve_solution, _emit_with_warning,
)


@click.group("view")
def view_group():
    """Create and manage system views (savedquery)."""


def _parse_column(raw: str) -> tuple[str, int]:
    """Parse 'logicalname:width' (width optional, default 100)."""
    if ":" in raw:
        name, _, w = raw.partition(":")
        try:
            return name, int(w)
        except ValueError:
            raise click.BadParameter(f"column width must be an int: {raw!r}")
    return raw, 100


@view_group.command("create")
@click.argument("entity")
@click.option("--name", required=True, help="View display name.")
@click.option("--otc", "object_type_code", type=int, required=True,
              help="Entity ObjectTypeCode (from `metadata entity <name>`).")
@click.option("--column", "columns", multiple=True, required=True,
              help="Repeatable 'logicalname[:width]'. Order preserved.")
@click.option("--order", "order_by", default=None, help="Attribute to sort by (ascending).")
@click.option("--filter-active", is_flag=True, help="Filter to statecode=0 (active) rows.")
@click.option("--default", "is_default", is_flag=True, help="Mark as the default view.")
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@_solution_option
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def view_create(ctx: CLIContext, entity, name, object_type_code, columns,
                order_by, filter_active, is_default, if_exists,
                solution, require_solution, publish):
    """Create a public system view on ENTITY."""
    parsed = [_parse_column(c) for c in columns]
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        info = views_mod.create_view(
            ctx.backend(), entity=entity, object_type_code=object_type_code,
            name=name, columns=parsed, order_by=order_by,
            filter_active=filter_active, is_default=is_default,
            solution=solution, if_exists=if_exists,
        )
        if publish and not info.get("_dry_run") and not info.get("skipped"):
            from crm.core import solution as sol_mod
            sol_mod.publish_all(ctx.backend())
            info["published"] = True
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)
```

- [ ] **Step 4: Register in `crm/cli.py`**

Add with the other command imports (after line 285, `from crm.commands.init import init_cmd`):
```python
from crm.commands.view import view_group  # noqa: E402
```
And with the `cli.add_command(...)` block (after `cli.add_command(init_cmd)`):
```python
cli.add_command(view_group)
```

- [ ] **Step 5: Run — verify pass**

Run: `.venv/bin/python -m pytest crm/tests/test_views.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: pyright + smoke**

Run: `pyright crm/core/views.py` → `0 errors`. (`crm/commands/view.py` is `# pyright: basic`.)
Run: `.venv/bin/crm view create --help` → shows the options.

- [ ] **Step 7: Commit**

```bash
git add crm/commands/view.py crm/cli.py crm/tests/test_views.py
git commit -m "feat(views): crm view create command + CLI test + registration"
```

---

### Task B3: `crm app` core + commands (`crm/core/appmodule.py`, `crm/commands/app.py`)

**Files:**
- Create: `crm/core/appmodule.py`, `crm/commands/app.py`
- Modify: `crm/cli.py` (register `app_group`)
- Test: `crm/tests/test_appmodule.py`

**Use the component-type ints and the AddAppComponents/sitemap shapes confirmed working in Task A6.** The code below uses the standard ints (entity=1, view=26, form=60, chart=59); if A6 proved different values on this org, use those and update the test to match.

- [ ] **Step 1: Write the failing test**

`crm/tests/test_appmodule.py`:
```python
"""Unit tests for crm.core.appmodule."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_APP_ID = "77777777-7777-7777-7777-777777777777"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


class TestCreateApp:
    def test_create_app_posts_appmodule_and_reads_back(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("appmodules"), json={"value": []})  # guard
            app_url = backend.url_for(f"appmodules({_APP_ID})")
            m.post(backend.url_for("appmodules"), status_code=204,
                   headers={"OData-EntityId": app_url})
            m.get(app_url, json={"appmoduleid": _APP_ID, "name": "CRMWorx",
                                 "uniquename": "cwx_crmworx"})
            out = appmodule.create_app(
                backend, name="CRMWorx", unique_name="cwx_crmworx",
                description="IT ticketing",
            )
        assert out["created"] is True
        assert out["appmoduleid"] == _APP_ID
        body = _posts(m)[0].json()
        assert body["uniquename"] == "cwx_crmworx"
        assert body["name"] == "CRMWorx"

    def test_create_app_skips_when_exists(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("appmodules"),
                  json={"value": [{"appmoduleid": _APP_ID, "uniquename": "cwx_crmworx"}]})
            out = appmodule.create_app(backend, name="CRMWorx",
                                       unique_name="cwx_crmworx", if_exists="skip")
        assert out["skipped"] is True
        assert not _posts(m)


class TestAddComponents:
    def test_add_components_builds_action_body(self, backend):
        from crm.core import appmodule
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("AddAppComponents"), status_code=204)
            out = appmodule.add_app_components(
                backend, app_id=_APP_ID,
                components=[("entity", "aaaa"), ("view", "bbbb"), ("chart", "cccc")],
            )
        assert out["added"] == 3
        body = _posts(m)[0].json()
        assert body["AppId"] == _APP_ID
        types = {c["Type"] for c in body["Components"]}
        assert types == {1, 26, 59}  # entity, view, chart

    def test_add_components_rejects_unknown_kind(self, backend):
        from crm.core import appmodule
        with pytest.raises(D365Error, match="unknown component kind"):
            appmodule.add_app_components(backend, app_id=_APP_ID,
                                         components=[("widget", "xxxx")])
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/bin/python -m pytest crm/tests/test_appmodule.py -q`
Expected: FAIL — `No module named 'crm.core.appmodule'`.

- [ ] **Step 3: Implement `crm/core/appmodule.py`**

```python
"""Model-driven app (appmodule) + component binding.

create_app POSTs an appmodules row (read-back non-fatal). add_app_components
wraps the unbound AddAppComponents action, mapping friendly component kinds to
Dataverse componenttype ints. set_sitemap creates a sitemaps row from raw XML.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import maybe_publish

# Dataverse component-type ints accepted by AddAppComponents.
_COMPONENT_TYPES: dict[str, int] = {
    "entity": 1,
    "view": 26,            # savedquery
    "chart": 59,           # savedqueryvisualization
    "form": 60,            # systemform
    "dashboard": 60,       # systemform (type=Dashboard) — same component type
    "bpf": 29,             # workflow (business process flow)
    "sitemap": 62,
}


def create_app(
    backend: D365Backend,
    *,
    name: str,
    unique_name: str,
    description: str | None = None,
    client_type: int = 4,
    navigation_type: int = 0,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a model-driven app module. Returns `{created, appmoduleid, ...}`."""
    if not unique_name or "_" not in unique_name:
        raise D365Error("unique_name must include a publisher prefix, e.g. 'cwx_crmworx'.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    un_lit = unique_name.replace("'", "''")
    existing = as_dict(backend.get(
        "appmodules",
        params={"$filter": f"uniquename eq '{un_lit}'",
                "$select": "appmoduleid,uniquename"},
    )).get("value", [])
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"App {unique_name!r} already exists.", code="AlreadyExists")
        return {"skipped": True, "exists": True, "uniquename": unique_name,
                "appmoduleid": existing[0].get("appmoduleid")}

    body: dict[str, Any] = {
        "name": name,
        "uniquename": unique_name,
        "clienttype": client_type,
        "navigationtype": navigation_type,
    }
    if description:
        body["description"] = description
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("appmodules", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    app_id = (entity_id_url.split("appmodules(")[-1].rstrip(")")
              if "appmodules(" in entity_id_url else None)
    out: dict[str, Any] = {
        "created": True, "name": name, "uniquename": unique_name,
        "appmoduleid": app_id, "solution": solution,
    }
    if app_id:
        try:
            rb = as_dict(backend.get(f"appmodules({app_id})",
                                     params={"$select": "name,uniquename,appmoduleid"}))
            out["name"] = rb.get("name", name)
        except D365Error as exc:
            out["app_lookup_error"] = f"Read-back failed: {exc}"
    maybe_publish(backend, out, publish)
    return out


def add_app_components(
    backend: D365Backend,
    *,
    app_id: str,
    components: list[tuple[str, str]],
) -> dict[str, Any]:
    """Bind components to an app via the AddAppComponents action.

    `components` is a list of `(kind, guid)` where kind is one of
    _COMPONENT_TYPES. Raises D365Error on an unknown kind before any HTTP call.
    """
    if not app_id:
        raise D365Error("app_id is required.")
    if not components:
        raise D365Error("at least one component is required.")
    payload_components: list[dict[str, Any]] = []
    for kind, guid in components:
        if kind not in _COMPONENT_TYPES:
            raise D365Error(
                f"unknown component kind {kind!r}; "
                f"expected one of {sorted(_COMPONENT_TYPES)}."
            )
        payload_components.append({"Type": _COMPONENT_TYPES[kind], "Id": guid})
    backend.post("AddAppComponents",
                 json_body={"AppId": app_id, "Components": payload_components})
    return {"added": len(payload_components), "app_id": app_id}


def set_sitemap(
    backend: D365Backend,
    *,
    sitemap_name: str,
    sitemap_xml: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a sitemaps row from raw SiteMapXml. Returns `{created, sitemapid}`."""
    if not sitemap_xml.strip():
        raise D365Error("sitemap_xml must not be empty.")
    body = {"sitemapname": sitemap_name, "sitemapxml": sitemap_xml}
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("sitemaps", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result
    entity_id_url = result.get("_entity_id_url") or ""
    smid = (entity_id_url.split("sitemaps(")[-1].rstrip(")")
            if "sitemaps(" in entity_id_url else None)
    return {"created": True, "sitemapid": smid, "sitemapname": sitemap_name,
            "solution": solution}
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest crm/tests/test_appmodule.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Implement `crm/commands/app.py`**

```python
"""Model-driven app (appmodule) commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import appmodule as app_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _resolve_publish, _solution_option,
    _require_solution, _resolve_solution, _emit_with_warning, _load_payload,
)


@click.group("app")
def app_group():
    """Create and manage model-driven apps (appmodule)."""


@app_group.command("create")
@click.option("--name", required=True, help="App display name.")
@click.option("--unique-name", required=True,
              help="Publisher-prefixed unique name, e.g. 'cwx_crmworx'.")
@click.option("--description", default=None)
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@_solution_option
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def app_create(ctx: CLIContext, name, unique_name, description, if_exists,
               solution, require_solution, publish):
    """Create a model-driven app."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        info = app_mod.create_app(
            ctx.backend(), name=name, unique_name=unique_name,
            description=description, solution=solution, if_exists=if_exists,
        )
        if publish and not info.get("_dry_run") and not info.get("skipped"):
            from crm.core import solution as sol_mod
            sol_mod.publish_all(ctx.backend())
            info["published"] = True
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)


@app_group.command("add-components")
@click.argument("app_id")
@click.option("--component", "components", multiple=True, required=True,
              help="Repeatable 'kind:guid' (kind: entity|view|chart|form|dashboard|bpf|sitemap).")
@pass_ctx
def app_add_components(ctx: CLIContext, app_id, components):
    """Bind components to an app (AddAppComponents)."""
    parsed: list[tuple[str, str]] = []
    for raw in components:
        kind, _, guid = raw.partition(":")
        if not guid:
            raise click.BadParameter(f"--component must be 'kind:guid': {raw!r}")
        parsed.append((kind, guid))
    try:
        info = app_mod.add_app_components(ctx.backend(), app_id=app_id,
                                          components=parsed)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@app_group.command("set-sitemap")
@click.argument("sitemap_name")
@click.option("--xml-file", type=click.Path(exists=True, dir_okay=False), required=True,
              help="Path to a file containing the SiteMapXml.")
@_solution_option
@pass_ctx
def app_set_sitemap(ctx: CLIContext, sitemap_name, xml_file, solution, require_solution):
    """Create a sitemap from a SiteMapXml file."""
    with open(xml_file, "r", encoding="utf-8") as fh:
        xml = fh.read()
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = app_mod.set_sitemap(ctx.backend(), sitemap_name=sitemap_name,
                                   sitemap_xml=xml, solution=solution)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
```

- [ ] **Step 6: Register in `crm/cli.py`** (alongside the Task B2 view registration)

```python
from crm.commands.app import app_group  # noqa: E402
...
cli.add_command(app_group)
```

- [ ] **Step 7: Add a CLI test for `app create`** (append to `crm/tests/test_appmodule.py`)

```python
class TestAppCommands:
    def test_app_create_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        def fake_create_app(backend, **kw):
            captured.update(kw)
            return {"created": True, "appmoduleid": _APP_ID, "uniquename": kw["unique_name"]}
        monkeypatch.setattr("crm.core.appmodule.create_app", fake_create_app)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
        from click.testing import CliRunner
        result = CliRunner().invoke(cli, [
            "--json", "app", "create", "--name", "CRMWorx",
            "--unique-name", "cwx_crmworx", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        assert captured["unique_name"] == "cwx_crmworx"

    def test_app_add_components_command(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        def fake_add(backend, **kw):
            captured.update(kw)
            return {"added": len(kw["components"]), "app_id": kw["app_id"]}
        monkeypatch.setattr("crm.core.appmodule.add_app_components", fake_add)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "app", "add-components", _APP_ID,
            "--component", "entity:aaaa", "--component", "view:bbbb",
        ])
        assert result.exit_code == 0, result.output
        assert captured["components"] == [("entity", "aaaa"), ("view", "bbbb")]
```

- [ ] **Step 8: Run all + pyright + smoke**

Run: `.venv/bin/python -m pytest crm/tests/test_appmodule.py crm/tests/test_views.py -q` → PASS.
Run: `pyright crm/core/appmodule.py crm/core/views.py` → `0 errors`.
Run: `.venv/bin/crm app create --help` and `.venv/bin/crm app add-components --help` → show options.

- [ ] **Step 9: Commit**

```bash
git add crm/core/appmodule.py crm/commands/app.py crm/cli.py crm/tests/test_appmodule.py
git commit -m "feat(app): crm app create / add-components / set-sitemap + tests"
```

---

### Task B4: Reproduce a Phase-A artifact with the new commands + document; full verification

**Files:**
- Modify: `docs/guides/crmworx-walkthrough.md` (add a `## 13. Promoted commands` section showing the new commands reproducing a Phase-A view + the app).

- [ ] **Step 1: Reproduce the "Active SLAs" view with the new command (live)**

Delete the A1 "Active SLAs" view first (or pick a new name), then recreate it via the command to prove the generator emits a server-accepted shape:
```bash
crm --json view create cwx_sla --name "Active SLAs (cmd)" --otc <cwx_sla OTC> \
  --column "cwx_name:240" --column "cwx_slatier:140" --filter-active --if-exists skip
crm --json query odata savedqueries --filter "name eq 'Active SLAs (cmd)'" --select name,savedqueryid
```
Expected: created + read-back shows the view. Transcribe.

- [ ] **Step 2: Reproduce the app create with the command (idempotent skip)**

```bash
crm --json app create --name CRMWorx --unique-name cwx_crmworx --if-exists skip
```
Expected: `skipped: true` (the app from A6 already exists) — proves the command + its existence guard. Transcribe.

- [ ] **Step 3: Write §13 + update the coverage table**

Add a coverage-table row: `view | §13 — crm view create` and `app | §13 — crm app create / add-components`.

- [ ] **Step 4: Full verification gate**

```bash
.venv/bin/python -m pytest -q 2>&1 | tail -8
pyright crm/core/views.py crm/core/appmodule.py 2>&1 | tail -3
mkdocs build --strict 2>&1 | tail -5
```
Expected: pytest — new tests pass; only the **3 pre-existing `TestConnectionEnv` failures (#36)** remain (env-specific `.env` fallback; not regressions — confirm they're the same 3 by name). pyright — `0 errors`. mkdocs — built, no warnings.

- [ ] **Step 5: Commit**

```bash
git add docs/guides/crmworx-walkthrough.md
git commit -m "docs(crmworx): §13 reproduce view + app via promoted commands; full verify"
```

---

## Completion

After all tasks pass verification:
- Announce: "I'm using the finishing-a-development-branch skill to complete this work."
- **REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present options (Push & PR is expected, base `main`), request a Copilot review (`gh pr edit <n> --add-reviewer @copilot`), and link any issue filed in Task A5.

---

## Self-review (author checklist — completed)

- **Spec coverage:** Views→A1/B1/B2; Forms→A2; Charts→A3; Dashboard→A4; BPF→A5 (with fallback+issue per spec §3.5); Sitemap+App→A6/B3; Browser proof→A7 (with read-back fallback per spec §3.7); Phase-B promotion (views+app+sitemap only)→B1–B4; coverage table + teardown→A8/B4; mkdocs --strict→A8/B4; "left deployed"→ implied (teardown is appendix-only, not run). All spec sections mapped.
- **Non-goal adherence:** No form/dashboard/chart/BPF *generators* promoted to commands (Phase B is views + app + sitemap only) — matches spec non-goals. No security roles / themes / managed packaging.
- **Placeholder scan:** No TBD/TODO. Live-server XML that genuinely cannot be pre-known (Fetch/Form/chart/dashboard/BPF exact bytes) is given as concrete starting templates with explicit "iterate against the quoted server error / fetch the verbose shape from MS Learn at execution time" instructions — this is correct for live work, not a placeholder.
- **Type consistency:** `create_view(...)` kwargs match between B1 core, the B1 tests, and the B2 command/test (`object_type_code`, `columns: list[tuple[str,int]]`, `order_by`, `filter_active`, `if_exists`). `create_app` / `add_app_components` / `set_sitemap` signatures match between B3 core, tests, and commands. Component-kind→int map (`_COMPONENT_TYPES`) is the single source of truth, asserted in the test.
- **Magic-number risk:** ObjectTypeCode (org-specific) is captured live in A0, never hardcoded. AddAppComponents component-type ints are flagged for live confirmation in A6 and the B3 code/test note to update if the org differs.
