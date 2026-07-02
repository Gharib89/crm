# Tab completion (optional)

Tab-completion for `crm` in bash, zsh, fish, or PowerShell.

!!! note "The REPL already tab-completes — nothing to install"
    This page is about completing `crm ...` at your **OS shell** prompt. The
    interactive `crm repl` has its own built-in Tab-completion (commands, flags,
    profile names, entity names) that needs no setup — see
    [how-to: completion](../how-to/completion.md#repl-tab-completion-built-in-nothing-to-install).

```bash
crm completion install --shell zsh
```

This writes the completion script under `~/.crm/completion/` and prints **one line**
to add to your shell startup file. It never edits the file for you — copy the printed
line yourself, then restart your shell:

- **zsh** → `~/.zshrc`: `source ~/.crm/completion/crm.zsh`
- **bash** → `~/.bashrc`: `source ~/.crm/completion/crm.bash`
- **fish** → `~/.config/fish/config.fish`: `source ~/.crm/completion/crm.fish`
- **PowerShell** → `$PROFILE`: `. ~/.crm/completion/crm.ps1` (requires
  `--shell powershell` — it can't be autodetected)

`--shell` defaults to autodetecting `$SHELL`. A later `crm self-update` regenerates
the cached script automatically. See
[how-to: completion](../how-to/completion.md) for `--path`, `show`, and the
"why a cached file, not `eval`" note.
