# How-to: async

Common `crm async` recipes. See the [CLI reference](../reference/cli.md) for every flag.

## List pending async operations

```bash
crm --json async list --state ready --top 50
```
`--state` accepts `ready | suspended | locked | completed | <int>`; add `--all` to follow `@odata.nextLink` (capped by `--max-pages`).

## List operations for a specific message

```bash
crm --json async list --message ImportSolution
```
`--message` filters by `messagename`; `--owner <guid>` narrows to one user.

## Inspect one operation

```bash
crm --json async get <asyncoperationid>
```
Use an `asyncoperationid` from `async list`; the response includes `state`, `status`, and `message` for error detail.

## Cancel a pending or suspended operation

```bash
crm --json async cancel <asyncoperationid> --yes
```
`--yes` skips the confirmation prompt; cancellation is gated by the destructive-op hook.
