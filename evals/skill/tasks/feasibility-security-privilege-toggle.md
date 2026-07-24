---
id: feasibility-security-privilege-toggle
domain: security
tier: 3
source:
  type: reddit
  url: https://www.reddit.com/r/Dynamics365/comments/1g3bxnz/
# Security demand cluster (#898), pilot harvest #885 row 13 — "Disable 'Export to Excel' in a
# Security Role" (toggle prvExportToExcel on an existing role). Harvested as a do/T1, but reframed
# to feasibility on hard evidence: the role<->privilege intersect is not an exposed OData entity
# set (`query odata roleprivileges` 404s, verified live on agent-cloud), so no static end-state
# predicate can read back whether a named privilege is present on a role — a do grader has nothing
# deterministic to assert. The real skill-lift discriminator here is knowledge, and it is a T3
# trap: (1) prvExportToExcel is a non-entity privilege reachable only through the `--privilege
# <name>` escape hatch of set-role-privileges, not the --access/--entities resolver a bare agent
# reaches for; (2) there is no --remove verb — "disabling" a privilege means re-running --replace
# with the full desired set minus that privilege, and --replace silently keeps an immovable
# SharePoint baseline, so exact set-equality never converges. An agent that answers "not possible
# via the CLI" or reaches only for --access/--entities is the miss. Host-agnostic -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - security list-roles
    - security set-role-privileges
evidence:
  - "reference/security.md (Customization privileges escape hatch): `crm security set-role-privileges <role> --privilege prvExportToExcel --depth global --add --yes` grants named non-entity privileges directly by privilege name — the only path to a privilege like prvExportToExcel, which the --access/--entities resolver does not cover. Verified via `crm security set-role-privileges --help` (--privilege option present)."
  - "reference/security.md (list-roles): `crm security list-roles --name-contains <text>` finds the target role by name server-side to obtain its roleid before editing its privileges. Verified via `crm security list-roles --help` (--name-contains present)."
  - "reference/security.md (`--replace` is destructive): set-role-privileges has no per-privilege remove verb (only --add and --replace, verified via `--help`); removing/disabling a privilege means re-running --replace with the complete desired set minus that privilege. --replace also silently retains the immovable SharePoint document-management baseline, so a strict exact-set reconcile never converges — check for subset satisfaction instead. cli_achievable holds (the toggle is scriptable), with this trap as the caveat."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"For governance we need to turn off the 'Export to Excel' privilege
(`prvExportToExcel`) on an existing security role, instead of clicking through the role editor in
the maker portal. Can this be scripted end-to-end with the CLI — finding the role and changing
that one privilege — and if so, which commands?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your current
working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command(s) the task needs — name the command
  path (e.g. `"security set-role-privileges"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
