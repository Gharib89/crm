#!/usr/bin/env bash
# Run-file mechanic: the init, open, next, close, skip and grade verbs. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154,SC2034 # shares the globals of run-file.sh (file, head, verb, usage)

case $verb in
init)
  ship_args "$usage" arg "$@"
  id=$1; shift
  scratchpad="" tripwires=None verifications="None applicable" reviewers=none legs=None
  rebuild=false states="" given="" from_profile=false profile_path="" profile_names="" criteria_src=""
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
      # `-` is the text on stdin, so it is the one dash-led value this flag takes.
      --criteria)      [ $# -ge 2 ] && [ -n "$2" ] || ship_tooling "--criteria needs a file or -"; case $2 in -?*) ship_tooling "$usage" ;; esac; criteria_src=$2; shift 2 ;;
      *) ship_tooling "unknown flag: $1" ;;
    esac
  done
  # Read before anything is written, so an unreadable source leaves no record.
  criteria=""
  if [ -n "$criteria_src" ]; then
    if [ "$criteria_src" = - ]; then text=$(cat) || ship_tooling "cannot read the criteria text"
    else
      [ -f "$criteria_src" ] && [ -r "$criteria_src" ] || ship_tooling "cannot read the criteria text at $criteria_src"
      text=$(cat "$criteria_src") || ship_tooling "cannot read the criteria text at $criteria_src"
    fi
    # `null` is what a failed read of the issue body gives; an empty body is a real read.
    [ "$text" != null ] || ship_tooling "the criteria text is null: a failed read of the issue body"
    criteria=$(criteria_entries "$text") || ship_tooling "cannot read the criteria text"
    # Text that yields nothing leaves the gate nothing to judge, so it is said (#958).
    [ -n "$criteria" ] \
      || printf 'run-file init: the --criteria text yields no criteria (no checkbox with text, and no heading naming criteria whose list holds plain lines and no checkbox): record each with `run-file note Criteria <text>`\n' >&2
  fi
  # The profile fills the slots a flag did not: the flag is the run's own word
  # for this run, the profile the repo's standing one.
  if [ "$from_profile" = true ]; then
    [ -n "$profile_path" ] || profile_path=$(ship_profile_path) || ship_tooling "not inside a git checkout: pass --from-profile <path>"
    [ -f "$profile_path" ] && [ -r "$profile_path" ] || ship_tooling "no readable ship profile at $profile_path"
    profile=$(cat "$profile_path")
    pf=$(awk '/^## / { f = ($0 ~ /^## Local gate[ \t\r]*$/); next }
              f && /^Tripwires:/ { sub(/^Tripwires:[ \t]*/, ""); sub(/[ \t\r]+$/, ""); print; exit }' <<<"$profile")
    case " $given " in *" tripwires "*) ;; *) [ -z "$pf" ] || tripwires=$pf ;; esac
    pn=$(profile_headings Verification "$profile")
    case " $given " in
      *" verifications "*) ;;
      *) if [ -n "$pn" ]; then
           refuse_bad_headings "$pn"
           verifications=$(join_names <<<"$pn") profile_names=$pn
         fi ;;
    esac
    pf=$(profile_headings Reviewers "$profile" | join_names)
    case " $given " in *" reviewers "*) ;; *) [ -z "$pf" ] || reviewers=$pf ;; esac
    pf=$(ship_profile_legs "$profile" | join_names)
    case " $given " in *" legs "*) ;; *) legs=${pf:-None.} ;; esac
  fi
  # A numbered issue's criteria are ones the gate holds on, and a run that
  # never passed them reaches its merge gate unjudged (#933). The profile names
  # the host the issue is read from, so a bare init reads none; a failed read
  # warns of nothing, since init needs no host.
  if [ "$from_profile" = true ] && [ -z "$criteria_src" ] && [[ $id =~ ^[1-9][0-9]*$ ]]; then
    count=$(issue_criteria "$id") && [ "$count" -gt 0 ] \
      && printf 'run-file init: issue #%s has %s criteria but --criteria was not passed: record each with `run-file note Criteria <text>`, or run the same init again with `--criteria -` and `--rebuild` added, the issue body on stdin\n' "$id" "$count" >&2
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
  # only when the caller says so. The create is the guard: under noclobber bash
  # opens the file O_EXCL, so of two initializers in one clone that both found
  # no file, the second refuses here rather than wipe the first's record.
  mkdir -p "$root/ship-$id" "$scratch" || ship_tooling "cannot create $root/ship-$id and $scratch"
  items=$(checklist "$tripwires" "$verifications" "$reviewers" "$legs")
  if [ -n "$profile_names" ]; then names=$profile_names; else names=$(verif_names "$verifications"); fi
  if ! ( [ "$rebuild" = true ] || set -C
    { printf '# ship run · %s\n\n' "$id"
      printf '%s\n' "$items" | sed 's/^/- [ ] /'
      if [ -n "$names" ]; then
        printf '\n## Verification results\n\n'
        printf '%s\n' "$names" | sed 's/$/: pending/; s/^/- /'
      fi
      [ -z "$criteria" ] || printf '\n## Criteria\n\n%s\n' "$criteria"
      printf '\n## Design and plan\n\n(pending)\n\n## Deviations log\n\n(none yet)\n'
      printf '\n## Direct calls\n\n(none yet)\n'
    } > "$file" ); then
    [ "$rebuild" = false ] && [ -e "$file" ] && ship_fail "Run file exists: $file: a resumed run reads that record, and a new run of an issue whose earlier run stopped replaces it with \`run-file init $id --rebuild\`"
    ship_tooling "cannot write $file"
  fi
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
    --arg src "$criteria_src" --arg crit "$criteria" \
    '{run_file: $f, id: $i, scratch: $s, items: (split("\n") | map(select(. != "")))}
     + if $auto then {opened: 0, mirror: "in_progress"} else {} end
     + if $src != "" then {criteria: ($crit | split("\n") | map(select(. != "")) | length)} else {} end'
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
    '')
      # Nothing to close: `next` is `open` under the same order check.
      gap=$(unflipped "$n")
      [ -n "$gap" ] && refuse_gap open "$gap" "$n"
      opened=$(render open "$(item "$next_line")" "$(date -u +%H:%M)")
      write_line "$next_lineno" "$opened"
      jq -n --arg f "$file" --argjson n "$n" --arg ol "$opened" \
        '{run_file: $f, closed: null, opened: {phase: $n, line: $ol, mirror: "in_progress"}}'
      exit 0
      ;;
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
  [ -z "$head" ] || [ "$n" = 3 ] || ship_tooling "--head belongs to close 3"
  [ -z "$results" ] || [ "$n" = 3 ] || ship_tooling "--result belongs to close 3"
  take_row "$n"
  check_results
  # Each result records the head it was proven on, so the clean gate can ask
  # whether that head still covers the PR's.
  head_given=$head
  if [ -n "$results" ] && [ -z "$head" ]; then
    head=$(git rev-parse HEAD 2>/dev/null) || ship_tooling "no --head given and not inside a git checkout"
  fi
  [ -z "$head" ] || [[ $head =~ ^[0-9a-f]{7,64}$ ]] || ship_tooling "--head takes a full or abbreviated commit sha"
  # A heading a later phase's fix adds, or a re-run of a recorded one, owes a
  # result after phase 3 has closed or been skipped, so this records it and keeps
  # phase 3's row: re-opening would need the open phase closed first and would
  # restamp a finished phase (#626).
  if [ "$n" = 3 ] && [ -n "$results" ] && [[ $line == "- [x] "* ]]; then
    record_results
    jq -n --arg f "$file" --arg l "$line" '{run_file: $f, phase: 3, state: "recorded", line: $l}'
    exit 0
  fi
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
  # An open phase 3 is settled by closing it, so its refusals name how (#989).
  route3='`close 3 --result <name>=n/a` per pending Verification'
  if is_open "$line"; then
    [ "$n" = 3 ] && ship_fail "phase 3 is open, so it cannot be skipped: close it with $route3"
    ship_fail "phase $n is open; close it before skipping it"
  fi
  # Rows 5 to 8 wait behind phase 3, as `open` and `close` hold them; a skip
  # was the way round (#933).
  case $n in
    [5-8]) case " $(open_phases | tr '\n' ' ') " in *" 3 "*) ship_fail "phase 3 is open; close it with $route3 before skipping $n" ;; esac ;;
  esac
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
  [ "$n" = 3 ] && settle_skipped "$reason"
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
        f && /^(- )?Grade: / { if (!done) print ENVIRON["repl"]; done = 1; next } { print }' "$file" > "$file.t.$$" \
        && mv "$file.t.$$" "$file"; }; then
      rm -f "$file.t.$$"; ship_tooling "cannot write $file"
    fi
  else
    append_to_section "## Grade" "Grade: $word"
  fi
  jq -n --arg f "$file" --arg g "$word" '{run_file: $f, grade: $g}'
  ;;
esac
