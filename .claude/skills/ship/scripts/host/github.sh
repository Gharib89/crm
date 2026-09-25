#!/usr/bin/env bash
# GitHub adapter: the host_* interface from _lib.sh over REST via `gh api`.
# REST throughout, retried by the policy on `api` below: gh's GraphQL paths
# (gh pr view --json, gh pr checks --watch) flake 401 mid-session. GraphQL is
# used only for the two things REST lacks, review-thread resolution state and
# resolveReviewThread, and those keep the flat one retry after 2 s that every
# REST call had before #173. Where a proxy refuses GraphQL outright (the Claude
# cloud sandbox's does), those two answer through the routes its refusal names
# instead; see `gql`. Sourced by _lib.sh's ship_load_host; needs SHIP_OWNER and
# SHIP_REPO set.

R="repos/$SHIP_OWNER/$SHIP_REPO"

# `gh api -i`, with the response status kept and the header block taken back
# out. gh loses the status whenever the error body is empty: a 500 carrying
# Content-Length: 0 surfaces as "unexpected end of JSON input" on stderr, which
# reads like a malformed request rather than a host that is down (#173), so the
# status comes from the response headers `-i` prints instead. Leaves
# SHIP_HTTP_STATUS at the last status line of the response, empty where the call
# got no HTTP answer at all.
#
# The whole response is buffered to split it, which is also what `--paginate`
# means here: gh prints one header block per page, separated from the page
# before it by a blank line, and the status is the last page's.
#
# A header block starts at the top of the response or on that separator, and at
# those two places alone; the separator goes out with the block it introduces,
# so the pages concatenate exactly as they do without `-i`. That is the whole
# defence against a body line shaped like a status line: one that does not
# follow a blank line is body, and every caller here reads `--jq` output, one
# JSON value per line, where a blank line does not arise. The status and the
# body come off the one program below, read twice, so a line is body for both
# or header for both: a second rule of its own would be the place the two could
# disagree.
_GH_AWK_SPLIT='
  BEGIN { start = 1 }
  start && /^HTTP\/[0-9.]+ [0-9][0-9][0-9]/ {
    hdr = 1; start = 0; held = ""; status = substr($2, 1, 3); next }
  hdr && /^[ \t\r]*$/ { hdr = 0; next }
  hdr { next }
  /^[ \t\r]*$/ { held = held "\n"; start = 1; next }
  { if (want == "body") { if (held != "") { printf "%s", held; held = "" } print }
    start = 0 }
  END { if (want == "status") print status }'
#
# gh's own message is the other half of the answer: the status says the host
# refused it, the message says why ("Validation Failed: body is too long"), and
# `open-pr` captures this stderr and prints it with `ship_tail40`. So each
# attempt's stderr is held rather than dropped, and a failure sends it on. A
# retried outage sends one message per attempt, which is the outage being
# visible rather than noise.
#
# The temp file takes an explicit `rm` where the standard asks for a RETURN
# trap, because a RETURN trap set in a callee REPLACES the caller's: arming one
# here would disarm the `rm -f "$buf"` that `api` uses to clean up a buffered
# payload. This function has one exit path, so the explicit removal covers it.
_gh() {
  local raw rc err
  # Cleared before anything can fail, so a call that gives up here reports no
  # status rather than the one the call before it left standing. `api` clears it
  # too, on the way in; this one covers `_gh_create`, which reaches `_gh`
  # without passing through `api`.
  SHIP_HTTP_STATUS=
  err=$(mktemp) || return 2
  raw=$(gh api -i "$@" 2>"$err"); rc=$?
  SHIP_HTTP_STATUS=$(printf '%s\n' "$raw" | awk -v want=status "$_GH_AWK_SPLIT")
  # The body goes out only on a success. A failed call's body is an error
  # document nothing here reads, and `_gh_create` prints its own JSON after
  # this returns, so emitting both would hand the caller two JSON values where
  # `ship_fail_host` expects one.
  if [ "$rc" -eq 0 ]; then
    [ -n "$raw" ] && printf '%s\n' "$raw" | awk -v want=body "$_GH_AWK_SPLIT"
  else
    ship_tail40 "$err"
  fi
  rm -f "$err"
  return $rc
}

