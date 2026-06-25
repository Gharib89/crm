---
id: connectionrole-create
domain: connectionrole
target: cloud
# The predicate asserts the role is enabled (statecode 0 = Active), so the prompt's
# "confirm it exists and is enabled" maps to the scored end state, not just existence.
end_state:
  query:
    - query
    - odata
    - connectionroles
    - --filter
    - "name eq 'EvalSet571 ConnRole'"
    - --select
    - name,statecode
  expect:
    count: 1
    row:
      name: EvalSet571 ConnRole
      statecode: "0"
cleanup:
  - entity: connectionroles
    id_field: connectionroleid
    filter: "name eq 'EvalSet571 ConnRole'"
---

On profile `agent-cloud`, create a connection role named `EvalSet571 ConnRole`
that can be applied to both accounts and contacts, so that records of those two
types can be linked through it. Confirm the role exists and is enabled.
