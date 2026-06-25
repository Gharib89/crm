---
id: security-role-create
domain: security
target: cloud
# Predicate: the role exists by name. Deleting the role (cleanup) also drops the
# role assignment, so no separate assignment teardown is needed.
end_state:
  query:
    - query
    - odata
    - roles
    - --filter
    - "name eq 'EvalSet571 Role'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 Role
cleanup:
  - entity: roles
    id_field: roleid
    filter: "name eq 'EvalSet571 Role'"
---

On profile `agent-cloud`, create a security role named `EvalSet571 Role` that
grants organization-level read access to accounts. Then assign that role to the
current user (the one the profile authenticates as) and confirm the assignment
took effect.
