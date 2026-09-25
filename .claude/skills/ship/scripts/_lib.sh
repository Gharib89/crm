#!/usr/bin/env bash
# Shared helpers for ship's generic mechanics. Sourced by them, and by them
# alone: the file is read into a mechanic's shell and run in no other way, being
# a library rather than a mechanic, answering no command line of its own, and a
# repo's local gate stands on its own. `contract-check.sh` skips it by name for
# that reason.
#
# What a mechanic answers, exit codes, --help, the vocabulary a read comes back
# in and all, is ../reference/mechanics.md. It is the one copy: a second one
# here would be the copy that goes stale.
#
# Host adapter interface. Each mechanic sources exactly one of host/github.sh or
# host/ado.sh, chosen from the origin remote rather than from a flag. An adapter
# defines every function below, in that read vocabulary. An entry marked
# `(fails with {status})` prints {status} on failure where the adapter reports
# one, and the mechanic carries it into its verdict's `status`; `az` reports
# none, so on Azure DevOps that verdict reads `status: null`.
#
# SHIP_HOST_ADAPTER is test-only: set, it names the file ship_load_host sources
# in place of host/$SHIP_HOST.sh; ship_detect_host still runs first. Ship's
# source repo points it at a fixture-driven adapter in its test suite, so a
# mechanic runs as a script without a host. Nothing under skills/ outside this
# file may read it, which that repo's contract gate enforces.
#
#   host_tooling_reasons                 -> one missing-tool reason per line
#   host_tooling_install                 -> install the host CLI where absent; non-zero = could not
#   host_identity                        -> the login the claim is written as
#                                           (fails with {status}, which reaches a verdict only through
#                                           the GitHub writes that open with this read,
#                                           host_pr_comment and host_pr_reply_thread)
#   host_can_push                        -> true | false on GitHub; always unknown on Azure
#                                           DevOps, which has no cheap push probe
#   host_copilot_login                   -> the login `copilot_code_review` governs, or
#                                           nothing on Azure DevOps, which has no Copilot reviewer
#   host_copilot_review_on_push          -> true | false, whether a push to an open PR
#                                           draws a fresh Copilot round on the default
#                                           branch; non-zero and silent where the host
#                                           cannot answer (always, on Azure DevOps, which has
#                                           no such ruleset), which preflight warns on
#   host_issue_get <n>                   -> {number,title,body,state,is_pr,labels[],assignees[],created_at,url}
#   host_issue_comments <n>              -> [{author,body,created_at}]
#   host_issue_blockers_open <n>         -> [n, ...] open blockers; non-zero exit = query unavailable
#   host_issue_linked_prs <n>            -> {closing:[{number,state}],
#                                           mentions:[{number,kind,state}]}
#                                           closing: live PRs whose body closes <n>; on Azure
#                                           DevOps, every live linked PR, since a linked PR
#                                           closes the work item on completion.
#                                           mentions: everything else that names it,
#                                           kind "pr" or "issue"; [] on Azure DevOps,
#                                           which records work-item links rather than
#                                           mentions.
#   host_issue_assign <n> <identity>
#   host_issue_unassign <n> <identity>
#   host_issue_has_label <n> <label>     -> exit 0 when present
#   host_issue_add_label <n> <label>
#   host_issue_remove_label <n> <label>  (no-op success when absent)
#   host_issue_comment <n> <body>        (fails with {status})
#   host_issue_close <n>
#   host_issue_create <title> <body-file> <label> -> {number,url}
#   host_issue_body <n>                  -> {body} the body as markdown. On Azure DevOps, a
#                                           description that is the one `<pre>` block
#                                           host_issue_create writes, unwrapped; any other
#                                           shape fails with {reason}.
#   host_issue_set_body <n> <body-file>  (fails with {status}); on Azure DevOps, written as
#                                           that one `<pre>` block.
#   host_issues_open                     -> [{number,title,url}] every open issue, newest first,
#                                           issues alone. Narrows only on Azure DevOps, where WIQL
#                                           refuses a set past 20000 rows, and says so on stderr
#                                           when it does.
#   host_pr_create <head> <base> <title> <body-file> <issue> -> {number,url,created_at}
#   host_pr_get <pr>                     -> {number,url,title,body,head_sha,head_ref,base_ref,state,mergeable}
#   host_pr_for_branch <branch>          -> {number,state} of the newest PR with that head, or null
#   host_pr_checks <pr> <head_sha>       -> [{name,status}]
#                                           status: pending | success | failure.
#   host_pr_reviews <pr> <head_sha> [<full-ids-json>]
#                                        -> {on_head:[REVIEW],all:[REVIEW],total}
#                                           REVIEW = {id,login,state,submitted_at,body}
#                                           state: approved, changes or comment.
#                                           An Azure DevOps reviewer vote is a state rather than a
#                                           written, timed round, so a vote's row carries id null,
#                                           body "" and submitted_at null.
#                                           Adapters send no `substantive`; poll-pr
#                                           adds it (`SHIP_SUBSTANTIVE`).
#                                           id: what the host knows the round by (a GitHub review,
#                                           an Azure DevOps thread), null for a vote. poll-pr --full
#                                           names ids from here.
#                                           body: the round's text. Phase 7 triages from it.
#                                           Past 2000 chars it is clipped and marked
#                                           "...[truncated]", unless <full-ids-json> names its id;
#                                           "" for a vote.
#                                           all: every round across heads, for poll-pr --since.
#                                           submitted_at: one UTC spelling, or null for a vote, which
#                                           the --since rule therefore cannot admit.
#   host_pr_threads <pr>                 -> [{id,resolved,replied,author,path,body}]; non-zero exit =
#                                           unavailable. replied: this identity has a comment in the
#                                           thread, which is how phase 7 skips a thread it already
#                                           dispositioned in an earlier round.
#                                           GitHub rows also carry comment_id, outdated and url,
#                                           which no mechanic reads. comment_id is the thread's first
#                                           review comment, the REST target GitHub's reply is keyed
#                                           to; the thread id is that same id, as a string.
#                                           outdated: the thread sits on a superseded diff. url:
#                                           that first comment's page. Azure DevOps rows carry none
#                                           of the three: the thread id is that reply target already.
#   host_pr_reviewer_blocked <pr> <login>-> {line, at} | null: that login's latest
#                                           quota or rate-limit notice line, from its review
#                                           bodies or its PR comments, and the UTC time the row
#                                           carrying it was posted. null on Azure DevOps, where
#                                           every comment is a thread that host_pr_reviews already
#                                           carries, so a notice there reaches poll-pr as a review
#                                           row, graded by SHIP_SUBSTANTIVE and refused through
#                                           SHIP_REFUSED_BY.
#   host_workflow_runs <file> <since-iso>-> [{status,conclusion,created_at,url,title}] the runs
#                                           of that workflow file, for the event a comment
#                                           transport starts, created at or
#                                           after <since>. status is the host's own vocabulary,
#                                           of which `completed` is the one word ship tests for:
#                                           every other status is a run still able to deliver,
#                                           conclusion the host's own word or null while it runs,
#                                           title the issue or PR the triggering event sits on,
#                                           which is what narrows the runs to one PR. Non-zero where
#                                           the host could not answer; always non-zero and silent on
#                                           Azure DevOps, which has no such read. poll-pr reports
#                                           either as "unavailable" and holds the window to the
#                                           constant on.
#   host_run_denials <run-url>           -> {denied} the count of tool calls the round in
#                                           that completed run was refused: the leading
#                                           numbers of its jobs' `claude-review` warning
#                                           annotations, summed, 0 where none was raised.
#                                           Non-zero where the host could not answer or a
#                                           warning leads with no number; always non-zero and
#                                           silent on Azure DevOps, which awaits no run.
#                                           poll-pr reports a failure as a `denied` of null
#                                           and leaves the run read standing.
#   host_pr_request_review <pr> <login>  -> {requested,readback[],requested_at}
#                                           readback: the host's own names for the PR's reviewers:
#                                           logins on GitHub, each reviewer's uniqueName and
#                                           displayName in one flat list on Azure DevOps. The adapter
#                                           matches <login> against it for `requested`; request-review
#                                           passes it through and no mechanic reads it.
#                                           requested_at: ISO-8601 time of the request event, or,
#                                           where the host records none (always, on Azure DevOps),
#                                           the wall clock, stamped before the call.
#   host_pr_review_queued <pr> <login> <since-iso>
#                                        -> true | false: true where the host records a request
#                                           event for <login> at or after <since>, or lists it as
#                                           a pending reviewer on the PR now, under any name the
#                                           host records it as. Non-zero and silent where
#                                           the host keeps no such record (always, on Azure DevOps,
#                                           whose reviewer list carries no request time), which
#                                           poll-pr reads as unknown and leaves the window to run.
#   host_pr_comment <pr> <body-file>     -> {id,url,created_at}
#                                           (fails with {status})
#                                           id: the comment's id on GitHub, the thread's id on
#                                           Azure DevOps, where a PR comment is a thread of its own.
#                                           created_at: the host's own creation time for the
#                                           comment, one UTC spelling, or null where the host
#                                           records none, the same way submitted_at is null for
#                                           a vote. `request-review`'s comment transport
#                                           reports it as `requested_at` and answers the null
#                                           with a wall clock read before the post, so --since
#                                           always has a bound to compare against.
#   host_pr_set_body <pr> <body-file>    (fails with {status})
#   host_pr_set_title <pr> <title>       (fails with {status})
#   host_pr_reply_thread <pr> <thread> <body-file> -> {replied,url}
#                                           (fails with {status})
#                                           A reply inside the thread, leaving its status alone.
#   host_pr_resolve_thread <pr> <thread> -> {resolved}
#                                           A failure may print {resolved,detail}, resolved false,
#                                           which resolve-thread reports as its error ("no such thread").
#   host_pr_merge <pr> <subject>         -> exit 0 once the host reports merged
#   host_prs_open                        -> [{number,title,head_ref,author,url,created_at}]
#   host_issues_ready <label>            -> [{number,title,created_at}] oldest first, unassigned, not PRs

