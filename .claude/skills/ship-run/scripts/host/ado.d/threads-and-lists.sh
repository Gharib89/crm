#!/usr/bin/env bash
# Azure DevOps adapter: thread replies, merge, listings and the unsupported stubs. Sourced by ../ado.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

# A child comment on the thread, status untouched: resolving stays
# host_pr_resolve_thread's job. parentCommentId is the thread's root comment,
# read back rather than assumed, because a reply parented to 0 opens a sibling
# comment instead of threading under the finding.
#
# The POST goes direct rather than through `invoke`, whose azx retry would post
# a second identical disposition when a slow success is read as a failure. Per
# this adapter's rule, a failed create re-reads the thread for the comment that
# success left, and only then posts again.
host_pr_reply_thread() { # <pr> <thread-id> <body-file>
  local pr=$1 thread=$2 file=$3 f root me
  me=$(host_identity) || return 1
  root=$(_thread_raw "$pr" "$thread" | jq -r '.comments[0].id') || return 1
  [ -n "$root" ] && [ "$root" != null ] \
    || { printf '{"replied": false, "url": null, "detail": "no such thread"}\n'; return 1; }
  f=$(mktemp); trap 'rm -f "$f"; trap - RETURN' RETURN
  jq -n --rawfile b "$file" --argjson p "$root" '{parentCommentId: $p, content: $b, commentType: 1}' > "$f"
  _post_reply() {
    az devops invoke "${ORG[@]}" --http-method POST --area git --resource pullRequestThreadComments \
      --api-version 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" \
      pullRequestId="$pr" threadId="$thread" --in-file "$f" -o json >/dev/null
  }
  # Exit 0 the reply is there, 1 the thread carries no such child, 2 the thread
  # could not be read. An unknown is not an absence, so only a successful read
  # licenses a second POST. The match is keyed to parentCommentId and to this
  # identity: a disposition quoting the finding verbatim would otherwise match
  # the root comment, and someone else's identical text would stand in for a
  # reply this run left unposted.
  _reply_landed() {
    local raw; raw=$(_thread_raw "$pr" "$thread") || return 2
    jq -e --argjson p "$root" --arg me "$me" --rawfile b "$file" \
      '[.comments[] | select(.parentCommentId == $p and .content == $b
                 and (if (.author.uniqueName // "") != "" then .author.uniqueName else .author.displayName end) == $me)]
       | length > 0' >/dev/null <<<"$raw"
  }
  if ! _post_reply; then
    sleep 2
    _reply_landed; local landed=$?
    case $landed in
      0) ;;                            # the reply is there: the lost response was a success
      1) _post_reply || return 1 ;;    # a read that found none: post again
      *) return 1 ;;                   # 2, or a jq error: unknown, so report no reply
    esac
  fi
  jq -n --arg u "$(_pr_url "$pr")" --arg t "$thread" '{replied: true, url: ($u + "?discussionId=" + $t)}'
}
host_pr_resolve_thread() { # <pr> <thread-id>
  local f out; f=$(mktemp); trap 'rm -f "$f"; trap - RETURN' RETURN; printf '{"status":"fixed"}' > "$f"
  out=$(invoke PATCH git pullRequestThreads 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1" threadId="$2" --in-file "$f")
  local rc=$?; rm -f "$f"; [ $rc -eq 0 ] || return 1
  jq '{resolved: (.status | IN("fixed","closed","wontFix","byDesign"))}' <<<"$out"
}
# A REST PATCH, not `az repos pr update --status completed`: az fills
# lastMergeSourceCommit from its own read at call time and takes no head, so a
# push after the merge gate would land. The host refuses a head that moved.
# `az rest`, not `invoke`: four git locations are named `pullRequests` and
# invoke takes the first, the project-level read route, which refuses PATCH. So
# the call carries the run's credential itself: the PAT as Basic auth where one
# is set, else an Azure DevOps token from `az login`. The headers ride in a
# file, read from the environment by jq, so the PAT is never on an argv `ps` shows.
host_pr_merge() { # <pr> <subject> <head>
  local f h auth=(--resource 499b84ac-1321-427f-aa17-267ca6975798)
  f=$(mktemp); h=$(mktemp); trap 'rm -f "$f" "$h"; trap - RETURN' RETURN
  jq -n --arg m "$2" --arg h "$3" '{status: "completed", lastMergeSourceCommit: {commitId: $h},
    completionOptions: {mergeStrategy: "squash", deleteSourceBranch: true, transitionWorkItems: true, mergeCommitMessage: $m}}' > "$f"
  jq -n '{"Content-Type": "application/json"} + (env.AZURE_DEVOPS_EXT_PAT // "" |
    if . == "" then {} else {Authorization: ("Basic " + (":" + . | @base64))} end)' > "$h"
  [ -z "${AZURE_DEVOPS_EXT_PAT:-}" ] || auth=(--skip-authorization-header)
  azx rest --method patch --body "@$f" --headers "@$h" "${auth[@]}" --url "$(jq -rn --arg o "$SHIP_ORG_URL" \
    --arg p "$SHIP_PROJECT" --arg r "$SHIP_REPO" --arg n "$1" \
    '"\($o)/\($p | @uri)/_apis/git/repositories/\($r | @uri)/pullRequests/\($n)?api-version=7.1"')" >/dev/null
}
host_prs_open() {
  azx repos pr list "${PRJ[@]}" --repository "$SHIP_REPO" --status active --top 100 \
    | jq --arg base "$SHIP_ORG_URL" --arg p "$(jq -rn --arg p "$SHIP_PROJECT" '$p | @uri')" --arg r "$SHIP_REPO" \
      '[.[] | {number: .pullRequestId, title, head_ref: (.sourceRefName | ltrimstr("refs/heads/")), author: .createdBy.uniqueName,
              url: ($base + "/" + $p + "/_git/" + $r + "/pullrequest/" + (.pullRequestId | tostring)), created_at: .creationDate}]'
}
# Every open work item, for file-issue's candidate check. No tag filter: an
# adjacent find may already sit under any tag, or none. PRs are not work items
# here, so nothing has to be excluded.
#
# Ask for all of them first, because a candidate the check cannot see is the
# bug it exists to stop. WIQL has no TOP and a project query past 20000 rows
# fails outright with VS402337, so a board that large gets the 180-day window
# instead and says so on stderr: a narrowed pool the caller is told about, not
# a silent one. A board that size is a whole ADO project, not a repo's tracker.
_wi_open_wiql() { # <extra predicate>
  printf "SELECT [System.Id], [System.Title] FROM WorkItems WHERE [System.TeamProject] = '%s' AND [System.State] <> '%s' AND [System.State] <> 'Removed'%s ORDER BY [System.CreatedDate] DESC" \
    "${SHIP_PROJECT//\'/\'\'}" "$ADO_CLOSED" "$1"
}
host_issues_open() {
  local out err rc=0
  err=$(mktemp); trap 'rm -f "$err"; trap - RETURN' RETURN
  out=$(azx boards query "${PRJ[@]}" --wiql "$(_wi_open_wiql "")" 2>"$err") || rc=$?
  if [ "$rc" -ne 0 ]; then
    # Only the row-limit answer earns the narrower pool. An auth or transient
    # failure that narrowed instead would hide an existing candidate and file a
    # second issue for a find already filed, which is the bug this check exists
    # to stop.
    if grep -q 'VS402337' "$err"; then
      echo "ADO: the project's open work items exceed WIQL's 20000-row limit; narrowing the candidate pool to the last 180 days" >&2
      out=$(azx boards query "${PRJ[@]}" --wiql "$(_wi_open_wiql " AND [System.CreatedDate] > @today - 180")") || return 1
    else
      ship_tail40 "$err"; return 1
    fi
  fi
  jq --arg org "$SHIP_ORG_URL" --arg project "$SHIP_PROJECT" \
      '[.[] | {number: .id, title: .fields["System.Title"],
               url: ($org + "/" + ($project | @uri) + "/_workitems/edit/" + (.id | tostring))}]' <<<"${out:-[]}"
}

