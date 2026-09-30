# GitHub Actions

## Signals
Kind: file kind
Names: None.
Paths: .github/workflows/*.yml .github/workflows/*.yaml
Extensions: None.
Shebangs: None.

## lint

### actionlint
Publisher: rhysd
Tier: 2: https://github.com/rhysd/actionlint
Evidence: `.github/actionlint.yaml`, `.github/actionlint.yml`
Rung: edit
Run: `actionlint -pyflakes= {files}`
Hook: local
Pin: package go github.com/rhysd/actionlint/cmd/actionlint
Route: `GOBIN="$HOME/.local/bin" go install github.com/rhysd/actionlint/cmd/actionlint@{version}`; Blocked: release binaries, `download-actionlint.bash`
Constraints: needs Go 1.25 or later, else the module proxy fetches a toolchain (which the cloud sandbox passes). GitHub publishes no workflow linter CLI of its own.
Traps: it runs `shellcheck` and `pyflakes` on `run:` scripts when either is on `PATH`, so results change with `PATH`: `Run:` turns pyflakes off, and the hook adds `-shellcheck=` unless the Shell entry's ShellCheck is wired (apt pins it). Its runner labels are compiled in: a new GitHub label or a self-hosted one needs a newer actionlint or `self-hosted-runner.labels` in `.github/actionlint.yaml`.

### zizmor
Publisher: zizmorcore
Tier: 2: https://github.com/zizmorcore/zizmor
Evidence: `zizmor.yml`, `.github/zizmor.yml`
Rung: edit
Run: `zizmor --offline --fix {files}`
Hook: local
Pin: package pypi zizmor
Route: `uv tool install zizmor=={version}`; Blocked: release binaries, the `ghcr.io/zizmorcore/zizmor` image
Constraints: a second default beside actionlint, not an alternative: actionlint checks correctness and zizmor security, so both are wired.
Traps: any `GH_TOKEN` or `GITHUB_TOKEN` switches zizmor to online mode, and a cloud session sets one the GitHub API refuses (401), so zizmor passes locally and fails only in a cloud session (measured). `--offline` overrides the token and turns off every online action, a superset of `--no-online-audits`. The vendor hook lives in `zizmorcore/zizmor-pre-commit`, not the tool's own repo.

## format

### Prettier
Publisher: Prettier
Tier: 2: https://github.com/prettier/prettier
Evidence: `.prettierrc*`, `prettier.config.*`, `prettier` dev dependency
Rung: edit
Run: `prettier --write {files}`
Hook: local
Pin: package npm prettier
Route: `npm install -g prettier@{version}`; Blocked: None.
Constraints: None.
Traps: yamllint is not a default: its stock config fights workflow files (35 findings on one real workflow, mostly `line-length`, `truthy` on `on:` and `document-start`). The image ships an unpinned global `prettier`, so the cloud setup's done test matches the picked version rather than `command -v`: `[ "$(prettier --version 2>/dev/null)" = <v> ]`, with no `|`, which separates a cloud setup row's fields.
