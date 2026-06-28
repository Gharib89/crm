# crm — CLI Contract

The vocabulary the `crm` CLI uses to talk to its callers — humans at a REPL and
coding agents in scripts. This context is about the *contract* (output shape,
exit codes), not D365 domain terms (which come from Microsoft: entity set,
FetchXML, solution, …).

## Language

### Output & failure

**Emit envelope**:
The single result a command produces via [`CLIContext.emit`](crm/cli.py) — either
a human rendering or, under `--json`, the `{ok, data?, error?, meta?}` object.
_Avoid_: response, output blob.

**Data payload**:
The `data` member of the emit envelope. A *curated, CLI-owned* shape
([ADR 0008](docs/adr/0008-cli-output-contract.md)) — not a
faithful passthrough of the raw D365 Web API response. The CLI normalizes it for
cross-command consistency: OData protocol keys (`@odata.context`, `@odata.etag`)
and paging links are stripped from `data` and, where useful, relocated to `meta`.
An agent learns one extraction rule, not one per command.
_Avoid_: response body, raw OData.

**Operational failure**:
A command that ran but did not achieve its effect — a D365 server error, a
client-side validation rejection, or a user-declined confirmation. Surfaces as
`emit(ok=false)` and exit code `1`.
_Avoid_: runtime error, command error, generic failure.

**Failure enrichment**:
Optional, additive detail a verb layers onto an operational failure — a fix-it
`hint` (appended to the error text; mirrored to `meta.hint` under `--json`)
and/or extra `meta` keys derived from the caught `D365Error`. The canonical
envelope is *reserved*: `meta`'s `{status, code, category, retryable}` and the
raw error text always come from the `D365Error` itself and can never be
overwritten — the [`d365_errors` seam](crm/commands/_helpers/errors.py) raises if
an enrichment names a reserved key. Enrichment that costs extra reads self-gates
on `--json` at the caller (the *when-to-pay* gate), so the human render skips it.
The alternate-key hint is one instance.
_Avoid_: error detail, error context (name the specific enrichment).

**Alternate-key hint**:
A best-effort enrichment attached to an operational failure when a write hits
the alternate-key uniqueness code `0x80060892`: the entity's alternate keys,
their attributes, and the colliding `payload_values`, plus a `primary_id_hint`
when the payload also carries the primary id (the server returns the same code
for a PK collision). Owned by [`crm/core/entity.py`](crm/core/entity.py) so every
write path can reach it — emitted in `meta` on `entity create` (`--json`) and
per-row on bulk `data import` failures. The human render skips it to avoid the
extra metadata reads (the *when-to-pay* gate stays at the caller, not in core).
Self-contained: it swallows its own errors and never masks the original failure.
_Avoid_: duplicate error, key error detail.

**Usage error**:
A caller mistake that exits `2`. Classically one Click rejects before the command
body runs — unknown flag, bad parameter value, missing required argument (Click's
default). Also a **validation predicate** rejection on the CLI path: `usage_guard`
([`errors.py`](crm/commands/_helpers/errors.py)) maps the predicate's status-less
`D365Error` to a `click.UsageError`, so the *same rule* that is an **operational
failure** (exit `1`) on the `apply` / direct-core path is a usage error (exit `2`)
here — one rule, two exit codes by caller.
_Avoid_: validation error (that term is reserved for the operational-failure kind
raised inside the command body).

**Validation predicate**:
A pure rule check in `crm/core/*` (e.g. `check_formula_compat`, `parse_default` in
[`metadata_attrs.py`](crm/core/metadata_attrs.py)) that raises a status-less
`D365Error` on bad input — the *single authority* for a rule shared by the CLI and
`apply`. The CLI pre-checks it inside `usage_guard` (→ **usage error**, exit `2`,
before the backend); `apply` / direct-core callers reach it through `d365_errors`
(→ **operational failure**, exit `1`). Pure coercion with no second caller
(string→typed parsing unique to the CLI, e.g. the `--reorder` `ParamType`) stays at
the command seam instead of moving to core.
_Avoid_: validator, guard (name the predicate); ParamType (that is the coercion
adapter, not the rule).

**Exit-code contract**:
The promise that `0` = success, `1` = operational failure, `2` = usage error —
the signal a coding agent loops on. Detail beyond the code lives in the emit
envelope. Formalized in [ADR 0001](docs/adr/0001-cli-exit-code-contract.md).

