#!/usr/bin/env bash
# Ship library: the exit shapes, argument handling and labels every mechanic
# shares. Sourced by ../_lib.sh, never run.

# The hidden line every issue or PR comment Ship posts ends with. It is how
# `read-issue` marks a comment `ship: true`: a claim, a hand-back or a PR line
# is told from a human's comment by this line, not by its wording, which a human
# can quote. It sits at the end so a comment-triggered workflow's request phrase
# still opens the body. A mechanic that posts a comment appends it through
# `ship_mark` or `ship_mark_file`, once, here rather than in each adapter. An
# empty span, not an HTML comment: Azure DevOps' work-item sanitizer drops
# `<!-- -->` and unquotes attributes, so this unquoted form is the one both hosts
# hand back byte for byte, and neither renders (probed on the lab, #487).
SHIP_COMMENT_MARKER='<span data-ship=1></span>'
readonly SHIP_COMMENT_MARKER
# ship_mark <body>: the body, then the marker on a line of its own.
ship_mark() {
  local b=$1
  case $b in ''|*$'\n') ;; *) b+=$'\n' ;; esac
  printf '%s%s\n' "$b" "$SHIP_COMMENT_MARKER"
}
# ship_mark_file <src> <dst>: <src>'s bytes then the marker, written to <dst>;
# 1 where <src> cannot be read, with <dst> untouched. The sentinel keeps the
# file's trailing newlines through `$( )`, and `&&` keeps `cat`'s status as the
# substitution's, which a `;` would replace with the sentinel's.
ship_mark_file() {
  local b
  b=$(cat "$1" && printf x) || return 1
  ship_mark "${b%x}" > "$2"
}

# ship_tooling <msg>: the exit-2 shape. Also used when the host adapter itself
# cannot load, so a broken install still emits the contract, not "command not found".
# The message is also written to stderr, as ship_fail does: a caller piping stdout
# through `jq -r .field` reads a malformed call as `null` and exit 0.
ship_tooling() { jq -n --arg e "$1" '{error: $e}'; printf '%s\n' "$1" >&2; exit 2; }
# ship_fail <msg> [<adapter-answer>]: the exit-1 shape. SHIP_BY_HAND, set by
# ship_reach_repo, rides every exit-1 answer as `command`. The message is also
# written to stderr: a caller piping stdout through `jq -r .field` reads a
# refusal as `null` and exit 0, and stderr is what still shows it.
#
# A second argument, even an empty one, marks a host call that failed (a write,
# or a read whose answer named a reason), and adds the HTTP status of its last
# attempt as `status`. Without it a host that is briefly
# down and a payload the host refuses produce the identical verdict, and the run
# has no way to tell them apart: PR #170 spent four minutes bisecting a valid
# body against a burst of 500s. <adapter-answer> is whatever the adapter printed
# on its failure path; an adapter that reports no status, as `az` does, leaves it
# empty and the status is null. A guessed status is worse than none, so anything
# that is not a number reads as null. One argument is a refusal the host never
# saw, which carries no `status` key.
ship_fail() {
  local s=
  if [ $# -ge 2 ]; then
    s=$(jq -r 'if (.status | type) == "number" then .status else "null" end' <<<"$2" 2>/dev/null) || s=null
    [ -n "$s" ] || s=null
  fi
  jq -n --arg e "$1" --arg s "$s" --arg c "${SHIP_BY_HAND:-}" \
    '{error: $e} + (if $s == "" then {} else {status: ($s | fromjson)} end)
     + if $c == "" then {} else {command: $c} end'
  printf '%s\n' "$1" >&2
  exit 1
}

# ship_recorded_grade <issue>: the grade the issue's Run file records, `patch`,
# `minor` or `breaking`, from the `Grade: <word>` line (an optional `- ` before
# it, nothing else before) under its `## Grade` heading (blanks after it allowed),
# the one shape `run-file grade` reads and writes. Prints nothing when there is
# no Run file, no such line or a word that is not one of the three: no recorded
# grade is no check, since a run that never graded has nothing to enforce.
# Returns nonzero when the record root cannot be named or the Run file cannot be
# read: a failed read, not an absent grade.
ship_recorded_grade() { # <issue>
  local root file
  root=$(ship_record_root) || return 2
  file=$root/ship-$1/run.md
  [ -f "$file" ] || return 0
  awk '/^## /{ f = ($0 ~ /^## Grade[ \t\r]*$/) }
    f && /^(- )?Grade: (patch|minor|breaking)[ \t\r]*$/ { sub(/^(- )?Grade: /, ""); sub(/[ \t\r]+$/, ""); print; exit }' "$file"
}

# ship_title_grade <title>: the grade a Conventional-Commit title implies, the
# type before an optional `(scope)` and an optional `!`, then `:`. `!` is
# `breaking`, `feat` is `minor`, every other type is `patch`. A title that is no
# Conventional Commit implies nothing and prints nothing: bump-guard owns that.
ship_title_grade() { # <title>
  local re='^([A-Za-z]+)(\([^)]*\))?(!)?:'
  [[ $1 =~ $re ]] || return 0
  if [ -n "${BASH_REMATCH[3]}" ]; then echo breaking
  elif [ "${BASH_REMATCH[1]}" = feat ]; then echo minor
  else echo patch; fi
}

# ship_require_grade <issue> <title>: exit 1 when the title grades below the
# issue's recorded grade. `Grade: minor` and `Grade: breaking` both need a
# `feat` or a `!` title (a 0.x skill's break is titled feat);
# `Grade: patch` accepts any. Exit 2 when the recorded grade cannot be read,
# which is tooling, not a grade. Run before the push or host write the title
# feeds.
ship_require_grade() { # <issue> <title>
  local want have type
  want=$(ship_recorded_grade "$1") || ship_tooling "cannot read the recorded grade: the record root or the Run file is unreadable"
  case $want in minor|breaking) ;; *) return 0 ;; esac
  have=$(ship_title_grade "$2")
  [ "$have" = patch ] || return 0
  type=${2%%[(!:]*}
  ship_fail "title type $type grades patch, below the recorded Grade: $want; retitle as feat(...)"
}

