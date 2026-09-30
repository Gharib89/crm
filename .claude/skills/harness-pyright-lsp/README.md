# harness-pyright-lsp

Vendored from anthropics/claude-plugins-official@2a8ad9f74633d10e3d9bb0660a03bfc6e50584b1, entry `pyright-lsp`, by `/setup-harness`.

Vendored rather than enabled from the marketplace because a marketplace source cannot be pinned to a commit, so every machine would get whatever `main` is. The launch pins Microsoft's npm pyright at the repo's one version (the `setup.py` `[dev]` comment names every site to bump together); `.claude/settings.json` disables the upstream plugin so only this server registers.