SHIP_SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly SHIP_SCRIPTS
# shellcheck disable=SC2034  # read by the mechanics that source this library
readonly SHIP_CLAIM_COMMENT='🤖 Claimed by a ship run: implementation in progress.'

# ship_tooling <msg>: the exit-2 shape. Also used when the host adapter itself
# cannot load, so a broken install still emits the contract, not "command not found".
ship_tooling() { jq -n --arg e "$1" '{error: $e}'; exit 2; }
ship_fail()    { jq -n --arg e "$1" '{error: $e}'; exit 1; }

# ship_fail_host <msg> <adapter-answer>: the exit-1 shape for a host write that
# failed, carrying the HTTP status of the last attempt. Without it a host that
# is briefly down and a payload the host refuses produce the identical verdict,
# and the run has no way to tell them apart: PR #170 spent four minutes
# bisecting a valid body against a burst of 500s. <adapter-answer> is whatever
# the adapter printed on its failure path; an adapter that reports no status,
# as `az` does, leaves it empty and the status is null. A guessed status is
# worse than none, so anything that is not a number reads as null.
ship_fail_host() { # ship_fail_host <msg> <adapter-answer>
  local s
  s=$(jq -r 'if (.status | type) == "number" then .status else "null" end' <<<"${2:-}" 2>/dev/null) || s=null
  [ -n "$s" ] || s=null
  jq -n --arg e "$1" --argjson s "$s" '{error: $e, status: $s}'
  exit 1
}

