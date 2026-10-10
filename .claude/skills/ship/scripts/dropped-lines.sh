#!/usr/bin/env bash
# Which deleted lines did the diff drop rather than move or rewrite? A self-review
# reads a diff for what it adds; a block of lines that left with nothing like it
# arriving anywhere is the finding a read of the added side never makes, and a
# long diff hides it. Pure git, host-neutral, no host call.
#
#   dropped-lines [--base <ref>]
#
# The diff is `git diff -U0` of the working tree against the base, plus every
# untracked file as a pure addition, so committed, staged and uncommitted work
# are all in it and a move into a file git has not been told about is still a
# move. `.claude/skills/` is left out whole: it is a skill's installed copy, the
# same text as its source, so a drop there is the source's drop reported twice.
# The base is the merge base of HEAD and origin/HEAD, or the commit --base names.
#
# A removed line is matched when some added line anywhere in the diff, in any
# file, equals it once leading and trailing whitespace is trimmed. Blank lines
# are never matched and never count. A block is a maximal run of consecutive
# removed lines in one hunk, no matched line among them (a blank one inside the
# run does not break it), holding at least DROPPED_MIN non-blank lines: one or
# two unmatched removed lines are an edit, not a drop. Text that was rewritten
# rather than moved is unmatched too, so a reworded block is reported and needs
# its disposition like a deleted one. Renames are read as a delete plus an add,
# so a file renamed with its content intact reports nothing.
#
# stdout: {base, blocks: [{id, file, line, lines, text}]}
#   base: the commit diffed against
#   id: "<file>:<line>", the block's handle for a disposition
#   file, line: where the block's first line was, in the base's version of the
#     file (a deleted file keeps its old path)
#   lines: the block's non-blank lines
#   text: the first up to 5 of them, trimmed
#   blocks: empty when nothing was dropped
# exit: 0 the answer, the list possibly empty · 2 tooling: not a git checkout,
#   no resolvable base, a bad call
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: dropped-lines [--base <ref>]'
ship_help "$usage" "$@"

# Fewer non-blank unmatched removed lines than this is an edit, not a drop.
DROPPED_MIN=3

ref=
while [ $# -gt 0 ]; do
  case $1 in
    --base) ship_flag_value "$usage" "${2:-}"; ref=$2; shift 2 ;;
    *) ship_tooling "$usage" ;;
  esac
done

# `cd ""` succeeds without moving, so the toplevel is read and checked first.
root=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$root" ] || ship_tooling "not inside a git checkout"
cd "$root" || ship_tooling "cannot enter $root"
if [ -z "$ref" ]; then
  tip=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
  ref=$(git merge-base HEAD "$tip") || ship_tooling "no merge base between HEAD and $tip"
fi
base=$(git rev-parse --verify -q "$ref^{commit}") || ship_tooling "$ref is not a commit"

# Explicit prefixes and no rename detection: a config that drops the prefix or
# pairs a rename would change the headers read below, and a rename is a delete
# plus an add here, the add being what matches the delete.
diffopts=(-c core.quotepath=off diff --no-color --no-ext-diff --no-renames -U0 --src-prefix=a/ --dst-prefix=b/)
stream=$(git "${diffopts[@]}" "$base" -- . ':(exclude).claude/skills') || ship_tooling "cannot diff against $base"
untracked=$(git ls-files --others --exclude-standard) || ship_tooling "cannot list untracked files"
while IFS= read -r f; do
  case $f in "" | .claude/skills/*) continue ;; esac
  # --no-index exits 1 when the files differ, which an untracked file always does.
  one=$(git "${diffopts[@]}" --no-index -- /dev/null "$f"); st=$?
  [ "$st" -le 1 ] || ship_tooling "cannot diff untracked file $f"
  stream=$stream$'\n'$one
done <<EOF
$untracked
EOF

# A hunk's own counts say where it ends, so a removed line reading `--- x` is a
# line, not the next file header.
records=$(printf '%s\n' "$stream" | awk -v min="$DROPPED_MIN" '
  function trim(s) { sub(/^[ \t\r]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
  function flush(   j, top) {
    if (cnt >= min) {
      print "B\t" sline "\t" cnt "\t" sfile
      top = cnt < 5 ? cnt : 5
      for (j = 1; j <= top; j++) print "T\t" txt[j]
    }
    cnt = 0
  }
  ro > 0 || rn > 0 {
    c = substr($0, 1, 1)
    if (c == "\\") next
    if (c == "-" && ro > 0) {
      ro--; n++; rfile[n] = file; rhunk[n] = hunk; rline[n] = oline++; rtext[n] = trim(substr($0, 2)); next
    }
    if (c == "+" && rn > 0) {
      rn--; t = trim(substr($0, 2)); if (t != "") added[t] = 1; next
    }
    ro = 0; rn = 0
  }
  /^diff --git / { oldp = ""; newp = ""; next }
  /^--- / { p = substr($0, 5); sub(/\t.*$/, "", p); oldp = (p == "/dev/null") ? "" : substr(p, 3); next }
  /^\+\+\+ / { p = substr($0, 5); sub(/\t.*$/, "", p); newp = (p == "/dev/null") ? "" : substr(p, 3); next }
  /^@@ / {
    h = $2; g = $3
    sub(/^-/, "", h); sub(/^\+/, "", g)
    k = split(h, hp, ","); oline = hp[1] + 0; ro = (k > 1) ? hp[2] + 0 : 1
    k = split(g, gp, ","); rn = (k > 1) ? gp[2] + 0 : 1
    file = (newp != "") ? newp : oldp
    hunk++
    next
  }
  END {
    prev = -1; cnt = 0
    for (i = 1; i <= n; i++) {
      if (rhunk[i] != prev) { flush(); prev = rhunk[i] }
      t = rtext[i]
      if (t == "") continue
      if (t in added) { flush(); continue }
      if (cnt == 0) { sfile = rfile[i]; sline = rline[i] }
      cnt++
      if (cnt <= 5) txt[cnt] = t
    }
    flush()
  }') || ship_tooling "cannot read the diff"

printf '%s\n' "$records" | jq -Rn --arg base "$base" '
  reduce (inputs | select(length > 0)) as $l ({blocks: []};
    if $l[0:2] == "B\t" then
      ($l[2:] | split("\t")) as $f
      | ($f[2:] | join("\t")) as $file
      | .blocks += [{id: ($file + ":" + $f[0]), file: $file, line: ($f[0] | tonumber), lines: ($f[1] | tonumber), text: []}]
    else
      .blocks[-1].text += [$l[2:]]
    end)
  | {base: $base, blocks: .blocks}' || ship_tooling "cannot build the answer"
