#!/usr/bin/env bash
# The upstream heads `plan` takes: for each GitHub-installed skill in a
# checkout's lock that the source repo does not own, the commit a refresh
# would move it to.
#
#   heads <checkout>
#
# A skill's base is its pin on the checkout's composes lines (ship's and
# setup-skills'), else the lock's recorded ref. Its head is the upstream
# default branch's HEAD when it has no base or its folder changed between the
# base and HEAD, and the base itself otherwise: an upstream commit that touches
# only other skills' folders is no drift, and a repo-level comparison would
# report every skill in a busy upstream as drifted on every run. A skill at the
# repo root, or with no skillPath, owns the whole repo; a compare listing 300
# files, GitHub's cap, counts as a folder change, since the list may be cut.
#
# Read through GitHub's public REST API with curl rather than `gh`, so a
# checkout whose host is Azure DevOps, with no GitHub credentials, still gets
# heads; GH_TOKEN or GITHUB_TOKEN, where set, only raises the rate limit. An
# upstream that does not answer costs only its own skills a head: each is an
# `unreachable` row, and every other upstream is still read.
#
# stdout: {"heads": {"<skill>": "<sha>"}, "unreachable": [{skill, error}]}
# exit: 0 · 1 an unreadable lock · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../ship/scripts/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || ship_tooling "cannot source update-skills' _lib.sh"

usage='usage: heads <checkout>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ $# -eq 1 ] || ship_tooling "$usage"
root=$1
case $root in -*) ship_tooling "$usage" ;; esac
command -v curl >/dev/null || ship_tooling "curl not installed"

lock=$(jq -ce 'select(type == "object")' "$root/skills-lock.json" 2>/dev/null) || ship_fail "cannot read lock: $root/skills-lock.json"
token=${GH_TOKEN:-${GITHUB_TOKEN:-}}

api=https://api.github.com
# get <path>: one GET, the body in $body, curl's own error on stderr as
# evidence; on failure $err names the request and get returns 1. Bounded in
# time, so an upstream that accepts and never answers fails here too.
get() {
  body=$(curl -fsSL --connect-timeout 10 --max-time 30 -H 'Accept: application/vnd.github+json' ${token:+-H "Authorization: Bearer $token"} "$api/$1") \
    || { err="cannot read $api/$1"; return 1; }
}

# One `<skill> <source> <folder> <base>` row per skill to look up, joined by
# the unit separator: tab is whitespace to `read`, which would collapse a root
# skill's empty folder into the next field.
rows=$(jq -r --argjson composed "$(us_composed "$root")" "$us_source_repo"'
  ($composed | map({key: .skill, value: .pin}) | from_entries) as $pins
  | .skills | to_entries | sort_by(.key)[]
  | select(.value.sourceType == "github" and (.value | source_repo | not))
  | [.key, .value.source, ((.value.skillPath // "") | sub("/?SKILL\\.md$"; "")), ($pins[.key] // .value.ref // "")] | join("\u001f")' <<<"$lock")

# seen maps a source to its HEAD, or to "!<error>" once it failed to answer.
out='{}' miss='[]' seen='{}'
while IFS=$'\037' read -r skill src dir base; do
  [ -n "$skill" ] || continue
  head=$(jq -r --arg s "$src" '.[$s] // ""' <<<"$seen")
  if [ -z "$head" ]; then
    if get "repos/$src/commits/HEAD"; then
      head=$(jq -r '.sha // ""' <<<"$body")
      [ -n "$head" ] || head="!no sha in $api/repos/$src/commits/HEAD"
    else
      head="!$err"
    fi
    seen=$(jq -c --arg s "$src" --arg h "$head" '.[$s] = $h' <<<"$seen")
  fi
  case $head in
    '!'*) miss=$(jq -c --arg k "$skill" --arg e "${head#!}" '. + [{skill: $k, error: $e}]' <<<"$miss"); continue ;;
  esac
  at=$head
  if [ -n "$base" ] && [ "$base" != "$head" ]; then
    if ! get "repos/$src/compare/$base...$head"; then
      miss=$(jq -c --arg k "$skill" --arg e "$err" '. + [{skill: $k, error: $e}]' <<<"$miss"); continue
    fi
    [ "$(jq -r --arg d "${dir:+$dir/}" '(.files | length) >= 300 or any(.files[]?.filename; startswith($d))' <<<"$body")" = true ] || at=$base
  elif [ -n "$base" ]; then
    at=$base
  fi
  out=$(jq -c --arg k "$skill" --arg v "$at" '.[$k] = $v' <<<"$out")
done <<<"$rows"
jq -cn --argjson h "$out" --argjson u "$miss" '{heads: $h, unreachable: $u}'