**List payload**:
The data payload of a list-returning verb: always a **bare array** of row
objects in `data` (`data[0]` is the first row), for every list verb. OData
paging is relocated to `meta` — `meta.next_link` (from `@odata.nextLink`) and
`meta.count` (from `@odata.count`); a change-tracking query (`--track-changes`/
`--delta-token`) likewise relocates `meta.delta_link` (from `@odata.deltaLink`)
and the bare `meta.delta_token` lifted out of it — and per-row protocol keys
(`@odata.etag`, `@odata.*`) are stripped. No command returns the raw OData
envelope in `data`.
_Avoid_: OData envelope, `data.value`, result wrapper.

**Normalized entity id**:
`_entity_id` (with companion `_entity_id_url`) — the CLI-synthesized, stable key
holding the affected record's GUID across the write verbs and single-record
reads, so chaining needs no per-entity primary-key knowledge. Leading underscore
marks it synthetic, distinct from the genuine PK attribute (`accountid`, …) that
still appears in a create/get's full record. Present on: create (alongside the
full record), update, delete (`{deleted: true, _entity_id, _entity_id_url}`), and
`entity get`. **Not** injected per-row in list payloads — each list row carries
its own PK attribute.
_Avoid_: `id`, `recordid`, primary key (the PK is the D365 attribute; this is the
normalized synthetic key).

**Record render modes**:
A single record renders differently per output mode, each mode with a default and
one opt-out knob. **JSON**: default = the full curated record (`@odata.*` stripped,
`_entity_id` injected); `--minimal` trims it. **Human**: default = *concise* —
null/empty fields hidden, `@odata.*` suppressed, `_entity_id` hoisted first (the
primary-name attribute hoisted too only when metadata is already cached, never via
an added round-trip); `--full` expands to every field including nulls. So JSON
defaults verbose-for-agents, human defaults concise-for-people.
_Avoid_: verbose dump, minimal mode (name the specific knob: `--minimal` / `--full`).

### Dry-run

**Dry-run preview**:
What a mutation returns under the global `--dry-run` flag instead of executing —
`data` carries `_dry_run: true` plus `would_*`/`_exists` keys describing the
skipped write (never the bare success key), and the envelope carries
`meta.dry_run: true`.
_Avoid_: request echo, stub response.

**Reads-execute rule**:
Under `--dry-run`, only mutations are previewed; reads (GET) always execute for
real. This is what lets a preview state live facts (`_exists`, `would_skip`)
instead of guesses. `--dry-run` means "no writes", not "no traffic".

**Multi-stage failure**:
An operational failure partway through a verb that writes in stages — the
envelope carries `meta.completed_steps` (what already happened, including any
ids minted) and `meta.failed_stage`. The error text states the recovery path;
re-running the whole verb is usually wrong once a stage has written
([ADR 0007](docs/adr/0007-record-clone-no-rollback-continue-and-report.md)).
_Avoid_: partial failure (ambiguous about whether anything was written).

### Cloning

**Schema clone**:
A copy of a *definition* — entity schema (`metadata clone-entity`), form
(`form clone`), workflow (`workflow clone`). Operates on customization
metadata; never touches record data.
_Avoid_: bare "clone" without a qualifier.

**Record clone**:
A copy of a single *data row* (`entity clone`) — new record with the source's
attribute values, minus the never-copy set. Never touches schema.
_Avoid_: bare "clone" without a qualifier, duplicate, copy record.

**Clone pre-flight**:
Everything a record clone resolves and validates *before its first write* —
lookup target resolution (including polymorphic targets), override/unset field
validation. All failures are batched into one operational failure naming every
offending field; `--dry-run` runs the same pre-flight, so the preview is the
complete fix list against an untouched org. Scoped to the parent record:
child rows (`--with-children`) are validated per-row at clone time, and a
child failure is a multi-stage failure, not a pre-flight failure.
_Avoid_: validation pass, pre-check.

**Never-copy set**:
Attributes a record clone never copies even when metadata says they are
writable: row identities (Uniqueidentifier-typed), state/status (server
default wins on create), provenance- or privilege-gated fields
(`overriddencreatedon`), and `ownerid` (a clone is owned by its creator, like
any created record). Anything dropped is re-addable explicitly via `--override`.

### Workflow records

**Workflow definition**:
The authored, editable workflow row (type=1) — the thing a maintainer creates,
edits, activates, and deletes. All [workflow verbs](crm/core/workflow.py) operate
on definitions.
_Avoid_: parent draft (a definition is not always a draft), workflow record.

