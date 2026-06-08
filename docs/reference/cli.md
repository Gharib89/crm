# CLI reference

This page is generated from the `crm` command tree.

::: mkdocs-click
    :module: crm.cli
    :command: cli
    :prog_name: crm
    :depth: 1

## `crm solution validate`

Statically validate a solution zip before import.

| Argument / Option | Description |
| --- | --- |
| `ZIP_PATH` | Path to the solution `.zip` to validate. |
| `--against-org` | Also run online checks against the connected org (GUID collisions, web-resource & option-set existence). Requires a connection/profile. |

Offline by default — no connection required. Exits non-zero when any
error-severity problem is found.
