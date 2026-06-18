# Add a profile

A working setup is a single saved **profile** — the server URL, auth scheme,
identity, and secret. Create one with:

```bash
crm profile add
```

On a terminal, `add` with no flags runs an interactive wizard: it asks for the
server URL, infers the auth scheme from it (`*.dynamics.*` → OAuth, anything else →
NTLM), collects the identity fields (NTLM username/domain or OAuth tenant/client id)
and the secret, then saves the profile, stores the secret, runs a `WhoAmI` to
confirm, and activates it.

You don't even have to run it first: the **first time you run any connection command
with no profile configured**, the CLI launches this wizard for you automatically (on
a terminal). Under `--json` or a non-interactive shell it skips the wizard and errors
cleanly, telling you to run `crm profile add`.

## Non-interactive (scripting / CI)

Pass flags instead of answering prompts.

**On-prem (NTLM):**

```bash
crm profile add \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod
```

**Online / Dataverse (OAuth):**

```bash
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --tenant-id <aad-tenant-id> --client-id <app-registration-id> \
    --password "$CLIENT_SECRET" \
    --name online
```

See [Configure & switch](configure.md) for the full NTLM vs OAuth field reference and
day-to-day profile management, and [how-to: profile](../how-to/profile.md) for every
flag.
