# The install check

Every third-party unit passes this before anything of it is written. The check is the skill's, not the human's taste: a unit it refuses is never written, and there is no override inside the skill.

## Contents

- [Scope](#scope)
- [Trust tiers](#trust-tiers)
- [Reading a unit](#reading-a-unit)
- [Pins](#pins)
- [Run-time fetches](#run-time-fetches)
- [Presenting](#presenting)
- [Refusing](#refusing)

## Scope

Third-party units only: packages, binaries, pre-commit hook repos, plugins, skills and MCP servers. Hook scripts and files the skill writes itself are diffs on the confirm step, not units. Dependencies restored from the repo's own lockfile (`uv sync --frozen`, `pnpm install --frozen-lockfile`) are exempt: the repo already chose them.

## Trust tiers

1. **Anthropic-authored** entries and docs. Being listed in the official marketplace is curation, not a tier.
2. **The tool's own vendor.** A partner's marketplace entry is admitted only when the partner makes the tool it wraps.
3. **Skill sources pinned in the consumer repo's own `skills-lock.json`.** A repo with none has no tier 3.

Anything else is "found, not trusted, not installed". No trust by stars, downloads or recency. A packaged tool's tier is its publisher's, or that of the tier-1 glue that names it; an exact pin is still required. The catalog's `Publisher:` line is the identity the resolved package must carry: check it against the registry (npm `maintainers` and `repository`, PyPI `project_urls` and the uploader's repository, the Go module path, crates.io `owners` and `repository`, NuGet `owners` and `projectUrl`, the Maven `groupId`, whose domain part carries the `Publisher:` (`com.pinterest.ktlint` for `pinterest`), the Docker Hub namespace), and refuse a mismatch.

## Reading a unit

- **Glue**, whatever makes Claude Code or git run the unit, is read in full and shown: a hook repo's `.pre-commit-hooks.yaml`, a plugin's directory with its `marketplace.json` entry, `hooks/hooks.json`, `.mcp.json`, `.lsp.json`, `bin/`, skill files.
- **A packaged tool** from tier 1 or 2 is not source-read: trust rests on tier, exact pin and published provenance (checksum, GitHub attestation, npm or PyPI provenance, a Maven repository's `.sha256` or `.asc`, or its `.sha1` where it publishes only sha1 and md5, an image digest), checked where it exists, `none published` where it does not.

## Pins

Each pin lives in its tool's own place; the skill adds no lock file of its own.

| Kind | Pin |
|---|---|
| Package | exact version as a dev dependency in the repo's manifest and lockfile when the stack has one (`uv add --dev ruff==<v>`, `pnpm add -D -E prettier@<v>`); else the exact version in the install command (`uv tool install prek==<v>`) |
| Pre-commit hook repo | `rev` frozen to a full SHA, reachable from the upstream tag it names (`prek autoupdate --freeze`, then confirm the SHA is on that tag) |
| apt package | name only, distro-pinned; the installed version is recorded in the report |
| Direct download | exact-version URL plus a sha256 check |
| Container image | `<repository>:<tag>@<digest>`, the tag by version choice and the digest the one `docker image inspect --format '{{index .RepoDigests 0}}'` reports after the pull |
| MCP server | exact version in `.mcp.json` args, never `@latest` |
| Skill | `skills-lock.json` `ref` (SHA) plus `computedHash` |
| Vendored plugin config | the repo's own commit; its README carries `Vendored from <repo>@<full sha>` and its `plugin.json` version `<entry version>+<full sha>` ([language-servers.md](language-servers.md)); a language server its entry's `Constraints:` do not pin in the stack's own files carries its exact version in the vendored launch command, and a language server whose launcher downloads the build (jdtls) the build's sha256 beside it, the download digest below |

**Version choice:** the newest non-prerelease whose registry publish time is at least 7 days old, installed as that exact version on every route; the package manager resolves peer caps. Run `scripts/pick-version.sh` from this skill's directory with the `Pin:` line's words after `package` (`<npm|pypi|go|crates|nuget|maven|dockerhub> <name>`, or `maven <repository> <group>:<artifact>`); it prints the version, exits 1 when none qualifies (a refusal), and exits 2 when the registry did not answer or does not know the name. Run it once more (a Maven Central 429 burst can outlast the script's six tries); a second 2 stops that row, reported with the script's stderr, not refused. apt is exempt.

**Download digest:** where a `Run:` carries `{sha256}`, download the picked version's file, to a temporary directory outside the repo, from the URL its entry's `Constraints:` spells (`<repository>` the `Pin:` line's), in Explore beside the version pick, check it against the repository's published `.sha1` (a mismatch is refused), and take its sha256 (`sha256sum`, or `shasum -a 256`). The row's Pin shows the version and that sha256, and a re-pin computes it again. The launcher checks the build against it, not against the sha1.

## Run-time fetches

Follow every launch command one level. `npx pkg@latest`, `uvx pkg`, `uvx git+...`, `pnpm dlx`, `curl ... | sh` and a hook repo's `language:` that downloads at run time are rewritten to a pinned form (`npx --no-install` over a pinned dev dependency, `uvx pkg==<v>`), or the unit is refused. A launcher this skill writes that fetches one exact version and checks its pinned sha256 before running it (jdtls) is already the pinned form. A binary taken from `PATH` must be one the harness itself installs and pins, or a runtime launcher a catalog `Constraints:` names, which makes the tool `Unavailable:` where it is missing (pyright's `npx`).

## Presenting

One table, a row per unit, then each unit's glue in full:

| # | Unit | Tier | Pin | Runs | Reaches | Cloud | Provenance |
|---|---|---|---|---|---|---|---|

- **Tier**: the number and the reason (`2: ruff is Astral's; PyPI publisher astral-sh`).
- **Runs**: each command and its trigger: install, on edit, on `Stop`, on commit, on `full`, session start.
- **Reaches**: the hosts at install and at run time, and any file written outside the repo (`~/.cache/prek`, `~/.local/bin`).
- **Cloud**: `yes`, or `local-only: <why>`.
- **Provenance**: `checked`, `none published` or `failed`.

## Refusing

Refused units are listed under **Found, not installed**, each with its reason, never dropped silently. A unit is refused when it:

1. sits in no trust tier;
2. can be neither pinned nor vendored;
3. fetches code at run time that cannot be rewritten to a pinned form;
4. has glue that cannot be read (a binary without source, a minified or obfuscated script);
5. fails its published provenance or its checksum;
6. is pinned to a SHA not reachable from the upstream ref it names (an impostor commit);
7. resolves to a publisher or repository its catalog `Publisher:` does not name.

A unit that cannot reach the cloud is not refused: its row says `local-only` and why. A unit the human installs by hand is audited on the next run like any other repo choice.
