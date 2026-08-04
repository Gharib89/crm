---
id: feasibility-solutions-managed-component-delete
domain: solutions
tier: 3
source:
  type: forum
  url: https://community.dynamics.com/crm/f/microsoft-dynamics-crm-forum/210587/how-to-remove-components-from-managed-solution-deployed-on-production-system
# Solutions-ALM demand cluster (#897) — the managed-LOCK trap named in the issue. Harvest
# runner-up pool ("cannot delete component from managed solution", solutions/T3), sourced
# to a real recurring forum ask. This is a T3 trap because the naive answer is FALSE
# ("managed components are locked, you can't"): `remove-component` genuinely refuses managed
# targets AND a patch/clone-as-patch does NOT delete anything. cli_achievable is TRUE only
# via the non-obvious managed-UPGRADE path: drop the component from the SOURCE unmanaged
# solution (`remove-component`), re-export it as managed, then ship it as an upgrade
# (stage-and-upgrade → DeleteAndPromote removes components dropped from the new version).
# A bare agent that answers `false`, that runs `remove-component` against the MANAGED
# solution (which refuses), or that reaches for `clone-as-patch` (a patch never deletes),
# is the skill-lift discriminator. `remove-component` is deliberately NOT a needle (#948):
# the component can also be dropped from the source solution by other valid means (e.g. in
# the dev org), so export + stage-and-upgrade are the real discriminators of the upgrade path.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - solution export
    - solution stage-and-upgrade
evidence:
  - "reference/solutions.md (add/remove-component): both `add-component` and `remove-component` 'refuse managed targets' — you cannot edit an installed managed solution in place, so in-place deletion is genuinely blocked (the managed lock). `remove-component` DOES operate on the SOURCE unmanaged solution, which is the actual first step of the supported path."
  - "reference/solutions.md (Managed-solution upgrade lifecycle): the supported removal path is `crm solution remove-component --solution <source> ...` to drop the component from the SOURCE unmanaged solution, then re-export as managed (`crm solution export --managed`, `--managed` flag verified via `crm solution export --help`), then ship it as an upgrade — `crm solution stage-and-upgrade <zip> --promote --solution <name>` (or stage then `apply-upgrade`), which fires DeleteAndPromote and 'replaces the base solution + its patches', deleting components absent from the new version. Verified via `crm solution stage-and-upgrade --help`."
  - "A patch does NOT delete: reference/solutions.md `clone-as-patch` clones a hotfix from a parent and only adds/updates — so the component-removal goal is achievable via the upgrade path, not a patch. cli_achievable holds, but only through the upgrade lifecycle."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"We shipped a managed solution to our production org and it is
installed there. We now need to permanently remove one component from it (a custom column
that should never have shipped) while keeping the rest of the solution in place. Can we get
that component removed from production using the `crm` CLI?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command(s) the task needs — name the
  command path (e.g. `"solution stage-and-upgrade"`); a full example invocation is fine too.
- `rationale` (string): a brief justification — call out the trap (why the obvious
  in-place delete does not work) if there is one.
