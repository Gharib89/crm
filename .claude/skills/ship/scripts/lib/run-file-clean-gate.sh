#!/usr/bin/env bash
# Run-file mechanic: the clean merge gate. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154,SC2034 # shares the globals of run-file.sh (file, head, verb, usage)

gate_clean() { # gate_clean <ci-file|->, with file and head already parsed
  local ci first profile_path profile reviewers verifications lines record_dir rec rhead='' rverdict='' rgates=null drafts='[]' p answer
  local stamps s cover='{}' scope='{}' vname vpaths sr_stamp sr c descends='{}'
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
  # The answer is the first JSON value: a background task's output file ends in
  # an exit-code trailer, which a whole-input parse would refuse. A parse error
  # before the first value leaves the input as is, for the check below to name.
  first=$(jq -c -n 'first(inputs)' <<<"$ci" 2>/dev/null) && [ -n "$first" ] && ci=$first
  jq -e 'type == "object" and (.status | IN("green", "no-checks", "conflict", "checks-failed", "timeout", "pending"))
    and (.head_sha | type == "string" and test("^[0-9a-f]{7,64}$"))
    and (.checks | type == "array" and all(.[]; type == "object" and (.name | type == "string") and (.status | type == "string")))' \
    >/dev/null 2>&1 <<<"$ci" || ship_tooling "CI answer is not a valid ci-wait object"
  profile_path=$(ship_profile_path) || ship_tooling "cannot locate ship profile"
  [ -f "$profile_path" ] && [ -r "$profile_path" ] || ship_tooling "cannot read ship profile at $profile_path"
  profile=$(awk "$SHIP_AWK_FENCE"'
    { if (!ship_fence($0)) print }
    END { if (_fenced) exit 1 }' "$profile_path") || ship_tooling "cannot read profile evidence at $profile_path"
  # A legal empty list also admits a path-filtered leg with no check on head (#589).
  local names legs no_checks=false
  ship_no_checks_legal "$profile"
  case $? in 0) no_checks=true ;; 1) ;; *) ship_tooling "cannot read profile no-checks policy" ;; esac
  names=$(ship_profile_legs "$profile") || ship_tooling "cannot read profile CI legs"
  legs=$(jq -Rn '[inputs | select(. != "")]' <<<"$names") || ship_tooling "cannot read profile CI legs"
  reviewers=$(ship_reviewers "$profile") || ship_tooling "cannot read profile reviewers"
  # Keep the association beside its heading, with the profile's exact field.
  verifications=$(awk '
    /^## / { f = ($0 ~ /^## Verification[ \t\r]*$/); next }
    f && /^### / { sub(/^### /, ""); sub(/[ \t\r]+$/, ""); print "name\t" $0; next }
    f && /^Also proven by CI: / { sub(/^Also proven by CI: /, ""); sub(/[ \t\r]+$/, ""); print "leg\t" $0 }
    f && /^Paths: / { sub(/^Paths: /, ""); sub(/[ \t\r]+$/, ""); gsub(/\t/, " "); print "paths\t" $0 }
    ' <<<"$profile" | jq -Rs 'reduce (split("\n")[] | select(. != "") | split("\t") | [.[0], (.[1:] | join("\t"))]) as $r ([];
      if $r[0] == "name" then . + [{name: $r[1], leg: null, paths: null}]
      elif length > 0 then .[-1][$r[0]] = $r[1] else . end)') || ship_tooling "cannot read profile verifications"
  # Every head a stop, a `Past-cap:` or `Docs-only:` line or a result was stamped at: whether it covers the PR head,
  # and, per Verification with `Paths:`, whether its globs saw no change since.
  # Trimmed as the jq below trims each line, so both read the same stamp.
  stamps=$(sed $'s/[ \t\r]*$//' <<<"$lines" | grep -oE ' at [0-9a-f]{7,64}(: |$)' | sed 's/^ at //; s/: $//' | sort -u)
  while IFS= read -r s; do
    [ -n "$s" ] || continue
    covers "$s" "$head"
    case $? in
      0) cover=$(jq -c --arg s "$s" '.[$s] = true' <<<"$cover") ;;
      1) ;;
      *) ship_tooling "cannot read whether $s covers head $head: a read the walk needs failed (origin/HEAD, a commit's parents, the ancestry check, git merge-tree --write-tree)" ;;
    esac
    while IFS=$'\t' read -r vname vpaths; do
      [ -n "$vname" ] || continue
      untouched "$s" "$head" "$vpaths" && scope=$(jq -c --arg v "$vname" --arg s "$s" '.[$v][$s] = true' <<<"$scope")
    done <<<"$(jq -r '.[] | select(.paths != null) | [.name, .paths] | @tsv' <<<"$verifications")"
  done <<<"$stamps"
  # A self-review stopped `cap at <S>`: which stamps are <S> or descend from it,
  # so a host stop stamped there read its last fixes. An unknown stamp descends
  # from nothing; a failed ancestry read is no verdict.
  sr_stamp=$(sed $'s/[ \t\r]*$//' <<<"$lines" | sed -n 's/^- //; s/^Stop: self-review: //p' | tail -n 1 \
    | sed -nE 's/^cap at ([0-9a-f]{7,64})$/\1/p')
  if [ -n "$sr_stamp" ] && sr=$(commit_of "$sr_stamp"); then
    while IFS= read -r s; do
      [ -n "$s" ] || continue
      c=$(commit_of "$s") || continue
      git merge-base --is-ancestor "$sr" "$c" 2>/dev/null
      case $? in
        0) descends=$(jq -c --arg s "$s" '.[$s] = true' <<<"$descends") ;;
        1) ;;
        *) ship_tooling "cannot read whether $s descends from the self-review stamp $sr_stamp" ;;
      esac
    done <<<"$stamps"
  fi
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
  for p in "$record_dir"/tracker-*.md; do
    [ -f "$p" ] || continue
    case $p in *.base.md) continue ;; esac
    drafts=$(jq -c --arg p "${p##*/}" '. + [$p]' <<<"$drafts") || ship_tooling "cannot read draft paths"
  done
  answer=$(jq -n --arg text "$lines" --arg head "$head" --arg rhead "$rhead" --arg verdict "$rverdict" \
    --argjson gates "$rgates" --argjson ci "$ci" --argjson legs "$legs" --argjson reviewers "$reviewers" \
    --argjson verifications "$verifications" --argjson drafts "$drafts" --argjson no_checks "$no_checks" --argjson cover "$cover" --argjson scope "$scope" --argjson descends "$descends" \
    --arg round_re "$round_re" --arg words "$result_re" '
    def samehead($a; $b): $a != "" and $b != "" and (($a | startswith($b)) or ($b | startswith($a)));
    def named($leg): .name == $leg or (.name | startswith($leg + " ("));
    ($text | split("\n")) as $lines
    | ($lines | map(sub("^- "; "") | sub("[ \\t\\r]+$"; ""))) as $evidence
    | def rests($prefix): [$evidence[] | select(startswith($prefix)) | ltrimstr($prefix)];
      # `not reviewed: <reason>` is `not reviewed` with its reason attached.
      def stop($name): (rests("Stop: " + $name + ": ") | last // "missing stop")
        | if startswith("not reviewed: ") then "not reviewed" else . end;
      def reviewed($name): (rests("Round: " + $name + " ") | last // "") | test($round_re);
      # A stamp is the head a stop or result was recorded on, `<word> at <sha>`.
      def word: sub(" at [0-9a-f]{7,64}$"; "");
      def stamp: [capture(" at (?<s>[0-9a-f]{7,64})$").s] | first // "";
      def stale($what; $sha): $what + " at " + $sha + ", which does not cover head " + $head;
      def unstamped($what): $what + " with no head stamp to cover head " + $head;
      # A finding declined and raised again was not answered by the next round
      # reading an unchanged tree: declining is no cheaper than fixing, so the
      # reviewer stops on cap.
      def reraised($name): any(rests("Round: " + $name + " ")[]; test("^[1-9][0-9]*: re-raised declined: "));
      def settled($name): (stop($name) | word) == "tree unchanged" and reviewed($name) and (reraised($name) | not) and $cover[stop($name) | stamp] == true;
      # The `## Criteria` entries, `- <n>: <text>`, each owed a `Criterion <n>:` line,
      # and every number a `Criterion <n>:` line names without an entry, which an
      # init that recorded none leaves as the only record of a criterion (#958).
      def criteria: ([$lines[] | sub("[ \\t\\r]+$"; "")] | reduce .[] as $l ({in: false, ids: []};
          if ($l | startswith("## ")) then .in = ($l == "## Criteria")
          elif .in and ($l | test("^- [1-9][0-9]*: ")) then .ids += [$l | capture("^- (?<n>[1-9][0-9]*): ").n]
          else . end) | .ids) as $ids
        | $ids + ([rests("Criterion ")[] | capture("^(?<n>[1-9][0-9]*): ").n] | unique_by(tonumber) | map(select(IN($ids[]) | not)));
      def criterion($n): rests("Criterion " + $n + ": ") | last // "";
      # Capped with findings: Cap: spent on a tree-changing last round, which a
      # docs-only end before the cap, also `Stop: cap`, is not, nor is a
      # re-raised declined finding: no fix went unread, so no fallback stands in. The dispositioned
      # rounds must read 1..Cap exactly: a gapped, blank or overrun record is no
      # evidence of a spent cap.
      def rounds($name): [rests("Round: " + $name + " ")[] | select(test($round_re))
        | capture("^(?<n>[1-9][0-9]*):").n | tonumber] | unique;
      # The numeric Cap: of a reviewer as a one-element array, empty for `None.`.
      def cap_of($name): [$reviewers[] | select(.name == $name) | .cap | numbers];
      # `Stop: cap` that no re-raised declined finding caused.
      def cap_unraised($name): (stop($name) | word) == "cap" and (reraised($name) | not);
      def cap_spent($name): cap_unraised($name)
        and (cap_of($name) as $cap | $cap != [] and rounds($name) == [range(1; $cap[0] + 1)]);
      # A docs-only end: `Stop: cap` on rounds reading 1..k, k short of any Cap:.
      def docs_only_end($name): cap_unraised($name)
        and (rounds($name) as $rounds | ($rounds | length) as $k | cap_of($name) as $cap
             | $k > 0 and $rounds == [range(1; $k + 1)] and ($cap == [] or $k < $cap[0]));
      # The head the last local review of a `Past-cap` or `Docs-only` kind read
      # with its tree unchanged.
      def local_stamp($kind; $name): rests($kind + ": " + $name + ": tree unchanged at ") | last // "";
      def owes_fallback($name): (stop($name) | IN("not reviewed", "not landed")) or cap_spent($name);
      # A revocation appends its own Lane: line, so a stale inline stop holds.
      def inline_run: (rests("Lane: ") | last // "") == "inline";
      def greenleg($leg): [$ci.checks[] | select(named($leg))] as $checks
        | ($checks | length) > 0 and all($checks[]; .status == "success") and samehead($ci.head_sha; $head) and $ci.status == "green";
      # Names may contain colons, so a result is cut by its full literal prefix,
      # and the rest must open on `pending` or a result word, as `entry_of`
      # reads it, so `lint` never reads `- lint: strict: pass`.
      def result($name):
        (reduce ($lines[] | sub("[ \\t\\r]+$"; "")) as $line ({inside: false, rows: []};
          if $line | startswith("## ") then .inside = ($line | test("^## Verification results[ \\t\\r]*$"))
          elif .inside and ($line | startswith("- " + $name + ": "))
            and ($line | ltrimstr("- " + $name + ": ") | test("^(pending|" + $words + ")(: |$)"))
          then .rows += [$line | ltrimstr("- " + $name + ": ")]
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
         # A deferral names no leg, so it leaves no leg legally missing.
         | if $checks == [] then (if $no_checks and ([($gates // {})[] | select(. == "deferred-to-ci")] == [])
             then empty else "CI " + $leg + ": missing" end)
           else $checks[] | select(.status != "success") | "CI " + .name + ": " + .status end),
       # A pass proves the head it ran on, and any head that head covers; with
       # `Paths:`, also a head whose changes since touched none of its globs.
       ($verifications[] as $v | result($v.name) as $r | ($r | word) as $status | ($r | stamp) as $sha
         | if $status == "pass" then
             if $sha == "" then unstamped("verification " + $v.name + ": pass")
             elif $cover[$sha] == true or ($v.paths != null and $scope[$v.name][$sha] == true) then empty
             else stale("verification " + $v.name + ": pass"; $sha) end
           elif $status == "n/a" then empty
           elif $status == "deferred-to-ci" then
             if $v.leg != null and ($legs | index($v.leg)) != null and greenleg($v.leg) then empty
             else "verification " + $v.name + ": no associated green CI leg" end
           elif $status == "missing" then
             "verification " + $v.name + ": missing; record it with `run-file close 3 --result " + (if ($v.name | test("\\s")) then "\"" + $v.name + "=<word>\"" else $v.name + "=<word>" end) + "`"
           else "verification " + $v.name + ": " + $status end),
       ($reviewers[] as $r
         | if $r.fallback_for != null and (owes_fallback($r.fallback_for) | not) then empty
           elif settled($r.name) or (stop($r.name) == "inline lane" and $r.trigger == "on-request" and inline_run) then empty
           elif owes_fallback($r.name) and any($reviewers[]; .fallback_for == $r.name and settled(.name)) then empty
           elif cap_spent($r.name) and local_stamp("Past-cap"; $r.name) != "" then
             local_stamp("Past-cap"; $r.name) as $s
             # A covering stamp names the hold but never lifts it: no host reviewer read those
             # fixes. One that no longer covers the PR head (a hand-resolved conflict merge, a
             # later fix) leaves the range a human reads to waive the cap.
             | "reviewer " + $r.name + ": past cap " + (cap_of($r.name) | first | tostring) + ", local review read " + $s + ", "
               + (if $cover[$s] == true then "no host reviewer read it" else "head moved: read " + $s + ".." + $head end)
           # Docs-only fixes are what the loop leaves to a local review, so one that
           # read the PR head clears the stop (#1034).
           elif docs_only_end($r.name) and local_stamp("Docs-only"; $r.name) != "" then
             local_stamp("Docs-only"; $r.name) as $s
             | if $cover[$s] == true then empty
               else "reviewer " + $r.name + ": docs-only end, local review read " + $s + ", head moved: read " + $s + ".." + $head end
           elif (stop($r.name) | word) == "tree unchanged" then
             if reraised($r.name) then "reviewer " + $r.name + ": tree unchanged with a re-raised declined finding, which stops on cap"
             elif reviewed($r.name) | not then "reviewer " + $r.name + ": no dispositioned round"
             elif (stop($r.name) | stamp) == "" then unstamped("reviewer " + $r.name + ": tree unchanged")
             else stale("reviewer " + $r.name + ": tree unchanged"; stop($r.name) | stamp) end
           else "reviewer " + $r.name + ": " + (stop($r.name) | if word == "cap" then "cap" else . end) end),
       # A self-review stopped at its cap left its last fixes unread; a host
       # reviewer that settled on the final head read them, as did one whose
       # last stop is stamped at the self-review stamp or a head descending from it.
       (rests("Stop: self-review: ") | last // "") as $sr
       | if ($sr | word) == "cap" and (any($reviewers[]; settled(.name)) | not)
           and ((($sr | stamp) != "" and any($reviewers[]; $descends[stop(.name) | stamp] == true)) | not)
         then "self-review: stopped at cap, its last fixes read by no reviewer" else empty end,
       # A criterion is met only by a line carrying its proof; the last line decides.
       (criteria[] as $n | criterion($n) as $c
         | if $c == "" then "criterion " + $n + ": no line"
           elif $c | test("^met: .*[^ ]") then empty
           else "criterion " + $n + ": " + $c end),
       (rests("Override: ")[] | select(. != "" and . != "none" and . != "None.") | "override needed: " + .),
       ($drafts[] | "Tracker draft: " + .)
      ] as $held | {clean: ($held == []), held_by: $held}
    ') || ship_tooling "cannot evaluate clean gate evidence"
  printf '%s\n' "$answer"
  jq -e '.clean' >/dev/null <<<"$answer"
  case $? in 0) return 0 ;; 1) ;; *) ship_tooling "cannot read clean gate verdict" ;; esac
  jq -r '.held_by[]' <<<"$answer" | tail -n 40 >&2
  return 1
}