# ship_help <usage> "$@": the --help contract. A run asks the script what its
# flags are rather than reading them out of SKILL.md, so the answer is the same
# usage string the mechanic's guards print, on stdout, exit 0, nothing on
# stderr. Called on the line after the usage assignment, before every other
# guard and before ship_load_host: a guard placed after it answers --help with a
# tooling error wherever the adapter cannot load, which is exactly where someone
# is asking what the flags are. Only the first argument is read, because --help
# behind real arguments is a call the caller meant to make.
ship_help() { # ship_help <usage> "$@"
  local usage=$1; shift
  [ "${1:-}" = --help ] || return 0
  printf '%s\n' "$usage"
  exit 0
}

# ship_tail40 <file>: a failing step's evidence, the last 40 lines of the log.
ship_tail40() { tail -n 40 "$1" >&2; }

# Branch convention: <type>/<slug>-<issue>. The "-<issue>" suffix is what
# preflight greps for on the remote.
ship_branch()           { printf '%s/%s-%s' "$1" "$2" "$3"; }
ship_branch_suffix_re() { printf -- '-%s$' "$1"; }

# The main checkout, even when run from inside a worktree: --git-common-dir
# points at the primary .git, so a run started in a worktree lands the new one
# beside it.
ship_main_checkout() {
  local common
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  dirname "$common"
}

# Sibling container beside the checkout: <parent>/<repo>.worktrees
ship_worktree_container() {
  local root; root=$(ship_main_checkout) || return 1
  printf '%s/%s.worktrees' "$(dirname "$root")" "$(basename "$root")"
}

# The base ref, resolved from origin/HEAD rather than a hardcoded branch (ADO
# defaults vary). Refreshes the symbolic ref where the clone lacks one.
ship_base_ref() {
  local ref
  ref=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) \
    || { git remote set-head origin -a >/dev/null 2>&1 \
         && ref=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null); } \
    || return 1
  printf '%s' "${ref#refs/remotes/}"
}
ship_base_branch() { local b; b=$(ship_base_ref) || return 1; printf '%s' "${b#origin/}"; }

# Host detection from the origin remote. Sets SHIP_HOST and the host's
# identifiers; no --host flag and no env var, because every Bash call is a fresh
# shell and a per-call flag is one more thing prose can get wrong.
ship_detect_host() {
  local url
  url=$(git remote get-url origin 2>/dev/null) || return 1
  case $url in
    *github.com[:/]*)
      SHIP_HOST=github
      local p=${url#*github.com}; p=${p#[:/]}; p=${p%.git}; p=${p%/}
      SHIP_OWNER=${p%%/*}; SHIP_REPO=${p#*/}
      SHIP_REPO_SLUG="$SHIP_OWNER/$SHIP_REPO" ;;
    *dev.azure.com/*)
      # https://[user@]dev.azure.com/<org>/<project>/_git/<repo>
      SHIP_HOST=ado
      local p=${url#*dev.azure.com/}; p=${p%.git}
      SHIP_ORG=${p%%/*}; p=${p#*/}
      SHIP_PROJECT=${p%%/_git/*}; SHIP_REPO=${p##*/_git/}
      SHIP_ORG_URL="https://dev.azure.com/$SHIP_ORG"
      SHIP_REPO_SLUG="$SHIP_ORG/$SHIP_PROJECT/$SHIP_REPO" ;;
    *@vs-ssh.visualstudio.com:v3/*)
      # git@ssh.dev.azure.com:v3/<org>/<project>/<repo> and the visualstudio.com twin
      SHIP_HOST=ado
      local p=${url#*:v3/}
      SHIP_ORG=${p%%/*}; p=${p#*/}
      SHIP_PROJECT=${p%%/*}; SHIP_REPO=${p#*/}
      SHIP_ORG_URL="https://dev.azure.com/$SHIP_ORG"
      SHIP_REPO_SLUG="$SHIP_ORG/$SHIP_PROJECT/$SHIP_REPO" ;;
    *ssh.dev.azure.com:v3/*)
      SHIP_HOST=ado
      local p=${url#*:v3/}
      SHIP_ORG=${p%%/*}; p=${p#*/}
      SHIP_PROJECT=${p%%/*}; SHIP_REPO=${p#*/}
      SHIP_ORG_URL="https://dev.azure.com/$SHIP_ORG"
      SHIP_REPO_SLUG="$SHIP_ORG/$SHIP_PROJECT/$SHIP_REPO" ;;
    *.visualstudio.com/*)
      # https://<org>.visualstudio.com/<project>/_git/<repo> (or DefaultCollection/)
      SHIP_HOST=ado
      local hostpart=${url#*://}; hostpart=${hostpart#*@}
      SHIP_ORG=${hostpart%%.visualstudio.com*}
      local p=${url#*.visualstudio.com/}; p=${p#DefaultCollection/}; p=${p%.git}
      SHIP_PROJECT=${p%%/_git/*}; SHIP_REPO=${p##*/_git/}
      SHIP_ORG_URL="https://dev.azure.com/$SHIP_ORG"
      SHIP_REPO_SLUG="$SHIP_ORG/$SHIP_PROJECT/$SHIP_REPO" ;;
    *) return 1 ;;
  esac
  SHIP_PROJECT=$(printf '%s' "${SHIP_PROJECT:-}" | sed 's/%20/ /g')
  export SHIP_HOST SHIP_OWNER SHIP_REPO SHIP_REPO_SLUG SHIP_ORG SHIP_PROJECT SHIP_ORG_URL
}