# A failed host call answers with its status, so `ship_fail_host` can tell "the
# host refused this" from "the host is down". `_gh` leaves the status in a
# variable, and a pipeline element and a `$( )` are both subshells where one
# dies unread, so both helpers below print it instead. The `( )` is what makes
# the `exit` end this call rather than the mechanic that made it.
_gh_status() { jq -n --argjson s "${SHIP_HTTP_STATUS:-null}" '{status: $s}'; }
# A create: one attempt, because creates go through `_gh_create_verify`.
_gh_create() { ( _gh "$@" || { _gh_status; exit 1; } ); }
# A write through the retrying wrapper: nothing on success, the status on failure.
_gh_write() { ( api "$@" >/dev/null || { _gh_status; exit 1; } ); }

# Reads retry on failure. Creates go through `_gh_create_verify`, which re-reads
# before retrying so a slow success is left alone.
# A retry has to be the request it retries. `gh api "$@"` carries the argument
# list but not stdin, so a `--input -` payload the failed attempt already drained
# reaches the retry empty and GitHub rejects it as "Body should be a JSON
# object" (#108); buffering it once is what keeps the two byte-identical.
#
# The retry POLICY reads the status `_gh` recovered. A 5xx or a 429 is the host
# and not the payload, and it can outlast a single sleep by minutes (PR #170),
# so it gets a growing backoff, bounded at five attempts and about 30 seconds so
# a genuinely broken call still fails fast. Every other failure, the one 401
# flake this wrapper was written for included, keeps its single retry.
#
# So does a call whose method cannot be repeated, whatever its status. A 5xx can
# be a response lost on the way back from a write that landed, and the wider
# backoff would then repeat the write. GET, PATCH and DELETE are safe to send
# again; POST doubles what it creates, and the one PUT here is the squash merge,
# which must not be attempted five times over 30 s. Those keep exactly the one
# retry they had before #173 rather than gaining four. The method is read from
# `-X`, `-X<M>`, `--method` and `--method=<M>` alike, so a future call site
# cannot spell its way past the classification.
api() {
  local a prev="" buf="" attempt=0 wait method=GET again
  local -a args=() backoff=(2 4 8 16)
  # A call that returns before `_gh` runs would otherwise leave the previous
  # call's status standing, and `_gh_write` would report it as this one's.
  SHIP_HTTP_STATUS=
  for a in "$@"; do
    # `gh` spells a stdin payload either `--input -` or `--input=-`.
    if { [ "$prev" = --input ] && [ "$a" = - ]; } || [ "$a" = --input=- ]; then
      buf=$(mktemp) || return 2
      trap 'rm -f "$buf"' RETURN
      cat > "$buf"
      case $a in --input=-) a=--input=$buf ;; *) a=$buf ;; esac
    fi
    case $a in
      -X?*)        method=${a#-X} ;;
      --method=?*) method=${a#--method=} ;;
      *) case $prev in -X|--method) method=$a ;; esac ;;
    esac
    args+=("$a"); prev=$a
  done
  case $method in GET|PATCH|DELETE) again=yes ;; *) again=no ;; esac
  while :; do
    _gh "${args[@]}" && return 0
    attempt=$((attempt + 1))
    case $again${SHIP_HTTP_STATUS:-} in
      yes5??|yes429) [ "$attempt" -le "${#backoff[@]}" ] || return 1; wait=${backoff[$((attempt - 1))]} ;;
      *)             [ "$attempt" -le 1 ] || return 1; wait=2 ;;
    esac
    sleep "$wait"
  done
}
# GraphQL, telling one refusal apart from a flake. The Claude cloud sandbox's
# session proxy answers every GraphQL call with a 403 whose message names the
# REST routes it serves in its place, review threads among them
# (`GET .../pulls/{n}/ccr/review_threads`); gh prints that message on stderr. On
# that answer, from either attempt, `gql` returns 3, which the thread functions
# below read as "take the REST path", and says so once on stderr. The refusal is
# remembered for the rest of the process, so a polling mechanic pays for one
# refused call rather than one per poll. The callers run in command
# substitutions, whose subshells lose a variable, so the memory is a marker
# directory named for this user and this process's pid ($$ is the parent's in
# every subshell); `mkdir` creates it without following a link planted at that
# path, and only a real directory this user owns counts as the marker, since the
# name is predictable and a shared /tmp lets another user plant one. It is
# made only where the proxy refused, which is a disposable sandbox, so it is
# left there rather than cleaned up by a trap that would replace the
# mechanic's own.
# Switching on the host's answer rather than on the environment keeps the
# sandbox out of the adapter's logic, and a run outside it never meets the
# refusal, so it never makes a `ccr` call.
_gh_gql_marker() { printf '%s/ship-gh-graphql-refused.%s.%s' "${TMPDIR:-/tmp}" "${UID:-0}" "$$"; }
gql() {
  local err rc attempt m
  m=$(_gh_gql_marker)
  [ -d "$m" ] && [ ! -L "$m" ] && [ -O "$m" ] && return 3
  for attempt in 1 2; do
    # stderr into $err, stdout on to the caller, through fd 3.
    { err=$(gh api graphql "$@" 2>&1 1>&3 3>&-); rc=$?; } 3>&1
    [ "$rc" -eq 0 ] && return 0
    case $err in *ccr/review_threads*)
      mkdir "$m" 2>/dev/null
      echo "GraphQL refused by the session proxy; review threads go through its REST routes" >&2
      return 3 ;;
    esac
    [ "$attempt" -eq 1 ] && sleep 2
  done
  printf '%s\n' "$err" | tail -n 40 >&2
  return "$rc"
}

