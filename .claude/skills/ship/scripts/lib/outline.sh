#!/usr/bin/env bash
# Ship library: the Change outline check. Sourced by ../_lib.sh, never run.
# Reads SHIP_AWK_FENCE from ../_lib.sh and ship_paths_cited from body.sh.

# ship_outline_missing <body> <paths>: the paths, one per line, that the body's
# `## Change outline` section does not mention. <paths> is newline-separated. A
# path is mentioned when the section names it by its full path, or by its
# basename when no other changed path shares that basename (a derived copy whose
# source twin is changed mirrors it and does not count): both bounded as
# `ship_paths_cited` bounds a path, so `_lib.sh` is no mention of
# `tests/lib.sh` and one bare `SKILL.md` covers no two. A derived copy
# under `.claude/skills/<rest>` is also mentioned when its source twin
# `skills/<rest>` is changed and mentioned, since the copy mirrors the source.
# A path under `.claude/skills/<name>/` of a vendored skill, one the repo root's
# `skills-lock.json` lists with a `source` other than `.`, is also mentioned
# when the section cites the folder `.claude/skills/<name>/`: it has no source
# twin. A lock that cannot be read is no answer (exit 2). `skills-lock.json`
# and any `CHANGELOG.md` are generated, never expected in an outline. The
# section is found by the same `ship_inert` rule as the headings, so a
# `## Change outline` in a fence is not one; a body with no such heading
# mentions nothing, and every expected path is missing.
ship_outline_missing() { # <body> <paths>
  local text p twin rc
  text=$(awk "$SHIP_AWK_FENCE"'
    { inert = ship_inert($0) }
    !inert && /^## / { name = $0; sub(/^## /, "", name); sub(/[ \t\r]+$/, "", name); on = (name == "Change outline"); next }
    on' <<<"$1")
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case ${p##*/} in skills-lock.json|CHANGELOG.md) continue ;; esac
    _ship_outline_names "$text" "$p" "$2"; rc=$?
    [ "$rc" -eq 0 ] && continue
    [ "$rc" -eq 2 ] && return 2
    case $p in
      .claude/skills/*)
        twin=skills/${p#.claude/skills/}
        if grep -Fxq -e "$twin" <<<"$2"; then
          _ship_outline_names "$text" "$twin" "$2"; rc=$?
          [ "$rc" -eq 0 ] && continue
          [ "$rc" -eq 2 ] && return 2
        fi
        _ship_outline_vendored "$text" "$p"; rc=$?
        [ "$rc" -eq 0 ] && continue
        [ "$rc" -eq 2 ] && return 2 ;;
    esac
    printf '%s\n' "$p"
  done <<<"$2"
}

# _ship_outline_vendored <text> <path>: true when <path> sits under
# `.claude/skills/<name>/`, the text cites that folder, and the repo root's
# `skills-lock.json` lists `<name>` with a `source` other than `.`: a vendored
# skill has no source twin to name. With no lock file, no skill is vendored.
# Exit 0 named, 1 not named, 2 the matcher or the lock read failed.
_ship_outline_vendored() { # <text> <path>
  local name c lock src
  name=${2#.claude/skills/}; name=${name%%/*}
  c=$(ship_paths_cited "$1" ".claude/skills/$name/") || return 2
  [ -n "$c" ] || return 1
  lock=$(git rev-parse --show-toplevel 2>/dev/null)/skills-lock.json || return 2
  [ -e "$lock" ] || return 1
  # -e: an empty lock prints nothing, which would read as vendored.
  src=$(jq -er --arg n "$name" '.skills[$n].source // "."' "$lock" 2>/dev/null) || return 2
  [ "$src" != . ]
}

# _ship_outline_names <text> <path> <paths>: true when the text names <path> by
# its full path, or by its basename when <path>'s basename is shared by no other
# path of <paths> (a derived copy under `.claude/skills/` whose source twin is in
# <paths> is a mirror and does not count). One bounded matcher: `ship_paths_cited`.
# Exit 0 named, 1 not named, 2 the matcher failed.
_ship_outline_names() { # <text> <path> <paths>
  local p=$2 base n c
  c=$(ship_paths_cited "$1" "$p") || return 2
  [ -n "$c" ] && return 0
  base=${p##*/}
  n=$(awk -v b="$base" '
    { all[NR] = $0; has[$0] = 1 }
    END {
      for (i = 1; i <= NR; i++) {
        f = all[i]; sub(/^.*\//, "", f)
        mirror = (all[i] ~ /^\.claude\/skills\// && ("skills/" substr(all[i], 16)) in has)
        if (f == b && !mirror) n++
      }
      print n + 0 }' <<<"$3")
  [ "$n" -eq 1 ] || return 1
  c=$(ship_paths_cited "$1" "$base") || return 2
  [ -n "$c" ]
}
