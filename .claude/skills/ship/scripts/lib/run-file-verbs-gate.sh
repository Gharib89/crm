#!/usr/bin/env bash
# Run-file mechanic: the gate, timing, prove, probe, call and note verbs. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154,SC2034 # shares the globals of run-file.sh (file, head, verb, usage)

case $verb in
gate)
  sub=${1:-}
  case $sub in record | read | clean | summary) shift ;; *) ship_tooling "$usage" ;; esac
  if [ "$sub" = record ] || [ "$sub" = clean ] || [ "$sub" = summary ]; then
    # `-` is the verdict on stdin, so it is the one dash-led value a positional may be.
    src=${1:-}
    case $src in '' | -?*) ship_tooling "$usage" ;; esac
    shift
  fi
  parse_file "$@"
  [ -z "$results" ] || ship_tooling "$usage"
  if [ "$sub" = summary ]; then
    [ -z "$head" ] || ship_tooling "$usage"
    [[ $src =~ ^[1-9][0-9]*$ ]] || ship_tooling "$usage"
    # shellcheck source=run-file-gate-summary.sh
    source "$SHIP_SCRIPTS/lib/run-file-gate-summary.sh"
    gate_summary "$src"
    exit $?
  elif [ "$sub" = clean ]; then
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
  # <where> ends at `--`; the command tail, `--` included, goes to revert-red as
  # typed.
  where=() fixarg=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do
    if [ "$1" = --fix ]; then
      ship_flag_value "$usage" "${2:-}"
      fixarg=(--fix "$2"); shift 2
    else
      where+=("$1"); shift
    fi
  done
  parse_file ${where[@]+"${where[@]}"}
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
  answer=$(bash "$SHIP_SCRIPTS/revert-red.sh" "${args[@]}" ${fixarg[@]+"${fixarg[@]}"} "$@")
  rc=$?
  [ "$rc" -eq 0 ] || { printf '%s\n' "$answer"; exit "$rc"; }
  out=$(jq -c --arg f "$file" --arg h "$head" '. + {run_file: $f, head: $h}' <<<"$answer" 2>/dev/null) \
    || ship_tooling "revert-red did not print a JSON verdict"
  append_to_section "## Evidence" "Reverted-fix: ${recorded[0]}: red at $head reverting ${recorded[*]:1}"
  jq . <<<"$out"
  ;;
