# How-to: org

`crm org` holds org-level orientation commands. Its first verb, `crm org brief`,
is a single **read-only** call that returns a summarized inventory of the whole
org — **counts and key names only, never full rows** — shaped for an agent's
context budget. It is the org-level analogue of `metadata describe` (the
per-entity write-readiness brief): orientation before work.

Point it at an unfamiliar org before assembling schema, queries, or writes,
instead of stitching together six fat list verbs (`solution list`,
`metadata entities`, `webresource list`, `plugin list`, `workflow list`,
`app list`), each of which can return hundreds to tens of thousands of rows.

See the [CLI reference](../reference/cli.md) for the full flag list.

## One-call inventory

```bash
crm --json org brief
```

```json
{
  "ok": true,
  "data": {
    "identity": {
      "profile": "contoso", "url": "https://internalcrm.contoso.local/api/data/v9.2/",
      "org_name": "Contoso", "version": "9.2.24091.00196", "api_version": "v9.2",
      "user_id": "…", "organization_id": "…"
    },
    "solutions": {
      "managed": 42, "unmanaged": 3,
      "unmanaged_names": ["ContosoCore", "ContosoExt"], "unmanaged_names_total": 2
    },
    "publishers": {
      "count": 2,
      "items": [{"unique_name": "contoso", "prefix": "cts", "friendly_name": "Contoso"}],
      "items_total": 2
    },
    "schema": {
      "custom_entities": 12, "custom_entity_names": ["cts_widget"],
      "custom_entity_names_total": 12, "global_optionsets": 30
    },
    "apps": {"count": 2, "names": ["Sales Hub"], "names_total": 2},
    "automation": {
      "plugin_assemblies": 4, "plugin_steps": 37,
      "workflows": {"total": 9, "by_category": {"workflow": {"total": 5, "activated": 4}}},
      "slas": 1
    },
    "components": {
      "webresources": {"total": 88, "by_type": {"html": 5, "css": 3, "script": 40}},
      "security_roles_custom": 6, "duplicate_rules": 2
    }
  },
  "meta": {"custom_entities": 12, "solutions": 45, "apps": 2, "plugin_steps": 37,
           "workflows": 9, "profile": "contoso", "url": "…"}
}
```

- **identity** — org name, version, and the serving `profile` / `url`, so the
  brief is self-identifying without GUID-matching.
- **solutions** — managed / unmanaged counts, plus the unmanaged **non-default**
  names in `unmanaged_names` (the `Default` and `Active` system solutions are
  excluded). These are your candidate `--solution` targets for a customization write.
- **publishers** — each publisher's **customization prefix**: what a new
  component's schema name must start with. Pick the prefix here before authoring.
- **schema** — custom entity count + logical names, and the global option-set count.
- **apps / automation / components** — model-driven app names; plug-in and
  workflow counts (workflows broken down by category and activated state); SLA,
  web-resource (by type), custom-security-role, and duplicate-rule counts.

Name lists are capped (the true total rides alongside as `*_total`), so a large
org never floods the payload.

## Read-only and target-agnostic

`org brief` issues only GETs — it is always safe to run first, including under
`--dry-run` (reads execute). The same command works against on-prem (NTLM, v9.1)
and Dataverse online (OAuth, v9.2).

The request count is a fixed constant independent of org size — that bound is the
point, so the brief stays cheap where the fat verbs it replaces do not.

!!! note "Counts saturate at 5000"
    Dataverse caps the `$count` annotation the brief uses at 5000: a set with more
    rows reports exactly `5000`. Treat a `5000` count as "at least 5000", not exact.
    An exact count past that would need `RetrieveTotalRecordCount` (out of scope —
    its availability differs by target).

## Human-readable

Without `--json`, `org brief` prints one labeled table per section:

```bash
crm org brief
```
