---
id: trial-bulk-load
domain: records
# TRIAL-6 (SCN-041): bulk-load records. Pinned on-prem because the probe is the v9.1
# quirk — no `CreateMultiple`; the skill must steer the agent to `data import`/batch
# rather than a cloud bulk API.
target: onprem
# Exact count is the point ("verify the exact number that landed"). Contacts are
# deletable records, so cleanup removes all 50.
end_state:
  query:
    - query
    - odata
    - contacts
    - --filter
    - "lastname eq 'EvalSet571Bulk'"
    - --select
    - contactid
  expect:
    count: 50
cleanup:
  - entity: contacts
    id_field: contactid
    filter: "lastname eq 'EvalSet571Bulk'"
---

On profile `agent-on-prem`: load 50 throwaway contact records — all with last name
'EvalSet571Bulk', varied first names — as efficiently as the platform supports, then
verify the exact number that landed.
