---
id: trial-import-diagnosis
domain: solutions
# TRIAL-8 (SCN-053): diagnose a failed solution import — the skill-gap probe for
# `solution import-result` / `job-status`. On-prem v9.1. Precondition: the broken
# archive `/tmp/agtrial8-broken.zip` (a real <MissingDependency> failure) and the
# source solution `agtrial8` exist (constructed in the trial environment).
target: onprem
# This task is primarily DIAGNOSTIC: its real signal is the *quality* of the failure
# diagnosis, scored qualitatively by the optional Claude `--analyze` pass (#572). The
# programmatic predicate only guards the deterministic invariant — the failed import
# left the org's solution inventory intact (the broken zip did not land a new
# solution; `agtrial8` is untouched). No record cleanup (the local zip is removed
# out of band).
end_state:
  query:
    - query
    - odata
    - solutions
    - --filter
    - "uniquename eq 'agtrial8'"
    - --select
    - uniquename
  expect:
    count: 1
    row:
      uniquename: agtrial8
cleanup: []
---

On profile `agent-on-prem`: the file `/tmp/agtrial8-broken.zip` is a solution
archive that fails to import. Attempt the import, then produce a precise diagnosis of
why it failed — component, reason, and what a fix would be — using only the CLI.
