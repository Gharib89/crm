#!/usr/bin/env bash
# Ship library: the branch convention and the checkout, worktree and base-ref
# reads. Sourced by ../_lib.sh, never run.

# preflight greps for on the remote and open-pr checks the branch ends in.
ship_branch() { printf '%s/%s-%s' "$1" "$2" "$3"; }

# The main checkout, even when run from inside a worktree: --git-common-dir
# points at the primary .git, so a run started in a worktree lands the new one
# in the main checkout's container, not its own.
ship_main_checkout() {
  local common
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  dirname "$common"
}

# ship_record_root: where a run keeps its record,
# ${XDG_STATE_HOME:-$HOME/.local/state}/ship/<repo key>, <repo key> the POSIX
# `cksum` of the physical git common dir path. Keyed on the common dir, the main
# checkout and every worktree of it resolve the same directory and two clones
# get different ones, short of a CRC collision among one user's clones. It sits
# outside every checkout because a session isolated by `EnterWorktree` refuses
# an Edit, or a Bash command, that names a `.git` path (measured, Claude Code
# 2.1.296), so the run could not write its own record there; the key is digits
# for the same reason, since digits never spell `git`. A relative
# XDG_STATE_HOME is ignored, as the XDG spec requires, since it would resolve
# under each worktree's own cwd. `cksum` because POSIX requires it, so no
# consumer needs a new tool. A state directory, not a temp one, because a wiped
# temp directory took the record with it in three runs. `run-file`, `cleanup`
# and the grade check in `open-pr` and `update-pr-title` read it. Returns 1
# outside a git checkout and 2 when cksum fails, whose empty key would put every
# repo's records under one root.
ship_record_root() {
  local common key state=${XDG_STATE_HOME:-}
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  common=$(cd "$common" && pwd -P) || return 1
  key=$(printf '%s' "$common" | cksum 2>/dev/null) || return 2
  key=${key%% *}
  [[ $key =~ ^[0-9]+$ ]] || return 2
  [[ $state == /* ]] || state=$HOME/.local/state
  printf '%s/ship/%s' "$state" "$key"
}

# Container inside the main checkout: <main>/.claude/worktrees. `EnterWorktree`
# enters a worktree there with no permission prompt and prompts everywhere else,
# whatever `permissions.allow` says (measured, Claude Code 2.1.296).
ship_worktree_container() {
  local root; root=$(ship_main_checkout) || return 1
  printf '%s/.claude/worktrees' "$root"
}

# ship_worktree_at <worktree> <branch> <sha>: whether the worktree is on the
# branch, at the sha, with nothing uncommitted: the one a Rework run reuses, so
# it never builds on work it cannot see on the PR. A read that fails is a no.
ship_worktree_at() {
  local s
  [ "$(git -C "$1" rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$2" ] \
    && [ "$(git -C "$1" rev-parse HEAD 2>/dev/null)" = "$3" ] \
    && s=$(git -C "$1" status --porcelain 2>/dev/null) && [ -z "$s" ]
}

# The base ref, resolved from origin/HEAD rather than a hardcoded branch (ADO
# defaults vary). Refreshes the symbolic ref where the clone lacks one, which
# asks the origin; `--local` reads the ref as it is, for a check that skips when
# the base is unknown rather than reach a host.
ship_base_ref() {
  local ref
  ref=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null) \
    || { [ "${1:-}" != --local ] && git remote set-head origin -a >/dev/null 2>&1 \
         && ref=$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null); } \
    || return 1
  printf '%s' "${ref#refs/remotes/}"
}
