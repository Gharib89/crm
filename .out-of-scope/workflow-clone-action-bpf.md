# Cloning an action or a business process flow onto another entity

`crm workflow clone` (and `metadata clone-entity --with-workflows`, which calls
it) clones classic workflows (category 0) and business rules (category 2) only.
It refuses actions (category 3) and business process flows (category 4) before
any write, and the refusal is a decision, not a gap waiting for a build.

## Why this is out of scope

Clone works by retargeting the `xaml` of a `workflow` row onto a new primary
entity and a new id. For categories 0 and 2 the `xaml` is the whole definition.
For the other two it is not:

- **Business process flow.** Cloning a BPF onto another entity authors a new
  BPF definition there: a `workflow` row with designer-generated `clientdata`,
  its `processstage` rows, and the one-way `IsBusinessProcessEnabled` flip on
  the target table. `.out-of-scope/bpf-definition-via-webapi.md` already
  rejects that authoring path (no supported Web API recipe, a direct POST
  answers `0x80040216`), and a clone does not avoid any of its blockers.
- **Action.** An action's definition includes its SDK message (`sdkmessage`
  and its request and response wiring) under the action's unique name, which is
  the public message callers invoke. A clone on another entity is a second,
  differently named message, which is re-authoring the action, not copying it.
  No user need for that has been reported, and entity clone records the
  skipped action under `skipped_workflows` with the working path.

## Supported alternative

`crm solution export` and `crm solution import` move the original process,
unchanged, for example into another org. Pointing an action or a BPF at a
different entity means authoring it there, in the process designer.

## Prior requests

- #1365: "Workflow clone refuses actions and business process flows" (the
  original clone plan deferred the decision to a live spike that never ran;
  the owner chose to make the refusal permanent)
