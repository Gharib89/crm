#!/usr/bin/env bash
# Ship library: where the ship profile lives and what its lines say. Sourced by
# ../_lib.sh, never run.

# ship_profile_path: the ship profile in the checkout the caller runs in, rather
# than the main one: a run inside a worktree is governed by the profile on its
# own branch, and a repo's first profile lands on a branch before it ever
# reaches main. Fails outside a checkout. Whether the file is there is the
# caller's own question, because the two that ask it answer differently: for
# preflight an absent profile is a stop, and for `ci-wait` it is the default
# grace standing.
ship_profile_path() {
  local here
  here=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
  printf '%s/docs/agents/ship.md' "$here"
}

# ship_body_check_path: the repo's own body checker, the path from the checkout
# root on the profile's `## PR` `Body-check:` line, which `open-pr` and
# `update-pr-body` run through `ship_require_body_check` on the body they would
# publish. Prints nothing for no profile, no line or `None.`: a repo with no
# checker publishes as it always did. A line inside a fenced block is example
# text, not the setting; a profile that exists and cannot be read returns 1,
# which the caller reads as tooling, not as no checker.
ship_body_check_path() {
  local profile text
  profile=$(ship_profile_path) && [ -f "$profile" ] || return 0
  text=$(cat "$profile") || return 1
  ship_unfenced "$text" | awk '/^## /{f = ($0 ~ /^## PR[ \t\r]*$/)}
    f && /^Body-check:/ { sub(/^Body-check:[ \t]*/, ""); sub(/[ \t\r]+$/, ""); if ($0 != "None.") print; exit }'
}

# ship_no_checks_expected <profile-body>: true when the profile's `## CI` block
# says both that no leg runs on a PR (`Legs: None.`) and that an empty check list
# is legal (`No-checks legal: yes`). That pair answers, up front, the question
# `ci-wait`'s no-checks grace exists to ask, so a run in such a repo waits
# nothing instead of two minutes for a check the profile says never comes
# (#218). Either fact alone keeps the grace: a repo with no legs that still
# calls an empty list illegal is waiting for a leg nobody named, and a repo with
# a leg has a check coming whatever the second line says.
#
# Read inside `## CI` and nowhere else, the way every profile fact is read from
# its own section: `Legs:` is that section's word, and a line of the same shape
# under another heading is prose. Both values are matched as the profile spells
# them, lower case: one function reading its own section two ways is the drift
# a second spelling starts.
ship_no_checks_expected() { # <profile-body>
  local ci
  ci=$(awk '/^## /{f = ($0 ~ /^## CI[ \t\r]*$/)} f' <<<"$1")
  grep -Eq '^Legs:[[:space:]]*None\.[[:space:]]*$' <<<"$ci" || return 1
  ship_no_checks_legal "$1"
}
# ship_no_checks_legal <profile-body>: true when the profile's `## CI` block says
# `No-checks legal: yes`, read in that section only like every profile fact.
ship_no_checks_legal() { # <profile-body>
  local ci
  ci=$(awk '/^## /{f = ($0 ~ /^## CI[ \t\r]*$/)} f' <<<"$1")
  grep -Eq '^No-checks legal:[[:space:]]*yes([^[:alnum:]]|$)' <<<"$ci"
}

# ship_profile_legs <profile-body>: the leg names the profile's `Legs:` names
# under `## CI`, one per line, the text before each entry's first colon. An
# entry is a `Legs: <name>: <what>` line, repeated or not, or a line below a
# bare `Legs:`, up to a blank line or the section's next label. `Legs: None.`
# prints nothing; a section with no `Legs:` line fails, which is "unknown", not
# "none". An entry that yields no name (no colon, or nothing before it) fails
# the same way: printed as nothing it would read as `Legs: None.`, and a red
# check no leg names would then pass as no-checks.
ship_profile_legs() { # <profile-body>
  awk 'function emit(  n) { sub(/^[ \t]+/, ""); n = index($0, ":"); if (n > 1) print substr($0, 1, n - 1); else bad = 1 }
    /^## / { f = ($0 ~ /^## CI[ \t\r]*$/); on = 0; next }
    !f { next }
    /^Legs:/ { seen = 1; on = 1; sub(/^Legs:[ \t]*/, ""); sub(/[ \t\r]+$/, "")
               if ($0 != "" && $0 != "None.") emit(); next }
    on && (/^[ \t\r]*$/ || /^(No-checks legal|Push policy):/) { on = 0; next }
    on { sub(/[ \t\r]+$/, ""); emit() }
    END { exit (!seen || bad) }' <<<"$1"
}
