# Dynamics 365 CE On-Premises (v9.x): Customization, Web API Automation & Agentic AI

> **Status:** complete (2026-05-30). Two passes. Initial pass → Parts 1–3. Follow-up pass on the 4
> open questions (run `wsva87sna`) → folded into the *Follow-up findings* section below; Q1/Q2 answered,
> Q4 partial, Q3 still open.
>
> **Method:** deep-research harness — fan-out web search, fetch sources, top-25 adversarial verification
> (3-vote, 2/3-refute kills a claim). Pass 1: 5 angles, 22 sources, 109 claims → 13 after dedup, 2 myths
> killed. Pass 2: 6 angles, 27 sources, 128 claims → 7 after dedup, 2 myths killed.

## Confidence legend

- 🟢 **verified** — 3-0 unanimous adversarial vote, primary source (mostly the op-9-1 on-prem moniker).
- 🟡 **high-confidence-with-caveat** — verified, but the source is a cloud-pathed Power Apps/Dataverse
  doc and the fact is *inferred* to apply on-prem (auth-independent OData v4 contract; only the version
  path differs). Reasonable, verifier-endorsed inference — **not** a direct op-9-1 statement.
- 🔧 **engineering judgment** — not Microsoft-documented. Derived from the verified platform mechanics
  plus this repo's existing `crm` CLI. The agentic-AI focus area produced **zero** independently
  verified claims; treat everything marked 🔧 as design, not cited fact.

---

## Part 1 — What's customizable on-prem, and how

### No-code surface (metadata-driven) 🟢

On-prem v9.x is a metadata-driven xRM platform. Everything built through the designers *becomes the
metadata*, and is therefore Microsoft-supported by definition. Surfaces, each with its own designer:

- Custom entities/tables, attributes/columns, **option sets**
- Relationships: **1:N, N:1, N:N** (verbatim from docs)
- Forms, views, charts, dashboards, **apps**, **sitemaps**
- Business process flows, **classic workflows**, **actions**, **business rules**, report wizard

> "Everything you do by using those tools is supported by Microsoft because they apply changes to the
> metadata or data that depends on the metadata." — [customize/overview op-9-1][s1]

### Code extensibility points 🟢

Code-based extension must use **only supported web services/APIs** to survive upgrades ([s1], [s2]).
Documented points ([s5], [s6]):

- **Plug-ins** — registered handlers subscribing to platform events (modify/augment standard behavior)
- **Custom workflow activities**
- **Client API** (form/client-side JavaScript) + **web resources** (JS/HTML)
- **Webhooks**, **Azure extensions**, asynchronous service
- Commands/ribbon, direct customizations-file editing

### On-prem divergences from cloud (the ones that bite) 🟢

- **Full-trust plug-ins** — on-prem can run plug-ins in the *same app domain* (not sandbox-only like
  online), with **on-disk assembly storage** at `<installdir>\Server\bin\assembly`. Cloud cannot.
  ([s6], [s7])
- **No maker portal, no admin center, no managed environments, no Solution Checker, no Power Automate
  cloud flows.** On-prem uses classic processes + NuGet-distributed tooling instead. ([s17], [s18], [s19])
- Plug-in registration via the **Plugin Registration Tool** (from NuGet).

### Business rules 🟢

Apply form logic without JS/plug-ins. Seven actions: set value, clear value, set requirement level,
show/hide, enable/disable, validate-with-error, recommendation. ([s9])

**Scope gotcha** — controls client vs server execution:

| Scope | Runs |
|---|---|
| **Entity** | all forms **+ server-side** |
| All Forms | all forms (client only) |
| Specific form | that form only |

⚠️ Under Entity scope, only *Set Field Value, Set Default Value, Show Error Message* run server-side.
Lock/Unlock, Set Visibility, Set Business Required, Recommendation are model-driven-app only and
**silently ignored server-side**. ([s9])

### Security model 🟢

