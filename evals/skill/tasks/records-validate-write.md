---
id: records-validate-write
domain: records
# Host-agnostic record automation (TRIAL-7, SCN-044/045). Pinned cloud so it runs
# on the cloud-ship routine's agent-cloud target; #573 broadens to the union.
target: cloud
# The trial ended by deleting the record; here the workflow ends after the
# verified write so the end state carries a durable artifact (a delete-ending
# workflow would score count:0 — indistinguishable from an agent that did nothing).
# Harness cleanup removes the record after scoring.
end_state:
  query:
    - query
    - odata
    - accounts
    - --filter
    - "name eq 'EvalSet571 Validate Co'"
    - --select
    - name,telephone1
  expect:
    count: 1
    row:
      name: EvalSet571 Validate Co
      telephone1: "0100000000"
cleanup:
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalSet571 Validate Co'"
---

On profile `agent-cloud`, you are automating account creation. First, try
creating an account named `EvalSet571 Validate Co` using a field name you suspect
is wrong — use `telephoneone` for the phone — and report exactly what the tooling
tells you about the bad field. Then create it correctly with phone `0100000000`,
read the record back to confirm the phone value landed exactly as sent, and
finally re-run the same create in a way that cannot produce a duplicate if it were
executed twice. Leave the single correct record in place.