ran)
  names=()
  # A name may begin with `--`, so only the flags <where> reads end the names.
  while [ $# -gt 0 ]; do case $1 in --file | --issue | --scratchpad | --result | --head) break ;; esac; names+=("$1"); shift; done
  [ ${#names[@]} -ge 1 ] || ship_tooling "$usage"
  parse_file "$@"
  no_extra
  # Every name is checked before any line is written, so a bad one leaves the
  # record as it was.
  # parse_result is the name check close 3 --result applies; the word is a stand-in.
  for nm in "${names[@]}"; do parse_result "$nm=pass"; done
  head=$(git rev-parse HEAD 2>/dev/null) || ship_tooling "ran records the head the verification ran at: not inside a git checkout with a commit"
  for nm in "${names[@]}"; do append_to_section "## Evidence" "Ran: $nm at $head"; done
  printf '%s\n' "${names[@]}" | jq -Rn --arg f "$file" --arg h "$head" '{run_file: $f, ran: [inputs | {name: ., head: $h}]}'
  ;;
probe)
  ref=${1:-}
  case $ref in '' | -*) ship_tooling "$usage" ;; esac
  shift
  # A ref holding `: ` or a newline blurs where the ref ends for the line's
  # readers: the self-review and the merge summary quoting it.
  case $ref in *": "* | *$'\n'*) ship_tooling "a probe's ref ends at its line's first ': ', so it cannot hold ': ' or a newline" ;; esac
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
  # Trimmed both ends, so the line reads the same however the output was padded.
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
call)
  why=${1:-}
  case $why in '' | -*) ship_tooling "$usage" ;; esac
  case $why in *$'\n'*) ship_tooling "a direct call's why is one line" ;; esac
  shift
  where=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do where+=("$1"); shift; done
  [ "${1:-}" = -- ] && [ $# -ge 2 ] || ship_tooling "$usage"
  shift
  parse_file ${where[@]+"${where[@]}"}
  no_extra
  # A credential would land in the Run file and in the summary an unattended run
  # posts to the PR, and the host CLI authenticates a direct call itself. The
  # refusal leaves the command out, so the credential reaches no output either.
  # `authorization` keyed by `:` or `=` anywhere in a word covers every header
  # spelling (`-H v`, `-Hv`, `--header=v`, a JSON `--headers` object with it at
  # any key); a field's value (gh's -f and -F, az's --body) is request content.
  skip=false
  for w in "$@"; do
    if [ "$skip" = true ]; then
      skip=false
      continue
    fi
    case $w in
    -f | -F | --raw-field | --field | --body) skip=true && continue ;;
    -f* | -F* | --raw-field=* | --field=* | --body=*) continue ;;
    esac
    lw=$(printf '%s' "$w" | tr '[:upper:]' '[:lower:]')
    case $lw in *authorization[:=]* | *authorization[\ \"\']*[:=]*)
      ship_fail "a direct call carries no credential: the host CLI authenticates it, so drop the Authorization header"
      ;;
    esac
  done
  cmd=$(printf '%q ' "$@")
  cmd=${cmd% }
  rest=false
  { [ "$1 ${2:-}" = "gh api" ] || [ "$1 ${2:-}" = "az rest" ]; } && [ $# -ge 3 ] && rest=true
  # `graphql` is the one `gh api` endpoint that is not REST, as a word, a path
  # or a URL. Only the endpoint, the first word that is neither a flag nor a
  # flag's value, is matched, so a field value naming graphql is still REST, and
  # its query string or fragment is stripped first, since gh sends neither.
  if [ "$1" = gh ]; then
    set -- "${@:3}"
    while [ $# -gt 0 ]; do
      case $1 in
      -X | --method | -H | --header | -f | --raw-field | -F | --field | --input | -q | --jq | -t | --template | --cache | -p | --preview | --hostname) shift 2 || break ;;
      -*) shift ;;
      *)
        ep=${1%%[?#]*}
        case ${ep%/} in graphql | */graphql) rest=false ;; esac
        break
        ;;
      esac
    done
  fi
  [ "$rest" = true ] || ship_fail "a direct call goes through the host's REST form, \`gh api\` or \`az rest\`: $cmd"
  append_to_section "## Direct calls" "- $cmd · $why"
  jq -n --arg f "$file" --arg c "$cmd" --arg w "$why" '{run_file: $f, command: $c, why: $w}'
  ;;
note)
  sect=${1:-}; text=${2:-}
  case $sect in '' | -* | '#'* | *$'\n'*) ship_tooling "$usage" ;; esac
  case $text in '' | -* | '#'* | *$'\n'*) ship_tooling "$usage" ;; esac
  shift 2
  parse_file "$@"
  no_extra
  # A Criterion line in any other form reads as unmet to `gate clean` (#853).
  crit='^Criterion [1-9][0-9]*: (met|not met): .*[^[:space:]]'
  # Under `## Criteria` any line but a `Criterion <n>: ` attestation is an entry
  # the gate holds, so it takes the number after the highest entry (#863); its
  # text may itself open on the word `Criterion`.
  attest='^Criterion [1-9][0-9]*: '
  if [ "$sect" = Criteria ] && ! [[ $text =~ $attest ]]; then
    n=$(awk '{ t = $0; sub(/[ \t\r]+$/, "", t) } t ~ /^## / { f = (t == "## Criteria") } f && /^- [1-9][0-9]*: / { v = substr($0, 3) + 0; if (v > m) m = v } END { print m + 1 }' "$file")
    text="- $n: $text"
  fi
  case $text in Criterion*) [[ $text =~ $crit ]] || ship_fail "a Criterion line is \`Criterion <n>: met: <proof>\` or \`Criterion <n>: not met: <reason>\`, the form gate clean reads: $text" ;; esac
  # The local review a run takes past a reviewer's cap, or after a docs-only end
  # before it: a line of its own, so the reviewer's `Round:` lines still read
  # 1..Cap (#880, #1034).
  case $text in Past-cap:* | Docs-only:*)
    kind=${text%%:*}
    [[ $text =~ ^$kind:\ .+:\ tree\ unchanged\ at\ [0-9a-f]{7,64}$ ]] || ship_fail "a $kind line is \`$kind: <reviewer>: tree unchanged at <sha>\`, the form gate clean reads: $text" ;;
  esac
  append_to_section "## $sect" "$text"
  jq -n --arg f "$file" --arg s "$sect" --arg l "$text" '{run_file: $f, section: $s, line: $l}'
  ;;
*)
  ship_tooling "$usage"
  ;;
esac
