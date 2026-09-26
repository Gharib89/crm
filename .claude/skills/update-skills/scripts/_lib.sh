# What update-skills' mechanics share, sourced after Ship's _lib.sh: `plan` and
# `heads` must read the composes lines and the lock the same way, or the pin a
# plan installs and the base `heads` compares against part.

# A lock entry the source repo installs: from `.` in the source repo itself,
# from Gharib89/skills anywhere else.
# shellcheck disable=SC2034  # read by plan and heads
us_source_repo='def source_repo: .source | ascii_downcase | . == "." or . == "gharib89/skills";'

# us_composed <checkout>: every entry of the checkout's Ship and setup-skills
# composes lines as a JSON array of {skill, source, pin}, sorted by skill. The
# first entry naming a skill wins; one not of the `<owner>/<repo>#<sha>:<skill>`
# form is dropped, since preflight already refuses it as `composes pin invalid`.
us_composed() {
  local s line=""
  for s in ship setup-skills; do
    [ -f "$1/.claude/skills/$s/SKILL.md" ] && line="$line $(ship_frontmatter "$1/.claude/skills/$s/SKILL.md" composes)"
  done
  jq -cn --arg c "$line" '[$c | split(" ")[] | select(test("^[^/#:]+/[^/#:]+#[0-9a-f]{40}:[^:]+$"))
    | capture("^(?<source>[^#]+)#(?<pin>[^:]+):(?<skill>.+)$") | {skill, source, pin}] | unique_by(.skill)'
}
