# JS/TS

## Signals
Kind: stack
Manifest: package.json
Lockfile: pnpm-lock.yaml, package-lock.json, yarn.lock, bun.lock
Workspace: pnpm-workspace.yaml, package.json `workspaces`
Extensions: .ts .tsx .js .jsx .mjs .cjs .mts .cts
Shebangs: node
Runtime version: .nvmrc, .node-version, package.json `engines`, .tool-versions, mise.toml
Library: a `package.json` with a `name` and no `"private": true`

## lint

### ESLint
Publisher: OpenJS
Tier: 2: https://github.com/eslint/eslint
Evidence: `eslint.config.*`, `.eslintrc*`, `eslint` dev dependency
Rung: edit
Run: `eslint --fix {files}`
Hook: https://github.com/eslint/eslint
Pin: package npm eslint
Route: `npm install -g eslint@{version}`; Blocked: None.
Constraints: ESLint 9 and later lint nothing without a flat config; a default writes `eslint.config.mjs` extending `@eslint/js` and, for TypeScript, `typescript-eslint`, both exact dev dependencies through the install check.
Traps: the vendor hook runs in an isolated env; flat-config plugins must be listed under `additional_dependencies` or they are not resolvable.

### Biome
Publisher: biomejs
Tier: 2: https://github.com/biomejs/biome
Evidence: `biome.json`, `biome.jsonc`, `@biomejs/biome` dev dependency
Rung: edit
Run: `biome lint --write {files}`
Hook: https://github.com/biomejs/pre-commit
Pin: package npm @biomejs/biome
Route: `npm install -g @biomejs/biome@{version}`; Blocked: None.
Constraints: None.
Traps: None.

### oxlint
Publisher: oxc-project
Tier: 2: https://github.com/oxc-project/oxc
Evidence: `.oxlintrc.json`, `oxlint` dev dependency
Rung: edit
Run: `oxlint --fix --deny-warnings {files}`
Hook: https://github.com/oxc-project/mirrors-oxlint
Pin: package npm oxlint
Route: `npm install -g oxlint@{version}`; Blocked: None.
Constraints: None.
Traps: every finding is a warning by default and exits 0; `--deny-warnings` is required for it to gate anything.

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
Traps: `prettier/pre-commit` and `pre-commit/mirrors-prettier` are both archived; do not wire either.

### Biome format
Publisher: biomejs
Tier: 2: https://github.com/biomejs/biome
Evidence: `biome.json`, `biome.jsonc`, `@biomejs/biome` dev dependency
Rung: edit
Run: `biome format --write {files}`
Hook: https://github.com/biomejs/pre-commit
Pin: package npm @biomejs/biome
Route: `npm install -g @biomejs/biome@{version}`; Blocked: None.
Constraints: None.
Traps: None.

## typecheck

### tsc
Publisher: Microsoft
Tier: 2: https://github.com/microsoft/TypeScript
Evidence: `tsconfig.json`, `typescript` dev dependency
Rung: turn
Run: `tsc --noEmit`
Hook: local
Pin: package npm typescript
Route: `npm install -g typescript@{version}`; Blocked: None.
Constraints: typescript-eslint requires `typescript >=4.8.4 <6.1.0`.
Traps: `tsc --noEmit <file>` fails with `TS5112` once a tsconfig is present; run project-scoped only, with no file arguments.

## test runner

### Vitest
Publisher: vitest-dev
Tier: 2: https://github.com/vitest-dev/vitest
Evidence: `vitest.config.*`, `vite.config.*` with a `test` key, `vitest` dev dependency
Rung: turn
Run: `vitest run`
Hook: local
Pin: package npm vitest
Route: `npm install -g vitest@{version}`; Blocked: None.
Constraints: None.
Traps: None.

### Jest
Publisher: jestjs
Tier: 2: https://github.com/jestjs/jest
Evidence: `jest.config.*`, `jest` key in `package.json`, `jest` dev dependency
Rung: turn
Run: `jest`
Hook: local
Pin: package npm jest
Route: `npm install -g jest@{version}`; Blocked: None.
Constraints: None.
Traps: `--watchman` defaults true; pass `--no-watchman` where the binary is absent.

### node:test
Publisher: nodejs
Tier: 2: https://nodejs.org/api/test.html
Evidence: `node:test` imports in test files, no dedicated config file
Rung: turn
Run: `node --test`
Hook: local
Pin: None.
Route: None.
Constraints: ships with the stack's own pinned Node runtime; no separate package to install.
Traps: no changed-files or related-tests mode; only whole-suite or watch mode.

