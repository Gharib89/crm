#!/usr/bin/env bash
# Ship library: a reviewer's derived rules, the stale-base and PR-state reasons
# and the brief projection. Sourced by ../_lib.sh, never run.

# ship_reviewer_derive <row-json> <since>: what a round of that reviewer is
# polled and requested with, derived from its block rather than handed to the
# mechanic flag by flag, as {name, login, rule, await_run, transport, phrase,
# timeout, refusal}.
#
# `rule` is the landing rule `poll-pr` applies: `head` for an on-push reviewer,
# whose every push earns a round on the new head, and `since` for every other
# trigger, which posts one round per request on whatever head it lands on.
# `transport` is `comment` where `Request:` reads `comment <phrase>`, with
# `phrase` its text and, under the since rule, `await_run` the block's
# `Workflow:`: a workflow path, whose run separates a round still being written
# from one that will not come, or `native codex`, whose status poll-pr reads off
# the PR instead; the run read is keyed by the --since instant, which a head-rule
# poll has none of. `host` otherwise, the host's own request-a-reviewer call,
# with both null. `timeout`
# is the poll's default bound. Under the head rule it is 480, the bound the
# on-push loop has always polled a push's round on: nothing is requested, so no
# transport sizes it. Under the since rule it is by transport: 600 where the
# host's call is the transport, since its round can take several minutes to
# land and a bound of a minute or two reports `silent` on a review still
# coming, and 60 where a
# comment is, since the run read then holds the window open for as long as a
# round is being written. A comment transport is only ever polled after its
# request, so the 60 is the bound on the run's creation, not on the round: the
# host creates the
# `issue_comment` run within seconds of the comment, and from then on the run,
# not the constant, holds the window. A backed-up queue that outlasts it reads
# as no run, `never-queued`; a caller expecting one passes `--timeout`. Under
# `native codex` the same 60 bounds Codex's acknowledgement (👀 or a status
# edit), which arrived within 11 s on both #500 probe PRs.
#
# `refusal` is null, or the line `poll-pr` exits 2 on, where the caller's
# <since> disagrees with the rule: a --since for an on-push reviewer, or none for
# a since-rule one. It is reported rather than exited on because `request-review`
# takes no --since and reads the transport alone.
ship_reviewer_derive() {
  jq -c --arg s "$2" '
    ((.request // "") | startswith("comment ")) as $c
    | (if .trigger == "on-push" then "head" else "since" end) as $rule
    | {name, login, rule: $rule,
       await_run: (if $c and $rule == "since" then .workflow else null end),
       transport: (if $c then "comment" else "host" end),
       phrase: (if $c then (.request | ltrimstr("comment ")) else null end),
       timeout: (if $rule == "head" then 480 elif $c then 60 else 600 end),
       refusal: (if $rule == "head" and $s != ""
                 then "\(.name) is on-push, whose rounds land on the head: --since does not apply"
                 elif $rule == "since" and $s == ""
                 then "\(.name) is \(.trigger), whose rounds land by time: --since <iso> is required"
                 else null end)}' <<<"$1"
}

# ship_copilot_trigger_reason <name> <trigger> <review_on_push>: the one
# `profile invalid:` line for a Copilot block whose `Trigger:` contradicts the
# repository ruleset that actually drives it, or nothing.
#
# The ruleset, not the block, decides whether a push draws a round, so a profile
# the two disagree about is one that lies about its own loop: #182 spent nine
# rounds against `Cap: 3` because the block said on-push and nothing checked it
# against the setting that made it so.
#
# `review_on_push` is the host's answer, passed in rather than read here, so the
# contradiction is a pure decision the tests drive with no host. An empty third
# argument is the check that could not run (no admin rights, a fork, an adapter
# with no equivalent) and yields no reason: a check that could not run is not a
# verdict, and preflight warns on stderr instead of refusing.
#
# `true` admits on-push alone. `false` admits both triggers that take their
# rounds without a push, because the ruleset still opens one when the PR does:
# auto-once stops there, on-request asks for the rest.
# ship_copilot_row <login> <rows-json>: the `<name>\t<trigger>` of the reviewer
# block posting under <login>, or nothing where no block does. Tab-joined because
# the refusal names the block and the check reads its trigger, and a lookup that
# returned one without the other is how a refusal ends up naming the wrong
# reviewer.
ship_copilot_row() { # <login> <rows-json>
  [ -n "$1" ] || return 0
  jq -r --arg l "$1" \
    '.[] | select((.login // "") | ascii_downcase == ($l | ascii_downcase))
         | "\(.name)\t\(.trigger // "")"' <<<"$2" | head -1
}

ship_copilot_trigger_reason() { # <name> <trigger> <review_on_push>
  local name=$1 trigger=$2 rop=$3
  [ -n "$trigger" ] && [ -n "$rop" ] || return 0
  case $rop:$trigger in
    true:on-push|false:on-request|false:auto-once) ;;
    *) printf 'profile invalid: %s is Trigger: %s, but the copilot_code_review ruleset has review_on_push: %s\n' \
         "$name" "$trigger" "$rop" ;;
  esac
}

