# How-to: examples

`crm examples [GROUP]` is a curated gallery of runnable example invocations — a
teaching surface for humans, distinct from [`crm describe`](describe.md) (the
exhaustive machine-readable catalogue). It needs **no D365 connection**.

The gallery lives in code, and every example is checked against the live command
tree by an offline test: if a curated example ever names a command or flag that
no longer exists, CI fails. So the examples never drift out of sync with the CLI.

## Interactive (a terminal)

With no argument on a terminal, pick a group, then an example. The chosen command
is **printed** for you to copy, edit, and run — nothing runs automatically:

```bash
crm examples
```

Pass a group name to skip straight to its examples:

```bash
crm examples solution
```

Curated groups today: `entity`, `query`, `solution`, `profile`, `metadata`.

## Scripts and agents

Under `--json`, or whenever stdout is not a terminal, `examples` prints the whole
listing instead of prompting — so it never blocks:

```bash
crm --json examples            # every group
crm --json examples query      # one group
```

```json
{
  "ok": true,
  "data": [
    {
      "group": "query",
      "command": "crm query odata accounts --select name,revenue --top 5",
      "description": "First N rows with selected columns."
    }
  ]
}
```

Each row is `{group, command, description}` (ADR 0008 envelope). Without `--json`,
the same listing renders as a `group | command | description` table.

See the [CLI reference](../reference/cli.md) for the full flag list.
