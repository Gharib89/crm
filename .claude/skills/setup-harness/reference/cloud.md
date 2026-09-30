# The cloud: verdict, cloud setup and proof

## Contents

- [The verdict](#the-verdict)
- [Rung labels](#rung-labels)
- [The host list](#the-host-list)
- [Writing the cloud setup](#writing-the-cloud-setup)
- [The proof](#the-proof)
- [Recording the proof](#recording-the-proof)

A repo is **cloud-first**: every Claude Code on the web session gets the harness's tools from a committed cloud setup, and the repo proves it in a real cloud session. It is **local-only** only for a reason below.

## The verdict

Propose local-only only on **evidence in the repo** (a file, a build target, a host) that the check entry point itself needs one of:

| Need | Evidence, for example |
|---|---|
| a build that is not Linux x86_64 | an Xcode project or `Package.swift` building for Apple platforms, a Windows-only `.vcxproj`/`.sln` target, an ARM-only toolchain |
| a private network or VPN | a host the checks or tests reach that only a VPN or private network serves (an `*.internal` or RFC 1918 address, a corporate DNS name) |
| interactive or SSO auth | `az login`, `aws sso login`, `gcloud auth login` or a browser sign-in on a check's path |
| more than the VM holds | a check that needs more than 4 vCPU, 16 GB RAM or 30 GB disk |
| hardware or a licensed tool | a device, GPU or license server a test needs |
| org IP allowlisting or Zero Data Retention | a stated org policy the repo carries |
| secrets that cannot be plain environment variables | a check reading a keystore, a key file or a credential store; a cloud environment's secrets are plain variables any of its users can read |

**Azure DevOps is not a reason**: its cloud route is a bundle session (below). Evidence of a need that blocks only some part (a live end-to-end suite against a VPN host) leaves the repo cloud-first: that part is recorded `Local-only: <part>: <reason>`, `<part>` its `FULL_ROWS` row's name, which goes on `LOCAL_ONLY` ([check-ladder.md](check-ladder.md)), so the other checks still run in the cloud. A part with no `FULL_ROWS` row of its own (a member's `TURN_ROWS` tests, a check target that runs other checks too) cannot be skipped alone: it is a local-only verdict question, with its evidence.

Present the verdict with its evidence paths, and the finding for each need that has none, so the human can check the reasoning. The human may also choose local-only with no evidence: record `Verdict: local-only: operator's choice: <why>`. A local-only verdict's reason names the file, target or host. A local-only repo gets no cloud setup and no `SessionStart` entry: `Setup: None.`, `Proof: unproven`, and the report says `cloud: n/a (local-only: <reason>)`.

## Rung labels

Each rung and part of the harness carries one cloud label, shown in the report's cloud table:

- `cloud`: its tools install through passing routes and run there.
- `local-only`: the cloud cannot run it, with no substitute. Language servers and repo-enabled plugins are always local-only (a cloud session installs no repo-enabled plugin); a `Local-only:` line's part is too.
- `needs the Custom allowlist: <hosts>`: a tool it needs installs only from a host the Default network blocks. The one environment step the skill names is adding those hosts to the cloud environment's Custom network allowlist.

Blocked hosts are re-derived every run by the static host check; only the human's answer is recorded. `Allowlist:` names the hosts the human says their environment's Custom allowlist admits, and nothing else.

## The host list

Measured in a cloud session on the Default (Trusted) network, Claude Code 2.1.283. A host missing from both columns is unmeasured: write the step, and the proof's cloud session is what proves it.

| Passes | Blocked (403 or CONNECT refused) |
|---|---|
| `archive.ubuntu.com` (apt), `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`, `proxy.golang.org`, `sum.golang.org`, `static.crates.io`, `static.rust-lang.org`, RubyGems, Maven Central, the Gradle plugin portal, `api.nuget.org`, `repo.packagist.org`, `raw.githubusercontent.com`, `registry-1.docker.io`, `mcr.microsoft.com` and its `*.data.mcr.microsoft.com` blobs, `dev.azure.com`, `packages.microsoft.com`, a `git clone` of a public repo over `github.com` | `objects.githubusercontent.com` and `release-assets.githubusercontent.com` (another project's GitHub release assets), `codeload.github.com` (tarballs), `deb.nodesource.com`, `cli.github.com` (apt), `apt.llvm.org`, `cdn.playwright.dev`, `playwright.download.prss.microsoft.com`, `playwright.azureedge.net`, `storage.googleapis.com` |

Node 20, 21 and 22 ship on the image under `/opt` (22 on `PATH`), as do `uv`, `pnpm`, Go 1.24, Rust (rustup, cargo), OpenJDK 21, Gradle 8.14, Maven 3.9 and Docker (Buildx 0.31), so a runtime is a step only where the repo's runtime version file asks for one the image lacks, and never through NodeSource.

**The static host check.** For every step the cloud setup runs, name the host its route reaches: apt is `archive.ubuntu.com`, `uv` and `pip` are PyPI, `npm`, `pnpm` and `npx` are `registry.npmjs.org`, `go install` is the Go proxy, `cargo install` is `index.crates.io` and `static.crates.io`, `rustup component add` is `static.rust-lang.org`, `docker pull` is `registry-1.docker.io`, `docker create` or `docker pull` of an image named with a registry host is that host (`mcr.microsoft.com`), `playwright install` is `cdn.playwright.dev` (plus apt with `--with-deps`), `dotnet` restore is `api.nuget.org`, a URL in the command is its own host. A step reaching a blocked host that is not on `Allowlist:` is not written: its rung is labelled `needs the Custom allowlist: <hosts>`, and the human is asked whether their environment admits those hosts. A yes adds them to `Allowlist:` and the step is written; the proof's cloud session then checks the claim.

## Writing the cloud setup

An existing `SessionStart` entry that runs a script only when `CLAUDE_CODE_REMOTE=true` is the repo's cloud setup: extend that script in place with the missing steps, keep what it does, and record its path on `Setup:`. Never add a second one.

The exception is Ship's cloud bootstrap, the script the `Bootstrap:` line under `## Cloud lane` in `docs/agents/ship.md` names: it has its own exit contract, which Ship reads as `bootstrap-failed`, and setup-skills rewrites it onto its own template, which runs the cloud setup from `Setup:`. Copy and fill `.claude/hooks/cloud-setup.sh` as below instead, but merge no `SessionStart` entry: chain the script into that entry after any redirect or subshell the entry wraps the bootstrap in, so its status line is the entry's own output (`<the entry's command>; "$CLAUDE_PROJECT_DIR"/.claude/hooks/cloud-setup.sh`). It is the last command unless the entry ends in `exit` or `exec`, which nothing after runs: it goes before a trailing `exit`, and a trailing `exec <command>` becomes `<command>` with the script after it. The entry's `timeout` becomes the larger of its own and the one below: the setup-skills bootstrap already runs the cloud setup from `Setup:`, which leaves the chained run its fast no-op.

Otherwise, or with no such entry, copy [templates/cloud-setup.sh](../templates/cloud-setup.sh) to `.claude/hooks/cloud-setup.sh`, executable, fill its `STEPS` block, and merge [templates/settings-cloud.json](../templates/settings-cloud.json) into `.claude/settings.json` beside the repo's own hooks, with `timeout` the `Cloud setup:` budget plus max(10 s, budget / 4): 375 at the default 300 s, the template's value, recomputed on an override. The entry is synchronous (no `async`) so the tools exist before the first edit, and its guard exits 0 outside a cloud session, so a local session shows no hook error.

`STEPS`, one `<name>|<done test>|<command>` row each, in this order:

1. A runtime or tool the image lacks, by the catalog tool's `Route:` (a `docker pull` route is step 5's, run after dockerd starts), done test `command -v <tool>`, or `<tool> --version` where a launcher answers before the tool exists (rustup's `rustfmt` and `cargo-clippy` proxies) or the image ships its own unpinned copy (`[ "$(prettier --version 2>/dev/null)" = <v> ]`: a done test carries no `|`, which separates the row's fields); a runtime the image already ships at a version the repo accepts needs no step. An apt route gains `-o DPkg::Lock::Timeout=120`, because the image's own dpkg still holds the lock when the hook starts and apt otherwise fails at once (`shellcheck|command -v shellcheck|sudo apt-get -o DPkg::Lock::Timeout=120 install -y shellcheck`).
2. prek where it is not a dev dependency, by the command [runner.md](runner.md) installed it with.
3. Each root's frozen install, no done test (`deps-api||cd api && uv sync --frozen`). Where a griffe row exists, then `tags|test -n "$(git tag -l)"|git fetch -q --tags origin`, since a GitHub cloud session's clone holds no tags (measured) and griffe diffs against the latest one; the done test skips the fetch where the clone has them.
4. The runner's git shim and hook environments, no done test (`prek||uv run --frozen prek install --prepare-hooks --allow-missing-config`), so the commit rung works and its first run downloads nothing.
5. Where a wired tool needs dockerd (a tool run in a container, such as the Dockerfile entry's hadolint, or `docker build --check`): after steps 1 to 4, because the setup stops at its first failed row and a Docker Hub 429 fails the pull: start dockerd, then pull each container image a wired tool runs, `<tag>` its picked version and `<digest>` the hook's pinned digest. The sandbox image ships dockerd without starting it, and an idle restart kills the daemon while the disk keeps the pulled images, so each row's done test lets a second run skip it:
   - `dockerd|docker info|setsid -f dockerd >"${TMPDIR:-/tmp}/dockerd.log" 2>&1 </dev/null; for i in $(seq 60); do docker info >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1`
   - `hadolint-image|docker image inspect hadolint/hadolint@<digest>|docker pull hadolint/hadolint:<tag>@<digest>`

   `setsid -f` and the redirects detach the daemon: one holding the hook's stdout or stderr makes Claude Code wait on the hook until its timeout.
6. For each web UI member ([surfaces.md](surfaces.md) `## Web UI`): last of all, after that member's frozen install and, on the `mcr` route, after the `dockerd` row, which the setup carries for it even where no container tool needs one. `<member>` is the member's directory, `.` at the repo root where the name drops its `-<member>`, and `<exec>` its stack's exec command (`pnpm exec`). The done test reads the revision directories from the member's own Playwright (`install --dry-run` lists them offline, under the image's `PLAYWRIGHT_BROWSERS_PATH`) and passes when each holds Playwright's `INSTALLATION_COMPLETE` marker, so a second run, or a repo on the image's own Playwright version, does nothing. One row, by route:
   - `browsers-<member>|cd <member> && n=0 && for p in $(<exec> playwright install --dry-run chromium); do case $p in /*) if [ ! -e "$p/INSTALLATION_COMPLETE" ]; then exit 1; fi; n=1 ;; esac; done; [ $n = 1 ]|cd <member> && <exec> playwright install --with-deps chromium`
   - `browsers-<member>|cd <member> && n=0 && for p in $(<exec> playwright install --dry-run chromium); do case $p in /*) if [ ! -e "$p/INSTALLATION_COMPLETE" ]; then exit 1; fi; n=1 ;; esac; done; [ $n = 1 ]|cd <member> && v=$(<exec> playwright --version) && c=$(docker create mcr.microsoft.com/playwright:v${v##* }-noble) && for p in $(<exec> playwright install --dry-run chromium); do case $p in /*) docker cp "$c:/ms-playwright/${p##*/}" "${p%/*}/" || { docker rm "$c"; exit 1; } ;; esac; done && docker rm "$c"`

   The first is the `vendor` route, for an `Allowlist:` naming `cdn.playwright.dev`; the second is `mcr`, which copies only the revision directories the member's Playwright names out of the image tagged with its version.

A step needing a blocked host is labelled, not written, per the static host check.

## The proof

Three parts, in order. The report says `cloud: unproven` until the third passes.

1. **Locally, twice.** `CLAUDE_CODE_REMOTE=true .claude/hooks/cloud-setup.sh`, timed with the wrapper's clock: the first prints `harness cloud setup: ok` within the `Cloud setup:` budget (else the override offer), and the second is a fast no-op. Report both times. The browser and `dockerd` rows act on this machine, not the image, so a local skip of either proves nothing about the cloud, and a failure of either row here is reported `local run: <row> not measurable locally`, not as a failed setup: step 1 then passes when every row before it passed, the budget and no-op judged on those rows. A failed `sudo` in any other row is a failed setup.
2. **Statically.** The static host check over the setup as written: no step reaches a blocked host that is not on `Allowlist:`.
3. **In a real cloud session.** The session runs the harness at a commit, so every file this run wrote or changed is committed first, then:
   - **GitHub** (the push route): name the branch and push it once the human says yes (`git push -u origin HEAD`). The session starts on the current branch.
   - **Azure DevOps** (`origin` on `dev.azure.com` or `visualstudio.com`): nothing is pushed. First the gate: `git status --porcelain` must print nothing (the tree was clean when the run began, so every line is this run's, a fix-mode rewrite included); else stop, name each file as `untracked: <path>` or `uncommitted: <path>`, and say why: a bundle session drops an untracked file silently and holds back an uncommitted `.claude/settings.json`, so the proof would test a harness that is not this one. Then the command is prefixed `CCR_FORCE_BUNDLE=1`, which uploads local `HEAD`, pushed or not.

   Print the command and have the human run it from the repo, after `/remote-env` in Claude Code has picked this repo's cloud environment (without one, `--cloud` uses a fallback environment silently):

   ```sh
   claude --cloud "Report the harness cloud setup line this session's SessionStart hook printed, then the output of git rev-parse HEAD, then run scripts/check.sh full twice and report each JSON line, exit code and wall-clock seconds."
   ```

   With `Allowlist:` hosts, the prompt ends `, then report the HTTP status of curl -s -o /dev/null -w %{http_code} https://<host> for each of: <hosts>.` The prompt carries no `$`, backtick or double quote, so the shell passes it as written.

   The human pastes the answer back. It proves the harness when the status line is `harness cloud setup: ok`, the sha is the `HEAD` the command was printed at, and both `check.sh full` runs exit 0; and an `Allowlist:` host answering `403` or `000` fails it, since the environment does not admit what the human said it does. Report a failure by what failed (`FAILED <step>`, a failing check, the host) and offer the proof again once it is fixed. No answer is `cloud: unproven`.

## Recording the proof

A passing answer writes `Proof: <sha>` into `docs/agents/harness.md` and commits that file alone (`git commit -m "chore(harness): cloud proof at <sha>" -- docs/agents/harness.md`), so the proven commit never carries its own proof. On Azure DevOps the sha may be unpushed.

The report's **cloud table**: one row per rung, its cloud label, and its times, `full` from the proof's cloud session (first run cold, second warm, judged against its budget) and the cloud setup from the local double run.

A re-run reads the proof as standing while `git diff <sha> -- <Setup: path> .claude/settings.json scripts/check.sh <each root's lockfile> <the runner config>` prints nothing; otherwise, or when the sha is not in the repo, it reports `cloud: unproven (changed since <sha>)` and offers the proof again.
