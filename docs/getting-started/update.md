# Update

Keep the `crm` CLI current.

## Check for a newer release

```bash
crm self-update --check
```

Reports your running version, the latest published version, and whether an update is
available — without changing anything. Works on every install type.

## Upgrade in place

```bash
crm self-update
```

For a binary installed via the install script, this downloads the platform archive,
verifies it against the published `SHA256SUMS`, and swaps the bundle in place. A
checksum mismatch or download failure leaves your install untouched.

For `pip` / `uv` / source installs, `self-update` doesn't touch the binary — it
points you at `pip install -U crm` (or re-running `uv tool install`).

A non-`--check` update also re-syncs any agent skills you installed (see
[Install the skill](skill.md)), so the shipped skill never lags the CLI.

## The passive update notice

On an interactive terminal, `crm` checks at most once every 24 hours for a newer
release and prints a one-line notice on stderr after a command finishes. It is
silent under `--json`, when stderr isn't a terminal, when `CI` is set, and when
`CRM_NO_UPDATE_CHECK` is set. Opt out permanently:

```bash
export CRM_NO_UPDATE_CHECK=1
```

See [how-to: self-update](../how-to/self-update.md) for the per-destination
skill-sync detail and the full flag reference.
