# How-to: profile

Set up and switch connection targets. A **profile** holds the server URL, auth
scheme, identity fields (NTLM username/domain or OAuth tenant/client id), and the
optional `publisher_prefix` used by scaffolding/schema-name derivation. The
secret (NTLM password or OAuth client secret) is stored alongside it. This is
the only place credentials come from — there is no `.env` and no credential
environment variables. See the [CLI reference](../reference/cli.md) for every flag.

Profiles do **not** carry a target solution — every customization-write command
(`metadata create-*`, `plugin register-*`, `webresource`, `form`, `view`,
`chart`, `dashboard`, `sitemap`, `apply`, …) requires its own explicit
`--solution <name>` (or an `apply` spec's `solution:` block); there is no
profile default and no opt-out (#636).

## Set up the first profile

```bash
crm profile add
```

On a terminal, `add` with no flags runs an **interactive wizard**: it asks for the
server URL, infers the auth scheme from it (`*.dynamics.*` → OAuth, anything else →
NTLM), prompts for the identity fields and the secret, then runs a `WhoAmI`
against the server **before** saving anything. On success it saves the profile,
stores the secret, and activates it — zero-to-working in one command, no extra
prompts. The secret prompt echoes `*` per keystroke — feedback that the typing
registered, not a security control (it reveals the secret's length). Off a TTY
(piped input, `--json`) nothing is prompted at all: pass the secret with
`--password` / `--client-secret`.

If that live test fails, `add` doesn't just save blindly: a URL/identity that's
structurally implausible (no hostname, missing tenant/client id, no secret) is
rejected outright — nothing is saved. A structurally plausible profile whose test
still failed (VPN down, server unreachable, app user not yet provisioned) gets a
`Save the profile anyway?` prompt on a TTY; declining leaves nothing saved.
Non-interactively (`--json` / no TTY / CI), pass `--save-on-test-failure` to save
it anyway — otherwise the command errors without saving. When it does save this
way, the command prints a warning that the connection was never verified (nothing
is flagged on the stored profile itself).

Leading/trailing whitespace in the URL — a stray space copied from chat or a
clipboard into `--url` or the wizard — is stripped automatically (as is a trailing
slash), so it no longer slips into the request path and breaks every call silently.
The same trimming self-heals on load, so a profile already saved with a stray space
is corrected the next time it is used. `profile edit --url` trims the same way.

The first time you run any connection command with no profile configured, the CLI
launches this wizard for you automatically (TTY only). Under `--json` or a
non-interactive shell it skips the wizard and errors cleanly, telling you to run
`crm profile add`.

For scripting or CI, pass flags instead of answering prompts:

```bash
# On-prem (NTLM) — auth inferred as ntlm from the URL
crm profile add \
    --url https://crm.contoso.local/Contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod

# Dataverse online (OAuth) — auth inferred as oauth from *.dynamics.com
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --tenant-id <aad-tenant-id> --client-id <app-registration-id> \
    --client-secret "$CLIENT_SECRET" \
    --name online
```

`--client-secret` is an alias for `--password` (the two are mutually exclusive) so
OAuth scripting reads naturally; either works. `--name` defaults to the URL host
label. Override the inferred scheme with `--auth-scheme` when the URL doesn't match
the heuristic — the interactive wizard offers the same choice as an inline arrow-key
picker (↑/↓ then Enter, Esc to cancel) with the inferred scheme preselected. Omit `--api-version` to
**auto-negotiate** — on-prem is capped at v9.1 (v9.2 returns HTTP 501), so the CLI
steps down automatically. Attach a schema-name prefix so scaffolding and
column-name derivation don't need a per-command flag:

```bash
crm profile add --url ... --publisher-prefix cwx --name crmworx
```

The interactive wizard also prompts for the prefix (blank skips — no default
prefix is set). Either path validates it — 2-8 alphanumeric characters,
starting with a letter, not starting `mscrm` — before saving: an invalid
`--publisher-prefix` fails at parse time (exit 2), an invalid wizard entry just
re-prompts.

## Switch the active profile

```bash
crm profile use            # interactive picker (no argument)
crm profile use prod       # switch to a named profile
crm profile use --none     # clear the active profile
```

The active profile is remembered across commands. Pass `--profile <name>` on any
command to override it for a single run.

## List saved profiles

```bash
crm profile list
crm --json profile list
```

Marks the active profile and shows each one's target (on-prem / cloud), URL,
where its secret lives (`cred=keyring`, `cred=plaintext`, or `cred=none`), and a
`read-only` marker on any read-only profile (JSON rows carry a `read_only` field).

## Edit a profile's fields

```bash
crm profile edit                # interactive picker (no argument)
crm profile edit prod --publisher-prefix cwx
crm profile edit online --url https://contoso.crm.dynamics.com --client-id <new-id>
crm profile edit lab --no-verify-ssl    # skip SSL verification (--verify-ssl re-enables)
```

`edit` changes any non-secret field — URL, identity fields, api-version,
publisher prefix, SSL-certificate verification (`--verify-ssl/--no-verify-ssl`,
the same toggle `profile add` sets, flippable in place either way), and the
read-only guardrail (see [Mark a profile
read-only](#mark-a-profile-read-only)). To change the secret, use `set-password`
(below). An invalid
`--publisher-prefix` is rejected the same way as on `add` (exit 2). Omitting
`NAME` on a TTY shows the same arrow-key picker as `profile use`; under
`--json` or with no TTY a missing `NAME` is still a usage error (exit 2).

## Mark a profile read-only

A **read-only** profile blocks accidental writes: the backend refuses every org
**mutation** (any non-GET Web API call, minus the solution/translation *export*
actions, which extract rather than mutate) with a loud operational failure
(`ok:false`, exit 1); reads run normally.

```bash
crm profile add --url ... --name prod-ro --read-only   # set at creation
crm profile edit prod-ro --read-only                    # or tighten an existing one
```

The flip is **asymmetric**: setting it on is unrestricted — the `--read-only`
flag works everywhere (including `--json` / CI), and the interactive `add` wizard
offers a read-only y/N step (default N). **Clearing** it requires an interactive
terminal:

```bash
crm profile edit prod-ro --no-read-only   # prompts y/N to confirm on a TTY
```

Under `--json` or with no TTY, `--no-read-only` errors cleanly (exit 1) and tells
you to run it from your shell — so a coding agent with no TTY can't flip the
guardrail off via the CLI.

This is a **guardrail, not a security boundary.** The CLI runs as your OS user,
which can hand-edit the profile file, clone it into a fresh `CRM_HOME`, or (WSL
plaintext fallback) read the secret and mint a new writable profile. It stops
accidents, not a determined same-user process — for real enforcement, use a
dedicated app registration / user with a **read-only security role** server-side.
A read-only refusal is never a `--dry-run` preview: `--dry-run` is checked first,
so a read-only + dry-run mutation still previews (`ok:true`), while a real
mutation is refused (`ok:false`, exit 1).

## Rename a profile

```bash
crm profile rename                  # interactive picker for OLD, then prompts for NEW
crm profile rename old-name         # picker skipped; prompts for NEW
crm profile rename old-name new-name
```

Omitting `OLD` on a TTY shows the same arrow-key picker as `profile use`; a
missing `NEW` is then prompted for. Under `--json` or with no TTY, either
missing name is still a usage error (exit 2).

All validation runs before anything on disk changes: `new-name` must be a
valid profile name, `old-name` must exist, `new-name` must not already exist
(rename refuses to clobber it), and the two names must differ. `old-name` and
`new-name` being the same is a usage error (exit 2); a missing `old-name`, an
already-taken `new-name`, or an invalid `new-name` are clean errors (exit 1).

On success the profile file (including an inline plaintext secret, if any) and
the per-profile metadata cache move to `new-name`, and `old-name` is deleted.
A keyring-stored secret is moved on a best-effort basis — if the move fails,
the rename still completes and the response carries a warning with a
`crm profile set-password new-name` recovery hint instead of rolling back. If
`old-name` was the active profile, the active pointer is updated too; a
**concurrent session** that is still pointing at `old-name` breaks after the
rename, exactly like `crm profile rm`.

`rename` is the safe way to relabel a profile in place — unlike deleting it and
re-running `add`, it carries the stored secret and cached metadata over.

## Delete a profile

```bash
crm profile rm                      # interactive picker (no argument), then prompts for confirmation
crm profile rm old-profile          # prompts for confirmation
crm profile rm old-profile --yes    # skip the prompt
```

Removes the profile and its stored secret. If it was the active profile, the active
pointer is cleared. Omitting `NAME` on a TTY shows the same arrow-key picker as
`profile use`; under `--json` or with no TTY a missing `NAME` is still a usage
error (exit 2).

## Manage the stored secret

Storing the secret is **automatic** when you run `crm profile add` — the wizard and
the flag-driven form both save it. Use `set-password` to store or replace it for a
profile that already exists, and `delete-password` to remove it:

```bash
crm profile set-password --profile prod                       # prompts for the secret on a TTY
crm profile set-password --profile prod --password "$SECRET"
crm profile set-password --profile online --client-secret "$CLIENT_SECRET"  # OAuth alias
crm profile delete-password --profile prod
```

`set-password` works the same for an NTLM password and an OAuth client secret.
Its TTY prompt echoes `*` per keystroke, same as the `add` wizard's secret
prompt above.

### Where the secret is stored

By default the secret goes into the **OS keyring** — macOS Keychain, Windows
Credential Manager, or Linux SecretService. Keyring support is a core dependency, so
it works out of the box on every install with no extra to set up.

On hosts with no keyring backend (typical WSL or headless CI), the secret falls back
automatically to a `0600` plaintext entry inside the profile file on disk — no flag
needed. To force plaintext even where a keyring exists, pass
`--store-password-plaintext` to `add` or `set-password`. On POSIX the file is created
`0600`; on Windows file permissions are not enforced and a warning is emitted.

### Secret resolution order

When a command needs the secret it checks, in order:

1. `--password` on the command line (a per-run override)
2. The stored secret — plaintext entry first, then the OS keyring
3. An interactive TTY prompt (skipped in `--json` / non-interactive contexts),
   also echoing `*` per keystroke

There is no environment-variable step — `.env`, `D365_*`, and `CRM_*` credential
variables are not read. `CRM_HOME` is the only env var involved in
credential/connection resolution (it relocates the state directory, default
`~/.crm/`). Other `CRM_*` vars tune unrelated runtime behavior (logging,
retries, stage-only) but never supply connection config.

## Confirm it works

`crm profile add` already runs a `WhoAmI` for you. To re-check an active profile
later, use the connection diagnostics:

```bash
crm connection whoami       # issue WhoAmI() against the server
crm connection doctor       # ordered DNS/TCP → TLS → version → auth probe
```

See [How-to: connection](connection.md) for the full diagnostics set.
