---
id: feasibility-records-paging-count
domain: records
tier: 3
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=e8a7e37c-bd3c-4463-863e-092d1e9f4dc4
# Paging / count-limit trap (pilot harvest #885 row 2): "how to get more than 5,000 records"
# — the single most recurring records ask, with the count-cap and aggregate-limit siblings
# folded in. Authored as a FEASIBILITY task rather than a do-task on purpose: the trap only
# bites past the platform's 5,000-row page ceiling, which cannot be honestly seeded at eval
# scale — a do-task where the agent creates the very rows it then counts leaks the answer
# (the harness seeds creds, not org data; the agent self-seeds in-prompt). Framing it as a
# capability question probes the exact skill knowledge without needing >5,000 live rows: does
# the agent know a single default page is not the whole set (follow it with `query odata
# --all`), and that `query count` returns a cached WHOLE-TABLE figure that ignores `--filter`?
# Host-agnostic, so `either`.
target: either
kind: feasibility
# cli_achievable is TRUE — retrieval past the cap and an exact filtered count are both
# reachable. The recall list pins the two non-negotiable mechanism tokens: `query odata` (the
# retrieval verb) and `--all` (the page-follow flag that walks past the 5,000-row ceiling). An
# answer that reaches only for `query count` — which ignores the filter and clamps at the
# cached total — is missing both tokens and fails, which is the discrimination the trap is
# built on: knowing the paging flag beats knowing a table-count shortcut.
answer_key:
  cli_achievable: true
  required_commands:
    - query odata
    - --all
evidence:
  - "reference/records.md: `query odata --all` follows the `@odata.nextLink` cursor past the default single page; the docs warn that a default single page sets `meta.has_more` and a `meta.warnings` entry when the org has more rows, so one page is not the whole result set."
  - "reference/records.md: `query count` 'gives a whole-table cached figure only, ignoring --filter', and a `--count` request whose `meta.count` lands on the server's 5000-row ceiling is flagged a clamped lower bound — 're-run with --all for an exact count of this query'. So an exact filtered count needs `query odata ... --all`, not `query count`."
  - "MS Learn (Dataverse Web API, Query data > Page results): large result sets are paged via the `Prefer: odata.maxpagesize` header and the `@odata.nextLink` annotation, confirming that retrieving past the page limit is an API-supported cursor-follow — exactly what `--all` automates."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"Our Accounts table holds well over 5,000 rows. Using the `crm`
CLI, can we (a) retrieve every matching record past the platform's 5,000-row page limit,
and (b) get the exact total count of a filtered subset — and if so, which command and flag
do we use?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm flag>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command path(s) and flag(s) the task
  needs — e.g. name the retrieval verb and the flag that walks past the page limit; a full
  example invocation is fine too.
- `rationale` (string): a brief justification.
