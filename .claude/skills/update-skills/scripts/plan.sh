#!/usr/bin/env bash
# The one deterministic core of update-skills: what a refresh moves, as one
# JSON plan. Run after the source-repo skills are refreshed, because the pins a
# refresh installs are the refreshed Ship's and setup-skills': the working tree
# is the new side, and <old> (default HEAD, the commit before the refresh) the
# old one.
#
#   plan <checkout> <heads-file> [--old <ref>]
#
# <heads-file> is `heads`' answer, whose `heads` map is read, injected rather
# than fetched here so the plan is testable with no network. A skill it omits
# has no known head: never a drift row, never an offered update.
#
# stdout: {"mode": "consumer"|"source", "old": "<ref>",
#          "source_skills": [{skill, old_version, new_version, changelog}],
#          "composed": [{skill, source, pin, old_ref, install}],
#          "drift": [{skill, pin, head}],
#          "others": [{skill, source, old_ref, head, install}],
#          "sections": [{section, template}]}
#   source_skills: lock entries installed from the source repo; changelog is
#     the path of the skill's CHANGELOG.md; a null old_version is a skill the
#     old tree lacked.
#   composed: every entry of the refreshed composes lines, installed at its
#     pin, the first entry naming a skill winning.
#   old_ref, in composed and others: the old tree's lock ref, null for a copy
#     installed unpinned or absent. Read from <old> rather than the working
#     tree, which the installs rewrite, so a re-run mid-refresh plans the same.
#   drift: a composed skill whose upstream head is not its pin.
#   others: every other repo-scoped lock entry whose head differs from its old_ref,
#     installed at the head so the next run has a ref to compare.
#   sections: the setup-skills sections whose template file or directory
#     differs between the old and new copies; SKILL.md is not a template.
#   mode: `source` where the lock installs ship from `.`, the source repo itself.
# exit: 0 · 1 an unreadable lock, heads file or old ref · 2 usage
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../ship/scripts/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || ship_tooling "cannot source update-skills' _lib.sh"

usage='usage: plan <checkout> <heads-file> [--old <ref>]'
ship_help "$usage" "$@"
[ -n "${1:-}" ] && [ -n "${2:-}" ] || ship_tooling "$usage"
root=$1 headsf=$2; shift 2
case $root in -*) ship_tooling "$usage" ;; esac
case $headsf in -*) ship_tooling "$usage" ;; esac
old=HEAD
while [ $# -gt 0 ]; do
  case $1 in
    --old) [ -n "${2:-}" ] || ship_tooling "$usage"; old=$2; shift 2 ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done

git -C "$root" rev-parse -q --verify "$old^{commit}" >/dev/null || ship_fail "not a commit in $root: $old"
lock=$(jq -ce 'select(type == "object")' "$root/skills-lock.json" 2>/dev/null) || ship_fail "cannot read lock: $root/skills-lock.json"
heads=$(jq -ce '.heads | select(type == "object")' "$headsf" 2>/dev/null) || ship_fail "cannot read heads: $headsf"
oldlock=$(git -C "$root" show "$old:skills-lock.json" 2>/dev/null | jq -ce 'select(type == "object")' 2>/dev/null) || oldlock='{}'

skills=.claude/skills

versions='[]'
while IFS= read -r s; do
  [ -n "$s" ] || continue
  new=$(ship_frontmatter "$root/$skills/$s/SKILL.md" version 2>/dev/null)
  was=$(git -C "$root" show "$old:$skills/$s/SKILL.md" 2>/dev/null | ship_frontmatter /dev/stdin version)
  versions=$(jq -c --arg s "$s" --arg o "$was" --arg n "$new" --arg c "$skills/$s/CHANGELOG.md" \
    '. + [{skill: $s, old_version: (if $o == "" then null else $o end), new_version: (if $n == "" then null else $n end), changelog: $c}]' <<<"$versions")
done < <(jq -r "$us_source_repo"'.skills | to_entries | sort_by(.key)[] | select(.value | source_repo) | .key' <<<"$lock")

# A template's content as `<blob sha> <path>` lines, old tree and working tree,
# so a directory's added, removed and changed files all read as a difference.
old_blobs() { git -C "$root" ls-tree -r "$old" -- "$1" | awk '{print $3, $4}' | LC_ALL=C sort; }
new_blobs() {
  (cd "$root" && find "$1" -type f 2>/dev/null | while IFS= read -r f; do
    printf '%s %s\n' "$(git hash-object "$f")" "$f"
  done) | LC_ALL=C sort
}
sections='[]'
for pair in pull_request_template.md:pr-template reviewers:reviewer-scaffolding local-gate.sh:local-gate \
  coding-standards.md:coding-standards dimension-labels.md:dimension-labels issue-tracker-ado.md:ado-tracker-doc; do
  t=${pair%%:*} p=$skills/setup-skills/${pair%%:*}
  [ "$(old_blobs "$p")" = "$(new_blobs "$p")" ] && continue
  sections=$(jq -c --arg s "${pair#*:}" --arg t "$t" '. + [{section: $s, template: $t}]' <<<"$sections")
done

jq -n --arg old "$old" --argjson composed "$(us_composed "$root")" --argjson lock "$lock" --argjson oldlock "$oldlock" \
  --argjson heads "$heads" --argjson versions "$versions" --argjson sections "$sections" "$us_source_repo"'
  def install($src; $ref; $s): "npx skills add \($src)#\($ref) --skill \($s) --agent claude-code -y";
  ($composed | map(.skill)) as $names
  | {mode: (if $lock.skills.ship.source? == "." then "source" else "consumer" end),
     old: $old,
     source_skills: $versions,
     composed: [$composed[] | . + {old_ref: ($oldlock.skills[.skill].ref? // null), install: install(.source; .pin; .skill)}],
     drift: [$composed[] | select($heads[.skill] != null and $heads[.skill] != .pin) | {skill, pin, head: $heads[.skill]}],
     others: [$lock.skills | to_entries | sort_by(.key)[]
       | select(.value | source_repo | not)
       | select(.key as $k | $names | index($k) | not)
       | (.key as $k | $oldlock.skills[$k].ref? // null) as $was
       | select($heads[.key] != null and $heads[.key] != $was)
       | {skill: .key, source: .value.source, old_ref: $was, head: $heads[.key],
          install: install(.value.source; $heads[.key]; .key)}],
     sections: $sections}'
