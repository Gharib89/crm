#!/usr/bin/env bash
# The Run file: a ship run's record, outside the repo, in the session's
# scratchpad. This mechanic owns the file's shape and every flip of it, so a
# stamp is a measurement the mechanic took rather than a time a run recalled,
# and the merge summary's `Timing:` row is arithmetic rather than mental
# subtraction.
#
#   run-file init <issue|slug> --scratchpad <dir> [--rebuild]
#                [--state <n>=open|done|done:<HH:MM→HH:MM>|skipped:<reason>]...
#                [--tripwires <t>] [--verifications <v>] [--reviewers <r>] [--legs <l>]
#   run-file open|close <n> <where>
#   run-file skip <n> <reason> <where>
#   run-file timing <where>
#
# where <where> is `--file <path>`, or `--issue <n|slug> [--scratchpad <dir>]`
# for the layout `init` wrote, `<scratchpad>/ship-<issue>/run.md`. A left-out
# `--scratchpad` is `$TMPDIR` or `/tmp`, which is where a run whose harness named
# no scratchpad put the record; a run whose harness named one passes it, the same
# directory it passed `init`. `init` owns
# that layout, so it is the mechanic that resolves it: a run whose context was
# compacted still has the issue it was invoked on and the scratchpad its
# environment block names, and called `close` without a path twice for want of
# the rest (#218). An explicit `--file` wins, and neither given is the usage
# error it always was.
#
# `init` writes the ten items and returns them, one per harness task the run
# then creates; `--rebuild` with `--state` is the recovery from a Run file a
# subagent overwrote. A flip returns the `mirror` value for that phase's task.
# Below the checklist it writes three sections the run fills by hand: `Design
# and plan`, `Deviations log`, and `Direct reads`, one line per informational
# read the run made straight through the host's REST form, no mechanic covering
# it, so one that recurs across runs is visible as a mechanic to promote.
#
# Reaches no host and no repo file: the scratchpad path is the only thing it
# writes.
#
# stdout: one JSON object per call
# exit: 0 ok · 1 the mechanic's own refusal · 2 malformed invocation
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: run-file init <issue|slug> --scratchpad <dir> [--rebuild] [--state <n>=<spec>] [--tripwires <t>] [--verifications <v>] [--reviewers <r>] [--legs <l>] | open <n> | close <n> | skip <n> <reason> | timing, each taking --file <path> or --issue <n|slug> [--scratchpad <dir>, default $TMPDIR or /tmp] resolving <scratchpad>/ship-<issue>/run.md'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
verb=$1; shift
case $verb in -*) ship_tooling "$usage" ;; esac

