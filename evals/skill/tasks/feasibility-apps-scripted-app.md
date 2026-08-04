---
id: feasibility-apps-scripted-app
domain: apps-sitemap
tier: 3
source:
  type: firsthand
  url: https://github.com/Gharib89/crm/issues/809
# Firsthand feasibility batch (#900), from crm#809 / DISCOVERED_BUGS #5. The real firsthand ask:
# stand up a model-driven app by script (instead of clicking through the maker portal), give it a
# sitemap, and then read it back to verify it exists — the reconcile round-trip (#795/#796) that
# #809 blocked. Covered (reference/apps-sitemap.md). cli_achievable is TRUE: `app create` creates
# the appmodule and `app set-sitemap` attaches a SiteMapXml. T3 (trap), not T1/T2: the read-back
# is the gotcha. A Web-API-created appmodule is Unpublished, and BOTH the by-id `appmodules(<id>)`
# retrieve and the plain `appmodules` collection GET return PUBLISHED apps only — so a naive
# read-back spuriously reports "Does Not Exist" even for the app just created (DISCOVERED_BUGS #5,
# live-confirmed on-prem v9.1 + Dataverse online). The CLI reads through
# RetrieveUnpublishedMultiple + $filter and app-scoped publish (sitemap bound via AddAppComponents,
# component 62) makes the app GET-visible in the collection. The discriminator is knowing app
# creation IS scriptable AND that the sitemap/publish step is what makes it readable back — not
# concluding model-driven apps are a maker-portal-only artifact. Host-agnostic -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - app create
    # `sitemap` (not `set-sitemap`, #948): substring recall then accepts both complete paths —
    # `app set-sitemap` and `app build-sitemap --unique-name <app>`.
    - sitemap
    # The read-back the task asks for: there is no `app get`/`app list` verb, so the app is
    # verified back with `crm query odata appmodules` — required so an answer that creates but
    # never reads back does not get full command credit (the end-to-end ask, per the T3 trap).
    - query
evidence:
  - "`crm app create --name <n> --unique-name <prefix_x>` creates a model-driven app (appmodule) — verified live via `--help` ('Create a model-driven app'). So the app itself is scriptable, not maker-portal-only."
  - "`crm app set-sitemap <name> --xml-file <f> --unique-name <app>` attaches a SiteMapXml to the app (`app build-sitemap` is the companion that constructs the XML from areas/groups/subareas) — verified live via `--help` ('Create a sitemap from a SiteMapXml file'). A model-driven app needs a sitemap to be a usable, publishable app."
  - "Read-back: the `app` group has no `get`/`list` verb (verified via `crm app --help`: only add-components/build-sitemap/create/delete/remove-components/set-sitemap), so the app is verified back with `crm query odata appmodules --filter \"uniquename eq '<name>'\"`. crm's appmodule reads go through RetrieveUnpublishedMultiple + $filter, so this returns the app regardless of publish state (the DISCOVERED_BUGS #5 / crm#809 fix)."
  - "Read-back gotcha (why T3): a Web-API-created appmodule is Unpublished, and a RAW by-id retrieve / plain `appmodules` collection GET returns published apps only, so a naive verification read reports the just-created app as 'Does Not Exist' (DISCOVERED_BUGS #5 / crm#809, live-confirmed both targets; MS Learn 'Create, manage, and publish model-driven apps using code'). The fix binds the sitemap via AddAppComponents so an app-scoped publish makes it GET-visible in the published collection too — so the round-trip is achievable, but the read path and publish step are what make it work."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"Instead of clicking through the maker portal, we want to script the
creation of a model-driven app: create the app, give it a sitemap for navigation, and then read it
back to confirm it was created. Can the `crm` CLI do this end to end, and which commands does it
take?"*

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
  path (e.g. `"app create"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
