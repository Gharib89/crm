---
id: trial-global-optionset
domain: metadata
# TRIAL-2 (SCN-003): create then evolve a global choice (option set). On-prem v9.1;
# the logical name uses the stock default publisher prefix (`new_`); adjust if the
# target org's default publisher differs.
target: onprem
# `metadata list-optionsets` returns the global option-set definitions as a list;
# the predicate asserts the created set is present by its logical name (existence
# only). The final option order/labels the prompt asks the agent to "prove" are
# metadata the list verb does not expand, so that step is the agent's demonstrated
# work, not machine-scored (#572's analyze pass). NOTE: a global option set is not a
# deletable record, so the record-delete cleanup model leaves it; teardown needs
# `metadata delete-optionset` (see the "Known cleanup limitation" note in README.md).
end_state:
  query:
    - metadata
    - list-optionsets
  expect:
    row:
      Name: new_maintenancepriority
cleanup: []
---

On profile `agent-on-prem`, working in solution `agtrial2` (create it if it does
not already exist): create a global choice (option set) named for 'maintenance
priority' with options Low, Medium, High. Then evolve it: add a 'Critical' option
at the top, rename 'Medium' to 'Standard', and make the final order Critical, High,
Standard, Low. Prove the final state is correct.
