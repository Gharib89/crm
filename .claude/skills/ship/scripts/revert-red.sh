#!/usr/bin/env bash
# revert-red: prove a test fails for the reason it exists, by running it on a
# tree with the fix taken out.
#
# A test written to prove a fix proves nothing until it has been red without the
# fix, and a vacuous assertion reads exactly like a sound one in a diff. This
# does the revert in a temp worktree at HEAD, so the working tree is never
# touched: the test runs once on the unreverted tree, then each <path> is
# restored from the merge base with origin/HEAD, or deleted where the fix added
# it, and the test runs again. With `--fix <commit>` only that commit's own change
# to each path comes out of HEAD's copy, so a review fix is judged alone: not
# against every commit the branch holds, nor with a later commit's edit to the
# file taken out too. A test that is not
# green at HEAD proves nothing when it goes red later, so that first run is the
# precondition. It reads committed state, so commit the test and the fix first.
#
# The test runs behind a `gh` and an `az` stub this mechanic writes itself, so it
# needs nothing from the repo it runs in: a revert can take out what swaps in a
# test's Host fake, and the reverted tree would then call the real host with the
# developer's own credentials. Each stub records its call and exits 127, the
# shell's "command not found". A run that reaches a host, at HEAD or reverted, is
# exit 2, since the red it shows is the stub's 127.
#
#   revert-red <test> <path>... [--fix <commit>] [-- <command>...]
#
# <test> and each <path> are files relative to the repo root; a directory is refused.
# With no <command>, <test> runs under bash, so it is a `.sh` file and another
# runner's test is refused. With one, <command> runs as argv, never through a
# shell string, at the temp worktree's top in place of `bash <test>`, and must
# name <test> as one of its words, or a node ID under it (`<test>::<name>`),
# ignoring a leading `./` on either. That checks the command's words, not what
# it runs: it refuses the command that names no word of <test>, which could go
# red for another test while the line credits this one.
# A red whose log names a file the fix added as missing, a `No such file`, a
# `Cannot find module`, a `No module named` or a like missing-file phrase,
# proves nothing about the fix: any test that reads the file goes red without
# it, and the check cannot tell that line from the test's other failures. Such a
# red is exit 1, red false. The route out: commit the new file with the lines
# the test reads stubbed, then commit their real body and prove that second
# commit with `--fix <commit>`; or revert a path the fix changed and the test
# reads instead.
# stdout: {test, paths, red, command[, fix]}   red: true when the test failed on
#   the reverted tree and its log names no file the fix added as missing;
#   command: the argv that ran, `["bash", <test>]` by default; fix: the commit
#   named, where one was given
# stderr: the test's last 40 lines when it went red, the evidence it failed (or,
#   exit 2, when it was already red at HEAD); on a green run, one line saying the
#   test does not prove the fix
# exit: 0 the test went red on the reverted tree · 1 it stayed green, or its red
#       names a file the fix added as missing, so it does not prove the fix (the
#       verdict, red false, is still on stdout) · 2 tooling,
#       a bad call, a non-`.sh` test with no command, a command not naming the
#       test, a test already red at HEAD, a `--fix` path a later commit added
#       or whose change does not reverse at HEAD, or a tree that reached a
#       host, none of
#       which is a verdict
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: revert-red <test> <path>... [--fix <commit>] [-- <command>...]'
ship_help "$usage" "$@"
ship_args "$usage" "arg arg" "$@"
test_path=$1
shift
paths=() fix=""
while [ $# -gt 0 ] && [ "$1" != -- ]; do
  if [ "$1" = --fix ]; then
    ship_flag_value "$usage" "${2:-}"
    fix=$2; shift 2
  else
    paths+=("$1"); shift
  fi
done
given=false command=(bash "$test_path")
if [ $# -gt 0 ]; then
  shift
  # An empty first word runs nothing.
  [ -n "${1:-}" ] || ship_tooling "$usage"
  given=true command=("$@")
fi
set -- "${paths[@]}"

# verdict <red> <path>...: the one JSON answer. jq reads options after `--args`,
# so `--` keeps a word like `--test` or `-c` a word.
verdict() {
  local red=$1; shift
  jq -n --arg t "$test_path" --argjson r "$red" --arg f "$fix" --argjson c "$(jq -nc '$ARGS.positional' --args -- "${command[@]}")" \
    '{test: $t, paths: $ARGS.positional, red: $r, command: $c} + (if $f == "" then {} else {fix: $f} end)' --args -- "$@"
}

# `cd ""` succeeds without moving, so the toplevel is read and checked first.
root=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$root" ] || ship_tooling "not inside a git repository"
cd "$root" || ship_tooling "cannot enter $root"
# The fix's parent is the tree before it, whichever commits came after: only that
# commit's own change is taken out, so a repo with no origin/HEAD still answers.
# The commit is named in full in the verdict.
if [ -n "$fix" ]; then
  fix=$(git rev-parse --verify -q "$fix^{commit}") || ship_tooling "--fix $fix is no commit here"
  git merge-base --is-ancestor "$fix" HEAD || ship_tooling "--fix $fix is not on HEAD's history"
  base=$(git rev-parse --verify -q "$fix^") || ship_tooling "--fix $fix has no parent to restore from"
  from="the parent of --fix"
else
  tip=$(ship_base_ref) || ship_tooling "cannot resolve origin/HEAD"
  base=$(git merge-base HEAD "$tip" 2>/dev/null) || ship_tooling "no merge base between HEAD and $tip"
  from="the merge base"
fi

# bash reads any other runner's test as a syntax error, which is red whatever the
# fix does.
if ! $given; then
  case $test_path in
    *.sh) ;;
    *) ship_tooling "revert-red runs a .sh test under bash; for another runner pass its command after \`--\` (\`revert-red <test> <path>... -- <command>...\`); record \`Reverted-fix: <test>: n/a: <reason>\` only where no command can run it" ;;
  esac