# ship_help <usage> "$@": the --help contract. A run asks the script what its
# flags and its answer are rather than reading them out of SKILL.md, so it
# prints the same usage string the mechanic's guards print, then the calling
# script's own `# stdout:` header block (from that line up to its `# exit:` line,
# or the end of the header comment), each line with its leading `# ` removed.
# On stdout, exit 0, nothing on stderr. Called on the line after the usage
# assignment, before every other
# guard and before ship_load_host: a guard placed after it answers --help with a
# tooling error wherever the adapter cannot load, which is exactly where someone
# is asking what the flags are. Only the first argument is read, because --help
# behind real arguments is a call the caller meant to make.
ship_help() { # ship_help <usage> "$@"
  local usage=$1; shift
  [ "${1:-}" = --help ] || return 0
  printf '%s\n' "$usage"
  awk '/^# stdout:/ { f = 1 }
    f { if ($0 !~ /^#/ || /^# exit:/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[1]}"
  exit 0
}

# ship_args <usage> <kinds> "$@": the argument check a mechanic that takes a
# positional or a `--body-file` runs on the line after `ship_help` (run-file
# again per verb), so before `ship_load_host` and before any guard of its own.
# <kinds> is one space-separated word list naming the leading positionals in
# order: `issue` and `pr` are a number, `issue|none` a number or the word
# `none`, `arg` any value. Each is required and none may start with `-`: without
# that a flag typed where an id belongs is read as the id and asked of the host.
# Empty <kinds> checks the `--body-file` alone. Every `--body-file` word among
# the rest, even one standing as another flag's value, must be followed by a
# readable regular file; the scan stops at `--`, after which run-file probe's
# words are its command's own. Every failure is `ship_tooling <usage>`; the mechanic
# still assigns its own variables and checks its own flags.
ship_args() { # ship_args <usage> <kinds> "$@"
  local usage=$1 kind v
  local -a kinds
  read -ra kinds <<<"$2"; shift 2
  for kind in ${kinds[@]+"${kinds[@]}"}; do
    v=${1:-}
    case $v in ''|-*) ship_tooling "$usage" ;; esac
    case $kind in
      issue|pr) [[ $v =~ ^[0-9]+$ ]] || ship_tooling "$usage" ;;
      issue\|none) [[ $v =~ ^[0-9]+$ ]] || [ "$v" = none ] || ship_tooling "$usage" ;;
    esac
    shift
  done
  while [ $# -gt 0 ]; do
    [ "$1" = -- ] && break
    if [ "$1" = --body-file ]; then
      [ -f "${2:-}" ] && [ -r "$2" ] || ship_tooling "$usage"
    fi
    shift
  done
}

# ship_flag_value <usage> <value> [<on-empty>]: the guard on the value a flag
# takes, run at the flag's own case arm. A missing, empty or leading-dash value
# is `ship_tooling <usage>`, or `ship_tooling <on-empty>` for an empty one where
# the caller keeps its own wording. A guard that tests only for emptiness reads
# the next flag as the value: `--title --body-file b.md` filed a title.
ship_flag_value() { # ship_flag_value <usage> <value> [<on-empty>]
  case $2 in -*) ship_tooling "$1" ;; esac
  [ -n "$2" ] || ship_tooling "${3:-$1}"
}

# ship_issue_has_label <n> <label>: exit 0 present, 1 absent, 2 unreadable,
# read from `host_issue_get`'s labels[] so no adapter keeps a second read of it.
# A failed read is never a verdict, so a caller confirming a removal tests for
# 1 (`ship_issue_lacks_label`), never for "not 0".
ship_issue_has_label() {
  local issue rc
  issue=$(host_issue_get "$1") || return 2
  jq -e --arg l "$2" 'any(.labels[]; . == $l)' <<<"$issue" >/dev/null; rc=$?
  [ "$rc" -le 1 ] || return 2
  return "$rc"
}
# ship_issue_lacks_label <n> <label>: exit 0 only on a confirmed absent.
ship_issue_lacks_label() { ship_issue_has_label "$1" "$2"; [ $? -eq 1 ]; }

# ship_tail40 <file>: a failing step's evidence, the last 40 lines of the log.
ship_tail40() { tail -n 40 "$1" >&2; }


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