## affected tests

### Vitest related
Publisher: vitest-dev
Tier: 2: https://github.com/vitest-dev/vitest
Evidence: `vitest.config.*`, `vite.config.*` with a `test` key, `vitest` dev dependency
Rung: turn
Run: `vitest related --run {files}`
Hook: local
Pin: package npm vitest
Route: `npm install -g vitest@{version}`; Blocked: None.
Constraints: follows static imports only; a dynamic `import()` is not tracked.
Traps: None.

### Jest findRelatedTests
Publisher: jestjs
Tier: 2: https://github.com/jestjs/jest
Evidence: `jest.config.*`, `jest` key in `package.json`, `jest` dev dependency
Rung: turn
Run: `jest --findRelatedTests {files}`
Hook: local
Pin: package npm jest
Route: `npm install -g jest@{version}`; Blocked: None.
Constraints: None.
Traps: None.

## language server

### typescript-language-server
Publisher: typescript-language-server
Tier: 1: https://github.com/anthropics/claude-plugins-official (the `typescript-lsp` entry names it)
Evidence: `.claude/skills/harness-typescript-lsp/`, `typescript-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`, `typescript-language-server` dev dependency
Rung: None.
Run: `${CLAUDE_PROJECT_DIR}/{root}/node_modules/.bin/typescript-language-server --stdio`
Hook: None.
Pin: package npm typescript-language-server
Route: None.
Constraints: vendored from the Anthropic `typescript-lsp` plugin per [reference/language-servers.md](../reference/language-servers.md), pinned once at the stack root beside the repo's own `typescript`, whose tsserver it drives. When the lockfile resolves that `typescript` to 7 or later, the tool is `Unavailable: typescript-lsp needs TS ≤ 6`; a stack with no `typescript` makes it `Unavailable: typescript-lsp needs the stack's typescript`, since the server refuses to initialize without one (measured, 6.0.0). Either way nothing of it is written.
Local-only: cloud sessions start no plugin language server.
Traps: None.

## browser

### Playwright Test
Publisher: Microsoft
Tier: 2: https://github.com/microsoft/playwright
Evidence: `playwright.config.*`, `@playwright/test` dev dependency
Rung: full
Run: `playwright test`
Hook: local
Pin: package npm @playwright/test
Route: `npm install --no-save @playwright/test@{version} && npx --no-install playwright install --with-deps chromium`; Blocked: `cdn.playwright.dev` for the browser download, on the Default network
Constraints: the repo's own suite only, wired where it is evidence and never a default; its browsers come from the cloud setup's browser step ([reference/surfaces.md](../reference/surfaces.md) `## Web UI`), and `PLAYWRIGHT_BROWSERS_PATH` is never overridden. A repo's copy comes from its frozen install, so `Route:` is the entry trial's alone and never a cloud setup step.
Traps: `--only-changed` selects by the import graph and the vendor calls it a heuristic, so it is never offered. A `webServer` in the config builds and starts the app, so the row's time is mostly the build's. A `webServer.command` run through `pnpm exec` starts in a new session (pnpm 11.27.1), so Playwright's teardown kill misses the server and the row hangs after its tests pass; where `webServer.command` starts with `pnpm exec`, the report asks the human, as a numbered question, to call `./node_modules/.bin/<bin>` instead, and the skill never edits the config itself; a no drops the `e2e-<member>` row, recorded `Declined: e2e-<member>: webServer.command runs through pnpm exec: <reason>`.

## public API

### publint
Publisher: bluwy
Tier: 2: https://github.com/publint/publint
Evidence: `publint` dev dependency
Rung: full
Run: `publint`
Hook: local
Pin: package npm publint
Route: `npm install -g publint@{version}`; Blocked: None.
Constraints: runs `npm pack` itself and checks the tarball's `main`, `exports` and `files` against the files it holds; offline.
Traps: a `"private": true` package is still checked.

### attw
Publisher: andrewbranch
Tier: 2: https://github.com/arethetypeswrong/arethetypeswrong.github.io
Evidence: `@arethetypeswrong/cli` dev dependency
Rung: full
Run: `attw --pack .`
Hook: local
Pin: package npm @arethetypeswrong/cli
Route: `npm install -g @arethetypeswrong/cli@{version}`; Blocked: None.
Constraints: checks how each module resolution mode resolves the packed tarball's types; offline.
Traps: a package with no types passes, since there is nothing to resolve. A package whose `exports` has no `require` or `default` condition leading to a CommonJS file is ESM-only, and fails `CJSResolvesToESM` under the default profile: its row passes `--profile esm-only`.
