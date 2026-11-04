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
