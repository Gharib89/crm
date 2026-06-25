---
id: fieldsec-profile-create
domain: fieldsec
target: cloud
# Predicate asserts the profile exists by name; the column permission the agent
# adds hangs off it and is removed when the profile is deleted.
end_state:
  query:
    - query
    - odata
    - fieldsecurityprofiles
    - --filter
    - "name eq 'EvalSet571 FSP'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 FSP
cleanup:
  - entity: fieldsecurityprofiles
    id_field: fieldsecurityprofileid
    filter: "name eq 'EvalSet571 FSP'"
---

On profile `agent-cloud`, create a field security profile named `EvalSet571 FSP`.
Pick any one secured (field-level-securable) column on a system table and grant
this profile read access to it, then confirm the profile and its column permission
exist.
