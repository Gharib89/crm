#!/usr/bin/env bash
# Ship library: a poll loop's host read and cursor, the stale-head test and the
# reviewer rows. Sourced by ../_lib.sh, never run.

# A poll loop's host read, telling "no answer yet" from an answer. A read that
# fails with no HTTP status (a dropped connection, a TLS timeout) is no answer
# yet, so the loop keeps going; one the host answered with a status is a real
# refusal. The adapter's `_gh` writes each call's status to
# $SHIP_HTTP_STATUS_FILE, a file rather than a variable because most reads run
# in a `$( )` where a variable dies unread; an adapter that never writes it (Azure
# DevOps, whose `az` reports no status) has every failure read as no answer, so
# its persistent refusal costs the loop three reads before it ends.
# The caller sets $SHIP_HTTP_STATUS_FILE to a file it traps for removal.
# ship_poll_read <out-file> <fn> <args...>: 0 answered, the answer in <out-file>
# · 1 the host refused, with a status · 3 no answer · 4 cut off at
# $SHIP_READ_LIMIT seconds, where the caller sets one. A wait bounded by a window
# or a call cap bounds its reads the same way: a read that outlasts the bound has
# spent the time the answer would have been graded in (#1346). The read runs in
# the background under a watchdog that ends it and its children, since the host
# functions are shell functions `timeout` cannot run.
ship_poll_read() {
  local out=$1 pid dog rc; shift
  : > "$SHIP_HTTP_STATUS_FILE"
  if [ -n "${SHIP_READ_LIMIT:-}" ]; then
    "$@" > "$out" & pid=$!
    # The watchdog traps TERM to end its own sleep, so `kill "$dog"` alone stops
    # both once the read has returned: left running, the sleep would hold a
    # caller's `$( )` pipe open until the limit ran out, and a watchdog that
    # outlived its sleep would signal a pid the `wait` below has already reaped.
    ( trap 'kill "$s" 2>/dev/null; exit 0' TERM
      sleep "$SHIP_READ_LIMIT" & s=$!; wait "$s"
      pkill -TERM -P "$pid" 2>/dev/null; kill -TERM "$pid" 2>/dev/null ) >/dev/null 2>&1 </dev/null & dog=$!
    wait "$pid"; rc=$?
    kill "$dog" 2>/dev/null; wait "$dog" 2>/dev/null
    [ "$rc" -ne 143 ] || return 4
    [ "$rc" -ne 0 ] || return 0
  else
    "$@" > "$out" && return 0
  fi
  [ -s "$SHIP_HTTP_STATUS_FILE" ] && return 1
  return 3
}

# ship_checks_valid <checks-json>: true when a `host_pr_checks` answer holds the
# rows its contract in lib/host.sh names, each a string name and a status of
# pending, success or failure. A row outside it is no verdict, and a grade that counts only the
# `failure` and `pending` rows would read it as green (#1343).
ship_checks_valid() {
  jq -e 'type == "array" and all(.[]; type == "object" and (.name | type) == "string"
    and (.status | IN("pending", "success", "failure")))' <<<"$1" >/dev/null 2>&1
}

# A wait's cursor: the window's state, opaque to the caller, which hands it back
# with `--cursor` to resume the same window. No call holds the tool past
# SHIP_CALL_CAP seconds (540, under the harness's 600 s limit on one tool call),
# so a window longer than that is a chain of calls joined by cursors.
SHIP_CALL_CAP=${SHIP_CALL_CAP:-540}
ship_cursor_make() { jq -rn --argjson c "$1" '$c | tojson | @base64'; } # <json>
ship_cursor_read() { jq -cn --arg c "$1" '$c | @base64d | fromjson | if type == "object" then . else error end' 2>/dev/null; } # <cursor>

