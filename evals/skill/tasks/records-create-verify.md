---
id: records-create-verify
domain: records
# Target gating: cloud | onprem | either. The tracer task is mutation-light and
# host-agnostic, but the harness only wires the cloud profile today (#570), so it
# is pinned to cloud; #573 broadens to the both-targets union.
target: cloud
# Deterministic end-state predicate, evaluated after the agent run. The query is
# the argv passed after `crm --json`; a list verb returns a bare array in `data`
# (see reference/records.md), so `count` is len(data) and `row` asserts one of
# those rows carries these exact field values (string compare).
end_state:
  query:
    - query
    - odata
    - contacts
    - --filter
    - "lastname eq 'EvalTracer570' and firstname eq 'Tracer'"
    - --select
    - firstname,lastname
  expect:
    count: 1
    row:
      firstname: Tracer
      lastname: EvalTracer570
# Cleanup runs unconditionally after scoring (pass or fail) so the live org is
# never polluted across runs. Each step queries `entity` for `id_field` matching
# `filter`, then deletes every matched row. Idempotent: no matches → no-op.
cleanup:
  - entity: contacts
    id_field: contactid
    filter: "lastname eq 'EvalTracer570'"
---

On profile `agent-cloud`, create a single contact record whose first name is
`Tracer` and whose last name is `EvalTracer570`. After creating it, confirm the
record exists in the org by reading it back.