# ship_load_host: detect and source the adapter, or exit 2 with the contract.
ship_load_host() {
  ship_detect_host || ship_tooling "cannot derive the host from the origin remote"
  # shellcheck source=/dev/null
  source "${SHIP_HOST_ADAPTER:-$SHIP_SCRIPTS/host/$SHIP_HOST.sh}" \
    || ship_tooling "cannot load host adapter ${SHIP_HOST_ADAPTER:-$SHIP_HOST}"
}

# Triage roles are canonical names; the label strings a repo actually uses live
# in the role table docs/agents/triage-labels.md opens with. The read stops at
# the first `## ` heading, where `setup-skills` puts `## Dimension labels`: a
# dimension row is keyed by label name, so one spelled like a role never
# resolves as one. Falls back to the canonical name when file or row is missing.
ship_triage_label() {
  local role=$1 root file label
  root=$(ship_main_checkout) || { printf '%s' "$role"; return; }
  file="$root/docs/agents/triage-labels.md"
  label=$(sed '/^## /q' "$file" 2>/dev/null | grep -E "^\| *\`$role\` *\|" | head -1 \
    | awk -F'|' '{print $3}' | tr -d '` ')
  printf '%s' "${label:-$role}"
}

# ship_frontmatter <file> <key>: one `  <key>: <value>` line from a skill's
# YAML frontmatter, value only. Stops at the closing `---`, so a body line that
# looks like the key is body text.
ship_frontmatter() {
  awk -v k="  $2:" 'NR>1 && /^---$/{exit} index($0, k) == 1 {sub(/^[^:]*: */, ""); print; exit}' "$1"
}

# ship_missing_skill_reasons <root> <composes>: the skills ship loads through
# the Skill tool, checked against a checkout before the claim. <composes> is
# ship's `metadata.composes` line: space-separated `<source-repo>:<skill>`
# entries, the single place the list lives. Prints one reason per skill whose
# `<root>/.claude/skills/<skill>/SKILL.md` is absent, carrying the line that
# installs it; prints nothing when every one is there.
#
# Only the consumer repo's own `.claude/skills` counts: a global copy under
# ~/.claude/skills is a personal skill rather than this repo's derived copy, per
# setup-skills.
ship_missing_skill_reasons() {
  local root=$1 entry source skill
  local -a entries
  # read -ra, not an unquoted expansion: the split on spaces is intentional and
  # explicit, and a glob character in an entry stays a literal character.
  read -ra entries <<<"$2"
  for entry in ${entries[@]+"${entries[@]}"}; do
    source=${entry%%:*}; skill=${entry##*:}
    [ -f "$root/.claude/skills/$skill/SKILL.md" ] && continue
    printf 'skill missing: %s; run npx skills add %s --skill %s --agent claude-code -y\n' \
      "$skill" "$source" "$skill"
  done
}

# The one fence rule every transformation here reads: ship_body_replace_section
# below, ship_body_closes under it, and _gh_add_closes in the GitHub adapter. A `## ` heading inside a fence
# is example text, so the two have to agree on where a fence starts and ends, or
# one rewrites the example and leaves the real section alone.
#
# CommonMark, as far as these two need it: an opening fence is three or more
# backticks or tildes under up to three leading spaces, and only a bare run of
# the same character at least as long closes it. The character rules a tilde
# fence in; the length keeps a ``` line inside a ```` fence from closing it and
# inverting the state for the rest of the body; the bareness keeps a ```js line
# inside a ``` fence from doing the same, since a closing fence carries no info
# string. A backtick opener carrying another backtick after its run is not a
# fence at all but paragraph text, which is the one asymmetry with tildes.
# Indented (four space) code blocks are not a fence form here.
#
# `ship_fence(line)` returns the in-fence state after the line: a fence line
# reads as fenced when it opens one and unfenced when it closes one. Prepend it
# to an awk program and call it once per line, before any heading test. It
# holds its state for the length of the input in the globals `_fenced`,
# `_fence_char` and `_fence_len`, so a host program leaves those three names to
# it, and reads no `RSTART` or `RLENGTH` of its own across a call, which
# `ship_deindent`'s `match` overwrites.
#
# `ship_deindent(s)` strips up to three leading spaces. It is a `match` because
# mawk, the default awk on Debian and Ubuntu, reads `sub(/^ ? ? ?/, ...)` as one
# optional space.
readonly SHIP_AWK_FENCE='function ship_deindent(s) {
    if (match(s, /^ +/)) s = substr(s, (RLENGTH < 3 ? RLENGTH : 3) + 1)
    return s
  }
  function ship_fence(line,   s, c, n) {
    s = ship_deindent(line); c = substr(s, 1, 1)
    if (c != "`" && c != "~") return _fenced
    n = 0; while (substr(s, n + 1, 1) == c) n++
    if (n < 3) return _fenced
    if (!_fenced) {
      if (c == "`" && index(substr(s, n + 1), "`")) return _fenced
      _fenced = 1; _fence_char = c; _fence_len = n
    }
    else if (c == _fence_char && n >= _fence_len && substr(s, n + 1) ~ /^[ \t\r]*$/) _fenced = 0
    return _fenced
  }
