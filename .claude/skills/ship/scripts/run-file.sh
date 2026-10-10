#!/usr/bin/env bash
# The Run file: a ship run's record, outside every working tree, in the repo's
# git common directory. This mechanic owns the file's shape and every flip of
# it, so a stamp is a measurement the mechanic took rather than a time a run
# recalled, and the merge summary's `Timing:` row is arithmetic rather than
# mental subtraction.
#
#   run-file init <issue|slug> [--scratchpad <dir>] [--rebuild]
#                [--state <n>=open|done|done:<HH:MM→HH:MM>|skipped:<reason>]...
#                [--tripwires <t>] [--verifications <v>] [--reviewers <r>] [--legs <l>]
#                [--from-profile [<path>]]
#   run-file open|next <n> <where>
#   run-file close <n> [--result <name>=<word>[: <note>]]... <where>
#   run-file skip <n> <reason> <where>
#   run-file grade <patch|minor|breaking> <where>
#   run-file gate record <file|-> [--head <sha>] <where>
#   run-file gate read --head <sha> <where>
#   run-file gate clean <ci-file|-> --head <sha> <where>
#   run-file timing <where>
#   run-file prove <test> <path>... <where>
#   run-file probe <ref> <where> -- <command>...
#   <where>: --file <path> | --issue <n|slug> [--scratchpad <dir>]
#
# The record root is `<git common dir>/ship`: the main checkout and every
# worktree of it resolve the same directory, git never lists it as a working-tree
# file, and it outlives a wiped temp directory, which took the record with it in
# three runs. `--scratchpad <dir>` names another root; outside a git checkout it
# is the only one. Under the root, `ship-<issue>/run.md` is the Run file and
# `scratch-<issue>/` is the run's Scratch directory, which `init` creates and
# returns as `scratch`; the roles' subdirectories are the callers'. `cleanup`
# removes both.
#
# The `--issue` form reads the layout `init` wrote. `init` owns that layout, so
# it is the mechanic that resolves it: a run whose context was compacted still
# has the issue it was invoked on, and called `close` without a path twice for
# want of the rest (#218). An explicit `--file` wins, and neither given is the
# usage error it always was.
#
# `init` writes the ten items and returns them, one per harness task the run
# then creates, and opens phase 0 (`opened`, `mirror`) unless a `--state` states
# the run itself; `--rebuild` with `--state` is the recovery from a Run file a
# subagent overwrote or removed, and `--rebuild` alone replaces the record of an
# issue whose earlier run stopped with a fresh one. A `--state` set holds one
# open phase, or 3 and 4 both, the overlap `open 4` allows. `--from-profile`
# fills `--tripwires`, `--verifications`, `--reviewers` and `--legs` from the
# ship profile (the `Tripwires:` line under `## Local gate`, the `###` headings
# under `## Verification` and `## Reviewers`, the leg names under `## CI`); a
# flag given beside it wins. Each `###` heading under `## Verification` is one
# Verification name whatever it holds, spaces and punctuation included, except
# `=`, which `--result` splits on. A flip returns the `mirror` value for that
# phase's task, and `next <n>` is the close of the one open phase and the open
# of <n> in one call, refused whole when either half would be. `open 4` is the
# one open admitted while phase 3 is open, the overlap the run has; any other
# open over an open phase is refused.
# Below the checklist it writes sections the run fills: `Verification results`
# where `--from-profile` took the names from the profile's headings, or
# `--verifications` names a comma list of name-shaped words (one
# `- <name>: pending` each, which `close 3 --result <name>=<word>` settles with
# one of pass, fail, deferred-to-ci, unavailable, unexercised or n/a, and which
# `close 3` will not pass while any is pending), then `Design and plan`,
# `Deviations log`, and `Direct reads`, one line per informational read the run
# made straight through the host's REST form, no mechanic covering it, so one
# that recurs across runs is visible as a mechanic to promote. `gate record`
# appends the local gate's verdict, the head it ran on and the verdict JSON's
# `gates` object (compact, one line, absent when the JSON has none) to a
# `Local gate` section; `gate read` answers whether the head the run is at is
# still the one that verdict covers, and returns the gates the merge gate cites
# when it does not re-run the gate. Both take `--head` as a full or abbreviated
# sha, 7 to 64 hex digits.
#
# `close 4` and `close 7` hold the self-review and the review loop to the
# evidence they leave in the Run file. They are completeness guards: they check
# that each line is present and in its shape, and the self-review keeps the
# judgment of what a line says. Two lines are produced, written by the verb that
# ran the proof: the red `Reverted-fix:` line by `prove` and the `Probe:` line by
# `probe`. The rest are attested, written by the run and taken at its word: the
# `n/a` `Reverted-fix:` line, `Dropped:`, `Near-miss:` and `Declined:`, and
# `close 7`'s `Round:` and `Stop:`. A refused close writes nothing, names
# everything missing in one message, and `next` over either phase is held the
# same way. A gate line is a line anywhere in the file, an optional leading
# `- ` accepted, matched by exact prefix at the line start. `close 4` reads the
# checkout's own diff (the working tree, untracked files included, against the
# merge base of HEAD and origin/HEAD) and asks `dropped-lines`; it needs:
#   `Reverted-fix: <test path>: red at <sha> reverting <path>...` (produced) or
#     `Reverted-fix: <test path>: n/a: <reason>` (attested) for each test file
#     the diff adds or changes, deletions aside. A red line stands while neither
#     the test nor a path it lists has changed since <sha> in the working tree,
#     untracked files included; a hand-written `red` or `red: <text>` is
#     refused. A test file has a path component `tests`, `test` or `__tests__`,
#     or a basename matching `*.test.*`, `*.spec.*`, `*_test.*` or `test_*`.
#   `Dropped: <id> re-homed at <path>` or `Dropped: <id> dropped on purpose:
#     <why>` for each block `dropped-lines` reports, <id> being its `id`.
#   a near-miss table for each added or changed `*.sh` outside the tests whose
#     added lines hold a pattern matcher (a heuristic: `=~`, a `grep` or `egrep`
#     command, or a regex literal in an `awk` or `sed` line, comment lines
#     aside): one `Near-miss: <script>: <kind>: <test path>` (a file in the
#     checkout) or `Near-miss: <script>: <kind>: n/a: <reason>` per kind, or one
#     `Near-miss: <script>: n/a: <reason>` for the script. The kinds are
#     partial-token (a near-miss token that must not match), quoted (the token
#     inside a comment, a code span or a quote), indented (indented or nested
#     input), unbalanced (unbalanced input) and unreadable (unreadable input,
#     which must exit 2).
#   every `Probe:` line in the produced shape,
#     `Probe: <ref>: <command> => exit <n> at <sha>: <last output line>`.
#   every `Declined: <ref>: <kind>: <reason>` carrying its kind, `claim` or
#     `judgment`, and a nonblank reason; <ref> is the text before the first
#     `: `. A `claim:` decline needs a produced `Probe: <ref>:` line, a
#     `judgment:` one needs none, and the reason's words decide nothing.
# `close 7` reads the reviewers from the phase's checklist row (nothing is held
# where the row names none, or names what is not a list of names) and needs a
# `Stop: <reviewer>: <reason>` for each, the last such line deciding, the reason
# one of cap, tree unchanged, small lane, auto-once or not reviewed, and unless
# it is `not reviewed` at least one `Round: <reviewer> <n>: <text>`, <n> a
# positive integer. `grade` writes `Grade: <word>` under the `## Grade` section,
# replacing the line a call before it wrote.
#
# `prove` runs the sibling `revert-red` on <test> and its <path>s. On its exit 0
# it appends `Reverted-fix: <test>: red at <sha> reverting <path>...` under
# `## Evidence`, <sha> the HEAD revert-red ran on; on its exit 1 or 2 it writes
# nothing and exits with that code, passing revert-red's answer through. It
# writes each path top-relative with no leading `./`, the form `close 4` looks it
# up by, and refuses a reverted path holding whitespace, which the line could
# not be read back from. `probe` runs <command> as argv, never through a shell string, at the
# checkout top with stdin from /dev/null, and appends
# `Probe: <ref>: <command> => exit <n> at <sha>: <last output line>`, <command>
# shell-quoted word by word (`printf %q`) so it re-runs as recorded, under
# `## Evidence`, the last nonblank line of stdout and stderr together, or
# `(no output)`. It exits 0 whatever the command's exit; a ref holding `: ` names
# no decline and is refused.
#
# Reaches no host and writes only the record root, except through the command
# `probe` is given, which may do either. `close 4` reads the checkout's git
# state and runs the sibling `dropped-lines` mechanic, `prove` the sibling
# `revert-red`, and a read that fails is exit 2, never a clean diff.
#
# stdout: one JSON object per call
#   init: {run_file, id, scratch, items[]}, plus {opened: 0, mirror} when it opened phase 0
#   open, close, skip: {run_file, phase, state, line, mirror[, reason]}
#   grade: {run_file, grade}
#   next: {run_file, closed: {phase, line, mirror}, opened: {phase, line, mirror}}
#   timing: {run_file, start_to_pr, pr_to_gate, phases{}, row}; a skipped phase
#     reads `skipped` and a phase with no range `unverified`
#   prove: {test, paths, red, run_file, head}, revert-red's verdict plus the
#     record and the head, its paths as typed where the line normalises them;
#     on a refusal revert-red's own answer
#   probe: {run_file, ref, command, exit, head, last}
#   gate record: {run_file, head, verdict}
#   gate read: {run_file, verdict, head, gates, current, behind}; gates is null
#     for a record made without one; behind is null where git cannot count the
#     commits between the recorded head and <sha>
#   gate clean: {clean, held_by[]}; clean means the current gate passed or deferred to CI every
#     check, every profile CI leg succeeded on head, verifications passed or were
#     inapplicable or deferred to a green associated CI leg, and each
#     reviewer's loop stopped on tree unchanged with a dispositioned Round. A
#     not-reviewed primary, or one that spent its Cap: (dispositioned Round
#     lines numbered 1 through Cap) and stopped at cap, may be covered by a
#     fallback that stopped so, and a fallback is owed its own stop only after
#     such a primary. Other cap,
#     small lane and auto-once stops, a deferred gate beside a non-green check
#     outside Legs:, nonblank Override or Ship-defect
#     evidence, and defect or tracker drafts beside the Run file (excluding
#     .base.md) hold the gate.
#     An ordinary deviation does not hold it. This read is independent of Merge.
# exit: 0 ok · 1 the mechanic's own refusal (a gate's missing evidence among
#   them, a held clean gate, or a test that stayed green under `prove`) · 2
#   malformed invocation, a read a gate needs that failed, or revert-red's own
#   tooling refusal under `prove`
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: run-file init <issue|slug> [--scratchpad <dir>] [--rebuild] [--state <n>=<spec>] [--tripwires <t>] [--verifications <v>] [--reviewers <r>] [--legs <l>] [--from-profile [<path>]] | open <n> | next <n> | close <n> [--result <name>=<word>[: <note>]] | skip <n> <reason> | grade <patch|minor|breaking> | gate record <file|-> [--head <sha>] | gate read --head <sha> | gate clean <ci-file|-> --head <sha> | timing | prove <test> <path>... | probe <ref> <where> -- <command>..., each taking <where>: --file <path> or --issue <n|slug> [--scratchpad <dir>, default <git common dir>/ship] resolving <root>/ship-<issue>/run.md'
# The recovery both refusals of a missing record carry, rather than prose a
# compacted run may no longer hold.
rebuild_hint="rebuild it with \`run-file init <issue> --rebuild [--scratchpad <dir>]\`, re-passing the --tripwires, --verifications, --reviewers and --legs the run began with and one --state per phase the transcript accounts for (open for the one that was running, or 3 and 4 both open if they overlapped, no invented range), then log what was lost in the deviations log"
ship_help "$usage" "$@"
ship_args "$usage" arg "$@"
verb=$1; shift

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
9 · Merge gate: summary, default human approval or clean opt-in (unattended: summary as PR comment, return)
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
  printf '%s\n' "$1" | grep -Eq '\([0-9]{2}:[0-9]{2}→([0-9]{2}:[0-9]{2}(\+1d)?)?\)$'
  case $? in
    0) ship_tooling "that reason leaves the phase line ending in one of the Run file's own state shapes; reword it" ;;
    1) ;;
    *) ship_tooling "cannot check that reason for the Run file's own state shapes: grep failed" ;;
  esac
}
reject_stamp_tail() { # reject_stamp_tail <flag> <value>
  printf '%s\n' "$2" | grep -Eq '\([0-9]{2}:[0-9]{2}→[0-9]{2}:[0-9]{2}(\+1d)?\)|in_progress \([0-9]{2}:[0-9]{2}→\)| skipped \('
  case $? in
    0) ship_tooling "$1 cannot carry the Run file's own state shapes ((HH:MM→HH:MM), in_progress (HH:MM→), skipped (<reason>)): a phase line reads them as state" ;;
    1) ;;
    *) ship_tooling "cannot check $1 for the Run file's own state shapes: grep failed" ;;
  esac
}
# One phase, one line here too: a line of the checklist's shape copied into the
# design and plan would otherwise report a phase that is not open. Every open
# phase, one per line: the run holds one, or two during the 3/4 overlap.
open_phases() {
  awk '/^- \[.\] [0-9][0-9]* · / {
         p = substr($0, 7); sub(/ .*/, "", p)
         if (p in seen) next
         seen[p] = 1
         if ($0 ~ / in_progress \([0-9][0-9]:[0-9][0-9]→\)$/) print p
       }' "$file"
}
# The first phase below <n> whose row is not `[x]`: closed, rebuilt `done` and
# skipped all tick it, so every phase that ran or was skipped passes. Only the
# first row per phase counts, as in `open_phases`. An open phase is not a gap:
# it is running, and the callers that let one stand while <n> opens (the 3/4
# overlap, `next`, which closes it) have already decided that.
unflipped() { # unflipped <n>
  awk -v n="$1" '/^- \[.\] [0-9][0-9]* · / {
         p = substr($0, 7); sub(/ .*/, "", p)
         if (p in seen) next
         seen[p] = 1
         if ($0 ~ / in_progress \([0-9][0-9]:[0-9][0-9]→\)$/) next
         if (p + 0 < n + 0 && $0 !~ /^- \[x\] /) { print p; exit }
       }' "$file"
}
# The refusal for a phase opened over an earlier one never flipped: that one
# stays unticked and `unverified` on the Timing row for a phase that may have
# run. For a phase that ran, the recovery is open-then-close: `close` needs it
# open, and `skip` would record it as not run. A phase that did not run is
# skipped.
# <verb> is the recovery flip: `open`, or `next` where another phase is open and
# `open` over it would be refused. The retry is `open` either way: once the gap
# is closed no phase is open, which `next` refuses.
refuse_gap() { # refuse_gap <verb> <gap> <n>
  ship_fail "phase $2 is neither closed nor skipped. If it ran: \`run-file $1 $2\`, \`run-file close $2\`, then note in the deviations log that its stamp is the recovery time, so its minutes and any start→PR or PR→gate figure it bounds reflect the recovery, plus when it really ran if the transcript holds that. If it did not run: \`run-file skip $2 <reason>\`. Then retry \`run-file open $3\`, which names the next such phase if any."
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
# The record root: the --scratchpad named, else the git common dir's `ship`
# directory. Sets `root` in this shell, because a refusal inside a `$( )` would
# exit only the substitution.
resolve_root() {
  if [ -n "$scratchpad" ]; then root=${scratchpad%/}
  else root=$(ship_record_root) || ship_tooling "not inside a git checkout: pass --scratchpad <dir>"
  fi
}
parse_file() { # parse_file "$@": where every flip and timing reads the record
  file="" issue="" scratchpad="" results="" head=""
  while [ $# -gt 0 ]; do
    case $1 in
      --file)       ship_flag_value "$usage" "${2:-}" "--file needs a path"; file=$2; shift 2 ;;
      --issue)      ship_flag_value "$usage" "${2:-}" "--issue needs an issue or slug"; issue=$2; shift 2 ;;
      --scratchpad) ship_flag_value "$usage" "${2:-}" "--scratchpad needs a directory"; scratchpad=$2; shift 2 ;;
      --result)     ship_flag_value "$usage" "${2:-}" "--result needs <name>=<word>"; results="$results$2"$'\n'; shift 2 ;;
      --head)       ship_flag_value "$usage" "${2:-}" "--head needs a sha"; head=$2; shift 2 ;;
      *) ship_tooling "unknown flag: $1" ;;
    esac
  done
  [ -n "$file" ] || [ -n "$issue" ] || ship_tooling "$usage"
  [ -n "$file" ] || { resolve_root; file="$root/ship-$issue/run.md"; }
  # The resolved path, not the flags it came from: a run that brought the wrong
  # scratchpad reads which record the mechanic went looking for, and one whose
  # record a subagent removed reads the same recovery as an overwritten one.
  [ -f "$file" ] || ship_fail "no Run file at $file: check that path first; if it is the right one, a subagent removed the Run file; $rebuild_hint"
}
# The row a flip acts on, or the refusal that it is not there. A missing line
# for one of the ten phases is the symptom of a Run file a subagent wrote over,
# so that refusal carries the recovery rather than leaving it to prose a
# compacted run may no longer hold; a number outside the ten is a typo, and
# rebuilding would wipe an intact record.
take_row() { # take_row <n>: sets line and lineno
  row=$(phase_row "$1")
  # No match is exit 1 and a grep that failed is 2: only the first is a missing line.
  [ $? -le 1 ] || ship_tooling "cannot read the phase lines in $file: grep failed"
  if [ -z "$row" ]; then
    case $1 in
      [0-9]) ship_fail "no phase $1 line in $file: a subagent overwrote the Run file; $rebuild_hint" ;;
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

