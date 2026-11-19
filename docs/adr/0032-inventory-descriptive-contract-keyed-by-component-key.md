---
status: accepted
---

# Migration inventory: a descriptive contract keyed by component key

A migration from CRM 2011 through on-prem v9.1 to the cloud needs comparable
views of each stage (#1034). We define the **migration inventory**: a versioned,
protocol-independent manifest plus raw artifacts that the legacy SOAP adapter
(#1033) and the v9/cloud Web API paths both emit. It is a new format, separate
from `solution export-spec`, and it matches records across stages by
**component key**, not by GUID.

The legacy adapter is the read-only, CRM 2011-only exception to ADR 0003 that
ADR 0031 records (it lands with #1043). This ADR adds no transport: the contract
is protocol-independent, and ADR 0003 still governs every v9/cloud path.

## Decision

- **Descriptive, not desired state.** A migration inventory records everything deployed,
  including what cannot be re-created from a spec: plug-in registrations whose
  binaries live on disk or in the GAC, processes, every form, ribbon and sitemap
  XML. `export-spec` stays apply-seedable only (ADR 0019); neither format grows
  into the other.
- **Matched by component key.** Each kind defines a natural key (an entity's
  logical name, a web resource's name, a plug-in type's assembly plus full type
  name, since one class name can be registered from two assemblies). The source
  GUID is recorded but never decides a match, because not every migration route
  preserves it. A key shared by two records in one migration inventory is reported as
  ambiguous.
- **Completeness is data.** Each kind carries its **kind coverage**, and what the
  source cannot expose is an **extraction gap** entry. A capped or truncated read
  must say so: equal counts never prove equivalence.
- **Versioned by an integer `schema_version`.** It changes only on a breaking
  change. Additive fields keep the version. A reader refuses an unknown version
  and ignores unknown fields.
- **Published state only.** Extractors read the published customization and
  record `publication_state`. Nothing publishes to obtain a migration inventory.

## Considered options

- **Extend `export-spec`.** Rejected: its invariant (ADR 0019) exists so its
  output can seed a second org; adding plug-ins, processes and every form would
  break that invariant or force a second, diff-only mode inside one format.
- **Match by GUID.** Rejected: it reports every re-created component as removed
  plus added, and silently pairs nothing when a route re-keys components.
- **Treat `solution export` zips as the migration inventory.** Rejected as the contract:
  they are a raw artifact source an extractor may use, but they carry no
  coverage evidence, no gaps, and no stable cross-stage shape.

## Amendments

- **2026-11-05 - schema_version 2 keys the null ribbon and sitemap `""` ([#1070](https://github.com/Gharib89/crm/issues/1070)).** v1 gave the application ribbon the key `application` and the default sitemap `default`. An org with an entity named `application` (measured on a Dataverse trial org, object type code 1204) then held two ribbons keyed `application`, and `diff`, `packages` and the source map, which all join on `(kind, key)`, lost one of them. v2 drops the special names: a null key field is the empty part, as it already was for every other nullable key field, so these two records key `""`, which no entity logical name or sitemap unique name can be. Pairing on `fields.entity` instead was rejected: it patches three consumers and leaves every future `(kind, key)` join exposed. The reader upgrades v1 on read by the field, so old manifests stay usable. Decision 3 (GUIDs never decide a match) stands. Status stays **accepted**.
- **2026-11-06 - schema_version 3 adds the `ribbon_diff` kind ([#1100](https://github.com/Gharib89/crm/issues/1100)).** The `ribbon` record is the merged ribbon (system plus customizations), so it cannot tell a rule that reads a custom button (#1101) or a convert patch that edits one (#1104) which part is the customization. The customization is the `RibbonDiffXml` in a solution export's `customizations.xml`, one for the application and one per entity. A new kind, keyed by entity logical name (`""` for the application, as `ribbon`), keeps the raw diff as its artifact and keeps only diffs with content (measured on a CRM 2011 org: 16 of 259 held any). The export is the only read for it, and Dataverse online refuses to export the Default solution, so a whole-org cloud run records `unsupported` with a gap and a `--solution` run reads the named solutions. The version moves because a v3 manifest requires the new coverage entry; the reader upgrades v1 and v2 with `unsupported` (not read), never an empty `complete`. Decision 3 stands. Status stays **accepted**.
- **2026-11-11 - schema_version 4 adds the `report` kind ([#1205](https://github.com/Gharib89/crm/issues/1205)).** A migration to Dataverse online needs each report's data provider, since online runs only the FetchXML one (#1217's classifier). A new kind, keyed by report name (the source GUID is recorded as `id` and never matched on, decision 3), keeps the RDL body as one text artifact and records `data_provider` (`fetch`, `sql` or `unknown`). Same-name reports are `ambiguous` and each keeps its own artifact path. A Reporting Services report whose body the endpoint did not return reads `unknown` with a reason, never `fetch` or `sql`, and is one extraction gap naming its key: the kind stays `complete` when every row was read, and a diff reads only that report as `not_comparable`, so a missing body never reads as a changed report. The version moves because a v4 manifest requires the new coverage entry; the reader upgrades v1, v2 and v3 with `unsupported` (not read), never an empty `complete`. Decision 3 stands. Status stays **accepted**.
- **2026-11-19 - schema_version 5 adds five security and configuration kinds ([#1340](https://github.com/Gharib89/crm/issues/1340)).** A stage diff could not show a lost security role, field security profile, duplicate-detection rule, email template or connection role, and `assess` could not list them as uncovered. Each is a new kind keyed by its natural key, never its GUID (decision 3): `security_role` by name, `field_security_profile` by name, `duplicate_rule` by base entity and name, `email_template` by template type and title, `connection_role` by name. Roles are read from the root business unit only, because every child business unit holds a copy of each role under the same name, and the copies would read as ambiguous. A key two records share is `ambiguous`, as for every kind. Each record carries the child rows a diff must see change: a role its privilege depths by privilege name, a profile its column permissions by `<entity>.<attribute>`, a rule its conditions, and a template its body as a text artifact. Duplicate rules carry no `ismanaged` or `solutionid` on CRM 2011 or on-premises v9 (measured: the read faults), so those two columns are read online only, and an on-premises stock-scoped run keeps every rule with the existing no-`ismanaged` gap. `packages` makes one package per new kind, since no reference connects these records to anything. The version moves because a v5 manifest requires the five coverage entries; the reader upgrades v1 to v4 with `unsupported` (not read), never an empty `complete`. Decision 3 stands. Status stays **accepted**.
- **2026-11-19 - schema_version 6 adds SLAs, model-driven apps, alternate keys, environment variables and connection references ([#1344](https://github.com/Gharib89/crm/issues/1344)).** A stage diff could not show a lost SLA, app, alternate key, environment variable or connection reference, and `assess` could not list them as uncovered. Each is a new kind keyed by its natural key, never its GUID (decision 3): `sla` by name, `app_module` by unique name, `alternate_key` by entity and key schema name (the record carries its columns), `environment_variable` by schema name (the record carries the default value; the org's current value is not read, since it is set per environment by design and a text variable can hold a credential), `connection_reference` by logical name. An SLA records its entity's logical name, not the `primaryentityotc` code, which is assigned per org for a custom entity. The four tables behind `sla`, `app_module`, `environment_variable` and `connection_reference` answer over the Web API on an on-premises 9.1 org and a Dataverse org (measured: each read complete on both). A key two records share is `ambiguous`, as for every kind. Alternate keys come from the `Keys` of the per-entity metadata read the extractor already makes (measured on an on-premises 9.1 org and a Dataverse org), so they follow their entity through `--solution` like an attribute. The platform makes some unmanaged keys itself (measured: 51 on a Dataverse org under three schema names), and user keys read `IsCustomizable` false too, so those three names are what mark a key stock. A connection reference's solution component type is assigned per org (measured: 10167 on a Dataverse org), so a membership of type 10000 or above is resolved by the component's id. None of the five exists on CRM 2011: a crm2011 run issues no read for them and records each `unsupported` with that reason. `packages` makes one package per new kind, as for v5. The version moves because a v6 manifest requires the five coverage entries; the reader upgrades v1 to v5 with `unsupported` (not read), never an empty `complete`. Decision 3 stands. Status stays **accepted**.
