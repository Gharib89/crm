---
id: trial-process-state
domain: automation
# TRIAL-5 (SCN-023): process inventory + reversible state control. On-prem v9.1; the
# trial revealed an on-prem quirk (business rules un-deactivatable via Web API), so
# this is pinned on-prem. Precondition: a business rule named 'Error Code Visibility'
# exists in the org (seeded in the trial environment).
target: onprem
# Predicate asserts the business rule ends in the Activated state (statecode 1) —
# i.e. the deactivate→activate round-trip restored it. No cleanup: the trial leaves
# org state as it found it.
end_state:
  query:
    - query
    - odata
    - workflows
    - --filter
    - "name eq 'Error Code Visibility'"
    - --select
    - name,statecode
  expect:
    count: 1
    row:
      name: Error Code Visibility
      statecode: "1"
cleanup: []
---

On profile `agent-on-prem`: produce an inventory of the org's real workflow/process
definitions — counts by category and state, definitions only (not activation
copies) — and check whether any duplicate definitions share a name. Then demonstrate
safe state control: take the business rule named 'Error Code Visibility' offline,
confirm it is off, bring it back online, and confirm it is active again.