# ship_stale_base_reason <base-fresh-json>: the refusal `merge` answers with when
# the branch has not seen every commit on its base, or nothing when it has. The
# base can move between phase 5's `base-fresh` and the human's "merge" (PR #131
# merged 46 seconds after its base moved, landing a body that no longer matched
# the template), so the merge asks the same question again and reads the answer
# here.
#
# Anything that is not a `fresh: true` verdict refuses, an unparseable one
# included: a check that could not ask its question must not answer "fresh".
ship_stale_base_reason() {
  jq -rn --arg v "$1" '
    (try ($v | fromjson) catch null) as $j
    | if ($j | type) != "object" then "stale-base: base freshness unreadable"
      elif $j.fresh == true then empty
      elif ($j.behind | type) == "number" then "stale-base: behind \($j.behind) on \($j.base)"
      else "stale-base: base freshness unreadable" end'
}

# ship_pr_state_reason <state>: the refusal `merge` answers with when the PR
# no longer admits an authorized merge, or nothing when it does. Both
# adapters normalise to `open`, `merged` or `closed` (Azure DevOps maps
# `abandoned` to `closed`), and GitHub's merge endpoint accepts a closed PR, so
# without this a PR somebody deliberately closed is squashed onto the base by a
# mechanic authorized by an explicit "merge" or a clean opted-in gate.
#
# `merged` admits because `merge` skips the merge call for it and still owes the
# steps after it. Anything else refuses, an unreadable state included: a check
# that could not ask its question must not answer "open", the rule
# ship_stale_base_reason follows.
ship_pr_state_reason() { # ship_pr_state_reason <state>
  case $1 in
    open|merged) ;;
    '') printf 'pr-closed: state unreadable\n' ;;
    *)  printf 'pr-closed: %s\n' "$1" ;;
  esac
}

