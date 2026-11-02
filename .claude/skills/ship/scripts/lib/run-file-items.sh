#!/usr/bin/env bash
# Run-file mechanic: the checklist, the item and line helpers, and the recorded results. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154,SC2034 # shares the globals of run-file.sh (file, head, verb, usage)

# The recovery both refusals of a missing record carry, rather than prose a
# compacted run may no longer hold.
rebuild_hint="rebuild it with \`run-file init <issue> --rebuild [--scratchpad <dir>]\`, re-passing the --tripwires, --verifications, --reviewers, --legs and --criteria the run began with and one --state per phase the transcript accounts for (open for the one that was running, or 3 and 4 both open if they overlapped, no invented range), then log what was lost in the deviations log"

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
  grep -Eq '\([0-9]{2}:[0-9]{2}→([0-9]{2}:[0-9]{2}(\+1d)?)?\)$' <<<"$1"
  case $? in
    0) ship_tooling "that reason leaves the phase line ending in one of the Run file's own state shapes; reword it" ;;
    1) ;;
    *) ship_tooling "cannot check that reason for the Run file's own state shapes: grep failed" ;;
  esac
}
reject_stamp_tail() { # reject_stamp_tail <flag> <value>
  grep -Eq '\([0-9]{2}:[0-9]{2}→[0-9]{2}:[0-9]{2}(\+1d)?\)|in_progress \([0-9]{2}:[0-9]{2}→\)| skipped \(' <<<"$2"
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
# Every phase below <n> whose row is not `[x]`, lowest first: closed, rebuilt
# `done` and skipped all tick it, so every phase that ran or was skipped passes.
# Only the first row per phase counts, as in `open_phases`. An open phase is not a gap:
# it is running, and the callers that let one stand while <n> opens (the 3/4
# overlap, `next`, which closes it) have already decided that.
unflipped() { # unflipped <n>
  awk -v n="$1" '/^- \[.\] [0-9][0-9]* · / {
         p = substr($0, 7); sub(/ .*/, "", p)
         if (p in seen) next
         seen[p] = 1
         if ($0 ~ / in_progress \([0-9][0-9]:[0-9][0-9]→\)$/) next
         if (p + 0 < n + 0 && $0 !~ /^- \[x\] /) print p
       }' "$file"
}
# The refusal for a phase opened over an earlier one never flipped: that one
# stays unticked and `unverified` on the Timing item for a phase that may have
# run. For a phase that ran, the recovery is open-then-close: `close` needs it
# open, and `skip` would record it as not run. A phase that did not run is
# skipped.
# <verb> is the recovery flip: `open`, or `next` where another phase is open and
# `open` over it would be refused. The retry is `open` either way: once the gap
# is closed no phase is open, and `next` would act as `open`.
refuse_gap() { # refuse_gap <verb> <gaps, one per line> <n>
  local gap=${2%%$'\n'*} also=''
  [ "$gap" = "$2" ] || also=" Also passed over: $(printf '%s' "${2#*$'\n'}" | paste -sd, - | sed 's/,/, /g')."
  ship_fail "phase $gap is neither closed nor skipped. If it ran: \`run-file $1 $gap\`, \`run-file close $gap\`, then note in the deviations log that its stamp is the recovery time, so its minutes and any start→PR or PR→gate figure it bounds reflect the recovery, plus when it really ran if the transcript holds that. If it did not run: \`run-file skip $gap <reason>\`. Then retry \`run-file open $3\`, which names the next such phase if any.$also"
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
  repl=$2 awk -v ln="$1" 'NR == ln { print ENVIRON["repl"]; next } { print }' "$file" > "$file.t.$$" \
    && mv "$file.t.$$" "$file" || { rm -f "$file.t.$$"; ship_tooling "cannot write $file"; }
}
phase_arg() { # phase_arg <value>: the phase number; a flag here is a usage error
  case ${1:-} in -*) ship_tooling "$usage" ;; esac
  case ${1:-} in '' | *[!0-9]*) ship_tooling "$usage" ;; esac
}
# The record root: the --scratchpad named, else `ship_record_root`. Sets `root`
# in this shell, because a refusal inside a `$( )` would exit only the
# substitution.
resolve_root() {
  if [ -n "$scratchpad" ]; then root=${scratchpad%/}; return; fi
  root=$(ship_record_root)
  case $? in
    0) ;;
    2) ship_tooling "cksum failed: pass --scratchpad <dir>" ;;
    *) ship_tooling "not inside a git checkout: pass --scratchpad <dir>" ;;
  esac
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
    repl=$2 awk -v ln="$last" '{ print } NR == ln { print ENVIRON["repl"] }' "$file" > "$file.t.$$" \
      && mv "$file.t.$$" "$file" || { rm -f "$file.t.$$"; ship_tooling "cannot write $file"; }
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
# A recorded word, `close 3` stamping it with the head it was recorded on.
result_re="($result_words)( at [0-9a-f]+)?"
# The section's entries, `- <name>: <what>` each. A note after the word may
# itself end in `: pending`, so pending is matched on the whole line.
results_lines() { awk '/^## /{ f = ($0 == "## Verification results") } f && /^- [^:]+: /' "$file"; }
# A name may hold a colon, so an entry is pending when the line ends `: pending`
# with no result word as a field before it (`- name: pass: waiting: pending` is a
# result whose note ends so), not when a colon-free name precedes it.
pending_verifs() {
  results_lines | w="$result_re" awk '
    $0 ~ /: pending$/ && $0 !~ ("^- .*: (" ENVIRON["w"] ")(:|$)") { sub(/^- /, ""); sub(/: pending$/, ""); print }'
}
# An awk test for the results entry of ENVIRON["nm"]: a profile name may hold a
# colon, so the entry is its prefix followed by `pending` or a result word
# (ENVIRON["w"]), which keeps `lint` off `- lint: strict: pending`.
entry_of='index($0, "- " ENVIRON["nm"] ": ") == 1 && substr($0, length(ENVIRON["nm"]) + 5) ~ ("^(pending|" ENVIRON["w"] ")(: |$)")'
# One `--result <name>=<word>[: <note>]`, checked against the names `init`
# recorded, then the checkout profile's Verification headings; sets rname,
# rword, rnote and radd, true for a name only the profile holds.
parse_result() { # parse_result <arg>
  local rest path
  case $1 in *=*) ;; *) ship_tooling "--result takes <name>=<word>[: <note>], word one of ${result_words//|/, }" ;; esac
  rname=${1%%=*}; rest=${1#*=}; rword=${rest%%:*}
  case $rest in *:*) rnote=${rest#*:}; rnote=${rnote# } ;; *) rnote="" ;; esac
  case $rword in
    pass | fail | deferred-to-ci | unavailable | unexercised | n/a) ;;
    *) ship_tooling "--result word '$rword' is not one of ${result_words//|/, }" ;;
  esac
  [ "${rnote%%$'\n'*}" = "$rnote" ] || ship_tooling "a result note is one line"
  radd=false
  results_lines | nm=$rname w="$result_re" awk "$entry_of { f = 1 } END { exit !f }" && return 0
  # `gate clean` judges the profile's headings, not init's list, so a heading
  # the PR adds after init owes a result that only this fallback can record
  # (#621).
  if path=$(ship_profile_path) && [ -r "$path" ] &&
    profile_headings Verification "$(cat "$path")" | nm=$rname awk '$0 == ENVIRON["nm"] { f = 1 } END { exit !f }'; then
    refuse_bad_headings "$rname"; radd=true; return 0
  fi
  ship_tooling "--result names '$rname', which neither the Run file's Verification results nor the profile's Verification headings name"
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
  local r text at ran
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    parse_result "$r"
    # The head the verification ran at, where `ran` marked it, outranks the head
    # the record is written on; a `--head` is the caller's own word and outranks
    # both (#933).
    at=$head
    if [ -z "$head_given" ]; then
      ran=$(rests "Ran: $rname at " | tail -n 1)
      [[ $ran =~ ^[0-9a-f]{7,64}$ ]] && at=$ran
    fi
    text="$rword at $at"; [ -z "$rnote" ] || text="$text: $rnote"
    if [ "$radd" = true ]; then
      # After the section's last line, or a new section at the end when init
      # named no Verification and so wrote none.
      nm=$rname tx=$text awk 'NR == FNR { if (/^## /) f = ($0 == "## Verification results"); if (f && (/^## / || /^- /)) last = FNR; next }
        { print } FNR == last { print "- " ENVIRON["nm"] ": " ENVIRON["tx"] }
        END { if (!last) printf "\n## Verification results\n\n- %s: %s\n", ENVIRON["nm"], ENVIRON["tx"] }' \
        "$file" "$file" > "$file.t.$$"
    else
      nm=$rname tx=$text w="$result_re" awk '/^## /{ f = ($0 == "## Verification results") }
        f && '"$entry_of"' { print "- " ENVIRON["nm"] ": " ENVIRON["tx"]; next } { print }' \
        "$file" > "$file.t.$$"
    fi && mv "$file.t.$$" "$file" || { rm -f "$file.t.$$"; ship_tooling "cannot write $file"; }
  done <<EORECORD
