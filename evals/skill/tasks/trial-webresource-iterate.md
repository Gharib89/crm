---
id: trial-webresource-iterate
domain: customizations
# TRIAL-4 (SCN-013): JavaScript web-resource publish + version iterate. On-prem v9.1,
# crmworx publisher (prefix `cwx_`).
target: onprem
# webresourceset rows are deletable records, so cleanup is the record-delete model.
# Predicate asserts the resource exists by name; the v1→v2 content swap the agent
# must prove is byte content the list query does not expand.
end_state:
  query:
    - query
    - odata
    - webresourceset
    - --filter
    - "name eq 'cwx_/agtrial4/hello.js'"
    - --select
    - name
  expect:
    count: 1
    row:
      name: cwx_/agtrial4/hello.js
cleanup:
  - entity: webresourceset
    id_field: webresourceid
    filter: "name eq 'cwx_/agtrial4/hello.js'"
---

On profile `agent-on-prem`, in solution `agtrial4` (create it if it does not already
exist): create a JavaScript web resource named `cwx_/agtrial4/hello.js` whose
content logs 'hello v1' to the console, make it live, then ship a second version
that logs 'hello v2' and prove the server now serves the v2 content.
