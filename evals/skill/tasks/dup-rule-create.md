---
id: dup-rule-create
domain: dup
target: cloud
# duplicaterule rows are deletable records, so cleanup is the record-delete model.
end_state:
  query:
    - query
    - odata
    - duplicaterules
    - --filter
    - "name eq 'EvalSet571 Dup'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 Dup
cleanup:
  - entity: duplicaterules
    id_field: duplicateruleid
    filter: "name eq 'EvalSet571 Dup'"
---

On profile `agent-cloud`, create a duplicate-detection rule named `EvalSet571 Dup`
on the contact table that flags two contacts as duplicates when their email
addresses match exactly. Publish (activate) the rule so it would run, then confirm
it is in the active state.