# A flag only one verb takes: every other verb reading it is malformed.
no_extra() { [ -z "$results$head" ] || ship_tooling "$usage"; }
# `close` and `next` stamp a close the same way, so the line is built once.
closed_line() { # closed_line <open line>: that line, closed at the clock
  local start end
  start=${1##*in_progress (}
  start=${start%%→*}
  end=$(date -u +%H:%M)
  # A close stamped before its open crossed midnight UTC; the range still reads
  # left to right, and `timing` adds the day.
  [ "$end" \< "$start" ] && end="$end+1d"
  render closed "$(item "$1")" "$start→$end"
}
# Insert a line at the end of a `## ` section, creating the section at the end
# of the file when the run has none. The end of the section is its last
# non-blank line, so a section the run wrote below it is not run into.
append_to_section() { # append_to_section <heading> <line>
  local last
  last=$(awk -v h="$1" '{ t = $0; sub(/[ \t\r]+$/, "", t) } t == h { f = 1; last = NR; next } /^## / { f = 0 } f && NF { last = NR } END { print last + 0 }' "$file")
  if [ "$last" -gt 0 ]; then
    repl=$2 awk -v ln="$last" '{ print } NR == ln { print ENVIRON["repl"] }' "$file" > "$file.t" \
      && mv "$file.t" "$file" || { rm -f "$file.t"; ship_tooling "cannot write $file"; }
  else
    printf '\n%s\n\n%s\n' "$1" "$2" >> "$file" || ship_tooling "cannot write $file"
  fi
}

# --- Phase 3's verifications ---------------------------------------------------
# The names a comma list names, one per line; nothing for `None.`, `None
# applicable` or prose that is not a list of names, so a free-text slot still
# works and simply has no results section to settle.
verif_names() { # verif_names <verifications>
  local names n
  case $1 in "" | "None applicable" | "None." | None | none) return 0 ;; esac
  names=$(printf '%s\n' "$1" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  while IFS= read -r n; do
    [[ $n =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || return 0
  done <<EONAMES
$names
EONAMES
  printf '%s\n' "$names"
}
result_words='pass|fail|deferred-to-ci|unavailable|unexercised|n/a'
# The section's entries, `- <name>: <what>` each. A note after the word may
# itself end in `: pending`, so pending is matched on the whole line.
results_lines() { awk '/^## /{ f = ($0 == "## Verification results") } f && /^- [^:]+: /' "$file"; }
# A name may hold a colon, so an entry is pending when the line ends `: pending`
# with no result word as a field before it (`- name: pass: waiting: pending` is a
# result whose note ends so), not when a colon-free name precedes it.
pending_verifs() {
  results_lines | w="$result_words" awk '
    $0 ~ /: pending$/ && $0 !~ ("^- .*: (" ENVIRON["w"] ")(:|$)") { sub(/^- /, ""); sub(/: pending$/, ""); print }'
}
# One `--result <name>=<word>[: <note>]`, checked against the names `init`
# recorded; sets rname, rword and rnote.
parse_result() { # parse_result <arg>
  local rest
  case $1 in *=*) ;; *) ship_tooling "--result takes <name>=<word>[: <note>], word one of ${result_words//|/, }" ;; esac
  rname=${1%%=*}; rest=${1#*=}; rword=${rest%%:*}
  case $rest in *:*) rnote=${rest#*:}; rnote=${rnote# } ;; *) rnote="" ;; esac
  case $rword in
    pass | fail | deferred-to-ci | unavailable | unexercised | n/a) ;;
    *) ship_tooling "--result word '$rword' is not one of ${result_words//|/, }" ;;
  esac
  [ "${rnote%%$'\n'*}" = "$rnote" ] || ship_tooling "a result note is one line"
  # A profile name may hold a colon, so the entry is found by its prefix rather
  # than by cutting the line at the first one.
  results_lines | nm=$rname awk 'index($0, "- " ENVIRON["nm"] ": ") == 1 { f = 1 } END { exit !f }' \
    || ship_tooling "--result names '$rname', which --verifications did not name at init"
}
# Every result is checked before any is written, so a bad one leaves the record
# as it was.
check_results() {
  local r
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    parse_result "$r"
  done <<EORESULTS
$results
EORESULTS
}
record_results() {
  local r text
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    parse_result "$r"
    text=$rword; [ -z "$rnote" ] || text="$rword: $rnote"
    nm=$rname tx=$text awk '/^## /{ f = ($0 == "## Verification results") }
      f && index($0, "- " ENVIRON["nm"] ": ") == 1 { print "- " ENVIRON["nm"] ": " ENVIRON["tx"]; next } { print }' \
      "$file" > "$file.t" && mv "$file.t" "$file" || { rm -f "$file.t"; ship_tooling "cannot write $file"; }
  done <<EORECORD
$results
EORECORD
}
# Phase 3 closes only once every named verification has a result.
refuse_pending() {
  local pend
  pend=$(pending_verifs | tr '\n' ',' | sed 's/,$//; s/,/, /g')
  [ -z "$pend" ] || ship_fail "phase 3 cannot close with verifications pending: $pend; record each with \`run-file close 3 --result <name>=<$result_words>\`"
}
# Every `###` heading under one `## ` section of a profile, one per line.
profile_headings() { # profile_headings <section> <profile-body>
  awk -v s="## $1" '/^## / { f = ($0 == s || $0 == s "\r") ; next }
    f && /^### / { h = substr($0, 5); sub(/[ \t\r]+$/, "", h); print h }' <<<"$2"
}
join_names() { awk '{ out = out (NR > 1 ? ", " : "") $0 } END { print out }'; }

