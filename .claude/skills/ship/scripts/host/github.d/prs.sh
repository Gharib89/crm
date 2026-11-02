#!/usr/bin/env bash
# GitHub adapter: pull request create, read, checks, reviews and threads. Sourced by ../github.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

# Put `Closes #<issue>` above the first `## ` heading, where no section rewrite
# reaches it: `update-pr-body` replaces a section wholesale, so a closing line
# appended to the end of the body sits inside the last section and the next
# rewrite of that section drops it. A body with no heading has no section to
# fall inside, so it keeps the append.
#
# Fenced blocks and `<details>` records are skipped by `ship_inert`, matching
# the fence model `ship_body_closes` uses: a `## ` inside a fence is example
# text, and a closing line printed into a fence renders as code, so the host
# registers no link and the keyword test that gates a re-run reads false.
# The heading match stays anchored at column 0 on purpose: it has to agree
# with `update-pr-body`, whose `^## ` is what decides a section boundary.
_gh_add_closes() { # <body> <issue>
  awk -v n="$2" "$SHIP_AWK_FENCE"'
    { inert = ship_inert($0) }
    !placed && !inert && /^## / { print "Closes #" n; print ""; placed = 1 }
    { print }
    END { if (!placed) printf "\nCloses #%s\n", n }' <<<"$1"
}

host_pr_create() { # <head> <base> <title> <body-file> <issue>
  local head=$1 base=$2 title=$3 file=$4 issue=$5 body
  body=$(cat "$file")
  # The mechanic translates the closing link for the host: add the keyword when
  # the body does not already carry one aimed at this issue.
  if [ -n "$issue" ] && ! ship_body_closes "$body" "$issue"; then
    body=$(_gh_add_closes "$body" "$issue")
  fi
  _pr_post() {
    jq -n --arg h "$head" --arg b "$base" --arg t "$title" --arg body "$body" \
      '{head: $h, base: $b, title: $t, body: $body, draft: false}' \
      | _gh_create -X POST "$R/pulls" --input - --jq '{number, url: .html_url, created_at}'
  }
  _pr_find() {
    api "$R/pulls" -X GET -f state=open -f "head=$SHIP_OWNER:$head" --jq 'first | select(. != null) | {number, url: .html_url, created_at}'
  }
  _gh_create_verify _pr_post _pr_find
}

host_pr_get() {
  api "$R/pulls/$1" --jq '{number, url: .html_url, title, body: (.body // ""),
    head_sha: .head.sha, head_ref: .head.ref, base_ref: .base.ref, draft,
    state: (if .merged then "merged" elif .state == "open" then "open" else "closed" end),
    mergeable: (if .mergeable_state == "dirty" then "conflict"
                elif .mergeable == true then "clean" else "unknown" end)}'
}

host_pr_for_branch() { # <branch>
  api "$R/pulls" -X GET -f state=all -f "head=$SHIP_OWNER:$1" -f sort=created -f direction=desc -f per_page=1 \
    --jq 'first | if . == null then null else {number, state: (if .merged_at != null then "merged" elif .state == "open" then "open" else "closed" end),
      head_sha: .head.sha} end'
}

