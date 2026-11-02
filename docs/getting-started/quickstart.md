# Quickstart

From nothing to a working query in about five minutes.

## 1. Install

=== "Windows (PowerShell)"

    Install [uv](https://docs.astral.sh/uv/):

    ```powershell
    winget install --id=astral-sh.uv -e
    ```

    Open a new shell and confirm uv is on your `PATH`:

    ```powershell
    uv --version
    ```

    Install `crm` with a pinned interpreter:

    ```powershell
    uv tool install --python 3.14 git+https://github.com/Gharib89/crm
    ```

    The `--python 3.14` pin matters: uv may otherwise pick Python 3.15, where
    PyYAML has no prebuilt Windows wheel and building one needs the MSVC Build
    Tools. Run `uv tool update-shell` once if `crm` isn't found in a new shell.
    The [install script](install.md#install-script-no-python-required) is an
    alternative that needs no Python at all.

=== "Linux"

    ```bash
    curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
    ```

The Linux install script's prebuilt binary bundles Python — nothing else to
install. See [Install](install.md) for every option, and for managed machines
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
auth scheme (any host containing `.dynamics.` → OAuth, anything else → NTLM),
prompts for what that scheme needs, runs a `WhoAmI` to verify **before** saving
anything, then stores the secret and activates the profile. See
[Add a profile](add-profile.md) for the non-interactive form and what happens if
the live test fails.

## 4. Confirm it works

```bash
crm connection whoami
crm query odata accounts --top 5
```

If `whoami` prints your user and organization, you're connected.

---

**Next:**

- [Install the skill](skill.md) and [use `/crm` with a coding agent](agent.md).
- Browse the [how-to guides](../how-to/connection.md) for task recipes.
- Hit a snag? See [Troubleshooting](troubleshooting.md).
