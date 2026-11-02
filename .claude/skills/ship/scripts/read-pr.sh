#!/usr/bin/env bash
# ship phases 6 and 7: the PR in one normalized payload, the read-back after a
# title or body write.
#
#   read-pr <pr>
#
# stdout: {number, url, title, body, head_sha, head_ref, base_ref, draft,
#          state, mergeable, headings[], missing[] | null,
#          outline_missing[] | null}
#   The adapter's PR object: the same fields on both hosts. Comments, review
#   threads and checks come from poll-pr. headings: the body's `## ` section
#   headings, in order (the rule `update-pr-body` reports with). missing: the
#   headings of the profile's `## PR` `Template:` file, read from the checkout
#   root, that the body lacks; [] where there is no profile or the Template: is
#   `None.`, null where the profile or its file cannot be read (null is no
#   answer, not a body that lacks nothing). outline_missing: the paths this
#   branch changes against the base (the three-dot diff to HEAD) that the
#   body's `## Change outline` section does not mention, [] when it mentions
#   each. A path is mentioned by its full path or its basename, which a derived
#   copy under `.claude/skills/` shares with its source; a vendored skill's
#   files (lock `source` not `.`) are also mentioned by their folder,
#   `.claude/skills/<name>/`. `skills-lock.json` and `CHANGELOG.md` are not
#   expected, and a body with no such section misses every path. null where the paths cannot be known: the
#   checkout is not on the PR's head branch, the base does not resolve, or a
#   folder mention meets a lock that cannot be read (no answer, not an outline
#   that misses nothing).
# exit: 0 · 2 the PR could not be read
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: read-pr <pr>'
ship_help "$usage" "$@"
ship_args "$usage" pr "$@"
pr=$1
[ $# -eq 1 ] || ship_tooling "unknown flag: $2"
ship_load_host

pull=$(host_pr_get "$pr") || ship_tooling "cannot read PR $pr"
headings=$(ship_body_headings "$(jq -r '.body // ""' <<<"$pull")" | jq -R . | jq -sc .)

missing=$(ship_template_missing "$(jq -r '.body // ""' <<<"$pull")") || missing=null

# The changed paths are known only from a checkout on the PR's head branch: the
# host adapters carry no file list, and a diff from any other branch is another
# change's.
outline=null
head_ref=$(jq -r '.head_ref // ""' <<<"$pull")
if [ -n "$head_ref" ] && [ "$(git symbolic-ref -q --short HEAD 2>/dev/null)" = "$head_ref" ] \
   && base=$(ship_base_ref --local) && paths=$(git -c core.quotepath=off diff --name-only "$base...HEAD" 2>/dev/null); then
  outline=$(ship_outline_missing "$(jq -r '.body // ""' <<<"$pull")" "$paths" | jq -R . | jq -sc .) || outline=null
fi
jq --argjson h "$headings" --argjson m "$missing" --argjson o "$outline" \
  '. + {headings: $h, missing: $m, outline_missing: $o}' <<<"$pull"
