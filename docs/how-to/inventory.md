# How-to: inventory

`crm inventory extract` reads an org's customizations into a **migration
inventory**: a `manifest.json` in [inventory manifest v1](../reference/inventory-manifest.md)
format plus the raw artifacts it points at. An inventory describes what is
deployed; it is never a desired state for `crm apply`
([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)).

It runs on a **`crm2011` profile** for now (see [How-to: legacy](legacy.md) to
create one). A v9 on-prem or cloud profile is refused before anything is read or
written; that path is a later slice. See the [CLI reference](../reference/cli.md)
for every flag.

## Extract

```bash
crm --json inventory extract --out ./inventory --profile legacy2011
```

The run is **read-only**. Every request goes through the same SOAP allowlist as
`crm legacy`, so `PublishXml`, `PublishAllXml` and `ImportSolution` are refused,
and nothing is published first: the inventory holds the **published**
customizations (`publication: "published"`). It reads every v1 kind for the whole
org (`scope.mode: "org"`):

- entities, global option sets, and each entity's attributes, relationships and
  local option sets, from the metadata reads;
- solutions, web resources, plug-in assemblies, plug-in types, steps, step images,
  processes (workflow definitions, type 1), forms, views, sitemaps, solution
  membership and dependencies, each with one `RetrieveMultiple` paged to its last
  page (dependencies are the `dependency` entity, read whole, not one component at
  a time);
- the application ribbon and the ribbon of each entity with a display label (an
  entity with none, such as an intersect entity, has no UI and no ribbon).

Expect it to take minutes on a real org.

`DIR` receives:

```text
<DIR>/
  manifest.json
  artifacts/<kind>/<slug>-<hash>.<name>
```

The artifacts are web resource content (`content.js`, `content.png`, ...), the
plug-in assembly binary (`content.dll`), workflow XAML (`workflow.xaml`), form XML
(`form.xml`), view fetch and layout XML (`fetch.xml`, `layout.xml`), sitemap XML
(`sitemap.xml`) and ribbon XML (`ribbon.xml`).

## The summary

`data` is a short summary; the manifest holds the records, coverage and gaps:

| Key | Value |
|---|---|
| `out`, `manifest` | the output folder and the `manifest.json` path |
| `records` | the number of records written |
| `kinds` | per kind, `{status, records}`; `status` is `complete`, `partial` or `failed` |
| `gaps` | the number of extraction gaps |
| `errors` | one line per failed read |
| `reused` | per kind, the reads taken from the checkpoint instead of the org |
| `resumed` | `true` when the run continued an earlier one |
| `complete` | `true` when every kind read without an error |

## Resume an interrupted run

Each finished read (one metadata request, one entity's detail, one
`RetrieveMultiple` page, one ribbon) is saved under `DIR/.checkpoint/` before the
next starts. To resume, **re-run the same command on the same `DIR`**: finished
reads are reused, and a kind's paging continues after its last finished page from
that page's paging cookie. `extracted_at` stays the first run's start time.

`DIR` is refused when:

- it holds a checkpoint of **another org** (a different profile URL);
- it holds files but **no checkpoint**, which includes a finished inventory, so a
  new extraction is never mixed into an old one. Pass an empty or new folder.

## When a read fails

A read that faults is recorded and the run goes on (continue-and-report,
[ADR 0007](../adr/0007-record-clone-no-rollback-continue-and-report.md)): its kind's
coverage is `failed` (no records) or `partial` (some records), its error lands in
the coverage entry and in `errors`, and the manifest is still written. The command
then exits **1** with `ok: false` and the summary still in `data`, and the
checkpoint is kept, so a re-run on the same `DIR` retries only the reads that
failed. A page that reports more records but carries no paging cookie marks its
kind `truncated` and `partial`.

An auth failure (401) or an unreachable org is not per read: it stops the run with
the standard error envelope and no summary. The checkpoint keeps every finished
read, so re-run once the org answers again.

The checkpoint is removed once a run reads everything without an error.

## Extraction gaps

The manifest's `gaps` names what the SOAP endpoint cannot read, rather than leaving
it out:

- a **plug-in assembly stored on disk or in the GAC**: its record is written, but
  the binary lives on the CRM server, not in the database;
- an **ISV page** (`/ISV/...`) referenced from a web resource or the sitemap: a
  file on the CRM web server;
- **external integrations**: services that call the org or that its plug-ins and
  processes call;
- the **SQL data sources of SSRS reports**;
- the **ribbons of entities with no display label** (intersect and internal
  entities): such an entity has no UI, and CRM 2011 faults its ribbon read, so
  one gap lists them all;
- **rows naming a component this inventory does not record** (a solution
  membership or dependency on a kind v1 does not define, or whose own read failed),
  counted per kind and component type;
- the **source identity and build**, when its `WhoAmI` or `RetrieveVersion` read
  failed: the manifest's `version` then reads `unknown`, and the gap carries the
  fault. A re-run retries the read.

Collect the others by hand from the server or the people who run the integrations.