# --- The self-review and review-loop gates -------------------------------------
# A gate line is a line of the Run file, an optional leading `- ` accepted,
# matched by exact prefix at the line start: the text after the prefix, one line
# each. The prefix goes through ENVIRON, so a name's dot or bracket is never a
# pattern.
rests() { # rests <prefix>
  p=$1 awk '{ l = $0; sub(/^- /, "", l) } index(l, ENVIRON["p"]) == 1 { print substr(l, length(ENVIRON["p"]) + 1) }' "$file"
}
trim_end() { # trim_end <text>
  local t=$1
  printf '%s' "${t%"${t##*[![:space:]]}"}"
}
is_blank() { [ -z "${1//[[:space:]]/}" ]; }
gaps=""
gap() { gaps="$gaps; $1"; }
refuse_gaps() { # refuse_gaps <phase>
  [ -z "$gaps" ] || ship_fail "phase $1 cannot close: ${gaps#; }"
  return 0
}

# A path is a test file by a component of it or by its basename.
is_test_path() {
  case /$1/ in */tests/* | */test/* | */__tests__/*) return 0 ;; esac
  case ${1##*/} in *.test.* | *.spec.* | *_test.* | test_*) return 0 ;; esac
  return 1
}
# A heuristic, not a parser: a line holding `=~`, a grep, or an awk or sed with a
# regex literal. Comment lines are not matchers. A false hit costs the author one
# `n/a` line; a miss is the gap this gate exists to close.
q="'"
matcher_re="=~|(^|[^[:alnum:]_.-])e?grep([[:space:]]|\$)|(^|[^[:alnum:]_-])(awk|sed)[[:space:]].*([~!(&|{;$q\"][[:space:]]*/[^/]+/|[^[:alnum:]_/]s/[^/]+/)"
has_matcher() { # has_matcher <added lines>
  local code
  code=$(printf '%s\n' "$1" | awk '!/^[[:space:]]*#/')
  grep -Eq -e "$matcher_re" <<<"$code"
  case $? in
    0) return 0 ;;
    1) return 1 ;;
    *) ship_tooling "cannot read the added lines for a pattern matcher" ;;
  esac
}
near_kinds="partial-token quoted indented unbalanced unreadable"
# The two produced lines, by the shape their verbs write. A red line names the
# head revert-red ran on and the paths it reverted; a probe line, after its
# `<ref>: `, the command, its exit status and the head it ran on.
red_re='^red at ([0-9a-f]{7,64}) reverting ([^[:space:]].*)$'
probe_re='^[^[:space:]].* => exit [0-9]+ at [0-9a-f]{7,64}: [^[:space:]]'
# A red line stands while neither its test nor a path it reverted has changed
# since its head, read against the working tree, untracked files included: a
# later commit that touches neither keeps it.
fresh_red() { # fresh_red <top> <test> <sha> <paths>: 0 fresh, 1 not, with why set
  local -a reverted
  local others stale="Reverted-fix line for $2 is stale: the test or a path it reverted changed since $3: re-run \`run-file prove $2 <path>...\`"
  read -ra reverted <<<"$4"
  if ! git -C "$1" cat-file -e "$3^{commit}" 2>/dev/null; then
    why="Reverted-fix line for $2 names $3, which is no commit here: run \`run-file prove $2 <path>...\`"
    return 1
  fi
  # Literal pathspecs: the line holds file names, and a name like `:x.sh` is
  # pathspec magic to git otherwise.
  git -C "$1" --literal-pathspecs diff --quiet "$3" -- "$2" "${reverted[@]}"
  case $? in
    0) ;;
    1) why=$stale; return 1 ;;
    *) ship_tooling "cannot read the diff of $2 since $3" ;;
  esac
  others=$(git -C "$1" --literal-pathspecs ls-files --others --exclude-standard -- "$2" "${reverted[@]}") \
    || ship_tooling "cannot list the untracked files among $2 and the paths it reverted"
  [ -z "$others" ] || { why=$stale; return 1; }
}

