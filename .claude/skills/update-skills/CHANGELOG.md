# CHANGELOG

Every entry here is written by the release run on main, which grades the bump
from the squash subject's Conventional-Commit type and cuts one entry per
released version. See
[docs/adr/0003-version-and-changelog-cut-on-merge.md](https://github.com/Gharib89/skills/blob/main/docs/adr/0003-version-and-changelog-cut-on-merge.md).

<!-- version list -->

## v0.7.1 (2026-10-10)

### Bug Fixes

- **update-skills**: An others row moves its printed pins in owned skills
  ([#541](https://github.com/Gharib89/skills/pull/541),
  [`26a4fc8`](https://github.com/Gharib89/skills/commit/26a4fc8f865469e8d46c1ab7da570845da758ff6))


## v0.7.0 (2026-10-10)

### Features

- **update-skills**: Daily upstream drift check files the drift issue and mentions the owner
  ([#536](https://github.com/Gharib89/skills/pull/536),
  [`911f84b`](https://github.com/Gharib89/skills/commit/911f84b05b9393baf45e111d6fe02aed7a44efe5))


## v0.6.1 (2026-10-05)

### Bug Fixes

- **ship**: Gate the retro's mechanical review findings
  ([#482](https://github.com/Gharib89/skills/pull/482),
  [`0c689e8`](https://github.com/Gharib89/skills/commit/0c689e8e731cc86b56bc2c28035c0e609183563a))


## v0.6.0 (2026-10-04)

### Features

- **update-skills**: Refresh every source-repo skill the lock records
  ([#475](https://github.com/Gharib89/skills/pull/475),
  [`a4dd316`](https://github.com/Gharib89/skills/commit/a4dd31692f01c34c0d42502a2bf719c798708b53))


## v0.5.3 (2026-10-04)

### Bug Fixes

- **update-skills**: Expect worktree exists alone before the push
  ([#474](https://github.com/Gharib89/skills/pull/474),
  [`8a7eba3`](https://github.com/Gharib89/skills/commit/8a7eba367e94766d27b2409cca0499e7335e0688))


## v0.5.2 (2026-10-04)

### Bug Fixes

- **update-skills**: Read upstream subjects by ancestry, not date
  ([#473](https://github.com/Gharib89/skills/pull/473),
  [`7a2b340`](https://github.com/Gharib89/skills/commit/7a2b34063b545f3696daf2802ede4a0d8f58d6c2))


## v0.5.1 (2026-10-02)

### Documentation

- Name the lock as the skill inventory, group the glossary, and hold hard-wrapped prose to 80
  columns ([#447](https://github.com/Gharib89/skills/pull/447),
  [`97cd290`](https://github.com/Gharib89/skills/commit/97cd290a2908d0b96d1b39b7766bbb667ebf11af))


## v0.5.0 (2026-09-30)

### Features

- **update-skills**: Follow setup-skills in-session instead of handing off
  ([#433](https://github.com/Gharib89/skills/pull/433),
  [`b98fed6`](https://github.com/Gharib89/skills/commit/b98fed61c2115036bf3a76e72bb92ce8b7bbc590))


## v0.4.1 (2026-09-30)

### Bug Fixes

- **ship**: Nested adapter calls survive RETURN traps; encoded PR lookup; token off argv; installer
  -f; capped base-fresh log ([#428](https://github.com/Gharib89/skills/pull/428),
  [`7266a14`](https://github.com/Gharib89/skills/commit/7266a14449df72012123eb22b543ced26f596ff4))


## v0.4.0 (2026-09-30)

### Features

- **ship**: Run the suite in parallel and show every refusal
  ([#417](https://github.com/Gharib89/skills/pull/417),
  [`f0510d0`](https://github.com/Gharib89/skills/commit/f0510d0927f3cb7fa0788cb1ca428cf97f6dabed))


## v0.3.0 (2026-09-29)

### Features

- **ship**: Move tdd and triage to d81f3a1 and retire CONTEXT.md for GLOSSARY.md
  ([#411](https://github.com/Gharib89/skills/pull/411),
  [`4ad593d`](https://github.com/Gharib89/skills/commit/4ad593dbc393d2e5d15046018cb9c8c98c9d2573))


## v0.2.0 (2026-09-28)

### Features

- **setup-skills**: Local gate and cloud bootstrap over the harness
  ([#384](https://github.com/Gharib89/skills/pull/384),
  [`a0a8f9a`](https://github.com/Gharib89/skills/commit/a0a8f9ae146bfec29e0f84faa3f72f76fb84c303))


## v0.1.1 (2026-09-28)

### Bug Fixes

- **skills**: Keep every skill self-contained, and gate it
  ([#380](https://github.com/Gharib89/skills/pull/380),
  [`76934a4`](https://github.com/Gharib89/skills/commit/76934a4d031a8f63229b3c79ea93df4d762b016e))


**The renumber to 0.x.** Every entry below predates it and keeps the number it was released under. `update-skills` was never publicly released, so on 2026-09-28 its version moved from 1.2.0 to 0.1.0: the old major is now the minor. The release run writes new entries above this note, counting on from 0.1.0. See [ADR 0005](https://github.com/Gharib89/skills/blob/main/docs/adr/0005-skills-stay-0x-until-public-release.md).

## v1.2.0 (2026-09-26)

### Features

- **setup-skills**: Ship block as a setup section, whole-file section re-run
  ([#326](https://github.com/Gharib89/skills/pull/326),
  [`874cca1`](https://github.com/Gharib89/skills/commit/874cca19fbe56ffc99be0d6cac1a31c09aa244ac))


## v1.1.0 (2026-09-26)

### Features

- **update-skills**: Dated branch, loop-safe installs, retired terms, readable PR body
  ([#325](https://github.com/Gharib89/skills/pull/325),
  [`ab84817`](https://github.com/Gharib89/skills/commit/ab84817eeee5d3ba3dec5c12c4f6f9eb27d66a30))