$results
EORECORD
}
# A skipped phase 3 has no close to take a result, so the skip settles each
# verification still pending as n/a with its reason. The entry is matched as the
# whole `- <name>: pending` line, not by prefix, so a name that begins another
# (`lint` beside `lint: strict`) settles only its own entry.
settle_skipped() { # settle_skipped <reason>
  local pend
  pend=$(pending_verifs) || ship_tooling "cannot read the verification results in $file"
  [ -n "$pend" ] || return 0
  ps=$pend tx="n/a: $1" awk '
    BEGIN { n = split(ENVIRON["ps"], p, "\n"); for (i = 1; i <= n; i++) want["- " p[i] ": pending"] = p[i] }
    /^## /{ f = ($0 == "## Verification results") }
    f && ($0 in want) { print "- " want[$0] ": " ENVIRON["tx"]; next } { print }' \
    "$file" > "$file.t.$$" && mv "$file.t.$$" "$file" || { rm -f "$file.t.$$"; ship_tooling "cannot write $file"; }
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
# A heading is a name whatever it holds, so profile names skip the name-shape
# test a hand-passed list gets; `=` is the one character `--result` cannot take
# in one, and a result word as a later field is the one shape the pending test
# reads as settled (`- Build: pass: pending`).
refuse_bad_headings() { # refuse_bad_headings <names, one per line>
  local bad
  case $1 in *=*) ship_tooling "a Verification heading cannot carry '=': --result splits the name from the word on it" ;; esac
  bad=$(w="$result_re" awk '$0 ~ (": (" ENVIRON["w"] ")(:|$)") { print; exit }' <<<"$1") ||
    ship_tooling "could not read the Verification headings for result words"
  [ -z "$bad" ] || ship_tooling "a Verification heading cannot carry a result word as a later field: '$bad' would read as a settled result while pending; rename it"
}
join_names() { awk '{ out = out (NR > 1 ? ", " : "") $0 } END { print out }'; }

# --- The self-review and review-loop gates -------------------------------------
# A gate line is a line of the Run file, an optional leading `- ` accepted,
# matched by exact prefix at the line start: the text after the prefix, one line
# each. The prefix goes through ENVIRON, so a name's dot or bracket is never a
# pattern.
rests() { # rests <prefix> [<text>]: reads the Run file, or <text> where given
  { if [ $# -gt 1 ]; then printf '%s\n' "$2"; else cat "$file"; fi; } \
    | p=$1 awk '{ l = $0; sub(/^- /, "", l) } index(l, ENVIRON["p"]) == 1 { print substr(l, length(ENVIRON["p"]) + 1) }'
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
