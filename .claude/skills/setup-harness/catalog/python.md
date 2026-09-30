# Python

## Signals
Kind: stack
Manifest: pyproject.toml
Lockfile: uv.lock, poetry.lock, pdm.lock
Workspace: `[tool.uv.workspace]`
Extensions: .py .pyi
Shebangs: python python3
Runtime version: .python-version, pyproject `requires-python`, .tool-versions, mise.toml
Library: a `pyproject.toml` with both `[build-system]` and `[project]`

## lint

### ruff
Publisher: astral-sh
Tier: 2: https://github.com/astral-sh/ruff
Evidence: `[tool.ruff]` table, `ruff` dev dependency
Rung: edit
Run: `ruff check --fix --force-exclude {files}`
Hook: https://github.com/astral-sh/ruff-pre-commit
Pin: package pypi ruff
Route: `uv tool install ruff=={version}`; Blocked: Astral's curl installer, release binaries
Constraints: None.
Traps: an explicit file path bypasses `[tool.ruff] exclude` unless `--force-exclude` is passed. ruff reads a `pyproject.toml` only when it has a `[tool.ruff]` table; a member below the root without one is linted from the root and its first-party imports are sorted as third-party, so the run adds an empty table there.

## format

### ruff format
Publisher: astral-sh
Tier: 2: https://github.com/astral-sh/ruff
Evidence: `[tool.ruff.format]` table, `ruff` dev dependency
Rung: edit
Run: `ruff format {files}`
Hook: https://github.com/astral-sh/ruff-pre-commit
Pin: package pypi ruff
Route: `uv tool install ruff=={version}`; Blocked: Astral's curl installer, release binaries
Constraints: the lint role's `--fix` must run before this, on the vendor hook's own ordering.
Traps: None.

### black
Publisher: psf
Tier: 2: https://github.com/psf/black
Evidence: `[tool.black]` table, `black` dev dependency
Rung: edit
Run: `black {files}`
Hook: https://github.com/psf/black-pre-commit-mirror
Pin: package pypi black
Route: `uv tool install black=={version}`; Blocked: None.
Constraints: None.
Traps: None.

## typecheck

### mypy
Publisher: python
Tier: 2: https://github.com/python/mypy
Evidence: `[tool.mypy]` table, `mypy.ini`, `mypy` dev dependency
Rung: turn
Run: `mypy .`
Hook: local
Pin: package pypi mypy
Route: `uv tool install mypy=={version}`; Blocked: None.
Constraints: None.
Traps: the only pre-commit hook, `pre-commit/mirrors-mypy`, is a third-party mirror that runs in an isolated venv without project dependencies; this entry pins and runs mypy itself instead.

### pyright
Publisher: Microsoft
Tier: 2: https://github.com/microsoft/pyright
Evidence: `pyrightconfig.json`, `[tool.pyright]` table, `pyright` dev dependency
Rung: turn
Run: `pyright .`
Hook: local
Pin: package npm pyright
Route: `npm install -g pyright@{version}`; Blocked: the PyPI `pyright` wheel is a third-party wrapper, not the vendor's own registry
Constraints: needs Node on PATH (ships as an npm package).
Traps: the CLI costs 4 to 10 s per call (Node start, no cache); do not put it on the edit rung.

### ty
Publisher: astral-sh
Tier: 2: https://github.com/astral-sh/ty
Evidence: `[tool.ty]` table, `ty` dev dependency
Rung: turn
Run: `ty check .`
Hook: https://github.com/astral-sh/ty-pre-commit
Pin: package pypi ty
Route: `uv tool install ty=={version}`; Blocked: None.
Constraints: beta (0.0.x, classifier "4 - Beta").
Traps: the vendor hook may create or update `uv.lock`; pass `--isolated` to avoid that.

## test runner

