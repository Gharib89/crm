#!/usr/bin/env bash
# Azure DevOps adapter: pull request create, reads and comment. Sourced by ../ado.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

host_pr_create() { # <head> <base> <title> <body-file> <issue>
  local out
  out=$(azx repos pr create "${PRJ[@]}" --repository "$SHIP_REPO" --source-branch "$1" --target-branch "$2" \
        --title "$3" --description "$(cat "$4")" --draft false ${5:+--work-items "$5"}) \
    || out=$(azx repos pr list "${PRJ[@]}" --repository "$SHIP_REPO" --source-branch "$1" --status active --top 1 | jq 'first | select(. != null)') \
    || return 1
  [ -n "$out" ] || return 1
  jq --arg u "$(_pr_url "$(jq -r .pullRequestId <<<"$out")")" \
    '{number: .pullRequestId, url: $u, created_at: (.creationDate | '"$_utc"')}' <<<"$out"
}
_pr_norm() {
  jq --arg u "$1" '{number: .pullRequestId, url: $u, title, body: (.description // ""),
    head_sha: .lastMergeSourceCommit.commitId, head_ref: (.sourceRefName | ltrimstr("refs/heads/")),
    base_ref: (.targetRefName | ltrimstr("refs/heads/")), draft: (.isDraft // false),
    state: (if .status == "completed" then "merged" elif .status == "active" then "open" else "closed" end),
    mergeable: (if .mergeStatus == "conflicts" then "conflict" elif .mergeStatus == "succeeded" then "clean" else "unknown" end)}'
}
host_pr_get() { azx repos pr show "${ORG[@]}" --id "$1" | _pr_norm "$(_pr_url "$1")"; }
host_pr_for_branch() {
  azx repos pr list "${PRJ[@]}" --repository "$SHIP_REPO" --source-branch "$1" --status all --top 1 \
    | jq 'first | if . == null then null else {number: .pullRequestId,
        state: (if .status == "completed" then "merged" elif .status == "active" then "open" else "closed" end),
        head_sha: .lastMergeSourceCommit.commitId} end'
}
# Policy evaluations (build validation, required reviewers) plus PR statuses.
host_pr_checks() { # <pr> <head_sha>
  local pol st
  pol=$(azx repos pr policy list "${ORG[@]}" --id "$1" | jq '[.[] | select(.configuration.isEnabled == true)
      | select(.status != "notApplicable")
      | {name: (.configuration.settings.displayName // .configuration.type.displayName),
         status: (if .status == "approved" then "success" elif (.status | IN("queued","running")) then "pending" else "failure" end)}]') || return 1
  # A status is re-posted per transition (queued → pending → final), so only the
  # newest row per context counts; notApplicable means "no check here", not red.
  st=$(invoke GET git pullRequestStatuses 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1" \
      | jq '[.value[] | {name: ((.context.genre // "status") + "/" + .context.name), created: .creationDate,
         status: (if .state == "succeeded" then "success" elif .state == "notApplicable" then "skip"
                  elif (.state == null) or (.state | IN("pending","notSet")) then "pending" else "failure" end)}]
         | group_by(.name) | map(sort_by(.created) | last | del(.created)) | map(select(.status != "skip"))') || st='[]'
  jq -n --argjson a "$pol" --argjson b "$st" '$a + $b | group_by(.name) | map(last)'
}
_threads_raw() { invoke GET git pullRequestThreads 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1"; }
# One thread, for the reply path: phase 7 replies per thread, so listing every
# thread on the PR once per reply is a walk this endpoint does not need.
_thread_raw() { invoke GET git pullRequestThreads 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1" threadId="$2"; }
# {id, created} of the newest iteration: a push makes one, so "on the current
# head" means iteration == this id, or (for a PR-level thread with no iteration
# context, how a build-service reviewer posts) published after it was created.
_latest_iteration() {
  invoke GET git pullRequestIterations 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1" \
    | jq '(.value | max_by(.id)) as $i | {id: ($i.id // 0), created: ($i.createdDate // "")}'
}
# A service identity (a build service) has an empty uniqueName; its displayName is the login then.
_comment_login='(.author | if (.uniqueName // "") != "" then .uniqueName else .displayName end)'
_author_login='(.comments[0] | '"$_comment_login"')'
# Votes are the review rows; a reviewer that only opened threads on the latest
# iteration counts as a comment review on the head. `on_head` is what poll-pr's
# default head rule reads; `all` carries every round across iterations, for its
# --since rule. Which rows are rounds is `SHIP_SUBSTANTIVE`'s grade, applied in
# poll-pr.
#
# A vote has no timestamp anywhere in the API, so its submitted_at is null: a
# vote is the reviewer's current state, not a timed event, and it cannot answer
# the question --since asks. It therefore appears in `all` and stays out of that
# rule's answer, which is why a reviewer who only votes lands under the head rule
# alone. `submitted_at` is emitted in one UTC spelling, because the API mixes
# two (publishedDate "...:28.343Z", creationDate "...:46.977591+00:00") and the
# adapter owes its caller one vocabulary.
_utc='(sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z"))'
# `body` is the thread's own text: an Azure DevOps round has no review body of
# its own, so the round a reviewer wrote is the comment it opened. A vote has
# no text at all, hence "".
# `id` is the thread's own id, which is also what `poll-pr --full` names to
# read this round whole; a vote has no thread, hence null there.
_review_row='(. as $r | {id: (.id | tostring), login: '"$_author_login"', state: "comment",
              submitted_at: (.publishedDate | '"$_utc"'),
              body: ((.comments[0].content // "") | clip($r.id))})'
host_pr_reviews() { # <pr> <head_sha> [<full-ids-json>]
  local votes it raw rows
  votes=$(azx repos pr reviewer list "${ORG[@]}" --id "$1" | jq '[.[] | select(.vote != 0)
      | {id: null, login: .uniqueName, state: (if .vote > 0 then "approved" else "changes" end), submitted_at: null, body: ""}]') || return 1
  it=$(_latest_iteration "$1") || it='{"id":0,"created":""}'
  raw=$(_threads_raw "$1") || raw='{"value":[]}'
  # `all` is not deduped by login: --since asks whether ANY round landed after a
  # time, so collapsing a reviewer's rounds could keep only the stale one.
  rows=$(jq --argjson it "$it" --argjson full "${3:-[]}" "$SHIP_REVIEW_CLIP"'[.value[] | select(.isDeleted != true)
      | select(.comments[0].commentType != "system")]
    | {on_head: [.[] | select((.pullRequestThreadContext.iterationContext.secondComparingIteration == $it.id)
                              or (.pullRequestThreadContext == null
                                  and (.publishedDate | '"$_utc"') >= ($it.created | '"$_utc"')))
                 | '"$_review_row"'] | unique_by(.login),
       all: [.[] | '"$_review_row"']}' <<<"$raw") || rows='{"on_head":[],"all":[]}'
  jq -n --argjson v "$votes" --argjson r "$rows" \
    '{on_head: ($v + $r.on_head), all: ($v + $r.all), total: ($v + $r.on_head | length)}'
}
host_pr_threads() {
  local me
  me=$(host_identity) || return 1
  _threads_raw "$1" | jq --arg me "$me" --arg m "$SHIP_COMMENT_MARKER" '[.value[] | select(.isDeleted != true) | select(.comments[0].commentType != "system")
    | {id: (.id | tostring), resolved: (.status | IN("fixed","closed","wontFix","byDesign")),
       replied: ([.comments[] | select('"$_comment_login"' == $me)] | length > 0),
       ship_last: ([.comments[] | select(.commentType != "system" and .isDeleted != true)] | last | .content // "" | contains($m)),
       author: '"$_author_login"', path: .threadContext.filePath, body: .comments[0].content}]'
}
# A PR comment is a PR-level thread here, one with no file context, so its
# first comment is the comment. `writer` is null for the reason host_issue_comments gives.
host_pr_comments() {
  _threads_raw "$1" | jq '[.value[] | select(.isDeleted != true) | select(.threadContext == null)
    | .comments[0] | select(.commentType != "system")
    | {author: '"$_comment_login"', body: (.content // ""), created_at: (.publishedDate | '"$_utc"'), writer: null}]'
}
# Each push makes an iteration carrying the pushed commit and its own time.
host_pr_pushed_at() { # <pr> <head_ref> <head_sha>
  local t
  t=$(invoke GET git pullRequestIterations 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1" \
    | jq -r --arg s "$3" '[.value[] | select(.sourceRefCommit.commitId == $s)] | max_by(.id) | .createdDate // empty | '"$_utc") || return 1
  [ -n "$t" ] && printf '%s\n' "$t"
}
host_pr_reviewer_blocked() { echo null; }
# Azure DevOps records no time for a reviewer-add, so requested_at is the wall
# clock in the adapter's one UTC spelling, stamped before the call so a review
# that lands immediately still counts.
host_pr_request_review() { # <pr> <login>
  local ok=false rb now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  azx repos pr reviewer add "${ORG[@]}" --id "$1" --reviewers "$2" >/dev/null && ok=true
  rb=$(azx repos pr reviewer list "${ORG[@]}" --id "$1" | jq '[.[] | .uniqueName, .displayName]') || rb='[]'
  jq -n --argjson ok "$ok" --argjson rb "$rb" --arg l "$2" --arg now "$now" \
    '{requested: ($ok and ([$rb[] | ascii_downcase] | index($l | ascii_downcase) != null)), readback: $rb, requested_at: $now}'
}
# Azure DevOps stamps request-review's `requested_at` off the wall clock and keeps
# no event to read it back from, so there is no instant to give.
host_pr_requested_at() { return 1; }
# A closed thread: visible, and a comment-resolution policy reads it as settled.
host_pr_comment() { # <pr> <body-file>
  local f out; f=$(mktemp); trap 'rm -f "$f"; trap - RETURN' RETURN
  jq -n --rawfile b "$2" '{comments: [{parentCommentId: 0, content: $b, commentType: 1}], status: "closed"}' > "$f"
  out=$(invoke POST git pullRequestThreads 7.1 --route-parameters project="$SHIP_PROJECT" repositoryId="$SHIP_REPO" pullRequestId="$1" --in-file "$f")
  local rc=$?; rm -f "$f"; [ $rc -eq 0 ] || return 1
  # The comment's time comes off the POST response, so the window `poll-pr
  # --since` opens is bounded by the host's clock: a local clock running ahead
  # would strand the round. The thread's own publishedDate is the fallback.
  jq --arg u "$(_pr_url "$1")" '{id: .id, url: ($u + "?discussionId=" + (.id | tostring)),
      created_at: ((.comments[0].publishedDate // .publishedDate) | if . then '"$_utc"' else null end)}' <<<"$out"
}
host_pr_set_body() { azx repos pr update "${ORG[@]}" --id "$1" --description "$(cat "$2")" >/dev/null; }
host_pr_set_title() { azx repos pr update "${ORG[@]}" --id "$1" --title "$2" >/dev/null; }
