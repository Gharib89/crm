# Customizations as code

Your org's customizations — tables, columns, option sets, relationships, views,
forms, the model-driven app and its sitemap, web resources, security roles,
plug-ins — live in a **git repo as a declarative spec**. Every change ships as a
**reviewed plan artifact**, and `crm apply --from-plan` runs *exactly* what was
approved. On-prem (NTLM) and online (OAuth), the same loop.

This is the desired-state workflow the platform doesn't give on-prem — a
`spec.yaml` you diff, a plan you review in a pull request, and an apply that
converges the live org to match. It sits alongside the classic
[zip-based solution promote](../how-to/solution.md); reach for that when you need
to ship a managed package.

## The gap this fills

As of July 2026, neither Microsoft nor any widely-known third-party tool offers a
*declarative desired-state engine with diff / plan-then-apply for Dataverse schema
and UI components* — tables, columns, forms, views, sitemaps, model-driven apps,
dashboards. Three adjacent tools come close and each stops short:

- **No desired-state engine for Dataverse schema + UI.** Microsoft's official
  Infrastructure-as-Code offering, the
  [Power Platform Terraform provider](https://learn.microsoft.com/business-applications/playbook/enterprise-solutions/power-platform-terraform-provider/),
  brings desired-state management to Power Platform **environments, tenant
  settings, governance (DLP / managed-environment) policies, connections, and
  users** — the *administrative and provisioning layer* — not to the schema/UI
  components inside a Dataverse database. Its
  [resource index](https://microsoft.github.io/terraform-provider-power-platform/)
  has no typed resource for a table, column, form, view, sitemap, model-driven
  app, or dashboard. The two escape hatches don't close the gap:
  [`powerplatform_solution`](https://microsoft.github.io/terraform-provider-power-platform/resources/solution/)
  imports a solution `.zip` as an **opaque, checksum-tracked artifact** (no diff
  of the components inside it), and
  [`powerplatform_data_record`](https://microsoft.github.io/terraform-provider-power-platform/resources/data_record/)
  is untyped **row** CRUD, explicitly "not recommended for managing business
  data." Environment provisioning exists; schema/UI desired-state does not.

- **The Dataverse MCP server is imperative — no plan gate.** Microsoft's
  [Dataverse MCP server](https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp#list-of-tools)
  exposes an imperative tool set (create / update / delete records and tables, run
  queries, manage skills and files). It has **no plan, preview, diff, or dry-run
  tool** and no convergence gate. Its only server-side safety affordance is that
  the two *delete* tools act "only after explicit user approval"; the schema
  mutations `create_table` / `update_table` apply directly, ungated. It is
  documented for **Dataverse online only**
  (`https://{org}.crm.dynamics.com/api/mcp`); no on-premises equivalent exists.

- **On-prem is excluded from cloud Git integration.** Microsoft's native
  [Dataverse Git integration](https://learn.microsoft.com/power-platform/alm/git-integration/connecting-to-git)
  requires **managed environments** and works **only against Azure DevOps** (the
  [only supported Git provider](https://learn.microsoft.com/power-platform/alm/git-integration/faqs)
  — GitHub is excluded even for online), configured through the cloud maker
  portals. All three prerequisites are cloud-only constructs, so D365 CE
  on-premises (v9.x) is excluded by construction — as it is from the rest of the
  cloud ALM stack (pipelines, Build Tools service connections, which target
  `*.crm.dynamics.com`). On-prem teams *can* put solution XML under source control
  with `SolutionPackager` / `pac solution unpack`, but that is manual packaging —
  not native Git integration, and not a desired-state engine. This is exactly the
  gap the repo-driven loop fills for on-prem.

## The model

Three nouns carry the whole workflow:

- **`spec.yaml` — the desired state.** A declarative document describing the
  components you want to exist. `crm apply` is *convergent*: a component that
  already matches is left untouched, one whose allowed fields drifted is updated
  in place, and one whose divergence would need a destructive drop-and-recreate is
  refused (no write) rather than silently rebuilt. Re-applying an unchanged spec is
  a no-op.
- **The plan — the unit of approval.** `crm apply --dry-run … -o plan.json`
  serializes the drift report to a plan artifact: it pins the target **org id**,
  embeds the **resolved spec**, records a **sha256 for each referenced file
  payload** (web-resource / plug-in files), fixes the **intent** (prune /
  allow-data-loss / stage-only), and lists a per-component verdict (would-create,
  would-update, refused, would-prune). Committed in a pull request, the plan *is*
  the review object.
- **`--from-plan` — execution.** `crm apply --from-plan plan.json` executes that
  plan and only that plan. It takes **no intent flags** — prune / allow-data-loss /
  stage-only are replayed from the plan, not re-specified. If the live org has
  moved since the plan was cut, apply **writes nothing, exits 1, and reports each
  diverged component** — a stale plan is loud on purpose (see
  [When the plan goes stale](#when-the-plan-goes-stale)).

## Repository layout

A convention, not tooling — `crm` reads and writes these files but does not
scaffold the tree. A workable shape:

| Path | What lives there |
|---|---|
| `spec.yaml` | The desired-state spec: publisher, solution, entities, option sets, web resources, security roles, plug-ins, model-driven app + sitemap. |
| `webresources/` | Web-resource source files (JS/HTML/CSS/…); the spec's `file:` paths resolve relative to `spec.yaml`. |
| `plugins/` | Plug-in assembly DLLs referenced by the spec. |
| `plans/` | Committed plan artifacts (see policy below). |

**`plans/` policy.** Name each plan
`plans/<target>/<YYYY-MM-DD>-<slug>.plan.json` — one subdirectory per target org,
so a plan is unambiguously bound to where it applies. Commit the plan in the change
PR (it is the review object) and keep executed plans as an audit trail of what
landed where.

## Seed the repo from a live org

Project an existing org's customizations into a spec, then commit it:

```bash
crm --json connection whoami                       # confirm you're pointed at the source org
crm solution export-spec MySolution -o spec.yaml   # project the whole solution → apply-ready spec
git add spec.yaml && git commit -m "seed: MySolution desired-state spec"
```

`crm solution export-spec` walks a solution's members read-only and merges them
into one apply-consumable document — entities (with attributes, option sets, 1:N
relationships, views, and the seedable main form), security roles, web resources
(inline content), and model-driven apps (identity + the Entity-backed sitemap,
under an `apps:` block). It bakes in the top-level `solution:` block that
`crm apply` requires. Members that can't round-trip a real apply (plug-ins,
additional main forms, an app's record-backed component bindings) are reported in a
`skipped` bucket rather than dropped silently — review it and fill those in by
hand where needed.

> To project a **single table** instead of a whole solution, use
> `crm metadata export-spec <entity> --with-forms --solution <name>`. It does *not*
> project the model-driven app or sitemap — use `solution export-spec` when you
> want those. See [How-to: metadata](../how-to/metadata.md).

Verify the seed round-trips before trusting it:

```bash
crm --dry-run apply -f spec.yaml     # a faithful seed reports all-no-op against the source org
```

## The everyday change loop (dev org)

Against a scratch / dev org, iterate directly:

```bash
git switch -c feat/add-priority-field
$EDITOR spec.yaml                                  # declare the change
crm --dry-run apply -f spec.yaml                   # drift report: would_create / would_update / refused
crm apply -f spec.yaml --yes                       # converge the dev org
crm --json apply -f spec.yaml | jq '.data'         # re-apply is a no-op → confirms convergence
git add spec.yaml && git commit -m "feat: add priority column"   # open the PR
```

The dry-run drift report is your read on the blast radius before any write. Reads
run for real under `--dry-run`, so verdicts reflect the live org
(`_exists`, `would_update`), not guesses. See [How-to: apply](../how-to/apply.md).

## The approval loop (promote to test / prod)

This is the golden workflow. The plan artifact is what gets reviewed and approved;
the apply that lands it takes no new intent.

```bash
# 1. Cut the plan AGAINST THE TARGET profile — this is what the reviewer reads.
crm --profile test --dry-run apply -f spec.yaml -o plans/test/2026-07-11-priority.plan.json
git add plans/test/2026-07-11-priority.plan.json
git commit -m "plan: priority column → test"      # the plan is the review object
#    → open the PR; merge = approval.

# 2. On a VPN-connected machine (on-prem is VPN-locked), re-verify then execute.
crm --profile test --dry-run --from-plan plans/test/2026-07-11-priority.plan.json   # still exactly true?
crm --profile test apply --from-plan plans/test/2026-07-11-priority.plan.json --yes # runs EXACTLY the plan
crm --json --profile test apply -f spec.yaml | jq '.data'                           # verify: all-no-op
```

`crm apply --from-plan` refuses `--prune`, `--allow-data-loss`, `--stage-only`, and
`-o` — the intent is fixed at plan time and replayed (ADR 0022). What was approved
is what runs.

## When the plan goes stale

A stale plan is loud on purpose. If the target org drifted between cutting the
plan and executing it, `--from-plan` computes the current state, finds it no longer
matches the plan, **writes nothing, exits 1, and reports every diverged component**
(`plan said …, live now computes …`). Do not force it — **re-plan** against the
current target, re-open the approval, and execute the fresh plan. The re-plan is
cheap; a forced apply against a moved org is the thing the plan gate exists to
prevent.

## What's in the spec, what isn't

**In the spec / apply surface** (created and reconciled convergently):

- tables (entities) and columns (attributes)
- global option sets
- 1:N relationships
- views
- forms (the seedable main form)
- the model-driven app and its sitemap
- web resources
- security roles
- plug-ins (assembly + types + steps + images) — apply *creates* these from a
  spec, but `solution export-spec` cannot *project* existing ones (they land in its
  `skipped` bucket), so author the `plugins:` block by hand when seeding

Re-applying converges the model-driven **app component set and sitemap**: app
components and sitemap nodes the spec no longer declares are removed on every
apply (this app-level convergence always runs, independent of `--prune`). Pruning
of solution-wide *schema* extras (tables, columns, and other components the spec
dropped) is opt-in via `--prune`, and destroying data-bearing extras additionally
requires `--allow-data-loss`.

**Not in the spec surface** — reach for the escape hatches:

- **Ribbon / command bar.** `RibbonDiffXml` has **no Web API write path**; it ships
  only through the solution-zip export → edit → import → publish pipeline. Use the
  working-copy ribbon flow (`crm ribbon export --solution` → edit → `crm ribbon
  apply`) and the zip promote. See the skill's `webresource-ribbon` reference.
- **A single targeted change** to an existing component is often faster imperatively
  (`crm metadata create-*`, `crm form`, `crm app`, `crm view`) than editing the
  spec — the imperative verbs remain the single-change path. See
  [How-to: metadata](../how-to/metadata.md).
- **N:N relationships** are outside the apply surface (apply authors 1:N).

When you need to ship a *managed* package downstream, fall back to the zip promote
in [How-to: solution](../how-to/solution.md).

## CI without an org

The promote loop is **human-in-the-loop by design**: a human approves the plan in
the PR, and a person on a VPN-connected machine runs `--from-plan`. On-prem orgs
are VPN-locked and unreachable from hosted CI, so **no automated pipeline runs
against a live org**. What CI *can* run, org-lessly:

- `crm solution validate <zip>` (offline shape checks)
- the offline test suite
- `mkdocs build --strict` and other doc/lint gates

Anything that touches an org — apply, export, whoami — is deliberately out of
hosted CI's reach. The plan artifact is what carries the org-touching change
through review instead.

## Appendix: optional cloud sandbox

For spec iteration without a dev org handy, a throwaway **online** org (a
Dataverse trial) is a convenient scratch target for the everyday change loop —
edit `spec.yaml`, `apply`, inspect, discard. Keep it strictly a dev-time
convenience: it is **never part of the promote path**, which always runs the
reviewed plan against the real target from a trusted machine.