# ship_head_stale <pr-json> [<sha>]: true while the host shows a PR head other
# than the expected one, so `ci-wait` and `poll-pr` wait inside their window
# rather than grade the previous head: straight after a push the host can still
# show it (#394). The expected head is `<sha>`, a prefix match against the host's
# full one, else the local HEAD when the caller's checkout is on the PR's head
# branch. With neither there is no expected head, and nothing is stale.
ship_head_stale() { # <pr-json> [<sha>]
  local want=${2:-}
  if [ -z "$want" ]; then
    [ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" = "$(jq -r .head_ref <<<"$1")" ] || return 1
    want=$(git rev-parse HEAD 2>/dev/null) || return 1
  fi
  case $(jq -r .head_sha <<<"$1") in "$want"*) return 1 ;; esac
}

# ship_reviewers <profile-body>: the `## Reviewers` section as one JSON row per
# reviewer, in profile order, each
# {name, login, trigger, request, workflow, cap, resolve, gating, fallback_for,
# instructions}.
# `None.` reads as null, `Cap:` as a number, `Gating:` as a boolean. A `Cap:` that
# is neither a number nor `None.`, and a `Gating:` that is neither `yes` nor `no`,
# come back as the raw string, so `ship_reviewer_reasons` can refuse it rather
# than read a typo as "uncapped" or "not gating".
#
# The row is the whole block, not just the fields preflight checks: a parser that
# dropped the rest would force whoever needs `Login:` or `Request:` to parse the
# block a second time, which is the drift this function exists to prevent.
#
# Only the nine field names are read, and only anchored at the start of a line
# inside a `### ` block: the prose paragraph under a block explains the reviewer
# and quotes its own field names ("what makes the trigger `on-push`"), which a
# looser match would read as fields. First occurrence wins within a block for the
# same reason, so prose below the fields cannot overwrite them; the counter keys
# that, not the name, or a repeated `### <name>` would suppress its own fields and
# come back as a row of nulls that no refusal can see. Tabs are the record
# delimiter into jq, so a value carrying one is flattened rather than truncated at
# it. Fenced blocks come out by SHIP_AWK_FENCE, so a `### ` inside a template
# example is not a reviewer, the way it is not a heading to `ship_body_headings`.
ship_reviewers() {
  awk "$SHIP_AWK_FENCE"'
    { fenced = ship_fence($0) }
    fenced { next }
    /^## / { s = $0; sub(/[ \t\r]+$/, "", s); sec = (s == "## Reviewers"); name = ""; next }
    !sec { next }
    /^### / {
      name = $0; sub(/^### /, "", name); gsub(/\t/, " ", name); sub(/[ \t\r]+$/, "", name)
      blk++; print "n\t" name; next
    }
    !blk { next }
    /^(Login|Trigger|Request|Workflow|Cap|Resolve|Gating|Fallback-for|Instructions): / {
      k = substr($0, 1, index($0, ":") - 1)
      if (seen[blk "\034" k]++) next
      v = substr($0, index($0, ":") + 2)
      gsub(/\t/, " ", v); sub(/[ \t\r]+$/, "", v)
      print "f\t" k "\t" v
    }' <<<"$1" | jq -Rs '
    def norm($k; $v):
      if $v == "None." or $v == "" then null
      elif $k == "cap" then ($v | tonumber? // $v)
      else $v end;
    reduce (splits("\n") | select(. != "") | split("\t")) as $p ([];
      if $p[0] == "n"
      then . + [{name: $p[1], login: null, trigger: null, request: null,
                 workflow: null, cap: null, resolve: null, gating: false,
                 fallback_for: null, instructions: null}]
      else (($p[1] | ascii_downcase | sub("-"; "_")) as $k
            | .[length - 1] += {($k): (if $k == "gating"
                                       then ($p[2] | if . == "yes" then true elif . == "no" then false else . end)
                                       else norm($k; $p[2]) end)})
      end)'
}

# ship_reviewer_reasons <rows-json> <checkout-root>: one `profile invalid:
# <detail>` line per reviewer-block fault, or nothing when the blocks hold.
# Preflight's only reviewer check, so the faults are refused in both lanes and
# before the claim, rather than at the phase that would have driven the reviewer.
#
# Fallbacks that name each other in a cycle leave no primary to withhold them
# for, and a `Cap:` below zero or with a fraction bounds no loop, so each is
# refused, as is a `Gating:` that is not yes or no; the cycle is reported once,
# from its first block in profile order. A name more than one block carries is
# ambiguous as a fallback target, so those blocks are left out of the walk and
# the rest are still checked.
#
# A fallback that is not on-request cannot be withheld, and withholding it until
# its primary degrades is the whole point of a fallback; one naming a reviewer
# nobody listed has no primary to stand in for, an on-request reviewer with no
# cap has no bound on its loop, and a `Cap:` that is not a number is a typo that
# would read as an uncapped one. A block named `self-review`, or starting
# `self-review `, would share the Run file's `Round: self-review` and
# `Stop: self-review:` lines with phase 4, so phase 4's record would close its
# phase 7.
#
# `Workflow:` and `Request:` are refused as a pair, in both directions. A comment
# transport's round comes from the run that comment starts, and that run is the
# only signal separating a round still being written from one that will not come,
# so a block asking for one and naming no file leaves phase 7 polling on a
# constant; a file named on a block no comment drives is a value nothing reads.
# The third is the one filesystem stat here, taken against `<checkout-root>`
# rather than a root read inside, so the cases drive it over a fixture tree: a
# path naming no file buys the same silence as no path, and is mistyped far more
# often than it is left out. The stat is keyed by path rather than by reviewer
# name, because two blocks sharing a name would otherwise answer for each other.
#
# `Request: comment` with the phrase left off is refused on its own, before the
# pair is read: the comment transport has no phrase to post, so that block
# cannot be asked for a round at all, and reading it as a transport owing a
# `Workflow:` would let one carrying a `Workflow:` through. With the bare value
# refused above it, the transport is a plain `startswith("comment ")`, which is
# also what keeps a mechanic name out of it: `comment-pr` is a mechanic, and a
# word boundary in place of the space would read it as a transport.
#
# `Workflow: native <integration>` is the other status source a comment
# transport may name: an integration that posts its round with no workflow run
# behind it (native Codex review), whose status poll-pr reads off the PR itself.
# It names no file, so it is never statted, and only an integration poll-pr has
# a reader for is admitted, since any other would poll on nothing.
#
# Anything that is not an array refuses, exactly as `ship_stale_base_reason`
# refuses an unreadable verdict: a parse that died must not come back as "the
# blocks hold", or preflight claims the issue on the strength of a check that
# was skipped.
ship_reviewer_reasons() {
  # The stat runs out here and its verdict goes into jq as a list of names, so
  # every reason is worded in one place and comes out in profile order.
  local absent wf inside
  absent='[]'
  while IFS= read -r wf; do
    inside=yes
    # A leading `/` or a `..` component stats true outside the checkout, and the
    # host's run listing takes neither, so such a path is as absent as a name
    # nothing carries rather than a second refusal of its own.
    case "/$wf/" in //*|*/../*) inside=no ;; esac
    [ "$inside" = yes ] && [ -f "$2/$wf" ] ||
      absent=$(jq -c --arg w "$wf" '. + [$w]' <<<"$absent")
  done < <(jq -r 'if type == "array"
                  then .[].workflow | select(. != null and (startswith("native ") | not))
                  else empty end' <<<"$1" 2>/dev/null)
  jq -rn --arg r "$1" --argjson absent "$absent" '
    (try ($r | fromjson) catch null) as $rows
    | if ($rows | type) != "array"
      then "profile invalid: the ## Reviewers blocks could not be parsed"
      else ([$rows[].name]) as $names
        | ([$names[] | . as $n | select(([$names[] | select(. == $n)] | length) > 1)] | unique) as $dups
        | ([$rows[] | . as $r | select($r.fallback_for != null and ($dups | index($r.name)) == null)
            | {key: $r.name, value: $r.fallback_for}] | from_entries) as $fallback
        | $rows[]
        | . as $x
        | (if $x.name == "self-review" or ($x.name | startswith("self-review "))
           then "profile invalid: \($x.name) is a reviewer name the Run file reserves for phase 4'"'"'s self-review lines"
           else empty end),
          (if $x.fallback_for != null and $x.trigger != "on-request"
           then "profile invalid: \($x.name) is Fallback-for: \($x.fallback_for) but its Trigger is \($x.trigger), not on-request"
           else empty end),
          (if $x.fallback_for != null and ($names | index($x.fallback_for)) == null
           then "profile invalid: \($x.name) is Fallback-for: \($x.fallback_for), which ## Reviewers does not list"
           else empty end),
          (if ($x.cap | type) == "string"
           then "profile invalid: \($x.name) has Cap: \($x.cap), which is neither a number nor None."
           else empty end),
          (if ($x.cap | type) == "number" and ($x.cap < 0 or $x.cap != ($x.cap | floor))
           then "profile invalid: \($x.name) has Cap: \($x.cap), which is not a whole number of rounds, 0 or more"
           else empty end),
          (if ($x.gating | type) == "string"
           then "profile invalid: \($x.name) has Gating: \($x.gating), which is neither yes nor no"
           else empty end),
          (if ($dups | index($x.name)) != null then empty
           else (([$x.name | limit(($names | length) + 1; recurse($fallback[.]; . != null))]) as $walk
                 | ($walk[1:] | index($x.name)) as $back
                 | if $back != null
                      and ([$walk[: $back + 1][] | . as $m | $names | index($m)] | min) == ($names | index($x.name))
                   then "profile invalid: reviewers name each other as fallbacks in a cycle: \($walk[: $back + 2] | join(" -> "))"
                   else empty end) end),
          (if $x.trigger == "on-request" and $x.cap == null
           then "profile invalid: \($x.name) is on-request with no Cap:"
           else empty end),
          (if $x.request == "comment"
           then "profile invalid: \($x.name) has Request: comment with no phrase for the transport to post"
           elif (($x.request // "") | startswith("comment "))
           then (if $x.workflow == null
                 then "profile invalid: \($x.name) has Request: \($x.request) with no Workflow: naming the workflow file its round comes from"
                 elif ($x.workflow | startswith("native ")) and $x.workflow != "native codex"
                 then "profile invalid: \($x.name) has Workflow: \($x.workflow), which names no native integration ship reads (codex)"
                 elif ($absent | index($x.workflow)) != null
                 then "profile invalid: \($x.name) has Workflow: \($x.workflow), which is not in the checkout"
                 else empty end)
           elif $x.workflow != null
           then "profile invalid: \($x.name) has Workflow: \($x.workflow) but its Request: is \($x.request // "None."), not comment <phrase>"
           else empty end)
      end'
}

# ship_reviewer_row <rows-json> <name>: the `ship_reviewers` row whose `### `
# heading is <name>, matched exactly, the key `Fallback-for:` and the merge
# summary already use. A name no block carries prints the refusal, listing the
# names the profile does carry, and returns 1: `poll-pr` and `request-review`
# exit 2 on it, because a mistyped name is a malformed invocation.
ship_reviewer_row() {
  jq -ce --arg n "$2" 'first(.[] | select(.name == $n))' <<<"$1" 2>/dev/null && return 0
  jq -rn --argjson r "$1" --arg n "$2" \
    '"no ## Reviewers block is named \($n); the profile names: \(
       if ($r | length) == 0 then "none" else [$r[].name] | join(", ") end)"' 2>/dev/null \
    || printf 'the ## Reviewers blocks could not be parsed\n'
  return 1
}

# ship_reviewer_by_name <name>: `ship_reviewer_row` over the profile in the
# caller's checkout, the one lookup `poll-pr` and `request-review` share. Prints
# the row, or the refusal and returns 1, a missing profile included.
ship_reviewer_by_name() {
  local profile
  profile=$(ship_profile_path) && [ -f "$profile" ] \
    || { printf 'no ship profile at %s; --reviewer reads it\n' "${profile:-docs/agents/ship.md}"; return 1; }
  ship_reviewer_row "$(ship_reviewers "$(cat "$profile")")" "$1"
}
