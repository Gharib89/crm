# Safe Customization-XML Editors — Research Synthesis Report

**Date:** 2026-06-21
**Status:** Research complete; review-gated. No work filed to GitHub. Awaiting user review before any backlog item is created.
**CLI assessed:** `crm` (current `main`) — dual-target: on-prem Dynamics 365 CE v9.x (NTLM) **and** Dataverse online (OAuth), both over the Dataverse Web API (OData v4).
**Inputs:** (1) the published Microsoft customization XSD set (Schemas.zip + on-prem ApplicationFiles); (2) live solution exports from `agent-cloud` and `agent-on-prem` v9.1; (3) the `crm` `SOLUTION_COMPONENT_TYPES` map + live `$metadata`; (4) the 59-scenario dev catalogue (`SCN-001..SCN-059`); (5) measured round-trip fidelity + a PyInstaller cost proxy for `lxml`; (6) per-family adversarial verification passes.

This report mirrors the June 2026 dev-scenarios report shape (executive summary → classified inventory → per-candidate dossiers → mechanics → consolidated verdict table). Every claim a verifier could not confirm is flagged inline as **[UNVERIFIED]**.

---

## 1. Executive summary

**What we did.** We asked a single question for each D365 customization component that is stored as XML: *can the `crm` CLI safely add a targeted, grammar-aware structural editor for it — the shape `form` and `ribbon` already use — and if so, how do we edit that XML without silent corruption?* We scored every candidate on **two independent axes** (D7): is the **grammar documented** (does MS publish an XSD?) and is the **edit operation supported** (does MS sanction *this* write path — entity-field PATCH vs solution roundtrip vs designer-only?). We then designed, per editor, the mandatory D2 safety bar: **T0** well-formed, **T2** grammar-aware semantic pre-flight, **T3** read-back verify.

**Top findings.**