# Phase 4: every test file changed has its Reverted-fix line, every removed block
# its disposition, every new matcher its near-miss table, every probe line its
# produced shape, and every decline its kind, a claim its probe.
gate_phase4() {
  local base mb top tracked untracked paths p r ok ids id miss k added here dl ref reason rc why
  base=$(ship_base_ref --local) || ship_tooling "close 4 reads the diff against origin/HEAD, which cannot be resolved here"
  top=$(git rev-parse --show-toplevel 2>/dev/null) || ship_tooling "close 4 reads the checkout's diff: not inside a git checkout"
  mb=$(git -C "$top" merge-base "$base" HEAD 2>/dev/null) || ship_tooling "close 4 reads the diff against $base: no merge base with HEAD"
  tracked=$(git -C "$top" diff --no-renames --name-status -z "$mb" | tr '\0' '\n' | awk 'NR % 2 == 1 { s = $0; next } s != "D" { print }') \
    || ship_tooling "cannot read the checkout's diff against $mb"
  untracked=$(git -C "$top" ls-files -z --others --exclude-standard | tr '\0' '\n') \
    || ship_tooling "cannot list the checkout's untracked files"
  # The installed copy under .claude/skills mirrors its source, which carries the
  # evidence, so it is not a second set of paths to answer for.
  paths=$(printf '%s\n%s\n' "$tracked" "$untracked" | awk 'NF && !seen[$0]++ && $0 !~ /^\.claude\/skills\//')

  while IFS= read -r p; do
    [ -n "$p" ] || continue
    is_test_path "$p" || continue
    ok=false why=""
    while IFS= read -r r; do
      r=$(trim_end "$r")
      case $r in
        "n/a: "?*) is_blank "${r#n/a: }" || ok=true ;;
        red*)
          if [[ $r =~ $red_re ]]; then
            fresh_red "$top" "$p" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" && ok=true
          else
            [ -n "$why" ] || why="Reverted-fix line for $p is hand-written: run \`run-file prove $p <path>...\`, which writes \`red at <sha> reverting <path>...\`"
          fi ;;
      esac
    done <<EORESTS
$(rests "Reverted-fix: $p: ")
EORESTS
    [ "$ok" = true ] && continue
    gap "${why:-no Reverted-fix line for $p: run \`run-file prove $p <path>...\` or add \`Reverted-fix: $p: n/a: <reason>\`}"
  done <<EOPATHS
$paths
EOPATHS

  here=$(dirname "${BASH_SOURCE[0]}")
  dl=$(bash "$here/dropped-lines.sh" --base "$mb") \
    || ship_tooling "dropped-lines failed, so the removed blocks cannot be read"
  ids=$(jq -r '.blocks[] | (.id | if type == "string" and . != "" then . else error("a block with no id") end)' <<<"$dl" 2>/dev/null) \
    || ship_tooling "dropped-lines did not print a JSON {blocks: [{id}]} answer"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    ok=false
    while IFS= read -r r; do
      is_blank "$r" || ok=true
    done <<EORESTS
$(rests "Dropped: $id re-homed at ")
$(rests "Dropped: $id dropped on purpose: ")
EORESTS
    [ "$ok" = true ] || gap "no disposition for removed block $id: add \`Dropped: $id re-homed at <path>\` or \`Dropped: $id dropped on purpose: <why>\`"
  done <<EOIDS
$ids
EOIDS

  while IFS= read -r p; do
    case $p in *.sh) ;; *) continue ;; esac
    is_test_path "$p" && continue
    grep -Fxq -- "$p" <<<"$untracked"; rc=$?
    case $rc in
      0) added=$(cat "$top/$p") || ship_tooling "cannot read $p" ;;
      1) added=$(git -C "$top" diff --no-renames -U0 "$mb" -- "$p" | awk '/^@@/ { h = 1; next } h && /^\+/ { print substr($0, 2) }') \
           || ship_tooling "cannot read the diff of $p" ;;
      *) ship_tooling "cannot tell whether $p is untracked" ;;
    esac
    has_matcher "$added" || continue
    ok=false
    while IFS= read -r r; do
      r=$(trim_end "$r")
      case $r in "n/a: "?*) is_blank "${r#n/a: }" || ok=true ;; esac
    done <<EORESTS
