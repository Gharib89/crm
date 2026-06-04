# How-to: describe

`crm describe` emits a machine-readable catalogue of the whole command tree —
every command, its options and arguments, `Choice` enums, defaults, and
envvars. It is built for agents and scripts that need to discover the CLI
surface without parsing `--help` text.

It walks the live Click objects, so it needs **no D365 connection** and pulls in
no extra dependencies. Pair it with `--json` for the structured envelope.

See the [CLI reference](../reference/cli.md) for the full flag list.

## Whole tree

```bash
crm --json describe
```

```json
{
  "ok": true,
  "data": {
    "root_options": [
      {
        "name": "json_mode",
        "opts": ["--json"],
        "type": "boolean",
        "required": false,
        "is_flag": true,
        "multiple": false,
        "choices": null,
        "default": false,
        "envvar": null
      }
    ],
    "commands": [
      {
        "name": "add-attribute",
        "path": "metadata add-attribute",
        "help": "Add a column to an existing entity.",
        "is_group": false,
        "args": [
          {"name": "entity", "type": "text", "required": true,
           "multiple": false, "choices": null, "default": null}
        ],
        "params": [
          {"name": "kind", "opts": ["--kind"], "type": "choice",
           "required": true, "is_flag": false, "multiple": false,
           "choices": ["string", "memo", "integer", "bigint", "decimal",
                       "double", "money", "boolean", "datetime", "picklist",
                       "multiselect", "lookup", "image", "file"],
           "default": null, "envvar": null}
        ]
      }
    ]
  }
}
```

- `root_options` — the sticky global flags (`--json`, `--dry-run`, `--profile`,
  `--auth-scheme`, `--log-level`, `--stage-only`, `--session`, …) that apply to
  every invocation rather than to one subcommand.
- `commands` — a flat list; groups and their subcommands both appear, each with
  its full `path` (e.g. `metadata add-attribute`). `is_group` marks group nodes.
- `args` are positional; `params` are options. `Choice` enums are surfaced
  verbatim, in declaration order.
- The interactive `repl` leaf is **excluded** — it is not meant to be driven
  programmatically.

## One subtree

Pass a top-level command name to scope the walk to that subtree. This imports
only that one command module — a lazy win over the full walk:

```bash
crm --json describe metadata
```

## Human-readable

Without `--json`, `describe` prints a `command | kind | help` table:

```bash
crm describe
```
