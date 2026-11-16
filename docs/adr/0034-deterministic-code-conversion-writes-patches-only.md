---
status: accepted
---

# Deterministic code conversion is an inventory stage that writes patches only

Moving a CRM 2011 org to Dataverse means rewriting its JavaScript web resources and
C# projects. A client assessment counts 137 own scripts using `Xrm.Page`, 65 using
OData v2 and 124 old-style .NET Framework projects. We add **code conversion** to the
`crm inventory` pipeline as an offline stage, after `assess` and before `verify`. It
rewrites only what a rule can prove equivalent, and it writes **conversion patches**
and **manual-review entries** under its own `--out`, never into a repository.

## Decision

- **Scope: the deterministic tier on both sides.** It covers client API renames
  (`Xrm.Page` to `formContext`, `Xrm.Page.context` to `getGlobalContext`,
  `Xrm.Utility` to `Xrm.Navigation`) and OData v2 calls in recognised library shapes,
  rewritten to `Xrm.WebApi`. 2011 SOAP calls are detected and recorded for manual
  review only: no SOAP adapter exists, and a synchronous SOAP call cannot be proved
  equivalent to an asynchronous Web API call with a different response shape. A
  future SOAP rewrite needs its own ADR. On the C# side it covers the project
  retarget: .NET Framework 4.6.2 or later, SDK NuGet references, and
  `OrganizationServiceProxy` to `ServiceClient`. Sandbox refactors (SQL, file,
  registry, ISV pages, Silverlight) stay findings for a human or an agent.
- **Home: a verb in this CLI.** The pipeline already has the extract, component
  keys, the source map, work packages and `verify` with negative controls. A
  converter here reuses those contracts; a separate CLI would re-implement them.
- **Inputs:** JavaScript is converted from the deployed web resource text in the
  migration inventory, because which source branch matches production is unproven.
  When the source map links a web resource to a repository file, the converter also
  writes a patch against that repository path. C# is converted from the mapped
  application repository, since a deployed assembly cannot be rewritten.
- **Output: patches and a ledger under `--out`.** No verb writes into a user
  repository (`map-source` already promises this). Applying a patch is a separate,
  reviewable `git apply`.
- **No proof, no rewrite.** Where a rule cannot prove its edit equivalent (a sync
  call becoming async, DOM access, `window.top`), the code stays unchanged and the
  ledger gets a manual-review entry. A rule never inserts TODO comments, and there
  is no confidence threshold.

## Considered options

- **A separate CLI** that consumes `compatibility-findings-v1` and is free to use
  jscodeshift and Roslyn: rejected because it duplicates contracts across repos and
  needs two extra runtimes.
- **Agent-applied rewrites through the `crm-migration` skill:** rejected for the
  reason ADR 0033 gives. Such rewrites are neither deterministic nor repeatable, and
  fixtures cannot test them.
- **Existing tools (the XrmToolBox JavascriptMigrator):** rejected because it is a
  Windows GUI with no batch contract and no C# support.
- **In-place rewrite of a repository checkout:** rejected because it would be the
  first verb to write into a user repository, and a patch replays and reviews per
  component.

## Engine, rules and gate

- **tree-sitter** parses JavaScript and C#: one Python dependency with byte-range syntax
  trees, so edits stay minimal and a match is a real call, not text in a string or
  comment. Its wheels resolve for Python 3.13 on Linux, Windows and macOS arm64
  (measured, `uv pip compile --only-binary :all:`). It is imported lazily and bundled
  at all 5 PyInstaller sites. Rejected: regex and a tokenizer (they cannot prove a
  call's receiver), and jscodeshift plus Roslyn (they need Node and .NET on every
  machine).
- **Rules stay single-sourced.** A compatibility rule gains an optional `transform`
  id naming a registered Python transform. Convert patches only components that
  carry a finding, so `assess` and convert never disagree on what matched.
- **The emit gate.** A patch is written only if the patched code re-parses with no
  new error nodes and re-assessing it clears the finding. Every transform also has
  before and after fixtures in the offline suite. Behavioural proof stays with
  `inventory verify`.
- **Sequencing.** The first transforms are the deprecation-table rows that need no
  join. OData and C# transforms follow the edits the remediation pilot (#1060) finds
  repeating.

## Transforms

The census figures are measured from a client CRM 2011 solution export: 151 own scripts and 61
plug-in and workflow projects. The call-graph shares are inferred from a name-based
graph.

- **Context threading.** Registered handlers take `executionContext` and derive
  `formContext`. Helpers gain a `formContext` parameter at every call site the
  call graph resolves. Unreachable or unresolved code gets a manual-review entry.
  Rejected: a library-scoped variable, since state is shared across forms in one
  window, and an `Xrm.Page` fallback shim, which never clears the finding.
