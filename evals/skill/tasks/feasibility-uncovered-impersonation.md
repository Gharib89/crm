---
id: feasibility-uncovered-impersonation
domain: uncovered
tier: 2
source:
  type: so
  url: https://stackoverflow.com/questions/46229133
# Security demand cluster (#898), pilot harvest #885 row 14 — "how to impersonate a user via
# odata" (19 votes, 2.3k views). Tagged `uncovered`: a verified skill blind spot over an existing
# CLI capability, not a CLI gap. The crm CLI ships impersonation on its read/write verbs via
# `--as-user <systemuser-guid>` (MSCRMCallerID header) and `--as-user-object-id <entra-guid>`
# (CallerObjectId, cloud) — verified live in `crm entity create --help` and `crm security
# assign-role --help` — but NO file under crm/skills/reference/ mentions impersonation, --as-user,
# MSCRMCallerID, or CallerObjectId (grep returns nothing), so a skill-equipped agent has no
# in-skill pointer to it either. This probes whether the agent, sent to investigate the CLI
# surface (`crm --help` / `crm describe`), discovers the flag rather than concluding impersonation
# needs a dedicated service account or is UI/SDK-only. A do-task was ruled out: it is not
# deterministically verifiable. Self-impersonating the profile's own connected user leaves createdby
# equal to that user with OR without --as-user, so a createdby read-back cannot distinguish
# impersonation from a plain write (did-nothing passes); impersonating a DIFFERENT user needs a
# second provisioned (licensed) systemuser GUID the harness cannot self-seed and no static predicate
# can pin at authoring time (the same limitation row 12 / security-role-create hit for user
# assignment). The discriminating signal is therefore knowledge of the flag, graded as feasibility —
# same shape as feasibility-bulk-dedupe-merge, an uncovered blind-spot probe (domain: uncovered).
# Host-agnostic — --as-user works on both cloud and on-prem; --as-user-object-id is the cloud/Entra
# variant — -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - --as-user
evidence:
  - "`crm entity create --help` / `crm security assign-role --help`: `--as-user GUID` sends the MSCRMCallerID header to impersonate a systemuser by GUID; `--as-user-object-id GUID` sends CallerObjectId to impersonate by Entra ID object id (cloud). Both flags sit on the record and security write verbs, so any Web API call the CLI issues can run on behalf of another user. Verified live on agent-cloud."
  - "No file in crm/skills/reference/ mentions `--as-user`, impersonation, MSCRMCallerID, or CallerObjectId (grep of the shipped skill tree returns nothing) — a documentation blind spot over a shipped capability, which is why this is tagged `uncovered` rather than a covered `security` task. cli_achievable is true regardless: the capability exists and is scriptable today."
  - "Stack Overflow 46229133 (the sourced ask) and the Dataverse impersonation docs describe exactly this MSCRMCallerID / CallerObjectId mechanism; the CLI's --as-user / --as-user-object-id flags are the thin wrapper over it, so the community ask maps 1:1 onto an existing crm capability."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"Using the Dynamics/Dataverse Web API through a service, how can we run
calls on behalf of another user — impersonate a specific user — so that, for example, a record is
created as if that user made it rather than the service account? Can the `crm` CLI do this, and
how?"*

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
- `required_commands` (list of strings): the `crm` command(s) or flag(s) the task needs — name the
  command path or option (e.g. `"entity create --as-user"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