$(rests "Near-miss: $p: ")
EORESTS
    [ "$ok" = true ] && continue
    miss=""
    for k in $near_kinds; do
      ok=false
      while IFS= read -r r; do
        r=$(trim_end "$r")
        case $r in
          "n/a: "?*) is_blank "${r#n/a: }" || ok=true ;;
          /*|..|../*|*/..|*/../*) ;;
          ?*) [ -f "$top/$r" ] && ok=true ;;
        esac
      done <<EORESTS
$(rests "Near-miss: $p: $k: ")
EORESTS
      [ "$ok" = true ] || miss="$miss, $k"
    done
    [ -z "$miss" ] || gap "$p has a new pattern matcher and no near-miss line for: ${miss#, }: add one line per kind, \`Near-miss: $p: <kind>: <test path>\` or \`Near-miss: $p: <kind>: n/a: <reason>\`, or \`Near-miss: $p: n/a: <reason>\` for the whole script"
  done <<EOPATHS
$paths
EOPATHS

  # Every probe line is one `run-file probe` wrote, wherever it stands.
  while IFS= read -r r; do
    r=$(trim_end "$r")
    [ -n "$r" ] || continue
    ref=${r%%: *}
    [ "$ref" != "$r" ] && [[ ${r#*: } =~ $probe_re ]] && continue
    gap "Probe: $ref is not a line \`run-file probe\` wrote: re-run it with \`run-file probe $ref <where> -- <command>...\`"
  done <<EOPROBES
$(rests "Probe: ")
EOPROBES

  # The kind of a decline is its field, never a phrase in its reason.
  while IFS= read -r r; do
    r=$(trim_end "$r")
    [ -n "$r" ] || continue
    ref=${r%%: *} reason=${r#*: }
    [ "$ref" = "$r" ] && reason=""
    case $reason in
      "judgment: "*) is_blank "${reason#judgment: }" || continue ;;
      "claim: "*)
        if ! is_blank "${reason#claim: }"; then
          ok=false
          while IFS= read -r p; do
            p=$(trim_end "$p")
            [[ $p =~ $probe_re ]] && ok=true
          done <<EORESTS
$(rests "Probe: $ref: ")
EORESTS
          [ "$ok" = true ] || gap "Declined: $ref is a claim and has no probe: run \`run-file probe $ref <where> -- <command>...\`"
          continue
        fi ;;
    esac
    gap "Declined: $ref carries neither \`claim:\` nor \`judgment:\` with a reason: write \`Declined: $ref: claim: <reason>\`, which needs \`run-file probe $ref <where> -- <command>...\`, or \`Declined: $ref: judgment: <reason>\`"
  done <<EODECLINED
$(rests "Declined: ")
EODECLINED
  refuse_gaps 4
}

# Phase 7: each reviewer the checklist row names has its stop reason and, unless
# it never reviewed, a round.
stop_reasons="cap, tree unchanged, small lane, auto-once, not reviewed"
gate_phase7() { # gate_phase7 <phase 7 row>
  local list names nm stop r ok re='^[1-9][0-9]*: [[:space:]]*[^[:space:]]'
  case $1 in *", one bounded pass each"*) ;; *) return 0 ;; esac
  list=${1#*"Reviewers: "}; list=${list%%", one bounded pass each"*}
  case $list in "" | None | None. | none | none.) return 0 ;; esac
  names=$(printf '%s\n' "$list" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  while IFS= read -r nm; do
    [[ $nm =~ ^[A-Za-z0-9][A-Za-z0-9\ ._-]*$ ]] || return 0
  done <<EONAMES
$names
EONAMES
  while IFS= read -r nm; do
    stop=$(rests "Stop: $nm: " | tail -n 1)
    stop=$(trim_end "$stop")
    case $stop in
      cap | "tree unchanged" | "small lane" | auto-once | "not reviewed") ;;
      "") gap "no Stop line for $nm: add \`Stop: $nm: <cap|tree unchanged|small lane|auto-once|not reviewed>\`" ;;
      *) gap "Stop line for $nm has reason '$stop', which is not one of $stop_reasons" ;;
    esac
    [ "$stop" = "not reviewed" ] && continue
    ok=false
    while IFS= read -r r; do
      [[ $r =~ $re ]] && ok=true
    done <<EORESTS
$(rests "Round: $nm ")
EORESTS
    [ "$ok" = true ] || gap "no Round line for $nm: add \`Round: $nm <n>: <text>\`, one per round, unless it stopped \`not reviewed\`"
  done <<EONAMES
$names
EONAMES
  refuse_gaps 7
}
# The gate a phase's close owes, run once the phase is known open and before a
# line is written, so a refusal leaves the record as it was.
close_gate() { # close_gate <phase> <its row>
  case $1 in
    4) gate_phase4 ;;
    7) gate_phase7 "$2" ;;
  esac
}

# The clean gate is a read of evidence, independent of the profile's merge opt-in.
# Only a stop of tree unchanged proves no further round would read anything new.
gate_clean() { # gate_clean <ci-file|->, with file and head already parsed
  local ci profile_path profile reviewers verifications lines record_dir rec rhead='' rverdict='' rgates=null drafts='[]' p answer
  [ -r "$file" ] || ship_tooling "cannot read Run file at $file"
  # Fenced examples cannot authorize a merge.
  lines=$(awk "$SHIP_AWK_FENCE"'
    { if (!ship_fence($0)) print }
    END { if (_fenced) exit 1 }' "$file") || ship_tooling "cannot read Run file evidence at $file"
  if [ "$1" = - ]; then ci=$(cat) || ship_tooling "cannot read CI answer"
  else
    [ -f "$1" ] && [ -r "$1" ] || ship_tooling "cannot read CI answer at $1"
    ci=$(cat "$1") || ship_tooling "cannot read CI answer at $1"
  fi
  jq -e 'type == "object" and (.status | IN("green", "no-checks", "conflict", "checks-failed", "timeout", "pending"))
    and (.head_sha | type == "string" and test("^[0-9a-f]{7,64}$"))
    and (.checks | type == "array" and all(.[]; type == "object" and (.name | type == "string") and (.status | type == "string")))' \
    >/dev/null 2>&1 <<<"$ci" || ship_tooling "CI answer is not a valid ci-wait object"
  profile_path=$(ship_profile_path) || ship_tooling "cannot locate ship profile"
  [ -f "$profile_path" ] && [ -r "$profile_path" ] || ship_tooling "cannot read ship profile at $profile_path"
  profile=$(awk "$SHIP_AWK_FENCE"'
    { if (!ship_fence($0)) print }
    END { if (_fenced) exit 1 }' "$profile_path") || ship_tooling "cannot read profile evidence at $profile_path"
  local names legs no_checks=false
  ship_no_checks_expected "$profile"
  case $? in 0) no_checks=true ;; 1) ;; *) ship_tooling "cannot read profile no-checks policy" ;; esac
  names=$(ship_profile_legs "$profile") || ship_tooling "cannot read profile CI legs"
  legs=$(jq -Rn '[inputs | select(. != "")]' <<<"$names") || ship_tooling "cannot read profile CI legs"
  reviewers=$(ship_reviewers "$profile") || ship_tooling "cannot read profile reviewers"
  # Keep the association beside its heading, with the profile's exact field.
  verifications=$(awk '
    /^## / { f = ($0 ~ /^## Verification[ \t\r]*$/); next }
    f && /^### / { sub(/^### /, ""); sub(/[ \t\r]+$/, ""); print "name\t" $0; next }
    f && /^Also proven by CI: / { sub(/^Also proven by CI: /, ""); sub(/[ \t\r]+$/, ""); print "leg\t" $0 }
    ' <<<"$profile" | jq -Rs 'reduce (split("\n")[] | select(. != "") | split("\t")) as $r ([];
      if $r[0] == "name" then . + [{name: $r[1], leg: null}]
      elif length > 0 then .[-1].leg = $r[1] else . end)') || ship_tooling "cannot read profile verifications"
  rec=$(awk '/^## / { f = ($0 ~ /^## Local gate[ \t\r]*$/) }
    f && /^- [0-9][0-9]:[0-9][0-9] [0-9a-f]+ [^ ]+( .*)?$/' <<<"$lines" | tail -n 1) \
    || ship_tooling "cannot read local gate record"
  if [ -n "$rec" ]; then
    rec=${rec#- ??:?? }; rhead=${rec%% *}; rec=${rec#"$rhead"}; rec=${rec# }
    rverdict=${rec%% *}; rgates=${rec#"$rverdict"}; rgates=${rgates# }; rgates=${rgates:-null}
    jq -e ' . == null or (type == "object" and all(.[]; type == "string"))' \
      >/dev/null 2>&1 <<<"$rgates" || ship_tooling "cannot read recorded local gate results"
  fi
  record_dir=$(dirname "$file") || ship_tooling "cannot locate Run file drafts"
  [ -r "$record_dir" ] && [ -x "$record_dir" ] || ship_tooling "cannot read draft directory at $record_dir"
  for p in "$record_dir"/defect-*.md "$record_dir"/tracker-*.md; do
    [ -f "$p" ] || continue
    case $p in *.base.md) continue ;; esac
    drafts=$(jq -c --arg p "${p##*/}" '. + [$p]' <<<"$drafts") || ship_tooling "cannot read draft paths"
  done
  answer=$(jq -n --arg text "$lines" --arg head "$head" --arg rhead "$rhead" --arg verdict "$rverdict" \
    --argjson gates "$rgates" --argjson ci "$ci" --argjson legs "$legs" --argjson reviewers "$reviewers" \
    --argjson verifications "$verifications" --argjson drafts "$drafts" --argjson no_checks "$no_checks" '
    def samehead($a; $b): $a != "" and $b != "" and (($a | startswith($b)) or ($b | startswith($a)));
    def named($leg): .name == $leg or (.name | startswith($leg + " ("));
    ($text | split("\n")) as $lines
    | ($lines | map(sub("^- "; "") | sub("[ \\t\\r]+$"; ""))) as $evidence
    | def rests($prefix): [$evidence[] | select(startswith($prefix)) | ltrimstr($prefix)];
      def stop($name): (rests("Stop: " + $name + ": ") | last // "missing stop");
      def reviewed($name): (rests("Round: " + $name + " ") | last // "") | test("^[1-9][0-9]*: [[:space:]]*[^[:space:]]");
      def settled($name): stop($name) == "tree unchanged" and reviewed($name);
      # Capped with findings: Cap: spent on a tree-changing last round, which a
      # docs-only end before the cap, also `Stop: cap`, is not. The dispositioned
      # rounds must read 1..Cap exactly: a gapped, blank or overrun record is no
      # evidence of a spent cap.
      def cap_spent($name): stop($name) == "cap"
        and ([$reviewers[] | select(.name == $name) | .cap | numbers] as $cap
             | $cap != [] and ([rests("Round: " + $name + " ")[] | select(test("^[1-9][0-9]*: [[:space:]]*[^[:space:]]"))
                 | capture("^(?<n>[1-9][0-9]*):").n | tonumber] | unique) == [range(1; $cap[0] + 1)]);
      def owes_fallback($name): stop($name) == "not reviewed" or cap_spent($name);
      def greenleg($leg): [$ci.checks[] | select(named($leg))] as $checks
        | ($checks | length) > 0 and all($checks[]; .status == "success") and samehead($ci.head_sha; $head) and $ci.status == "green";
      # Names may contain colons, so a result is cut by its full literal prefix.
      def result($name):
        (reduce $lines[] as $line ({inside: false, rows: []};
          if $line | startswith("## ") then .inside = ($line | test("^## Verification results[ \\t\\r]*$"))
          elif .inside and ($line | startswith("- " + $name + ": ")) then .rows += [$line | ltrimstr("- " + $name + ": ")]
          else . end) | .rows | last // "missing") | split(": ")[0];
      [if $rhead == "" then "local gate: no record"
       elif samehead($rhead; $head) | not then "local gate: recorded head differs" else empty end,
       if $verdict != "" and $verdict != "pass" then "local gate: " + $verdict else empty end,
       if $gates == null or $gates == {} then "local gate: missing gate results"
       else $gates | to_entries[] | select(.value != "pass" and .value != "deferred-to-ci") | "local gate " + .key + ": " + .value end,
       if $gates != null and $gates != {} and $gates.secrets == null then "local gate: missing secrets result" else empty end,
       # A deferral names no leg, so a check outside Legs: that is not green may be the one covering it.
       ((($gates // {}) | to_entries[] | select(.value == "deferred-to-ci")) as $g
         | ($ci.checks[] | . as $c | select(any($legs[]; . as $l | $c | named($l)) | not) | select(.status != "success"))
         | "local gate " + $g.key + ": deferred-to-ci while CI " + .name + ": " + .status),
       if samehead($ci.head_sha; $head) | not then "CI: head differs" else empty end,
       if $ci.status != "green" and ($ci.status != "no-checks" or ($no_checks | not)) then "CI: " + $ci.status else empty end,
       ($legs[] as $leg | [$ci.checks[] | select(named($leg))] as $checks
         | if $checks == [] then "CI " + $leg + ": missing"
           else $checks[] | select(.status != "success") | "CI " + .name + ": " + .status end),
       ($verifications[] as $v | result($v.name) as $status
         | if $status == "pass" or $status == "n/a" then empty
           elif $status == "deferred-to-ci" then
             if $v.leg != null and ($legs | index($v.leg)) != null and greenleg($v.leg) then empty
             else "verification " + $v.name + ": no associated green CI leg" end
           else "verification " + $v.name + ": " + $status end),
       ($reviewers[] as $r
         | if $r.fallback_for != null and (owes_fallback($r.fallback_for) | not) then empty
           elif settled($r.name) then empty
           elif owes_fallback($r.name) and any($reviewers[]; .fallback_for == $r.name and settled(.name)) then empty
           elif stop($r.name) == "tree unchanged" then "reviewer " + $r.name + ": no dispositioned round"
           else "reviewer " + $r.name + ": " + stop($r.name) end),
       (rests("Override: ")[] | select(. != "" and . != "none" and . != "None.") | "override needed: " + .),
       (rests("Ship-defect: ")[] | select(. != "" and . != "none" and . != "None.") | "Ship defect: " + .),
       ($drafts[] | if startswith("defect-") then "Ship defect draft: " + . else "Tracker draft: " + . end)
      ] as $held | {clean: ($held == []), held_by: $held}
    ') || ship_tooling "cannot evaluate clean gate evidence"
  printf '%s\n' "$answer"
  jq -e '.clean' >/dev/null <<<"$answer"
  case $? in 0) return 0 ;; 1) ;; *) ship_tooling "cannot read clean gate verdict" ;; esac
  jq -r '.held_by[]' <<<"$answer" | tail -n 40 >&2
  return 1
}

case $verb in
init)
  ship_args "$usage" arg "$@"
  id=$1; shift
  scratchpad="" tripwires=None verifications="None applicable" reviewers=none legs=None
  rebuild=false states="" given="" from_profile=false profile_path="" profile_names=""
  while [ $# -gt 0 ]; do
    case $1 in
      --scratchpad)    [ $# -ge 2 ] && [ -n "$2" ] || ship_tooling "--scratchpad needs a directory"; case $2 in -*) ship_tooling "$usage" ;; esac; scratchpad=$2; shift 2 ;;
      --tripwires)     [ $# -ge 2 ] || ship_tooling "--tripwires needs a value"; case $2 in -*) ship_tooling "$usage" ;; esac; tripwires=$2; given="$given tripwires"; shift 2 ;;
      --verifications) [ $# -ge 2 ] || ship_tooling "--verifications needs a value"; case $2 in -*) ship_tooling "$usage" ;; esac; verifications=$2; given="$given verifications"; shift 2 ;;
      --reviewers)     [ $# -ge 2 ] || ship_tooling "--reviewers needs a value"; case $2 in -*) ship_tooling "$usage" ;; esac; reviewers=$2; given="$given reviewers"; shift 2 ;;
      --legs)          [ $# -ge 2 ] || ship_tooling "--legs needs a value"; case $2 in -*) ship_tooling "$usage" ;; esac; legs=$2; given="$given legs"; shift 2 ;;
      --state)         [ $# -ge 2 ] || ship_tooling "--state needs <n>=<spec>"; case $2 in -*) ship_tooling "$usage" ;; esac; states="$states$2
"; shift 2 ;;
      # The path is optional, so a value that is a flag is the next flag, not a path.
      --from-profile)  from_profile=true
                       if [ $# -ge 2 ]; then case $2 in -*) shift ;; *) profile_path=$2; shift 2 ;; esac; else shift; fi ;;
      --rebuild)       rebuild=true; shift ;;
      *) ship_tooling "unknown flag: $1" ;;
    esac
  done
  # The profile fills the slots a flag did not: the flag is the run's own word
  # for this run, the profile the repo's standing one.
  if [ "$from_profile" = true ]; then
    [ -n "$profile_path" ] || profile_path=$(ship_profile_path) || ship_tooling "not inside a git checkout: pass --from-profile <path>"
    [ -f "$profile_path" ] && [ -r "$profile_path" ] || ship_tooling "no readable ship profile at $profile_path"
    profile=$(cat "$profile_path")
    pf=$(awk '/^## / { f = ($0 ~ /^## Local gate[ \t\r]*$/); next }
              f && /^Tripwires:/ { sub(/^Tripwires:[ \t]*/, ""); sub(/[ \t\r]+$/, ""); print; exit }' <<<"$profile")
    case " $given " in *" tripwires "*) ;; *) [ -z "$pf" ] || tripwires=$pf ;; esac
    # A heading is a name whatever it holds, so these names skip the name-shape
    # test a hand-passed list gets; `=` is the one character `--result` cannot
    # take in one.
    pn=$(profile_headings Verification "$profile")
    case " $given " in
      *" verifications "*) ;;
      *) if [ -n "$pn" ]; then
           case $pn in *=*) ship_tooling "a Verification heading cannot carry '=': --result splits the name from the word on it" ;; esac
           verifications=$(join_names <<<"$pn") profile_names=$pn
         fi ;;
    esac
    pf=$(profile_headings Reviewers "$profile" | join_names)
    case " $given " in *" reviewers "*) ;; *) [ -z "$pf" ] || reviewers=$pf ;; esac
    pf=$(ship_profile_legs "$profile" | join_names)
    case " $given " in *" legs "*) ;; *) legs=${pf:-None.} ;; esac
  fi
  resolve_root
  reject_stamp_tail --tripwires "$tripwires"
  reject_stamp_tail --verifications "$verifications"
  reject_stamp_tail --reviewers "$reviewers"
  reject_stamp_tail --legs "$legs"
  file="$root/ship-$id/run.md"
  scratch="$root/scratch-$id"
  # A run that states no phase is a run starting, and a run starting is in
  # phase 0: opening it here is the call the run would make next.
  auto=false
  [ -n "$states" ] || { states="0=open
"; auto=true; }
  # Every state is validated before anything is written, so a bad spec leaves
  # no half-built file behind.
  state_usage='--state takes <0-9>=open|done|done:<HH:MM→HH:MM>|skipped:<reason>'
  open_states=0 stated="" opened_at=""
  while IFS= read -r st; do
    [ -n "$st" ] || continue
    # One state per phase: two for the same one would apply in order and leave
    # a rebuild contradicting its own recovered state.
    case " $stated " in *" ${st%%=*} "*) ship_tooling "--state names phase ${st%%=*} twice" ;; esac
    stated="$stated${st%%=*} "
    case $st in
      [0-9]=open) open_states=$((open_states + 1)); opened_at="$opened_at${st%%=*} " ;;
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
  # The 3/4 overlap is the one pair `open 4` allows, so it is the one a rebuild
  # may record.
  [ "$open_states" -le 1 ] || [ "$opened_at" = "3 4 " ] || [ "$opened_at" = "4 3 " ] \
    || ship_fail "a Run file holds one open phase, or 3 and 4 together; $open_states were given"
  # The rebuild path: a Run file a subagent overwrote is rebuilt in place, so
  # the guard that keeps a resumed run from wiping its own record steps aside
  # only when the caller says so.
  [ -e "$file" ] && [ "$rebuild" = false ] && ship_fail "Run file exists: $file: a resumed run reads that record, and a new run of an issue whose earlier run stopped replaces it with \`run-file init $id --rebuild\`"
  mkdir -p "$root/ship-$id" "$scratch" || ship_tooling "cannot create $root/ship-$id and $scratch"
  items=$(checklist "$tripwires" "$verifications" "$reviewers" "$legs")
  if [ -n "$profile_names" ]; then names=$profile_names; else names=$(verif_names "$verifications"); fi
  { printf '# ship run · %s\n\n' "$id"
    printf '%s\n' "$items" | sed 's/^/- [ ] /'
    if [ -n "$names" ]; then
      printf '\n## Verification results\n\n'
      printf '%s\n' "$names" | sed 's/$/: pending/; s/^/- /'
    fi
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
  printf '%s\n' "$items" | jq -Rs --arg f "$file" --arg i "$id" --arg s "$scratch" --argjson auto "$auto" \
    '{run_file: $f, id: $i, scratch: $s, items: (split("\n") | map(select(. != "")))}
     + if $auto then {opened: 0, mirror: "in_progress"} else {} end'
  ;;
open)
  phase_arg "${1:-}"; n=$1; shift
  parse_file "$@"
  no_extra
  take_row "$n"
  opens=$(open_phases | tr '\n' ' ')
  if [ -n "$opens" ]; then
    case " $opens " in *" $n "*) ship_fail "phase $n is already open" ;; esac
    # Phase 4 begins while phase 3's verifications run, so it is the one open
    # admitted over an open phase, and only over phase 3 alone.
    [ "$n $opens" = "4 3 " ] || ship_fail "phase ${opens%% *} is open; close it before opening $n"
  fi
  gap=$(unflipped "$n")
  [ -n "$gap" ] && refuse_gap open "$gap" "$n"
  new=$(render open "$(item "$line")" "$(date -u +%H:%M)")
  write_line "$lineno" "$new"
  flip_json open "$new" in_progress
  ;;
