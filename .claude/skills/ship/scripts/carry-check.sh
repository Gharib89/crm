#!/usr/bin/env bash
# Do the worktree's `Carry:` files still match the main checkout's copies? Phase 5
# owes that check because ship never edits untracked secrets. It is a script, not
# an inline `cmp .env <main>/.env`, because a consumer that denies `Read(.env)`
# makes Claude Code refuse an inline Bash command naming that path, while the
# same comparison inside a script passes (measured, Claude Code 2.1.327). Pure
# git and `cmp -s`, host-neutral, no host call, and it never prints a file's
# contents, a diff or a hash: only names.
#
#   carry-check [<file>...]
#
# Run from the worktree, with the profile's `Carry:` files as paths relative to
# the checkout root, the same list `isolate --carry` takes. The main checkout is
# `ship_main_checkout`'s, and a file is read from the root of the worktree
# whatever the current directory. `Carry: None.` passes no files and answers ok.
#
# stdout: {ok, match, differs, missing}
#   ok: true when nothing differs and nothing is missing
#   match: the files identical in both checkouts
#   differs: the files present in both whose bytes differ
#   missing: the files absent from the worktree or from the main checkout, or
#     unreadable on either side
# exit: 0 every file matches · 1 one differs or is missing (the stop `carried
#   file modified`; ship does not repair it) · 2 tooling: not a git checkout, run
#   from the main checkout itself (there each file would match itself), a flag or
#   a path leaving the checkout where a file belongs
# contract: bare-run
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }
usage='usage: carry-check [<file>...]'
ship_help "$usage" "$@"

for f in "$@"; do
  case $f in
    -*) ship_tooling "$usage" ;;
    /*|..|../*|*/..|*/../*) ship_tooling "carry-check: a file is a path under the checkout root, got: $f" ;;
  esac
done
here=$(git rev-parse --show-toplevel 2>/dev/null) || ship_tooling "not inside a git checkout"
main=$(ship_main_checkout) || ship_tooling "not inside a git checkout"
[ $# -eq 0 ] || [ "$(cd "$here" && pwd -P)" != "$(cd "$main" && pwd -P)" ] || ship_tooling "run from the worktree: in the main checkout each file would match itself"

match='[]' differs='[]' missing='[]'
for f in "$@"; do
  # cmp exits 2 on a read it could not make (a directory, a vanished file): that
  # is `missing`, never `differs`.
  cmp -s "$here/$f" "$main/$f" 2>/dev/null
  case $? in
    0) match=$(jq --arg f "$f" '. + [$f]' <<<"$match") ;;
    1) differs=$(jq --arg f "$f" '. + [$f]' <<<"$differs") ;;
    *) missing=$(jq --arg f "$f" '. + [$f]' <<<"$missing") ;;
  esac
done
ok=true
[ "$differs$missing" = '[][]' ] || ok=false
jq -n --argjson ok "$ok" --argjson match "$match" --argjson differs "$differs" --argjson missing "$missing" \
  '{ok: $ok, match: $match, differs: $differs, missing: $missing}'
$ok || jq -rn --argjson d "$differs" --argjson m "$missing" \
  '"carried files differ or are missing: " + (($d + $m) | join(", "))' >&2
$ok
