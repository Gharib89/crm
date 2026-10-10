# CHANGELOG

Every entry here is written by the release run on main, which grades the bump
from the squash subject's Conventional-Commit type and cuts one entry per
released version. See
[docs/adr/0003-version-and-changelog-cut-on-merge.md](https://github.com/Gharib89/skills/blob/main/docs/adr/0003-version-and-changelog-cut-on-merge.md).

<!-- version list -->

## v0.16.0 (2026-10-10)

### Features

- **ship**: A fallback reviewer also stands in for a primary capped with findings
  ([#534](https://github.com/Gharib89/skills/pull/534),
  [`da3acb0`](https://github.com/Gharib89/skills/commit/da3acb05d00fee258e13850b566ba89a5764a1d9))


## v0.15.0 (2026-10-10)

### Features

- **ship**: Move find-docs to 522c4db ([#532](https://github.com/Gharib89/skills/pull/532),
  [`0c7d869`](https://github.com/Gharib89/skills/commit/0c7d869fe95f147ef7e034e8e2937956862e974d))


## v0.14.0 (2026-10-08)

### Bug Fixes

- **setup-skills**: Pin claude-code-action v1.0.244 so opus reaches Opus 5.5
  ([#514](https://github.com/Gharib89/skills/pull/514),
  [`28342e5`](https://github.com/Gharib89/skills/commit/28342e514254fef5ee3b2d97f832e5f02fd2e614))

### Features

- **ship**: Merge on a clean gate in repos that opt in
  ([#501](https://github.com/Gharib89/skills/pull/501),
  [`3d7d8c2`](https://github.com/Gharib89/skills/commit/3d7d8c2d517d558298f6c855dbb8b5a9751dd259))

- **ship**: Move code-review and tdd to f3fc563
  ([#508](https://github.com/Gharib89/skills/pull/508),
  [`16646c2`](https://github.com/Gharib89/skills/commit/16646c25737c6b151cee4433afe2bba838d5a09e))

- **ship**: Read native Codex review as a third on-request reviewer
  ([#513](https://github.com/Gharib89/skills/pull/513),
  [`e7b6598`](https://github.com/Gharib89/skills/commit/e7b65987e2400571eb9fa790e46227b79f323922))

- **ship**: Self-review gates what reviewers keep catching
  ([#494](https://github.com/Gharib89/skills/pull/494),
  [`5ffa640`](https://github.com/Gharib89/skills/commit/5ffa64042668f61e455249374dc6c221c10ae348))

- **ship**: State each rule once and keep unmet criteria verbatim
  ([#515](https://github.com/Gharib89/skills/pull/515),
  [`c923bb4`](https://github.com/Gharib89/skills/commit/c923bb4f493ac13c8883a7328c440e3620ea9f5f))


## v0.13.1 (2026-10-05)

### Bug Fixes

- **setup-skills**: Have the claude reviewer read the standards files its diff routes to
  ([`cf5984e`](https://github.com/Gharib89/skills/commit/cf5984e9736850a16552f042b683ddf3f1c9bc7b))


## v0.13.0 (2026-10-04)

### Features

- **update-skills**: Refresh every source-repo skill the lock records
  ([#475](https://github.com/Gharib89/skills/pull/475),
  [`a4dd316`](https://github.com/Gharib89/skills/commit/a4dd31692f01c34c0d42502a2bf719c798708b53))


## v0.12.2 (2026-10-03)

### Bug Fixes

- **setup-skills**: A later gate write keeps the worse status
  ([#465](https://github.com/Gharib89/skills/pull/465),
  [`8c85f1d`](https://github.com/Gharib89/skills/commit/8c85f1dbc916629539b03d60498f041f8f9793be))


## v0.12.1 (2026-10-03)

### Refactoring

- **setup-skills**: Local gate on Bash 3.2, one bootstrap test, stale gate comments
  ([#463](https://github.com/Gharib89/skills/pull/463),
  [`75a0de3`](https://github.com/Gharib89/skills/commit/75a0de3ed65528dda3630a8c748d5c9367bed81b))


## v0.12.0 (2026-10-02)

### Features

- The lock decides the skill set, and each check and tool message points to the way forward
  ([#443](https://github.com/Gharib89/skills/pull/443),
  [`4af91bb`](https://github.com/Gharib89/skills/commit/4af91bbc5c913ef958d6d591e961133dfc279fbf))


## v0.11.1 (2026-09-30)

### Documentation

- **setup-skills**: Carry the three depth checks in the standards template
  ([#430](https://github.com/Gharib89/skills/pull/430),
  [`5e55dca`](https://github.com/Gharib89/skills/commit/5e55dca39b7d904d797e752a4b9d1d5bfd23152c))


## v0.11.0 (2026-09-29)

### Features

- **ship**: Move tdd and triage to d81f3a1 and retire CONTEXT.md for GLOSSARY.md
  ([#411](https://github.com/Gharib89/skills/pull/411),
  [`4ad593d`](https://github.com/Gharib89/skills/commit/4ad593dbc393d2e5d15046018cb9c8c98c9d2573))


## v0.10.2 (2026-09-29)

### Bug Fixes

- **setup-skills**: Refresh an installed Claude reviewer workflow on a setup section re-run
  ([#409](https://github.com/Gharib89/skills/pull/409),
  [`8d3774f`](https://github.com/Gharib89/skills/commit/8d3774f5ff0a5eb7ed628b5f857f76a53effe6d4))


## v0.10.1 (2026-09-29)

### Bug Fixes

- **setup-skills**: Map check.sh's per-check statuses on exit 2 and 3
  ([#410](https://github.com/Gharib89/skills/pull/410),
  [`226127c`](https://github.com/Gharib89/skills/commit/226127cd6445116c57b4abe7a32c48f4642f2874))


## v0.10.0 (2026-09-29)

### Features

- **setup-skills**: Pin the claude reviewer template's actions and drop checkout credentials
  ([#405](https://github.com/Gharib89/skills/pull/405),
  [`9b40b12`](https://github.com/Gharib89/skills/commit/9b40b12537d8a02cbbd3df9cb213305ca145d0f5))


## v0.9.0 (2026-09-28)

### Features

- **setup-skills**: Local gate and cloud bootstrap over the harness
  ([#384](https://github.com/Gharib89/skills/pull/384),
  [`a0a8f9a`](https://github.com/Gharib89/skills/commit/a0a8f9ae146bfec29e0f84faa3f72f76fb84c303))


## v0.8.2 (2026-09-28)

### Bug Fixes

- **skills**: Keep every skill self-contained, and gate it
  ([#380](https://github.com/Gharib89/skills/pull/380),
  [`76934a4`](https://github.com/Gharib89/skills/commit/76934a4d031a8f63229b3c79ea93df4d762b016e))


**The renumber to 0.x.** Every entry below predates it and keeps the number it was released under. `setup-skills` was never publicly released, so on 2026-09-28 its version moved from 8.1.1 to 0.8.1: the old major is now the minor. The release run writes new entries above this note, counting on from 0.8.1. See [ADR 0005](https://github.com/Gharib89/skills/blob/main/docs/adr/0005-skills-stay-0x-until-public-release.md).

## v8.1.1 (2026-09-26)

### Bug Fixes

- **skills**: Prompt-audit cleanup of ship and setup-skills prose
  ([`9b84fb6`](https://github.com/Gharib89/skills/commit/9b84fb6742875c2fd9dda08f0010b31f200bb5ab))


## v8.1.0 (2026-09-26)

### Features

- **setup-skills**: Ship block as a setup section, whole-file section re-run
  ([#326](https://github.com/Gharib89/skills/pull/326),
  [`874cca1`](https://github.com/Gharib89/skills/commit/874cca19fbe56ffc99be0d6cac1a31c09aa244ac))


## v8.0.0 (2026-09-26)

### Bug Fixes

- **ship**: Move show-me to ca7c808 ([#319](https://github.com/Gharib89/skills/pull/319),
  [`33061b2`](https://github.com/Gharib89/skills/commit/33061b21f99791ae686b005f3055995d174f2537))

### Breaking Changes

- **ship**: Preflight refuses a consumer whose show-me is still at 6ab9013; refresh it at ca7c808.


## v7.1.0 (2026-09-26)

### Features

- **update-skills**: Refresh skills at pinned refs, report upstream drift, summarise in one PR
  ([#318](https://github.com/Gharib89/skills/pull/318),
  [`232fae5`](https://github.com/Gharib89/skills/commit/232fae52afd8369053e6555cc5ce543d5dad83aa))


## v7.0.0 (2026-09-26)

### Features

- **ship**: Pin composed skills and route Ship defects to the source repo
  ([#316](https://github.com/Gharib89/skills/pull/316),
  [`34c2c35`](https://github.com/Gharib89/skills/commit/34c2c35bb8f03e52a9ffbafb25734eea1f1df46e))

### Breaking Changes

- **ship**: Ship's metadata.composes entries are <owner>/<repo>#<sha>:<skill>; a consumer's composed
  skills are refreshed at those pins.


## v6.0.0 (2026-09-25)

### Features

- **ship**: A narrower mechanic rule and a best-effort reviewer loop
  ([#309](https://github.com/Gharib89/skills/pull/309),
  [`38fcbc5`](https://github.com/Gharib89/skills/commit/38fcbc57869018b4602888a3f2be4cf991326189))

### Breaking Changes

- **ship**: Poll-pr drops --free-round, --review-on-push and the never_queued and degraded fields,
  and gains not_reviewed.

- The phase-7 exit vocabulary the Review line and cloud-ship relay changes from converged/degraded
  to reviewed/not reviewed.


## v5.9.0 (2026-09-25)

### Features

- **setup-skills**: Fail the gate on an unresolvable base, and gate the Claude reviewer on the PR
  author ([#305](https://github.com/Gharib89/skills/pull/305),
  [`093a7a6`](https://github.com/Gharib89/skills/commit/093a7a6afc53aca7b63bc7fdffa4f18d5083770d))


## v5.8.0 (2026-09-25)

### Features

- **ship**: A measured 200-line small lane, reviewer probes from preflight, parallel phase 4
  ([#300](https://github.com/Gharib89/skills/pull/300),
  [`38daa3f`](https://github.com/Gharib89/skills/commit/38daa3f04531f861e93e67362f4d26564f23b0f2))


## v5.7.0 (2026-09-25)

### Features

- **setup-skills**: The Claude reviewer prompt names the Bash shapes its allowlist refuses
  ([#296](https://github.com/Gharib89/skills/pull/296),
  [`527ee4c`](https://github.com/Gharib89/skills/commit/527ee4c962de749abdcf89a3b9b53fb3ec0f08ba))


## v5.6.0 (2026-09-25)

### Features

- **ship**: Update-issue-body lands a tracker-issue docs-sync target after the merge
  ([#292](https://github.com/Gharib89/skills/pull/292),
  [`bd47489`](https://github.com/Gharib89/skills/commit/bd47489c43b1d696750b3efdab14efa9d6f9450f))


## v5.5.0 (2026-09-24)

### Features

- **setup-skills**: The Claude reviewer reads a saved diff and the PR head's copy of a changed file
  ([#287](https://github.com/Gharib89/skills/pull/287),
  [`c169357`](https://github.com/Gharib89/skills/commit/c169357164d5a1febd10afaeb830cb4dfc6f9ef6))


## v5.4.0 (2026-09-24)

### Features

- **setup-skills**: The Claude reviewer posts its review with typed -F fields and names its denied
  calls ([#283](https://github.com/Gharib89/skills/pull/283),
  [`d58b728`](https://github.com/Gharib89/skills/commit/d58b7281084e3ecad2476d7eac94e19f5e93c3f5))


## v5.3.0 (2026-09-24)

### Features

- **ship**: An attended run in a cloud sandbox prepares it as an unattended run does
  ([#263](https://github.com/Gharib89/skills/pull/263),
  [`2101eae`](https://github.com/Gharib89/skills/commit/2101eae4be28ca49caa65dc32e47dcc817a734c4))


## v5.2.0 (2026-09-23)

### Features

- **ship**: Poll-pr and request-review take the Reviewer by name
  ([#248](https://github.com/Gharib89/skills/pull/248),
  [`ef35ae6`](https://github.com/Gharib89/skills/commit/ef35ae6b5805d05becf0aebc3abbe4857abbcbba))


## v5.1.2 (2026-09-23)

### Documentation

- **ship**: Agent-facing is the one rule, subagents write their own Report files, derived-copy
  pointer rule ([#245](https://github.com/Gharib89/skills/pull/245),
  [`c3b3e89`](https://github.com/Gharib89/skills/commit/c3b3e8907859cc343442f05aad3dc4783f61b722))


## v5.1.1 (2026-09-23)

### Bug Fixes

- **ship**: Drop dated prompt patterns found by a prompt audit
  ([`c4e475e`](https://github.com/Gharib89/skills/commit/c4e475e2d89af2ad7b81d594990ad695460c3522))


## v5.1.0 (2026-09-21)

### Features

- **ship**: The PR body states the door and the blast radius
  ([#237](https://github.com/Gharib89/skills/pull/237),
  [`5f6a870`](https://github.com/Gharib89/skills/commit/5f6a87093c08e94667b719c5a628d9375cdbeed4))


## v5.0.0 (2026-09-21)

### Features

- **ci**: Cut the version bump and per-skill changelog on merge
  ([#224](https://github.com/Gharib89/skills/pull/224),
  [`612baa8`](https://github.com/Gharib89/skills/commit/612baa85c93acbe697872ca324396722afe14ca2))

- **setup-skills**: Create the Kind, Size and Priority dimension labels and write their section
  ([#228](https://github.com/Gharib89/skills/pull/228),
  [`4913e44`](https://github.com/Gharib89/skills/commit/4913e4487191bcdb66140b2bea968bdde6108739))

- **ship**: --full needs --brief, ci-wait reads No-checks legal, run-file takes --issue
  ([#232](https://github.com/Gharib89/skills/pull/232),
  [`6765e04`](https://github.com/Gharib89/skills/commit/6765e047c40cfe4ebae0e0bb438b7d7f115c0e22))

- **ship**: The PR body becomes the reviewer's short form
  ([#226](https://github.com/Gharib89/skills/pull/226),
  [`1fd3013`](https://github.com/Gharib89/skills/commit/1fd30135b2121bafae1f3a669281f0e877139817))

### Breaking Changes

- **ship**: `poll-pr <pr> --full <id>` without `--brief` now exits 2. The invocation that lifted a
  round's clip is `poll-pr <pr> --brief --full <id>`.