next)
  phase_arg "${1:-}"; n=$1; shift
  parse_file "$@"
  no_extra
  take_row "$n"
  next_line=$line next_lineno=$lineno
  opens=$(open_phases | tr '\n' ' ')
  case $opens in
    '') ship_fail "no phase is open; use \`run-file open $n\`" ;;
    *" "?*) ship_fail "phases ${opens%% *} and $(printf '%s' "${opens#* }" | tr -d ' ') are both open; close ${opens%% *} first with \`run-file close ${opens%% *}\`, then retry \`run-file next $n\`" ;;
  esac
  c=${opens% }
  [ "$c" = "$n" ] && ship_fail "phase $n is already open"
  gap=$(unflipped "$n")
  [ -n "$gap" ] && refuse_gap next "$gap" "$n"
  [ "$c" = 3 ] && refuse_pending
  take_row "$c"
  close_gate "$c" "$line"
  closed=$(closed_line "$line")
  opened=$(render open "$(item "$next_line")" "$(date -u +%H:%M)")
  write_line "$lineno" "$closed"
  write_line "$next_lineno" "$opened"
  jq -n --arg f "$file" --argjson c "$c" --arg cl "$closed" --argjson n "$n" --arg ol "$opened" \
    '{run_file: $f, closed: {phase: $c, line: $cl, mirror: "completed"}, opened: {phase: $n, line: $ol, mirror: "in_progress"}}'
  ;;
close)
  phase_arg "${1:-}"; n=$1; shift
  parse_file "$@"
  [ -z "$head" ] || ship_tooling "$usage"
  [ -z "$results" ] || [ "$n" = 3 ] || ship_tooling "--result belongs to close 3"
  take_row "$n"
  check_results
  if ! is_open "$line"; then
    # Never opened is a different mistake from already closed: the way forward
    # is the open it skipped, so the refusal says so.
    case $line in
      "- [ ] "*" in_progress ("*) ;;
      "- [ ] "*) ship_fail "phase $n was never opened; run \`run-file open $n\` first, then close it" ;;
    esac
    ship_fail "phase $n is not open"
  fi
  # The results land before the pending check, so a close refused for one
  # verification still keeps the ones it was given.
  [ "$n" = 3 ] && { record_results; refuse_pending; }
  close_gate "$n" "$line"
  new=$(closed_line "$line")
  write_line "$lineno" "$new"
  flip_json closed "$new" completed
  ;;
