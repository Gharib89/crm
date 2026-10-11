#!/usr/bin/env bash
# .claude/skills/harness-pyright-lsp/pyright-launch.sh: the vendored pyright
# plugin's launcher, written by setup-harness. Its `.lsp.json` launches it with
# the pinned version and the stack root (`.` at the repo root); re-run
# setup-harness to move them.
#
#   pyright-launch.sh <version> <stack root>
#
# pyright given no interpreter setting asks the first `python` on PATH, so the
# launch puts a venv's bin first: `<stack root>/.venv` under the checkout the
# server starts in (Claude Code starts it in the session's checkout, a git
# worktree included), else under the main checkout, which serves a worktree
# that has no venv of its own. With neither, PATH is left as it is. An
# interpreter path in `settings` cannot do this: pyright resolves it against
# the checkout the server starts in alone.
#
# exit: 2 usage
set -uo pipefail
[ $# -eq 2 ] || { echo "usage: pyright-launch.sh <version> <stack root>" >&2; exit 2; }
version=$1 stack=/$2 # the stack root as a path suffix, empty at the repo root
[ "$2" = . ] && stack=
venv=$PWD$stack/.venv/bin
if [ ! -x "$venv/python" ] && common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
  venv=${common%/*}$stack/.venv/bin
fi
[ -x "$venv/python" ] && PATH=$venv:$PATH
exec npx --yes --package="pyright@$version" pyright-langserver --stdio