# Check runs plus classic commit statuses, one row per name, latest wins. Within
# a name, the latest check run is the latest created, by id: a cancellation can
# complete after its successor started, so a time rule prefers the cancelled row.
# A `cancelled` check run whose workflow has a newer workflow run on this head was
# superseded by its concurrency group, and its successor can have no check run
# yet (#394): it reads `pending` until the successor's own row outranks it by id.
# A successor is a newer run still going, or one that wrote a row of this name.
# One cancelled with no successor, or no workflow behind it, is a failure.
host_pr_checks() { # <pr> <head_sha>
  local sha=$2 runs statuses wruns='[]'
  runs=$(api "$R/commits/$sha/check-runs" --paginate --jq '.check_runs[] | {id, name,
      suite: .check_suite.id, cancelled: (.conclusion == "cancelled"),
      status: (if .status != "completed" then "pending"
               elif (.conclusion | IN("success","neutral","skipped")) then "success"
               else "failure" end), at: (.completed_at // .started_at // "")}' | jq -s .) || return 1
  if jq -e 'any(.[]; .cancelled)' <<<"$runs" >/dev/null; then
    wruns=$(api "$R/actions/runs?head_sha=$sha&per_page=100" --paginate \
      --jq '.workflow_runs[] | {id, workflow_id, suite: .check_suite_id, done: (.status == "completed")}' | jq -s .) || return 1
  fi
  statuses=$(api "$R/commits/$sha/status" --jq '.statuses[] | {name: .context,
      status: (if .state == "success" then "success" elif .state == "pending" then "pending" else "failure" end),
      at: .updated_at}' | jq -s .) || return 1
  jq -n --argjson a "$runs" --argjson b "$statuses" --argjson wruns "$wruns" '
    def superseded: . as $r | ($wruns | map(select(.suite == $r.suite)) | first) as $w
      | $w != null and any($wruns[]; .workflow_id == $w.workflow_id and .id > $w.id
          and ((.done | not) or (.suite as $s | any($a[]; .suite == $s and .name == $r.name))));
    ($a | map(if .cancelled and superseded then .status = "pending" else . end)
        | group_by(.name) | map(max_by(.id))) + $b
    | group_by(.name) | map(max_by(.at) | {name, status})'
}

# The re-runnable job behind a check: an Actions job's id is its check run's id,
# so the latest check run of that name on the head names the job, and the job
# says how many times it has run (run_attempt, 1 until a re-run). A check run
# some other app wrote is no Actions job, and its id would 404 on the jobs
# routes, so it answers nulls rather than failing: there is nothing to re-run,
# which is not the host being down. The log is a courtesy for the run reading
# why the check failed, so a log that cannot be read (expired, still running)
# leaves log_tail null and does not fail the call.
host_check_job() { # <pr> <head_sha> <name>
  local cr id attempt log esc bel
  esc=$(printf '\033') bel=$(printf '\007')
  cr=$(api "$R/commits/$2/check-runs" -X GET -f "check_name=$3" -f per_page=100 \
    --jq '.check_runs | max_by(.id) // empty | {id, actions: (.app.slug == "github-actions")}') || return 1
  [ -n "$cr" ] || return 1
  if ! jq -e .actions <<<"$cr" >/dev/null; then
    printf '{"job_id":null,"attempt":null,"log_tail":null}\n'; return 0
  fi
  id=$(jq -r .id <<<"$cr")
  attempt=$(api "$R/actions/jobs/$id" --jq .run_attempt) || return 1
  # A job log carries colour codes, which the endpoint refuses to hand over
  # unless asked; they are stripped (CSI, then OSC ended by BEL; the C locale,
  # since `[@-~]` is no byte range in a UTF-8 one) so the tail is
  # text and not escapes inside a JSON string.
  if log=$(api "$R/actions/jobs/$id/logs" --allow-escape-sequences 2>/dev/null); then
    log=$(printf '%s\n' "$log" | tail -n 40 | LC_ALL=C sed -e "s/$esc\][^$bel]*$bel//g" -e "s/$esc\[[0-9;?]*[@-~]//g")
  else log=; fi
  jq -n --argjson id "$id" --argjson a "$attempt" --arg l "$log" \
    '{job_id: $id, attempt: $a, log_tail: (if $l == "" then null else $l end)}'
}
# Re-runs the one job, not the whole workflow run, so a leg's other jobs keep
# their results. Nothing on success, the status on failure, like every write.
host_check_rerun() { _gh_write -X POST "$R/actions/jobs/$1/rerun"; } # <job_id>

# `on_head` is keyed to the current head (a review on an older commit does not
# count), which poll-pr's default head rule reads; `all` carries every round
# across heads, for its --since rule. Every review stays in the two lists with
# its body, a reviewer's reply to one thread and a quota notice included, so the
# run can see what it is waiting on; which of them is a round is
# `SHIP_SUBSTANTIVE`'s grade, applied in poll-pr.
_gh_reviews_projection="$SHIP_REVIEW_CLIP"'
    def row: . as $r | {id: (.id | tostring), login: .user.login,
      state: (if .state == "APPROVED" then "approved" elif .state == "CHANGES_REQUESTED" then "changes" else "comment" end),
      submitted_at, body: ((.body // "") | clip($r.id))};
    {on_head: [.[] | select(.commit_id == $sha) | row], all: [.[] | row], total: length}'
host_pr_reviews() { # <pr> <head_sha> [<full-ids-json>]
  api "$R/pulls/$1/reviews" --paginate --jq '.[]' \
    | jq -s --arg sha "$2" --argjson full "${3:-[]}" "$_gh_reviews_projection"
}

_threads_query='query($o:String!,$r:String!,$n:Int!,$after:String){
  repository(owner:$o,name:$r){ pullRequest(number:$n){
    reviewThreads(first:100, after:$after){
      pageInfo{hasNextPage endCursor}
      nodes{ id isResolved isOutdated path
        comments(first:1){ nodes{ databaseId author{login} body url } }
        mine: comments(last:100){ nodes{ viewerDidAuthor } }
        latest: comments(last:1){ nodes{ body } } } } } } }'
# A thread's id, on both paths, is its root review comment's REST id as a
# string: the id the reply route posts to and the `ccr` routes key on; REST
# routes take it, where they cannot take GraphQL's node id. On this path a
# thread whose root was deleted takes its next comment's id. The GraphQL rows
# also carry `node`, the thread's node id, which resolveReviewThread needs and
# host_pr_threads drops.
# Returns 3 where GraphQL was refused (see `gql`), 1 on any other failure.
_gh_threads_gql() { # <pr>
  local after=null page out='[]' rc
  while :; do
    # shellcheck disable=SC2046  # deliberate: the optional `-F after=<cursor>` pair must split
    page=$(gql -f query="$_threads_query" -F o="$SHIP_OWNER" -F r="$SHIP_REPO" -F n="$1" \
      $([ "$after" != null ] && printf -- '-F after=%s' "$after") \
      --jq '.data.repository.pullRequest.reviewThreads'); rc=$?
    [ "$rc" -eq 0 ] || { [ "$rc" -eq 3 ] && return 3; return 1; }
    out=$(jq --argjson p "$page" --arg m "$SHIP_COMMENT_MARKER" '. + [$p.nodes[] | select(.comments.nodes[0] != null)
      | {id: (.comments.nodes[0].databaseId | tostring), comment_id: .comments.nodes[0].databaseId,
      resolved: .isResolved, outdated: .isOutdated, path,
      replied: ([.mine.nodes[] | select(.viewerDidAuthor)] | length > 0),
      ship_last: (.latest.nodes[0].body // "" | contains($m)),
      author: .comments.nodes[0].author.login, body: .comments.nodes[0].body, url: .comments.nodes[0].url,
      node: .id}]' <<<"$out")
    [ "$(jq -r .pageInfo.hasNextPage <<<"$page")" = true ] || break
    after=$(jq -r .pageInfo.endCursor <<<"$page")
  done
  printf '%s\n' "$out"
}
# The REST path, taken when the proxy refuses GraphQL: the proxy's thread list
# carries each thread's state, path and `comment_ids` but no body or author, so
# the PR's review comments, joined on id, supply the rest. `author` is the REST
# login, which keeps a bot's `[bot]` suffix where GraphQL drops it.
_gh_threads_ccr() { # <pr>
  local threads comments me
  threads=$(api "$R/pulls/$1/ccr/review_threads" --paginate --jq '.[]' | jq -s .) || return 1
  comments=$(api "$R/pulls/$1/comments?per_page=100" --paginate \
    --jq '.[] | {id, login: .user.login, body, url: .html_url}' | jq -s .) || return 1
  me=$(host_identity) || return 1
  jq -n --argjson t "$threads" --argjson c "$comments" --arg me "$me" --arg m "$SHIP_COMMENT_MARKER" '
    (reduce $c[] as $x ({}; .[$x.id | tostring] = $x)) as $by
    | [$t[] | select((.comment_ids | length) > 0) | ($by[.comment_ids[0] | tostring] // {}) as $root
       | {id: (.comment_ids[0] | tostring), comment_id: .comment_ids[0], resolved, outdated, path,
          replied: any(.comment_ids[]; $by[tostring].login == $me),
          ship_last: ($by[.comment_ids | max | tostring].body // "" | contains($m)),
          author: $root.login, body: $root.body, url: $root.url}]'
}
host_pr_threads() {
  local out rc
  out=$(_gh_threads_gql "$1"); rc=$?
  case $rc in
    0) jq 'map(del(.node))' <<<"$out" ;;
    3) _gh_threads_ccr "$1" ;;
    *) return 1 ;;
  esac
}

# The awaited login's own account of a round it has not delivered. A reviewer
# states it either as a review of its own or as a PR comment (Copilot did the
# former on PR #154, three times), so both surfaces merge into one time-sorted
# list and the latest notice wins whichever way it arrived.
# The login is compared through `SHIP_LOGIN_NORM`, so a `Login:` typed
# in another case cannot land rounds here and report blocked nowhere.
# The notice carries the time its row was posted, which is what poll-pr's since
# rule reads for a comment (#256).
_gh_blocked_select="$SHIP_BLOCKED_NOTICE$SHIP_LOGIN_NORM"'
  [sort_by(.at)[] | select((.login | norm) == ($l | norm)) | .at as $at
   | (.body // "") | notice_lines | {line: ., at: $at}]
  | last // null'
# A PR's conversation comments are its issue's on this host.
host_pr_comments() { host_issue_comments "$1"; }
# The repository activity API keeps each update to a branch with its `after` sha
# and its own time, newest first; a commit's dates are when it was made, which
# can be long before the push. No activity_type filter: a branch's first push is
# logged as `branch_creation`, and a force push as `force_push`.
host_pr_pushed_at() { # <pr> <head_ref> <head_sha>
  local t
  t=$(api "$R/activity" -X GET -f "ref=refs/heads/$2" -f per_page=100 \
    | jq -r --arg s "$3" '[.[] | select(.after == $s)] | first | .timestamp // empty') || return 1
  [ -n "$t" ] && printf '%s\n' "$t"
}

host_pr_reviewer_blocked() { # <pr> <login>
  { api "$R/issues/$1/comments" --paginate --jq '.[] | {login: .user.login, body, at: .created_at}'
    api "$R/pulls/$1/reviews"   --paginate --jq '.[] | {login: .user.login, body, at: .submitted_at}'
  } | jq -s --arg l "$2" "$_gh_blocked_select"
}
