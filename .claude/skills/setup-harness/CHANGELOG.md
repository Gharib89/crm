# CHANGELOG

Every entry here is written by the release run on main, which grades the bump
from the squash subject's Conventional-Commit type and cuts one entry per
released version. See
[docs/adr/0003-version-and-changelog-cut-on-merge.md](https://github.com/Gharib89/skills/blob/main/docs/adr/0003-version-and-changelog-cut-on-merge.md).

<!-- version list -->

## v0.15.0 (2026-10-30)

### Features

- **setup-harness**: A local-only harness and a no-harness gate name an install for a missing tool
  ([#1065](https://github.com/Gharib89/skills/pull/1065),
  [`fb0f990`](https://github.com/Gharib89/skills/commit/fb0f9903207c2b80a1eb09bac439933ea40d3efb))


## v0.14.0 (2026-10-30)

### Features

- **setup-harness**: A local session gets the gate tools' local install
  ([#1063](https://github.com/Gharib89/skills/pull/1063),
  [`36b7fc1`](https://github.com/Gharib89/skills/commit/36b7fc1df30af474fb8e9a9e53207946562088e2))


## v0.13.1 (2026-10-30)

### Bug Fixes

- **setup-harness**: The edit hook stays in the session's repo, and pinned cloud done tests match
  the pin ([#1061](https://github.com/Gharib89/skills/pull/1061),
  [`f943f0b`](https://github.com/Gharib89/skills/commit/f943f0b7a4789251ff8d9887bd60f13fa5b71cde))


## v0.13.0 (2026-10-20)

### Features

- **setup-harness**: {paths} hands a turn row its deleted paths
  ([#914](https://github.com/Gharib89/skills/pull/914),
  [`d33dd8f`](https://github.com/Gharib89/skills/commit/d33dd8f198f3c2d44a9cdc01b944bdbf8abc2c9e))


## v0.12.3 (2026-10-19)

### Bug Fixes

- **harness**: Turn rung runs the tests naming a deleted .sh
  ([#886](https://github.com/Gharib89/skills/pull/886),
  [`63e8b4f`](https://github.com/Gharib89/skills/commit/63e8b4f151f8586ff7718370567632d07cab21ec))


## v0.12.2 (2026-10-15)

### Bug Fixes

- **setup-harness**: Check.sh skips installed skills with no Excluded: line
  ([#714](https://github.com/Gharib89/skills/pull/714),
  [`ce88761`](https://github.com/Gharib89/skills/commit/ce88761915cb75acbea9ab5aba691140cf39468e))


## v0.12.1 (2026-10-14)

### Bug Fixes

- **setup-harness**: Check-hook stays quiet in a repo with no check.sh
  ([#634](https://github.com/Gharib89/skills/pull/634),
  [`211da1d`](https://github.com/Gharib89/skills/commit/211da1de1feda0e8da921800a45c1a3c054120de))


## v0.12.0 (2026-10-11)

### Features

- **setup-harness**: Check.sh installs a root's missing dependencies first
  ([#588](https://github.com/Gharib89/skills/pull/588),
  [`5a71eaf`](https://github.com/Gharib89/skills/commit/5a71eaf52af31eafe656c93d91919d57ae328417))


## v0.11.1 (2026-10-11)

### Bug Fixes

- **setup-harness**: Pyright finds the main checkout's .venv from a worktree
  ([#587](https://github.com/Gharib89/skills/pull/587),
  [`26136b0`](https://github.com/Gharib89/skills/commit/26136b0a725b96d72939faf026e5b20d2a50acb4))


## v0.11.0 (2026-10-11)

### Features

- **setup-harness**: The CLAUDE.md harness block says when to use and distrust LSP
  ([#583](https://github.com/Gharib89/skills/pull/583),
  [`dbffccb`](https://github.com/Gharib89/skills/commit/dbffccb734a00414ee252a8bc37302c7e525c560))


## v0.10.5 (2026-10-11)

### Bug Fixes

- **setup-harness**: Fetch a cloud proof sha the clone lacks before calling it unproven
  ([#580](https://github.com/Gharib89/skills/pull/580),
  [`2988fcf`](https://github.com/Gharib89/skills/commit/2988fcf1081b3a66767b9b7aec4675e911b11dba))


## v0.10.4 (2026-10-11)

### Bug Fixes

- **setup-harness**: Exclude the PR template from markdownlint, name the dev add's lockfile
  ([#575](https://github.com/Gharib89/skills/pull/575),
  [`1912043`](https://github.com/Gharib89/skills/commit/1912043fda57bb33d91afa62a9626b4375c7bfaf))


## v0.10.3 (2026-10-11)

### Bug Fixes

- **setup-harness**: Correct the hadolint row placement, move the schema rules, cut dead lines
  ([#571](https://github.com/Gharib89/skills/pull/571),
  [`acba298`](https://github.com/Gharib89/skills/commit/acba29869385b7dd5d63903fa4c8badf124c9360))


## v0.10.2 (2026-10-10)

### Bug Fixes

- **setup-harness**: Check.sh drops files under an exported CDPATH or a dash-named directory
  ([#563](https://github.com/Gharib89/skills/pull/563),
  [`c42be8e`](https://github.com/Gharib89/skills/commit/c42be8e80230776d9dc2603a75756953a25a9123))


## v0.10.1 (2026-10-10)

### Bug Fixes

- **setup-harness**: Run the full runner alone first, bound check.sh paths by real path
  ([#542](https://github.com/Gharib89/skills/pull/542),
  [`3c391a1`](https://github.com/Gharib89/skills/commit/3c391a14a9784f395b905825b3013f9f16be2454))


## v0.10.0 (2026-10-08)

### Bug Fixes

- **setup-harness**: Refresh the apt index before an apt route's install
  ([#512](https://github.com/Gharib89/skills/pull/512),
  [`a5200c5`](https://github.com/Gharib89/skills/commit/a5200c5c055d62879871f883343a20b30d9388f2))

### Features

- **setup-harness**: Run the full rung's rows concurrently
  ([#524](https://github.com/Gharib89/skills/pull/524),
  [`a215272`](https://github.com/Gharib89/skills/commit/a215272b10716b046fc8905bcd27e1841371a9d0))


## v0.9.4 (2026-10-05)

### Bug Fixes

- **ship**: Gate the retro's mechanical review findings
  ([#482](https://github.com/Gharib89/skills/pull/482),
  [`0c689e8`](https://github.com/Gharib89/skills/commit/0c689e8e731cc86b56bc2c28035c0e609183563a))


## v0.9.3 (2026-10-03)

### Bug Fixes

- **setup-harness**: Check.sh skips paths outside the repo
  ([#461](https://github.com/Gharib89/skills/pull/461),
  [`03cb0e1`](https://github.com/Gharib89/skills/commit/03cb0e1285c6f08d137adc8ac7ce7d2fefc1b9e5))


## v0.9.2 (2026-09-30)

### Bug Fixes

- **ship**: Nested adapter calls survive RETURN traps; encoded PR lookup; token off argv; installer
  -f; capped base-fresh log ([#428](https://github.com/Gharib89/skills/pull/428),
  [`7266a14`](https://github.com/Gharib89/skills/commit/7266a14449df72012123eb22b543ced26f596ff4))


## v0.9.1 (2026-09-30)

### Bug Fixes

- Self-contained gate tracks fence length and reference links
  ([#427](https://github.com/Gharib89/skills/pull/427),
  [`4a7d28f`](https://github.com/Gharib89/skills/commit/4a7d28faf1017cd1af6aefb618795bdfdf0eb500))


## v0.9.0 (2026-09-29)

### Features

- **setup-harness**: Exclude fixture trees and fix five more setup gaps
  ([#402](https://github.com/Gharib89/skills/pull/402),
  [`deabc91`](https://github.com/Gharib89/skills/commit/deabc9171865360d274c8e77016a86a131397625))


## v0.8.2 (2026-09-29)

### Bug Fixes

- **setup-harness**: A new CLAUDE.md opens with a heading, and Prove checks the run's own files
  ([#396](https://github.com/Gharib89/skills/pull/396),
  [`69d412d`](https://github.com/Gharib89/skills/commit/69d412d348249330df4259c9f9389ebabcbb18fb))


## v0.8.1 (2026-09-29)

### Bug Fixes

- **setup-harness**: Decide a vendored plugin pin's Behind by its vendored glue
  ([#391](https://github.com/Gharib89/skills/pull/391),
  [`d2acca4`](https://github.com/Gharib89/skills/commit/d2acca44c0340ebc71be6e1dac45b7cab27f260d))


## v0.8.0 (2026-09-29)

### Features

- **setup-harness**: Record a root the human marked in the harness profile (Schema 2)
  ([#393](https://github.com/Gharib89/skills/pull/393),
  [`094089d`](https://github.com/Gharib89/skills/commit/094089db2ba2d71e9fab489efcf8eefc719e53dd))


## v0.7.1 (2026-09-29)

### Bug Fixes

- **setup-harness**: Vendored pyright resolves a stack's .venv, testmon honours addopts selectors
  ([#386](https://github.com/Gharib89/skills/pull/386),
  [`7530d75`](https://github.com/Gharib89/skills/commit/7530d75dc0aa770cbf9116e2d5efb24525ef936f))


## v0.7.0 (2026-09-29)

### Features

- **setup-harness**: Pin jdtls from repo.eclipse.org behind a vendored launcher
  ([#388](https://github.com/Gharib89/skills/pull/388),
  [`9d2f028`](https://github.com/Gharib89/skills/commit/9d2f0286534ddce1837bc763631098091e9caf62))


## v0.6.1 (2026-09-28)

### Bug Fixes

- **setup-harness**: Pass ShellCheck at default severity in the check.sh template
  ([#385](https://github.com/Gharib89/skills/pull/385),
  [`014f6d4`](https://github.com/Gharib89/skills/commit/014f6d454605c7045e32e5535a9afff295dcdea4))


## v0.6.0 (2026-09-28)

### Features

- **setup-harness**: Surfaces and behaviour tools
  ([#382](https://github.com/Gharib89/skills/pull/382),
  [`0a17675`](https://github.com/Gharib89/skills/commit/0a17675dc772413920fc3c10e1a2ce72d650b39d))


## v0.5.1 (2026-09-28)

### Bug Fixes

- **skills**: Keep every skill self-contained, and gate it
  ([#380](https://github.com/Gharib89/skills/pull/380),
  [`76934a4`](https://github.com/Gharib89/skills/commit/76934a4d031a8f63229b3c79ea93df4d762b016e))


## v0.5.0 (2026-09-28)

### Features

- **setup-harness**: Catalog entries for Go, Rust, .NET, Java/Kotlin, Dockerfile, workflows and
  Markdown ([#378](https://github.com/Gharib89/skills/pull/378),
  [`ca2322d`](https://github.com/Gharib89/skills/commit/ca2322d56aaa0321a1dd88de65ba0a1545e89625))


## v0.4.0 (2026-09-28)

### Features

- **setup-harness**: The re-run gap report and the check.sh contract check
  ([#375](https://github.com/Gharib89/skills/pull/375),
  [`4e0de2e`](https://github.com/Gharib89/skills/commit/4e0de2ec72f7f72bafb4230f35a5e564c4db320d))


## v0.3.0 (2026-09-28)

### Features

- **setup-harness**: Vendored language servers and the LSP proof
  ([#374](https://github.com/Gharib89/skills/pull/374),
  [`3b6d33f`](https://github.com/Gharib89/skills/commit/3b6d33fab6e0b24918e570c52d78fca2368c3327))


## v0.2.0 (2026-09-28)

### Features

- **setup-harness**: Cloud setup, the cloud-first verdict and its proof
  ([#372](https://github.com/Gharib89/skills/pull/372),
  [`7324351`](https://github.com/Gharib89/skills/commit/73243511ff3e36977588a9e910c66c0c85d4b2f3))


**The renumber to 0.x.** Every entry below predates it and keeps the number it was released under. `setup-harness` was never publicly released, so on 2026-09-28 its version moved from 1.0.0 to 0.1.0: the old major is now the minor. The release run writes new entries above this note, counting on from 0.1.0. See [ADR 0005](https://github.com/Gharib89/skills/blob/main/docs/adr/0005-skills-stay-0x-until-public-release.md).