host_tooling_reasons() {
  command -v gh  >/dev/null || echo "gh not installed"
  command -v jq  >/dev/null || echo "jq not installed"
  command -v git >/dev/null || echo "git not installed"
}
# The Ubuntu archive is the one route the cloud sandbox proxy passes (the
# release tarball host 404s through it); apt installs 2.45.0 in about 12 s.
host_tooling_install() {
  command -v gh >/dev/null && return 0
  local sudo=""; [ "$(id -u)" -eq 0 ] || sudo="sudo -n"
  $sudo apt-get install -y gh
}
# Prints the status on failure, the way `_gh_write` does and for the same
# reason: the read runs in a command substitution at every call site, so a
# status left in a variable dies with the subshell, and the two writes that open
# with this read would answer a 5xx burst with a null status.
host_identity() { ( api user --jq .login || { _gh_status; exit 1; } ); }
host_can_push() { api "$R" --jq '.permissions.push // false'; }

# Prints `true` or `false`: whether a push to an open PR draws a fresh Copilot
# round on the default branch. Non-zero, printing nothing, where the host cannot
# answer, which is the ordinary result for a fork or an account without admin on
# the repo; preflight warns and continues on that, because a check that could
# not run is not a verdict.
#
# Two calls, and the second is the point: `rules/branches/<branch>` returns the
# rules the host has already resolved for ONE branch, enforcement and branch
# conditions applied. Listing rulesets instead and taking the first
# `copilot_code_review` found reads a rule scoped to `release/*` as governing
# the default branch, which is a refusal aimed at an honest profile.
#
# No `copilot_code_review` rule on that branch is `false`: nothing there draws a
# round from a push, which is precisely what `false` asserts to
# `ship_copilot_trigger_reason`.
host_copilot_review_on_push() {
  local branch review_on_push
  branch=$(api "$R" --jq .default_branch) || return 1
  [ -n "$branch" ] || return 1
  # `@uri`, because a default branch may carry a slash (`release/main`) and an
  # unencoded one silently addresses a different route, which comes back as a
  # failed read and skips the check rather than failing it.
  branch=$(jq -rn --arg b "$branch" '$b | @uri') || return 1
  # `--paginate` because the list is paged, and a rule on page two read as
  # absent would admit the contradiction this check exists to refuse.
  review_on_push=$(api "$R/rules/branches/$branch" --paginate \
    --jq '.[] | select(.type == "copilot_code_review") | .parameters.review_on_push') || return 1
  # Two rulesets can both carry the rule for one branch, and a two-line value
  # matches neither arm below, so it would read as "no rule here". First wins.
  review_on_push=${review_on_push%%$'\n'*}
  case $review_on_push in true|false) printf '%s\n' "$review_on_push"; return 0 ;; esac
  # No copilot_code_review rule on this branch: nothing here draws a round from
  # a push, which is what `false` asserts.
  printf 'false\n'
}

# The login `copilot_code_review` governs. Here rather than in `preflight.sh`,
# because a bot's brand is host detail and a generic mechanic matches on the
# trigger rather than the brand. An adapter with no Copilot prints nothing,
# which is what makes the check skip rather than branch on the host name.
host_copilot_login() { printf 'copilot-pull-request-reviewer[bot]\n'; }
# The name GitHub records that login under, on `requested_reviewers` and the
# timeline alike. A different name, not the login less its `[bot]` suffix, so
# `host_pr_request_review` matches it by alias rather than by suffix (#239).
_gh_copilot_recorded=Copilot

