---
id: diagnostic-data-quality
domain: diagnostics
# Mutation-free read-only investigation; pinned cloud like the tracer task (#570),
# since the harness wires only the cloud profile today (#573 broadens this).
target: cloud
# A DIAGNOSTIC task (#572): it declares no `expect`, so there is no clean
# programmatic predicate — the agent is asked to investigate and summarize, and the
# quality of that read is judged by the `--analyze` pass, not by a record assertion.
# The `query` is still declared so the final org state flows to the analyzer
# alongside the transcript (here: a small sample of contacts to ground the read).
end_state:
  query:
    - query
    - odata
    - contacts
    - --top
    - "5"
    - --select
    - fullname,emailaddress1
# No `expect:` block — this is what makes the task diagnostic.
# Read-only task: nothing to clean up.
cleanup: []
---

On profile `agent-cloud`, investigate this org's contact data quality. Report how
many of the first few contacts you can read lack an email address, and summarize
any data-quality concerns you notice. Do not create, modify, or delete any records.