host_issues_ready() { # <label>
  local out
  out=$(azx boards query "${PRJ[@]}" --wiql "SELECT [System.Id], [System.Title], [System.CreatedDate] FROM WorkItems WHERE [System.TeamProject] = '${SHIP_PROJECT//\'/\'\'}' AND [System.State] <> '$ADO_CLOSED' AND [System.State] <> 'Removed' AND [System.Tags] CONTAINS '${1//\'/\'\'}' AND [System.AssignedTo] = '' ORDER BY [System.CreatedDate] ASC") || return 1
  jq '[.[] | {number: .id, title: .fields["System.Title"], created_at: .fields["System.CreatedDate"]}]' <<<"${out:-[]}"
}

# Azure DevOps runs its pipelines against the PR itself, so there is no
# comment-started run sitting off the PR head to settle. Non-zero and silent is
# "this host has no such read", the answer every read the host lacks gives, and
# `poll-pr` then holds its window to the constant.
host_workflow_runs() { return 1; } # <workflow-file> <since-iso>
# With no awaited run there is no run whose denied calls to count.
host_run_denials() { return 1; } # <run-url>
# No reviewer here posts through a native integration, so there is no activity
# to read a round's status from.
host_pr_native_activity() { return 1; } # <pr> <since-iso> <phrase>
# A policy evaluation here is no job this adapter can re-run yet, so `ci-wait
# --rerun-failed` reports "unavailable" and the run reads the failure by hand.
host_check_job() { return 1; } # <pr> <head_sha> <name>
host_check_rerun() { return 1; } # <job_id>

# Azure DevOps has no Copilot-review ruleset, so there is nothing to contradict
# a profile with. Non-zero and silent is "not checked", the same answer the
# GitHub adapter gives for a ruleset it cannot read, which is what lets preflight
# hold one warn-and-continue path rather than branching on the host.
host_copilot_review_on_push() { return 1; }

# No Copilot reviewer on this host, so no login to match and nothing to check.
host_copilot_login() { :; }
