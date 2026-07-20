#!/usr/bin/env bash
# Ship phase-0 isolation when the EnterWorktree tool is absent: fresh worktree
# on a clean branch off the up-to-date default branch. Prints the worktree path.
#
#   scripts/isolate.sh <type> <slug> <issue>     e.g. isolate.sh fix retry-401 812
set -u
TYPE="${1:?usage: isolate.sh <type> <slug> <issue>}"
SLUG="${2:?usage: isolate.sh <type> <slug> <issue>}"
N="${3:?usage: isolate.sh <type> <slug> <issue>}"
BRANCH="$TYPE/$SLUG-$N"

ROOT=$(git rev-parse --show-toplevel) || exit 1
git -C "$ROOT" fetch origin --quiet
DEFAULT=$(git -C "$ROOT" symbolic-ref -q --short refs/remotes/origin/HEAD || echo origin/main)

git -C "$ROOT" rev-parse --verify -q "refs/heads/$BRANCH" >/dev/null && {
  echo "branch $BRANCH already exists — preflight should have stopped this run" >&2
  exit 1
}

WT="$ROOT/.claude/worktrees/$SLUG-$N"
git -C "$ROOT" worktree add -b "$BRANCH" "$WT" "$DEFAULT" >/dev/null || exit 1
echo "$WT"
