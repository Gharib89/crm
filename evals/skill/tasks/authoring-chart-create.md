---
id: authoring-chart-create
domain: authoring
target: cloud
# System charts are savedqueryvisualizations rows — deletable records, so cleanup
# uses the record-delete model.
end_state:
  query:
    - query
    - odata
    - savedqueryvisualizations
    - --filter
    - "name eq 'EvalSet571 Chart'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSet571 Chart
cleanup:
  - entity: savedqueryvisualizations
    id_field: savedqueryvisualizationid
    filter: "name eq 'EvalSet571 Chart'"
---

On profile `agent-cloud`, author a system chart named `EvalSet571 Chart` on the
account table — a simple bar chart counting accounts grouped by their owner — and
make it available to users (publish). Confirm the chart exists on the account
table.
