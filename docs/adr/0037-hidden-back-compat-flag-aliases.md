---
status: accepted
---

# A renamed flag keeps a hidden alias, with its own dest, folded by presence

Several flags were renamed so one spelling means one thing across the CLI:
`query --orderby` and `view create --order` became `--order-by` (#711), and
`fieldsec assign --user`, `webresource create|update --display-name` and
`chart list|get --user` became `--user-id`, `--display` and `--user-owned`
(#712). Scripts and agent prompts
still pass the old spellings, so each old name stays accepted.

## Decision

- **The old spelling is a hidden option, not a second name on the canonical
  option.** `--help`, `crm describe` and completion show only the canonical
  flag, so new callers learn one spelling.
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
