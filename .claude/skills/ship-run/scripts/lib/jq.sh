#!/usr/bin/env bash
# Ship library: the id list and the jq programs the review mechanics embed.
# Sourced by ../_lib.sh, never run.

# ship_id_list <comma-list>: the ids `poll-pr --full` takes, trimmed, blanks
# dropped, as a JSON array. Prints nothing and answers non-zero when the list
# holds no id or one that reads as an option, so `--full --timeout` is the
# caller's usage error rather than a poll that quietly matches no row.
ship_id_list() {
  jq -cne --arg n "$1" '
    ($n | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(. != ""))) as $ids
    | if ($ids | length) == 0 or any($ids[]; startswith("-")) then empty else $ids end'
}

# The review-body clip both adapters run, so they clip in one vocabulary: a jq
# `clip($id)` filter over a body string, invoked with `--argjson full <ids>`.
# The cut leaves a marker, so a clipped round reads as clipped. The id `open`
# unclips every row (`poll-pr --brief --full open`).
# `$id | tostring` so a row the host gives no id (an Azure DevOps vote) compares
# without erroring; such a row carries no body to unclip.
# shellcheck disable=SC2034  # read by the host adapters that source this library
readonly SHIP_REVIEW_CLIP='def clip($id):
  if ($full | index("open")) or ($full | index($id | tostring)) then .
  elif length > 2000 then .[0:2000] + "\n...[truncated]"
  else . end;'

# The vocabulary a reviewer refuses a round in, shared by the two readers: the
# blocked lookup, which returns the notice line, and `SHIP_SUBSTANTIVE` below,
# which refuses to call such a body a round.
#
# The refusal VERB carries the match, not the bare noun. A round that merely
# mentions a quota or a rate limit ("back off rather than burn the API quota")
# is a finding, and a draft of this that matched the noun alone dropped such a
# round as a notice, which costs the run its whole poll window.
#   notice_body:  the body on one line, HTML comments and quote or bold marks
#                 gone, so a wrapped notice and one carrying a generated footer
#                 classify the same as the one-line form. PR #154's notice
#                 arrived all three ways.
#   notice_lines: the matching lines of a body, de-quoted and unbolded: what the
#                 blocked lookup reports, so the summary quotes the reviewer.
#   is_notice:    the body is ONLY a refusal, every sentence of it one. Known
#                 limit: a one-sentence round whose whole content is a refusal
#                 phrase reads as a notice. The run then waits the window out
#                 and reports the reviewer blocked, with the body still in
#                 `rounds[]` to read, rather than losing it.
readonly SHIP_BLOCKED_NOTICE='def notice_re:
  "(unable|not able|cannot|could not|failed)( to)? [a-z ]{0,24}review"
  + "|(reached|exceeded|hit|out of|ran out of) [a-z ]{0,24}(quota|rate limit)"
  + "|next included review";
def notice_body:
  gsub("<!--[\\s\\S]*?-->"; "") | gsub("[*>]"; "") | gsub("\\s+"; " ")
  | sub("^ +"; "") | sub(" +$"; "");
def notice_lines:
  splits("\n") | select(test(notice_re; "i")) | sub("^>\\s*"; "") | gsub("\\*"; "");
def is_notice:
  [notice_body | splits("(?<=[.!?]) +") | select(test("\\S"))] as $sentences
  | ($sentences | length) > 0 and all($sentences[]; test(notice_re; "i"));'