Role-based, **cumulative** — a user holds multiple roles and gets the *union* of all privileges ([s10]).
Record-level = **8 privileges** (Create, Read, Write, Delete, Append, Append To, Assign, Share) ×
**5-tier access hierarchy**: Global(Organization) ⊃ Deep(Parent:Child BUs) ⊃ Local(BU) ⊃ Basic(User)
⊃ None. Higher tier subsumes lower. Matters for agentic work: **privilege grants are cumulative and
effectively irreversible-by-accident** — an over-broad role can't be "un-unioned" by adding another role.

### ❌ Myths the research killed (do not repeat these)

- **"Four clean classic process types each with own designer"** — refuted 0-3. The
  BPF/workflow/action/business-rule taxonomy is *not* as clean as commonly stated. Don't assert it.
- **"OAuth is cloud-only / undocumented for on-prem"** — refuted 1-2. OAuth **is** documented for
  on-prem, but **gated to IFD + AD FS 3.x+**. See Part 2.

---

## Part 2 — Web API automation (the deep path)

### Three developer web services 🟢

On-prem v9.x exposes the **Web API (OData v4)**, the **Organization Service (SOAP/WCF, .NET-optimized)**,
and the **Discovery Service** ([s3], [s4]). (Pedantic note: docs actually enumerate *five* surfaces
total — these three plus the deprecated Organization Data service (OData v2) and on-prem-only Deployment
service. The "three" is the developer trio.)

### Metadata operations — the contract 🟡

*Caveat: these come from cloud-pathed Power Apps docs. Microsoft maintains no separate on-prem copy of
the Web API metadata reference. Verifiers ruled them auth-independent OData v4 contracts that apply
identically on-prem — only the version path differs (`/api/data/v9.0/` or `/v9.1/` instead of the cloud
`/v9.2/`). High-confidence, but an inference.*

**Create a custom table** — POST the JSON entity definition to `EntityDefinitions`. Body **must** include
a `StringAttributeMetadata` primary-name attribute with `IsPrimaryName: true`. ([s14])

**Update is PUT-replace, never PATCH** — the single most important safety fact:

> "You can't use the PATCH method to update data model entities... you must use the PUT method... and be
> careful to include all the existing properties that you don't intend to change. You can't update
> individual properties." — ([s14], [s15], [s16])

Parity with SDK `UpdateEntityRequest` — it **replaces the entire definition**. Any automated metadata
update **must retrieve-then-merge the full current definition first**, or it silently wipes properties.
(Carve-outs: option-set options use a dedicated `UpdateOptionValue`; business *data* rows do use PATCH
normally.)

**Publish gates go-live** 🟡 — after updating a table/column definition, changes are **not applied** until
you call the `PublishXml` or `PublishAllXml` Web API action ([s14], [s15]). This is a natural,
platform-enforced "staging vs production" boundary.

### Auth — the critical on-prem ≠ cloud divergence 🟢

On-prem supports **exactly three** security models ([s11], [s12], [s13]):

1. **Claims-based authentication** (all on-prem editions support it)
2. **Active Directory authentication** (NTLM/Windows credentials for plain on-prem Web API —
   `NetworkCredential` + `HttpClientHandler`)
3. **OAuth 2.0 — IFD only** (requires AD FS 3.x+ app registration)

Which model you need depends on **both** deployment type (plain on-prem vs IFD) **and** which endpoint
(Web API vs Org Service). Cloud Dataverse is OAuth-only via Entra ID; **none of that applies on-prem.**
⚠️ Doc warning: *do not use `ServiceClient` if using AD FS for on-prem auth* ([s13]). Practitioner
confirmation of the Python + NTLM connection pattern exists ([s24]) — this is what this repo's
`crm/utils/d365_backend.py` already implements.

### Tooling (no maker portal, so: NuGet) 🟢

- **SolutionPackager** ships in `Microsoft.CrmSdk.CoreTools` NuGet (current 9.1.0.179, `bin\coretools`)
  — compress/extract solution files for source control ([s17], [s20])
