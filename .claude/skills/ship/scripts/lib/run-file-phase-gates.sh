#!/usr/bin/env bash
# Run-file mechanic: the phase 4, 5 and 7 gates, and the covered-head and untouched-path reads. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154,SC2034 # shares the globals of run-file.sh (file, head, verb, usage)

is_test_path() {
  case /$1/ in */tests/* | */test/* | */__tests__/*) return 0 ;; esac
  case ${1##*/} in *.test.* | *.spec.* | *_test.* | test_*) return 0 ;; esac
  return 1
}
# A produced red line, by the shape `prove` writes: the head revert-red ran on
# and the paths it reverted.
red_re='^red at ([0-9a-f]{7,64}) reverting ([^[:space:]].*)$'
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

# The rest of a `Round: <name> ` line, close 4's self-review and close 7's
# reviewers alike: a positive round number, then the outcome.
round_re='^[1-9][0-9]*: [[:space:]]*[^[:space:]]'

# Phase 4: every test file changed has its Reverted-fix line, and the self-review
# loop its rounds and its stop.
gate_phase4() {
  local base mb top tracked untracked paths p r ok why
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

  # The self-review loop, in close 7's vocabulary: a round, then the stop. Every
  # round line must be well formed, since no other label starts `self-review `;
  # close 7 cannot ask that, its `claude` prefix also reading `claude code`'s.
  ok=false
  while IFS= read -r r; do
    if [[ $r =~ $round_re ]]; then
      ok=true
    else
      ok=false; break
    fi
  done <<EORESTS
$(rests "Round: self-review ")
EORESTS
  [ "$ok" = true ] || gap "no Round line for self-review: add \`Round: self-review <n>: <text>\`, one per self-review round, each in that shape"
  r=$(trim_end "$(rests "Stop: self-review: " | tail -n 1)")
  case $r in
    "tree unchanged" | cap) ;;
    "cap at "*) [[ ${r#"cap at "} =~ ^[0-9a-f]{7,64}$ ]] ||
      gap "Stop line for self-review has reason '$r', whose head is not a commit sha" ;;
    "") gap "no Stop line for self-review: add \`Stop: self-review: <tree unchanged|cap at <sha>>\`" ;;
    *) gap "Stop line for self-review has reason '$r', which is not one of tree unchanged, cap at <sha>, cap" ;;
  esac
  refuse_gaps 4
}

# Phase 7: each reviewer the checklist row names has its stop reason and, unless
# it never reviewed, a round.
stop_reasons="cap at <sha>, cap, tree unchanged at <sha>, small lane, inline lane, auto-once, not landed, not reviewed, not invoked: <primary> reviewed"
gate_phase7() { # gate_phase7 <phase 7 row>
  local list names nm stop r ok p ps
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
    # Only an unchanged tree settles a reviewer, so it must name the head it
    # read: the clean gate holds it once the PR moves past that head. A cap may
    # name its head, which lifts a capped self-review's hold.
    case $stop in
      cap | "small lane" | "inline lane" | auto-once | "not landed" | "not reviewed" | "not reviewed: "?* | "not invoked: "?*" reviewed") ;;
      "tree unchanged at "* | "cap at "*) [[ ${stop##*" at "} =~ ^[0-9a-f]{7,64}$ ]] ||
        gap "Stop line for $nm has reason '$stop', whose head is not a commit sha" ;;
      "tree unchanged") gap "Stop line for $nm has no head: write \`Stop: $nm: tree unchanged at <sha>\`, the head read-pr returned for the round" ;;
      "") gap "no Stop line for $nm: add \`Stop: $nm: <cap at <sha>|cap|tree unchanged at <sha>|small lane|inline lane|auto-once|not landed|not reviewed|not invoked: <primary> reviewed>\`" ;;
      *) gap "Stop line for $nm has reason '$stop', which is not one of $stop_reasons" ;;
    esac
    # A fallback not invoked names a primary on the row that stopped reviewed;
    # whether its cap was spent with findings is left to the clean gate.
    case $stop in "not invoked: "?*" reviewed")
      p=${stop#"not invoked: "}; p=${p%" reviewed"}
      if ! grep -qxF -- "$p" <<<"$names"; then
        gap "Stop line for $nm names $p, which is not a reviewer on the phase 7 row"
      else
        ps=$(trim_end "$(rests "Stop: $p: " | tail -n 1)")
        case $ps in "not reviewed" | "not reviewed: "* | "not landed" | "inline lane" | "not invoked: "* | "")
          gap "Stop line for $nm says $p reviewed, but $p stopped '$ps'" ;; esac
      fi ;;
    esac
    case $stop in "not reviewed" | "not reviewed: "?* | "not invoked: "?*" reviewed" | "inline lane") continue ;; esac
    ok=false
    while IFS= read -r r; do
      [[ $r =~ $round_re ]] && ok=true
    done <<EORESTS
$(rests "Round: $nm ")
EORESTS
    [ "$ok" = true ] || gap "no Round line for $nm: add \`Round: $nm <n>: <text>\`, one per round, unless it stopped \`not reviewed\`, \`not invoked: <primary> reviewed\` or \`inline lane\`"
  done <<EONAMES
