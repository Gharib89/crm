---
id: trial-global-optionset
domain: metadata
# TRIAL-2 (SCN-003): create then evolve a global choice (option set). On-prem v9.1,
# crmworx publisher (prefix `cwx_`).
target: onprem
# `metadata list-optionsets` returns the global option-set definitions as a list;
# the predicate asserts the created set is present by its logical name. The final
# option order/labels the agent must achieve are metadata the list verb does not
# expand — existence proves the create path; #573's on-prem leg refines deeper
# checks. NOTE: a global option set is not a deletable record, so the record-delete
# cleanup model leaves it; teardown needs `metadata delete-optionset`.
end_state:
  query:
    - metadata
    - list-optionsets
  expect:
    row:
      Name: cwx_maintenancepriority
cleanup: []
---

On profile `agent-on-prem`, working in solution `agtrial2` (create it if it does
not already exist): create a global choice (option set) named for 'maintenance
priority' with options Low, Medium, High. Then evolve it: add a 'Critical' option
at the top, rename 'Medium' to 'Standard', and make the final order Critical, High,
Standard, Low. Prove the final state is correct.
