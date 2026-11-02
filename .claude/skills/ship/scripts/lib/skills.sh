#!/usr/bin/env bash
# Ship library: a skill's frontmatter and the check that the skills ship composes
# are installed at their pins. Sourced by ../_lib.sh, never run.

# ship_frontmatter <file> <key>: one `  <key>: <value>` line from a skill's
# YAML frontmatter, value only. Stops at the closing `---`, so a body line that
# looks like the key is body text.
ship_frontmatter() {
  awk -v k="  $2:" 'NR>1 && /^---$/{exit} index($0, k) == 1 {sub(/^[^:]*: */, ""); print; exit}' "$1"
}

# ship_missing_skill_reasons <root> <composes>: the skills ship loads through
# the Skill tool, checked against a checkout before the claim. <composes> is
# ship's `metadata.composes` line: space-separated `<source-repo>#<sha>:<skill>`
# entries, the single place the list lives, each pinned at the upstream commit
# the source repo tested. Prints one reason per entry whose pin is not a 40-hex
# sha, one per skill whose `<root>/.claude/skills/<skill>/SKILL.md` is absent,
# and one per present skill `<root>/skills-lock.json` records at another `ref`
# or none, each of the last two carrying the pinned line that installs it;
# prints nothing when every one is well pinned, there and at its pin. An absent
# lock records no ref, so every copy reads as off its pin; a lock that is not one
# JSON object prints one `skills lock unreadable` reason and nothing else.
#
# Only the consumer repo's own `.claude/skills` counts: a global copy under
# ~/.claude/skills is a personal skill rather than this repo's derived copy, per
# setup-skills.
ship_missing_skill_reasons() {
  local root=$1 entry source skill ref lock='{}' pin='^[^/#:]+/[^/#:]+#[0-9a-f]{40}$'
  local -a entries
  # Every install line below rewrites the lock, and the CLI reads one it cannot
  # parse as empty, erasing every other entry: such a lock gets no install line.
  if [ -e "$root/skills-lock.json" ]; then
    lock=$(jq -cs 'if length == 1 and (.[0] | type) == "object" then .[0] else error end' \
      "$root/skills-lock.json" 2>/dev/null) || {
      echo 'skills lock unreadable: skills-lock.json; repair it, then re-run preflight'
      return 0
    }
  fi
  # read -ra, not an unquoted expansion: the split on spaces is intentional and
  # explicit, and a glob character in an entry stays a literal character.
  read -ra entries <<<"$2"
  for entry in ${entries[@]+"${entries[@]}"}; do
    source=${entry%%:*}; skill=${entry##*:}
    if ! [[ $source =~ $pin ]]; then
      printf 'composes pin invalid: %s; want <owner>/<repo>#<40-hex sha>:<skill>\n' "$entry"
      continue
    fi
    if ! [ -f "$root/.claude/skills/$skill/SKILL.md" ]; then
      printf 'skill missing: %s; run npx skills add %s --skill %s --agent claude-code -y\n' \
        "$skill" "$source" "$skill"
      continue
    fi
    ref=$(jq -r --arg k "$skill" '(.skills[$k]?.ref? | select(type == "string" and . != "")) // "none"' <<<"$lock")
    [ "$ref" = "${source#*#}" ] && continue
    printf 'skill off pin: %s at %s, pinned %s; run npx skills add %s --skill %s --agent claude-code -y\n' \
      "$skill" "$ref" "${source#*#}" "$source" "$skill"
  done
}
