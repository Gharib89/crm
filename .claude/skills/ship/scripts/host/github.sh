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
  # A poll loop's read cannot see this variable from inside a `$( )`, so the
  # status also goes where `ship_poll_read` reads it.
  [ -z "${SHIP_HTTP_STATUS_FILE:-}" ] || printf '%s' "$SHIP_HTTP_STATUS" > "$SHIP_HTTP_STATUS_FILE"
  # The body goes out only on a success. A failed call's body is an error
  # document nothing here reads, and `_gh_create` prints its own JSON after
  # this returns, so emitting both would hand the caller two JSON values where
  # `ship_fail` expects one.
  if [ "$rc" -eq 0 ]; then
    [ -n "$raw" ] && printf '%s\n' "$raw" | awk -v want=body "$_GH_AWK_SPLIT"
  else
    ship_tail40 "$err"
  fi
  rm -f "$err"
  return $rc
}

# A failed host call answers with its status, so `ship_fail` can tell "the
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
      trap 'rm -f "$buf"; trap - RETURN' RETURN
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
gql() {
  local err rc attempt m
  m="${TMPDIR:-/tmp}/ship-gh-graphql-refused.${UID:-0}.$$"
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

# The adapter's remaining functions, by concern. Sourced here, after the helpers above, so each piece sees them.
# shellcheck source=github.d/issues.sh
source "$(dirname "${BASH_SOURCE[0]}")/github.d/issues.sh"
# shellcheck source=github.d/prs.sh
source "$(dirname "${BASH_SOURCE[0]}")/github.d/prs.sh"
# shellcheck source=github.d/runs-and-reviews.sh
source "$(dirname "${BASH_SOURCE[0]}")/github.d/runs-and-reviews.sh"