1. **The two axes genuinely diverge — "has an XSD" ≠ "editing it is supported."** Microsoft explicitly lists editing `customizations.xml` to *define* Entities, Attributes, Relationships, OptionSets, WebResources, Processes, Plugins, SDK steps, Reports, ConnectionRoles, Templates, SecurityRoles and FieldSecurityProfiles as **unsupported** — use the metadata APIs instead ([When to edit the customizations file](https://learn.microsoft.com/power-platform/alm/when-edit-customization-file)). Only **four** customization edits are sanctioned in `customizations.xml`: **ribbons, forms, SiteMap, saved queries**. That four-item allow-list is the spine of every build-now verdict below.

2. **Six XML families carry real, safe, build-worthy editors.** Forms (FormXml), Dashboards (dashboard FormXml), Charts/visualizations, Ribbon/command bar (RibbonDiffXml), SiteMap (SiteMapXml), and Views/saved-query layout (layoutxml+fetchxml). Each maps onto a sanctioned write path and a documented (or, for two narrow sub-cases, observed-only) grammar, and each already has a partial implementation seam in the repo to mirror.

3. **The catastrophic corruptions are well-formed AND XSD-valid — so a pure XSD gate is insufficient.** A mutated `classid`, or the form-clone internal-GUID collision class (issue #275), passes both T0 and an XSD check yet silently breaks the artifact. This is why the safety bar is grammar-aware T2 (validate referenced columns/web-resources/views exist; **protect** `classid` and fixed platform refs; regenerate dependent internal GUIDs *consistently*) **plus** T3 read-back — not a runtime XSD validator. We **ship the XSDs as reference** and build to the grammar (D3); we do not add a runtime XSD dependency.

4. **Generic XPath / path primitives are rejected by design (D1).** A grammar-blind "set any attribute at any path" verb *is* the silent-corruption surface this whole effort exists to avoid — it can blank a required `@Id`, inject a space-bearing SiteMap Id, strip an internal `ResourceId`, or mutate a `classid`, all while emitting well-formed XML. Every family below rejects its generic-primitive candidate and folds the legitimate behavior into targeted, invariant-keeping verbs.

5. **Stay stdlib-only. Do not adopt `lxml`.** Measured round-trip fidelity (see §6) shows `lxml`'s non-configurable self-close re-spacing turns a one-attribute FormXml edit into ~6,928 diff lines, while stdlib `xml.etree.ElementTree` matches D365's export style and preserves `classid`/attribute-order/indentation. The proxy PyInstaller cost of `lxml` is **~9.4 MB uncompressed (~14% bundle growth) — labelled proxy, not a real build diff**. Neither of `lxml`'s unique advantages (namespace-prefix-faithful serialization, full XPath 1.0) is needed: every editable payload is namespace-free.

6. **Two Track-2 families stay rejected.** BPF definition authoring (clientdata JSON + XAML + processstage rows; SCN-021, #37) and Business-rule logic authoring (workflow category 2 XAML) both fail all three flip conditions — no published authorable grammar, no sanctioned logic-write path, and a tri-container/opaque-XAML corruption surface that T2/T3 cannot certify. Consistent with `CONTEXT.md` and the existing `workflow clone` category-4 refusal.

**Top build-now recommendations** (full detail in §3, consolidated table in §7):

1. **Form JS event/handler wiring** (onload/onsave/onchange + form libraries) — documented grammar, sanctioned PATCH, named SCN-012 gap, strong demand, no scriptable dual-target alternative. *High.*
2. **Ribbon hide OOB button** (default to the reversible false-DisplayRule method; gate the one-way `HideCustomAction` behind explicit confirm) — highest-demand ribbon gap (SCN-049). *High.*
3. **Ribbon edit enable/display rules on a custom command** — directly closes SCN-049; the apply/validate plumbing already exists. *High.*
4. **Dashboard add-chart / add-view** (ChartGrid control) + **chart `update` (PATCH)** — close the SCN-016 create-only gap on the proven FormXml seam. *High.*
5. **SiteMap add/remove/reorder nav nodes** (read-modify-write `sitemapxml`) + **View edit-columns / set-order** (layoutxml+fetchxml PATCH) — both green on both axes, both live-confirmed dual-target, both close the family's headline edit-existing gap. *High.*

---

## 2. Phase 0 — classified inventory (coverage spine)

Sources merged: the published XSD set, live cloud + on-prem v9.1 exports, and the `crm` `SOLUTION_COMPONENT_TYPES` map / `$metadata`.

**Live export structure (confirmed inline).** Solution zip = `customizations.xml` + `solution.xml` + `[Content_Types].xml`. Root `<ImportExportXml>` with sections: Entities, Roles, Workflows, FieldSecurityProfiles, Templates, EntityMaps, EntityRelationships, OrganizationSettings, optionsets, CustomControls, AppModuleSiteMaps, EntityDataProviders, Languages. Each `<Entity>` holds EntityInfo, FormXml, SavedQueries, RibbonDiffXml, Visualizations (charts when present). SiteMap lives under `AppModuleSiteMaps/AppModuleSiteMap/SiteMap`.

**MS publishes these customization XSDs:** `CustomizationsSolution.xsd`, `FormXml.xsd`, `RibbonCore.xsd`, `RibbonTypes.xsd`, `RibbonWSS.xsd`, `SiteMap.xsd`, `SiteMapType.xsd`, `fetch.xsd`, `VisualizationDataDescription.xsd`, `isv.config.xsd`. **No BPF / process-XAML / business-rule schema exists in the published set.**

### 2.1 Classified inventory table

| Component (XML container) | Published XSD? | Edit supported? (sanctioned write path) | Track |
|---|---|---|---|
| **Forms** — `systemform.formxml` | Yes — `FormXml.xsd` | Yes — PATCH `systemforms({id}){formxml}` + export/reimport | Track-1 |
| **Dashboards** — `systemform.formxml` (formtype=dashboard) | Yes — `FormXml.xsd` (+ MS Learn dashboard element table) | Yes — same FormXml PATCH path | Track-1 |
| **Charts / visualizations** — `savedqueryvisualization.{datadescription, presentationdescription}` | datadescription: Yes (thin) — `VisualizationDataDescription.xsd`; presentationdescription: **No XSD** | Yes — PATCH writable columns (IsValidForUpdate) | Track-1 |
| **Ribbon / command bar** — `<Entity>/RibbonDiffXml` (in `customizations.xml`) | Yes — `RibbonCore/Types/WSS.xsd` | Yes — solution roundtrip (export→edit→import→publish) | Track-1 |
| **SiteMap** — `sitemap.sitemapxml` | Yes — `SiteMap.xsd` + `SiteMapType.xsd` | Yes — PATCH `sitemaps({id}){sitemapxml}` (writable column + Update message) | Track-1 |
| **Views / saved-query layout** — `savedquery.{layoutxml, fetchxml}` | Yes — `CustomizationsSolution.xsd` (layout) + `fetch.xsd` | Yes — PATCH writable columns, gated by `IsCustomizable` | Track-1 |
| **BPF definition** — `workflow.{clientdata, xaml}` + processstage rows (category 4) | **No** | **No** sanctioned authoring path (designer-only) | Track-2 (reject) |
| **Business rules** — `workflow.xaml` (category 2) | **No** | **No** sanctioned logic-write path (designer-only) | Track-2 (reject) |
| Entity / Attribute / Relationship / OptionSet / WebResource / Process / Plugin / SDKstep / Report / ConnectionRole / Template / SecurityRole / FieldSecurityProfile **definition** | (some have XSD via CustomizationsSolution) | **Unsupported** to edit as XML — use metadata APIs | Out-of-domain |

> **Confidentiality note.** Live exports and the MS sample FormXML carry real GUIDs and a machine-fingerprint suffix. This report describes XML **shape and element/attribute names only**; placeholders (`<entity>`, `GUID`, `contoso`) stand in for any real identifier.

---

## 3. Track-1 dossier compendium

Each dossier records both D7 axes, the T2/T3 design, round-trip fidelity, dual-target status, demand evidence, and verdict+priority. Verifier corrections are folded in; any claim a verifier marked `holds=false` is downgraded and flagged.

### 3.1 Forms (FormXml)

**Family baseline (axis-2, verified `holds=true`).** FormXml is a writable column → direct PATCH `systemforms({id}){formxml}` is supported, and solution export/reimport of FormXml is sanctioned. MS lists "Form and dashboard customization using FormXml" as a supported task and FormXml editing among the supported `customizations.xml` edits. The shipped `form add/remove/set-field` editors (`crm/core/forms.py`) already exercise this PATCH path with live both-target e2e coverage. Sources: [FormXml schema](https://learn.microsoft.com/power-apps/developer/model-driven-apps/form-xml-schema), [When to edit the customizations file](https://learn.microsoft.com/power-platform/alm/when-edit-customization-file).

| Candidate | Axis1 grammar | Axis2 edit | T2 (validate / protect) | T3 read-back | Verdict (priority) |
|---|---|---|---|---|---|
| **Columns/fields (cell+control)** — *already shipped* | Documented (`FormXml.xsd`) | PATCH | classid from live metadata (never regen); duplicate guard; tab/section validated | etree round-trip + e2e; optional explicit read-after-PATCH | **build-now (high)** — baseline; only add is explicit T3 |
| **JS event/handler wiring** (onload/onsave/onchange + formLibraries) | Documented — `FormXmlEventsType`, `FormXmlHandlerType` (functionName, libraryName, handlerUniqueId, passExecutionContext), `FormXmlLibraryType` | PATCH | **Validate referenced web resource EXISTS** (GET webresourceset) — #1 guard; onchange `--field` is a real on-form attribute; fresh handlerUniqueId/libraryUniqueId; preserve handler ORDER; **merge `<Handlers>` into existing `<event>`, never append a duplicate `<event>`**; **target `<Handlers>`, never the sibling `<InternalHandlers>`** | parse; assert `<Handler functionName=… libraryName=…>` under right `<event>` (right control for onchange) with the fresh GUID; remove asserts absent | **build-now (high)** |
| **Tabs** (add/remove/rename/reorder) | Documented | PATCH | fresh tab+section id; `IsUserDefined=1`; emit non-empty columns/section skeleton; refuse removing only tab / tab holding bound controls | assert named tab present/absent; sibling GUIDs untouched | **build-now (medium)** |
| **Sections** (add/remove/rename/reorder, column count) | Documented | PATCH | fresh section id; columns 1–4; refuse remove holding bound controls (or `--force` surfacing orphans); reuse `_resolve_target_section` | assert section present/absent under named tab | **build-now (medium)** — closes the add-field "no section to target" gap |
| **Subgrids** (related-records grid) | Documented (control + loosely-typed `<parameters>`) | PATCH (params bag under-specified) | validate relationship + ViewId (savedquery of target) + target entity; protect subgrid classid; ViewId external ref (validate, never regen); **`<parameters>` required-key set is the corruption surface** | assert subgrid present with resolved relationship+view | **needs-more-research (medium)** — capture verified per-target parameter templates first |
| **Quick View control** (embed quick form) | Documented (`QuickForms` is `xs:string` — HTML-encoded inner XML) | PATCH (nested-grammar trap) | validate lookup attr is a lookup; quick-view form (type 6) exists; correctly HTML-encode inner `<QuickFormIds>` payload | decode inner QuickFormId; assert == resolved formid | **needs-more-research (low)** — dedicated inner-payload serializer; capture cloud exemplar |
| **Header / Footer field placement** (`FormXmlHeaderFooterType`) | Documented | PATCH | thin retarget of add/remove-field onto `<header>`/`<footer>`; reuse classid + duplicate guard; warn over ~4-cell classic header | assert control present/absent under header/footer | **build-later (low)** |
| **Field properties** (locked/disabled/showlabel/visible — **NOT required-level**) | Documented (cell/control attrs) | mixed — form attrs are PATCH; **required-level is attribute metadata (UpdateAttribute), a different family** | toggle existing attr in place (no GUID/classid/ref); **explicitly EXCLUDE required-level → route to metadata** | assert the attr flipped on the right cell/control | **build-now (medium)** — among the safest editors (no GUID/classid surface) |
| **Quick Create form authoring** (type 7) | Documented (same grammar) | PATCH (CopySystemForm for create) | reuse field/section T2; quick-create layout constraints not XSD-enforced | same as field edits | **build-later (low)** — not a distinct editor; only marginal `form create --type quickcreate` |
| **DisplayConditions / Navigation** | Documented | designer-only | n/a | n/a | **reject (low)** |

**Verifier corrections folded in (Forms).**
- **DisplayConditions reject rationale corrected (verifier `holds=false`).** `FormDisplayConditionsType` is **not** opaque: `FormXml.xsd` gives it a fully-specified simple grammar (a choice of `<Everyone/>` or one-or-more `<Role Id='GUID'/>`, with optional `FallbackForm`/`Order`). This is the documented form-by-security-role / form-order feature, which makers do edit. The **reject decision stands** but is re-justified on **demand + absence-from-both-exports** grounds, not on "no verified authoring grammar."
- **Dual-target parity for events/subgrid/quickview is [UNVERIFIED] this run.** The cited live `/tmp` exemplars from a prior session were gone when re-checked; only the **field editors** carry actual both-target live e2e evidence. The dossier's "capture a live exemplar per target" caveats are therefore **mandatory pre-build steps** for the subgrid and quickview candidates, not optional polish. (Schema parity is established via the single shared `FormXml.xsd`.)
- **Web-resource supportability framed precisely.** "Web Resources" on the unsupported list means **defining/creating** a web resource via `customizations.xml`, **not referencing an existing one** in `<formLibraries>`. The event/library editor must **require the web resource to pre-exist** (GET webresourceset) and never create it.
- **Round-trip "minimal-touch" is semantic, not byte-identical.** etree reserialization normalizes untouched markup (attribute order, whitespace, self-closing style). Fine for cell/control GUID-preservation (proven by the clone-id guard + e2e); but the events and QuickForms editors must not rely on byte-stable **encoded-text** payloads — QuickForms needs its own encode/decode serializer.

### 3.2 Dashboards (dashboard FormXml)

**Axis-2 (verified `holds=true`).** MS lists "Editing FormXml — used to define forms **and dashboards**" under supported `customizations.xml` edits on both the Dataverse and on-prem v9.1 doc trees; dashboards are absent from the unsupported list. Runtime write = `SystemForm.formxml` PATCH then publish — the same path `crm dashboard create` already uses. Sources: [Understand dashboards (FormXML elements)](https://learn.microsoft.com/power-apps/developer/model-driven-apps/understand-dashboards-dashboard-components-formxml), [Create a dashboard — Limitations](https://learn.microsoft.com/power-apps/developer/model-driven-apps/create-dashboard).

**T2 rules are MS-documented (verified `holds=true`).** tab ≥ 1 section; a cell's `rowspan` must equal the count of `<row>` in its section; grid `AutoExpand` must be `Fixed` (Auto is the documented footgun); IFRAME `<Url>` must be non-empty; org-owned dashboards admit only org-owned charts and saved-query grids; **6 components is a *default* cap** (raisable on-prem via PowerShell) — use `--force`, never hard-block.

| Candidate | Axis1 | Axis2 | T2 (validate / protect) | T3 read-back | Verdict (priority) |
|---|---|---|---|---|---|
| **add-chart** (ChartGrid, `ChartGridMode=Chart`) | Documented | PATCH | **protect classid `{E7A81278-…}` (MS-documented constant)**; fresh cell id; validate TargetEntityType/ViewId/VisualizationId (org-owned, primaryentity matches); `AutoExpand=Fixed`; rowspan==row-count; tab≥1 section; default-6 cap | re-GET formxml; classid intact, refs landed verbatim, cell-count +1, no pre-existing cell id mutated | **build-now (high)** — lowest-risk highest-demand (SCN-016) |
| **add-view** (ChartGrid, `ChartGridMode=List/All`) | Documented | PATCH | protect classid; ViewId is a savedquery whose returnedtypecode==target; `IsUserView=false`; **enforce `AutoExpand=Fixed`** (the documented silent-misconfiguration); same layout invariants | classid intact, ViewId landed, `AutoExpand==Fixed`, cell-count +1 | **build-now (high)** — near-zero marginal cost once add-chart exists |
| **add-iframe / add-webresource** (`{fd2a7985-…}`) | Documented | PATCH | **`<Url>` non-empty** (documented footgun); web resource EXISTS + warn if not form-enabled; typed bool Security/Scrolling/Border; layout invariants | classid intact, `<Url>` == requested, cell-count +1 | **build-now (medium)** |
| **remove-component** (any cell) | Documented | PATCH | no classid emission (delete-only); **keep rowspan==row-count by dropping matching empty `<row/>`**; refuse ambiguous target | cell-count −1; target gone; other ids+classids unchanged; sections satisfy rowspan==row-count | **build-now (medium)** |
| **move-component** (relocate cell) | Documented | PATCH | **preserve moved cell id/control/classid/params**; validate destination; re-satisfy rowspan==row-count in BOTH source and dest | cell byte-identical to pre-move, now under requested tab/section, total cell-count unchanged | **build-later (low)** — highest-bookkeeping / lowest-demand |
| **raw grid-geometry primitive** (free rowspan/colspan/width, naked add-section/row) | Documented | designer-only | n/a — invariants folded into add/remove/move as automatic bookkeeping | n/a | **reject (low)** — the silent-corruption surface (D1) |

**Verifier corrections folded in (Dashboards).**
- **ChartGrid classid `{E7A81278-…}` is MS-DOCUMENTED** (appears in the MS *Sample dashboards* FormXML), not merely a live constant — strengthen the framing. **But the IFRAME/web-resource classid `{fd2a7985-…}` was NOT found in the MS sample/element table** — it rests on live read-back only; **[UNVERIFIED]** — validate against a live IFRAME tile before hard-coding it as a protected constant.
- **Round-trip "verbatim" downgraded to [UNVERIFIED].** The reuse seam re-serializes the whole tree via `ET.tostring(root)`, which does **not** produce "minimal diff = mutated subtree only" and may reorder attributes / drop the `<?xml?>` declaration / normalize whitespace relative to the platform-stored blob. The correct bar is the **structural** T3 read-back (classid intact, ids unchanged, cell-count delta) — keep that; do not promise byte-verbatim until proven live.
- **Publish path corrected.** `crm dashboard create` publishes via `maybe_publish → PublishAllXml`, **not** a per-dashboard `PublishXmlRequest`. The add/remove/move verbs should reuse `maybe_publish`, or introduce a scoped `PublishXml` as a deliberate, documented choice.

### 3.3 Charts / visualizations (datadescription + presentationdescription)

**Axis-2 (verified `holds=true`).** `DataDescription` and `PresentationDescription` are `IsValidForUpdate=true` on both `savedqueryvisualization` and `userqueryvisualization` → direct Web API PATCH is sanctioned (a data operation governed by `IsValidForUpdate`). Sources: [SavedQueryVisualization reference](https://learn.microsoft.com/power-apps/developer/data-platform/reference/entities/savedqueryvisualization), [Understand charts](https://learn.microsoft.com/power-apps/developer/model-driven-apps/understand-charts-underlying-data-chart-representation), [Visualization data description schema](https://learn.microsoft.com/power-apps/developer/model-driven-apps/visualization-data-description-schema).

| Candidate | Axis1 | Axis2 | T2 (validate / protect) | T3 read-back | Verdict (priority) |
|---|---|---|---|---|---|
| **datadescription** — edit FetchXML data layer (categories/series/measures) | Documented but **thin** — `VisualizationDataDescription.xsd` validates only the outer tree; **inner `<fetch>` validates against `fetch.xsd`, NOT the data-description XSD** | PATCH | **REQUIRED pre-flight: every inner `<attribute name>`/`<entity name>` exists (metadata)** — the un-validated FetchXML island is the real risk surface; **ALIAS-COUPLING invariant**: every `<measure alias>` ↔ a fetch `<attribute alias>`/aggregate ↔ a presentationdescription `<Series>`; protect primaryentitytypecode | re-GET, re-parse, re-run alias-coupling + fetch-column-exists on the server-returned XML; primaryentitytypecode unchanged | **build-now (high)** |
| **presentationdescription** — edit appearance (chart type, colors, axes, titles) | **No XSD** — serialization of `System.Web.UI.DataVisualization.Charting.Chart` | PATCH (axis-2 green, axis-1 **yellow flag**) | build to **observed grammar + curated allow-list**, never a free path edit; bounded enums (ChartType); each `<Series>` ↔ a datadescription measure; **pass through unmodeled attributes verbatim** | assert targeted attr changed AND Series count/aliases unchanged (proves no accidental series drop) | **build-later (medium)** — rides on undocumented serialization; must be called out |
| **chart `update`** — whole-document PATCH (the create-only gap closer) | Composite (thin XSD + fetch.xsd; presentation none) | PATCH | reuse create's mode validation; when both XML columns given, run cross-container alias-coupling before write; when one given, read the other live and validate the pair; protect primaryentitytypecode | provided fields landed; unprovided column unchanged; full alias-coupling on server doc | **build-now (high)** — smallest/safest/highest-leverage; the natural host for the shared alias-coupling T2 |

**Verifier corrections folded in (Charts).**
- **Drop the `customizations.xml` carve-out reasoning (verifier).** The supported allow-list (ribbons/forms/SiteMap/**saved queries**) covers `savedquery` (**views**), **not** `savedqueryvisualization` (**charts**). The chart write is sanctioned **solely** by the direct Web API PATCH on `IsValidForUpdate=true` columns — restate the rationale on that basis only.
- **Publish is REQUIRED for SYSTEM charts (verifier — major omission).** Any update to a `savedqueryvisualization` must be published via `PublishAllXml` to take effect org-wide; **user charts (`userqueryvisualization`) do not need publish**. The update / set-fetch / add-series designs must default-to (or strongly prompt) publish for system charts, and T3 should verify the **published** state. Gate the publish step on system-vs-user target.
- **add-series series-count bounds (verifier).** Non-comparison charts support a maximum of **five** series; comparison charts support exactly one series and two categories. `chart add-series` must reject a 6th series (or a series on a comparison chart) before write, alongside the alias-coupling check.
- **Dual-target softened to "doc-consistent + matches the dossier's own live read-backs"** — the byte-level "identical, no divergence" claim is **[UNVERIFIED]** this run (on-prem needs VPN; the dossier notes an on-prem FetchXML dategrouping-attr difference inside the un-validated `<fetch>` island). Build-now decision stands.
- **Web-resource path size cap (verifier).** The web-resource branch of `chart update` inherits the ~16,000-char base64 (~12,000 raw) cap on imported visualization XML; the direct column PATCH path is not subject to it.

### 3.4 Ribbon / command bar (RibbonDiffXml)

**Axis-2 (verified `holds=true`).** MS lists "Editing the ribbon" under supported tasks; the on-prem v9.x "Supported extensions" page states verbatim that *"Use of RibbonDiffXml to add, remove, or hide ribbon elements is supported."* `HideCustomAction` is documented. The shipped `ribbon add-button/remove/list/export` verbs already drive the `apply_ribbon_change` export→mutate→validate→import→publish round-trip with a wired `$webresource:` existence check (`crm/core/solution_validate.py`). Sources: [Define custom actions to modify the ribbon](https://learn.microsoft.com/power-apps/developer/model-driven-apps/define-custom-actions-modify-ribbon), [Define ribbon enable rules](https://learn.microsoft.com/power-apps/developer/model-driven-apps/define-ribbon-enable-rules), [Define ribbon actions](https://learn.microsoft.com/power-apps/developer/model-driven-apps/define-ribbon-actions).

| Candidate | Axis1 | Axis2 | T2 (validate / protect) | T3 read-back | Verdict (priority) |
|---|---|---|---|---|---|
| **hide-button** (OOB) | Documented (`HideCustomAction`) | solution-roundtrip | validate `--target-id` resolves in the live composed ribbon (a typo silently no-ops — #1 ribbon defect); **`HideCustomAction` is a one-way trapdoor** — default to the reversible method (a CustomAction whose `Location` == the OOB element's Id, with `Mscrm.HideOnModern` + `Mscrm.ShowOnlyOnModern` → always-false); gate `HideCustomAction` behind explicit irreversibility confirm; never mutate classid/Command/TemplateAlias | RetrieveEntityRibbon: target absent (hide-action) or carries the two false DisplayRules; assert by parsed value | **build-now (high)** — SCN-049 |
| **set-rules / add-custom-rule** (enable/display rules) | Documented (`RibbonTypes.xsd`) | solution-roundtrip | platform `Mscrm.*` ids from a curated allow-list (unknown id silently no-ops); custom rule → `$webresource:` exists (reuse `_check_webresource_refs`); ValueRule `--field` is a real column; never touch CommandDefinition Id | exported CommandDefinition's EnableRule/DisplayRule set matches exactly | **build-now (high)** — closes SCN-049 |
| **set-label** (labels + tooltips + LocLabels) | Documented | solution-roundtrip | target is a custom Button; protect Command/TemplateAlias/Sequence/Id; **`$LocLabels:<LocLabel-Id>` directive** (text in `<Titles><Title languagecode=… description=…>`); validate `--lcid` against provisioned languages; **LocLabel Ids are CASE-SENSITIVE** | exported Button has new LabelText/ToolTip*, correctly XML-escaped; LocLabel row for lcid exists | **build-now (medium)** |
| **set-sequence** (ordering) | Documented | solution-roundtrip | target is a custom CustomAction/Button; only `@Sequence` | exported `@Sequence` == requested | **build-later (low)** — overlaps add-button `--sequence` |
| **set-action** (JS/Url + params) | Documented (`Actions`, `JavaScriptFunction`, `Url`, `*Parameter`) | solution-roundtrip | `$webresource:` exists; **preserve positional param ORDER (unnamed, must match function arg order)** — load-bearing; CrmParameter Value from documented enum; never touch CommandDefinition Id | exported Actions: FunctionName/Library == requested; param child sequence byte-identical in order+type | **build-later (medium)** — larger grammar, lower demand |
| **app-ribbon targeting** (`--application`) | Documented | mixed | app-ribbon RibbonDiffXml node exists; EntityRule Context/AppliesTo enums; don't write app-scoped action into an `<Entity>` diff | RetrieveApplicationRibbon: new app-scoped CustomAction landed in the app container | **needs-more-research (medium)** — app-ribbon node was absent from both test-org exports |
| **ribbon clone** (bulk subtree copy) | Documented | solution-roundtrip | n/a — verbatim id/Location copy = #275-class collision | n/a | **reject (low)** — generic-subtree-copy footgun; safe shape is a per-button id-rebasing add |

**Verifier corrections folded in (Ribbon).**
- **Round-trip "byte-equal" language dropped (verifier `holds=false`).** `_rewrite_customizations` byte-preserves only the *other* zip members; `customizations.xml` itself is fully reparsed and re-serialized via `ET.tostring(encoding='utf-8')`, which re-quotes/reorders attributes and normalizes whitespace. **T3 must compare parsed element/attribute values, never raw bytes.**
- **Add the unsupported-OOB-JS-reuse constraint (verifier — dossier omission).** The on-prem SDK "Supported extensions" page states *"Reuse of JavaScript functions defined within ribbon commands isn't supported"* and *"we reserve the right to change or deprecate the available commands."* `hide-via-display-rule`, `set-rules`, and `set-action` against OOB commands sit on this caveat — the CLI may **emit it as a warning**, not silently bless it.
- **Fix the hide mechanism wording (verifier).** The override is a CustomAction whose `Location` is set **equal to the OOB element's Id** (the worked MS example redefines the OOB `<CommandDefinition Id=…>` with the two false DisplayRules).
- **Fix the LocLabels design (verifier `holds=false`).** Directive is `$LocLabels:<LocLabel-Id>` (not `…/Title`); localized text lives in `<LocLabels>/<LocLabel Id=…>/<Titles>/<Title languagecode=… description=…>`; map `--lcid` to the `<Title>` `languagecode`; add the **case-sensitive Id** T2 check.
- Container distinction: read-back is via RetrieveEntityRibbon/RetrieveApplicationRibbon (decode member `RibbonXml.xml`); the **edit** target is `customizations.xml` inside the solution zip — keep these two distinct in any T3 wording.

### 3.5 SiteMap (SiteMapXml)

**Both axes GREEN (rare).** SiteMap is one of the four explicitly supported `customizations.xml` edits, AND `sitemapxml` is a writable column with a first-class `Update` (PATCH `sitemaps({id})`) message; MS states you "can programmatically update site map." Write path = GET `sitemaps(id)?$select=sitemapxml` → mutate tree → PATCH back. Sources: [SiteMap schema](https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/customize-dev/sitemap-schema?view=op-9-1), [Customize SiteMaps](https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/customize-dev/customize-sitemaps?view=op-9-1), [Change application navigation using the SiteMap](https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/customize-dev/change-application-navigation-using-sitemap?view=op-9-1).

The existing `app build-sitemap` is **rebuild-only** (constructs `<SiteMap>` from tuples; SubArea supports `Entity=`/`Title=` only) and `app set-sitemap` POSTs a raw file with no read-modify-write and no read-back. The gap is **edit-existing** of a live/default sitemap.

| Candidate | Axis1 | Axis2 | T2 (validate / protect) | T3 read-back | Verdict (priority) |
|---|---|---|---|---|---|
| **add-area / add-group / add-subarea** | Documented | PATCH | parent Id exists; new Id matches `[a-zA-Z0-9_]+` & unique (publisher-prefix recommended); **for `--entity`: logical name EXISTS** (dangling Entity= silently hides the node); `--dashboard` GUID exists; `--webresource`/`$webresource:` icon exists; **exactly-one-of {entity, url, dashboard}**; never touch `ResourceId`/`IntroducedVersion`; **no internal GUIDs → #275 class is ABSENT** | re-GET sitemapxml; spliced node present under named parent; Id-set after == before ∪ {new} | **build-now (high)** |
| **remove-node** (`--comment-out`) | Documented | PATCH | target Id exists once; warn on Area/Group cascade; comment-out must stay well-formed (no `--` inside comment) | target Id absent (or only inside a comment); all other Ids present | **build-now (high)** |
| **move-node** (`--before/--after/--index`) | Documented | PATCH | node + anchor share same parent and node type; index in range; move only — never mutate the node | node at requested position; parent's child-Id multiset unchanged (pure permutation) | **build-now (medium)** — the canonical case MS says needs a manual XML edit |
| **set-title / set-description** (localized) | Documented (`TitlesType_SiteMap`) | PATCH | LCID is a 4-digit installed language; one Title per LCID (dedup is a tool rule — XSD allows dup); **respect strict child sequence: Titles → Descriptions → child nodes** (naive append = schema-invalid import); never touch `ResourceId` | node has `<Titles><Title LCID=… Title=…>`; element order schema-valid | **build-later (medium)** |
| **add-privilege** (SubArea guard) | Documented | PATCH | SubArea exists; Entity exists; **Privilege ∈ full enum** (Read\|Write\|Append\|AppendTo\|Create\|Delete\|Share\|Assign\|All\|AllowQuickCampaign\|CreateEntity\|ImportCustomization\|UseInternetMarketing) | SubArea carries `<Privilege Entity=… Privilege=…>` | **build-later (low)** — fold into the SubArea editor as a flag |
| **generic set-attr (XPath)** | Documented | unsupported (by spec) | n/a | n/a | **reject (low)** — violates D1 |

**Verifier corrections folded in (SiteMap).**
- **Axis-2 wording softened** to "MS-sanctioned (writable column + Update message + explicit 'programmatically update' statement)" — MS does **not** publish a worked GET-mutate-PATCH-sitemapxml recipe and consistently recommends the designer first.
- **Round-trip fidelity downgraded to VERIFY-AT-BUILD.** "Byte-stable elsewhere" is not MS-guaranteed (platform may normalize on PATCH/publish). The T3 Id-set diff (after == before ∪ {new}) is a **required** gate; do not promise byte-equality in user-facing copy.
- **SubArea content-mode list FIXED (verifier `holds=false`).** There is **no** SubArea `WebResource` attribute in the XSD. A web-resource subarea is expressed via `Url` (HTML web-resource URL); the maker "Web resource" content type maps to `Url`. **Collapse `--webresource` into the `--url` path**; reserve `$webresource:` strictly for the **Icon** directive. Mutually-exclusive content attributes are `Entity` vs `Url` (with `DefaultDashboard` as a third).
- **Id-uniqueness scope tightened (verifier).** XSD `<unique>` is **scoped**: Area Ids unique among Areas; Group Ids unique within their parent Area; **SubArea Id has no XSD uniqueness — the tool must enforce it.** Don't claim document-wide uniqueness.
- **Dual-target write-semantics parity is [UNVERIFIED].** Docs-level shape parity holds (`sitemap{sitemapid, sitemapname, isappaware}`, same op-9-1 XSD); per repo rule (cloud silently rewrites inputs on-prem rejects), require an actual on-prem PATCH+read-back before shipping.
- **Managed-merge "only ONE sitemap customization between publishes" is the dossier's paraphrase — [UNVERIFIED] this pass;** re-confirm exact wording from the understand-managed-solutions-merged page at build time before citing it as a hard constraint. The "designer appends new nodes at the bottom → reorder needs a manual edit" rationale is doc-consistent.

### 3.6 Views / saved-query layout (layoutxml + fetchxml)

**Both axes GREEN (verified `holds=true`).** MS sanctions setting `savedquery.layoutxml` AND `fetchxml` via UpdateRequest (= Web API PATCH); both are `IsValidForUpdate`; the edit is gated by the `IsCustomizable` managed property. `savedquery` is **not** on the unsupported define-list. Sources: [Customize views](https://learn.microsoft.com/power-apps/developer/model-driven-apps/customize-entity-views), [SavedQuery reference](https://learn.microsoft.com/power-apps/developer/data-platform/reference/entities/savedquery), [Can't see data in certain columns (layout/fetch mismatch)](https://learn.microsoft.com/troubleshoot/dynamics-365/sales/troubleshoot-table-views-issues). Today the CLI is **create-only** (`view create`); `read_entity_views` parses cells/order (a partial RMW base, but public-views-only).

| Candidate | Axis1 | Axis2 | T2 (validate / protect) | T3 read-back | Verdict (priority) |
|---|---|---|---|---|---|
| **edit-columns** (add/remove/reorder/resize) | Documented (`CustomizationsSolution.xsd` grid/row/cell) | PATCH (IsCustomizable-gated) | **MISMATCH INVARIANT** (MS troubleshooting): every non-PK layoutxml `<cell name>` ↔ a fetchxml `<attribute name>` — add MUST add both, remove MUST remove both, never edit layoutxml alone; each added column EXISTS (metadata); keep PK cell+attribute; width is `nonNegativeInteger` > 0; no GUIDs → no regen risk | GET layoutxml,fetchxml(,**layoutjson**); cell-name set+order as expected; width landed; **re-run mismatch check** on the server doc | **build-now (high)** |
| **set-order** (fetch `<order>`) | Documented (`fetch.xsd`) | PATCH | order attribute EXISTS; `descending ∈ {true,false}`; fetch-only → no layoutxml coupling; protect filter/condition/link-entity siblings | GET fetchxml; `<order>` set (attr+descending, in order) matches; siblings untouched | **build-now (high)** |
| **add-filter / remove-filter** (`<filter>/<condition>`) | Documented (`fetch.xsd`) | PATCH | condition attr EXISTS; operator ∈ `fetch.xsd` enum; value type/cardinality matches operator (`in`→children, `null`→none, `between`→2); **some operators are version-gated on v9.1** — validate or let backend reject; protect existing conditions/link-entity | GET fetchxml; new/removed condition present/absent (attr+op+value); siblings preserved; optional query-probe to confirm backend accepts | **build-later (medium)** — condition mini-grammar is materially larger |
| **scope / target-resolution contract** (public vs singleton types) | Documented | PATCH (public=CRUD; advanced-find/associated/quick-find/lookup=Update-only) | resolve EXACTLY ONE savedquery by name+returnedtypecode+querytype (no alt-key); **check `IsCustomizable` `.Value` before PATCH** (clean "not customizable" error); quick-find layout-vs-find-columns guard | read-back by resolved **savedqueryid** (not name); querytype matches | **build-now (medium)** — the target-selection layer the column/order editors must get right |

**Verifier corrections folded in (Views).**
- **Add `savedquery.layoutjson` to T3 (verifier `holds=false`).** `LayoutJson` is a separate `IsValidForUpdate` field driving modern Unified Interface grid rendering; a layoutxml-only PATCH can leave a stale layoutjson and desync what users see. Add it to the read-back `$select`; decide explicitly to **null/clear it (let the server rebuild) or rebuild it**. **Downgrade fidelity from "HIGH" to "HIGH for layoutxml+fetchxml; layoutjson must be reconciled."**
- **`read_entity_views` is NOT the RMW base for non-public views (verifier `holds=false`).** It hard-filters `querytype eq 0`. The editor needs a **new** read path resolving by name+returnedtypecode+querytype for all editable types (select layoutxml,fetchxml,layoutjson,iscustomizable). Only the public path reuses `read_entity_views`; `_parse_column`/`_parse_order` **are** reusable.
- **Querytype enumeration corrected (verifier `holds=false`).** Live cloud export contains `querytype=8192` (a layoutxml-less system saved-query) outside the 5-type map. Target resolution must **whitelist editable querytypes** and handle savedqueries with **no layoutxml** (skip/error), not assume one of five.
- **Dual-target (`holds=true`):** layoutxml/fetchxml shape identical on both exports; cosmetic export-only difference (cloud `IsCustomizable/CanBeDeleted` vs on-prem `queryapi`) does not touch the editable fields.
- **Managed-layer warning** (doc requirement, not a blocker): editing an OOB/managed view creates an unmanaged active layer that solution upgrades may revert — surface in help text.

---

## 4. Track-2 — feasibility-only (BPF + business rules)

Both are **undocumented designer-owned serializations** with no published authorable grammar and no sanctioned logic-write path. Both **stay rejected**, consistent with `CONTEXT.md` (line ~167, "internal-serialization surgery, out of scope") and the existing `workflow clone` category-4 refusal.

### 4.1 Business Process Flow definition authoring (clientdata + xaml, workflow category 4) — SCN-021, #37

- **Supported path found:** No. MS routes BPF *definition* authoring through the visual designer only; the documented programmatic surface is entirely **instances/navigation/activation** (the nearest definition message, `SetProcess`, is deprecated). Sources: [BPF with code](https://learn.microsoft.com/power-automate/developer/business-process-flows-code), [Model BPFs (on-prem)](https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/model-business-process-flows?view=op-9-1).
- **D2 three-test:** **T0** partial/fail (clientdata is undocumented JSON with no grammar to be well-formed *against*; the artifact is tri-part — clientdata + xaml + processstage rows — that must be mutually consistent). **T2** fail — no grammar; cannot validate refs or enforce the **stage-GUID tri-consistency** (each stage GUID appears in clientdata, in xaml, AND as a processstage row — the #275 collision class made worse). **T3** weak/fail — the only correctness oracle is activation + opening the designer; read-back of an opaque blob proves storage, not fidelity.
- **Verdict: stays-rejected.** **What would flip it:** ALL of — MS publishes a documented authorable BPF clientdata/xaml grammar **OR** ships a supported server-side *authoring* action (analogous to the StageAndUpgrade actions that turned out to back managed-lifecycle in SCN-035); AND a sketchable T2 (refs + stage-GUID tri-consistency vs that grammar); AND a T3 asserting against the grammar (not just activation); AND recurring demand beyond the lone SCN-021. **Recommended off-ramp:** a narrow, fully-supported **`bpf instance`** family (create/advance/abort process-instance rows) is separately buildable and serves real demand without touching the definition surface.

### 4.2 Business rules (workflow category 2 XAML)

- **Supported path found:** No. Logic is designer-generated; MS lists Processes/Workflows as **unsupported** to define via `customizations.xml`. The only adjacent real lever is the **Process Trigger** (initiation-only — it re-binds which events *initiate* an existing rule; it cannot author logic). Sources: [Create a business rule](https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-create-business-rule), [When to edit the customizations file](https://learn.microsoft.com/power-platform/alm/when-edit-customization-file), [How business rules are initiated (on-prem)](https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/customize-dev/create-edit-how-business-rules-initiated?view=op-9-1).
- **D2 three-test:** **T0** partial (well-formed bytes, but no grammar to build to). **T2** not sketchable (no XSD; cannot validate XAML structure, internal activity GUIDs, namespace/assembly refs, or the synchronous-plugin compilation contract). **T3** partial only (correctness is gated by server-side compilation at activate time; read-back of the stored blob proves storage, not that the rule fires). The catastrophic-yet-well-formed corruption (#275-class) passes silently.
- **Verdict: stays-rejected.** **What would flip it:** MS publishes a business-rule/workflow-XAML grammar AND sanctions an authoring write path; **OR** scope is redefined toward the genuinely-supported, non-XML **Process Trigger** lever (a `processtrigger` entity-CRUD feature: T2 = validate the referenced workflowid + event/sdkmessage exist; T3 = read back the processtrigger row) — buildable now, but **not** this candidate (no XML editor, does not touch logic) and to be assessed on its own if recurring demand appears.

---

## 5. The D2 safety bar (how every editor edits XML safely)

Every build-now/build-later editor must satisfy all three tiers. T1 (XSD validation) is **optional**, used only where a clean public XSD exists, and is shipped **as reference**, not as a runtime gate.

- **T0 — well-formed.** Parse with stdlib `xml.etree.ElementTree`; serialize back to well-formed XML.
- **T2 — grammar-aware semantic pre-flight.** Validate that referenced columns / web resources / views / relationships / dashboards / languages actually exist (live metadata read). **Protect** `classid` and fixed platform refs (e.g. `Mscrm.HideOnModern`, ChartGrid classid) — never emit any other value, never regenerate. Regenerate dependent **internal** GUIDs *consistently* (fresh `uuid4`, brace-wrapped where the family requires it) and refuse to write if a non-target external GUID would change (the form-clone guard pattern). Enforce per-grammar invariants (rowspan==row-count; layout↔fetch column match; alias-coupling; exactly-one-of content modes; child-sequence ordering).
- **T3 — read-back verify.** Read the artifact after write, re-parse, and assert the edit landed structurally un-corrupted: the new/removed node is present/absent, protected constants are intact, id-sets changed only by the intended delta, and the grammar invariant still holds against the **server-returned** XML (the server may normalize).

**Why not a pure XSD gate:** the two reference corruptions — `classid` mutation and the #275 internal-GUID collision — are **both well-formed AND XSD-valid**. An XSD check would pass them. Only grammar-aware T2 + read-back T3 catch them.

---

## 6. Editing-mechanics recommendation

### 6.1 Round-trip fidelity (MEASURED on real exported D365 XML)

Measured parse → edit one node → reserialize → diff across three strategies on the on-prem `customizations.xml` (478k; 2 FormXml, 1 RibbonDiffXml, 264 `classid` controls), the cloud `customizations.xml` (80k; SiteMap + RibbonDiffXml), a live chart, and a live dashboard formxml. **Shape constraint that drives everything: every editable sub-tree is namespace-free and prefix-free** (zero `a:tag`, zero `xsi:nil`/`xsi:type`); the only `xmlns:xsi` decl lives on the outer `ImportExportXml` root, which a sub-tree edit never reserializes.

Churn = added+removed diff lines for a single-attribute edit:

| Strategy | FormXml (on-prem) | RibbonDiffXml | Chart | Dashboard | Behavior |
|---|---|---|---|---|---|
| **stdlib `ElementTree`** | 10 (5 edit + collapse of 3 empty pairs) | 2 | 2 | 2 | Preserves attribute order, `classid` byte-for-byte, original indentation, and the `<x />` space style. Two cosmetic deviations: collapses empty `<X></X>` → `<X />` (semantically identical; only empty value/container elements), and rewrites ns prefixes to `ns0:` — **the one real bug, but MOOT here (no prefixed elements exist)**. |
| **anchored regex** | 2 (exactly 1 line changed) | 2 | 2 | 2 | Perfectly surgical, but **unvalidated text substitution** — a too-broad pattern is exactly how `classid`/GUID corruption (#275) happens. Safe only with a tightly anchored pattern **plus** a re-parse validation gate. |
| **`lxml` 6.1.1** | **6,928 (+3464/−3464)** | 14 | 2 | 2 | Preserves attribute order/classid/GUIDs but **unconditionally strips the space from self-closing tags** (`<x />`→`<x/>`) with no flag to disable — re-spaces all 3,245 self-closing tags in one FormXml. Its ns-prefix advantage confers **no** benefit (payloads have no prefixes). |

`classid` integrity (264 controls): identical to original under both stdlib and lxml. Dashboard id/name GUID pair (#275 risk): preserved by all three.

**Recommendation:** **Use stdlib `xml.etree.ElementTree` as the default mutation engine,** with anchored regex as a minimal-diff fast path for trivial single-attribute edits **behind a mandatory re-parse-and-validate gate**. **Do not adopt `lxml`** — it is the worst choice for minimal-diff fidelity here, and neither of its unique advantages (ns-prefix-faithful serialization, full XPath 1.0) is needed: payloads are namespace-free and ET's `ElementPath` already locates every target node. Never write a regex that can match `classid="{…}"` or an id/name GUID pair.

### 6.2 `lxml` PyInstaller cost — **PROXY measurement (not a real build diff)**

This is a **proxy** derived from wheel inspection + `ldd`, **not** (binary WITH lxml) − (WITHOUT).

- `lxml` 6.1.1 cp39 manylinux x86_64 wheel: 5.2 MB compressed; **9.41 MB uncompressed** content PyInstaller would carry (9.0 MB `.so` + 0.45 MB data).
- `libxml2`/`libxslt` are **statically linked** inside `etree…so` (confirmed via `ldd` — no separate `.so` bundled).
- The PyInstaller hook calls `collect_submodules('lxml')` → bundles **all** submodules (objectify 2.73 MB, html/_difflib 0.51 MB, html/diff 0.32 MB, sax 0.17 MB, builder 0.11 MB) even if only `lxml.etree` is imported. Minimum useful subset (etree + _elementpath) is ~5.1 MB, but the hook prevents reaching it without a custom `excludes` list.
- Against the current ~67 MB bundle (59 MB `_internal`), the proxy delta is **~9.4 MB ≈ 14% growth — labelled proxy.**

**Recommendation:** **Do not add `lxml`.** The ~14% proxy growth is not earned: the D6-lean bar (≥3 editors genuinely needing lxml-only XPath/fidelity) is not met. Stay stdlib-only (PyInstaller-friendly). Re-evaluate only if a future editor truly needs full XPath 1.0 or XSLT **and** the editable content becomes genuinely namespaced — with a **real build diff**, and a custom `excludes` list to drop objectify/html (cutting ~9.4 MB to ~5.5 MB).

---

## 7. Consolidated prioritized verdict table

| # | Family | Editor | Both D7 axes | Verdict | Priority | Demand |
|---|---|---|---|---|---|---|
| 1 | Forms | JS event/handler wiring | green/green | build-now | **high** | SCN-012 |
| 2 | Ribbon | hide OOB button | green/green | build-now | **high** | SCN-049 |
| 3 | Ribbon | set-rules / add-custom-rule | green/green | build-now | **high** | SCN-049 |
| 4 | Dashboards | add-chart | green/green | build-now | **high** | SCN-016 |
| 5 | Dashboards | add-view | green/green | build-now | **high** | SCN-016 |
| 6 | Charts | `update` (PATCH, create-only gap closer) | green/green | build-now | **high** | SCN-016 |
| 7 | Charts | datadescription edit (FetchXML data layer) | green/green(thin) | build-now | **high** | SCN-016 |
| 8 | SiteMap | add-area/group/subarea | green/green | build-now | **high** | SiteMap nav scenarios |
| 9 | SiteMap | remove-node (`--comment-out`) | green/green | build-now | **high** | SiteMap nav scenarios |
| 10 | Views | edit-columns (layout+fetch) | green/green | build-now | **high** | edit-existing-view (SCN-011 adjacent) |
| 11 | Views | set-order (fetch `<order>`) | green/green | build-now | **high** | edit-existing-view |
| 12 | Forms | tabs | green/green | build-now | medium | SCN-010 |
| 13 | Forms | sections | green/green | build-now | medium | SCN-010 |
| 14 | Forms | field properties (locked/disabled/showlabel/visible) | green/mixed | build-now | medium | SCN-010 |
| 15 | Dashboards | add-iframe / add-webresource | green/green | build-now | medium | SCN-016 |
| 16 | Dashboards | remove-component | green/green | build-now | medium | SCN-016 |
| 17 | Ribbon | set-label (labels + tooltips + LocLabels) | green/green | build-now | medium | ribbon label/tooltip gap |
| 18 | SiteMap | move-node | green/green | build-now | medium | reorder (MS-documented manual-edit case) |
| 19 | Views | scope/target-resolution contract | green/green | build-now | medium | enabling layer for 10/11 |
| 20 | Charts | presentationdescription (bounded appearance) | green/**yellow (no XSD)** | build-later | medium | SCN-016 |
| 21 | Views | add-filter / remove-filter | green/green | build-later | medium | edit-existing-view |
| 22 | Ribbon | set-action (JS/Url + params) | green/green | build-later | medium | command-def/JS-param gap |
| 23 | SiteMap | set-title / set-description (localized) | green/green | build-later | medium | multilingual nav |
| 24 | Forms | header/footer field placement | green/green | build-later | low | SCN-010 |
| 25 | Forms | quick-create authoring (type 7) | green/green | build-later | low | SCN-010 subset |
| 26 | Dashboards | move-component | green/green | build-later | low | SCN-016 |
| 27 | Ribbon | set-sequence | green/green | build-later | low | minor convenience |
| 28 | SiteMap | add-privilege | green/green | build-later | low | niche |
| 29 | Forms | subgrids | green/green | **needs-more-research** | medium | SCN-010 — capture per-target `<parameters>` first |
| 30 | Forms | quick-view control | green/green | **needs-more-research** | low | SCN-010 — inner-payload serializer first |
| 31 | Ribbon | app-ribbon targeting | green/mixed | **needs-more-research** | medium | absent from both test-org exports |
| — | Forms | DisplayConditions/Navigation | docs/designer-only | **reject** | low | demand + corpus absence |
| — | Dashboards | raw grid-geometry primitive | docs/designer-only | **reject** | low | D1 silent-corruption surface |
| — | Ribbon | bulk subtree clone | docs/roundtrip | **reject** | low | #275-class collision |
| — | SiteMap | generic XPath set-attr | docs/unsupported | **reject** | low | D1 |
| — | Views | generic layout primitive (implied) | — | folded into targeted verbs | — | — |
| — | Track-2 | BPF definition authoring | no/no | **stays-rejected** | — | SCN-021 / #37 |
| — | Track-2 | business-rule logic authoring | no/no | **stays-rejected** | — | reading-only demand |

### 7.1 Open / unverifiable items to settle before the relevant build
*(Several of these were cleared by the 2026-06-21 live on-prem pass — see §8.)*
- **[CLEARED §8]** ~~Dashboard IFRAME/web-resource classid~~ — confirmed live: `{FD2A7985-3187-444E-908D-6624B21F69C0}`.
- **[CLEARED §8]** ~~`savedquery.layoutjson` v9.1 presence~~ — confirmed present + populated on on-prem; B7 reconciliation applies to both targets.
- **[CLEARED §8]** ~~PATCH-target validity~~ — `IsValidForUpdate=true` confirmed on-prem for all seven write-target columns.
- **[CLEARED §8]** ~~Subgrid `<parameters>` template (G1)~~ — captured (stable ~14-key bag; two classids); confirm cloud parity before build.
- **[PROVEN §8.1 — with a caveat]** Actual on-prem **PATCH write** is **proven for `savedquery.layoutxml`+`fetchxml`** (B7): the edit persists, but is **publish-gated** — a Web API GET returns the *published* layer, so a read-back *before* `PublishXml/PublishAllXml` falsely reports a no-op. Other system-customization PATCH editors (charts, SiteMap) almost certainly share this publish-gating; confirm per-editor at build via a **publish-then-read-back** T3.
- **[STILL UNVERIFIED]** Round-trip "verbatim/byte-stable" claims for dashboards, ribbon, and SiteMap — rely on structural T3 read-back instead; do not promise byte-equality.
- **[STILL UNVERIFIED]** Managed-merge "only ONE sitemap customization between publishes" exact wording — re-confirm before citing as a hard constraint.
- **[STILL OPEN]** App-ribbon RibbonDiffXml container location (G3) — needs a solution that actually customizes the app ribbon (absent from both test orgs); quick-view inner-payload serializer (G2).

---

## 8. Live on-prem verification (2026-06-21 — `agent-on-prem` v9.1 test org, read-only)

Run *after* synthesis to clear the §7.1 `[UNVERIFIED]` dual-target flags. All checks were read-only GETs against the local on-prem v9.1 test org over the Web API. Classids below are platform constants (safe to record); no org-specific identifiers are included.

| Check | Result | Clears |
|---|---|---|
| `savedquery.layoutjson` exists + populated on v9.1 | **Yes** — GET returned a populated `layoutjson` alongside `layoutxml` | `layoutjson` is **not** cloud-only; B7's reconciliation requirement applies to **both** targets |
| Dashboard ChartGrid classid | `{E7A81278-8635-4D9E-8D4D-59480B391C5B}` — 96 live controls | B4/B10 protected constant confirmed on-prem |
| Dashboard IFRAME / web-resource classid | **`{FD2A7985-3187-444E-908D-6624B21F69C0}`** — live tile | **B10 `[UNVERIFIED]` → confirmed**; safe to hard-code as a protected constant |
| SiteMap SubArea content attributes | `Url` / `Entity` / `DefaultDashboard` present; **no `WebResource` attribute** anywhere | B6 verifier correction confirmed live (web-resource subarea = `Url`) |
| `IsValidForUpdate` on every PATCH target | **`true`** for `savedquery.{layoutxml, layoutjson, fetchxml}`, `savedqueryvisualization.{datadescription, presentationdescription}`, `sitemap.sitemapxml`, `systemform.formxml` | every PATCH-based editor's write **target** is valid on v9.1 (read-only proof; substitutes for a write probe) |
| Subgrid `<parameters>` template (gate G1) | Captured — a **stable ~14-key bag**: `ViewId, IsUserView, RelationshipName, TargetEntityType, AutoExpand, EnableQuickFind, EnableViewPicker, ViewIds, EnableJumpBar, ChartGridMode, VisualizationId, IsUserChart, EnableChartPicker, RecordsPerPage`. Two form-subgrid classids: `{E7A81278-…}` (+`HeaderColorCode`) and `{02D4264B-47E2-4B4C-AA95-F439F3F4D458}` (reference-panel, +`ReferencePanelSubgridIconUrl`) | G1 materially de-risked — the bag is regular, not free-form (still confirm cloud parity before build) |

**Gates G2/G3 remain open** (deferred to build time): **G2** quick-view inner-payload serializer; **G3** app-ribbon RibbonDiffXml container (needs a solution that actually customizes the app ribbon; absent from the test orgs).

### 8.1 Live WRITE test — `savedquery` PATCH on-prem (B7), reversible

Exercised B7's exact write path end-to-end on the on-prem v9.1 test org, fully self-contained (create throwaway view → PATCH → read-back → delete; both throwaway views removed afterward, org left clean).

**Procedure.** Created a disposable public view (`name:300, telephone1:100`), then PATCHed `layoutxml`+`fetchxml` to add an `emailaddress1` column to **both** (the B7 mismatch-invariant edit) via `entity update --data-file`, using stdlib ElementTree to build the payload (the recommended engine, §6).

**Result — the write path works, but read-back is publish-gated:**

| Step | Observation |
|---|---|
| PATCH `layoutxml`+`fetchxml` | HTTP success (`ok:true`) |
| GET immediately after PATCH | **Returned the OLD value** — `emailaddress1` absent (looks like a silent no-op) |
| `PublishAllXml`, then GET | **`emailaddress1` now present** in both `layoutxml` and `fetchxml` — the edit had persisted all along |
| Control: PATCH a plain `name` column | Same pre-publish staleness → behavior is **layer/publish-gating, not column-specific** |

**Conclusion (B7, and by extension B5/B6 — system-customization PATCH editors):**
1. On-prem v9.1 **accepts** the `savedquery.layoutxml`+`fetchxml` PATCH; the data persists.
2. A Web API **GET returns the published layer**, so an immediate post-PATCH read-back is a **false negative**. The editor MUST publish (`PublishXml`/`PublishAllXml`) and **T3 read-back must run AFTER publish** — otherwise it wrongly reports the edit failed. This is the single most important on-prem build-time gotcha surfaced; cloud-green would not have caught it. (It generalizes the §3.3 charts "publish-required-for-system" note to views, and adds the read-back **ordering** requirement.)
3. *(Operational note, not a finding: the repo's destructive-op gate blocks bare `crm entity delete`; cleanup needs `--yes` or a script-file invocation.)*