# ship_brief <poll-json> <identity> <on_head|all> [<full-ids-json>]: the projection `poll-pr
# --brief` prints, from the JSON the poll already built. One host fetch, two
# output shapes; the full shape stays the default so nothing existing changes
# meaning.
#
# <identity> is the login the run posts as: its own thread replies land as review
# rows of their own, and a round count that counts them reads its own voice as
# the reviewer's. An empty <identity> drops nothing: a host that could not
# name the run must not cost it the rounds it came for. The awaited reviewer's
# login, `.reviewer.login`, narrows the rounds to its rows: under `--reviewer
# claude` a Copilot quota notice is not a round of claude's (#255).
# <on_head|all> is `all` under the --since rule and `on_head` under the head
# rule, matching the list that rule lands from.
#
# A round's body comes down to its lead line and its finding items, which is what
# a triage acts on: the lead line carries the round's verdict and the items carry
# what to fix. A round with no items is clipped instead, so it is short without
# being empty. A row named by <full-ids-json> keeps its whole body: `--full` says
# "this round, verbatim" and outranks the cut, the way it outranks the adapter's.
#
# A body the adapter already clipped ends in the truncation marker, and the cut
# carries that marker through: a round nobody has read whole must not come back
# looking complete, or the loop stops before re-polling it with --full.
# Threads come down to the open ones, the only ones still owed a disposition,
# each carrying the `path` its finding sits on and the `lead` line that states
# it, cut at the same width: a run answers one thread off the brief, and a row
# holding an id alone sent it back for the full shape to read what the finding
# was. A lead that drops a later line with text carries the marker too (one
# marker, even where the width cut also bit), so a finding below a short first
# line is not read as the whole comment (#328). A CRLF blank line is blank.
# A thread named by <full-ids-json> carries its whole first comment as
# `lead`, so a clipped lead is lifted by the re-poll that lifts a clipped round
# (#327). The string "unavailable" passes through as itself. `reviewer_run` passes
# through whole, the string "unavailable" included: it is four fields, and a
# loop reading rounds from the brief is the loop that has to tell a silent
# reviewer from one whose run is still going.
#
# `pending_checks` (the names of the head's checks still pending) and `stale`
# (the host showing another head than the expected one, which the poll adds to
# its JSON for this projection alone) say why a window with a landed round
# closed `done: false`, exit 1. The full shape answers through `checks` and
# `head_sha`, the brief through these two (#667).
ship_brief() {
  jq -c --arg me "$2" --arg key "$3" --argjson full "${4:-[]}" "$SHIP_LOGIN_NORM"'
    def by($l): ((.login // "") | norm) == ($l | norm);
    def mine: $me != "" and by($me);
    def awaited($l): $l == "" or by($l);
    def clip: if length > 200 then .[0:200] + "\n...[truncated]" else . end;
    # Only the HTML names a reviewer body carries are markup: a quoted usage line
    # (`<n>`, `<name>`) is text and stays.
    def notags: gsub("</?(details|summary|picture|source|img|br|hr|p|a|div|span|sub|sup|b|i|em|strong|code|pre|kbd|table|thead|tbody|tr|td|th|ul|ol|li|h[1-6]|blockquote)(\\s[^>]*)?/?>"; ""; "i");
    # The markup of a round comes off before any line is read. A `<details>` block is
    # replaced by what a loop acts on: `What changed` (the summary of the PR) and
    # `Resolved` (findings an earlier round closed) go, a block holding list
    # items keeps its body, and one holding none is itself a finding (the `Previously missed` of a
    # Copilot overview, which has no thread to carry it), lifted to its title
    # and first line. Innermost first, so a block inside a block lifts before
    # the one around it is read. A `<picture>` is a severity badge and goes with
    # the space before it.
    def details:
      gsub("<details[^>]*>\\s*<summary>(?<t>(?:(?!</summary>)[\\s\\S])*)</summary>(?<b>(?:(?!<details)[\\s\\S])*?)</details>";
           (.t | notags | gsub("^\\s+|\\s+$"; "")) as $t
           | if ($t | test("^(What changed|Resolved)")) then ""
             elif (.b | test("(^|\n)[ \t]*([-*+]|[0-9]+[.)])[ \t]")) then .b
             else "\n- \($t): \(.b | notags | [splits("\n") | select(test("^\\s*$") | not)][0] // "")\n" end);
    def unhtml:
      gsub("<!--[\\s\\S]*?-->"; "") | gsub("[ \t]*<picture>[\\s\\S]*?</picture>"; "")
      | details | details | details | notags
      | gsub("</?[A-Za-z][^>]*$"; "") | gsub("<!--[\\s\\S]*$"; "");
    # The lead (the first line with text that is not a heading, behind the h3
    # verdict heading above it where there is one: an overview states its
    # severity there and nowhere else), the `Findings:` line and the items. A round with none of the last two is clipped instead.
    def finding_items:
      (if endswith("\n...[truncated]") then "\n...[truncated]" else "" end) as $mark
      | sub("\n\\.\\.\\.\\[truncated\\]$"; "") | unhtml as $body
      | [$body | splits("\n") | sub("[ \t\r]+$"; "") | select(test("^[ \t]*$") | not)] as $lines
      | [$lines[] | select(test("^[ \t]*([-*+]|[0-9]+[.)])[ \t]"))] as $items
      | [$lines[] | select(test("^[ \t*_]*Findings:"))] as $found
      | ([$lines | to_entries[] | select(.value | test("^#") | not)][0]) as $first
      | ($first.value // $lines[0] // "") as $text
      | ([$lines[:($first.key // 0)][] | select(test("^###[ \t]+[^# \t]"))][0] // null) as $verdict
      | (if $verdict == null then $text else ($verdict | sub("^###[ \t]+"; "")) + ": " + $text end) as $lead
      | if ($items | length) == 0 and ($found | length) == 0 then (($body + $mark) | clip)
        else (reduce ([$lead] + $found + $items)[] as $l ([]; if index([$l]) then . else . + [$l] end)
              | join("\n")) + $mark end;
    def lead: [splits("\n") | select(test("^[ \t\r]*$") | not)] as $lines
      | ($lines[0] // "") as $first | ($first | clip) as $cut
      | if ($lines | length) > 1 and $cut == $first
        then $first + "\n...[truncated]" else $cut end;
    (.reviewer.login // "") as $await
    | {head_sha, mergeable, reviewer, landed_by, refused_by, not_reviewed, done,
     pending_checks: [.checks[]? | select(.status == "pending") | .name], stale: (.stale // false),
     reviewer_blocked, reviewer_run,
     rounds: [.reviews[$key][] | select((mine | not) and awaited($await)) | . as $r
              | {id, submitted_at, substantive,
                 body: (if ($full | index("open")) or ($full | index($r.id | tostring)) then $r.body
                        else ($r.body | finding_items) end)}],
     threads: (if (.threads | type) == "array"
               then [.threads[] | select(.resolved | not)
                     | . as $t | {id, path,
                                  lead: (if ($full | index("open")) or ($full | index($t.id | tostring)) then ($t.body // "")
                                         else (($t.body // "") | lead) end),
                                  resolved, replied}]
               else .threads end)}' <<<"$1"
}
