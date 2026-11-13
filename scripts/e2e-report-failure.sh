#!/usr/bin/env bash
# Report a failed scheduled live e2e run (called by .github/workflows/e2e.yml).
# Opens one needs-triage issue, or comments on the open one an earlier failure
# left, found by its fixed title, so repeated failures never open a second.
# Env: GH_TOKEN, RUN_URL, FAILED_JOB; optional GH_REPO (gh reads it, default
# the checkout's repo). The text carries no secret and no org URL: the repo is
# public.
set -euo pipefail

TITLE="e2e-live: scheduled live e2e run failed"
: "${RUN_URL:?}" "${FAILED_JOB:?}"

body="The scheduled live e2e run failed.

- Run: ${RUN_URL}
- Failed job: \`${FAILED_JOB}\`"

open=$(gh issue list --state open --label needs-triage --search "\"${TITLE}\" in:title" \
  --json number,title --jq "[.[] | select(.title == \"${TITLE}\")] | sort_by(.number) | .[0].number // empty")

if [ -n "$open" ]; then
  gh issue comment "$open" --body "$body"
else
  gh issue create --title "$TITLE" --label needs-triage \
    --body "$body

Read the run's log, then triage. A later scheduled failure comments here while this issue is open."
fi
