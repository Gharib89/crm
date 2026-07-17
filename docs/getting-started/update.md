# Update

Keep the `crm` CLI current.

## Check for a newer release

```bash
crm self-update --check
```

Reports your running version, the latest published version, and whether an update is
available — without changing anything. Works on every install type.

## Upgrade

```bash
crm self-update
```

`self-update` is the single, install-method-aware upgrade entry point. It detects
how `crm` was installed and does the right thing:

- **Install-script binary (frozen)** — downloads the platform archive, verifies it
  against the published `SHA256SUMS`, and swaps the bundle in place. A checksum
  mismatch or download failure leaves your install untouched.
- **uv tool / pipx** — force-reinstalls from the latest release tag
  (`uv tool install --force git+https://github.com/Gharib89/crm@vX.Y.Z`, or the
  pipx equivalent). Because `crm` is a git source, a forced reinstall is what
  reliably fetches the new version — a plain `uv tool upgrade` can no-op on the
  pinned commit. On a terminal `self-update` prints the exact command and asks
  before running it; non-interactively (`--json` or no TTY) it runs only with
  `--yes`, otherwise it prints the command and does nothing.
- **editable / `pip install git+…` / unknown** — prints the correct git-based
  upgrade command (`git pull && pip install -e .`, or
  `pip install -U git+…@vX.Y.Z`) and never mutates the environment, since we don't
  own or safely know it.

All non-frozen methods converge on the **latest release tag**, the same version
the frozen path reads from the release server, so "latest" means one thing
everywhere.

After a successful upgrade — or on any run that doesn't upgrade (already current,
or a printed-guidance / declined install) — `self-update` re-syncs any agent
skills you installed (see [Install the skill](skill.md)), so the shipped skill
never lags the CLI. After a uv/pipx reinstall the refresh runs via the freshly
installed `crm`, so the skill matches the version you just installed. (A failed
upgrade leaves the skills untouched.)

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