skip)
  phase_arg "${1:-}"; n=$1; shift
  ship_args "$usage" arg "$@"
  reason=$1; shift
  parse_file "$@"
  no_extra
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
grade)
  word=${1:-}
  case $word in patch | minor | breaking) shift ;; *) ship_tooling "$usage" ;; esac
  parse_file "$@"
  no_extra
  # The line lives under `## Grade`: a call replaces the one before it, and a
  # `Grade:` line in another section is not this one. The heading and line shapes
  # are the ones `ship_recorded_grade` reads: blanks after the heading, an optional
  # `- ` before the line.
  if awk '/^## / { f = ($0 ~ /^## Grade[ \t\r]*$/) } f && /^(- )?Grade: / { found = 1 } END { exit !found }' "$file"; then
    if ! { repl="Grade: $word" awk '/^## / { f = ($0 ~ /^## Grade[ \t\r]*$/) }
        f && /^(- )?Grade: / { if (!done) print ENVIRON["repl"]; done = 1; next } { print }' "$file" > "$file.t" \
        && mv "$file.t" "$file"; }; then
      rm -f "$file.t"; ship_tooling "cannot write $file"
    fi
  else
    append_to_section "## Grade" "Grade: $word"
  fi
  jq -n --arg f "$file" --arg g "$word" '{run_file: $f, grade: $g}'
  ;;
gate)
  sub=${1:-}
  case $sub in record | read | clean) shift ;; *) ship_tooling "$usage" ;; esac
  if [ "$sub" = record ] || [ "$sub" = clean ]; then
    # `-` is the verdict on stdin, so it is the one dash-led value a positional may be.
    src=${1:-}
    case $src in '' | -?*) ship_tooling "$usage" ;; esac
    shift
  fi
  parse_file "$@"
  [ -z "$results" ] || ship_tooling "$usage"
  if [ "$sub" = clean ]; then
    [ -n "$head" ] || ship_tooling "$usage"
    [[ $head =~ ^[0-9a-f]{7,64}$ ]] || ship_tooling "--head takes a full or abbreviated commit sha"
    gate_clean "$src"
    exit $?
  elif [ "$sub" = read ]; then
    [ -n "$head" ] || ship_tooling "$usage"
    [[ $head =~ ^[0-9a-f]{7,64}$ ]] || ship_tooling "--head takes a full or abbreviated commit sha"
    rec=$(awk '/^## /{ f = ($0 ~ /^## Local gate[ \t\r]*$/) } f && /^- [0-9][0-9]:[0-9][0-9] [0-9a-f]+ [^ ]+( .*)?$/' "$file" | tail -n 1)
    [ -n "$rec" ] || ship_fail "no gate record in $file"
    # The line is `- HH:MM <sha> <verdict>[ <gates JSON>]`, and the JSON may hold
    # spaces, so it is cut by position rather than split into words.
    rec=${rec#- ??:?? }
    rhead=${rec%% *}; rec=${rec#"$rhead"}; rec=${rec# }
    rverdict=${rec%% *}; rgates=${rec#"$rverdict"}; rgates=${rgates# }
    [ -z "$rgates" ] || jq -e . >/dev/null 2>&1 <<<"$rgates" \
      || ship_fail "the gates on the last gate record in $file are not JSON"
    # A prefix either way is the same commit: the run records the full sha, and a
    # caller may hold the abbreviated one.
    current=false
    case $rhead in "$head"*) current=true ;; esac
    case $head in "$rhead"*) current=true ;; esac
    behind=$(git rev-list --count "$rhead..$head" 2>/dev/null) || behind=null
    jq -n --arg f "$file" --arg v "$rverdict" --arg h "$rhead" --argjson g "${rgates:-null}" --argjson c "$current" --argjson b "${behind:-null}" \
      '{run_file: $f, verdict: $v, head: $h, gates: $g, current: $c, behind: $b}'
  else
    if [ "$src" = - ]; then body=$(cat)
    else [ -f "$src" ] && [ -r "$src" ] || ship_tooling "cannot read the gate verdict at $src"; body=$(cat "$src")
    fi
    verdict=$(jq -rs 'map(select(type == "object" and (.verdict | type) == "string")) | last | .verdict // empty' <<<"$body" 2>/dev/null) \
      || ship_tooling "the gate verdict is not JSON"
    [ -n "$verdict" ] || ship_tooling "the gate verdict has no verdict key"
    # The gates ride on the line so the merge gate can cite them without
    # re-running the gate; only an object counts, and `jq -c` keeps it one line.
    gates=$(jq -rsc 'map(select(type == "object" and (.verdict | type) == "string")) | last | (.gates | select(type == "object")) // empty' <<<"$body" 2>/dev/null)
    [[ $verdict =~ ^[A-Za-z0-9_-]+$ ]] || ship_tooling "the gate's verdict '$verdict' is not one word"
    [ -n "$head" ] || head=$(git rev-parse HEAD 2>/dev/null) || ship_tooling "no --head given and not inside a git checkout"
    [[ $head =~ ^[0-9a-f]{7,64}$ ]] || ship_tooling "--head takes a full or abbreviated commit sha"
    append_to_section "## Local gate" "- $(date -u +%H:%M) $head $verdict${gates:+ $gates}"
    jq -n --arg f "$file" --arg h "$head" --arg v "$verdict" '{run_file: $f, head: $h, verdict: $v}'
  fi
  ;;
timing)
  parse_file "$@"
  no_extra
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
      skipped = (s ~ / skipped \(.*\)$/)
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
      phase[p] = skipped ? "skipped" : (found ? total : "unverified")
    }
    END {
      for (p in seen) printf "phase\t%s\t%s\n", p, phase[p]
      stp = (0 in first && 6 in last) ? agg(first[0], last[6]) : "unverified"
      ptg = (6 in last && 8 in last) ? agg(last[6], last[8]) : "unverified"
      printf "agg\tstart_to_pr\t%s\n", stp
      printf "agg\tpr_to_gate\t%s\n", ptg
    }
  ' "$file" | jq -Rs --arg f "$file" '
    def num: if . == "unverified" or . == "skipped" then . else tonumber end;
    def m: if . == "unverified" or . == "skipped" then . else tostring + "m" end;
    [split("\n")[] | select(. != "") | split("\t")] as $rows
    | ($rows | map(select(.[0] == "phase") | {(.[1]): (.[2] | num)}) | add // {}) as $phases
    | ($rows | map(select(.[0] == "agg") | {(.[1]): (.[2] | num)}) | add) as $agg
    | {run_file: $f, start_to_pr: $agg.start_to_pr, pr_to_gate: $agg.pr_to_gate, phases: $phases,
       row: ("start→PR \($agg.start_to_pr | m) · PR→gate \($agg.pr_to_gate | m) · per phase: "
             + ([range(0; 9) | "\(.) \($phases[tostring] // "unverified")"] | join(" · ")))}'
  ;;
prove)
  # The positionals end where <where> begins.
  args=()
  while [ $# -gt 0 ]; do case $1 in --*) break ;; esac; args+=("$1"); shift; done
  [ ${#args[@]} -ge 2 ] || ship_tooling "$usage"
  parse_file "$@"
  no_extra
  # `close 4` looks the line up by the path git diff prints, top-relative with
  # no leading `./`, so the line is written in that form. revert-red takes the
  # paths as typed: a `./` can be what keeps a name like `:x.sh` literal.
  recorded=("${args[@]}")
  for i in "${!recorded[@]}"; do
    while [ "${recorded[i]#./}" != "${recorded[i]}" ]; do recorded[i]=${recorded[i]#./}; done
  done
  # The line lists the paths space-separated, and `close 4` splits them back on
  # that space.
  for p in "${args[@]:1}"; do
    case $p in *[[:space:]]*) ship_tooling "prove cannot record '$p': a reverted path holding whitespace cannot be read back from its line" ;; esac
  done
  head=$(git rev-parse HEAD 2>/dev/null) || ship_tooling "prove records the head revert-red runs on: not inside a git checkout with a commit"
  answer=$(bash "$(dirname "${BASH_SOURCE[0]}")/revert-red.sh" "${args[@]}")
  rc=$?
  [ "$rc" -eq 0 ] || { printf '%s\n' "$answer"; exit "$rc"; }
  out=$(jq -c --arg f "$file" --arg h "$head" '. + {run_file: $f, head: $h}' <<<"$answer" 2>/dev/null) \
    || ship_tooling "revert-red did not print a JSON verdict"
  append_to_section "## Evidence" "Reverted-fix: ${recorded[0]}: red at $head reverting ${recorded[*]:1}"
  jq . <<<"$out"
  ;;
probe)
  ref=${1:-}
  case $ref in '' | -*) ship_tooling "$usage" ;; esac
  shift
  # A decline's ref is the text before its first `: `, so a ref holding one, or
  # a newline, names no decline.
  case $ref in *": "* | *$'\n'*) ship_tooling "a probe's ref is the text before a decline's first ': ', so it cannot hold ': ' or a newline" ;; esac
  where=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do where+=("$1"); shift; done
  [ "${1:-}" = -- ] && [ $# -ge 2 ] || ship_tooling "$usage"
  shift
  # An empty first word runs nothing, and its line could never be read back.
  [ -n "$1" ] || ship_tooling "probe needs a command: its first word is empty"
  parse_file ${where[@]+"${where[@]}"}
  no_extra
  top=$(git rev-parse --show-toplevel 2>/dev/null) || ship_tooling "probe runs its command at the checkout top: not inside a git checkout"
  head=$(git -C "$top" rev-parse HEAD 2>/dev/null) || ship_tooling "probe records the head it ran on: $top has no commit"
  # argv, never a shell string, and no terminal to wait on.
  output=$(cd "$top" && "$@" </dev/null 2>&1)
  rc=$?
  # Trimmed both ends: `close 4` reads the line's tail from its first non-space.
  last=$(printf '%s\n' "$output" | awk '{ sub(/\r$/, "") } NF { l = $0 } END { sub(/^[ \t]+/, "", l); sub(/[ \t]+$/, "", l); print l }') \
    || ship_tooling "cannot read the last line of the probe's output"
  [ -n "$last" ] || last="(no output)"
  # Shell-quoted, so each argument's boundary survives on the one line and the
  # command can be re-run as recorded.
  cmd=$(printf '%q ' "$@")
  cmd=${cmd% }
  append_to_section "## Evidence" "Probe: $ref: $cmd => exit $rc at $head: $last"
  jq -n --arg f "$file" --arg r "$ref" --arg c "$cmd" --argjson x "$rc" --arg h "$head" --arg l "$last" \
    '{run_file: $f, ref: $r, command: $c, exit: $x, head: $h, last: $l}'
  ;;
*)
  ship_tooling "$usage"
  ;;
esac