# Ship's grade of a review row, applied by poll-pr alone, once, to the
# `host_pr_reviews` answer before any rule below reads it: every row in
# `on_head` and `all` gets `substantive`, overwriting whatever an adapter sent,
# so a notice refuses the round on every host alike (#268). A row with a body is
# a round unless the body is only a refusal notice. A bodiless row is a round
# only when it is a verdict, `approved` or `changes`: a GitHub approval with no
# text, an Azure DevOps vote. A bodiless comment is a reviewer's reply to one
# thread, which posts as a review row of its own, and counting it lands round 2
# off round 1.
# shellcheck disable=SC2034  # read by poll-pr
readonly SHIP_SUBSTANTIVE="$SHIP_BLOCKED_NOTICE"'
  def substantive:
    if (.body // "") != "" then (.body | is_notice | not)
    else (.state | IN("approved", "changes")) end;
  .on_head |= map(.substantive = substantive) | .all |= map(.substantive = substantive)'

# The one rule for matching a login: case-insensitive, less a `[bot]` suffix, so
# `Copilot` and `copilot[bot]` are one reviewer wherever a login is compared.
# Prepend it to a jq program that calls `norm`.
readonly SHIP_LOGIN_NORM='def norm: ascii_downcase | sub("\\[bot\\]$"; "");'

# poll-pr's two row rules over a `host_pr_reviews` projection: `round_by(<row
# predicate>)`, invoked with `--arg l <login>` and `--arg s <since|"">`, both
# sides normalised by `norm`. It answers "head" under the head rule (`$s` is
# empty) when a row on the head satisfies the predicate, "since" under the since
# rule when a row in `all` submitted at or after `$s` does, else null.
#
# Landing passes `.substantive`: only a SUBSTANTIVE row lands, as
# `SHIP_SUBSTANTIVE` graded it, which is what keeps a quota notice from
# answering for a round that has yet to arrive (#155).
#
# Refusal passes a NOTICE predicate: a row by that login that is not
# substantive and has a body, the one kind `is_notice` leaves. A quota notice answers the request
# it follows, and no round is coming after it: Copilot's quota is the requesting
# user's and monthly, so waiting the window out, or asking again, buys nothing
# (#248, #250: every poll spent its whole window on a refusal already posted).
# Read only when no round landed, so a round that follows a notice still lands. A
# notice posted as a PR comment rather than as a review leaves no row here:
# poll-pr admits it from `host_pr_reviewer_blocked`'s `at`, under the since rule
# only (#256).
# shellcheck disable=SC2034  # read by poll-pr
readonly SHIP_ROUND_BY="$SHIP_LOGIN_NORM"'
  def round_by(row):
    def mine: [.[] | select(row and ((.login | norm) == ($l | norm)))];
    if $s == "" then (if (.on_head | mine) != [] then "head" else null end)
    else (if (.all | mine | map(select(.submitted_at != null and .submitted_at >= $s))) != [] then "since" else null end)
    end;'

# poll-pr's pick of THE run a comment-transport reviewer's round is waiting on,
# over a `host_workflow_runs` projection, invoked with `--arg t <the PR's title>`.
# The workflow fires on every comment in the repo, so the rows are narrowed to
# the PR being polled first. Among what is left a LIVE run wins: it is the one
# still able to deliver the round, and a concluded run that arrived after it
# would otherwise close the window on a round still being written. Then the
# newest run that did something, because a `skipped` run is the workflow's own
# `if` refusing a comment that was not the request. Live is every status but
# `completed`, so the approval states a host also reports (`requested`,
# `waiting`, `pending`) hold the window the way `queued` does. The title is the
# only link the host offers, so a PR renamed mid-poll matches nothing: the
# adapter's own comment carries what that costs. `none` is the read finding
# no run at all, which poll-pr reports as never-queued. `denied` is null
# here: poll-pr fills it from `host_run_denials` once, for a completed pick.
# shellcheck disable=SC2034  # read by poll-pr
readonly SHIP_REVIEWER_RUN='
  def live: .status != "completed";
  ([.[] | select(.title == $t)] | sort_by(.created_at)) as $rows
  | (([$rows[] | select(live)] | last)
     // ([$rows[] | select(.conclusion != "skipped")] | last)
     // ($rows | last)
     // {status: "none", conclusion: null, url: null})
  | {status, conclusion, url, denied: null}'

# poll-pr's read of a native Codex round (`Workflow: native codex`), over a
# `host_pr_native_activity` answer, invoked with `--arg l <login> --arg s <since>
# --arg h <head sha>`. Codex runs no workflow: it acknowledges a request with 👀
# on the request comment (removed when the round ends), keeps one status comment
# marked `codex-pull-request-review-summary` edited in place, its Code Review row
# naming the status, the time and the short commit, and delivers a round either
# as a formal review (findings) or, clean, as an issue comment whose first prose
# line is `Codex Review: Didn't find any major issues` plus 👍 on the PR, with no
# review at all (probed on #504 and #505 for #500). A comment's first prose line
# is its first non-blank line, trimmed, that is not a whole-line `<!-- ... -->`
# marker; since #736 the clean comment opens with
# `<!-- codex-review-receipt:v1 -->`. Answers {run, rounds, notice}:
#   rounds  each clean delivery at or after <since> as a REVIEW row, so the
#           landing rule lands a clean round like any other; the 👍 counts only
#           where no clean comment does.
#   notice  {line, at} for the first comment by the login at or after <since>
#           that is neither the status comment nor a clean one: a refusal of a
#           shape not yet observed (quota, access, a missing environment), whose
#           first prose line poll-pr reports as the blocked notice.
#   run     the `reviewer_run` shape, so the window logic a workflow run drives
#           drives this too: completed/success once a clean round or a Completed
#           row on the head is in; completed/refused beside a notice; in_progress
#           while the 👀 is on, or the row, touched since the request, is not
#           Completed; completed/stale-head for a Completed row on another
#           commit; "unavailable" for a Completed row whose commit cell holds no
#           backticked sha, since a row this reader cannot parse names no
#           commit; `none` for silence, which is how an unconnected repo answers.
#           Completed is the only terminal word the probes showed: a row ending
#           in another word reads in progress and holds the window to the
#           ceiling, which then reports still-running.
# A status comment last edited before <since> belongs to an older request.
# `Didn.t` matches the apostrophe without one inside this single-quoted string.
# shellcheck disable=SC2034  # read by poll-pr
readonly SHIP_NATIVE_CODEX="$SHIP_LOGIN_NORM"'
  def utc: (. // "") | sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z");
  def is_status: (.body // "") | contains("<!-- codex-pull-request-review-summary -->");
  def first_prose_line: [(.body // "") | splits("\n") | sub("^\\s+"; "") | sub("\\s+$"; "")
    | select(. != "" and (test("^<!--(?:(?!-->).)*-->$") | not))] | first // "";
  def is_clean: first_prose_line | test("^Codex Review: Didn.t find any major issues");
  def mine: ((.login // "") | norm) == ($l | norm);
  [.comments[] | select(mine)] as $c
  | ([$c[] | select(is_status)] | last) as $st
  | (($st.body // "") | [splits("\n") | select(test("\\*\\*Code Review\\*\\*"))] | first // "") as $row
  | (($row | capture("`(?<sha>[0-9a-f]{7,40})`").sha) // null) as $sha
  | ($st != null and ($st.updated_at | utc) >= $s) as $touched
  | [$c[] | select(is_clean and (.created_at | utc) >= $s)
     | {id: (.id | tostring), login, state: "comment", submitted_at: .created_at, body}] as $clean
  | [.pr_reactions[] | select(mine and .content == "+1" and (.created_at | utc) >= $s)
     | {id: null, login, state: "comment", submitted_at: .created_at,
        body: "Codex Review: reacted 👍 to the PR, posting no findings."}] as $thumbs
  | (if $clean != [] then $clean else $thumbs end) as $rounds
  | ([$c[] | select((is_status | not) and (is_clean | not) and (.created_at | utc) >= $s)] | first) as $reply
  | {rounds: $rounds,
     notice: (if $reply == null then null else {line: ($reply | first_prose_line), at: ($reply.created_at | utc)} end),
     run: (if $rounds == [] and $reply == null and $touched and $sha == null
             and ($row | test("Completed")) then "unavailable"
           else {url: ($st.url // $reply.url // null), denied: null} +
       if $rounds != [] then {status: "completed", conclusion: "success"}
       elif $reply != null then {status: "completed", conclusion: "refused"}
       elif any(.request_reactions[]; mine and .content == "eyes") then {status: "in_progress", conclusion: null}
       elif $touched and ($row | test("Completed") | not) then {status: "in_progress", conclusion: null}
       elif $touched and ($h | startswith($sha)) then {status: "completed", conclusion: "success"}
       elif $touched then {status: "completed", conclusion: "stale-head"}
       else {status: "none", conclusion: null, url: null} end end)}'
