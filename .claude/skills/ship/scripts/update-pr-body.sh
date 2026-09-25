#!/usr/bin/env bash
# Replace one part of the PR body and leave every other line untouched: one
# `## <section>`, creating the section at the end when the body has none, or the
# PREAMBLE, everything above the first `## ` heading.
#
#   update-pr-body <pr> --section <name> --body-file <path>
#   update-pr-body <pr> --preamble      --body-file <path>
#
# The two are exclusive, and one is required: they are the two halves of a body,
# and a call carrying both asks for two rewrites of the same body.
#
# `--section`: the body file carries the section's CONTENT, and this writes the
# `## <name>` line itself. A file that opens with that heading anyway has it
# dropped rather than doubled, and a body already carrying the pair of headings
# that mistake left, one repeating the other inside the same section, is
# collapsed to one heading by an ordinary write. A `## <name>` that follows a
# DIFFERENT heading is a section of its own, not a duplicate, and is replaced
# like any other.
#
# `--preamble`: the body file is the whole preamble. A preamble is always there,
# empty at the emptiest, so it is always replaced and the create path is unused.
# A closing line the old preamble carried and the file does not is carried over,
# where `open-pr` puts it, so a rewrite that says nothing about closing keeps
# the link to the issue.
# A file carrying its own closing line states what the PR closes and is left
# alone, whichever issues it names.
#
# A section write replaces everything from its own `## ` heading to the next one,
# which is what fixes where the attribution footer `open-pr` places has to sit:
# under a `## Attribution` heading of its own, after every section a later phase
# rewrites. A footer left loose at the end of the last section sits inside that
# section, and the phase-7 `--section Review` write takes it with the section.
#
# A body file whose fence state ends open is refused before any host read: an
# open fence inverts the in-fence state for the rest of the body, so the rewrite
# reads every later `## ` as example text and swallows the sections between them.
# A `--preamble` file carrying an unfenced `## ` heading is refused there too:
# the preamble is by definition what sits above the first heading, so a heading
# in it would open a section the write then puts the carried closing line inside.
#
# stdout: {pr, section, replaced, created, sections[]}
#         {pr, section: null, preamble: true, replaced, created, sections[]}
#   sections[]: the `## ` headings of the body AFTER the write, so a swallowed
#   section is visible in the verdict rather than in a review eight minutes later.
# exit: 0 · 1 update failed, with the host's status where there was one · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: update-pr-body <pr> (--section <name> | --preamble) --body-file <path>'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
pr=$1; shift
case $pr in -*) ship_tooling "$usage" ;; esac
section=""; preamble=false; file=""
while [ $# -gt 0 ]; do
  case $1 in
    --section) case ${2:-} in ""|-*) ship_tooling "$usage" ;; esac; section=$2; shift 2 ;;
    --preamble) preamble=true; shift ;;
    --body-file) case ${2:-} in ""|-*) ship_tooling "$usage" ;; esac; file=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done
if [ "$preamble" = true ]; then
  [ -z "$section" ] || ship_tooling "$usage"
else
  [ -n "$section" ] || ship_tooling "$usage"
fi
[ -f "$file" ] || ship_tooling "$usage"
content=$(cat "$file") || ship_tooling "cannot read $file"
unclosed=$(ship_fence_unclosed "$content")
[ -z "$unclosed" ] || ship_tooling "body file ends inside an unclosed fence ($unclosed)"
# Counted, not read: `ship_body_headings` prints the heading TEXT, and a bare
# `## ` has none, so a non-empty test passes on the one heading most likely to
# be a typo.
if [ "$preamble" = true ] && [ "$(ship_body_headings "$content" | wc -l)" -gt 0 ]; then
  ship_tooling "preamble body file carries a \`## \` heading: the preamble ends at the first one"
fi
ship_load_host

body=$(host_pr_get "$pr" | jq -r .body) || ship_tooling "cannot read PR $pr"
new=$(mktemp); trap 'rm -f "$new"' EXIT
if [ "$preamble" = true ]; then
  ship_body_replace_preamble "$body" "$file" > "$new"
  replaced=true; created=false
elif ship_body_replace_section "$body" "$section" "$file" > "$new"; then
  replaced=true; created=false
else
  replaced=false; created=true
fi
answer=$(host_pr_set_body "$pr" "$new") || ship_fail_host "PR body update failed" "$answer"
sections=$(ship_body_headings "$(cat "$new")")
jq -n --argjson pr "$pr" --arg s "$section" --argjson p "$preamble" \
  --argjson r "$replaced" --argjson c "$created" --arg h "$sections" \
  '{pr: $pr}
   + (if $p then {section: null, preamble: true} else {section: $s} end)
   + {replaced: $r, created: $c,
      sections: ($h | split("\n") | map(select(. != "")))}'