_norm_issue='{number, title, body: (.body // ""),
  state: (if .state == "open" then "open" else "closed" end),
  is_pr: (.pull_request != null),
  labels: [.labels[].name], assignees: [.assignees[].login],
  created_at, url: .html_url}'

host_issue_get()      { api "$R/issues/$1" --jq "$_norm_issue"; }
host_issue_comments() {
  api "$R/issues/$1/comments" --paginate --jq '.[] | {author: .user.login, body, created_at}' | jq -s .
}
# GitHub has native issue dependencies, so a failed query is "unavailable" rather than empty.
host_issue_blockers_open() {
  api "$R/issues/$1/dependencies/blocked_by" --paginate --jq '.[] | select(.state == "open") | .number' | jq -s .
}
# Cross-referenced timeline events hydrate each source PR in full, so the split
# between "claims to close this issue" and "merely mentions it" costs no extra call.
host_issue_linked_prs() {
  local n=$1 raw
  raw=$(api "$R/issues/$n/timeline" --paginate --jq '
      .[] | select(.event == "cross-referenced") | .source.issue
      | select(if .pull_request != null then (.state == "open" or .pull_request.merged_at != null)
               else .state == "open" end)
      | {number,
         kind: (if .pull_request != null then "pr" else "issue" end),
         state: (if .pull_request != null and .pull_request.merged_at != null then "merged" else .state end),
         body: (.body // "")}' \
    | jq -s 'unique_by(.number)') || return 1
  local out='{"closing":[],"mentions":[]}' row
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    # Only a PR can close an issue: an issue whose body says "closes #n" is a
    # mention, and reading it as a closing link would stop every run on this
    # issue with `existing PR`.
    if [ "$(jq -r .kind <<<"$row")" = pr ] && ship_body_closes "$(jq -r .body <<<"$row")" "$n"; then
      out=$(jq --argjson r "$row" '.closing += [$r | {number, state}]' <<<"$out")
    else
      out=$(jq --argjson r "$row" '.mentions += [$r | {number, kind, state}]' <<<"$out")
    fi
  done < <(jq -c '.[]' <<<"$raw")
  printf '%s\n' "$out"
}
host_issue_assign()   { api -X POST   "$R/issues/$1/assignees" -f "assignees[]=$2" >/dev/null; }
host_issue_unassign() { api -X DELETE "$R/issues/$1/assignees" -f "assignees[]=$2" >/dev/null; }
host_issue_has_label(){ api "$R/issues/$1/labels" --jq '.[].name' | grep -qxF -- "$2"; }
host_issue_add_label(){ api -X POST "$R/issues/$1/labels" -f "labels[]=$2" >/dev/null; }
# Check first: a DELETE 404s the same way whether the label was already gone or
# the call itself failed, and a removal reported on a label still there is
# exactly the residue this exists to stop.
host_issue_remove_label() {
  host_issue_has_label "$1" "$2" || return 0
  api -X DELETE "$R/issues/$1/labels/$(jq -rn --arg l "$2" '$l | @uri')" >/dev/null
}
# A create, so it is create-then-verify: a 5xx may have landed the comment, and a
# blind retry would post it twice. An issue comment is the same REST route as a
# PR comment, so it is host_pr_comment over a body file. Nothing on success, as
# on Azure DevOps; {"status": <n|null>} on failure, which `comment-issue` reads.
host_issue_comment() {
  local f out rc
  f=$(mktemp) || return 2
  trap 'rm -f "$f"' RETURN
  printf '%s' "$2" > "$f"
  out=$(host_pr_comment "$1" "$f"); rc=$?
  [ "$rc" -eq 0 ] || printf '%s\n' "$out"
  return $rc
}
host_issue_close()    { api -X PATCH "$R/issues/$1" -f state=closed -f state_reason=completed >/dev/null; }
host_issue_body()     { host_issue_get "$1" | jq '{body}'; }
# Nothing on success, {"status": <n|null>} on failure, as host_pr_set_body.
host_issue_set_body() { jq -n --rawfile b "$2" '{body: $b}' | _gh_write -X PATCH "$R/issues/$1" --input -; }

# Create-then-verify, the shape every create in this adapter has: attempt the
# POST once, and on failure look for the row a slow success would have left
# before posting again, so a flake costs a read rather than a second row. <post>
# performs the one POST and prints the row; <find> prints that row, or nothing
# when there is none. A <find> that fails is not an absent row: it answers
# unknown, so the caller gets the failure rather than a second POST. A <find>
# that ends in a pipe answers that way only under the `pipefail` every mechanic
# sets, which is where a failed `api` upstream of a `jq` becomes the pipeline's
# exit status.
#
# A <post> goes through `_gh_create`, not `api`: `api` retries on its own, and a
# create is retried only after the re-read.
#
# Whichever way this fails, what it prints last is the failed POST's status, so
# the mechanic's verdict names the status of the WRITE. Where the re-read failed
# too, that is the first POST's, held from before the read: the read's own
# status says nothing about whether the create landed.
_gh_create_verify() { # <post-fn> <find-fn>
  local out posted
  if out=$("$1"); then printf '%s\n' "$out"; return 0; fi
  posted=$out
  sleep 2
  out=$("$2") || { [ -n "$posted" ] && printf '%s\n' "$posted"; return 1; }
  if [ -n "$out" ]; then printf '%s\n' "$out"; return 0; fi
  "$1"
}

host_issue_create() { # <title> <body-file> <label>
  local title=$1 file=$2 label=$3 me
  me=$(host_identity) || return 1
  _issue_post() {
    jq -n --arg t "$title" --rawfile b "$file" --arg l "$label" \
      '{title: $t, body: $b, labels: (if $l == "" then [] else [$l] end)}' \
      | _gh_create -X POST "$R/issues" --input - --jq '{number, url: .html_url}'
  }
  _issue_find() {
    api "$R/issues?state=open&creator=$me&sort=created&direction=desc&per_page=20" --jq '.[]' \
      | jq -s --arg t "$title" '[.[] | select(.pull_request == null and .title == $t)] | first | select(. != null) | {number, url: .html_url}'
  }
  _gh_create_verify _issue_post _issue_find
}

# Put `Closes #<issue>` above the first `## ` heading, where no section rewrite
# reaches it: `update-pr-body` replaces a section wholesale, so a closing line
# appended to the end of the body sits inside the last section and the next
# rewrite of that section drops it. A body with no heading has no section to
# fall inside, so it keeps the append.
#
# Fenced blocks are skipped by `SHIP_AWK_FENCE`, matching the model
# `ship_body_closes` uses: a `## ` inside a fence is example text, and a closing
# line printed into a fence renders as code, so the host registers no link and
# the keyword test that gates a re-run reads false.
# The heading match stays anchored at column 0 on purpose: it has to agree
# with `update-pr-body`, whose `^## ` is what decides a section boundary.
_gh_add_closes() { # <body> <issue>
  awk -v n="$2" "$SHIP_AWK_FENCE"'
    { fenced = ship_fence($0) }
    !placed && !fenced && /^## / { print "Closes #" n; print ""; placed = 1 }
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
    api "$R/pulls?state=open&head=$SHIP_OWNER:$head" --jq 'first | select(. != null) | {number, url: .html_url, created_at}'
  }
  _gh_create_verify _pr_post _pr_find
}

host_pr_get() {
  api "$R/pulls/$1" --jq '{number, url: .html_url, title, body: (.body // ""),
    head_sha: .head.sha, head_ref: .head.ref, base_ref: .base.ref,
    state: (if .merged then "merged" elif .state == "open" then "open" else "closed" end),
    mergeable: (if .mergeable_state == "dirty" then "conflict"
                elif .mergeable == true then "clean" else "unknown" end)}'
}

