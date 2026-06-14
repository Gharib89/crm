# How-to: completion

Tab-completion for `crm` in bash, zsh, fish, or PowerShell. `crm completion` is a
thin wrapper over Click's built-in completion — it makes it discoverable, caches the
generated script to a file, and records a marker so [`self-update`](self-update.md)
keeps it current. See the [CLI reference](../reference/cli.md) for every flag.

## Install completion

```bash
crm completion install --shell zsh
```

Writes the completion script to `${CRM_HOME:-~/.crm}/completion/crm.zsh` and prints
the single line to add to your shell startup file. It **never edits the file for
you** — copy the printed line yourself. `--shell` defaults to autodetecting
`$SHELL`; pass it explicitly if autodetection can't map `$SHELL` to bash/zsh/fish.
PowerShell sets no `$SHELL`, so `--shell powershell` is **required** (it can't be
autodetected).

Re-running is idempotent: it rewrites the same script and marker, no duplication.

### Per-shell setup

After `crm completion install`, add the printed line to the matching startup file
and restart your shell (or re-source it):

- **zsh** — add to `~/.zshrc`: `source ~/.crm/completion/crm.zsh`
- **bash** — add to `~/.bashrc`: `source ~/.crm/completion/crm.bash`
- **fish** — add to `~/.config/fish/config.fish`: `source ~/.crm/completion/crm.fish`
- **PowerShell** — add to your `$PROFILE` (Windows PowerShell 5.1 or PowerShell 7+):
  `. ~/.crm/completion/crm.ps1` (PowerShell dot-sources; install it with
  `crm completion install --shell powershell`)

## Print the script without installing

```bash
crm completion show --shell bash
```

Prints the completion source script to stdout and writes nothing — useful to pipe
into a system-wide completion directory yourself, or to inspect the script.

## Install to a custom path

```bash
crm completion install --shell zsh --path ~/.zfunc/_crm
```

`--path` overrides the default `${CRM_HOME}/completion/crm.<shell>` location. The
marker records this path so `self-update` refreshes the script there.

## Keeping completion current across upgrades

If you installed completion through `crm completion install`, a later
[`crm self-update`](self-update.md) regenerates the cached script at the recorded
path using the upgraded binary. A completion-refresh failure never fails the
update — it's surfaced as a status line instead. If you set completion up manually
(without `crm completion install`), `self-update` leaves it untouched.

!!! note "Why a cached file, not `eval`"
    `install` always caches the script to a file and tells you to `source` it.
    Avoid the inline `eval "$(_CRM_COMPLETE=zsh_source crm)"` form in your rc — it
    spawns Python on every shell start, slowing down each new terminal.