- **Registrations travel with the code.** The same work package's patches set
  `passExecutionContext` on each rewritten form handler and add `PrimaryControl` to
  ribbon actions whose function now takes it. Only 70 of 768 custom handlers pass
  context today.
- **Unit: the work package.** Calls are resolved and context threaded within one
  package. A call that crosses a package boundary gets a manual-review entry.
- **`alert` and `confirm` stay.** They are browser APIs that still run in Unified
  Interface, so they are optional modernization, not conversion.
- **Synchronous OData v2 stays synchronous.** 123 of 140 call sites are sync, so
  moving them to async would change control flow. Convert rewrites the URL to the
  Web API and routes the response through one generated adapter that returns v2
  shapes, which leaves consumer code untouched. Async remains an optional finding.
- **C# retarget.** Old-style csproj files become SDK-style, targeting `net462`, with
  `Microsoft.CrmSdk.CoreAssemblies` in place of SDK 5.0 HintPaths and signing kept.
  `Microsoft.Xrm.Client` usage gets a manual-review entry. Builds run outside convert's
  gate, on Linux with the .NET SDK: the pilot built 9 of 9 converted projects that way
  (#1059), so no Windows worker is needed.

## Surface and slices

- **`crm inventory convert`** reads the migration inventory, the findings, the
  packages, and optionally a source map and a target migration inventory. It writes
  `--out` for one work package or for all of them.
- **v4 names come from a target migration inventory** (entity set and navigation
  property names), and are never derived from 2011 by pluralising. An unresolved
  name gets a manual-review entry.
- **Org-sourced web resources** get their diff plus the full patched file and its
  hash, so `map-source` and `verify` can prove the deployed file is the patched one.
- **Slice order:**
  1. engine (tree-sitter, transform registry, ledger, patches, gate);
  2. the deprecation-table renames;
  3. context threading with the form XML and ribbon patches;
  4. the csproj retarget;
  5. the sync OData adapter, which needs a target inventory.

## Registrations

- **The extract gains the ribbon diff.** `inventory extract` stores the compiled
  effective ribbon (`RetrieveEntityRibbon`, with system and custom merged), which
  cannot be imported. The extract therefore also records each entity's and the
  application's ribbon diff, read through the read-only ExportSolution that
  `crm legacy export-solution` uses, and convert patches that diff. Measured on a
  client CRM 2011 dev org: the `Default` export took 344 s and 88 MB and holds 259
  RibbonDiffXml.
- **Registration problems are findings.** New compatibility rules on the form and
  ribbon kinds flag, for example, a handler without `passExecutionContext` whose
  library carries an `Xrm.Page` finding, or a JavaScriptFunction that calls a
  threaded function without `PrimaryControl`. Their transforms make the XML edits.
  This keeps the rule that convert patches only components carrying a finding.

## OData v2 URLs built in a variable (#1280)

On a client CRM 2011 org, convert refused 184 of 186 OData v2 call sites because the
URL is built in a variable before the `open` call. 130 of them reach a literal
`<Name>Set` through local variables assigned once.

- **The static rewrite follows the variable.** Convert follows a URL variable back to
  its assignment and rewrites the literal pieces it finds there, under the same
  emit gate and the same proof rule as an inline URL. A site whose entity set name
  is not a literal stays a manual-review entry.
- **How far it follows.** Only variables assigned exactly once, in the call's own
  function and the scopes that enclose it in the same file. It stops at a function
  parameter, a return value, a second assignment (`+=` included) and another file:
  past those, one rewrite would have to hold for every caller. Only the path after
  `OrganizationData.svc/` must be literal; the server URL in front may be any
  expression.
- **All or nothing per variable.** When one variable feeds several calls (an
  endpoint base, typically), convert rewrites it only if every call it feeds
  converts, and otherwise refuses them all. A rewritten base under a refused call
  would send a v2 query to the Web API. Rejected for now: a Web API copy of the
  variable for the converted calls only, until a prototype counts what
  all-or-nothing costs.
- **No run-time translation.** Rejected: translating v2 URLs in the browser, in the
  adapter. The adapter only reshapes Web API responses; every URL is translated at
  convert time. Run-time translation would mean a second translator in JavaScript,
  a v2 literal left in the script (so the finding never clears), and no proof step
  for a call that cannot be translated.
- **jQuery calls stay manual review.** The adapter's response hook works on a native
  `XMLHttpRequest`, which `$.ajax` and its shorthands hide. Hooking them is a
  separate mechanism with its own proof (33 of the 184 sites).
- **Writes are a follow-up.** The 28 sites that write can be automated: the URL
  rewritten statically and the adapter translating the request body at run time
  from the target inventory's attribute types, the inverse of its response
  mapping. That needs its own rule for a body attribute the adapter's table does
  not know, since the body is built at run time and a wrong write corrupts data,
  so it is decided in its own ticket: see the next section.

## OData v2 writes (#1320)

A v2 write (a create by `POST`, an update by `POST` with `X-HTTP-Method: MERGE`, a
delete by `POST` with `X-HTTP-Method: DELETE`) is rewritten with the same split as a
read: the URL and the method at convert time, the request body at run time in the
adapter. This amends "No run-time translation" above for bodies only; every URL is
still translated at convert time.

- **The method is read statically.** Convert accepts three shapes on a `POST`. No
  `X-HTTP-Method` call on the receiver is a create and keeps `POST`. Exactly one
  `setRequestHeader("X-HTTP-Method", <literal>)` on the call's receiver in the same
  function, with `MERGE` or `DELETE`, rewrites the `open` method literal to `PATCH`
  or `DELETE` and removes the header call. Anything else (a non-literal value, an
  unknown literal, the header set twice or in another function) stays manual review.
  Convert cannot see a header set in a function it does not follow, so the adapter
  also guards at run time: an `X-HTTP-Method` header set on a converted request
  throws, and nothing is sent. An update can therefore never go out as a create.
- **An update never creates.** A Web API `PATCH` on a key that does not exist creates
  the record, where a v2 `MERGE` failed. The adapter sends `If-Match: *` with every
  converted `PATCH`, so a missing record fails with 412 as v2 failed with 404. Rejected: holding the native `open` until
  `send` in the adapter, which would also buffer `setRequestHeader` and fake
  `readyState` 1, run-time behaviour that changes what the caller observes and that
  fixtures cannot test.
- **The adapter carries the full attribute table of every written entity.** The body
  is built at run time, so convert cannot see which attributes it sends. A body
  attribute missing from that table makes `send` throw an `Error` naming it, and
  nothing is sent. Rejected: refusing at convert time unless the body is a literal
  object, which would convert almost nothing, since bodies are built in code.
- **Values are mapped strictly.** Lookups `{Id, LogicalName}` become
  `<nav>@odata.bind`, `{Value}` is unwrapped for option sets and money, and
  `/Date(n)/` becomes ISO 8601. A date-only attribute (`DateTimeBehavior` DateOnly,
  which the Web API types `Edm.Date` and which rejects a time part) gets `YYYY-MM-DD`,
  the calendar date of the instant in the browser's time zone, since a 2011 script
  builds the value from a local `Date`. The extract records each date attribute's
  behaviour, so the adapter knows which attributes these are. A value whose shape its type does not allow (an option
  set as a bare number, a lookup without `Id` and `LogicalName`) throws before `send`.
  `null` passes as `null`; a null single-target lookup sends `<nav>@odata.bind: null`.
  A null customer or owner lookup throws, unless a live probe proves that one
  navigation property's null clears the lookup whatever entity it points at.
- **Navigation property names come from the target inventory.** The extract records
  each one-to-many relationship's `ReferencingEntityNavigationPropertyName`, and the
  adapter maps each lookup to a navigation property per target entity, so a customer
  or owner lookup picks its name from the body's `LogicalName`. A target inventory
  extracted before this field existed leaves the write as manual review that asks for
  a re-extract; the name is never guessed from the logical name.
- **A create still returns its record in v2 shape.** The extract records each
  attribute's schema name, the adapter sends `Prefer: return=representation`, and it
  maps the created record to the v2 spelling, so a caller reading `d.AccountId` (or
  any other field) keeps working. Rejected: returning only the primary id, which
  breaks a caller reading any other field.
- **Scope matches reads.** Native `XMLHttpRequest` calls, URLs followed through a
  variable as #1280 decides. jQuery writes, `$batch`, and `$links` associate and
  disassociate calls stay manual review.
- **Proof.** Node tests cover every mapping and every throw. Since a wrong write
  corrupts data and a fake request cannot show what the server accepts, the change
  also carries a live probe on a cloud and an on-premises v9 target: a create with
  every attribute type (a DateOnly date and a user-local date among them) read back
  and compared, an update, an update of a
  deleted record (it must fail, not create), a null lookup (single-target and
  customer), a delete, then cleanup. Convert stays a local verb
  for the e2e coverage gate, so this is a recorded probe, not an e2e test.
