# Catalog

One file per stack or file kind. The catalog is closed at run time: an entry changes only by PR.

## Contents

- [Entry format](#entry-format)
- [Label vocabulary](#label-vocabulary)
- [Yielding to the repo](#yielding-to-the-repo)
- [Affected tests](#affected-tests)

## Entry format

The profile grammar: fixed headings, facts on `Label:` lines, `None.` where a label has nothing to say.

```
# <Stack or file kind>

## Signals
Kind: stack | file kind
Names: <file basenames> | None.        (file kind only)
Paths: <repo-relative globs> | None.   (file kind only)
Manifest: <file names>                 (stack only)
Lockfile: <file names>                 (stack only)
Workspace: <where a workspace is named> (stack only)
Extensions: <.ext ...> | None.
Shebangs: <interpreters> | None.
Runtime version: <files, in precedence order> (stack only)
Library: <what makes a root publishable> (stack only)

## <role>
### <tool>        (default first, then known alternatives)
Publisher: <registry identity the install check matches>
Tier: <n>: <source URL>
Evidence: <config files, [tool.x] tables, dependency names>
Rung: edit | turn | full | None.     (None.: language server only)
Files: <.ext ...>                      (only when the tool takes a subset of Extensions:)
Run: `<command>`
Hook: local | <vendor hook repo URL> | None.
Pin: <pin kind> <registry> [<repository URL>] <name>
Route: `<install command>`; Blocked: <routes that 403> | None.
Done test: `<passes only on the pinned install>`  (only when Route: carries {version}, a `docker pull` route excepted)
Constraints: <needs / breaks with> | None.
Unavailable: <reason>                  (only when it applies)
Local-only: <reason>                   (only when it applies)
Traps: <one line each> | None.
```

Roles, as `##` headings, from this set only: `lint`, `format`, `typecheck`, `test runner`, `affected tests`, `language server`, `browser`, `public API`. A file-kind entry carries `lint` and `format` only and never `Rung: turn`: a file kind has no project to typecheck or test. `browser` and `public API` are `Rung: full` only. A `language server` takes `Rung: None.`, `Hook: None.` and a `Local-only:` line: it answers Claude's `LSP` calls, not a check, and no cloud session starts it.

## Label vocabulary

- **Rung.** `edit` takes one file and must fit the 5 s edit budget: formatters and most linters, run through the pre-commit runner, fix mode on. `turn` is project-scoped: typecheckers and tests, run by `check.sh` per member. `full` runs only on `check.sh full`. `None.` is a language server's, and only a language server's.
- **Run.** One command, in backticks. `{files}` stands for the file list, `{member}` for the member's directory and `{package}` for the member's package name where a selector takes names; a `turn` command runs in the member's directory, unless its `Constraints:` name the stack root (Maven's `-amd`). `{version}` stands for the pin where the command carries it: a container image's tag, a language server's launch. `{sha256}` stands for the install check's download digest (jdtls). `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PROJECT_DIR}` are written as is: Claude Code expands them to the vendored plugin's directory (where the launcher an entry's `Constraints:` names is copied) and to the repo root. A `lint` or `format` command is the runner hook's `entry`, in fix mode where the tool has one. A tool run in a container image is a `language: docker_image` hook instead, its `Run:` that hook spelled out (hadolint). `Run: None.` is for an `Unavailable:` tool only. A command names the tool's own binary, or the launcher its `Constraints:` names (jdtls, pyright); where the tool is a dev dependency, the written hook entry or turn row prefixes the stack's exec command (`uv run`, `pnpm exec`, `npx --no-install`). A language server's command is the one its vendored `.lsp.json` launches, `{root}` standing for the stack root.
- **Hook.** `local` or the vendor hook repo (`None.` for a language server); [reference/runner.md](../reference/runner.md) `## Writing hooks` says which a run writes.
- **Files.** A tool taking only some of the entry's extensions names them (ktlint's `.kt .kts` beside google-java-format's `.java`); its hook is scoped to them by `types`.
- **Paths.** A file kind claimed by path scopes each of its hooks with `files:`, a regex of its `Paths:` globs (`^\.github/workflows/[^/]+\.ya?ml$`), never by `types`, which would hand every YAML file to a workflow linter. A glob's `*` stays within one directory.
- **Pin.** The kind names the install check's pin table: `package <npm|pypi|go|crates|nuget|maven|dockerhub> <name>` (maven `<group>:<artifact>` on Maven Central, or `<repository URL> <group>:<artifact>` on another Maven repository; dockerhub the image repository), `apt <name>`, `hook-repo <url>`, `download <url>`.
- **Route.** The install that passes in a cloud sandbox, in backticks, with `{version}` for the version the run picks; `Blocked:` names the routes a cloud sandbox refuses, so a run never proposes them. Route serves the cloud sandbox; a local install follows the install check's pin table.
- **Done test.** The cloud setup row's done test for a `Route:` carrying `{version}`, in backticks, passing only when the tool's own output names the pinned version: most read `[[ " $(<tool> --version 2>&1) " == *[!0-9.]{version}[!0-9.-]* ]]`, the version preceded by any character but a digit or `.` and followed by any but those or `-`, and one whose output names several versions anchors on its own name (pytest-testmon's `pytest-testmon-{version}`), so an older install, `1.2.30` or `1.2.3-rc.1` against a `1.2.3` pin, fails it and the step runs again. A tool printing its version without the pin's leading `v` strips it in the test (golangci-lint's `v={version}; ... ${v#v}`). No `|` (the row's field separator) and no `'` (`STEPS` is single-quoted). A `docker pull` route has none: [reference/cloud.md](../reference/cloud.md) step 5 pins its image by digest.

Each stack's commands, by its lockfile:

| Lockfile | Exec | Exact dev add | Frozen install | Present test |
|---|---|---|---|---|
| `uv.lock` | `uv run --frozen` | `uv add --dev <t>==<v>` | `uv sync --frozen` | `test -d .venv` |
| `poetry.lock` | `poetry run` | `poetry add --group dev <t>==<v>` | `poetry sync` | `poetry env info -p` |
| `pdm.lock` | `pdm run` | `pdm add -dG dev <t>==<v>` | `pdm sync` | `test -d .venv` |
| `pnpm-lock.yaml` | `pnpm exec` | `pnpm add -D -E <t>@<v>` | `pnpm install --frozen-lockfile` | `test -d node_modules` |
| `package-lock.json` | `npx --no-install` | `npm install -D -E <t>@<v>` | `npm ci` | `test -d node_modules` |
| `yarn.lock` | `yarn run` | `yarn add -D -E <t>@<v>` | `yarn install --immutable` (Yarn 1: `--frozen-lockfile`) | `test -e node_modules -o -e .pnp.cjs` |
| `bun.lock` | `bun run` | `bun add -d --exact <t>@<v>` | `bun install --frozen-lockfile` | `test -d node_modules` |
| `go.sum` | None. | None. | `go mod download` | None. |
| `Cargo.lock` | None. | None. | `cargo fetch --locked` | None. |
| `packages.lock.json` | None. | None. | `dotnet restore --locked-mode` | None. |
| `gradle.lockfile` | None. | None. | `gradle dependencies` | None. |

A present test passes once the frozen install has filled the root; Poetry's environment sits in its cache rather than the root, and `poetry env info -p` exits 1 while the project has none, at the cost of starting Poetry on each check. A `None.` present test is a stack that restores into a cache every checkout shares.

A `None.` exec or dev add means the stack's tools are toolchain components or `Route:` installs, run by their own binary. A root with no lockfile (every Maven root, most Gradle and .NET roots) is `unlocked` and restores from its manifest (`mvn -q dependency:go-offline`, `gradle dependencies`, `dotnet restore`). An `unlocked` root can gain a lockfile from its exact dev add: [reference/detection.md](../reference/detection.md).

## Yielding to the repo

- Evidence of a tool the entry lists (default or alternative) keeps that tool; only its gaps (wiring, pin, rung) are filled.
- Evidence of a tool the entry does not list keeps it, wired only through the repo's own invocation (a `package.json` script, a make or just target), else reported `unwired: <tool> (<evidence path>)`.
- A default applies only to a role with no evidence, written once at the stack root and inherited by every member without evidence of its own. A member's own evidence wins for that member. A `typecheck` or `test runner` default also needs its target (tsc a `tsconfig.json`, a test runner one test file); without it the field stays empty and the role is reported under Not acted on, since an empty suite exits nonzero and would start the turn rung red.
- Two tools in one role: the one the runner config or CI calls is active, the other "present, not wired"; asked only when both or neither are called. Never both wired, because two formatters on the edit rung fight. Two exceptions are both wired: tools with disjoint `Files:` (each the default for its own files), and a tool whose `Constraints:` name it a second default (zizmor beside actionlint), because they check different things.
- One tool several detected entries list (Prettier in js-ts, Markdown and GitHub Actions) is one hook, never one per entry. `types_or` and `files` both filter a hook, so it routes by `types_or` alone where every entry's files carry a type (`markdown`, `yaml`), else by one `files` regex joining each entry's patterns. It runs the binary a stack pins where one does (the `pnpm exec prettier` hook), the entry's global route only where none does: two copies at two versions disagree on output as two formatters do.

## Affected tests

The member is the selection unit. A runner that selects natively (the Go test cache, Gradle's up-to-date checks) gets the member's test command. Vitest `related` and Jest `--findRelatedTests` are used whenever that runner is present. pytest-testmon and nextest `rdeps()` are offered as gap proposals through the install check, and may be declined. A runner with name filters only (bats, node:test, Maven, .NET), or a declined selector, runs the affected members' whole suites, plus their reverse dependents where the build names them (Maven's `-amd`, .NET project references). No file-name heuristics.
