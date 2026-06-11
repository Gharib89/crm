# How-to: self-update

Keep the `crm` CLI current. See the [CLI reference](../reference/cli.md) for every flag.

## Check for a newer release

```bash
crm self-update --check
```
Reports the running version, the latest published version, and whether an update
is available — without modifying anything. Works on every install type. Under
`--json` it emits the standard envelope (`data.current`, `data.latest`,
`data.update_available`); if the release server is unreachable it returns a clean
error envelope rather than hanging or crashing.

## Upgrade a frozen binary in place

```bash
crm self-update
```
For a binary installed via the install script, this downloads the platform
archive, verifies it against the published `SHA256SUMS` (the same integrity check
the install script uses), and swaps the bundle in place — the `crm` launcher on
your PATH keeps working. A checksum mismatch or download failure leaves the
existing install untouched and exits non-zero.

For `pip` / `uv` / source installs, `self-update` changes nothing and points you
at `pip install -U crm` (or re-running `uv tool install`).

## The passive update notice

On an interactive terminal, `crm` checks at most once every 24 hours whether a
newer release exists and prints a one-line notice on stderr after a command
finishes. The probe runs in the background and never delays a command.

It is silent — and skips the network entirely — in any of these cases:

- `--json` output mode (machine-readable output is never polluted),
- stderr is not a terminal (pipes, redirects, agents),
- the `CI` environment variable is set,
- the `CRM_NO_UPDATE_CHECK` environment variable is set.

Set `CRM_NO_UPDATE_CHECK=1` to opt out permanently:

```bash
export CRM_NO_UPDATE_CHECK=1
```

The last check result is cached under `${CRM_HOME:-~/.crm}/update-check.json`.
