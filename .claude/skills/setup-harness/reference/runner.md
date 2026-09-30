# The pre-commit runner

The runner is the commit rung and the one place the linter set is defined: `check.sh edit` and `check.sh full` call it rather than naming linters.

## Contents

- [Which runner is active](#which-runner-is-active)
- [A repo with no runner: prek](#a-repo-with-no-runner-prek)
- [Writing hooks](#writing-hooks)
- [A repo with a runner](#a-repo-with-a-runner)

## Which runner is active

The active runner is the one git invokes: `git config core.hooksPath`, else the shim in `$(git rev-parse --git-path hooks)/pre-commit`. Read the shim to name its runner (pre-commit and prek write a `pre-commit`/`prek` call, lefthook a `lefthook` call, husky sets `core.hooksPath` to `.husky/_`). Runner configs git does not invoke (`.pre-commit-config.yaml`, `lefthook.yml`, `.lintstagedrc*`, `package.json` `lint-staged`, `.husky/pre-commit`) are reported "present, not wired" and never deleted. One config with no shim is a gap filled by that runner's own install command; no shim and more than one config is asked.

## A repo with no runner: prek

Install prek, pinned per [install-check.md](install-check.md): an exact dev dependency when a root stack has a manifest (`uv add --dev prek==<v>` for Python, `pnpm add -D -E @j178/prek@<v>` for JS/TS), else `uv tool install prek==<v>`. The same command goes into the cloud setup later. prek's standalone installer is out: it downloads a GitHub release asset, which a cloud sandbox refuses.

Write `.pre-commit-config.yaml` with pre-commit-compatible keys only, so pre-commit can still run it and the choice stays reversible; prek-only keys a repo already has are kept. Its top-level `exclude:` is one regex with an alternative for `.claude/skills/` and one for each `Excluded:` prefix, regex-escaped (`exclude: ^(\.claude/skills/|tests/fixtures/)`); a repo's own `exclude:` gains the missing alternatives instead, and a prefix answered read, or whose line is gone, leaves it. Then `prek install --allow-missing-config` writes the git shim, which the flag marks with `--skip-on-missing-config` (the mark gap-report.md reads), and `prek run --all-files` proves the config before any hook is timed. The shim lands in the common `hooks` directory every worktree of the repo shares, and without the flag it fails a commit in any checkout whose tree has no config yet: the default branch and every branch cut before the harness merges.

## Writing hooks

A tool the repo's lockfile pins, which is every tool this skill installs as a dev dependency, runs as a `local` hook with `language: system` calling that binary through the stack's exec command, so the commit rung never runs a different version from the edit and turn rungs:

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff
        entry: uv run --frozen ruff check --fix
        language: system
        types_or: [python, pyi]
      - id: prettier
        name: prettier
        entry: pnpm exec prettier --write --ignore-unknown
        language: system
        types_or: [ts, tsx, javascript, jsx, json, markdown, yaml]
```

The `entry` is the catalog tool's `Run:` without `{files}` (the runner appends the files), prefixed with the exec command. Route each hook by `types`, `types_or` or `files`, the way `check.sh edit` expects: it hands the runner any file and the runner picks the hooks. A member-only tool is scoped with `files: ^<prefix>` and its `entry` reaches the member's pinned binary from the repo root, where the runner passes root-relative paths: `uv run --frozen --project api ruff check --fix`, `web/node_modules/.bin/eslint --fix` (not `pnpm --dir web exec`, which changes directory under those paths). A container tool (hadolint) is a `repo: local` hook with `language: docker_image` and `entry: <image>:<tag>@<digest> <binary>`, in place of `language: system`. A file kind claimed by `Paths:` routes by `files:`, never `types`.

A remote hook repo is used only for a tool the repo does not pin (the catalog's `Hook:` names the vendor repo), `rev` frozen to a full SHA through the install check.

## A repo with a runner

Keep it and extend it in its own format: `.pre-commit-config.yaml` for pre-commit or prek, `lefthook.yml` for lefthook, the lint-staged config for husky with lint-staged. Add only the missing checks, and each `Excluded:` prefix to its exclude in that format (lefthook's `exclude:` globs, a lint-staged pattern that skips the prefix); offer no migration, not even pre-commit to prek. Its `EDIT_RUN` in `check.sh` is its own file-list command (`pre-commit run --files {files}`, `lefthook run pre-commit --file {files}`). lint-staged takes no file list, so under it `check.sh edit` calls the linters directly, `EXCLUDED` keeping them off each `Excluded:` prefix ([check-ladder.md](check-ladder.md)), and the runner config stays the commit rung: `EDIT_RUN` chains each linter's `Run:` with `&&`, keeping only tools that skip foreign files (`prettier --ignore-unknown`, `eslint --no-warn-ignored`); any other is a question.

lefthook's `ai:` key is never written; this skill writes its own `.claude/hooks/`. A repo already using it keeps it, and an absolute binary path it wrote into a committed `.claude/settings.json` is reported with a proposed relative command.
