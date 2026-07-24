---
id: records-reassign-parent
domain: records
tier: 2
source:
  type: reddit
  url: https://www.reddit.com/r/Dynamics365/comments/10azr2a/
# Cross-entity reassignment (pilot harvest #885 row 3): the "mass change when a salesperson
# leaves" ask — reassign a departing party's whole book of related records onto a replacement.
# Modeled here as reassigning a set of contacts from an outgoing parent account to a
# replacement account through the customer lookup (parentcustomerid), rather than reassigning
# systemuser OWNERSHIP: the count/row predicate can only assert a lookup value nameable at
# authoring time, and the replacement owner would be a runtime-chosen systemuser GUID no static
# predicate can pin. Parent-account reassignment keeps the "mass-reassign every related record"
# shape while staying deterministic and needing no second provisioned user. Host-agnostic —
# the customer lookup and navigation-property filter work on cloud and on-prem v9.1 — so `either`.
target: either
kind: do
# End state: all five contacts now hang off the NEW parent. The filter counts contacts that
# carry BOTH the task marker (lastname) AND the new parent, reached through the
# `parentcustomerid_account` single-valued navigation property — the disambiguated nav prop
# for the polymorphic customer lookup (MS Learn: a customer lookup exposes one nav prop per
# target table). count:5 rules out did-nothing (0 under the new parent — they start under the
# old one) and a partial reassignment (<5). Reassigning is state-identical to creating five
# fresh contacts directly under the new parent, which no final-state check can distinguish
# (ADR 0028's L1-proves-state-not-path limitation); a cooperative agent following the prompt
# does the move, and whether it batched the reassignment efficiently is the advisory L2 judge's call.
end_state:
  query:
    - query
    - odata
    - contacts
    - --filter
    - "lastname eq 'EvalRec896Book' and parentcustomerid_account/name eq 'EvalRec896 New Owner Co'"
    - --select
    - lastname
  expect:
    count: 5
cleanup:
  - entity: contacts
    id_field: contactid
    filter: "lastname eq 'EvalRec896Book'"
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalRec896 Old Owner Co' or name eq 'EvalRec896 New Owner Co'"
---

Working against the connected Dynamics 365 org, first create two accounts named
`EvalRec896 Old Owner Co` and `EvalRec896 New Owner Co`. Then create five contacts, each
with the last name `EvalRec896Book`, and set the company name (parent account) of all five
to `EvalRec896 Old Owner Co`. A salesperson is leaving, so their whole book of business has
to move: reassign every one of those five contacts from `EvalRec896 Old Owner Co` to
`EvalRec896 New Owner Co`. Verify that all five now belong to the new account.