# The ten items, in the fixed wording. Four carry a tail the ship profile
# supplies; the rest are the same in every repo.
checklist() { # checklist <tripwires> <verifications> <reviewers> <legs>
  cat <<ITEMS
0 · Isolate: preflight, read the issue, worktree (or in place) on a fresh branch off the default
1 · Understand: derive success, claim, apply spec precedence
2 · Implement: classify (docs/code/infra), lane keys, TDD per class, tripwires $1
3 · Verify: $2 scoped to what changed
4 · Docs-sync + self-review: sync docs first, then \`code-review\` on the diff, auto-triage
5 · Local gate: base-fresh, then the repo's gate, all green
6 · Open PR: non-draft, Conventional-Commit title, Closes, reflect on the issue
7 · Reviewers: $3, one bounded pass each
8 · CI: resolve any conflict, land $4 green
9 · Merge gate: hard stop for human approval (unattended: summary as PR comment, return)
ITEMS
}

# One phase, one line: the first line carrying that number, so a flip and
# `timing` always mean the same line even where the design and plan below the
# checklist carry a line of the same shape.
phase_row()  { grep -n "^- \[.\] $1 · " "$file" | head -1; }
# The item text: the line without its marker and without the suffixes a flip
# owns. Ranges stay, because a re-open appends its own after them.
item()       { printf '%s' "$1" | sed 's/^- \[.\] //; s/ in_progress ([0-9][0-9]:[0-9][0-9]→)$//; s/ skipped (.*)$//'; }
# The clock is numeric wherever the mechanic writes it, so it is numeric
# wherever the mechanic reads it back: `??` would let prose cross the boundary.
is_open()    { case $1 in *" in_progress ("[0-9][0-9]:[0-9][0-9]"→)") return 0 ;; esac; return 1; }
# The mechanic owns the state shapes, so it owns what may sit beside them: a
# profile tail carrying one would be read as state by `timing`, by `ran` and by
# `open_phase`, and phase 2's tail sits at the end of its line. Refusing it here
# is the one place the boundary between wording and state can be made
# unambiguous.
# A reason is free text the profile does not supply, and `render` wraps it: a
# reason that closes the wrapper early leaves the line ending in a state shape
# the mechanic reads back, which is enough to report a skipped phase as open.
# The rendered line is what the file holds, so the rendered line is what is
# checked.
reject_written_state() { # reject_written_state <rendered line>
  [ "${1%%$'\n'*}" = "$1" ] \
    || ship_tooling "a phase is one line: that reason carries a newline; reword it"
  printf '%s\n' "$1" \
    | grep -Eq '\([0-9]{2}:[0-9]{2}→([0-9]{2}:[0-9]{2}(\+1d)?)?\)$' \
    && ship_tooling "that reason leaves the phase line ending in one of the Run file's own state shapes; reword it"
  return 0
}
reject_stamp_tail() { # reject_stamp_tail <flag> <value>
  printf '%s\n' "$2" \
    | grep -Eq '\([0-9]{2}:[0-9]{2}→[0-9]{2}:[0-9]{2}(\+1d)?\)|in_progress \([0-9]{2}:[0-9]{2}→\)| skipped \(' \
    && ship_tooling "$1 cannot carry the Run file's own state shapes ((HH:MM→HH:MM), in_progress (HH:MM→), skipped (<reason>)): a phase line reads them as state"
  return 0
}
# One phase, one line here too: a line of the checklist's shape copied into the
# design and plan would otherwise report a phase that is not open.
open_phase() {
  awk '/^- \[.\] [0-9][0-9]* · / {
         p = substr($0, 7); sub(/ .*/, "", p)
         if (p in seen) next
         seen[p] = 1
         if ($0 ~ / in_progress \([0-9][0-9]:[0-9][0-9]→\)$/) { print p; exit }
       }' "$file"
}

# Every line the mechanic writes is rendered here, so the flips and `init`'s
# rebuild cannot drift into two spellings of the same state.
render() { # render open|closed|rangeless|skipped <item> [<stamp>]
  case $1 in
    open)      printf -- '- [ ] %s in_progress (%s→)' "$2" "$3" ;;
    closed)    printf -- '- [x] %s (%s)' "$2" "$3" ;;
    rangeless) printf -- '- [x] %s' "$2" ;;
    skipped)   printf -- '- [x] %s skipped (%s)' "$2" "$3" ;;
  esac
}
write_line() { # write_line <lineno> <replacement>
  repl=$2 awk -v ln="$1" 'NR == ln { print ENVIRON["repl"]; next } { print }' "$file" > "$file.t" \
    && mv "$file.t" "$file" || { rm -f "$file.t"; ship_tooling "cannot write $file"; }
}
phase_arg() { # phase_arg <value>: the phase number; a flag here is a usage error
  case ${1:-} in -*) ship_tooling "$usage" ;; esac
  case ${1:-} in '' | *[!0-9]*) ship_tooling "$usage" ;; esac
}
parse_file() { # parse_file "$@": where every flip and timing reads the record
  file="" issue="" scratchpad=${TMPDIR:-/tmp}
  while [ $# -gt 0 ]; do
    case $1 in
      --file)       [ -n "${2:-}" ] || ship_tooling "--file needs a path"; file=$2; shift 2 ;;
      --issue)      [ -n "${2:-}" ] || ship_tooling "--issue needs an issue or slug"; issue=$2; shift 2 ;;
      --scratchpad) [ -n "${2:-}" ] || ship_tooling "--scratchpad needs a directory"; scratchpad=$2; shift 2 ;;
      *) ship_tooling "unknown flag: $1" ;;
    esac
  done
  [ -n "$file" ] || [ -n "$issue" ] || ship_tooling "$usage"
  [ -n "$file" ] || file="${scratchpad%/}/ship-$issue/run.md"
  # The resolved path, not the flags it came from: a run that brought the wrong
  # scratchpad reads which record the mechanic went looking for.
  [ -f "$file" ] || ship_fail "no Run file at $file"
}
# The row a flip acts on, or the refusal that it is not there. A missing line
# for one of the ten phases is the symptom of a Run file a subagent wrote over,
# so that refusal carries the recovery rather than leaving it to prose a
# compacted run may no longer hold; a number outside the ten is a typo, and
# rebuilding would wipe an intact record.
take_row() { # take_row <n>: sets line and lineno
  row=$(phase_row "$1")
  if [ -z "$row" ]; then
    case $1 in
      [0-9]) ship_fail "no phase $1 line in $file: a subagent overwrote the Run file; rebuild it with \`run-file init <issue> --scratchpad <dir> --rebuild\`, re-passing the --tripwires, --verifications, --reviewers and --legs the run began with and one --state per phase the transcript accounts for (open for the one that was running, no invented range), then log what was lost in the deviations log" ;;
    esac
    ship_fail "no phase $1 line in $file"
  fi
  lineno=${row%%:*}
  line=${row#*:}
}
flip_json() { # flip_json <state> <line> <mirror> [<reason>]
  jq -n --arg f "$file" --argjson n "$n" --arg s "$1" --arg l "$2" --arg m "$3" --arg r "${4:-}" \
    '{run_file: $f, phase: $n, state: $s, line: $l, mirror: $m}
     | if $r == "" then . else .reason = $r end'
}

case $verb in
init)
  [ -n "${1:-}" ] || ship_tooling "$usage"
  id=$1; shift
  case $id in -*) ship_tooling "$usage" ;; esac
  scratchpad="" tripwires=None verifications="None applicable" reviewers=none legs=None
  rebuild=false states=""
  while [ $# -gt 0 ]; do
    case $1 in
      --scratchpad)    [ $# -ge 2 ] || ship_tooling "--scratchpad needs a directory"; scratchpad=$2; shift 2 ;;
      --tripwires)     [ $# -ge 2 ] || ship_tooling "--tripwires needs a value"; tripwires=$2; shift 2 ;;
      --verifications) [ $# -ge 2 ] || ship_tooling "--verifications needs a value"; verifications=$2; shift 2 ;;
      --reviewers)     [ $# -ge 2 ] || ship_tooling "--reviewers needs a value"; reviewers=$2; shift 2 ;;
      --legs)          [ $# -ge 2 ] || ship_tooling "--legs needs a value"; legs=$2; shift 2 ;;
      --state)         [ $# -ge 2 ] || ship_tooling "--state needs <n>=<spec>"; states="$states$2
"; shift 2 ;;
      --rebuild)       rebuild=true; shift ;;
      *) ship_tooling "unknown flag: $1" ;;
    esac
  done
  [ -n "$scratchpad" ] || ship_tooling "$usage"
  reject_stamp_tail --tripwires "$tripwires"
  reject_stamp_tail --verifications "$verifications"
  reject_stamp_tail --reviewers "$reviewers"
  reject_stamp_tail --legs "$legs"
  file="$scratchpad/ship-$id/run.md"
  # Every state is validated before anything is written, so a bad spec leaves
  # no half-built file behind.
  state_usage='--state takes <0-9>=open|done|done:<HH:MM→HH:MM>|skipped:<reason>'
  open_states=0 stated=""
  while IFS= read -r st; do
    [ -n "$st" ] || continue
    # One state per phase: two for the same one would apply in order and leave
    # a rebuild contradicting its own recovered state.
    case " $stated " in *" ${st%%=*} "*) ship_tooling "--state names phase ${st%%=*} twice" ;; esac
    stated="$stated${st%%=*} "
    case $st in
      [0-9]=open) open_states=$((open_states + 1)) ;;
      [0-9]=done) : ;;
      # The reason is free text, and it is decided here with every other state:
      # under `--rebuild` the write is what the caller is recovering from, so a
      # refusal after it would destroy the record this call exists to restore.
      [0-9]=skipped:?*) sp=${st#*=}; reject_written_state "$(render skipped x "${sp#skipped:}")" ;;
      [0-9]=done:[0-9][0-9]:[0-9][0-9]→[0-9][0-9]:[0-9][0-9] | [0-9]=done:[0-9][0-9]:[0-9][0-9]→[0-9][0-9]:[0-9][0-9]+1d) : ;;
      *) ship_tooling "$state_usage" ;;
    esac
  done <<EOSTATES
$states
EOSTATES
  [ "$open_states" -le 1 ] || ship_fail "a Run file holds one open phase; $open_states were given"
  # The rebuild path: a Run file a subagent overwrote is rebuilt in place, so
  # the guard that keeps a resumed run from wiping its own record steps aside
  # only when the caller says so.
  [ -e "$file" ] && [ "$rebuild" = false ] && ship_fail "Run file exists: $file (pass --rebuild to rebuild it in place)"
  mkdir -p "$scratchpad/ship-$id" || ship_tooling "cannot create $scratchpad/ship-$id"
  items=$(checklist "$tripwires" "$verifications" "$reviewers" "$legs")
  { printf '# ship run · %s\n\n' "$id"
    printf '%s\n' "$items" | sed 's/^/- [ ] /'
    printf '\n## Design and plan\n\n(pending)\n\n## Deviations log\n\n(none yet)\n'
    printf '\n## Direct reads\n\n(none yet)\n'
  } > "$file" || ship_tooling "cannot write $file"
  while IFS= read -r st; do
    [ -n "$st" ] || continue
    take_row "${st%%=*}"
    spec=${st#*=}
    case $spec in
      open)      new=$(render open "$(item "$line")" "$(date -u +%H:%M)") ;;
      done)      new=$(render rangeless "$(item "$line")") ;;
      done:*)    new=$(render closed "$(item "$line")" "${spec#done:}") ;;
      skipped:*) new=$(render skipped "$(item "$line")" "${spec#skipped:}") ;;
    esac
    write_line "$lineno" "$new"
  done <<EOAPPLY
$states
EOAPPLY
  printf '%s\n' "$items" | jq -Rs --arg f "$file" --arg i "$id" \
    '{run_file: $f, id: $i, items: (split("\n") | map(select(. != "")))}'
  ;;
open)
  phase_arg "${1:-}"; n=$1; shift
  parse_file "$@"
  take_row "$n"
  busy=$(open_phase)
  if [ -n "$busy" ]; then
    [ "$busy" = "$n" ] && ship_fail "phase $n is already open"
    ship_fail "phase $busy is open; close it before opening $n"
  fi
  new=$(render open "$(item "$line")" "$(date -u +%H:%M)")
  write_line "$lineno" "$new"
  flip_json open "$new" in_progress
  ;;
close)
  phase_arg "${1:-}"; n=$1; shift
  parse_file "$@"
  take_row "$n"
  is_open "$line" || ship_fail "phase $n is not open"
  start=${line##*in_progress (}
  start=${start%%→*}
  end=$(date -u +%H:%M)
  # A close stamped before its open crossed midnight UTC; the range still reads
  # left to right, and `timing` adds the day.
  [ "$end" \< "$start" ] && end="$end+1d"
  new=$(render closed "$(item "$line")" "$start→$end")
  write_line "$lineno" "$new"
  flip_json closed "$new" completed
  ;;
skip)
  phase_arg "${1:-}"; n=$1; shift
  [ -n "${1:-}" ] || ship_tooling "$usage"
  reason=$1; shift
  case $reason in -*) ship_tooling "$usage" ;; esac
  parse_file "$@"
  take_row "$n"
  is_open "$line" && ship_fail "phase $n is open; close it before skipping it"
  # The marker is the mechanic's own record that the phase is done, so it
  # answers before the stamps do: a phase rebuilt as `done` carries no range.
  # A skipped line carries that same marker, so its arm comes first: a second
  # skip re-stamps the reason rather than being refused as a phase that ran.
  case $line in
    *" skipped ("*) ;;
    "- [x] "*) ship_fail "phase $n has already run; it cannot be skipped" ;;
  esac
  new=$(render skipped "$(item "$line")" "$reason")
  reject_written_state "$new"
  write_line "$lineno" "$new"
  flip_json skipped "$new" completed "$reason"
  ;;
timing)
  parse_file "$@"
  # Every range on every phase line, in minutes. A `+1d` close crossed midnight
  # UTC, so it carries the day; an aggregate whose end lands before its start
  # crossed one too. Times are read by splitting on the arrow rather than by
  # offset, because it is one character to gawk and three bytes to mawk.
  awk '
    function mins(t,   hm) { split(t, hm, ":"); return hm[1] * 60 + hm[2] }
    function agg(a, b) { return (b < a) ? b + 1440 - a : b - a }
    /^- \[.\] [0-9][0-9]* · / {
      p = substr($0, 7); sub(/ .*/, "", p)
      if (p in seen) next          # one phase, one line: the first, as a flip reads it
      seen[p] = 1
      s = $0
      sub(/ in_progress \([0-9][0-9]:[0-9][0-9]→\)$/, "", s)
      sub(/ skipped \(.*\)$/, "", s)
      total = 0; found = 0
      # Only the trailing run of ranges is a stamp. A range-shaped substring
      # inside the item text (a profile tail, a reason) is wording, not time,
      # so the scan anchors at the end of the line and stops at the first
      # thing that is not a range.
      while (match(s, / \([0-9][0-9]:[0-9][0-9]→[0-9][0-9]:[0-9][0-9](\+1d)?\)$/)) {
        r = substr(s, RSTART, RLENGTH)
        s = substr(s, 1, RSTART - 1)
        split(r, part, "→")
        st = part[1]; sub(/^ \(/, "", st)
        en = part[2]; day = (en ~ /\+1d/) ? 1440 : 0
        sub(/\+1d/, "", en); sub(/\)$/, "", en)
        total += mins(en) + day - mins(st)
        if (!found) { last[p] = mins(en) + day; found = 1 }
        first[p] = mins(st)        # the scan runs right to left, so this ends leftmost
      }
      phase[p] = found ? total : "unverified"
    }
    END {
      for (p in seen) printf "phase\t%s\t%s\n", p, phase[p]
      stp = (0 in first && 6 in last) ? agg(first[0], last[6]) : "unverified"
      ptg = (6 in last && 8 in last) ? agg(last[6], last[8]) : "unverified"
      printf "agg\tstart_to_pr\t%s\n", stp
      printf "agg\tpr_to_gate\t%s\n", ptg
    }
  ' "$file" | jq -Rs --arg f "$file" '
    def num: if . == "unverified" then . else tonumber end;
    def m: if . == "unverified" then . else tostring + "m" end;
    [split("\n")[] | select(. != "") | split("\t")] as $rows
    | ($rows | map(select(.[0] == "phase") | {(.[1]): (.[2] | num)}) | add // {}) as $phases
    | ($rows | map(select(.[0] == "agg") | {(.[1]): (.[2] | num)}) | add) as $agg
    | {run_file: $f, start_to_pr: $agg.start_to_pr, pr_to_gate: $agg.pr_to_gate, phases: $phases,
       row: ("start→PR \($agg.start_to_pr | m) · PR→gate \($agg.pr_to_gate | m) · per phase: "
             + ([range(0; 9) | "\(.) \($phases[tostring] // "unverified")"] | join(" · ")))}'
  ;;
*)
  ship_tooling "$usage"
  ;;
esac
