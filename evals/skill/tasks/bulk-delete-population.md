---
id: bulk-delete-population
domain: bulk
tier: 3
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=9c1c2f90-efdf-4652-9f49-4993e0a5f2b9
# T3 trap: clearing a whole population "in one shot" tempts a per-row delete loop that
# times out; the platform path is a server-side BulkDelete job (`crm data delete`, which
# takes FetchXML) or batched `$batch` deletes. Host-agnostic — both work on cloud and
# on-prem — so `either`.
target: either
kind: do
# End state: only the Keeper survives among this task's two record names. count:1 plus the
# Keeper row rules out did-nothing (0 rows, no Keeper), create-without-delete (16 rows),
# and deleting-the-Keeper-too (0 rows). The Keeper carries a distinctive phone so a stray
# same-named record can't satisfy the row match by accident. The filter names the two
# records explicitly rather than `startswith(name,'EvalBulk895')` so a sibling bulk task's
# leftover (Delta/List Co) from a partially-failed cleanup can't leak into the count.
end_state:
  query:
    - query
    - odata
    - accounts
    - --filter
    - "name eq 'EvalBulk895 Purge Co' or name eq 'EvalBulk895 Keeper Co'"
    - --select
    - name,telephone1
  expect:
    count: 1
    row:
      name: EvalBulk895 Keeper Co
      telephone1: "0300000000"
cleanup:
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalBulk895 Purge Co'"
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalBulk895 Keeper Co'"
---

Working against the connected Dynamics 365 org, a throwaway test table has filled up with
records you need to clear out in one shot. First create 15 accounts named
`EvalBulk895 Purge Co`, and separately one account named `EvalBulk895 Keeper Co` with
`telephone1` set to `0300000000`. Then delete every `EvalBulk895 Purge Co` account
efficiently — do not leave a per-record deletion running one call at a time — while
leaving the Keeper account untouched. Verify that only the Keeper remains.
