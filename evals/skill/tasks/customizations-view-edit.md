---
id: customizations-view-edit
domain: customizations
# View create-then-edit (TRIAL-3, SCN-011) — the skill teaches view *create*; the
# edit (savedquery PATCH of fetchxml/layoutxml + publish) is the untaught path.
# Host-agnostic (account is a system table; the view name is user-chosen), so it
# runs on cloud; #573 adds the on-prem leg.
target: cloud
# Predicate asserts the view exists by its (user-chosen) name. The column/sort edit
# the agent must perform is layout state the list query can't see; existence proves
# the create+publish path, and a competent edit keeps the same name.
end_state:
  query:
    - query
    - odata
    - savedqueries
    - --filter
    - "name eq 'EvalSet571 View'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 View
cleanup:
  - entity: savedqueries
    id_field: savedqueryid
    filter: "name eq 'EvalSet571 View'"
---

On profile `agent-cloud`, create a system view on the account table named
`EvalSet571 View` that shows the account name and main phone, newest accounts
first. After it exists, change that same view: add the city column and flip the
sort to account name descending — and make sure users would actually see the
updated definition (publish it).