$names
EONAMES
  refuse_gaps 7
}
# A `not met` reason that says nothing: only words like these, or none at all.
# The merge summary is posted after phase 5, so a criterion still open then
# names what is outstanding and the step that settles it (#894).
placeholder_reason() { # placeholder_reason <reason>: 0 when it names no step
  local w
  for w in $(tr '[:upper:]' '[:lower:]' <<<"$1" | tr -c 'a-z\n' ' '); do
    case $w in pending | todo | tbd | later | wip | open | unknown) ;; *) return 1 ;; esac
  done
  return 0
}

# Phase 5: every `## Criteria` entry has a `Criterion <n>:` line, and one still
# `not met` gives a real reason. A Run file with no `## Criteria` is not refused.
gate_phase5() {
  local n r lines
  # The unfenced text gate clean reads: a fenced example is neither a line nor an entry.
  lines=$(awk "$SHIP_AWK_FENCE"'
    { if (!ship_fence($0)) print }
    END { if (_fenced) exit 1 }' "$file") || ship_tooling "cannot read Run file evidence at $file"
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    r=$(trim_end "$(rests "Criterion $n: " "$lines" | tail -n 1)")
    case $r in
      "") gap "criterion $n has no line: write \`Criterion $n: met: <proof>\`, or \`Criterion $n: not met: <what is outstanding and the step that settles it>\`" ;;
      "met: "?*) ;;
      "not met: "?*)
        if placeholder_reason "${r#not met: }"; then
          gap "criterion $n is \`$r\`, which names no outstanding work: write \`Criterion $n: not met: <what is outstanding and the step that settles it>\`"
        fi ;;
      *) gap "criterion $n has a line gate clean cannot read: $r" ;;
    esac
  done <<EOCRIT
$(awk '{ t = $0; sub(/[ \t\r]+$/, "", t) } t ~ /^## / { f = (t == "## Criteria") } f && /^- [1-9][0-9]*: / { v = substr($0, 3); sub(/:.*/, "", v); print v }' <<<"$lines")
EOCRIT
  refuse_gaps 5
}

# The gate a phase's close owes, run once the phase is known open and before a
# line is written, so a refusal leaves the record as it was.
close_gate() { # close_gate <phase> <its row>
  case $1 in
    4) gate_phase4 ;;
    5) gate_phase5 ;;
    7) gate_phase7 "$2" ;;
  esac
}

# The commit a hash names, where the hash is that commit's or an abbreviation of
# it: git also resolves a full hash with hex digits appended to it.
commit_of() { # commit_of <hash>
  local c
  c=$(git rev-parse -q --verify "$1^{commit}" 2>/dev/null) || return 1
  case $c in "$1"*) printf '%s\n' "$c" ;; *) return 1 ;; esac
}
# A stamp covers the head when it is the head, or when every later commit on the
# head's first-parent line is a clean base merge: two parents, the second on the
# base, and a tree `merge-tree` reproduces. A merge that resolved a conflict, any
# other commit, or a stamp or head git cannot resolve breaks coverage. Exit 0
# covered, 1 not, 2 a read the walk needs failed (the base ref, a commit's
# parents, the ancestry check, git's merge-tree).
covers() { # covers <stamp> <head>
  local s c t base parents p=()
  [ "$1" = "$2" ] && return 0
  s=$(commit_of "$1") && c=$(commit_of "$2") || return 1
  [ "$s" = "$c" ] && return 0
  base=refs/remotes/$(ship_base_ref --local) || return 2
  while [ "$c" != "$s" ]; do
    parents=$(git rev-list --parents -n 1 "$c" 2>/dev/null) || return 2
    read -r -a p <<<"$parents"
    [ "${#p[@]}" -gt 0 ] || return 2
    [ "${#p[@]}" -eq 3 ] || return 1
    git merge-base --is-ancestor "${p[2]}" "$base" 2>/dev/null
    case $? in 0) ;; 1) return 1 ;; *) return 2 ;; esac
    t=$(git merge-tree --write-tree "${p[1]}" "${p[2]}" 2>/dev/null)
    case $? in 0) ;; 1) return 1 ;; *) return 2 ;; esac
    [ "$t" = "$(git rev-parse "$c^{tree}" 2>/dev/null)" ] || return 1
    c=${p[1]}
  done
}
# A Verification's `Paths:` globs, comma-separated, as git `:(glob)` pathspecs,
# where `*` stays inside one directory and `**` crosses them. True when no file
# they match changed between the stamp and the head; a failed diff is a change,
# and a glob matching no file at either head (a typo, `None.`) proves nothing.
untouched() { # untouched <stamp> <head> <paths>
  local g s h empty globs=() specs=()
  s=$(commit_of "$1") && h=$(commit_of "$2") || return 1
  [ -n "$3" ] && empty=$(git hash-object -t tree --stdin </dev/null 2>/dev/null) || return 1
  IFS=, read -r -a globs <<<"$3"
  for g in "${globs[@]}"; do
    g=$(trim_end "${g#"${g%%[![:space:]]*}"}")
    [ -n "$g" ] || continue
    ! git diff --quiet "$empty" "$s" -- ":(glob)$g" 2>/dev/null \
      || ! git diff --quiet "$empty" "$h" -- ":(glob)$g" 2>/dev/null || return 1
    specs+=(":(glob)$g")
  done
  [ "${#specs[@]}" -gt 0 ] && git diff --quiet "$s" "$h" -- "${specs[@]}" 2>/dev/null
}

# The clean gate is a read of evidence, independent of the profile's merge opt-in.
# Only a stop of tree unchanged proves no further round would read anything new.