host_pr_for_branch() { # <branch>
  api "$R/pulls?state=all&head=$SHIP_OWNER:$1&sort=created&direction=desc&per_page=1" \
    --jq 'first | if . == null then null else {number, state: (if .merged_at != null then "merged" elif .state == "open" then "open" else "closed" end)} end'
}

# Check runs plus classic commit statuses, one row per name, latest wins.
host_pr_checks() { # <pr> <head_sha>
  local sha=$2 runs statuses
  runs=$(api "$R/commits/$sha/check-runs" --paginate --jq '.check_runs[] | {name,
      status: (if .status != "completed" then "pending"
               elif (.conclusion | IN("success","neutral","skipped")) then "success"
               else "failure" end), at: (.completed_at // .started_at // "")}' | jq -s .) || return 1
  statuses=$(api "$R/commits/$sha/status" --jq '.statuses[] | {name: .context,
      status: (if .state == "success" then "success" elif .state == "pending" then "pending" else "failure" end),
      at: .updated_at}' | jq -s .) || return 1
  jq -n --argjson a "$runs" --argjson b "$statuses" \
    '$a + $b | group_by(.name) | map(max_by(.at) | {name, status})'
}

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
        mine: comments(last:100){ nodes{ viewerDidAuthor } } } } } } }'
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
    out=$(jq --argjson p "$page" '. + [$p.nodes[] | select(.comments.nodes[0] != null)
      | {id: (.comments.nodes[0].databaseId | tostring), comment_id: .comments.nodes[0].databaseId,
      resolved: .isResolved, outdated: .isOutdated, path,
      replied: ([.mine.nodes[] | select(.viewerDidAuthor)] | length > 0),
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
  jq -n --argjson t "$threads" --argjson c "$comments" --arg me "$me" '
    (reduce $c[] as $x ({}; .[$x.id | tostring] = $x)) as $by
    | [$t[] | select((.comment_ids | length) > 0) | ($by[.comment_ids[0] | tostring] // {}) as $root
       | {id: (.comment_ids[0] | tostring), comment_id: .comment_ids[0], resolved, outdated, path,
          replied: any(.comment_ids[]; $by[tostring].login == $me),
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
# The login is compared the way `SHIP_LANDED_BY` compares it, so a `Login:` typed
# in another case cannot land rounds here and report blocked nowhere.
# The notice carries the time its row was posted, which is what poll-pr's since
# rule reads for a comment (#256).
_gh_blocked_select="$SHIP_BLOCKED_NOTICE"'
  def norm: ascii_downcase | sub("\\[bot\\]$"; "");
  [sort_by(.at)[] | select((.login | norm) == ($l | norm)) | .at as $at
   | (.body // "") | notice_lines | {line: ., at: $at}]
  | last // null'
host_pr_reviewer_blocked() { # <pr> <login>
  { api "$R/issues/$1/comments" --paginate --jq '.[] | {login: .user.login, body, at: .created_at}'
    api "$R/pulls/$1/reviews"   --paginate --jq '.[] | {login: .user.login, body, at: .submitted_at}'
  } | jq -s --arg l "$2" "$_gh_blocked_select"
}

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
# `gh run list` rather than `api`: it builds the `--created` search itself, and
# a read that returns nothing is an answer here rather than a failure. `--limit`
# truncates a set `--created` has already bounded to the poll's own window, so
# it is set far above the runs one such window can hold rather than at the page
# size: a truncated read drops the run the poll is waiting on and reads as
# `never-queued`. Its stderr is tailed the way `_gh` tails its own,
# because a CLI diagnostic is unbounded and the caller prints one JSON object.
_gh_run_list() { # <workflow-file> <since-iso>
  gh run list --repo "$SHIP_REPO_SLUG" --workflow "$1" --event issue_comment --created ">=$2" --limit 200 \
    --json status,conclusion,createdAt,url,displayTitle \
    --jq 'map({status, conclusion, created_at: .createdAt, url, title: .displayTitle})'
}
# GitHub knows a workflow by its file name, and answers the repo-relative path
# the profile's `Workflow:` carries with a 404; every workflow sits flat in
# `.github/workflows/`, so the name alone is unambiguous.
host_workflow_runs() { # <workflow-file> <since-iso>
  local err rc wf=${1##*/}
  err=$(mktemp) || return 2
  trap 'rm -f "$err"' RETURN
  _gh_run_list "$wf" "$2" 2>"$err"; rc=$?
  # The flat one retry `gql` keeps rather than `api`'s backoff: a read that
  # answers non-zero reports the reviewer unreachable, and this CLI family is the
  # one the header names as flaking 401 mid-session, so a bad second would
  # otherwise be read as evidence about a reviewer that is working.
  if [ "$rc" -ne 0 ]; then sleep 2; : > "$err"; _gh_run_list "$wf" "$2" 2>"$err"; rc=$?; fi
  [ "$rc" -eq 0 ] || ship_tail40 "$err"
  return $rc
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

# The PR's review_requested events, oldest first, as {login, created_at}.
_gh_requested_events() { # <pr>
  api "$R/issues/$1/timeline" --paginate \
    --jq '.[] | select(.event == "review_requested") | select(.requested_reviewer.login) | {login: .requested_reviewer.login, created_at}' \
    | jq -s .
}

# The alias GitHub records <login> under, or nothing where it has none.
_gh_alias() { [ "$1" = "$(host_copilot_login)" ] && printf '%s' "$_gh_copilot_recorded"; return 0; }
# `recorded`: whether the name in hand is one GitHub records the reviewer under,
# given $l, its login, and $alias from `_gh_alias`: the login less a `[bot]`
# suffix (github-actions[bot] reads back as github-actions), or the alias
# (Copilot), in any case.
_gh_recorded_def='def norm: ascii_downcase | sub("\\[bot\\]$"; "");
  def recorded: norm as $r | [$l, $alias | select(. != "") | norm] | index($r) != null;'

# Whether a round is queued for the login since <since>: over $e, the PR's
# review_requested events, and $p, the logins pending on it now. An event before
# <since> answered an earlier request and counts for nothing. The pending list
# counts whatever its age, since a reviewer drops off it once it submits: one
# still on it has a round queued and unposted.
_gh_queued_select="$_gh_recorded_def"'
  any(($e[] | select(.created_at >= $s) | .login), $p[]; recorded)'

# A quota-out Copilot leaves no trace but a PR-page banner no API exposes: in
# every run observed since #266, its ruleset queued no request event for it and
# posted no notice, so the host's own record of requests is the only thing that
# tells a free round still coming from one that never will (#284).
host_pr_review_queued() { # <pr> <login> <since-iso>
  local e p
  e=$(_gh_requested_events "$1") || return 1
  p=$(api "$R/pulls/$1" --jq '[.requested_reviewers[].login]') || return 1
  jq -n --argjson e "$e" --argjson p "$p" --arg l "$2" --arg alias "$(_gh_alias "$2")" --arg s "$3" "$_gh_queued_select"
}

# Request, then read the request back off the host's own record: the login you
# request and the login you read back can differ (Copilot is requested as
# copilot-pull-request-reviewer[bot] and recorded as `Copilot`, the
# `_gh_copilot_recorded` alias above),
# and an empty requested_reviewers list proves nothing once the bot has posted.
host_pr_request_review() { # <pr> <login>
  local pr=$1 login=$2 ok=false before after pending readback now
  before=$(_gh_requested_events "$pr") || before='[]'
  # Stamped before the POST, so a review submitted the instant the request lands
  # is still at-or-after the fallback requested_at.
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  api -X POST "$R/pulls/$pr/requested_reviewers" -f "reviewers[]=$login" >/dev/null && ok=true
  sleep 2
  after=$(_gh_requested_events "$pr") || after='[]'
  pending=$(api "$R/pulls/$pr" --jq '.requested_reviewers[].login' | jq -R . | jq -s .)
  readback=$( { jq -r '.[]' <<<"$pending"; jq -r '.[].login' <<<"$after"; } | jq -R . | jq -s 'unique')
  # The request landed if the timeline gained a review_requested event during
  # this call, or if the reviewer is pending on `requested_reviewers` now under
  # any name it is recorded as (`recorded`).
  # The timeline can lag the wait above, so the pending list alone must be
  # enough, or a landed request reads as never-queued. The match reads the
  # pending list and not the timeline's logins, which keep every earlier
  # request on the PR: matched against those, a second request that queued
  # nothing would read as landed. The pending list drops a reviewer once it
  # submits, so a reviewer still on it has a round queued and unposted, which
  # lands after this call's `requested_at` and is what the caller waits for:
  # already pending counts as landed. The timeline is chronological, so a new
  # event is the last one.
  jq -n --argjson ok "$ok" --argjson b "$before" --argjson a "$after" --argjson p "$pending" --argjson rb "$readback" --arg l "$login" --arg alias "$(_gh_alias "$login")" --arg now "$now" \
    "$_gh_recorded_def"'
     {requested: ($ok and (($a | length) > ($b | length) or any($p[]; recorded))),
      readback: $rb,
      requested_at: (if ($a | length) > ($b | length) then ($a[-1].created_at // $now) else $now end)}'
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
# Nothing on success, {"status": <n|null>} on failure: the caller's `ship_fail_host`
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
host_pr_merge() { # <pr> <subject>
  api -X PUT "$R/pulls/$1/merge" -f merge_method=squash -f commit_title="$2" >/dev/null
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