### pytest
Publisher: pytest-dev
Tier: 2: https://github.com/pytest-dev/pytest
Evidence: `pytest` dev dependency, `[tool.pytest.ini_options]` table, `pytest.ini`, `tests/` directory
Rung: turn
Run: `pytest`
Hook: local
Pin: package pypi pytest
Route: `uv tool install pytest=={version}`; Blocked: None.
Constraints: None.
Traps: None.

## affected tests

### pytest-testmon
Publisher: tarpas
Tier: 2: https://testmon.org
Evidence: `.testmondata`, `pytest-testmon` dev dependency
Rung: turn
Run: `pytest --testmon`
Hook: local
Pin: package pypi pytest-testmon
Route: `uv tool install pytest --with pytest-testmon=={version}`; Blocked: None.
Constraints: needs `coverage<8` and a first full run to build `.testmondata` before it can select. The route installs it into a pytest tool environment because `uv tool install` refuses a package with no executable of its own; that pytest is the newest release testmon admits.
Traps: crashes with `KeyError: 'lf'` under `-p no:cacheprovider`. Where the stack's pytest `addopts` (`[tool.pytest.ini_options]`, `pytest.ini`, `setup.cfg` or `tox.ini`) carries `-m` or `-k`, testmon deselects nothing and every turn runs the whole suite: write the turn row's `<affected tests>` field as `pytest --testmon --testmon-forceselect`, behind any exec prefix (measured, pytest-testmon 2.2.0).

## language server

### pyright
Publisher: Microsoft
Tier: 2: https://github.com/microsoft/pyright
Evidence: `.claude/skills/harness-pyright-lsp/`, `pyright-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`
Rung: None.
Run: `npx --yes --package=pyright@{version} pyright-langserver --stdio`
Settings: `{"python": {"pythonPath": "{root}/.venv/bin/python"}}`
Hook: None.
Pin: package npm pyright
Route: None.
Constraints: vendored from the Anthropic `pyright-lsp` plugin per [reference/language-servers.md](../reference/language-servers.md); needs Node, and with no `npx` on `PATH` the tool is `Unavailable: pyright needs Node`. The Python registries carry no trusted pyright, so the exact version rides in the vendored launch command instead of a dev dependency. Without `Settings:` pyright finds no in-project `.venv` (its `python` is off `PATH`), so every third-party import is `reportMissingImports`; `Settings:` names the stack root's `.venv`, which uv and pdm create and a uv workspace's members share. A venv elsewhere (`UV_PROJECT_ENVIRONMENT`, Poetry's default out-of-project venv) does not match, and a missing `.venv` leaves imports unresolved as before (measured, pyright 1.1.414, Claude Code 2.1.283). One interpreter per server: a second Python root is not served ([reference/language-servers.md](../reference/language-servers.md) `## Vendoring` step 2). Rejected: a root `pyrightconfig.json` naming the venv, which writes a repo-owned file and changes the repo's own `pyright` typecheck, and per-`executionEnvironments` `venvPath`/`venv`, which pyright refuses (`unrecognized setting "venvPath"`).
Local-only: cloud sessions start no plugin language server.
Traps: the first launch of a version fetches pyright into the npx cache, which the first `LSP` call waits out (10 s cold, 3 s warm, measured).

## public API

### griffe
Publisher: mkdocstrings
Tier: 2: https://github.com/mkdocstrings/griffe
Evidence: `griffe` dev dependency
Rung: full
Run: `griffe check {package} -s src`
Hook: local
Pin: package pypi griffe
Route: `uv tool install griffe=={version}`; Blocked: None.
Constraints: compares the working tree's public API with the latest git tag's, so it needs a release tag in the history; `{package}` is the import package's name in either layout, not the distribution name, and `-s` the directory holding it: `Run:` gives a src layout's `src`, and a flat layout writes `-s .`.
Traps: with no tag in the history it exits 1 with a traceback rather than a finding; a clone that fetched no tags, as a cloud session's does, needs the cloud setup's `tags` row ([reference/cloud.md](../reference/cloud.md) step 3).