fi
# undot <path>: the path with every leading `./` taken off.
undot() { local p=$1; while [ "${p#./}" != "$p" ]; do p=${p#./}; done; printf '%s' "$p"; }
want=$(undot "$test_path")
named=false
for w in "${command[@]}"; do
  w=$(undot "$w")
  case $w in "$want" | "$want"::*) named=true; break ;; esac
done
$named || ship_tooling "the command after -- must name $test_path as one of its words (or a node ID under it, $test_path::<name>), so its red is that test's"

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
    || ship_tooling "$p is a file at neither $from nor HEAD"
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
  (cd "$wt" && PATH="$tmp/stub:$PATH" SHIP_REVERT_HOSTLOG="$hostlog" "${command[@]}") >"$log" 2>&1
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

added=()
for p in "$@"; do
  if ! git cat-file -e "$base:$p" 2>/dev/null; then
    # A path a later commit added is not this fix's to take out.
    if [ -n "$fix" ] && ! git cat-file -e "$fix:$p" 2>/dev/null; then
      ship_tooling "$p is not a file --fix ${fix:0:12} added"
    fi
    rm -f "$wt/$p"
    added+=("$p")
  elif [ -n "$fix" ]; then
    # Only the fix's own hunks come out: restoring the whole file from its parent
    # would take every later commit's change with it, and a red from one of those
    # would be credited to this fix.
    git diff --binary -U0 "$base" "$fix" -- "$p" > "$tmp/fix.patch" || ship_tooling "cannot diff $p across --fix"
    if [ -s "$tmp/fix.patch" ]; then
      git -C "$wt" apply -R --unidiff-zero "$tmp/fix.patch" || ship_tooling "the fix's change to $p does not reverse cleanly at HEAD, since a later commit edits the same lines: revert a hunk inside it by hand"
    fi
  else
    git -C "$wt" checkout -q "$base" -- "$p" || ship_tooling "cannot restore $p from $base"
  fi
done

run_test "with the fix reverted"

list=$(printf '%s, ' "$@")
list=${list%, }
if [ "$rc" -eq 0 ]; then
  verdict false "$@"
  if [ -n "$fix" ]; then
    echo "revert-red: $test_path stays green with ${fix:0:12}'s change to $list reverted, so it does not prove that fix." >&2
  else
    echo "revert-red: $test_path stays green with $list reverted, so it does not prove the fix." >&2
  fi
  exit 1
fi
tail -n 40 "$log" >&2
# A red naming a file the fix added as missing is the absence of the file, not
# the fix's behaviour: the line carries a missing-file phrase and the file's name,
# or the module name a runner reports in place of the file's.
missing_re='No such file or directory|not found|Cannot find module|No module named|ModuleNotFoundError|ImportError|cannot open|ENOENT|does not exist'
missing_lines=$(grep -E "$missing_re" "$log")
for p in ${added[@]+"${added[@]}"}; do
  b=${p##*/} stem=${p##*/}; stem=${stem%.*}
  names=(-e "$b")
  # A dotfile has no stem, and the empty one would match any quote.
  [ -z "$stem" ] || names+=(-e "'$stem'" -e ".$stem'" -e "/$stem'")
  if grep -qF "${names[@]}" <<<"$missing_lines"; then
    verdict false "$@"
    echo "revert-red: $test_path goes red with $list reverted, but its error names $p as missing, which says nothing about the fix's behaviour: commit $p with the lines the test reads stubbed, then commit their real body and prove that second commit with --fix <commit>; or revert a path the fix changed and the test reads." >&2
    exit 1
  fi
done
verdict true "$@"