- **Configuration Migration Tool** (NuGet) — moves *configuration/reference data* (in custom entities)
  across orgs, explicitly distinct from end-user data (accounts/contacts) ([s18])
- Tools are **no longer individually downloadable** — all via NuGet ([s19])
- **pac CLI** is the modern steer, but it pulls from nuget.org too — and its on-prem support is
  **unverified** (open question #2 below)

### ⚠️ Gaps the research could NOT verify (do not assume)

1. **Solution import/export over Web API** — `ImportSolution`/`ExportSolution`/`AddSolutionComponent`
   as on-prem Web API actions produced **zero verified claims**. Behavior + managed/unmanaged +
   publisher-prefix patterns for on-prem need primary confirmation before automation.
2. **`pac` CLI against on-prem v9.x** (NTLM/AD FS) — supported, or cloud-Dataverse-only? Unverified.
3. **On-prem-specific metadata Web API limits/throttling** under NTLM at v9.0/v9.1 — undocumented in
   the cloud-pathed pages.

---

## Part 3 — Agentic AI in D365 dev 🔧

**Honest framing:** this section is **engineering judgment, not Microsoft-documented**. The
deep-research verify phase produced zero surviving claims for the agentic angle — the general-practice
sources ([s25] Anthropic best practices, [s26] MCP-to-production, [s27] CLI-tools-for-agents,
[s28] hooks-as-guardrails) were fetched but didn't yield falsifiable, independently-verifiable claims.
Treat everything here as a *design derived from the verified platform mechanics above + this repo's
`crm` CLI*, not as cited fact.

### The key insight

The platform's own constraints **are** the safety rails. Don't invent guardrails — encode the ones
D365 already enforces:

| Verified platform fact | Becomes this agent guardrail |
|---|---|
| PUT replaces whole definition, no PATCH ([s14]) | **Retrieve-merge-write** is mandatory; never let an agent PUT a partial body |
| Publish is a separate gated action ([s14]) | **Publish gating** — separate "stage metadata" from "publish"; require explicit approval to publish |
| Privileges cumulative & not easily revoked ([s10]) | Treat role/privilege grants as **irreversible-class** ops → dry-run + confirm |
| Delete-entity/delete-attribute = data loss | **Destructive-op denylist** — agent proposes, never auto-executes |

### General best practices (vendor-neutral) 🔧

- **CLI as the tool layer beats raw API calls** — a well-designed CLI gives the agent typed verbs,
  validation, and consistent error surfaces. Design verbs for *agents*: deterministic output (JSON),
  explicit exit codes, idempotent operations, `--dry-run` everywhere. ([s27])
- **MCP for production reach** — wrap the CLI behind an MCP server so the agent gets discoverable,
  schema'd tools rather than free-form shell. ([s26])
- **Hooks as deterministic guardrails** — pre-tool hooks that block destructive patterns regardless of
  model behavior; the model can't rationalize past a hook. ([s28])
- **Subagent fan-out + deterministic workflows** — orchestrate read-heavy investigation in parallel
  subagents; keep mutation paths single-threaded and gated. ([s25])

### Concretely wiring the `crm` CLI 🔧

This repo is already the right shape: Python CLI wrapping the Dataverse Web API + NTLM, pyright-strict
core. To make it a safe agent tool layer:

1. **Idempotency + dry-run as first-class flags.** Every mutating command (`entity create`,
   `attribute add`, `solution import`) gets `--dry-run` that prints the computed request body + a
   metadata diff and exits without writing. Make dry-run the *default* for destructive verbs.
2. **Split stage vs publish.** Mirror the platform: customize commands write definitions; a separate
   `publish` command (or `--publish` opt-in, default off) calls PublishXml/PublishAllXml. Agent stages
   freely; publishing is the gate.
3. **Retrieve-merge-write helper for all PUT paths.** Bake the "fetch full definition, merge, PUT" cycle
   into the CLI so an agent *cannot* issue a partial PUT that wipes properties. The #1 silent-data-loss
   risk.
4. **Destructive-op gate.** delete-entity / delete-attribute / role changes require an explicit
   destructive-confirm flag *and* a Claude Code PreToolUse hook that hard-blocks them unless a token is
   present. Don't rely on the prompt.
5. **Solution-aware by default.** Require a target solution + publisher prefix on create ops so changes
   land in a managed unit, not the Default solution. (⚠️ gated on open-question #1 — verify solution
   Web API actions behave on on-prem first.)
6. **Structured output.** JSON mode on every command → agent parses results deterministically instead of
   scraping prose. Extend the existing `crm` skill, or add an MCP wrapper.
7. **Reuse the platform's auth boundary.** NTLM is already in `crm/utils/d365_backend.py` — the agent
   never handles credentials; it calls the CLI, the CLI owns auth. Good isolation.

**Order of trust for guardrails:** platform constraints (free) → CLI validation/dry-run → Claude Code
hooks (deterministic block) → prompt instructions (weakest). Never let the weakest layer be the only one.

### Tracked as issues

A codebase audit (2026-05-30) found the `crm` CLI already ships `--json` (deterministic `ctx.emit`
envelope), `--dry-run` request preview, and a `solution publish` / `publish-all` command — so those
guardrails are **done**. The remaining gaps are filed as `enhancement` + `ready-for-agent` issues:

- [#18](https://github.com/Gharib89/crm/issues/18) — idempotent create ops (`--if-exists skip|error`)
- [#19](https://github.com/Gharib89/crm/issues/19) — `--stage-only` safe mode (force `--no-publish` on mutations)
- [#20](https://github.com/Gharib89/crm/issues/20) — `update-entity/attribute/relationship` with retrieve-merge-write (no partial PUT)
- [#21](https://github.com/Gharib89/crm/issues/21) — destructive-op PreToolUse hook + standardize confirm coverage
- [#22](https://github.com/Gharib89/crm/issues/22) — profile default solution + publisher prefix; warn on unsolutioned mutations

(An MCP-server wrapper was considered and **dropped** — `SKILL.md` + `--json` already make the CLI
agent-usable.)

---

## Follow-up findings (research run `wsva87sna`, 2026-05-30)

Second pass: 110 agents, 27 sources, 7 verified findings. Provenance tags used here: **DOC-ON-PREM**
(stated on an op-9-1 page) · **INFER-CLOUD** (cloud doc; surface stable, on-prem runtime not separately
stated) · **ENG-JUDGMENT** (not Microsoft-documented).

### Q1 — Solution import/export over the Web API → **ANSWERED** 🟢/🟡

`ImportSolution`, `ExportSolution`, `AddSolutionComponent` **are** OData v4 actions in the
`Microsoft.Dynamics.CRM` namespace. Surfaces documented & stable across v9.0/v9.1/v9.2:

- **`ImportSolution`** ([s30]): `CustomizationFile` (Edm.Binary/base64), `OverwriteUnmanagedCustomizations`
  + `PublishWorkflows` (bool), `ImportJobId` (Edm.Guid).
- **`ExportSolution`** ([s32]) → `ExportSolutionResponse.ExportSolutionFile` (Edm.Binary, inline base64
  in the JSON response — not a streamed download).
- **`AddSolutionComponent`** ([s33]): unbound action, **targets UNMANAGED solutions only**; 6 params
  (`ComponentId`, `ComponentType`, `SolutionUniqueName`, `AddRequiredComponents`,
  `DoNotIncludeSubcomponents`, `IncludedComponentSettingsValues`).
- **Progress tracking is DOC-ON-PREM** ([s35], op-9-1 *Work with solutions*): pass an `ImportJobId` →
  query the `ImportJob` table → parse `//solutionManifest/result/@result`; `RetrieveFormattedImportJobResults`
  fetches the log.
- ⚠️ **Async staging** (`StageSolution` → `ImportSolutionAsync` → poll `asyncoperation`) is a **cloud-era
  construct** ([s34], [s36]). The `asyncoperation` polling entity *is* DOC-ON-PREM, but `StageSolution`
  + ComponentParameters (env vars / connection refs) on-prem availability is **NOT established**. Use
  **synchronous `ImportSolution` (+ `ExecuteAsync`)** on-prem until verified on the target server.
- Caveat: reference pages resolve to cloud `dataverse-latest`; the *surface* is DOCUMENTED, on-prem
  *runtime* is INFER-CLOUD. The `crm` CLI already ships `solution import` / `solution export` — this
  confirms the underlying actions, so no new CLI command is needed.

### Q2 — pac CLI on-prem → **ANSWERED, DEFINITIVE** 🟢

**pac is NOT supported on-prem.** Verbatim op-9-1 ([s37]): *"the Power Platform CLI is not available for
Dynamics 365 Customer Engagement (on-premises), you must use CrmSvcUtil.exe."* `pac auth` offers **only**
Entra-ID/Azure cloud auth (device code, service-principal app-id/secret/cert, managed identity) — **no
NTLM/AD/AD FS/IFD path**; legacy `--url` is deprecated for the cloud `--environment` construct ([s38]).
A Microsoft maintainer confirms pac is online-only (built on `Microsoft.PowerPlatform.Dataverse.Client`,
no IFD/AD FS) ([s39]). On-prem tooling = **`SolutionPackager.exe` + `CrmSvcUtil.exe`** from the
`Microsoft.CrmSdk.CoreTools` NuGet package. ⚠️ Refuted (0-3): the claim that CoreTools bundles the *full*
legacy tool set (PluginRegistration, ConfigurationMigration, PackageDeployer) — only SolutionPackager +
CrmSvcUtil are confirmed-by-doc.

### Q3 — On-prem metadata API limits / throttling → **STILL OPEN** 🔴

**No verified evidence either way.** Whether on-prem v9.0/v9.1 has page-size/payload caps, unsupported
metadata operations under NTLM, or whether cloud service-protection throttling (HTTP 429,
`x-ms-ratelimit`, `Retry-After`) applies on-prem — unestablished; this was a primary question and the gap
is significant. Working assumption: cloud-style 429 service-protection is a multi-tenant cloud mechanism
and likely absent on-prem, **but verify against the actual target server** before tuning batch size /
concurrency. Needs primary research or field measurement.

### Q4 — Agentic-CLI safety patterns → **PARTIALLY ANSWERED**

Only **two Microsoft-DOCUMENTED-for-on-prem** safety primitives exist:

1. **Solution-aware in-context create/update via `SolutionUniqueName`** (Web API: the
   `MSCRM.SolutionUniqueName` request header on Create/Update). Microsoft explicitly says **prefer this
   over standalone `AddSolutionComponentRequest`** (which forces you to know the `ComponentType` integer
   and can't target a managed solution). This is *the* documented idempotency/solution-awareness pattern
   → directly validates issue **#22**. ([s31], [s35])
2. **`ImportJobId` + `RetrieveFormattedImportJobResults`** for verifiable import outcomes. ([s35])

Everything else in Part 3 (dry-run metadata diff, publish gating, retrieve-merge-write for PUT,
destructive-op guards) is **ENG-JUDGMENT** — sound, drawn from terraform-style plan/apply ([s42]), MCP
tool-annotations / human-in-the-loop guardrails ([s43]), and Claude Code hooks ([s28]) — but **not
Microsoft-documented**. So issues **#18–#21 are valid engineering hardening, not vendor-prescribed**.

### Still open (carried forward)

- **Q3** above — metadata limits/throttling on-prem.
- Does `StageSolution` + `ImportSolutionAsync` function on op-9-1, or is sync `ImportSolution` +
  `ExecuteAsync` the only on-prem async path?
- Are PackageDeployer + the Configuration Migration Tool in `Microsoft.CrmSdk.CoreTools`, or a separate
  NuGet, for on-prem?
- Behavioral differences between `/api/data/v9.0`, `/v9.1`, `/v9.2` on the same on-prem server?

---

## Sources

Primary Microsoft Learn op-9-1 (on-prem, strongest scope):

- [s1]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/customize/overview?view=op-9-1>
- [s2]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/customize/customizations-supported?view=op-9-1>
- [s3]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/overview?view=op-9-1>
- [s4]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/use-microsoft-dynamics-365-web-services?view=op-9-1>
- [s5]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/extend-customer-engagement?view=op-9-1>
- [s6]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/plugin-development?view=op-9-1>
- [s7]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/register-deploy-plugins?view=op-9-1>
- [s9]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/customize/create-business-rules-recommendations-apply-logic-form?view=op-9-1>
- [s10]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/admin/security-roles-privileges?view=op-9-1>
- [s11]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/authenticate-users?view=op-9-1>
- [s12]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/webapi/authenticate-web-api?view=op-9-1>
- [s13]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/active-directory-claims-based-authentication?view=op-9-1>
- [s17]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/compress-extract-solution-file-solutionpackager?view=op-9-1>
- [s18]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/admin/manage-configuration-data?view=op-9-1>
- [s19]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/download-tools-nuget?view=op-9-1>
- [s23]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/customize-dev/publish-customizations?view=op-9-1>

Cloud-pathed Power Apps Dataverse (Web API metadata; inferred-for-on-prem 🟡):

- [s8]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/plug-ins>
- [s14]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-entity-definitions-using-web-api>
- [s15]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-column-definitions-using-web-api>
- [s16]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-relationship-definitions-using-web-api>
- [s21]: <https://learn.microsoft.com/power-apps/developer/data-platform/webapi/web-api-metadata-operations-sample>
- [s22]: <https://learn.microsoft.com/power-apps/developer/data-platform/webapi/use-web-api-metadata>

NuGet / blogs:

- [s20]: <https://www.nuget.org/packages/Microsoft.CrmSdk.CoreTools>
- [s24]: <https://alexanderdevelopment.net/post/2018/01/15/connecting-to-an-on-premise-dynamics-365-org-from-python/>
- [s25]: <https://www.anthropic.com/engineering/claude-code-best-practices>
- [s26]: <https://claude.com/blog/building-agents-that-reach-production-systems-with-mcp>
- [s27]: <https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no>
- [s28]: <https://paddo.dev/blog/claude-code-hooks-guardrails/>
- [s29]: <https://www.iesgp.com/blog/whats-the-difference-between-dynamics-365-online-and-on-premises>

Follow-up pass (`wsva87sna`) — solution Web API actions, pac on-prem, agentic safety:

- [s30]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/importsolution?view=dataverse-latest>
- [s31]: <https://learn.microsoft.com/en-us/power-platform/alm/solution-api>
- [s32]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/exportsolutionresponse?view=dataverse-latest>
- [s33]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/addsolutioncomponent?view=dataverse-latest>
- [s34]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/reference/importsolutionasync?view=dataverse-latest>
- [s35]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/work-solutions?view=op-9-1> (op-9-1 on-prem)
- [s36]: <https://learn.microsoft.com/en-us/power-platform/alm/solution-async>
- [s37]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/org-service/create-early-bound-entity-classes-code-generation-tool?view=op-9-1> (op-9-1 — "pac not available on-prem")
- [s38]: <https://learn.microsoft.com/en-us/power-platform/developer/cli/reference/auth>
- [s39]: <https://github.com/microsoft/powerplatform-build-tools/discussions/1097> (Microsoft maintainer: pac is online-only)
- [s40]: <https://learn.microsoft.com/en-us/dynamics365/customerengagement/on-premises/developer/solution-tools-team-development?view=op-9-1> (op-9-1 — SolutionPackager via CoreTools NuGet)
- [s41]: <https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits>
- [s42]: <https://microsoft.github.io/terraform-provider-power-platform/>
- [s43]: <https://modelcontextprotocol.io/specification/2025-11-25/server/tools> · <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>
