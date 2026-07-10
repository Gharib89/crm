# Customizations-as-Code: Market-Claim Verification (July 2026)

**Purpose.** This note verifies, against primary sources, the three market claims that an
earlier PRD used to position `crm`'s customizations-as-code workflow (declarative
`spec.yaml` for forms / model-driven apps / sitemaps / entities, with a Terraform-like
plan -> human-approve -> apply-from-plan loop). It feeds the *positioning* section of the
customizations-as-code guide. Every claim below carries the defensible wording the guide
may quote, plus inline citations (URL + what the source says). Do **not** overclaim beyond
the wording given here.

## Freshness note

All sources accessed **2026-07-10**. These products move fast — the Dataverse MCP server
and Power Platform Git integration in particular are on active preview -> GA tracks and change
tool surfaces without notice (the MCP tool list was already renamed once; see Claim 2).
**Before the `/ship` run that drafts the guide, re-verify each claim at draft time** by
re-fetching the cited Microsoft Learn pages and the Terraform provider resource index.
Most Microsoft Learn pages do not surface a machine-readable "last updated" date in fetched
markdown; where a source states a date (e.g. release-plan preview/GA milestones) it is
recorded inline.

---

## Claim 1 — No desired-state / convergent "Terraform-style" engine for Dataverse schema + UI exists

**Verdict: Confirmed (with refinement).**

> **Quotable wording:** As of July 2026, neither Microsoft nor any widely-known third-party
> tool offers a *declarative desired-state engine with diff/plan-then-apply for Dataverse
> schema and UI components* — tables, columns, forms, views, sitemaps, model-driven apps,
> dashboards. Microsoft's own Infrastructure-as-Code offering, the official Power Platform
> Terraform provider, brings desired-state management to Power Platform **environments,
> tenant settings, governance (DLP / managed-environment) policies, connections, and users**
> — the *administrative and provisioning layer* — not to the schema/UI components inside a
> Dataverse database. Its two "escape-hatch" resources (`powerplatform_solution` and
> `powerplatform_data_record`) come closest but do not close the gap (see near-misses).

**Evidence.**

