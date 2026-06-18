# Quickstart

From nothing to a working query in about five minutes.

## 1. Install

=== "Windows (PowerShell)"

    ```powershell
    irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
    ```

=== "Linux"

    ```bash
    curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
    ```

The prebuilt binary bundles Python — nothing else to install. See
[Install](install.md) for `uv` and from-source options, and for managed machines
where the binary is blocked.

## 2. Open a new shell

So your `PATH` picks up `crm`, then confirm:

```bash
crm --version
```

## 3. Create a profile

```bash
crm profile add
```

On a terminal this runs a wizard: enter your server URL, and the CLI infers the
auth scheme (any host containing `.dynamics.` → OAuth, anything else → NTLM), prompts for what
that scheme needs, stores the secret, runs a `WhoAmI` to verify, and activates the
profile. See [Add a profile](add-profile.md) for the non-interactive form.

## 4. Confirm it works

```bash
crm connection whoami
crm query account --top 5
```

If `whoami` prints your user and organization, you're connected.

---

**Next:**

- [Install the skill](skill.md) and [use `/crm` with a coding agent](agent.md).
- Browse the [how-to guides](../how-to/connection.md) for task recipes.
- Hit a snag? See [Troubleshooting](troubleshooting.md).
