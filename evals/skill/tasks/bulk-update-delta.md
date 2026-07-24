---
id: bulk-update-delta
domain: bulk
tier: 2
source:
  type: so
  url: https://stackoverflow.com/questions/8531185
# Host-agnostic: the efficient bulk-write path is `crm data import` over `$batch`, which
# works on cloud and on-prem (CreateMultiple/UpsertMultiple are cloud-only, but the skill
# steers to `$batch`), so `either`.
target: either
kind: do
# The delta update landed on ALL 20 rows iff exactly 20 accounts carry BOTH the marker
# name and the new phone. A did-nothing agent leaves 0 matches (default telephone1 is
# null); an agent that creates but never updates also leaves 0 — so count:20 has no
# did-nothing false-pass: the new value is present only if the bulk update actually ran.
end_state:
  query:
    - query
    - odata
    - accounts
    - --filter
    - "name eq 'EvalBulk895 Delta Co' and telephone1 eq '0200000000'"
    - --select
    - name,telephone1
  expect:
    count: 20
cleanup:
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalBulk895 Delta Co'"
---

Working against the connected Dynamics 365 org, you maintain a set of accounts that all
share the name `EvalBulk895 Delta Co`. First create 20 such accounts. Then apply a delta
update that sets the `telephone1` field to `0200000000` on every account with that name —
using the most efficient mechanism the platform offers, not one request per record.
Verify the update landed on all of them.
