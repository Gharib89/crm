# Dataverse environment/tenant lifecycle — durable cloud e2e org strategy

**Purpose:** primary-source facts on Dataverse/Power Platform environment provisioning, licensing,
expiry, and idle-disable rules, to input into re-opening the cloud e2e org strategy.
**Date:** 2026-07-09.
**Related:** issue [Gharib89/crm#760](https://github.com/Gharib89/crm/issues/760);
[`docs/adr/0012-dedicated-cloud-e2e-org.md`](../adr/0012-dedicated-cloud-e2e-org.md) (its
2026-06-22 addendum already found the CS-trial's own 30d/60d/14d-idle numbers and deferred the
durable option as a procurement decision — this report verifies those numbers against Microsoft
Learn and fills in the paid-SKU, S2S, region, and scripting questions the addendum didn't cover).

All facts below are sourced from Microsoft Learn unless marked otherwise; the exact quoted rule
and its source URL follow each claim. Where a rule could not be pinned to a primary source, or is
plausibly stale, that is called out explicitly rather than guessed.

---

## Comparison table

| Env type | Provisioning | Cost | Expiry rule | Idle-disable rule | Extendable | Survives 60d unattended? |
|---|---|---|---|---|---|---|
| **Trial (subscription-based)** | Power Platform admin center or Dynamics "get started" links; tenant admin only; needs an active offer-based Dynamics 365 Trial subscription | Free — "neither type of environment consumes paid capacity" | Tied to the parent M365/D365 subscription's own end date (not a fixed N days) | Not itself in the activity-based cleanup (only default/developer/Teams envs are); but if the *subscription* lapses, the environment is disabled then deleted on a fixed schedule (below) regardless of usage | One M365-admin-center self-service subscription extension; beyond that, convert to production | **No** — bound to subscription term; exactly the failure mode that hit `agent-cloud` |
| **Trial (standard)** via trials.dynamics.com | Self-service by any user with a qualifying license, or tenant admin; also creatable in Power Platform admin center | Free | Fixed **30 days**, then disabled and deleted | D365-app-specific trials (Sales/Field Service/Customer Service) additionally auto-expire after **14 consecutive days with no activity**; separately, "Trial (standard) environments with no activity in the environment databases for 30 days are deleted" | One self-service +30-day extension (available in the last 7 days before expiry) — max life ~60 days total, then must convert to production | **No** — 14-day idle kill for app trials is well inside 60 days; even the extended 60-day ceiling is a hard cap, not open-ended |
| **Developer environment** (Power Apps Developer Plan) | Free, self-service per user (viral/internal license the tenant must allow); one per user by default, up to 3 via admin center | Free "as long as there's active usage and no abuse"; capped at 2 GB DB, 750 flow runs/month | No fixed expiry | **30 days no activity → disabled**; **15 more days → deleted**; 7-day recovery window after deletion | No formal extend — any activity (including a running scheduled cloud flow) resets the 30-day clock | **No** by default. A "managed" personal dev environment gets a 60-day idle threshold instead of 30 — but Managed Environments licensing explicitly excludes the Developer Plan ("A managed environment isn't included as an entitlement in the Developer Plan when users run their assets"), so getting to 60 days this way isn't a clean free path — see Q6 |
| **Paid/production Dataverse** (Power Apps per-app / per-user, or Dynamics 365 license) | Power Platform admin center / `pac admin create --type Production`; needs 1 GB of available Dataverse database capacity | Cheapest generic Dataverse capacity: **Power Apps per app plan**, historically $5/user/app/month per Learn licensing FAQ ([UNVERIFIED current price] — not shown on the current marketing pricing page, see Q4 caveat); confirmed current price for **Power Apps Premium (per user)** is $20/user/month paid yearly; cheapest **with Customer Service preinstalled** is **Dynamics 365 Customer Service Professional** at $50/user/month paid yearly (official pricing page, not Learn) | None — production environments don't expire | Not subject to activity-based cleanup (only default/developer/Teams); **is** subject to subscription-lapse cleanup if the paid license itself is canceled/expires | N/A — renews with the subscription | **Yes**, as long as the license stays paid |
| **Sandbox** | Power Platform admin center / `pac admin create --type Sandbox`; same 1 GB capacity prerequisite as production | Requires the same paid capacity as production — no separate "free sandbox" tier | None — doesn't auto-expire on its own | Not in the activity-based cleanup; **is** covered by the same subscription-lapse cleanup as production ("Only production and sandbox environments are affected by the subscription-based automatic cleanup") | N/A | **Yes**, as long as the underlying subscription/capacity stays paid |

---

## Q6 — Is there any FREE option that survives >60 days unattended?

**Short answer: no clean one.** Every free tier has either a hard expiry, an idle-disable well
under 60 days, or both:

- **Trial (standard):** hard 30-day life, one +30-day extension (60-day ceiling), and the
  D365-app trial variant additionally dies after "**14 consecutive days**" idle — quoted from the
  [Dynamics 365 Sales trial FAQ](https://learn.microsoft.com/dynamics365/sales/sales-trial-faq#trial-app):
  *"The trial expires if there's no activity for 14 consecutive days."* Same wording appears in the
  [Field Service](https://learn.microsoft.com/dynamics365/field-service/trial-faq#trial-app) and
  [Customer Service](https://learn.microsoft.com/dynamics365/customer-service/implement/trial-faq#trial-app)
  trial FAQs. Expired trials "cannot be reactivated."
- **Trial (subscription-based):** bound to the parent subscription's end date, not to usage; one
  extension only, then convert-or-lose. Not a >60-day-unattended answer either, since it depends on
  a subscription term that the ADR's history shows is not guaranteed to run that long.
- **Developer environment — the crux.** The default rule is explicit:
  > "After 30 days of inactivity, the system automatically disables environments. If, after 15
  > days, you don't re-enable the environment, the system deletes the environment."
  — [Automatic deletion of Power Platform environments § Developer environments](https://learn.microsoft.com/power-platform/admin/automatic-environment-cleanup#developer-environments)

  There is a documented 60-day variant, but it comes with a catch:
  > "Personal developer environments that are **managed environments** use a 60-day inactivity
  > threshold instead of 30 days."
  — same page, [§ Developer environments](https://learn.microsoft.com/power-platform/admin/automatic-environment-cleanup#developer-environments)

  Making a personal dev environment "managed" is not free-and-clear, though:
  > "A managed environment isn't included as an entitlement in the Developer Plan when users run
  > their assets."
  — [Managed environments — Licensing](https://learn.microsoft.com/power-platform/admin/managed-environment-licensing)

  So the 60-day idle threshold exists, but the licensing doc frames staying compliant on it as
  needing a premium license anyway — it isn't a documented, unconditionally-free route past 60
  days. Even taken at face value, 60 days is not *more* than 60 days, and the environment still
  disables (breaking any live e2e run) at exactly that point, plus 15 more days to deletion.

**The one legitimate free lever that *does* work:** the cleanup mechanism is inactivity-based, and
"activity" includes unattended automation, not just human logins:
> "Activity includes automated behaviors such as scheduled flow runs. For example, if there's no
> user, maker, or admin activity in an environment, but it contains a cloud flow that runs daily,
> then the environment is considered active."
— [Automatic deletion of Power Platform environments § Inactivity-based cleanup](https://learn.microsoft.com/power-platform/admin/automatic-environment-cleanup#inactivity-based-cleanup)

and admins can force this directly:
> "Once environment administrators receive notification that an environment will be cleaned up,
> environment admins can trigger activity on the environment... On the Environment page, select
> Trigger environment activity."
— [same page § Trigger activity, re-enable, and recover an environment](https://learn.microsoft.com/power-platform/admin/automatic-environment-cleanup#trigger-activity,-re-enable,-and-recover-an-environment)

This means a **free Developer environment with a scheduled heartbeat** (a daily cloud flow, or
even a periodic `crm` CLI CRUD call against it from the routine itself) resets the 30-day idle
clock indefinitely and never needs to survive true dormancy — it just needs *some* automated touch
inside every 30-day window. That is a materially different, and cheaper, proposal than "a free env
that tolerates being ignored," and worth weighing against a paid SKU when ADR 0012 is revisited.

---

## Q7 — S2S / service-principal stability across org swaps

**The Entra app registration is reusable; the Dataverse-side binding is per-environment.**

- The Microsoft Entra ID application (client ID / secret) is a tenant-level Entra object. Nothing
  in the docs ties it to a single Dataverse environment — it's the **application user** row that's
  scoped per-environment:
  > "In an environment, **only one application user for each Microsoft Entra ID registered
  > application is supported**."
  — [Use single-tenant server-to-server authentication § Application user creation](https://learn.microsoft.com/power-apps/developer/data-platform/use-single-tenant-server-server-authentication#application-user-creation)
- Standing up a new environment in the same tenant therefore needs, **per new environment**: (1) an
  application user created for the existing app registration, and (2) a (custom) security role
  assigned to it — the app registration itself does not need to be recreated:
  > "You must add the Application ID as an Application User in the Microsoft Power Platform
  > environment you're connecting to."
  — [Power Platform Build Tools § Configure environment with the Application ID](https://learn.microsoft.com/power-platform/alm/devops-build-tools#configure-service-connections-using-a-service-principal)

  For multi-tenant/ISV-style apps, the equivalent step is spelled out in
  [Use multi-tenant server-to-server authentication § Create an application user](https://learn.microsoft.com/power-apps/developer/data-platform/use-multi-tenant-server-server-authentication#create-an-application-user-associated-with-the-registered-application-in-dataverse),
  and the general "how" is [Create an application user](https://learn.microsoft.com/power-platform/admin/manage-application-users#create-an-application-user).
- Scriptably via `pac`:
  - `pac admin application register --application-id <id>` registers an **existing** Entra app with
    the Power Platform tenant (no new app/secret minted) —
    [`pac admin` reference](https://learn.microsoft.com/power-platform/developer/cli/reference/admin#pac-admin-application-register).
  - `pac admin assign-user --environment <newEnvId> --user <existingAppId> --application-user --role "System Administrator"`
    then creates the application user + role binding in the *new* environment, reusing the same
    app ID/secret — [`pac admin assign-user`](https://learn.microsoft.com/power-platform/developer/cli/reference/admin#pac-admin-assign-user).
  - `pac admin create-service-principal --environment <id>` is the all-in-one convenience path, but
    it **mints a brand-new** Entra app + client secret each time it's run —
    [`pac admin create-service-principal`](https://learn.microsoft.com/power-platform/developer/cli/reference/admin#pac-admin-create-service-principal) —
    so it's the wrong verb if the goal is reusing `agent-cloud`'s existing client ID across a swap.

**Bottom line for #760:** an org swap does **not** require re-registering the Entra app or
rotating the client secret crm already has saved in the profile — it requires re-running the
application-user + security-role step against the new environment only.

---

## Q8 — Region / hostname variance

Dataverse/Dynamics 365 URLs encode the datacenter region as a **numeric suffix on the `crm`
label**, not as a subdomain depth or path:

| Region | URL |
|---|---|
| NAM | `crm.dynamics.com` |
| SAM | `crm2.dynamics.com` |
| CAN | `crm3.dynamics.com` |
| EUR | `crm4.dynamics.com` |
| APJ | `crm5.dynamics.com` |
| OCE | `crm6.dynamics.com` |
| JPN | `crm7.dynamics.com` |
| IND | `crm8.dynamics.com` |
| GCC | `crm9.dynamics.com` |
| GBR | `crm11.dynamics.com` |
| FRA | `crm12.dynamics.com` |
| ZAF | `crm14.dynamics.com` |
| UAE | `crm15.dynamics.com` |
| GER | `crm16.dynamics.com` |
| CHE | `crm17.dynamics.com` |
| NOR | `crm19.dynamics.com` |
| SGP | `crm20.dynamics.com` |
| KOR | `crm21.dynamics.com` |
| SWE | `crm22.dynamics.com` |
| DEU (sovereign) | `crm.microsoftdynamics.de` |
| GCC High | `crm.microsoftdynamics.us` |
| CHN | `crm.dynamics.cn` |

— [Datacenter regions](https://learn.microsoft.com/power-platform/admin/new-datacenter-regions)

**A `*.crm.dynamics.com` wildcard does NOT cover other regions.** Each region other than NAM is a
*different second-level label* (`crm2`, `crm4`, `crm11`, …), which a wildcard scoped to
`*.crm.dynamics.com` does not match — it only matches the NAM region's third-level subdomains
(`{org}.crm.dynamics.com`). The firewall allow-list documentation makes this explicit by requiring
a **separate entry per region's numeric suffix**:
> "Replace # in `http://*.crm#.dynamics.com` and `https://*.crm#.dynamics.com` with your region's
> number: Asia/Pacific: 5; Canada: 3; Europe, Africa, and Middle East: 15 and 4; France: 12;
> Germany: 16; India: 8; Japan: 7; Korea: 21; North America: no number; Norway: 19; Oceania: 6;
> Singapore: 20; South Africa: 14; South America: 2; Switzerland: 17; UAE: 15; United Kingdom: 11;
> Dynamics 365 US Government: 9."
— [Power Platform URLs and IP address ranges § Microsoft's consolidated domain initiative](https://learn.microsoft.com/power-platform/admin/online-requirements#microsoft's-consolidated-domain-initiative)

Practical consequence for crm: any hardcoded host allow-list/regex in the e2e harness or a TLS
pinning rule keyed on `*.crm.dynamics.com` silently excludes every non-NAM org; a genuinely
region-agnostic match needs a pattern like `*.crm*.dynamics.com` (or an explicit per-region list).

---

## Q9 — Scripted provisioning via `pac admin`

**Yes — `pac admin create` provisions Trial, SubscriptionBasedTrial, Developer, Sandbox,
Production, or Teams environments non-interactively**, and can request a Dynamics 365 app template
at creation time:

```powershell
pac admin create `
  --name "Contoso Test" `
  --type Trial `
  --domain ContosoTest `
  --templates "D365_CustomerServicePro"
```

- `--type` accepts exactly `Trial | Sandbox | Production | Developer | Teams |
  SubscriptionBasedTrial`.
- `--templates` "Sets the Dynamics 365 app that needs to be deployed, passed as comma separated
  values. For example: `-tm "D365_Sample, D365_Sales"`."
- `--input-file` accepts the same fields as JSON for fully unattended/CI use.

— [`pac admin` reference § pac admin create](https://learn.microsoft.com/power-platform/developer/cli/reference/admin#pac-admin-create)

The available template names are enumerated by `pac admin list-app-templates`, which lists
`D365_CustomerService` and `D365_CustomerServicePro` among others —
[`pac admin` reference § pac admin list-app-templates](https://learn.microsoft.com/power-platform/developer/cli/reference/admin#pac-admin-list-app-templates).
**Caveat:** the sample output in that same doc shows `D365_CustomerService` and
`D365_CustomerServicePro` both flagged `Is Disabled: True` for the `unitedstates` region in the
worked example — i.e. the CLI can *name* the template, but whether the *install actually succeeds*
still depends on the tenant holding the underlying Dynamics 365 app entitlement. This is consistent
with, and gives primary-source backing to, ADR 0012's 2026-06-22 addendum finding that "a generic
Trial environment cannot install Customer Service (a Dynamics 365 license is required)" — `pac`
does not bypass that licensing wall, it only automates the same admin-center flow.

For post-creation installs from Microsoft Marketplace-listed Dataverse apps (a different mechanism
from `--templates`), `pac application install --environment-id <id> --application-name <name>`
is the scriptable path —
[`pac application` reference](https://learn.microsoft.com/power-platform/developer/cli/reference/application).

---

## Q4 detail — cheapest non-expiring Dataverse SKU (pricing caveat)

Two figures were found, from two different classes of source, and they disagree on which SKU is
"cheapest," which is worth flagging rather than silently picking one:

- The **Microsoft Learn licensing FAQ** page states: *"Power Apps per app enables individual users
  to run (one custom app or access one Power Pages website) for a specific business scenario based
  on the full capabilities of Power Apps for **$5/user/app/month**."* —
  [Power Platform licensing FAQs § Power Apps](https://learn.microsoft.com/power-platform/admin/powerapps-flow-licensing-faq#power-apps).
  This SKU still appears to exist as a distinct admin concept (allocated to an *environment*, not
  assigned to a user) per [About Power Apps per app plans](https://learn.microsoft.com/power-platform/admin/about-powerapps-perapp).
- The **current official marketing pricing page** (`microsoft.com/power-platform/products/power-apps/pricing`,
  not a Learn doc) does **not** list a per-app price tile as of this pass — it shows only "Power
  Apps Premium" at **$20/user/month** (paid yearly), a 2,000-seat-minimum Premium tier at
  $12/user/month, and a Dataverse Database Capacity add-on at $40/GB/month.

**[UNVERIFIED / date-sensitive]** — it's unclear from these two sources alone whether the per-app
plan's $5 figure is current or a stale cached value from an older Learn revision; the admin
mechanics doc (`about-powerapps-perapp`) still describes it as an active, purchasable plan with no
deprecation notice, but its price isn't independently confirmed on the current pricing page. Get a
live quote/SKU check before treating $5/user/app/month as authoritative.

For a SKU that ships **Customer Service preinstalled** (removing the CS-entitlement wall from Q9),
the official pricing page confirms:
- **Dynamics 365 Customer Service Professional** — **$50/user/month**, paid yearly.
- **Dynamics 365 Customer Service Enterprise** — **$105/user/month**, paid yearly.
(Source: `microsoft.com/en-us/dynamics-365/products/customer-service/pricing`, official pricing
page but not Microsoft Learn; a 40%-off promo is advertised through June 30 2026, which is exactly
the kind of time-boxed detail that will be stale by the time this is read — reverify at purchase
time.)

Either paid SKU satisfies the "1 GB of available database capacity" creation prerequisite stated in
[Create and manage environments § Create an environment with a database](https://learn.microsoft.com/power-platform/admin/create-environment#create-an-environment-with-a-database).

---

## Bottom line (input to reopening ADR 0012 — not a decision)

- **No free tier cleanly survives >60 days of true dormancy.** Trial (standard) hard-caps at 60
  days even with its one extension and independently dies at 14 days idle for D365-app trials;
  Trial (subscription-based) is bound to a subscription term outside crm's control (the exact
  mechanism that killed the current `agent-cloud` org — see #760); the Developer Plan's 60-day
  variant requires "managed environment" status, which Microsoft's own licensing docs say isn't a
  Developer-Plan entitlement.
- **The one real free lever is a heartbeat, not durability.** Automated activity (a daily flow, or
  a scheduled `crm` CLI touch) resets the Developer environment's 30-day idle clock indefinitely,
  per Microsoft's own definition of "activity." This turns "free and unattended" into "free and
  lightly attended by automation" — cheaper than any paid SKU, but it adds a dependency (the
  heartbeat itself must never silently stop) that a paid, non-expiring environment does not have.
- **The honest trade is free-but-fragile vs. paid-but-durable**, and the paid side has a real price
  floor: ~$5–$20/user/month for bare Dataverse capacity (SKU/price needs a live requote per the Q4
  caveat), or $50/user/month for Customer Service Professional if the CS-install wall needs to be
  cleared without `pac`/template gymnastics.
- **S2S continuity is not a blocker either way.** The existing `agent-cloud` Entra app registration
  (client ID/secret already in the profile) can be reused verbatim against any new environment in
  the same tenant — swapping orgs only requires re-creating the application user + security role
  in the new environment (`pac admin application register` + `pac admin assign-user
  --application-user`), not rotating credentials.
- **Scripted provisioning is real but licensing-gated.** `pac admin create --type
  Developer|Trial|Sandbox|Production --templates D365_CustomerServicePro` is a genuine
  non-interactive path (good for reproducing a dedicated org from a script/runbook), but the
  `--templates` parameter names an app to deploy — it does not grant the entitlement to deploy it.
  Any durable-org runbook still needs the tenant to hold a real Dynamics 365/CS license first.

The actual choice — pay for a durable Dataverse+CS environment vs. keep cycling ephemeral
trials/heartbeat-a-free-dev-env — is a cost/ops trade-off for a human to make when ADR 0012 is
reopened; this report intentionally stops short of recommending one.

---

## Sources

- [About trial environments](https://learn.microsoft.com/power-platform/admin/trial-environments)
- [Automatic deletion of Power Platform environments](https://learn.microsoft.com/power-platform/admin/automatic-environment-cleanup)
- [Power Platform environments overview](https://learn.microsoft.com/power-platform/admin/environments-overview)
- [Create and manage environments in the Power Platform admin center](https://learn.microsoft.com/power-platform/admin/create-environment)
- [Power Apps Developer Plan Guide: Features and Benefits](https://learn.microsoft.com/power-platform/developer/plan)
- [Environment routing](https://learn.microsoft.com/power-platform/admin/default-environment-routing)
- [Enable managed environments](https://learn.microsoft.com/power-platform/admin/managed-environment-enable)
- [Managed environments — Licensing](https://learn.microsoft.com/power-platform/admin/managed-environment-licensing)
- [Power Platform licensing FAQs](https://learn.microsoft.com/power-platform/admin/powerapps-flow-licensing-faq)
- [About Power Apps per app plans](https://learn.microsoft.com/power-platform/admin/about-powerapps-perapp)
- [Dynamics 365 Sales trial FAQ](https://learn.microsoft.com/dynamics365/sales/sales-trial-faq)
- [Dynamics 365 Field Service trial FAQ](https://learn.microsoft.com/dynamics365/field-service/trial-faq)
- [Dynamics 365 Customer Service trial FAQ](https://learn.microsoft.com/dynamics365/customer-service/implement/trial-faq)
- [Use single-tenant server-to-server authentication](https://learn.microsoft.com/power-apps/developer/data-platform/use-single-tenant-server-server-authentication)
- [Use multi-tenant server-to-server authentication](https://learn.microsoft.com/power-apps/developer/data-platform/use-multi-tenant-server-server-authentication)
- [Create users § Create an application user](https://learn.microsoft.com/power-platform/admin/create-users#create-an-application-user)
- [Microsoft Power Platform Build Tools for Azure DevOps](https://learn.microsoft.com/power-platform/alm/devops-build-tools)
- [Creating a service principal application using API (preview)](https://learn.microsoft.com/power-platform/admin/powerplatform-api-create-service-principal)
- [`pac admin` command reference](https://learn.microsoft.com/power-platform/developer/cli/reference/admin)
- [`pac application` command reference](https://learn.microsoft.com/power-platform/developer/cli/reference/application)
- [Datacenter regions](https://learn.microsoft.com/power-platform/admin/new-datacenter-regions)
- [Power Platform URLs and IP address ranges](https://learn.microsoft.com/power-platform/admin/online-requirements)
- [Dataverse capacity-based storage overview — change log](https://learn.microsoft.com/power-platform/admin/whats-new-storage)
- Official pricing pages (not Microsoft Learn — reverify before purchase):
  [Power Apps pricing](https://www.microsoft.com/en-us/power-platform/products/power-apps/pricing),
  [Dynamics 365 Customer Service pricing](https://www.microsoft.com/en-us/dynamics-365/products/customer-service/pricing)
- [docs/adr/0012-dedicated-cloud-e2e-org.md](../adr/0012-dedicated-cloud-e2e-org.md)
- [GitHub issue #760](https://github.com/Gharib89/crm/issues/760)
