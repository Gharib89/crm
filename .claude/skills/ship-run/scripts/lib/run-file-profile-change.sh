#!/usr/bin/env bash
# Run-file mechanic: the ship-profile comparison the clean gate holds on. Sourced by ../run-file.sh, never run.
# shellcheck disable=SC2154,SC2034 # shares the globals of run-file.sh (head) and sets profile_held, profile_blob

# Sets profile_held, the holds a profile change earns as a JSON array, and
# profile_blob, the profile's blob at the head (empty when unreadable): the
# head's committed copy against the base branch fetched now (the PR's base:
# open-pr opens every PR on it), and against the checkout's copy. Blob ids
# compare, so a read that fails is a hold, never a match. A single working-tree
# diff would miss a committed change reverted locally, and an untracked first
# profile.
profile_change() { # profile_change <checkout profile path>, with head parsed
  local base h bblob wblob held=()
  profile_blob=""
  if ! h=$(commit_of "$head") || ! profile_blob=$(git rev-parse -q --verify "$h:$SHIP_PROFILE_REL" 2>/dev/null); then
    profile_blob=""
    held+=("ship profile: cannot read it at head $head")
  else
    if ! base=$(ship_base_ref) \
      || ! git fetch -q origin "+refs/heads/${base#origin/}:refs/remotes/$base" >/dev/null 2>&1; then
      held+=("ship profile: cannot fetch base ${base:-origin/HEAD}, so a change to it cannot be ruled out")
    elif ! bblob=$(git rev-parse -q --verify "refs/remotes/$base:$SHIP_PROFILE_REL" 2>/dev/null); then
      held+=("ship profile: absent from base $base, so head $head adds it")
    elif [ "$bblob" != "$profile_blob" ]; then
      held+=("ship profile: head $head differs from base $base")
    fi
    wblob=$(git hash-object -- "$1" 2>/dev/null) || wblob=""
    [ "$wblob" = "$profile_blob" ] || held+=("ship profile: the checkout's copy differs from the one committed at head $head")
  fi
  # Bash 3.2 reads an empty array as unbound under set -u.
  profile_held=$(jq -cn '$ARGS.positional' --args ${held[@]+"${held[@]}"})
}
