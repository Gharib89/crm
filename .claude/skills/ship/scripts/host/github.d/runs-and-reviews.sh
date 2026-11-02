#!/usr/bin/env bash
# GitHub adapter: workflow runs, native activity, review requests, comments, merge and listings. Sourced by ../github.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

# The runs of one workflow file, created at or after <since>.
# A reviewer reached through a comment transport runs as a workflow, and an
# `issue_comment` run is attached to the DEFAULT BRANCH's SHA rather than to the
# PR head: `host_pr_checks` reads the head and cannot see it, which is why a
# window that closed before the round landed looked like `never-queued` (#203).
# `gh run list` is the one read that sees it.
#
# `issue_comment` is this host's word for the event a comment transport starts,
# and it is named here rather than passed in: the caller is a generic mechanic,
# and a host's own words stop at the adapter.
#
# `title` is the run's display title, which on this host is the title of the
# issue or PR the triggering comment sits on. The workflow fires on every
# comment in the repo, so that is what narrows the runs to the PR being polled,
# and it is the only link the host offers between a run and its PR. Two open PRs
# sharing a title therefore match each other's runs, which costs the poll time
# and no more, and a title rewritten between the request and the poll matches
# nothing, which reads as `never-queued`.
#
# The event and the window are filtered by `host_workflow_runs`' jq, not by the
# host: a list filtered by `--event` or `--created` can leave a live run out for
# minutes while the same workflow's unfiltered list carries it, and the missing
# run reads as `never-queued` for a reviewer still running (#496).
# `gh run list` rather than `api --paginate`, and one page of 100, because the
# cloud sandbox's proxy refuses the numeric-ID URL GitHub's `Link` header names
# for page two. The list is newest first, so a full page drops the oldest runs,
# and one page spanned five days of this repo's runs on 2026-10-07.
_gh_run_list() { # <workflow-file>
  gh run list --repo "$SHIP_REPO_SLUG" --workflow "$1" --limit 100 \
    --json status,conclusion,createdAt,url,displayTitle,event
}
# GitHub knows a workflow by its file name, and answers the repo-relative path
# the profile's `Workflow:` carries with a 404; every workflow sits flat in
# `.github/workflows/`, so the name alone is unambiguous.
host_workflow_runs() { # <workflow-file> <since-iso>
  local err out rc wf=${1##*/}
  err=$(mktemp) || return 2
  trap 'rm -f "$err"; trap - RETURN' RETURN
  out=$(_gh_run_list "$wf" 2>"$err"); rc=$?
  # The flat one retry `gql` keeps rather than `api`'s backoff: a read that
  # answers non-zero reports the reviewer unreachable, and this CLI family is the
  # one the header names as flaking 401 mid-session, so a bad second would
  # otherwise be read as evidence about a reviewer that is working.
  if [ "$rc" -ne 0 ]; then sleep 2; : > "$err"; out=$(_gh_run_list "$wf" 2>"$err"); rc=$?; fi
  # The stderr is tailed the way `_gh` tails its own, because a CLI diagnostic is
  # unbounded and the caller prints one JSON object.
  [ "$rc" -eq 0 ] || { ship_tail40 "$err"; return $rc; }
  # A full page whose oldest run is still inside the window may have cut off the
  # run the poll awaits, so the read fails rather than answer without it. An
  # empty list is still an answer (no run yet), and an empty or unparseable
  # output is not, which `-e` makes a failure.
  if jq -e --arg s "$2" 'length >= 100 and .[-1].createdAt >= $s' <<<"$out" >/dev/null; then
    echo "run list for $wf holds 100 runs inside the window since $2: the oldest may be cut off" >&2
    return 1
  fi
  jq -ce --arg s "$2" 'map(select(.event == "issue_comment" and .createdAt >= $s)
    | {status, conclusion, created_at: .createdAt, url, title: .displayTitle})' <<<"$out"
}

