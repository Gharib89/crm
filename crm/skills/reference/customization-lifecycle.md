# Customization lifecycle — end to end

The connective tissue for schema-and-component work: the **order** customizations go in,
the disciplines that span domains, and how a change rides from one environment to the
next. Per-command flags and per-domain depth live in the sibling files linked below —
this file is the map, not the reference. Start here when the task is "customize this org"
and you don't yet know which group to reach for.

D365 / Dataverse ALM (MS Learn "Use a solution to customize", "ALM basics with Power
Platform"): build in a **dev** org inside your **own unmanaged solution**, export it **as
managed**, import that managed zip into **test** then **prod**. Never customize in the
default solution/publisher (wrong prefix, can't export), and never hand-edit a managed
component on a downstream org — that active unmanaged layer silently blocks future managed
updates.

## The shape of a customization job

Canonical order; each step's detail lives in the linked file:

1. **Publisher + solution first.** `solution create-publisher`, then `solution create` —
   every component you author next inherits the publisher prefix and lands in this
   solution. → `reference/solutions.md`
2. **Author schema.** Tables, columns, option sets, relationships — declaratively with
   `apply -f` / `scaffold table`, or imperatively with the `metadata create-*` verbs.
   → `reference/authoring.md`, `reference/metadata.md`
3. **UI layer.** Views (`view create`), forms (`form`), web resources (`webresource`),
   ribbon buttons (`ribbon`), model-driven app + sitemap (`app`).
   → `reference/authoring.md` (views), `reference/customizations.md` (the rest)
4. **Automation.** Plug-in assembly + steps (`plugin`), workflows (`workflow`), SLAs
   (`sla`). → `reference/automation.md`
5. **Publish once**, then **verify it landed** (disciplines 3–4 and 6 below).
6. **Package + promote.** export (managed) → validate → import to the next org → verify.
   → `reference/solutions.md`

**Dependency order bites.** A ribbon button needs its web resource to exist first; a new
table is invisible in an app until a sitemap subarea references it; a plug-in step needs
its assembly registered and its type confirmed first. The sibling files call out each.

## Cross-cutting disciplines (the things that bite)

**1 — Publisher + solution before anything.** With a named profile active,
`create-publisher` / `create` auto-wire `publisher_prefix` + `default_solution` into the
profile, so every `metadata create-*` afterward targets the right prefix/solution without
repeating `--solution`. Skip this and components fall into the default solution with the
wrong prefix and can't be cleanly exported.

**2 — Thread `--solution` for components that don't auto-wire.** `webresource`, `plugin
register-assembly`, `app`, and friends take `--solution <unique_name>` to drop the
component into your solution; omitting it strands it in the Default solution, outside your
export. (The `metadata` verbs already honor the profile default from discipline 1.)

**3 — Stage many, publish once.** Each `metadata create/update` auto-publishes, and a
publish is slow and disruptive. Across a batch, set `--stage-only` (or `CRM_STAGE_ONLY=1`)
on every write, then run `solution publish-all` **once** at the end. This matters beyond
speed: **only published customizations export** — an unpublished change silently drops out
of the solution zip. → `reference/authoring.md`

**4 — Publish-before-read, then poll.** A just-published metadata change takes a beat to
propagate. Gate the read with `metadata ... --expect ATTR=VALUE` and retry on mismatch
rather than trusting an immediate read. → `reference/metadata.md`

**5 — `apply -f` vs imperative.** Use `apply -f spec.yaml` (or `scaffold table`) to stand
up a whole table idempotently in dependency order with a single publish — re-applying an
unchanged spec is a no-op, and `metadata export-spec` round-trips a live entity back into
a spec for the next environment. Use the imperative `metadata create-*` verbs for a single
targeted change to an existing table. → `reference/authoring.md`

**6 — `--dry-run` first on every write.** Reads still fire under dry-run, so a preview
reports live facts (`_exists`, `would_skip`) not guesses; `meta.dry_run: true` flags it.
Validate a record payload with `entity ... --validate` before committing — but note
`--validate` is **record-write-only** (`entity create`/`update`): `metadata create-*`,
`solution`, and component writes have **no `--validate`** (their typed flags are checked
client-side), so `--dry-run` — plus `--expect` after publish — is their pre-write net.

## Promote to the next environment

```bash
crm solution set-version MyApp --version 1.2.0.0           # bump (unmanaged only)
crm solution publish-all                                   # unpublished == not exported
crm solution export MyApp -o myapp_managed.zip --managed   # ship downstream orgs MANAGED
crm solution validate myapp_managed.zip --against-org      # offline + live pre-flight
crm solution import myapp_managed.zip --yes                # into test, then prod
crm --json solution components MyApp --diff expected.json  # verify the target landed
```

On-prem imports run synchronously (no async job to poll); cloud returns an async op to
watch. A failed import has a full post-mortem path (`solution import-result`,
`job-status`), and the managed-upgrade verbs (`clone-as-patch`,
`stage-and-upgrade --promote`, `uninstall`) handle the patch / holding-solution dance.
→ `reference/solutions.md`

## Don't reach past the API

Some customizations have **no Web API write path**, and the CLI says so rather than faking
it: ribbon `RibbonDiffXml` (solution-zip pipeline only — that's why a cloned entity's
ribbon doesn't come across), and early-bound class generation (external `CrmSvcUtil.exe` /
`pac modelbuilder`, never a `crm` verb).
→ `reference/customizations.md`, `reference/automation.md`
