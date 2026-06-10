# Environment admin verbs (create / copy / backup / restore)

This CLI does not provision, copy, back up, or restore Dataverse environments,
and won't grow `crm environment create|copy|backup|restore` verbs. It speaks the
**org-scoped Dataverse Web API** (OData v4) over a saved profile's org URL.
Environment lifecycle is a **tenant-level Power Platform admin** concern that
lives on a different API, behind a different credential model, and is already
covered cross-platform by Microsoft's own tools.

## Why this is out of scope

**It's a different API on a different host.** Environment create/copy/backup/
restore are not operations on the org's Dataverse Web API — they live on the
Power Platform admin APIs: `https://api.powerplatform.com/environmentmanagement/...`
(e.g. the [Environment Copy API](https://learn.microsoft.com/rest/api/power-platform/environmentmanagement/environment-copy/copy-environment))
and the legacy BAP endpoint `api.bap.microsoft.com`. MS Learn states it plainly:
"The Power Platform Environment Copy API uses a different endpoint
(`https://api.powerplatform.com`) than the Dataverse APIs." Different host,
different OAuth token audience, tenant scope rather than org scope. The proposed
wrapper cannot be built on the existing `D365Backend` / profile plumbing.

**The credential model doesn't transfer.** Calling these APIs with client
credentials requires the service principal to hold the tenant-scope
**Power Platform Contributor** RBAC role, plus out-of-band registration steps a
service principal cannot perform for itself (interactive tenant-admin context —
`New-PowerAppManagementApp`, or a BAP `PUT /adminApplications/...` with an
admin's user token). The CLI's v2.0.0 profile model stores org-URL-scoped
client-credential secrets. Supporting environment admin means a second HTTP
client, a second token audience, a tenant-level profile concept, and admin setup
the CLI can neither perform nor verify.

**The scriptable gap is already covered, cross-platform.** `pac admin
create|copy|backup|restore` (pac CLI runs on Linux as a dotnet tool), the Power
Platform Build Tools (Azure DevOps), the GitHub Actions for Power Platform
administration, and the admin PowerShell module all script these operations
today. A crm wrapper would be a fifth client for the same online-only API.

**The dual-target promise is unmeetable in principle.** Online environments are
a managed-service concept; on-prem organizations are managed by Deployment
Manager / SQL backups. The proposal itself concedes on-prem would only ever get
a "use Deployment Manager" message — which docs deliver without code. The
both-targets contract that justifies this CLI's other verbs cannot be met here.

## Supported alternative

- **Online:** `pac admin create|copy|backup|restore` (cross-platform dotnet
  tool), Power Platform Build Tools, the Power Platform admin PowerShell module,
  or the Power Platform admin center UI.
- **On-prem:** Deployment Manager, the on-prem PowerShell snap-in, or SQL Server
  backups.

If Microsoft ever folds environment lifecycle into the org-scoped Dataverse Web
API under an org-URL client-credential token, this rejection can be revisited.

## Prior requests

- #201 — "Build environment admin verbs (online-only gap, no dual-target tool)"
