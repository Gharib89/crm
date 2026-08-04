# D365 Web API quirks

Non-obvious Dynamics 365 CE / Dataverse platform behaviors that shaped `crm`'s
implementation — each live-confirmed against a real org (on-prem v9.1 and/or
Dataverse online v9.2) and keyed to the issue that surfaced it. Read the
relevant section before touching a command in that domain; several of these
produce **false-green mocked tests** (the mock can't know the platform rejects
the shape), so they recur if rediscovered from scratch.

## On-prem v9.x platform behaviors

### 1. `appmodule` (model-driven app) publish + read mechanics (#809)

A Web-API-created appmodule is **Unpublished**, and the plain `appmodules`
collection / by-id GET return **published apps only** — a fresh app reads back
"Does Not Exist". Four hard facts:

- Read the unpublished view via the **collection function**
  `GET appmodules/Microsoft.Dynamics.CRM.RetrieveUnpublishedMultiple()?$filter=…`.
  The by-id `appmodules(<id>)/…RetrieveUnpublished()` is **not bound** to
  appmodule on on-prem v9.1 (errors "does not support entities of type
  'appmodule'") even though MS Learn's bound-entities table lists it. All app
  reads (create read-back, resolve, find-live) go through
  RetrieveUnpublishedMultiple + `$filter`.
- **`PublishAllXml` does NOT flip an appmodule to Published** — only an
  app-scoped `POST PublishXml` with
  `<importexportxml><appmodules><appmodule>{id}</appmodule></appmodules></importexportxml>`
  does.
- Targeted `PublishXml` runs `ValidateApp`; an app with no bound sitemap
  **fails** with `0x80050114` ("App does not contain Site Map"). The
  `sitemapnameunique == app.uniquename` link is insufficient — the sitemap must
  be bound as an app **component (type 62)** via `AddAppComponents`.
- A bare app (no sitemap) is therefore unpublishable — `crm app create
  --publish` keeps `PublishAllXml` (surrounding customizations); the app-scoped
  publish lives in the `apply` apps phase, gated on the app having a sitemap.
  `ValidateApp(AppModuleId=<id>)` (GET) is the pre-publish check.

### 2. Ribbon buttons are not API-creatable; unpublished diffs don't export (#142, #766)

Command-bar buttons live as `<RibbonDiffXml>` in `customizations.xml`; the only
path is export → edit XML → import → publish (`crm ribbon` automates it).
Additionally, an **unpublished** RibbonDiffXml is **not carried by
`ExportSolution`** (unlike forms/views, whose unpublished edits round-trip) — so
ribbon edits do **not compose across separate verb invocations** without a
publish between each; the empty-diff error says "publish first" (#766; real
compose fix tracked as a local working-copy flow, #773). `hide-button` differs:
it reads the live composed ribbon via `RetrieveEntityRibbon`, not the exported
diff.

On-prem also **permits deleting a webresource still referenced by a published
ribbon button** (online blocks it, `0x8004f01f`), leaving a dangling
`$webresource:` ref that `solution validate` correctly rejects on the next
ribbon import. In shared-fixture teardown: `ribbon remove` the referencing
button **before** deleting the referenced webresource.

### 3. Solution-import dependency failures split into two classes (#182)

(a) **Declared** `<MissingDependency>` → faults loudly upfront (HTTP 500,
`0x80048033`), async and sync alike. (b) **Undeclared dangling reference**
(parent stripped from the zip, nothing declared) → imports as **full success**,
even via sync `ImportSolution` — dangling lookup created, every per-component
row `success`, importjob clean. No envelope fix detects class (b); only
post-import read-back verification can.

### 4. `ImportSolutionAsync` rejects `ImportJobId` and creates no importjob row on v9.1 (#182)

`crm solution import` auto-falls back to sync `ImportSolution` with the same
client GUID (envelope `action:"ImportSolution"`, `async_operation_id:null`).

### 5. `RemoveSolutionComponent` takes a `SolutionComponent` entity-reference BODY (#181)

Not a `ComponentId` scalar param. Generalizes: Web API **unbound actions**
reject SDK-style scalar param names — check MS Learn for the action's real body
shape.

### 6. Business-rule activation via `statecode` PATCH is blocked server-side (`0x80045002`)

Platform constraint, not a CLI bug.

## Both targets

### 7. Global option set retrieve must be a BARE GET (#179, #205)

`GET GlobalOptionSetDefinitions(Name='…')` with no `$select`/`$expand` —
`$expand=Options` → HTTP 400 on both targets, because the entity set is
base-typed and `Options` is a **complex-type collection on the derived type,
not a nav property**. The bare GET serializes the full derived type. Contrast:
attribute-metadata reads CAST to the typed attribute subtype and then
`$expand=OptionSet,GlobalOptionSet` — those ARE nav props. Rule: `$expand` nav
prop = OK; `$expand` complex-type collection = 400.

### 8. `workflow` rows: type 1=Definition, 2=Activation copy, 3=Template (#160, #165)

Activating spawns a type=2 copy **sharing the definition's `name`**
(`parentworkflowid` set only on the copy). Any list/group/dup-detect over raw
rows flags every activated workflow as a duplicate — operate on **type=1 only**
(`crm/core/workflow.py::list_workflows` already filters; reuse it). State-change
or delete on a type=2 GUID → `0x80045003` / `0x80045004`.

### 9. `Workflows/*.xaml` in solution zips is often UTF-16 with BOM (#163)

Blind `decode("utf-8")` yields NUL-laden text and content regexes silently miss
everything — the feature no-ops on a real org while UTF-8 mocks pass. Decode by
BOM (`solution_validate._decode_xaml`); tests need a `.encode("utf-16")`
fixture. Applies to any serialized D365 export member. Also guard extracted
values against a GUID regex before interpolating into an OData `$filter`.

### 10. HTTP 412 is overloaded — not only an ETag conflict (#232)

A 412 body can carry a real D365 error code — notably `0x80060892`
(alternate-key uniqueness violation). Apply the generic `PreconditionFailed`
label ONLY when the body yielded no code. Duplicate taxonomy (all distinct,
detect on the CODE): `0x80060892` alternate-key, `0x80040237`
DuplicateRecord/SQL integrity, `0x80040333` duplicate-detection rule.

### 11. `form clone` collides on FormXml-INTERNAL GUIDs, not the root form id (#275)

Uniqueness is enforced across the form's internal registration GUIDs
(`labelid`, layout `id`, `uniqueid`, `handlerUniqueId`, `libraryUniqueId`) —
regenerate ALL braced form-internal GUIDs with a consistent old→new mapping per
clone, but **never rewrite `classid`** (fixed control-type platform constants),
`Role Id`, `ViewId`, or `QuickFormId`.

### 12. `update-relationship` must PUT-replace the UN-cast path (#267)

`RelationshipDefinitions(<MetadataId>)` — the cast subtype path returns 405 on
both targets. The PUT body MUST carry the `@odata.type` discriminator, but a
minimal-metadata cast GET can omit it — inject it during retrieve-merge-write.
Contrast quirk 7: optionset/attribute READS cast then expand; relationship
WRITES go to the un-cast set.

### 13. Polling `importjobs(<id>)` online can transiently return `0x80040217` before the row commits (#276)

A race, not a real failure. Classify via `classify_d365_error`
(`0x80040217 → not_found` regardless of HTTP status) and tolerate/retry
`not_found` — a narrow `status == 404` check misses the code riding a non-404
status. (`0x80040217` is the generic "does not exist" code — it also surfaces
as the "solution unique name not valid" 404.)

### 14. `RetrieveTotalRecordCount` keys on the entity LOGICAL name (#314)

The CLI resolves user input (logical OR set name, case-insensitive) through the
cached `NameMap.resolve` before the call. Reuse pattern: a verb whose backing
API wants a logical name resolves through `NameMap`, never pushes the
distinction onto the user. Still a server-CACHED count; `query odata --count`
is the live path.

### 15. `$metadata` (CSDL) is served as XML only (#266)

The default `Accept: application/json` makes Dataverse answer HTTP 415 on both
targets. Any CSDL fetch must override `Accept: application/xml` for that single
GET (`crm/core/metadata.py::_fetch_csdl`).

### 16. Not every SDK message is a Web API action (#435 → #453, ADR 0003)

Some messages are SOAP-only and absent from the Web API entirely (e.g.
`ConvertDateAndTimeBehavior`: zero CSDL hits, `action invoke` → "Resource not
found for the segment", no MS Learn webapi reference page). Before building a
command around an SDK message, grep the live CSDL (`metadata list-actions` /
`list-functions`). Contrast `RetrieveMetadataChanges` — a real unbound Function
(powers `metadata changes`, #455) taking the EntityQueryExpression as raw
un-quoted JSON via parameter alias.

### 17. Web API `pluginassemblies` create does NOT auto-create `plugintype` rows (#506, #517)

Only the Plug-in Registration Tool reflects client-side and creates them; a raw
upload of a valid signed `IPlugin` leaves zero plugintypes. To register a step
you must `POST plugintypes` explicitly (`typename` + `friendlyname` +
`pluginassemblyid@odata.bind`; `publickeytoken`/`version`/`culture` are
read-only, derived from the bound assembly). Online forces sandbox isolation
(mode 2), which validates the registered `publickeytoken` against the uploaded
content — supply the DLL's real strong-name token, never the `"null"` default.

### 18. The workflow-create wall (`0x80045040`) is XAML-PROVENANCE-sensitive, not target-sensitive

Live-confirmed identical on both targets (2026-06-24): a raw `POST workflows`
with hand-authored/foreign XAML → `0x80045040` on **both**; `crm workflow
clone` (reusing an existing workflow's real designer XAML) creates a draft
successfully on **both**, including online. The platform gate accepts XAML it
recognizes as designer-produced — it is NOT "cloud blocks all create". MS docs
saying "XAML create supported on-prem, not with Dataverse" refer to the SOAP
SDK + hand-authored-XAML case. Cloning a **system** workflow still 500s
(`0x80040216`, internal-GUID collision — quirk 11's class). Feasibility doc:
`docs/superpowers/specs/2026-06-24-workflow-create-update-feasibility.md`.

### 19. `workflow update`: published-edit lock, no `triggeronupdate` column, type=2 orphans (#538, ADR 0013)

- Editing an ACTIVATED definition → `0x80045002` ("Cannot update a published
  workflow definition") — the generic can't-mutate-a-published-process gate
  (same code as quirk 6). `update_workflow` tries the direct PATCH first and
  only on `0x80045002` drives deactivate → edit → reactivate.
- There is NO `triggeronupdate` boolean: the writable trigger columns are
  `triggeroncreate`, `triggerondelete` (bools), and
  `triggeronupdateattributelist` (memo; non-empty = on-update enabled).
- On-prem, deactivating does NOT delete the type=2 activation copy, and
  deleting the type=1 parent does not cascade — an orphaned type=2 row is
  UI-only deletable (`0x80045004`). E2e consequence: throwaway-clone tests
  never activate. `workflow_scope` enum: 1=User, 2=BusinessUnit,
  3=Parent:Child BUs, 4=Organization.

### 20. Security-role privileges: unbound read; the baseline can't be stripped (#551)

- Read via the UNBOUND `GET RetrieveRolePrivilegesRole(RoleId=<guid>)` — the
  bound form 404s. On-prem returns no `PrivilegeName` (only Depth +
  PrivilegeId). PrivilegeDepth: Basic=0/Local=1/Deep=2/Global=3/RecordFilter=4
  (serialized as member NAME; normalize int|str).
- Every role auto-gets **immovable baseline privileges** (the SharePoint
  document-management set) that `ReplacePrivilegesRole` cannot remove — a
  strict set-equality reconcile never converges. `apply` uses
  **subset-satisfaction** (skip when every declared privilege is present at
  declared depth); a removal-only spec change is a no-op.

### 21. A Create-message entity image must set `messagepropertyname="Id"`, not `"Target"`

The platform rejects a Create image keyed on `Target`. MS Learn's
image-message table conflates the **request** property (`Target`) with the
**image** property (`Id` for Create) — verify against a live registration, not
the table. Only a **post**-image is valid on Create. All other messages: image
property == request property.

### 22. `MSCRM.SolutionUniqueName` is ADDITIVE, not steering (ADR 0020, #636)

Every new customization component is ALWAYS added to the system Default
Solution; the header only ALSO files it into the named unmanaged solution. A
component created with no solution context lands only in the Default Solution —
orphaned / not exportable. Design consequences (explicit `--solution` required
for customization writes) are recorded in ADR 0020.

### 23. `WhoAmI` is privilege-free — a green `whoami` is a FALSE all-clear

A principal with NO security role still gets exit 0 from `connection whoami`
(returns UserId/OrgId), while every data op fails "has not been assigned any
roles". Role assignment for an S2S app user is a mandatory manual admin step
(the roleless principal cannot self-grant). Any org-bootstrap/runbook VERIFY
step must be a **write** (entity CRUD roundtrip), never just whoami.

### 24. Async `BulkDetectDuplicates` logs flagged records, not A↔B pairs (#710)

Each `duplicaterecord` row has `_baserecordid_value` set but
`_duplicaterecordid_value` (the matched-counterpart lookup) left NULL —
live-confirmed on both targets; `duplicateid` is only the log row's own PK.
`dup bulk-detect --wait` surfaces `_baserecordid_value` and never presents
`duplicateid` as "the record it duplicates". Trap for readers:
`$select=_duplicaterecordid_value` is a valid select (doesn't 400) but returns
null on bulk-job rows — canned mocks lie about population; confirm against a
live raw GET.

### 25. `systemform.formxml` is a caller-language projection of a per-language label store (#940)

Tab/section/cell labels are NOT stored in the formxml — the platform keeps them
in a per-language label store, and the `formxml` served over the Web API is a
projection **filtered to the calling user's `usersettings.uilanguageid`**. On a
bilingual org (e.g. 1033 + 1025) a caller whose UI language is 1025 sees each
element serialize with a single `<label>` — 1025 where an Arabic label exists,
otherwise 1033 — so the same form reads with an inconsistent, one-language shape
depending on who asks. A `formxml` PATCH is projected the same way: the platform
**silently discards** any `<label>` whose `languagecode` differs from the
caller's UI language (204, no error), and a read-back "passes" because it
re-projects to the caller's language. CLI-authored labels are hardcoded to
`1033`, so even a plain `form add-field` run by a non-1033 caller writes a label
the platform throws away. `crm form` write verbs warn when the outgoing formxml
carries a foreign-language label, and `form export` notes the projection
language (both via `meta.warnings`); the warning is advisory — the write still
proceeds. The multi-language route for these labels is `translation
export`/`import`, **not** formxml: in `CrmTranslations.xml` they appear as
lowercase `displayname` rows keyed by `LabelId` (easily mistaken for the
capitalized attribute `DisplayName` rows). Confirmation the store is not
actually lossy: a `translation export` of the same solution lists both the
`1033` and `1025` columns populated, including for tabs whose formxml emits a
single language. Same mechanism on dashboards/ribbon (same 1033 hardcode,
different surface); `entity get/update --select formxml` and `crm batch` PATCH
payloads sit on the raw seam and stay **unwarned** — this entry is their
warning.
