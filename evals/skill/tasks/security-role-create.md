---
id: security-role-create
domain: security
target: cloud
# The predicate scores role *existence* by name — the deterministic floor. The
# prompt's assign-to-user step is the agent's to demonstrate but is not separately
# scored: the assignment is an M:N keyed by the run-time user id, which a static
# predicate query can't express. (Deleting the role in cleanup cascades any
# assignment, so teardown needs no separate step.)
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