**Activation record**:
The server-created internal copy of a definition (type=2) that exists while the
definition is activated. Read-only from the caller's perspective: it cannot be
deleted, deactivated, or edited directly — operations resolve to its definition
([ADR 0013](docs/adr/0013-workflow-step-editing-onprem-direct-patch.md)).
_Avoid_: activation copy, activation row.

**Provenance wall**:
The platform's refusal to create, update, or publish a classic process
(workflow/business-rule/action — categories 0/2/3) whose `xaml` it does not
recognise as Web-application–authored. Backed by the read-only `iscrmuiworkflow`
flag and surfaced as `0x80045040` ("created outside the Microsoft Dynamics 365
Web application"). It is **provenance-sensitive, not target-sensitive**: a clone
or solution-import of *unmodified* designer xaml carries provenance and passes;
*hand-edited* xaml is rejected — verified on both orgs, via direct PATCH and via
solution import alike. On-prem has a deployment-administrator privilege escape
(`0x80045041 NotEnoughPrivilegesForXamlWorkflows`) when the org is configured to
permit non-UI XAML; cloud (Dataverse) has none, so hand-edited step authoring is
on-prem-only.
_Avoid_: "cloud block" (it is not cloud-only), "create wall" (it also blocks
update and publish).

### Customization XML

**Targeted structural editor**:
A verb that edits one component type's customization XML through its own
element grammar — it knows what a tab, a ribbon button, or a sitemap area
*is*, mutates only the intended nodes, and refuses to touch protected ones
(`classid`, fixed platform references). Today: `form` field add/remove/move,
`ribbon add-button`/`remove`, `view create`, `app build-sitemap`, `chart
create`. The only sanctioned shape for editing customization XML.
_Avoid_: generic XML primitive (a path/XPath editor that mutates arbitrary
nodes with no grammar — the silent-corruption surface the out-of-scope family
rejects), "XML surgery" (the codebase already does targeted XML editing;
"surgery" means the *unsupported* internal-serialization kind below).

**Internal-serialization surgery**:
Hand-writing or rewriting an *undocumented* internal serialization format with
no supported Web API write path — BPF `clientdata`/process XAML authoring
(#37), solution clone via export→GUID-regen→reimport (#166). Out of scope:
no supported API, broad silent-corruption surface, live-org-only verification.
Distinct from a targeted structural editor, which operates on documented
customization XML through a known grammar.

## Relationships

- Every **operational failure** is an **emit envelope** with `ok=false` and exits `1`.
- A **failure enrichment** only *adds* to an **operational failure** — it never
  alters the reserved `{status, code, category, retryable}` or the raw error text.
- A **usage error** is rejected by Click and exits `2`. Without `--json` it prints
  raw Click text; under `--json` the root group renders it as an `{ok: false, error}`
  envelope on stdout (still exit `2`, never `1`) — it does not flow through `emit`.
- The **exit-code contract** is the union: `{0 success, 1 operational failure, 2 usage error}`.
- A **validation predicate** is one rule with two exit codes by caller: a **usage
  error** (exit `2`) when the CLI pre-checks it via `usage_guard`, an **operational
  failure** (exit `1`) when `apply` / direct-core reaches it via `d365_errors`.
- A **clone pre-flight** failure is an **operational failure** (exit `1`) that
  occurs before any write — the org is untouched.
- An **activation record** always belongs to exactly one **workflow definition**
  (its parent); deleting or deactivating the definition removes it.
- A **dry-run preview** is a successful **emit envelope** (`ok: true`, exit `0`)
  with `meta.dry_run: true` — a previewed write is not an **operational failure**.

## Example dialogue

> **Agent author:** "If `entity create` hits a 412 from the server, what does my script see?"
> **Maintainer:** "An **operational failure** — exit `1`, and the **emit envelope** carries `meta.status: 412`. If instead you passed a flag that doesn't exist, that's a **usage error**, exit `2`, before the command even runs."

## Flagged ambiguities

- "validation error" was used for both a bad `--flag` (Click, exit 2) and an
  in-command rejection like `--bind-set without --bind-id` (emit, exit 1).
  Resolved: the former is a **usage error**; the latter is an **operational
  failure**.
- "preview" meant both the request echo a read returned under `--dry-run` and
  the would-write description a mutation returned. Resolved by the
  **reads-execute rule**: reads are never previewed; **dry-run preview** refers
  only to mutations.
