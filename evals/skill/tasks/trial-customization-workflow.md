---
id: trial-customization-workflow
domain: customizations
# TRIAL-1 (SCN-005/002/032/029): full table→columns→view→solution→export workflow.
# On-prem v9.1: the trial ran on the crmworx org with publisher prefix `cwx_`.
target: onprem
# Predicate asserts the new view exists (a view requires the table + columns to
# exist first, so it proves the workflow reached the end). NOTE: the record-delete
# cleanup model cannot drop the custom *table definition* (`cwx_equipmentloan`) or
# the unmanaged solution's components — that needs `metadata delete-entity`, outside
# this model. The on-prem execution leg (#573) handles definition teardown.
end_state:
  query:
    - query
    - odata
    - savedqueries
    - --filter
    - "name eq 'EvalSet571 Equipment Loans'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 Equipment Loans
cleanup:
  - entity: savedqueries
    id_field: savedqueryid
    filter: "name eq 'EvalSet571 Equipment Loans'"
---

On profile `agent-on-prem`, in a new unmanaged solution `agtrial1` (create it if it
does not already exist), create a new custom table for tracking equipment loans: it
needs a name, a borrower (plain text is fine), a loan date, a return-due date, and a
status choice with options Out / Returned / Overdue. Add a view named
`EvalSet571 Equipment Loans` showing the loan name, borrower, and return-due date.
Then export solution `agtrial1` as an unmanaged zip to `/tmp/agtrial1.zip` and
confirm the new table actually made it into the zip.
