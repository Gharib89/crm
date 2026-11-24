#!/usr/bin/env bash
# poll-pr: the flags, the cursor, --since and the reviewer derivation. Sourced by ../poll-pr.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

pr=$1; shift
timeout=""; interval=20; name=""; since=""; full='[]'; brief=false; after_run=0; want=""; cursor=""
while [ $# -gt 0 ]; do
  case $1 in
    --brief) brief=true; shift ;;
    --reviewer) case ${2:-} in ''|-*) ship_tooling "$usage" ;; esac; name=$2; shift 2 ;;
    --since) ship_flag_value "$usage" "${2:-}"; since=$2; shift 2 ;;
    # Ids stay strings: GitHub numbers a review and Azure DevOps numbers a
    # thread, and the adapters compare `.id | tostring` against this list.
    --full)
      ship_flag_value "$usage" "${2:-}"
      full=$(ship_id_list "$2") || ship_tooling "$usage"
      shift 2 ;;
    --timeout) ship_flag_value "$usage" "${2:-}"; timeout=$2; shift 2 ;;
    --interval) ship_flag_value "$usage" "${2:-}"; interval=$2; shift 2 ;;
    --sha) case ${2:-} in ''|-*) ship_tooling "$usage" ;; esac; want=$2; shift 2 ;;
    --cursor) ship_flag_value "$usage" "${2:-}"; cursor=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
# `--full` is a --brief modifier and nothing else. It lifted the adapter's clip
# in the full shape too, but that shape keeps its rounds under `reviews` and has
# no `rounds[]` to read the lifted body off: /ship 205 asked three times and got
# null each time, with no error to say the pair was wrong (#218).
[ "$full" = '[]' ] || $brief || ship_tooling "--full needs --brief; $usage"
[ -z "$since" ] || [ -n "$name" ] || ship_tooling "--since needs --reviewer"
# The cursor fixed the window's deadline and its landing rule's instant already,
# so a --since or --timeout beside it would be a second, silent opinion.
start=""; deadline=""
if [ -n "$cursor" ]; then
  [ -z "$since$timeout" ] || ship_tooling "--cursor resumes its own window: drop --since and --timeout"
  c=$(ship_cursor_read "$cursor") || c=null
  jq -e '(.start | type) == "number" and (.deadline | type) == "number"' <<<"$c" >/dev/null \
    || ship_tooling "--cursor does not read: pass the cursor a status: pending answer carried"
  start=$(jq -r .start <<<"$c"); deadline=$(jq -r .deadline <<<"$c")
  since=$(jq -r '.since // ""' <<<"$c"); after_run=$(jq -r '.after_run // 0' <<<"$c")
  [ -n "$want" ] || want=$(jq -r '.want // ""' <<<"$c")
  timeout=$((deadline - start))
  [ -z "$since" ] || [ -n "$name" ] || ship_tooling "--cursor holds a --since: pass its --reviewer again"
fi
# --since is compared as a string against submitted_at, which every adapter
# emits as UTC "YYYY-MM-DDTHH:MM:SSZ". Accept only what normalises to that, so
# an offset this cannot convert (+05:00) is refused outright rather than
# silently sorting wrong. Every mechanic that reports a timestamp
# (request-review's requested_at, open-pr's created_at) already emits it.
if [ -n "$since" ]; then
  case $since in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].*Z|\
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*+00:00)
      since=$(printf '%s' "$since" | sed 's/\.[0-9]*//; s/+00:00$/Z/') ;;
    *) ship_tooling "--since must be UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ), got: $since" ;;
  esac
fi
# The reviewer is derived from the profile before any host is reached, so a
# mistyped name or a --since the landing rule refuses costs no host call. The one
# refusal held back is a since-rule reviewer with no --since: the host may know
# the instant (`host_pr_requested_at`), and only once it is loaded.
reviewer=null; await=""; await_run=""; phrase=""; refusal=""; need_since=false
if [ -n "$name" ]; then
  row=$(ship_reviewer_by_name "$name") || ship_tooling "$row"
  d=$(ship_reviewer_derive "$row" "$since")
  refusal=$(jq -r '.refusal // empty' <<<"$d")
  if [ -n "$refusal" ] && [ -z "$since" ] && [ "$(jq -r .rule <<<"$d")" = since ]; then need_since=true
  elif [ -n "$refusal" ]; then ship_tooling "$refusal"; fi
  phrase=$(jq -r '.phrase // empty' <<<"$d")
  await=$(jq -r '.login // empty' <<<"$d")
  [ -n "$await" ] || ship_tooling "$name has no Login: to await"
  await_run=$(jq -r '.await_run // empty' <<<"$d")
  [ -n "$timeout" ] || timeout=$(jq -r .timeout <<<"$d")
  reviewer=$(jq -c --arg t "$timeout" '{name, login, rule, await_run, timeout: ($t | tonumber? // $t)}' <<<"$d")
fi
[ -n "$timeout" ] || timeout=480
