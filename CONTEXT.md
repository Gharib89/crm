# crm — CLI Contract

The vocabulary the `crm` CLI uses to talk to its callers — humans at a REPL and
coding agents in scripts. This context is about the *contract* (output shape,
exit codes), not D365 domain terms (which come from Microsoft: entity set,
FetchXML, solution, …).

## Language

### Output & failure

**Emit envelope**:
The single result a command produces via `CLIContext.emit` — either a human
rendering or, under `--json`, the `{ok, data?, error?, meta?}` object.
_Avoid_: response, output blob.

**Operational failure**:
A command that ran but did not achieve its effect — a D365 server error, a
client-side validation rejection, or a user-declined confirmation. Surfaces as
`emit(ok=false)` and exit code `1`.
_Avoid_: runtime error, command error, generic failure.

**Usage error**:
A caller mistake Click rejects before the command body runs — unknown flag, bad
parameter value, missing required argument. Exit code `2` (Click's default).
_Avoid_: validation error (that term is reserved for the operational-failure kind
raised inside the command body).

**Exit-code contract**:
The promise that `0` = success, `1` = operational failure, `2` = usage error —
the signal a coding agent loops on. Detail beyond the code lives in the emit
envelope.

### Workflow records

**Workflow definition**:
The authored, editable workflow row (type=1) — the thing a maintainer creates,
edits, activates, and deletes. All workflow verbs operate on definitions.
_Avoid_: parent draft (a definition is not always a draft), workflow record.

**Activation record**:
The server-created internal copy of a definition (type=2) that exists while the
definition is activated. Read-only from the caller's perspective: it cannot be
deleted, deactivated, or edited directly — operations resolve to its definition.
_Avoid_: activation copy, activation row.

## Relationships

- Every **operational failure** is an **emit envelope** with `ok=false` and exits `1`.
- A **usage error** is rejected by Click and exits `2`. Without `--json` it prints
  raw Click text; under `--json` the root group renders it as an `{ok: false, error}`
  envelope on stdout (still exit `2`, never `1`) — it does not flow through `emit`.
- The **exit-code contract** is the union: `{0 success, 1 operational failure, 2 usage error}`.
- An **activation record** always belongs to exactly one **workflow definition**
  (its parent); deleting or deactivating the definition removes it.

## Example dialogue

> **Agent author:** "If `entity create` hits a 412 from the server, what does my script see?"
> **Maintainer:** "An **operational failure** — exit `1`, and the **emit envelope** carries `meta.status: 412`. If instead you passed a flag that doesn't exist, that's a **usage error**, exit `2`, before the command even runs."

## Flagged ambiguities

- "validation error" was used for both a bad `--flag` (Click, exit 2) and an
  in-command rejection like `--bind-set without --bind-id` (emit, exit 1).
  Resolved: the former is a **usage error**; the latter is an **operational
  failure**.
