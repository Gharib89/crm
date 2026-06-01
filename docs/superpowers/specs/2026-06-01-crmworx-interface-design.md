# CRMWorx interface — live build + command promotion

**Date:** 2026-06-01
**Status:** Approved (pending user review of written spec)
**Target version:** 0.7.0 (Phase B commands)
**Predecessor:** [#35 — CRMWorx live-run walkthrough](https://github.com/Gharib89/crm/pull/35) (merged) and [Spec D — Metadata write API](./2026-05-25-spec-d-metadata-write-design.md) (which listed "Form / view / chart metadata" as out of scope — this spec picks it up).

---

## 0. Why

The CRMWorx walkthrough proves the `crm` CLI can build a Dynamics 365 **data model** end to end (entities, attributes, relationships, option sets, solution). It does **not** yet produce a *usable interface*: the entities are reachable only via Advanced Find / direct URL, the only views show the primary name + created-on, and there is no app to navigate.

This spec extends the walkthrough to a **fully working model-driven UI** — views, forms, charts, a dashboard, a business process flow, a sitemap, and a model-driven app — built **live against the same D365 CE on-prem 9.1 server**, and then promotes the highest-value pieces to first-class CLI commands. The deliverable is a proof: *Claude Code + the `crm` CLI can fully automate D365 customization, data model through interface, end to end.*

## 1. Goals + non-goals

### Goals

- **Phase A (prove live, existing primitives):** build a navigable CRMWorx app on the live server using only today's CLI (`entity create`, `action invoke`, `query`, `solution publish-all`) with Claude-authored XML, and transcribe every real command + output into the walkthrough. Artifacts:
  - **Views** (`savedquery`) — custom public views on `cwx_ticket` + `cwx_sla` surfacing priority / severity / SLA / customer.
  - **Forms** (`systemform`) — a customized main form (clone-and-augment) + a quick-create form on `cwx_ticket`.
  - **Charts** (`savedqueryvisualization`) — "Tickets by Priority" bar chart.
  - **Dashboard** (`systemform` type=Dashboard) — "CRMWorx SLA Overview": the chart + an Active-Tickets list.
  - **Business process flow** (`workflow` category=BusinessProcessFlow) — "Ticket Resolution": New → In Progress → Resolved.
  - **Sitemap + model-driven app** (`appmodule` + `sitemap` + `AddAppComponents`) — UCI app "CRMWorx" binding all of the above.
  - **Browser proof** — Playwright MCP navigates the live app and screenshots the Tickets list, a ticket form (custom fields visible), and the dashboard, embedded in the guide.
- **Phase B (promote to commands):** ship reusable CLI commands for the small-predictable-XML, high-reuse pieces, each generator emitting the XML shape **the live server already accepted in Phase A**:
  - `crm view create` — `savedquery` (generates LayoutXml + FetchXml).
  - `crm app create` — `appmodule`.
  - `crm app add-components` — `AddAppComponents` wrapper.
  - sitemap authoring (folded into `app create` or a `crm app set-sitemap`).
- Bump to **0.7.0**. All Phase B additions are additive (new commands + helper modules).

### Non-goals

- **Form / dashboard / chart / BPF generators as commands.** FormXml, dashboard FormXml, chart data/presentation XML, and BPF clientdata/xaml are large and fragile; generating them generically does not pay off. They stay **primitive-driven and documented in the guide**, not promoted to commands.
- **PCF controls, custom pages, canvas apps, web resources (JS/HTML).** Out of scope.
- **Security roles / field security / business units.** Out of scope.
- **Theme / branding of the app.** Default theme.
- **Managed-solution packaging of the app.** The app ships in the existing unmanaged `CRMWorx` solution.

### Breaking changes

None. Phase B is pure additive CLI surface. Phase A adds no code (uses existing commands).

---

## 2. Architecture

### 2.1 Two phases, one branch, one session each

- **Branch:** `docs/crmworx-interface`, cut from **`main`** (which now carries the merged walkthrough + CLI fixes). Phase A and Phase B both land on this branch; one PR to `main`.
- **Execution:** the live build runs in a **clean session from `main`** via the executing-plans skill against the plan this spec produces. (This spec + its plan are committed to `main` first so that session can load them.)
- **Doc target:** extend `docs/guides/crmworx-walkthrough.md`. Insert interface sections **between §5 Package and the Capability-coverage / Teardown tail**, renumbering teardown last. New sections, in dependency order: Views → Forms → Charts → Dashboard → Business process flow → Sitemap + model-driven app → Launch & verify (screenshots). Update the Capability-coverage table and the teardown appendix to remove the new artifacts.

### 2.2 Dependency order (Phase A)

Each step: preview (`--dry-run` where the verb supports it) → execute `--json` → transcribe real output → read-back proof (`query` / `metadata` / `action`) → publish. Idempotent re-runs guarded by an existence query before create (savedquery/systemform/etc. have no `--if-exists`, so the guide GETs by name first).

```
entities (already deployed, #35)
  └─> 1. views (savedquery)            depends on: entity + its attributes + ObjectTypeCode
  └─> 2. forms (systemform)            depends on: entity + attributes
  └─> 3. charts (savedqueryvisualization)
        └─> 4. dashboard (systemform type=Dashboard)   references chart GUID + view GUID
  └─> 5. BPF (workflow category=4)     depends on: entity + stage fields
        └─> 6. sitemap + appmodule + AddAppComponents   binds entities/forms/views/charts/dashboard/BPF
              └─> PublishAllXml → ValidateApp → 7. browser launch + screenshots
```

### 2.3 Dataverse targets (implementation reference)

Exact payloads are **validated live** during execution (the live 9.1 server is the oracle, as in Plan 2); Microsoft Learn is consulted at execution time for the risky shapes. Targets:

| Artifact | Entity set | Key fields | Create via | Publish |
|---|---|---|---|---|
| View | `savedqueries` | `name`, `returnedtypecode`, `fetchxml`, `layoutxml`, `querytype` (0=public), `isdefault` | `entity create savedqueries` | PublishXml (entity) |
| Form | `systemforms` | `objecttypecode`, `type` (2=Main, 7=QuickCreate), `formxml`, `name` | clone-and-augment: GET default → PATCH; quick-create via `entity create` | PublishXml |
| Chart | `savedqueryvisualizations` | `name`, `primaryentitytypecode`, `datadescription`, `presentationdescription` | `entity create savedqueryvisualizations` | PublishXml |
| Dashboard | `systemforms` | `type`=0 (Dashboard), `formxml`, `name` | `entity create systemforms` | PublishXml |
| BPF | `workflows` | `category`=4, `type`=1, `primaryentity`, `uniquename`, `clientdata`, `xaml` | `entity create workflows` + activate | activate (statecode=1) |
| App | `appmodules` | `name`, `uniquename` (`cwx_crmworx`), `clienttype`, `navigationtype` | `entity create appmodules` | PublishAll + ValidateApp |
| Sitemap | `sitemaps` | `sitemapname`, `sitemapxml` | `entity create sitemaps` + associate to app | PublishAll |
| Bind components | — | `AppId`, `Components` | `action invoke AddAppComponents` | PublishAll |

LayoutXml `object=` and several payloads need the entity **ObjectTypeCode** — fetched live via `metadata entity cwx_ticket` (`ObjectTypeCode`).

### 2.4 Phase B module layout

```
crm/core/
  views.py        — NEW: create_view(backend, entity, name, columns, order, filter_active, querytype, is_default)
                    + _build_layoutxml(...) + _build_fetchxml(...)
  appmodule.py    — NEW: create_app(backend, name, unique_name, description)
                    + add_app_components(backend, app_id, components)
                    + set_sitemap(backend, app_id, sitemap_xml | area/group/subarea spec)
crm/commands/
  view.py         — NEW: `crm view create`
  app.py          — NEW: `crm app create`, `crm app add-components`, `crm app set-sitemap`
crm/tests/
  test_views.py        — NEW: LayoutXml/FetchXml generation + POST payload (requests_mock)
  test_appmodule.py    — NEW: appmodule create + AddAppComponents payload + sitemap
```

`crm/core/views.py` and `crm/core/appmodule.py` are pyright-strict (under `crm/core/*`). Reuse `D365Backend.post`/`get`, the `--solution` helper (`MSCRM.SolutionUniqueName`), the read-back-on-create precedent, and `label`/`maybe_publish`/`as_dict` from `crm/core/metadata.py`.

---

## 3. Phase A artifact detail

### 3.1 Views (`savedquery`)
- `cwx_ticket`: **"Active Tickets"** (public, default) — cols: `cwx_name`, `cwx_priority`, `cwx_severity`, `cwx_SLA` (lookup), `cwx_customerid` (lookup), `createdon`, `statuscode`; FetchXml filter `statecode=0`, order by `cwx_priority`. Plus **"Tickets by Priority"** (public).
- `cwx_sla`: **"Active SLAs"** — cols: `cwx_name`, response/resolution-target fields, tier; filter `statecode=0`.
- LayoutXml grid + FetchXml; `object=` uses live ObjectTypeCode.

### 3.2 Forms (`systemform`) — clone-and-augment
- GET `cwx_ticket` default main form (`type eq 2`), inject a **"Ticket details"** tab/section laying out priority / severity / SLA / customer / category, PATCH `formxml` back. Lower failure rate than from-scratch — starts from a form 9.1 already accepts.
- **Quick-create form** on `cwx_ticket` (`type`=7): name + priority + customer.

### 3.3 Charts (`savedqueryvisualization`)
- `cwx_ticket` **"Tickets by Priority"** — bar; `datadescription` FetchXML grouped by `cwx_priority` with count aggregate; `presentationdescription` bar chart.

### 3.4 Dashboard (`systemform` type=Dashboard)
- **"CRMWorx SLA Overview"** — two components: the priority chart + an Active-Tickets list (references the §3.1 view GUID).

### 3.5 Business process flow (`workflow` category=4) — time-boxed + fallback
- `cwx_ticket` **"Ticket Resolution"**: stages **New → In Progress → Resolved**, each with a couple of step fields.
- On-prem 9.1 BPF-via-Web-API (`workflows` POST with `clientdata` JSON + `xaml`, then activate) is the **most underdocumented** piece. Approach: time-boxed live attempt (bounded iterations). If it cannot be made to work, the guide documents the **manual portal steps** and an **issue is filed** (Plan 2's hybrid policy) — the guide states plainly which path was taken. No faked "done."

### 3.6 Sitemap + model-driven app
- `appmodules` POST → app **"CRMWorx"** (`uniquename` `cwx_crmworx`, UCI `clienttype`).
- `sitemaps` POST → area **"Service"** → group **"CRMWorx"** → subareas Tickets (`cwx_ticket`), SLAs (`cwx_sla`), Dashboard; associate to the app.
- `action invoke AddAppComponents` → bind entities (`cwx_ticket`, `cwx_sla`), forms, views, chart, dashboard, BPF.
- `solution publish-all` → `ValidateApp` (read-back). App-validation gaps (missing component deps) resolved by adding the dependency and re-binding.

### 3.7 Launch & verify
- App URL `…/main.aspx?appid=<appmoduleid>` (or `…/apps/`). Playwright MCP navigates, screenshots **Tickets list**, a **ticket form** (custom fields visible), the **dashboard** → embedded in the guide as the visual proof.
- **Fallback** (host can't reach the on-prem URL — NTLM/network): CLI `RetrieveAppComponents` / `query` read-back proof + a noted caveat. Honest about which proof was captured.

---

## 4. Risks

- **FormXml** — clone-and-augment lowers but does not remove validation pain; may need live iteration.
- **BPF on 9.1 via API** — real chance of landing on the documented-manual fallback + filed issue.
- **appmodule / AddAppComponents on on-prem 9.1** — validated live; app-validation errors fixed by adding missing component deps.
- **Browser proof** — depends on this host reaching the on-prem app URL; CLI read-back fallback otherwise.
- **Phase B generators only proven for this demo** — mitigated by mocked-payload tests asserting the exact XML the live server accepted, plus parameterization (columns, filters, app name).

## 5. Success criteria

- Every Phase A artifact created live; each command + real output transcribed; read-back confirms each exists and is published.
- Browser screenshots of a navigable CRMWorx app (Tickets list + ticket form + dashboard), **or** the documented read-back fallback with a stated reason.
- Phase B: `crm view create`, `crm app create`, `crm app add-components` (+ sitemap) work; tests pass; **pyright clean** on `crm/core/views.py` + `crm/core/appmodule.py`; each command reproduces a Phase-A artifact.
- `mkdocs build --strict` passes. CRMWorx left deployed.
- Any artifact the Web API cannot do on 9.1 is documented with a manual fallback + filed issue, not omitted or faked.
