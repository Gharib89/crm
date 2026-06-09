# Initialize

## First-run setup

A working setup is a single saved **profile** — the server URL, auth scheme,
identity, and secret. Create one with:

```bash
crm profile add
```

On a terminal, `add` with no flags runs an interactive wizard: it asks for the server
URL, infers the auth scheme from it (`*.dynamics.*` → OAuth, anything else → NTLM),
collects the identity fields (NTLM username/domain or OAuth tenant/client id) and the
secret, then saves the profile, stores the secret, runs a `WhoAmI` to confirm it
works, and activates it. That is the quickest path to a working profile.

You don't even have to run it first: the **first time you run any connection command
with no profile configured**, the CLI launches this wizard for you automatically (on
a terminal). Under `--json` or a non-interactive shell it skips the wizard and errors
cleanly, telling you to run `crm profile add`.

For scripting or CI, pass flags instead of answering prompts:

```bash
crm profile add \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod
```

See [How-to: profile](../how-to/profile.md) for switching, editing, and managing the
stored secret, and [Configure](configure.md) for the NTLM vs OAuth field reference.
