---
id: connectionrole-create
domain: connectionrole
target: cloud
end_state:
  query:
    - query
    - odata
    - connectionroles
    - --filter
    - "name eq 'EvalSet571 ConnRole'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 ConnRole
cleanup:
  - entity: connectionroles
    id_field: connectionroleid
    filter: "name eq 'EvalSet571 ConnRole'"
---

On profile `agent-cloud`, create a connection role named `EvalSet571 ConnRole`
that can be applied to both accounts and contacts, so that records of those two
types can be linked through it. Confirm the role exists and is enabled.
