---
id: trial-import-diagnosis
domain: solutions
# TRIAL-8 (SCN-053): diagnose a failed solution import — the skill-gap probe for
# `solution import-result` / `job-status`. On-prem v9.1. Precondition: the broken
# archive `/tmp/agtrial8-broken.zip` (a real <MissingDependency> failure) and the
# source solution `agtrial8` exist (constructed in the trial environment).
target: onprem
# This task is purely DIAGNOSTIC: its only signal is the *quality* of the failure
# diagnosis, scored qualitatively by the optional Claude `--analyze` pass (#572).
# There is intentionally no `expect` predicate — the old one only asserted the source
# solution `agtrial8` is still present, which proves nothing about the diagnosis and
# false-failed on any org never seeded with the `agtrial8` fixtures (the broken zip
# and source solution are "constructed in the trial environment" — absent elsewhere).
# With no `expect`, `is_diagnostic` is True and the set runner SKIPs the task (scored
# by --analyze, not the set). `end_state.query` is kept so the analyzer still receives
# org state. No record cleanup (the local zip is removed out of band).
end_state:
  query:
    - query
    - odata
    - solutions
    - --filter
    - "uniquename eq 'agtrial8'"
    - --select
    - uniquename
cleanup: []
---

On profile `agent-on-prem`: the file `/tmp/agtrial8-broken.zip` is a solution
archive that fails to import. Attempt the import, then produce a precise diagnosis of
why it failed — component, reason, and what a fix would be — using only the CLI.
