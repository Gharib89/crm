# Customizations as code — repo-driven promote

Reach here when the **source of truth is a git repo of specs** and changes ride
between orgs as **plan artifacts** — a desired-state loop: edit `spec.yaml`,
`--dry-run apply` to diff, commit a plan, execute exactly it with `--from-plan`.
For the classic **zip-based** promote (export managed → import downstream), use
`customization-lifecycle.md` instead — that file keeps the zip spine as the
alternate promote model.

**Repo layout** (convention only — `crm` reads/writes these, it does not scaffold
them):

- `spec.yaml` — the desired-state spec (publisher, solution, entities, option
  sets, web resources, roles, plug-ins, model-driven app + sitemap).
- `webresources/`, `plugins/` — source files the spec's `file:` paths resolve
  against (relative to `spec.yaml`).
- `plans/<target>/<YYYY-MM-DD>-<slug>.plan.json` — one subdir per target org;
  commit the plan in the change PR (it is the review object), keep executed plans
  as an audit trail.

## Spine 1 — seed: live org → repo

```
connection whoami            → confirm the SOURCE org
solution export-spec <name> -o spec.yaml   → project the whole solution to an apply-ready spec
git commit spec.yaml
```

Data flow: `solution export-spec` walks the solution read-only and merges entities
(+ attributes, option sets, 1:N relationships, views, seedable main form), roles,
web resources, and model-driven apps (+ Entity-backed sitemap) into one document,
baking the top-level `solution:` block `apply` requires. Unrepresentable members
land in a `skipped` bucket — review it, don't assume full coverage.
**Verify:** `--dry-run apply -f spec.yaml` against the source reports all-no-op.

> Per-table seed instead of whole-solution: `metadata export-spec <entity>
> --with-forms --solution <name>` — but it does **not** project the app/sitemap.

## Spine 2 — change loop against dev

```
edit spec.yaml
--dry-run apply -f spec.yaml   → drift report: read the verdicts (_exists / would_create / would_update / refused)
apply -f spec.yaml --yes       → converge the dev org
```

Data flow: the dry-run verdicts are computed from live reads (reads execute under
`--dry-run`), so they reflect the real org, not guesses. A "refused" verdict means
the change would need a destructive drop-and-recreate — apply won't do it silently.
**Verify:** `--dry-run apply -f spec.yaml` reports all-no-op — confirms
convergence (read-only; reads still execute under `--dry-run`). Then commit; the
PR is the change.

## Spine 3 — plan → approve → execute (promote)

```
--profile <target> --dry-run apply -f spec.yaml -o plans/<target>/<date>-<slug>.plan.json
git commit the plan                 → the PR review object; merge = approval
--profile <target> --dry-run --from-plan <plan>   → re-verify it is still exactly true
--profile <target> apply --from-plan <plan> --yes → runs EXACTLY the plan
```

Data flow: the plan pins the target **org id**, embeds the **resolved spec**,
records a **sha256 per referenced file payload**, and fixes the **intent** (prune /
allow-data-loss / stage-only) + a per-component verdict. `--from-plan` replays that
intent and **takes no intent flags** — it refuses `--prune` / `--allow-data-loss` /
`--stage-only` / `-o`. Cut the plan against the **target** profile (not dev), and
execute from a machine that can reach the target (on-prem is VPN-locked).
**Verify:** after apply, `--json --dry-run apply -f spec.yaml | jq .data` is
all-no-op (read-only check).

## When the plan goes stale

If the target drifted since the plan was cut, `--from-plan` finds the live state no
longer matches: **zero writes, exit 1, a per-component divergence report** (`plan
said …, live now computes …`). Do not force it — **re-plan** against the current
target, re-approve, execute the fresh plan.

## Edges of the surface

- **Ribbon / command bar** — no Web API write path; goes through the solution-zip
  export → edit `RibbonDiffXml` → import → publish pipeline (see
  `webresource-ribbon.md`), not the spec.
- **A single targeted change** — the imperative verbs (`form`, `app`, `sitemap`,
  `metadata create-*`) are the faster path than editing the spec; see `forms.md`,
  `apps-sitemap.md`, `metadata.md`.
- **N:N relationships** are outside the apply surface (apply authors 1:N).