'

# The closing-keyword vocabulary, read by both closing tests below so a keyword
# one recognizes is a keyword the other does. It ends on the `#` of the issue
# number: `ship_body_closes` anchors the one number it was asked about to it,
# `ship_body_closing_line` takes any number, since it asks whether a line closes
# anything at all. The "(#n, and #m)" run lets a multi-issue "Closes #75, #81"
# count for #81.
readonly SHIP_CLOSES_RE='\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing))\s+(#[0-9]+[\s,]+(and[\s,]+)?)*#'

# ship_unfenced <text>: <text> with every fenced block taken out, opener and
# closer included, by SHIP_AWK_FENCE. What is left is the text a closing test
# reads: a "Closes #n" inside a fence is example text, not a claim.
ship_unfenced() { # ship_unfenced <text>
  awk "$SHIP_AWK_FENCE"'
    { was = fenced; fenced = ship_fence($0); if (!was && !fenced) print }' <<<"$1"
}

# Closing-keyword test, the same on both hosts: does <body> claim to close
# <issue>? Fenced blocks and inline code come out first (a PR quoting
# "Closes #n" while discussing another PR mentions the issue, it does not claim
# it).
#
# The fenced blocks come out by SHIP_AWK_FENCE, so this agrees with the two
# heading transformations on what a fence is: a tilde-fenced example carrying
# "Closes #n" reads as a mention here too, and host_pr_create, which asks this
# before it places a closing line, still places one on a body that only mentions
# the issue.
#
# Code spans come out after, and stay in jq, because a span opens mid-line where
# a line-oriented pass cannot see it. One rule for every run length: a span
# closes on a backtick run as long as the one that opened it, which the
# backreference says directly, and a multi-line ```md span is that rule at
# length three rather than a fence form of its own.
ship_body_closes() { # ship_body_closes <body> <issue> -> exit 0 when it does
  local unfenced
  unfenced=$(ship_unfenced "$1")
  jq -e -n --arg body "$unfenced" --arg n "$2" --arg re "$SHIP_CLOSES_RE" '
    $body
    | gsub("(?s)(`+).*?\\1"; "")
    | test($re + $n + "\\b"; "i")' >/dev/null
}

# ship_body_closing_line <text>: the first line of <text> that claims to close
# an issue, any issue, or nothing where no line does. The same vocabulary and
# the same fence and code-span rules as ship_body_closes, answering with the
# line itself: `ship_body_replace_preamble` carries the line over rather than
# rebuilding it, so it needs the line and not a yes or no.
#
# The spans come out of the whole text at once, exactly as ship_body_closes
# takes them out, so a multi-line span is inert for both. Each span leaves its
# own newlines behind, which keeps the stripped text line for line with the
# original: the test reads the stripped line and the answer is the original one,
# backticks and all. Stripping per line instead would read a line inside a
# multi-line span as a claim, and carrying that line over lifts a quoted
# `Closes #n` out of its span and makes it a real one.
ship_body_closing_line() { # ship_body_closing_line <text>
  local unfenced
  unfenced=$(ship_unfenced "$1")
  jq -rn --arg body "$unfenced" --arg re "$SHIP_CLOSES_RE" '
    ($body | split("\n")) as $lines
    | ($body | gsub("(?s)(?<b>`+)(?<c>.*?)\\1"; (.c | gsub("[^\n]"; "")))
       | split("\n")) as $bare
    | first(range($lines | length)
            | select($bare[.] | test($re + "[0-9]+\\b"; "i")))
    | $lines[.] // empty'
}

# Replace one `## <section>` of <body> with <body-file>'s content, appending the
# section when the body has none. Every other line is untouched, including a
# `Closes` line above the first heading. Prints the new body; exit 0 replaced,
# 1 created, the way ship_body_closes answers with its exit code.
#
# The heading match is anchored at column 0 and skips fenced blocks by
# SHIP_AWK_FENCE, the rule _gh_add_closes reads too, so the two agree on what a
# section boundary is. It compares the line to `## <section>` LITERALLY rather
# than building an ERE around the name: `--section` takes any name, and a `.` or
# a `+` in one would otherwise match a heading nobody asked for, silently
# rewriting the wrong section of a PR body. Both sides of that comparison have
# their trailing blanks trimmed, the name included, so a `--section 'Review '`
# still matches `## Review` and the heading this appends is the one the next
# write will match.
#
# The name reaches awk through the environment rather than `-v`, which decodes
# backslash escapes in its value: a `--section 'Review\name'` arrived as two
# lines, so the real section was left alone and a mangled heading appended.
#
# The body file carries the section's CONTENT. A file that opens with the
# section's own heading, and the blank line under it, has both dropped rather
# than printed under the heading this writes: two `## <section>` lines make a
# body no mechanic can repair afterwards, because both of them match here and a
# later clean write puts the content under each. Any other opening line is
# content, `## <other>` included.
#
# A repeat met while the section just placed is still open is swallowed for the
# same reason, which is what repairs a body already carrying the duplicate. A
# `## <section>` after a DIFFERENT heading has closed that state is a section of
# its own and is replaced, so a malformed body with the section twice over still
# gets both.
#
# The strip trims a trailing CR before that comparison where the boundary match
# does not, on purpose: they read different inputs. A CRLF heading left in the
# file becomes a second `## <section>` in the body that the boundary match misses
# and rule 3 then treats as the section's end, which is a body no later
# write can repair. The boundary match reads the body the host returns and is the
# pre-existing rule `_gh_add_closes` agrees with.
ship_body_replace_section() { # ship_body_replace_section <body> <section> <body-file>
  SHIP_SECTION="$2" awk -v file="$3" "$SHIP_AWK_FENCE"'
    function trimmed(line, cr) {
      if (cr) sub(/[ \t\r]*$/, "", line); else sub(/[ \t]*$/, "", line)
      return line
    }
    function dump(   n, i, line, start, buf) {
      n = 0
      while ((getline line < file) > 0) { n++; buf[n] = line }
      close(file)
      start = 1
      if (n >= 1 && trimmed(buf[1], 1) == hd) {
        start = 2
        if (n >= 2 && buf[2] ~ /^[ \t\r]*$/) start = 3
      }
      for (i = start; i <= n; i++) print buf[i]
    }
    BEGIN { hd = trimmed("## " ENVIRON["SHIP_SECTION"], 1) }
    { fenced = ship_fence($0) }
    !fenced && trimmed($0, 0) == hd {
      if (skip) next
      print; print ""; dump(); print ""; skip=1; placed=1; next }
    skip && !fenced && /^## / { skip=0 }
    !skip { print }
    END { if (!placed) { printf "\n%s\n\n", hd; dump(); exit 1 } }' <<<"$1"
}

