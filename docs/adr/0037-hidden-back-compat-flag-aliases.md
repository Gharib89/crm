---
status: accepted
---

# A renamed flag keeps a hidden alias, with its own dest, folded by presence

Several flags were renamed so one spelling means one thing across the CLI:
`query --orderby` and `view create --order` became `--order-by` (#711), and
`fieldsec assign --user`, `webresource create|update --display-name` and
`chart --user` (on every chart verb that takes `--user-owned`) became
`--user-id`, `--display` and `--user-owned` (#712). Scripts and agent prompts
still pass the old spellings, so each old name stays accepted.

## Decision

- **The old spelling is a hidden option, not a second name on the canonical
  option.** `--help`, `crm describe`, OS-shell completion and REPL completion
  show only the canonical flag, so new callers learn one spelling (REPL
  completion skips hidden options since #1376).
- **The alias has its own click dest.** With one shared dest, whichever option
  click processes last writes its default over the other's value.
- **A valued alias folds in by presence, not truthiness**
  (`x if x is not None else alias`), so an explicit empty canonical value still
  counts as given and keeps the meaning it had before the rename: a usage error
  for `view create --order-by ""`, no ordering for `query --order-by ""`. A
  boolean alias has no empty value and folds with `or`.
- **The canonical name avoids a spelling another command uses with a different
  type.** `chart --user` is a boolean while `fieldsec --user` took a GUID, which
  is why both moved to explicit names.

## Considered options

- **Drop the old spelling.** Rejected: it breaks existing scripts with no
  release-note path to warn them first.
- **List both names on one option** (`click.option("--order-by", "--order")`).
  Rejected: both names then show in `--help`, so the deprecated one keeps
  spreading.

## Amendments

- **2026-11-27: an explicit empty value is refused before the fold ([#1417](https://github.com/Gharib89/crm/issues/1417)).** The fold-by-presence bullet gave `query --order-by ""` the meaning "no ordering". Every optional text or path flag now rejects an explicit `""` as a usage error at parse time, on the canonical and the alias spelling alike, since an empty value that means "as if omitted" silently drops the flag. The fold still runs by presence for every non-empty value. Status stays **accepted**: only the empty-value case changed.
