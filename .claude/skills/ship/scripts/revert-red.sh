#!/usr/bin/env bash
# revert-red: prove a test fails for the reason it exists, by running it on a
# tree with the fix taken out.
#
# A test written to prove a fix proves nothing until it has been red without the
# fix, and a vacuous assertion reads exactly like a sound one in a diff. This
# does the revert in a temp worktree at HEAD, so the working tree is never
# touched: the test runs once on the unreverted tree, then each <path> is
# restored from the merge base with origin/HEAD, or deleted where the fix added
# it, and the test runs again. A test that is not green at HEAD proves nothing
# when it goes red later, so that first run is the precondition. It reads
# committed state, so commit the test and the fix first.
#
# The test runs behind a `gh` and an `az` stub this mechanic writes itself, so it
# needs nothing from the repo it runs in: a revert can take out what swaps in a
# test's Host fake, and the reverted tree would then call the real host with the
# developer's own credentials. Each stub records its call and exits 127, the
# shell's "command not found". A run that reaches a host, at HEAD or reverted, is
# exit 2, since the red it shows is the stub's 127.
#
#   revert-red <test> <path>...
#
# <test> and each <path> are files relative to the repo root; a directory is refused.
# <test> runs under bash, so it is a `.sh` file: another runner's test is refused
# (revert the path by hand and record `Reverted-fix: <test>: n/a: <reason>`).
# stdout: {test, paths, red}   red: true when the test failed on the reverted tree
# stderr: the test's last 40 lines when it went red, the evidence it failed (or,
#   exit 2, when it was already red at HEAD); on a green run, one line saying the
#   test does not prove the fix
# exit: 0 the test went red on the reverted tree · 1 it stayed green, so it does
#       not prove the fix (the verdict, red false, is still on stdout) · 2 tooling,
#       a bad call, a non-`.sh` test, a test already red at HEAD, or a tree that
#       reached a host, none of which is a verdict
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: revert-red <test> <path>...'
ship_help "$usage" "$@"
ship_args "$usage" "arg arg" "$@"
test_path=$1
shift

# verdict <red> <path>...: the one JSON answer.
verdict() {
  local red=$1; shift
  jq -n --arg t "$test_path" --argjson r "$red" '{test: $t, paths: $ARGS.positional, red: $r}' --args "$@"
}

# `cd ""` succeeds without moving, so the toplevel is read and checked first.
root=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$root" ] || ship_tooling "not inside a git repository"
cd "$root" || ship_tooling "cannot enter $root"
tip=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
base=$(git merge-base HEAD "$tip" 2>/dev/null) || ship_tooling "no merge base between HEAD and $tip"

# bash reads any other runner's test as a syntax error, which is red whatever the
# fix does.
case $test_path in
  *.sh) ;;
  *) ship_tooling "revert-red runs shell tests (a .sh file); for another runner revert the path by hand and record \`Reverted-fix: <test>: n/a: <reason>\`" ;;
esac

# A test bash cannot open exits non-zero and would read as red; a mistyped path
# reverts nothing, so the test stays green and would read as vacuous.
[ "$(git cat-file -t "HEAD:$test_path" 2>/dev/null)" = blob ] \
  || ship_tooling "$test_path is not a file committed at HEAD"
for p in "$@"; do
  # The test among its own paths would be deleted or reverted, and bash failing to
  # open it reads as red.
  [ "$p" != "$test_path" ] || ship_tooling "$p is the test, not a path to revert"
  # A blob, since a directory clears `cat-file -e` and `rm -f` then reverts nothing.
  [ "$(git cat-file -t "$base:$p" 2>/dev/null)" = blob ] || [ "$(git cat-file -t "HEAD:$p" 2>/dev/null)" = blob ] \
    || ship_tooling "$p is a file at neither the merge base nor HEAD"
done

tmp=$(mktemp -d) || ship_tooling "cannot create a temp directory"
wt=$tmp/tree
log=$tmp/test.log
trap 'git -C "$root" worktree remove --force "$wt" 2>/dev/null; rm -rf "$tmp"' EXIT
git worktree add -q --detach "$wt" HEAD || ship_tooling "cannot create a worktree at HEAD"
mkdir "$tmp/stub" || ship_tooling "cannot create a temp directory"
hostlog=$tmp/hostlog
: > "$hostlog" || ship_tooling "cannot create the host log"
for cli in gh az; do
  # Only the CLI's name is interpolated here: the rest is the stub's own runtime,
  # which reads the log's path from the environment.
  printf '#!/bin/sh\nprintf "%%s %%s\\n" %s "$*" >> "$SHIP_REVERT_HOSTLOG"\nexit 127\n' "$cli" > "$tmp/stub/$cli" \
    && chmod +x "$tmp/stub/$cli" || ship_tooling "cannot write the $cli stub"
done

# run_test <label>: the test in the temp worktree, its output in $log, its exit
# status in $rc. A run that reached a host is exit 2 whatever the test answered.
run_test() {
  (cd "$wt" && PATH="$tmp/stub:$PATH" SHIP_REVERT_HOSTLOG="$hostlog" bash "$test_path") >"$log" 2>&1
  rc=$?
  if [ -s "$hostlog" ]; then
    ship_tooling "$(echo "revert-red: $test_path reached a host $1:"; head -n 20 "$hostlog" | sed 's/^/    /')"
  fi
}

# A test red at HEAD reads red with the fix reverted too, whatever the fix does.
run_test "at HEAD"
if [ "$rc" -ne 0 ]; then
  tail -n 40 "$log" >&2
  ship_tooling "$test_path is red at HEAD, so a red with the fix reverted proves nothing"
fi

for p in "$@"; do
  if git cat-file -e "$base:$p" 2>/dev/null; then
    git -C "$wt" checkout -q "$base" -- "$p" || ship_tooling "cannot restore $p from $base"
  else
    rm -f "$wt/$p"
  fi
done

run_test "with the fix reverted"

list=$(printf '%s, ' "$@")
list=${list%, }
if [ "$rc" -eq 0 ]; then
  verdict false "$@"
  echo "revert-red: $test_path stays green with $list reverted, so it does not prove the fix." >&2
  exit 1
fi
tail -n 40 "$log" >&2
verdict true "$@"