# Replace the PREAMBLE of <body>, everything above its first `## ` heading, with
# <body-file>'s content, and print the new body. A body with no heading is all
# preamble. The preamble is where `open-pr` puts the closing line, and under
# this repo's standard it holds nothing else. Nothing under scripts/ could
# rewrite that half before #173, so an accepted body-shape finding in phase 7 was
# reported and left standing; this is what makes it a fix like any other.
#
# The boundary is the same column-0 `^## ` outside a fence that
# ship_body_replace_section and _gh_add_closes read, so the two halves of a body
# meet exactly and neither can reach into the other.
#
# Unlike a section, a preamble is always present: a body that opens on its first
# heading has an empty one, and the content is placed above that heading. So
# there is no created case and no exit-1 answer.
#
# A closing line the OLD preamble carried and the new content does not is
# carried over, last, where `open-pr` puts it. `open-pr` places it above the
# first heading precisely so no section rewrite reaches it; this rewrite does
# reach it, and a rewrite that says nothing about closing should not drop the
# link the PR was opened with.
#
# Content that carries a closing line of its own is left exactly as written,
# whichever issues that line names. The test is per line, not per issue number:
# the line is the caller saying what this PR closes, and merging the old line's
# references into it would put back an issue they had just taken out, which the
# caller cannot see in the file they wrote.
#
# The content reaches awk through the environment rather than a file, because
# the carried line is appended to it first, and rather than `-v`, which decodes
# backslash escapes in its value the way it did to a `--section` name.
ship_body_replace_preamble() { # ship_body_replace_preamble <body> <body-file>
  local content carried pre
  content=$(cat "$2")
  pre=$(awk "$SHIP_AWK_FENCE"'
    { fenced = ship_fence($0) }
    !fenced && /^## / { exit }
    { print }' <<<"$1")
  carried=$(ship_body_closing_line "$pre")
  if [ -n "$carried" ] && [ -z "$(ship_body_closing_line "$content")" ]; then
    if [ -n "$content" ]; then content=$(printf '%s\n\n%s' "$content" "$carried")
    else content=$carried
    fi
  fi
  SHIP_PREAMBLE="$content" awk "$SHIP_AWK_FENCE"'
    function dump(   n, i, a) {
      n = split(ENVIRON["SHIP_PREAMBLE"], a, "\n")
      # Trailing blanks come off, so the one blank line under the preamble is
      # placed here however the content file ended.
      while (n > 0 && a[n] ~ /^[ \t\r]*$/) n--
      for (i = 1; i <= n; i++) print a[i]
      return n
    }
    { fenced = ship_fence($0) }
    !placed && !fenced && /^## / { if (dump() > 0) print ""; placed = 1 }
    placed { print }
    END { if (!placed) dump() }' <<<"$1"
}

# Generic English function words of four or more characters; shorter ones the
# length rule already drops. No repo-specific word belongs here: the candidate
# check is generic, and an over-eager candidate costs one `--distinct-from`
# while a missed one costs a second issue for a find already filed.
readonly SHIP_TITLE_STOPWORDS='about also been both does each else from have here into just like made make more most much must only over same some such than that their them then there these they this those very were what when where which while will with would your'

# Titles that look like <title> among <open-issues>, for file-issue's candidate
# check. Compares lowercased tokens with punctuation as a separator, dropping
# tokens under four characters and the stopwords above; three or more shared
# tokens is a candidate. <exclude> is the JSON array of numbers the caller has
# read and judged different. Prints the candidates as compact JSON.
ship_title_candidates() { # ship_title_candidates <title> <open-issues-json> <exclude-json>
  jq -c --arg t "$1" --argjson x "$3" --arg s "$SHIP_TITLE_STOPWORDS" '
    def tokens: ascii_downcase | [splits("[^a-z0-9]+")]
      | map(select(length >= 4)) | unique | . - ($s | split(" "));
    ($t | tokens) as $new
    | [ .[]
        | select(([.number] - $x) != [])
        | select((($new - ($new - (.title | tokens))) | length) >= 3)
        | {number, title, url} ]' <<<"$2"
}

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
# The cut leaves a marker, so a clipped round reads as clipped.
# `$id | tostring` so a row the host gives no id (an Azure DevOps vote) compares
# without erroring; such a row carries no body to unclip.
# shellcheck disable=SC2034  # read by the host adapters that source this library
readonly SHIP_REVIEW_CLIP='def clip($id):
  if ($full | index($id | tostring)) then .
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

# poll-pr's two landing rules over a `host_pr_reviews` projection, invoked with
# `--arg l <normalised login>` and `--arg s <since|"">`. `$l` arrives already
# lowercased and stripped of a `[bot]` suffix, the row side normalised here to
# match. Only a SUBSTANTIVE row lands, as `SHIP_SUBSTANTIVE` graded it, which is
# what keeps a quota notice from answering for a round that has yet to arrive
# (#155).
# shellcheck disable=SC2034  # read by poll-pr
readonly SHIP_LANDED_BY='
  def mine: [.[] | select(.substantive and ((.login | ascii_downcase | sub("\\[bot\\]$"; "")) == $l))];
  if $s == "" then (if (.on_head | mine) != [] then "head" else null end)
  else (if (.all | mine | map(select(.submitted_at != null and .submitted_at >= $s))) != [] then "since" else null end)
  end'

# poll-pr's refusal test, over the same projection and the same two arguments:
# the rule that admitted a NOTICE row by that login, a row with a body that is
# not substantive, which is the one kind `is_notice` leaves. A quota notice
# answers the request it follows, and no round is coming after it: Copilot's
# quota is the requesting user's and monthly, so waiting the window out, or
# asking again, buys nothing (#248, #250: every poll spent its whole window on
# a refusal already posted). Read only when no round landed, so a round that
# follows a notice still lands. A notice posted as a PR comment rather than as a
# review leaves no row here: poll-pr admits it from `host_pr_reviewer_blocked`'s
# `at`, under the since rule only (#256).
# shellcheck disable=SC2034  # read by poll-pr
readonly SHIP_REFUSED_BY='
  def refusals: [.[] | select((.substantive | not) and (.body // "") != ""
                              and ((.login | ascii_downcase | sub("\\[bot\\]$"; "")) == $l))];
  if $s == "" then (if (.on_head | refusals) != [] then "head" else null end)
  else (if (.all | refusals | map(select(.submitted_at != null and .submitted_at >= $s))) != [] then "since" else null end)
  end'

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
# no run at all, which the review loop reads as never-queued. `denied` is null
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

# ship_fence_unclosed <text>: does the text end inside a fenced block? Prints
# `line <n>: <run>` naming the opener still open, or nothing when the
# fence state is balanced. `update-pr-body` asks before it rewrites a section:
# an open fence inverts the in-fence state for the rest of the body, so every
# `## ` heading after it reads as example text and the rewrite swallows the
# sections between them (run #121 lost four that way).
ship_fence_unclosed() {
  awk "$SHIP_AWK_FENCE"'
    { was = fenced; fenced = ship_fence($0)
      if (!was && fenced) {
        open_line = NR; open_run = $0
        open_run = ship_deindent(open_run); sub(/[^`~].*$/, "", open_run)
      } }
    END { if (fenced) printf "line %d: %s\n", open_line, open_run }' <<<"$1"
}

# ship_body_headings <body>: the body's `## ` section headings, heading text
# only, one per line, in order. The same fence rule as every transformation
# above, so a `## ` inside a fence is example text here too. `update-pr-body`
# reports this after the write, where a swallowed section is visible in the JSON
# rather than eight minutes later in a review.
ship_body_headings() {
  awk "$SHIP_AWK_FENCE"'
    { fenced = ship_fence($0) }
    !fenced && /^## / { sub(/^## /, ""); sub(/[ \t\r]+$/, ""); print }' <<<"$1"
}

# ship_profile_path: the ship profile in the checkout the caller runs in, rather
# than the main one: a run inside a worktree is governed by the profile on its
# own branch, and a repo's first profile lands on a branch before it ever
# reaches main. Fails outside a checkout. Whether the file is there is the
# caller's own question, because the two that ask it answer differently: for
# preflight an absent profile is a stop, and for `ci-wait` it is the default
# grace standing.
ship_profile_path() {
  local here
  here=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
  printf '%s/docs/agents/ship.md' "$here"
}

# ship_no_checks_expected <profile-body>: true when the profile's `## CI` block
# says both that no leg runs on a PR (`Legs: None.`) and that an empty check list
# is legal (`No-checks legal: yes`). That pair answers, up front, the question
# `ci-wait`'s no-checks grace exists to ask, so a run in such a repo waits
# nothing instead of two minutes for a check the profile says never comes
# (#218). Either fact alone keeps the grace: a repo with no legs that still
# calls an empty list illegal is waiting for a leg nobody named, and a repo with
# a leg has a check coming whatever the second line says.
#
# Read inside `## CI` and nowhere else, the way every profile fact is read from
# its own section: `Legs:` is that section's word, and a line of the same shape
# under another heading is prose. Both values are matched as the profile spells
# them, lower case: one function reading its own section two ways is the drift
# a second spelling starts.
ship_no_checks_expected() { # <profile-body>
  local ci
  ci=$(awk '/^## /{f = ($0 ~ /^## CI[ \t\r]*$/)} f' <<<"$1")
  grep -Eq '^Legs:[[:space:]]*None\.[[:space:]]*$' <<<"$ci" || return 1
  grep -Eq '^No-checks legal:[[:space:]]*yes([^[:alnum:]]|$)' <<<"$ci" || return 1
  return 0
}

# ship_reviewers <profile-body>: the `## Reviewers` section as one JSON row per
# reviewer, in profile order, each
# {name, login, trigger, request, workflow, cap, resolve, gating, fallback_for,
# instructions}.
# `None.` reads as null, `Cap:` as a number, `Gating:` as a boolean. A `Cap:` that
# is neither a number nor `None.` comes back as the raw string, so
# `ship_reviewer_reasons` can refuse it rather than read a typo as "uncapped".
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
      elif $k == "gating" then $v == "yes"
      else $v end;
    reduce (splits("\n") | select(. != "") | split("\t")) as $p ([];
      if $p[0] == "n"
      then . + [{name: $p[1], login: null, trigger: null, request: null,
                 workflow: null, cap: null, resolve: null, gating: false,
                 fallback_for: null, instructions: null}]
      else (($p[1] | ascii_downcase | sub("-"; "_")) as $k
            | .[length - 1] += {($k): (if $k == "gating" then $p[2] == "yes"
                                       else norm($k; $p[2]) end)})
      end)'
}

# ship_reviewer_reasons <rows-json> <checkout-root>: one `profile invalid:
# <detail>` line per reviewer-block fault, or nothing when the blocks hold.
# Preflight's only reviewer check, so the faults are refused in both lanes and
# before the claim, rather than at the phase that would have driven the reviewer.
#
# A fallback that is not on-request cannot be withheld, and withholding it until
# its primary degrades is the whole point of a fallback; one naming a reviewer
# nobody listed has no primary to stand in for, an on-request reviewer with no
# cap has no bound on its loop, and a `Cap:` that is not a number is a typo that
# would read as an uncapped one.
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
                  then .[].workflow | select(. != null)
                  else empty end' <<<"$1" 2>/dev/null)
  jq -rn --arg r "$1" --argjson absent "$absent" '
    (try ($r | fromjson) catch null) as $rows
    | if ($rows | type) != "array"
      then "profile invalid: the ## Reviewers blocks could not be parsed"
      else ([$rows[].name]) as $names
        | $rows[]
        | . as $x
        | (if $x.fallback_for != null and $x.trigger != "on-request"
           then "profile invalid: \($x.name) is Fallback-for: \($x.fallback_for) but its Trigger is \($x.trigger), not on-request"
           else empty end),
          (if $x.fallback_for != null and ($names | index($x.fallback_for)) == null
           then "profile invalid: \($x.name) is Fallback-for: \($x.fallback_for), which ## Reviewers does not list"
           else empty end),
          (if ($x.cap | type) == "string"
           then "profile invalid: \($x.name) has Cap: \($x.cap), which is neither a number nor None."
           else empty end),
          (if $x.trigger == "on-request" and $x.cap == null
           then "profile invalid: \($x.name) is on-request with no Cap:"
           else empty end),
          (if $x.request == "comment"
           then "profile invalid: \($x.name) has Request: comment with no phrase for the transport to post"
           elif (($x.request // "") | startswith("comment "))
           then (if $x.workflow == null
                 then "profile invalid: \($x.name) has Request: \($x.request) with no Workflow: naming the workflow file its round comes from"
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
# `Workflow:`, the run that separates a round still being written from one that
# will not come; the run read is keyed by the --since instant, which a head-rule
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
# `never-queued`; a caller expecting one passes `--timeout`.
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

# ship_pr_state_reason <state>: the refusal `merge` answers with when the PR is
# not one a human can still say "merge" about, or nothing when it is. Both
# adapters normalise to `open`, `merged` or `closed` (Azure DevOps maps
# `abandoned` to `closed`), and GitHub's merge endpoint accepts a closed PR, so
# without this a PR somebody deliberately closed is squashed onto the base by a
# mechanic whose whole contract is that a human said "merge" about THIS PR.
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
# rows of their own, and a convergence test that counts them reads its own voice
# as the reviewer's. An empty <identity> drops nothing: a host that could not
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
# was. The string "unavailable" passes through as itself. `reviewer_run` passes
# through whole, the string "unavailable" included: it is four fields, and a
# loop reading rounds from the brief is the loop that has to tell a silent
# reviewer from one whose run is still going.
ship_brief() {
  jq -c --arg me "$2" --arg key "$3" --argjson full "${4:-[]}" '
    def norm: ascii_downcase | sub("\\[bot\\]$"; "");
    def by($l): ((.login // "") | norm) == ($l | norm);
    def mine: $me != "" and by($me);
    def awaited($l): $l == "" or by($l);
    def clip: if length > 200 then .[0:200] + "\n...[truncated]" else . end;
    def finding_items:
      (if endswith("\n...[truncated]") then "\n...[truncated]" else "" end) as $mark
      | [splits("\n") | select(test("^[ \t]*$") | not)] as $lines
      | [$lines[] | select(test("^[ \t]*([-*+]|[0-9]+[.)])[ \t]"))] as $items
      | if ($items | length) == 0 then clip
        elif $lines[0] == $items[0] then (($items | join("\n")) + $mark)
        else ((([$lines[0]] + $items) | join("\n")) + $mark) end;
    def lead: [splits("\n") | select(test("^[ \t]*$") | not)] | (.[0] // "") | clip;
    (.reviewer.login // "") as $await
    | {head_sha, mergeable, reviewer, landed_by, refused_by, never_queued, degraded, reviewer_blocked, reviewer_run,
     rounds: [.reviews[$key][] | select((mine | not) and awaited($await)) | . as $r
              | {id, submitted_at, substantive,
                 body: (if ($full | index($r.id | tostring)) then $r.body
                        else ($r.body | finding_items) end)}],
     threads: (if (.threads | type) == "array"
               then [.threads[] | select(.resolved | not)
                     | {id, path, lead: ((.body // "") | lead), resolved, replied}]
               else .threads end)}' <<<"$1"
}