# The scaffolded Claude reviewer job raises one warning annotation titled
# `claude-review` when its round had refused tool calls, `N tool call(s)
# denied; ...`, and none when it had none (#283). A job is its own check run on
# this host, so the run's jobs are read first, then each job's annotations, and
# every such warning is summed, so a workflow of several jobs is not
# undercounted. Only
# the leading number is read, so the wording around it may change; a warning
# that leads with none is a count this read cannot give, and fails rather than
# answering 0.
host_run_denials() { # <run-url>
  local id=${1##*/runs/} jobs j a ann=""
  id=${id%%[/?#]*}
  case $id in ''|*[!0-9]*) return 1 ;; esac
  jobs=$(api "$R/actions/runs/$id/jobs" --paginate --jq '.jobs[].id') || return 1
  for j in $jobs; do
    a=$(api "$R/check-runs/$j/annotations" --paginate --jq '.[]') || return 1
    ann+="$a"$'\n'
  done
  jq -sc '[.[] | select(.annotation_level == "warning" and .title == "claude-review") | .message]
    | if all(test("^\\s*[0-9]")) then {denied: (map(capture("^\\s*(?<n>[0-9]+)").n | tonumber) | add // 0)}
      else error("a claude-review warning leads with no count") end' <<<"$ann"
}

# What a reviewer integration with no workflow run leaves on the PR, for
# poll-pr to read its round status from: the PR's comments and the reactions on
# the PR and on the request comment. A PR's conversation comments and reactions
# are its issue's on this host. The request comment is the latest one opening
# with the phrase at or after <since>, the comment `request-review` posted.
host_pr_native_activity() { # <pr> <since-iso> <phrase>
  local comments pr_r req_r='[]' id
  comments=$(api "$R/issues/$1/comments" --paginate \
    --jq '.[] | {id, login: .user.login, created_at, updated_at, body, url: .html_url}' | jq -s .) || return 1
  pr_r=$(api "$R/issues/$1/reactions" --paginate --jq '.[] | {content, login: .user.login, created_at}' | jq -s .) || return 1
  id=$(jq -r --arg s "$2" --arg p "$3" \
    '[.[] | select(.created_at >= $s and ((.body // "") | startswith($p)))] | last | .id // empty' <<<"$comments") || return 1
  if [ -n "$id" ]; then
    req_r=$(api "$R/issues/comments/$id/reactions" --paginate \
      --jq '.[] | {content, login: .user.login, created_at}' | jq -s .) || return 1
  fi
  jq -n --argjson c "$comments" --argjson p "$pr_r" --argjson r "$req_r" \
    '{comments: $c, pr_reactions: $p, request_reactions: $r}'
}

# The PR's review events, oldest first, as {event, login, created_at}: each
# review_requested and review_request_removed under the reviewer it names, and
# each review under its author.
_gh_review_events() { # <pr>
  api "$R/issues/$1/timeline" --paginate \
    --jq '.[] | select(.event == "review_requested" or .event == "review_request_removed" or .event == "reviewed")
      | {event, login: (if .event == "reviewed" then .user.login else .requested_reviewer.login end), created_at: (.created_at // .submitted_at)}
      | select(.login)' \
    | jq -s .
}

# The alias GitHub records <login> under, or nothing where it has none.
_gh_alias() { [ "$1" = "$(host_copilot_login)" ] && printf '%s' "$_gh_copilot_recorded"; return 0; }
# `recorded`: whether the name in hand is one GitHub records the reviewer under,
# given $l, its login, and $alias from `_gh_alias`: the login less a `[bot]`
# suffix (github-actions[bot] reads back as github-actions), or the alias
# (Copilot), in any case.
_gh_recorded_def="$SHIP_LOGIN_NORM"'
  def recorded: norm as $r | [$l, $alias | select(. != "") | norm] | index($r) != null;'

# Request, then read the request back off the host's own record: the login you
# request and the login you read back can differ (Copilot is requested as
# copilot-pull-request-reviewer[bot] and recorded as `Copilot`, the
# `_gh_copilot_recorded` alias above),
# and an empty requested_reviewers list proves nothing once the bot has posted.
host_pr_request_review() { # <pr> <login>
  local pr=$1 login=$2 ok=false before after pending readback now
  before=$(_gh_review_events "$pr") || before='[]'
  # Stamped before the POST, so a review submitted the instant the request lands
  # is still at-or-after the fallback requested_at.
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  api -X POST "$R/pulls/$pr/requested_reviewers" -f "reviewers[]=$login" >/dev/null && ok=true
  sleep 2
  after=$(_gh_review_events "$pr") || after='[]'
  pending=$(api "$R/pulls/$pr" --jq '.requested_reviewers[].login' | jq -R . | jq -s .)
  readback=$( { jq -r '.[]' <<<"$pending"; jq -r '.[] | select(.event == "review_requested") | .login' <<<"$after"; } | jq -R . | jq -s 'unique')
  # The request landed if the timeline gained a review_requested event for the
  # reviewer during this call, or if the reviewer is pending on
  # `requested_reviewers` now, under any name it is recorded as (`recorded`)
  # either way: another reviewer's request in the same window is not this one.
  # The timeline can lag the wait above, so the pending list alone must be
  # enough, or a landed request reads as never-queued. The match reads the
  # pending list and not the timeline's logins, which keep every earlier
  # request on the PR: matched against those, a second request that queued
  # nothing would read as landed. The pending list drops a reviewer once it
  # submits, so a reviewer still on it has a round queued and unposted, which
  # lands after this call's `requested_at` and is what the caller waits for:
  # already pending counts as landed. The timeline is chronological, so a new
  # event is the last one.
  # A round in flight counts as well, whatever the POST answered: before the
  # POST, the reviewer's last review event was a request, neither answered nor
  # withdrawn, and no withdrawal ends the call. GitHub drops a reviewer from the
  # pending list once it starts reviewing and a re-request writes no event, so a
  # Copilot ruleset's PR-open round reads that way while Copilot is mid-review,
  # and its request's time is the round's `requested_at` (#444). Read off the
  # timeline before the POST, so a review that lands during the call still
  # counts. An answered request is followed by its review, so an earlier round
  # never reads as this one.
  jq -n --argjson ok "$ok" --argjson b "$before" --argjson a "$after" --argjson p "$pending" --argjson rb "$readback" --arg l "$login" --arg alias "$(_gh_alias "$login")" --arg now "$now" \
    "$_gh_recorded_def"'
     def requests: map(select(.event == "review_requested" and (.login | recorded)));
     ($a | requests) as $ar | (($ar | length) > ($b | requests | length)) as $new
     | def last_of: [.[] | select(.login | recorded)] | last;
     ($b | last_of) as $lb
     | ($lb.event == "review_requested" and ($a | last_of).event != "review_request_removed") as $open
     | {requested: (($ok and ($new or any($p[]; recorded))) or $open),
        readback: $rb,
        requested_at: (if $new then ($ar[-1].created_at // $now) elif $open then $lb.created_at else $now end)}'
}

# The latest instant a round was asked of <login>, for a caller that holds no
# `requested_at` of its own: the last `review_requested` event on the timeline
# under any name the reviewer is recorded as, or, under a comment transport
# (<phrase>), the last PR comment opening with the phrase, by anyone, since the
# request reads the same whoever posted it. Nothing and non-zero where there is none.
host_pr_requested_at() { # <pr> <login> [<phrase>]
  local at raw
  if [ -n "${3:-}" ]; then
    raw=$(api "$R/issues/$1/comments?per_page=100" --paginate --jq '.[]') || return 1
    at=$(jq -rs --arg p "$3" '[.[] | select(.body | startswith($p))] | last | .created_at // empty' <<<"$raw") || return 1
  else
    raw=$(_gh_review_events "$1") || return 1
    at=$(jq -r --arg l "$2" --arg alias "$(_gh_alias "$2")" "$_gh_recorded_def"'
          [.[] | select(.event == "review_requested" and (.login | recorded))] | last | .created_at // empty' <<<"$raw") || return 1
  fi
  [ -n "$at" ] && printf '%s\n' "$at"
}

host_pr_comment() { # <pr> <body-file>
  local pr=$1 file=$2 me
  me=$(host_identity) || { [ -n "$me" ] && printf '%s\n' "$me"; return 1; }
  _comment_post() { jq -n --rawfile b "$file" '{body: $b}' | _gh_create -X POST "$R/issues/$pr/comments" --input - --jq '{id, url: .html_url, created_at}'; }
  _comment_find() {
    api "$R/issues/$pr/comments?per_page=100" --paginate --jq '.[]' \
      | jq -s --arg me "$me" --rawfile b "$file" '[.[] | select(.user.login == $me and .body == $b)] | last | select(. != null) | {id, url: .html_url, created_at}'
  }
  _gh_create_verify _comment_post _comment_find
}
# Nothing on success, {"status": <n|null>} on failure: the caller's `ship_fail`
# turns that into the verdict.
host_pr_set_body() { jq -n --rawfile b "$2" '{body: $b}' | _gh_write -X PATCH "$R/pulls/$1" --input -; }
host_pr_set_title() { jq -n --arg t "$2" '{title: $t}' | _gh_write -X PATCH "$R/pulls/$1" --input -; }

# REST whichever path the threads came from: a reply is
# `POST .../comments/{id}/replies` keyed by the thread's root comment, and that
# id is the thread id itself, so no GraphQL read stands between the caller and
# the post. The id is checked against the PR's review comments first, so an id
# no comment carries (a GraphQL node id from an older ship included) answers
# `no such thread` rather than a failed post. A reply's own id passes that
# check and the host answers the post, with its status: the check names unknown
# ids, it does not rank them.
#
# Create-then-verify like every other create here, with a find that returns
# non-zero when the comment read fails, so a read that failed is reported rather
# than turned into a duplicate disposition in the thread.
host_pr_reply_thread() { # <pr> <thread-id> <body-file>
  local pr=$1 cid=$2 file=$3 me ids
  case $cid in ''|*[!0-9]*) printf '{"replied": false, "url": null, "detail": "no such thread"}\n'; return 1 ;; esac
  ids=$(api "$R/pulls/$pr/comments?per_page=100" --paginate --jq '.[].id') \
    || { printf '{"replied": false, "url": null, "detail": "unavailable"}\n'; return 1; }
  grep -qx "$cid" <<<"$ids" || { printf '{"replied": false, "url": null, "detail": "no such thread"}\n'; return 1; }
  me=$(host_identity) || { [ -n "$me" ] && printf '%s\n' "$me"; return 1; }
  _reply_post() { jq -n --rawfile b "$file" '{body: $b}' \
    | _gh_create -X POST "$R/pulls/$pr/comments/$cid/replies" --input - --jq '{replied: true, url: .html_url}'; }
  _reply_find() {
    local raw
    raw=$(api "$R/pulls/$pr/comments?per_page=100" --paginate --jq '.[]') || return 1
    jq -s --argjson c "$cid" --arg me "$me" --rawfile b "$file" \
      '[.[] | select(.in_reply_to_id == $c and .user.login == $me and .body == $b)] | last | select(. != null) | {replied: true, url: .html_url}' <<<"$raw"
  }
  _gh_create_verify _reply_post _reply_find
}

# The thread id is a root comment id, so the GraphQL path finds the thread
# carrying it to get the node id resolveReviewThread takes: a walk of the PR's
# threads, one GraphQL page per hundred threads. Where GraphQL was refused, the
# proxy's own route takes the comment id directly, is idempotent, and answers
# 404 for an id no thread on the PR contains. Either way an unknown id prints
# {resolved: false, detail: "no such thread"} and returns 1.
host_pr_resolve_thread() { # <pr> <thread-id>
  local rows node rc nosuch='{"resolved": false, "detail": "no such thread"}'
  case $2 in ''|*[!0-9]*) printf '%s\n' "$nosuch"; return 1 ;; esac
  rows=$(_gh_threads_gql "$1"); rc=$?
  case $rc in
    0) node=$(jq -r --arg id "$2" 'first(.[] | select(.id == $id) | .node) // empty' <<<"$rows")
       [ -n "$node" ] || { printf '%s\n' "$nosuch"; return 1; }
       gql -f query='mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ isResolved } } }' \
         -F id="$node" --jq '{resolved: .data.resolveReviewThread.thread.isResolved}' ;;
    3) api -X POST "$R/pulls/$1/ccr/comments/$2/resolve" >/dev/null && { printf '{"resolved": true}\n'; return 0; }
       [ "${SHIP_HTTP_STATUS:-}" = 404 ] && { printf '%s\n' "$nosuch"; return 1; }
       return 1 ;;
    *) return 1 ;;
  esac
}

# Squash with the PR title as the subject; release tooling reads it. Verified by
# the caller re-reading host_pr_get rather than by this call's exit code.
host_pr_merge() { # <pr> <subject> <head>
  api -X PUT "$R/pulls/$1/merge" -f merge_method=squash -f commit_title="$2" -f sha="$3" >/dev/null
}

host_prs_open() {
  api "$R/pulls?state=open&per_page=100" --paginate \
    --jq '.[] | {number, title, head_ref: .head.ref, author: .user.login, url: .html_url, created_at}' | jq -s .
}
# Every open issue, for file-issue's candidate check. No label filter: an
# adjacent find may already sit under any label, or none. Paginated, because a
# candidate the check cannot see is the bug it exists to stop, and a repo's
# open issues are a bounded read.
host_issues_open() {
  api "$R/issues?state=open&sort=created&direction=desc&per_page=100" --paginate \
    --jq '.[] | select(.pull_request == null) | {number, title, url: .html_url}' | jq -s .
}

# Oldest first, unassigned, issues only (the issues endpoint also lists PRs).
host_issues_ready() { # <label>
  api "$R/issues?state=open&assignee=none&sort=created&direction=asc&per_page=100&labels=$(jq -rn --arg l "$1" '$l | @uri')" \
    --paginate --jq '.[] | select(.pull_request == null) | {number, title, created_at}' | jq -s .
}