- Microsoft's own framing of the gap the Terraform provider was built to fill:
  "Power Platform and Dynamics 365 are currently primarily managed, governed, and deployed
  through an admin portal graphical user interface ... there is currently no first-party
  solution for deploying resources using infrastructure as code (IaC)." The provider's stated
  scope is deploying/governing **environments and D365 resources at scale** (provisioning
  Dev/Test/Prod environments, governance, tenant management) — not schema/UI authoring.
  — [Power Platform Terraform provider (MS Learn playbook)](https://learn.microsoft.com/business-applications/playbook/enterprise-solutions/power-platform-terraform-provider/)

- Complete resource/data-source inventory of the official provider
  (`microsoft/terraform-provider-power-platform`, GitHub Pages docs index). **Resources:**
  `billing_policy`, `billing_policy_environment`, `connection`, `connection_share`,
  `data_loss_prevention_policy`, `data_record`, `environment`,
  `environment_application_package_install`, `environment_group`, `environment_settings`,
  `managed_environment`, `rest`, `solution`, `tenant_settings`, `user` (plus preview
  resources: `analytics_data_exports`, `copilot_studio_application_insights`,
  `environment_group_rule_set`, `environment_wave`, `tenant_capacity`). **No resource models
  a table/entity, column/attribute, form, view, sitemap, model-driven app, or dashboard as a
  first-class typed resource.**
  — [Power Platform Terraform Provider docs (resource index)](https://microsoft.github.io/terraform-provider-power-platform/)

- `powerplatform_solution` is `pac solution import` wrapped: it takes a solution `.zip` as an
  **opaque artifact**, tracks a file checksum to detect change, and imports it. It does not
  diff or converge the individual schema/UI components *inside* the solution.
  — [powerplatform_solution resource](https://microsoft.github.io/terraform-provider-power-platform/resources/solution/)

- `powerplatform_data_record` is untyped **row** CRUD via dynamic column maps over existing
  tables; the docs explicitly say it "is not recommended for managing business data" and it
  targets configuration/seed rows. It gives desired state over *rows*, not over *schema*
  (entity/attribute metadata is not row-addressable) and carries no UI-component abstraction.
  — [powerplatform_data_record resource](https://microsoft.github.io/terraform-provider-power-platform/resources/data_record/)

- Pac CLI / Build Tools ALM is import/export-and-package, not desired-state: `pac solution
  export` / `import`, `pack` / `unpack` (decomposes the solution zip into XML or the newer
  YAML source-control format). It moves and repackages solution artifacts; there is no
  diff-then-converge planner over the component graph.
  — [pac solution (MS Learn)](https://learn.microsoft.com/power-platform/developer/cli/reference/solution)

- Native Dataverse Git integration stores solution components in human-readable files for
  version control, but it is a **sync** mechanism (commit/pull solutions to/from Git), not a
  desired-state planner: "an easier way of syncing Power Platform customizations (solutions
  and their objects) with a Git repository."
  — [FAQs about source code integration (MS Learn)](https://learn.microsoft.com/power-platform/alm/git-integration/faqs)

**Near-misses / caveats.**

- *`powerplatform_data_record` (closest first-party).* Because it can write arbitrary rows, one
  could in theory poke a `systemform`, `sitemap`, or `appmodule` row's XML through it. That is
  raw, schema-unaware row CRUD — no typed form/sitemap/app model, no component-graph diff, and
  entity/attribute *metadata* (the `EntityMetadata` API) is not exposed as records at all.
  So it does **not** constitute a desired-state engine for schema + UI. Distinguish carefully
  from the claim.
- *Environment provisioning vs. schema/UI.* The provider genuinely delivers Terraform
  desired-state — but for the **environment/tenant/governance layer** (create environments,
  set DLP, manage-environment policies, assign users/roles). The claim is about the
  **schema/UI layer inside** a Dataverse database, which the provider does not manage. Keep
  this distinction explicit in the guide; conflating them is the easy way to be wrong.
- *Pulumi / Bicep / azure-native.* No dedicated Pulumi or Bicep provider manages Dataverse
  schema/UI as desired state. The only Power Platform surface in `azure-native` is
  `powerplatform.EnterprisePolicy` (an Azure control-plane resource for VNet/CMK enterprise
  policies) — infrastructure, not Dataverse tables/forms.
  — [azure-native.powerplatform.EnterprisePolicy (Pulumi Registry)](https://www.pulumi.com/registry/packages/azure-native/api-docs/powerplatform/enterprisepolicy/)
- *Universal-negative caveat.* "Nothing exists anywhere" cannot be fully proven; searches
  surfaced no credible OSS desired-state-for-schema/UI tool as of the access date. Phrase the
  guide as "no first-party and no widely-known third-party tool," not an absolute.

---

## Claim 2 — Microsoft's Dataverse MCP server is imperative, with no diff-before-apply / plan-review gate

**Verdict: Confirmed.**

> **Quotable wording:** The Microsoft Dataverse MCP server exposes an **imperative** tool set
> (create / update / delete records and tables, run queries, manage skills and files). It has
> **no plan / preview / diff tool and no built-in convergence or plan-review gate.** Its only
> server-side safety affordance is that the two destructive delete tools act "only after
> explicit user approval"; the non-destructive mutations (create/update record, create/update
> table) apply directly. Any richer approval workflow is generic MCP client-side confirmation,
> not something the server itself provides. The server is documented for **Dataverse online
> only** (`https://{org}.crm.dynamics.com/api/mcp`); no on-premises equivalent is documented.

**Evidence.**

- Full GA tool list (from the canonical MS Learn page): `search_data`, `search`,
  `create_record`, `update_record`, `delete_record` ("Delete a row, only after explicit user
  approval"), `create_table` ("Creates a new table with a specified schema"), `update_table`
  ("Modifies schema or metadata of an existing table"), `delete_table` ("only after explicit
  user approval"), `read_query`, `describe`, `upsert_skill`, `delete_skill`,
  `init_file_upload`, `commit_file_upload`, `file_download`. **There is no `plan`, `preview`,
  `diff`, `dry-run`, or `apply` tool.** The approval note appears only on the two *delete*
  tools; `create_table` / `update_table` (schema changes) carry no approval note.
  — [Connect to Dataverse with Model Context Protocol — List of tools (MS Learn)](https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp#list-of-tools)

- Security is enforced by Dataverse role/row security, not by any MCP plan gate: "The
  Dataverse MCP server respects Dataverse security roles and row-level security ... No
  additional MCP-specific access controls are needed."
  — [Dataverse MCP FAQ (MS Learn)](https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp-faq)

- Online-only + hosted-service nature and availability dates: a "remote ... centrally hosted,
  managed service"; **public preview May 20, 2025; general availability December 15, 2025.**
  The server URL is `https://{dataverseOrgName}.crm.dynamics.com/api/mcp` (a cloud
  `crm.dynamics.com` endpoint).
  — [Dataverse MCP Server (Power Platform release plan)](https://learn.microsoft.com/power-platform/release-plan/2025wave2/data-platform/dataverse-mcp-server)
  and [MCP overview page (URL format)](https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp)

**Near-misses / caveats.**

- The per-tool "only after explicit user approval" on `delete_record` / `delete_table` is a
  **destructive-delete confirmation**, not a diff-before-apply plan. It shows Microsoft added a
  narrow human gate on deletes — worth acknowledging so the guide isn't accused of ignoring it
  — but it is not equivalent to `crm`'s reviewable plan artifact across all mutations.
- The tool surface is unstable: `describe_table` / `list_tables` / `fetch` were removed and
  folded into `describe`, and the old data-search `search` was renamed `search_data`. Re-check
  the tool list at draft time (see Freshness note).
  — [Dataverse MCP FAQ — tool-rename note](https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp-faq)
- A separate **Dynamics 365 Customer Service MCP server** exists alongside the Dataverse one;
  both are cloud endpoints. Neither adds a plan/diff gate. Don't conflate them.

---

## Claim 3 — On-prem orgs are excluded from Microsoft's cloud-only Git integration

**Verdict: Confirmed (with refinement).**

> **Quotable wording:** Microsoft's native Dataverse "Git integration in Power Platform"
> requires **managed environments** and works **only against Azure DevOps** (the only
> supported Git provider — GitHub is not supported even for online), and is configured through
> the Power Platform admin center / make.powerapps.com. All three prerequisites are
> cloud-only constructs. **Dynamics 365 Customer Engagement on-premises (v9.x) has none of
> them, so it is excluded from native Git integration** — as it is from the rest of the
> cloud-native ALM stack (managed environments, pipelines in Power Platform, Build Tools
> service connections, which target `*.crm.dynamics.com` cloud endpoints). On-prem ALM remains
> the classic **solution export/import** model; solution XML can be decomposed for source
> control with SolutionPackager / `pac solution unpack`, but that must be orchestrated
> manually — there is no first-party native Git integration, managed-environment ALM, or
> desired-state engine that targets on-prem.

**Evidence.**

- Git integration is gated on managed environments + Azure DevOps: "Dataverse Git integration
  is a feature of managed environments. Development and target environments must be enabled as
  managed environments," plus "An Azure DevOps subscription and licenses ... are required."
  — [Dataverse Git integration setup — Prerequisites (MS Learn)](https://learn.microsoft.com/power-platform/alm/git-integration/connecting-to-git)

- Azure DevOps is the **only** supported Git provider (GitHub excluded, online included):
  "Azure DevOps Git repositories are currently the only Git provider supported."
  — [FAQs about source code integration (MS Learn)](https://learn.microsoft.com/power-platform/alm/git-integration/faqs)
  and [Connect/disconnect via code — "Azure DevOps is currently the only supported Git provider."](https://learn.microsoft.com/power-platform/alm/git-integration/git-api)

- Git integration is driven from the cloud maker portals: it "is initiated from Power Platform
  in the Solutions area within Power Apps, Copilot Studio, Power Automate, and Power Pages,"
  and managed environments are enabled in the Power Platform admin center — neither exists for
  on-prem CE.
  — [Dataverse Git integration setup (MS Learn)](https://learn.microsoft.com/power-platform/alm/git-integration/connecting-to-git)

- Managed environments are a cloud entitlement tied to cloud licenses (Power Apps / Power
  Automate / Copilot Studio / Power Pages / Dynamics 365 online), administered from the Power
  Platform admin center — confirming the "cloud-only" nature of the Git-integration
  precondition.
  — [Managed environments overview (MS Learn)](https://learn.microsoft.com/power-platform/admin/managed-environment-overview)

- The rest of the cloud ALM stack likewise targets cloud Dataverse: Power Platform Build Tools
  service connections and environment tasks use `*.crm.dynamics.com` endpoints and the
  "Power Platform" service-connection type (SPN / Entra app). There is no on-prem CE service
  connection.
  — [Microsoft Power Platform Build Tools tasks (MS Learn)](https://learn.microsoft.com/power-platform/alm/devops-build-tool-tasks)

- On-prem CE v9.x is a distinct, self-hosted product line (AuthType `AD` / `IFD` / on-prem
  `OAuth` via ADFS), with its own connectivity model separate from Dataverse online — i.e. not
  a target of the cloud maker/admin surfaces above.
  — [Use connection strings in XRM tooling (D365 CE on-premises) (MS Learn)](https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/xrm-tooling/use-connection-strings-xrm-tooling-connect?view=op-9-1)

**Near-misses / caveats.**

- **Refinement — GitHub is excluded even online.** The claim's phrasing "cloud-only" is
  correct, but the sharper fact is that the native integration is *Azure-DevOps-only*; GitHub
  users (cloud included) are also excluded today. Say "Azure DevOps only," not "any Git host."
- **On-prem is not literally "manual only."** SolutionPackager and `pac solution unpack/pack`
  are source-agnostic XML/YAML (de)serializers that work on a solution zip exported from an
  on-prem org, so on-prem teams *can* put solution source in Git and script import/export.
  What on-prem lacks is the *first-party native* Git integration, managed-environment ALM, and
  any desired-state/diff-plan tooling. Phrase as "no native/first-party Git integration or
  desired-state for on-prem," not "on-prem cannot use Git at all." This is exactly the gap
  `crm`'s customizations-as-code loop fills for on-prem.
- No Microsoft Learn page states "on-prem is unsupported for Git integration" in those words;
  the exclusion is *constructive* (managed environments + Azure DevOps + make.powerapps.com are
  all cloud-only, and on-prem CE has none of them). The cited prerequisites carry the argument.

---

## Sources

Accessed 2026-07-10. Microsoft Learn pages did not expose an explicit last-updated date in the
fetched markdown unless noted.

**Claim 1 — desired-state for schema/UI**
- Power Platform Terraform provider (playbook / business problem + use cases): https://learn.microsoft.com/business-applications/playbook/enterprise-solutions/power-platform-terraform-provider/
- Terraform provider resource index (GitHub Pages docs): https://microsoft.github.io/terraform-provider-power-platform/
- `powerplatform_solution` resource: https://microsoft.github.io/terraform-provider-power-platform/resources/solution/
- `powerplatform_data_record` resource: https://microsoft.github.io/terraform-provider-power-platform/resources/data_record/
- pac solution reference: https://learn.microsoft.com/power-platform/developer/cli/reference/solution
- Git integration FAQ (sync-not-desired-state): https://learn.microsoft.com/power-platform/alm/git-integration/faqs
- Pulumi azure-native PowerPlatform EnterprisePolicy: https://www.pulumi.com/registry/packages/azure-native/api-docs/powerplatform/enterprisepolicy/

**Claim 2 — Dataverse MCP server**
- Connect to Dataverse with MCP (tool list, URL format): https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp
- Dataverse MCP FAQ (security model, tool renames): https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp-faq
- Dataverse MCP Server release plan (preview 2025-05-20 / GA 2025-12-15, hosted-cloud nature): https://learn.microsoft.com/power-platform/release-plan/2025wave2/data-platform/dataverse-mcp-server
- Configure the Dataverse MCP server for an environment: https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp-disable

**Claim 3 — on-prem exclusion from cloud Git integration / ALM**
- Overview of Git integration in Power Platform: https://learn.microsoft.com/power-platform/alm/git-integration/overview
- Dataverse Git integration setup (managed-env + Azure DevOps prerequisites): https://learn.microsoft.com/power-platform/alm/git-integration/connecting-to-git
- Git integration FAQ (Azure DevOps only): https://learn.microsoft.com/power-platform/alm/git-integration/faqs
- Connect/disconnect Dataverse from Git by code ("Azure DevOps is currently the only supported Git provider"): https://learn.microsoft.com/power-platform/alm/git-integration/git-api
- Managed environments overview (cloud entitlement): https://learn.microsoft.com/power-platform/admin/managed-environment-overview
- Power Platform Build Tools tasks (cloud service connections): https://learn.microsoft.com/power-platform/alm/devops-build-tool-tasks
- XRM tooling connection strings, D365 CE on-premises: https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/xrm-tooling/use-connection-strings-xrm-tooling-connect?view=op-9-1
