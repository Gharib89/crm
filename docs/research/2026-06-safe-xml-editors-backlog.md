# Safe Customization-XML Editors — Review-Gated Build Backlog

> **NOT YET FILED TO GITHUB.** These are proposed build items awaiting user review. Nothing here has been created as an issue or branch. Capture-don't-implement: review, prune, and approve before any item is filed or built. Each item names the verb sketch, the mandatory D2 safety bar (T0/T2/T3), the dual-target note, and a priority. All editors use stdlib `xml.etree.ElementTree` (regex only behind a re-parse gate); **do not add `lxml`** (measured ~14% bundle growth, no XPath/fidelity benefit on namespace-free payloads).

**Global safety bar (applies to every item):**
- **T0** — parse + re-serialize well-formed (stdlib ElementTree).
- **T2** — grammar-aware pre-flight: validate referenced objects EXIST; **protect** `classid`/fixed platform refs (never mutate/regen); regenerate dependent **internal** GUIDs consistently and refuse to write if a non-target external GUID would change.
- **T3** — read back after write; assert the edit landed structurally un-corrupted (node present/absent, protected constants intact, id-set delta == intended, grammar invariant holds on the **server-returned** XML).
- A pure XSD gate is insufficient (classid mutation and #275 GUID collision are both well-formed AND XSD-valid). Ship XSDs as reference; no runtime XSD dependency.

---

## Priority 1 — build-now / high

### B1. Form JS event/handler wiring (SCN-012)
- **Verbs:** `form add-library --library <webresource>`; `form add-handler --event onload|onsave|onchange [--field <attr>] --library --function [--param]... [--pass-context/--no-pass-context] [--no-enabled]`; `form remove-handler --event … --function [--field]`; `form list-handlers`.
- **T2:** referenced web resource **EXISTS** (GET webresourceset) — the editor references it, never creates it (creating is the unsupported path); for onchange, `--field` is a real on-form attribute; fresh handlerUniqueId/libraryUniqueId; **preserve handler ORDER**; **merge `<Handlers>` into the existing stock `<event name=…>`, never append a duplicate `<event>`**; **target `<Handlers>`, never the sibling `<InternalHandlers>`**; dedupe `<Library>`; never touch classid.
- **T3:** parse; assert `<Handler functionName=… libraryName=…>` under the right `<event>` (right control for onchange) with the fresh GUID; remove asserts absent.
- **Dual-target:** schema parity via shared `FormXml.xsd`; on-prem Handler shape confirmed historically but **[verify on-prem live before ship]**. Events editor must not silently re-encode any sibling encoded-text parameter bag.

### B2. Ribbon hide OOB button (SCN-049)
- **Verbs:** `ribbon hide-button ENTITY --target-id <OOB Button Id> [--method display-rule|hide-action]`; default `display-rule` (reversible).
- **T2:** `--target-id` resolves in the live composed ribbon (RetrieveEntityRibbon/RetrieveApplicationRibbon) — a typo silently no-ops; for `display-rule`, emit a CustomAction whose **`Location` == the OOB element's Id** with `Mscrm.HideOnModern` + `Mscrm.ShowOnlyOnModern` (always-false; reuse the platform rule ids verbatim — fixed refs); gate `hide-action` (`HideCustomAction`, **one-way trapdoor**) behind explicit irreversibility confirm; never mutate classid/Command/TemplateAlias; **emit a warning** that OOB-command reuse is on unsupported ground.
- **T3:** RetrieveEntityRibbon — target absent (hide-action) or carries the two false DisplayRules; assert by **parsed value, not bytes** (customizations.xml is reserialized).
- **Dual-target:** identical import path on both; SkuRule available in-grammar for target-specific hides.

### B3. Ribbon edit enable/display rules (SCN-049)
- **Verbs:** `ribbon set-rules ENTITY --command-id <id> --enable-rule <RuleId>... --display-rule <RuleId>...`; `ribbon add-custom-rule ENTITY --command-id <id> --webresource <js> --function <fn>`.
- **T2:** platform `Mscrm.*` ids from a curated allow-list (unknown id silently no-ops); custom rule → `$webresource:` EXISTS (reuse `_check_webresource_refs`); ValueRule `--field` is a real column; never touch CommandDefinition Id; warn on OOB-command reuse.
- **T3:** exported CommandDefinition's EnableRule/DisplayRule set matches exactly (no drop/reorder); assert by parsed value.
- **Dual-target:** identical on both; CommandClientTypeRule/SkuRule in-grammar for intentional divergence.

### B4. Dashboard add-chart + add-view (SCN-016)
- **Verbs:** `dashboard add-chart <dashboard-id> --view <savedquery> --chart <visualization> [--tab --section --rowspan --colspan]`; `dashboard add-view <dashboard-id> --view <savedquery> [--mode list|all] [--records-per-page] [layout flags]`.
- **T2:** **protect ChartGrid classid `{E7A81278-…}`** (MS-documented constant); fresh cell id; validate TargetEntityType (entity), ViewId (savedquery of that entity), VisualizationId (**org-owned**, primaryentity matches); enforce `AutoExpand=Fixed`; `IsUserView=false` for grids; **rowspan == count of `<row>`**; tab ≥ 1 section; refuse > 6 components (default cap) unless `--force`.
- **T3:** re-GET formxml — classid intact, refs landed verbatim, cell-count +1, no pre-existing cell id mutated, sections satisfy rowspan==row-count.
- **Dual-target:** classids + rowspan/colspan grammar identical per docs and live read-back. Publish via existing `maybe_publish` (PublishAllXml). Round-trip is **structural, not byte-verbatim** — do not promise byte-equality.

### B5. Chart `update` (PATCH) + datadescription editors (SCN-016)
- **Verbs:** `chart update <id> [--data-description FILE] [--presentation-description FILE] [--name] [--description] [--type] [--user] [--solution] [--publish]`; then `chart set-fetch <id> --fetch FILE`; `chart add-series <id> --column --aggregate --alias`; `chart remove-series <id> --alias`; `chart set-groupby <id> --column [--dategrouping …]`.
- **T2:** **every inner `<fetch>` `<attribute name>`/`<entity name>` EXISTS** (metadata) — the un-validated FetchXML island is the real risk; **ALIAS-COUPLING**: every datadescription `<measure alias>` ↔ a fetch attribute alias ↔ a presentationdescription `<Series>` (partial update reads the other column live and validates the pair); add-series caps — **≤5 series (non-comparison); comparison = 1 series/2 categories**; protect primaryentitytypecode (no re-homing).
- **T3:** re-GET; provided fields landed, unprovided column unchanged; re-run alias-coupling + fetch-column-exists on the server doc.
- **Dual-target:** doc-consistent + matches prior live read-backs **[byte-level parity UNVERIFIED]**. **Publish is REQUIRED for system charts** (`savedqueryvisualization` → PublishAllXml; default/strongly prompt; verify published state in T3); **user charts do not publish**. Web-resource branch inherits the ~16k-char base64 cap; direct column PATCH does not.

### B6. SiteMap add-node + remove-node
- **Verbs:** `sitemap add-area <sitemap> --id --title [--icon $webresource:..] [--show-groups]`; `sitemap add-group <sitemap> --area --id --title`; `sitemap add-subarea <sitemap> --area --group --id (--entity <logical> | --url <u> [--pass-params] | --dashboard <guid>) [--title] [--icon]`; `sitemap remove-node <sitemap> --id <nodeId> [--comment-out]`.
- **T2:** parent Id exists; new Id matches `[a-zA-Z0-9_]+` and is unique (Area among Areas; Group within parent Area; **tool-enforced for SubArea**; publisher-prefix recommended); **for `--entity` the logical name EXISTS** (dangling Entity= silently hides the node); `--dashboard` GUID exists; **exactly-one-of {entity, url, dashboard}** (web-resource subarea is a `--url` to an HTML web resource — there is **no** SubArea WebResource attribute; `$webresource:` is the **Icon** directive only); never touch `ResourceId`/`IntroducedVersion`; remove warns on Area/Group cascade; comment-out stays well-formed; **no internal GUIDs → #275 class absent**.
- **T3:** re-GET sitemapxml — spliced node present under named parent (or absent / only-in-comment for remove); **Id-set after == before ∪ {new}** (required gate; byte-stability not promised).
- **Dual-target:** shape identical per docs; **[verify on-prem PATCH+read-back before ship]** (cloud silently rewrites inputs on-prem rejects).

### B7. View edit-columns + set-order
- **Verbs:** `view edit-columns <entity> <view> [--query-type T] --add 'logicalname[:width]' | --remove <logical> | --reorder <csv> | --width <logical>:<int>`; `view set-order <entity> <view> [--query-type T] --order '<attr> [asc|desc]' | --add-order … | --clear-order`.
- **T2:** **MISMATCH INVARIANT** — every non-PK layoutxml `<cell name>` ↔ a fetchxml `<attribute name>`; add MUST add both, remove MUST remove both, never edit layoutxml alone; each added column EXISTS (metadata); keep PK cell+attribute; width `nonNegativeInteger` > 0; set-order attribute EXISTS, `descending ∈ {true,false}`, protect filter/condition/link-entity siblings; **resolve EXACTLY ONE savedquery** by name+returnedtypecode+querytype (no alt-key); **check `IsCustomizable.Value` before PATCH**; **whitelist editable querytypes** and skip/error on layoutxml-less system queries (e.g. querytype 8192).
- **T3:** **PUBLISH then read back** — `PublishXml` (or `PublishAllXml`) the savedquery BEFORE the read-back. **Proven on-prem 2026-06-21 (report §8.1): a Web API GET returns the *published* layer, so a read-back before publish falsely reports a no-op even though the PATCH succeeded.** Then GET layoutxml, fetchxml, **layoutjson** — cell-name set+order as expected, width landed, **re-run mismatch check** on the server doc; read back by resolved **savedqueryid**. Reconcile `layoutjson` (null/clear so the server rebuilds, or rebuild) — **fidelity is HIGH for layoutxml+fetchxml only until layoutjson is reconciled**.
- **Dual-target:** **on-prem WRITE path proven** (§8.1) — PATCH accepted + persists, publish-gated read-back. layoutxml/fetchxml shape identical on both (cosmetic export-only difference does not touch editable fields). Help text: editing OOB/managed views creates an unmanaged layer upgrades may revert.

---

## Priority 2 — build-now / medium

### B8. Form tabs + sections
- **Verbs:** `form add-tab|remove-tab|rename-tab|move-tab`; `form add-section|remove-section|rename-section|move-section` (with `--columns`, `--label`, `--after`).
- **T2:** fresh tab+section id (brace-wrapped, matching `_fresh_cell_id`); `IsUserDefined=1`; emit non-empty columns/section skeleton (empty tab is XSD-valid but renders broken); columns 1–4; reuse `_resolve_target_section`; refuse removing the only tab or a tab/section holding bound controls (or `--force` surfacing orphans).
- **T3:** assert named tab/section present/absent; sibling GUIDs untouched (reuse the clone-id guard).
- **Dual-target:** identical `/tabs/tab` + `/sections/section` grammar in both exports.

### B9. Form field properties (NOT required-level)
- **Verb:** `form set-field-props <attr> [--locked/--unlocked] [--disabled/--enabled] [--show-label/--no-show-label] [--visible/--hidden]`.
- **T2:** field on form (reuse `_find_field_control`); toggle the existing cell/control attribute in place (no GUID/classid/ref surface); **explicitly EXCLUDE required-level → route to attribute metadata** so "set required" never silently no-ops at the form layer.
- **T3:** assert the attribute value flipped on the right cell/control.
- **Dual-target:** locklevel/disabled/showlabel present in both exports. Among the safest editors in the set.

### B10. Dashboard add-iframe / add-webresource + remove-component
- **Verbs:** `dashboard add-iframe <id> --url <url> [--security] [--scrolling --border --pass-parameters] [layout]`; `dashboard add-webresource <id> --webresource <name-or-id> [layout]`; `dashboard remove-component <id> --cell-id | --index | --view | --chart | --url`.
- **T2:** **protect IFRAME/webresource classid `{FD2A7985-3187-444E-908D-6624B21F69C0}`** (confirmed live on on-prem v9.1, 2026-06-21); **`<Url>` non-empty**; web resource EXISTS + warn if not form-enabled; typed bool params; remove keeps rowspan==row-count (drop matching empty `<row/>`), refuses ambiguous target.
- **T3:** classid intact, `<Url>` == requested / target gone, cell-count ±1, other ids+classids unchanged, sections satisfy rowspan==row-count.
- **Dual-target:** classid + param set per docs/live; user-dashboard security semantics out of scope (verbs target org/SystemForm dashboards).

### B11. Ribbon set-label (labels + tooltips + LocLabels)
- **Verb:** `ribbon set-label ENTITY --button-id <id> --label <text> [--tooltip-title] [--tooltip-description] [--lcid <int>]`.
- **T2:** target is a custom Button; protect Command/TemplateAlias/Sequence/Id; localized text via **`$LocLabels:<LocLabel-Id>`** with text in `<LocLabels>/<LocLabel Id=…>/<Titles>/<Title languagecode=… description=…>`; map `--lcid` to `<Title>` `languagecode`; validate `--lcid` against provisioned languages; **LocLabel Ids are CASE-SENSITIVE**.
- **T3:** exported Button has new LabelText/ToolTip*, correctly XML-escaped (round-trip a string with `&`/`<`/quotes); LocLabel row for the lcid exists; assert by parsed value.
- **Dual-target:** identical on both.

### B12. SiteMap move-node
- **Verb:** `sitemap move-node <sitemap> --id <nodeId> (--before <siblingId> | --after <siblingId> | --index N)`.
- **T2:** node + anchor share the same parent and the same node type; index in range; move only — never mutate the node's attributes/children.
- **T3:** moved node at requested position; parent's child-Id multiset unchanged (pure permutation); no attributes changed.
- **Dual-target:** identical; the canonical case MS documents as needing a manual XML edit.

---

## Priority 3 — build-later

### B13. Chart presentationdescription (bounded appearance) — **rides on undocumented serialization**
- **Verbs:** `chart set-type <id> --type <…>`; `chart set-title <id> --text`; `chart set-colors <id> --series <alias> --color <#rgb>`.
- **T2:** **no XSD** — build to observed grammar + curated allow-list; T0 well-formed; structural assertions against the known element set; bound enums (ChartType); target an existing `<Series>` (never add/remove in isolation); **pass through unmodeled attributes/CustomProperties/Font verbatim**.
- **T3:** targeted attr changed AND Series count/aliases unchanged; alias-coupling vs datadescription holds.
- **Dual-target:** observed identical on both; must be called out as riding on an undocumented .NET serialization. Defer behind the datadescription editor (B5).

### B14. View add-filter / remove-filter — **larger condition grammar**
- **Verbs:** `view add-filter <entity> <view> --condition '<attr> <op> [value]' [--type and|or]`; `view remove-filter … --condition '<attr> <op>'`.
- **T2:** condition attr EXISTS; operator ∈ `fetch.xsd` enum; value type/cardinality matches operator (`in`→children, `null`→none, `between`→2); **some operators are version-gated on v9.1** — validate or let backend reject (T3 query-probe); protect existing conditions/link-entity.
- **T3:** GET fetchxml — new/removed condition present/absent (attr+op+value), siblings preserved; optional one query to confirm the backend accepts.
- **Dual-target:** structurally identical; operator availability is the only divergence risk.

### B15. Ribbon set-action (JS/Url + params) — **positional-param footgun**
- **Verbs:** `ribbon set-action ENTITY --command-id <id> --webresource <js> --function <fn> [--param-string]... [--param-crm <enum>]... [--param-bool] [--param-int] [--param-decimal]`; `ribbon set-url-action ENTITY --command-id <id> --address <url>`.
- **T2:** `$webresource:` EXISTS; **preserve positional param ORDER (unnamed; must match function arg order — never sort)**; CrmParameter Value from documented enum; never touch CommandDefinition Id; warn on OOB-command reuse.
- **T3:** exported Actions — FunctionName/Library == requested; param child sequence byte-identical in order+type; assert by parsed value.
- **Dual-target:** identical on both.

### B16. SiteMap set-title / set-description (localized) — **strict child-sequence**
- **Verbs:** `sitemap set-title <sitemap> --id <nodeId> --lcid <n> --title <…>` (repeatable); `sitemap set-description …`.
- **T2:** LCID is a 4-digit installed language (cross-check live languages); node Id exists; one Title per LCID (dedup is a tool rule); **respect child sequence Titles → Descriptions → child nodes** (naive append = schema-invalid import); never touch `ResourceId`.
- **T3:** node has `<Titles><Title LCID=… Title=…>` matching input and element order is schema-valid.
- **Dual-target:** grammar identical; LCID availability is per-org.

### B17. Form header/footer field placement
- **Verbs:** `form add-header-field <attr> [--locklevel]`; `form remove-header-field <attr>` (+ footer variants).
- **T2:** thin retarget of add/remove-field cell surgery onto `<header>`/`<footer>`; reuse classid resolution + duplicate guard; validate attribute exists; classid protected; fresh cell id; warn over the ~4-cell classic header limit.
- **T3:** assert control present/absent under `<header>`/`<footer>`.
- **Dual-target:** header shape confirmed on-prem historically; identical grammar.

### B18. Form quick-create scaffold (type 7)
- **Verb:** `form create --type quickcreate` (the existing FormXml field/section editors already accept `--form` to target a type-7 form).
- **T2/T3:** reuse field/section T2/T3; quick-create layout constraints (single column, limited tabs) are not XSD-enforced — guard in code.
- **Dual-target:** `FORM_TYPE_BY_NAME` already maps quickcreate=7.

### B19. Dashboard move-component — **highest bookkeeping**
- **Verb:** `dashboard move-component <id> --cell-id <id> --to-tab <t> --to-section <s> [--rowspan --colspan]`.
- **T2:** **preserve the moved cell id/control/classid/params** (move ≠ rebuild — regen nothing); validate destination (reuse `_resolve_target_section`); re-satisfy rowspan==row-count in BOTH source and destination.
- **T3:** moved cell byte-identical to pre-move, now under requested tab/section, total cell-count unchanged, both sections satisfy rowspan==row-count.
- **Dual-target:** identical. Ship after add/remove land and the rowspan helper is battle-tested.

### B20. Ribbon set-sequence
- **Verb:** `ribbon set-sequence ENTITY --button-id <id> --sequence <int>`.
- **T2:** target is a custom CustomAction/Button; only `@Sequence`; optionally surface neighbour sequence values for the in-between gotcha.
- **T3:** exported `@Sequence` == requested.
- **Dual-target:** identical. Overlaps add-button `--sequence`; fold in only if a generic ribbon-set-attribute seam emerges.

### B21. SiteMap add-privilege
- **Verb:** `sitemap add-privilege <sitemap> --subarea <id> --entity <logical> --privilege Read[,Write,...]` (best as a flag on the SubArea editor).
- **T2:** SubArea exists; Entity exists; **Privilege ∈ full enum** (Read\|Write\|Append\|AppendTo\|Create\|Delete\|Share\|Assign\|All\|AllowQuickCampaign\|CreateEntity\|ImportCustomization\|UseInternetMarketing; comma-list, no spaces); respect the SubArea child sequence.
- **T3:** SubArea carries `<Privilege Entity=… Privilege=…>` matching input.
- **Dual-target:** identical.

---

## Pre-build research gates (do NOT build until resolved)

*Status updated by the 2026-06-21 live on-prem read-only pass (see feasibility report §8).*

- **G1 — Form subgrids (PARTLY CLEARED):** the `<parameters>` bag was captured live on on-prem — a **stable ~14-key set** (`ViewId, IsUserView, RelationshipName, TargetEntityType, AutoExpand, EnableQuickFind, EnableViewPicker, ViewIds, EnableJumpBar, ChartGridMode, VisualizationId, IsUserChart, EnableChartPicker, RecordsPerPage`), with two form-subgrid classids `{E7A81278-…}` (+`HeaderColorCode`) and `{02D4264B-47E2-4B4C-AA95-F439F3F4D458}` (reference-panel, +`ReferencePanelSubgridIconUrl`). Remaining: confirm the **cloud** bag matches before designing T2 (the bag is regular, not free-form — risk downgraded).
- **G2 — Form quick-view (OPEN):** design the HTML-encoded inner `<QuickFormIds>` serializer and capture a cloud exemplar before building.
- **G3 — Ribbon app-ribbon targeting (OPEN):** the app-ribbon RibbonDiffXml container was absent from both test-org exports; capture a real export carrying an app-ribbon customization and confirm its location + round-trip before designing the node-locator. Read-back is via RetrieveApplicationRibbon.
- **G4 — Dual-target on-prem verification (LARGELY CLEARED):** read-only on-prem v9.1 confirmed — `IsValidForUpdate=true` on all PATCH targets; `savedquery.layoutjson` present+populated (B7 applies to both targets); dashboard IFRAME classid `{FD2A7985-3187-444E-908D-6624B21F69C0}` confirmed (B10); SiteMap SubArea content attrs `Url`/`Entity`/`DefaultDashboard`, no `WebResource` attr (B6). **WRITE path proven for B7** (report §8.1): on-prem PATCH of `savedquery.layoutxml`+`fetchxml` is accepted and persists, but is **publish-gated** — `PublishXml` before the T3 read-back or it false-negatives (this almost certainly applies to B5 charts and B6 sitemap too — build each with publish-then-read-back T3). **Remaining:** confirm the publish-gated write per-editor for B5/B6 at build time; re-confirm the managed-merge "one sitemap customization between publishes" wording.

## Explicitly rejected (do not build)
- Forms DisplayConditions/Navigation (designer-only; demand + corpus absence).
- Dashboard raw grid-geometry primitive (D1 silent-corruption surface; invariants folded into add/remove/move).
- Ribbon bulk subtree clone (#275-class collision; keep the refusal — safe shape is a per-button id-rebasing add).
- SiteMap generic XPath set-attr (D1).
- Track-2: BPF definition authoring and business-rule logic authoring (no published grammar, no sanctioned logic-write path). Recommended off-ramps are non-XML: a `bpf instance` family and a `processtrigger` CRUD feature — assess separately only if recurring demand appears.
