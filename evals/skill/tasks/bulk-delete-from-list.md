---
id: bulk-delete-from-list
domain: bulk
tier: 2
source:
  type: reddit
  url: https://www.reddit.com/r/Dynamics365/comments/1fno8uh/
# Delete an *enumerated* set — specific keys from a supplied list, not a query-match:
# resolve each key to its GUID, then batch the deletes. Host-agnostic (`$batch`), so `either`.
target: either
kind: do
# The five listed accounts (EVB-02/05/07/09/11) must be gone AND the seven others intact.
# One query asserts one shape, so we assert the *survivors*: count:7 plus a specific
# survivor row (EVB-12). This rules out did-nothing (12 rows), delete-all (0 rows, no
# EVB-12), and over/under-deletion. A "deleted the wrong five yet still 7 remain, EVB-12
# among them" case is not distinguished — an accepted narrow gap for a cooperative agent
# (the eval measures capability, not adversarial verifier-gaming).
end_state:
  query:
    - query
    - odata
    - accounts
    - --filter
    - "name eq 'EvalBulk895 List Co'"
    - --select
    - accountnumber
  expect:
    count: 7
    row:
      accountnumber: EVB-12
cleanup:
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalBulk895 List Co'"
---

Working against the connected Dynamics 365 org, first create 12 accounts named
`EvalBulk895 List Co`, setting `accountnumber` to `EVB-01`, `EVB-02`, … through `EVB-12`
(one value per account). You are then handed a list of five specific accounts to remove:
`EVB-02`, `EVB-05`, `EVB-07`, `EVB-09`, and `EVB-11`. Delete exactly those five accounts —
and no others — then verify the remaining seven are still intact.
