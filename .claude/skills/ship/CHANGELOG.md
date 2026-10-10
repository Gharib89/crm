# CHANGELOG

Every entry here is written by the release run on main, which grades the bump
from the squash subject's Conventional-Commit type and cuts one entry per
released version. See
[docs/adr/0003-version-and-changelog-cut-on-merge.md](https://github.com/Gharib89/skills/blob/main/docs/adr/0003-version-and-changelog-cut-on-merge.md).

<!-- version list -->

## v0.21.1 (2026-10-10)

### Bug Fixes

- **ship**: Remove an Azure DevOps tag through invoke wit/$batch, so a PAT-only session can (#540)
  ([#543](https://github.com/Gharib89/skills/pull/543),
  [`96a28db`](https://github.com/Gharib89/skills/commit/96a28dbc3febdd1b1eb6e7f965ab0d20ade3ac2f))


## v0.21.0 (2026-10-10)

### Features

- **ship**: A fallback reviewer also stands in for a primary capped with findings
  ([#534](https://github.com/Gharib89/skills/pull/534),
  [`da3acb0`](https://github.com/Gharib89/skills/commit/da3acb05d00fee258e13850b566ba89a5764a1d9))


## v0.20.0 (2026-10-10)

### Features

- **ship**: Move find-docs to 522c4db ([#532](https://github.com/Gharib89/skills/pull/532),
  [`0c7d869`](https://github.com/Gharib89/skills/commit/0c7d869fe95f147ef7e034e8e2937956862e974d))


## v0.19.0 (2026-10-08)

### Bug Fixes

- **ship**: Read the reviewer's workflow runs unfiltered so a live run is seen
  ([#522](https://github.com/Gharib89/skills/pull/522),
  [`a16a3f2`](https://github.com/Gharib89/skills/commit/a16a3f26027c491208243f699092d17950f1a9b9))

- **ship**: Run-file guards answer tooling when grep itself fails (#495)
  ([#498](https://github.com/Gharib89/skills/pull/498),
  [`d9e1561`](https://github.com/Gharib89/skills/commit/d9e1561a20de8f9d120f196ec31c6c4aa84d7107))

### Features

- **ship**: End host review rounds on a docs-only fix and prove round fixes once
  ([#525](https://github.com/Gharib89/skills/pull/525),
  [`a07bbe6`](https://github.com/Gharib89/skills/commit/a07bbe6bea7b6e6dfd61d0e511ac20248dee07f5))

- **ship**: Grade a reviewer run still live at the ceiling still-running, not infra-error
  ([#497](https://github.com/Gharib89/skills/pull/497),
  [`620c6d5`](https://github.com/Gharib89/skills/commit/620c6d5962e913620a9d4df54b965707cc612683))

- **ship**: Merge on a clean gate in repos that opt in
  ([#501](https://github.com/Gharib89/skills/pull/501),
  [`3d7d8c2`](https://github.com/Gharib89/skills/commit/3d7d8c2d517d558298f6c855dbb8b5a9751dd259))

- **ship**: Move code-review and tdd to f3fc563
  ([#508](https://github.com/Gharib89/skills/pull/508),
  [`16646c2`](https://github.com/Gharib89/skills/commit/16646c25737c6b151cee4433afe2bba838d5a09e))

- **ship**: Read native Codex review as a third on-request reviewer
  ([#513](https://github.com/Gharib89/skills/pull/513),
  [`e7b6598`](https://github.com/Gharib89/skills/commit/e7b65987e2400571eb9fa790e46227b79f323922))

- **ship**: State each rule once and keep unmet criteria verbatim
  ([#515](https://github.com/Gharib89/skills/pull/515),
  [`c923bb4`](https://github.com/Gharib89/skills/commit/c923bb4f493ac13c8883a7328c440e3620ea9f5f))

- **ship**: Write the red and probe evidence lines from the mechanics that ran them
  ([#521](https://github.com/Gharib89/skills/pull/521),
  [`86ba015`](https://github.com/Gharib89/skills/commit/86ba015e963032bd921ccf56a5e1ac93909f9f50))


## v0.18.0 (2026-10-06)

### Features

- **ship**: Self-review gates what reviewers keep catching
  ([#494](https://github.com/Gharib89/skills/pull/494),
  [`5ffa640`](https://github.com/Gharib89/skills/commit/5ffa64042668f61e455249374dc6c221c10ae348))


## v0.17.0 (2026-10-06)

### Features

- **ship**: A durable run record, capped waits and answers from what the profile names
  ([#491](https://github.com/Gharib89/skills/pull/491),
  [`58c0da4`](https://github.com/Gharib89/skills/commit/58c0da48c47220a26296b2da6f122a96346723a3))


## v0.16.6 (2026-10-05)

### Bug Fixes

- **ship**: Gate the retro's mechanical review findings
  ([#482](https://github.com/Gharib89/skills/pull/482),
  [`0c689e8`](https://github.com/Gharib89/skills/commit/0c689e8e731cc86b56bc2c28035c0e609183563a))


## v0.16.5 (2026-10-05)

### Bug Fixes

- **ship**: Count a criterion's throwaway probe PR as verification scaffolding
  ([#481](https://github.com/Gharib89/skills/pull/481),
  [`7f36cf8`](https://github.com/Gharib89/skills/commit/7f36cf8975b244dfd1194f7b319d83a0a1fcaf91))


## v0.16.4 (2026-10-03)

### Bug Fixes

- **ship**: The merge gate closes phase 9 in both lanes
  ([#466](https://github.com/Gharib89/skills/pull/466),
  [`054e04a`](https://github.com/Gharib89/skills/commit/054e04a9e4ee52f45aabd382c04661ddcd962146))


## v0.16.3 (2026-10-03)

### Bug Fixes

- **ship**: Read-pr carries the PR's draft flag on both hosts
  ([#460](https://github.com/Gharib89/skills/pull/460),
  [`9a57b5c`](https://github.com/Gharib89/skills/commit/9a57b5c2d50f0ec34d4a57aa0f58713d3fe6d40f))


## v0.16.2 (2026-10-03)

### Bug Fixes

- **ship**: One argument check and login rule; ADO removes a non-last tag
  ([#457](https://github.com/Gharib89/skills/pull/457),
  [`551b658`](https://github.com/Gharib89/skills/commit/551b65883a800b9025fc291bc1c5013ae39fcca8))


## v0.16.1 (2026-10-03)

### Bug Fixes

- **ship**: Count only the target reviewer's request events in request-review
  ([#455](https://github.com/Gharib89/skills/pull/455),
  [`4cc742e`](https://github.com/Gharib89/skills/commit/4cc742e56438093df629ad0d745c16de773ef1c5))


## v0.16.0 (2026-10-02)

### Features

- **ship**: Section surgery keeps <details> records intact
  ([#450](https://github.com/Gharib89/skills/pull/450),
  [`ff1b53a`](https://github.com/Gharib89/skills/commit/ff1b53a60749387a90836230484359d91e0db6e9))


## v0.15.2 (2026-10-02)

### Documentation

- Name the lock as the skill inventory, group the glossary, and hold hard-wrapped prose to 80
  columns ([#447](https://github.com/Gharib89/skills/pull/447),
  [`97cd290`](https://github.com/Gharib89/skills/commit/97cd290a2908d0b96d1b39b7766bbb667ebf11af))


## v0.15.1 (2026-10-02)

### Bug Fixes

- **ship**: Request-review reads a round in flight as landed
  ([#446](https://github.com/Gharib89/skills/pull/446),
  [`a2146b0`](https://github.com/Gharib89/skills/commit/a2146b09bdee6386e53af3307d1dadf423793c45))


## v0.15.0 (2026-10-02)

### Features

- The lock decides the skill set, and each check and tool message points to the way forward
  ([#443](https://github.com/Gharib89/skills/pull/443),
  [`4af91bb`](https://github.com/Gharib89/skills/commit/4af91bbc5c913ef958d6d591e961133dfc279fbf))


## v0.14.2 (2026-09-30)

### Bug Fixes

- **ship**: Nested adapter calls survive RETURN traps; encoded PR lookup; token off argv; installer
  -f; capped base-fresh log ([#428](https://github.com/Gharib89/skills/pull/428),
  [`7266a14`](https://github.com/Gharib89/skills/commit/7266a14449df72012123eb22b543ced26f596ff4))


## v0.14.1 (2026-09-30)

### Bug Fixes

- **ship**: Let the test suite shorten ci-wait's no-checks grace
  ([#421](https://github.com/Gharib89/skills/pull/421),
  [`2589a67`](https://github.com/Gharib89/skills/commit/2589a67edfe628d0554c5986f13f0aa2b03590af))


## v0.14.0 (2026-09-30)

### Features

- **ship**: Run the suite in parallel and show every refusal
  ([#417](https://github.com/Gharib89/skills/pull/417),
  [`f0510d0`](https://github.com/Gharib89/skills/commit/f0510d0927f3cb7fa0788cb1ca428cf97f6dabed))


## v0.13.0 (2026-09-29)

### Features

- **ship**: Move tdd and triage to d81f3a1 and retire CONTEXT.md for GLOSSARY.md
  ([#411](https://github.com/Gharib89/skills/pull/411),
  [`4ad593d`](https://github.com/Gharib89/skills/commit/4ad593dbc393d2e5d15046018cb9c8c98c9d2573))


## v0.12.0 (2026-09-29)

### Features

- **ship**: Ci-wait and poll-pr grade the latest check run on the expected head
  ([#397](https://github.com/Gharib89/skills/pull/397),
  [`8f4c95d`](https://github.com/Gharib89/skills/commit/8f4c95d8f8188909172ae4774379b27317754329))


## v0.11.6 (2026-09-29)

### Bug Fixes

- **ship**: Run-file open refuses a phase over an earlier one never flipped
  ([#392](https://github.com/Gharib89/skills/pull/392),
  [`4d9c292`](https://github.com/Gharib89/skills/commit/4d9c292d22749b4e445cb23344e11b506d855bbe))


## v0.11.5 (2026-09-28)

### Bug Fixes

- **skills**: Keep every skill self-contained, and gate it
  ([#380](https://github.com/Gharib89/skills/pull/380),
  [`76934a4`](https://github.com/Gharib89/skills/commit/76934a4d031a8f63229b3c79ea93df4d762b016e))


**The renumber to 0.x.** Every entry below predates it and keeps the number it was released under. `ship` was never publicly released, so on 2026-09-28 its version moved from 11.1.4 to 0.11.4: the old major is now the minor. The release run writes new entries above this note, counting on from 0.11.4. See [ADR 0005](https://github.com/Gharib89/skills/blob/main/docs/adr/0005-skills-stay-0x-until-public-release.md).

## v11.1.4 (2026-09-26)

### Bug Fixes

- **skills**: Prompt-audit cleanup of ship and setup-skills prose
  ([`9b84fb6`](https://github.com/Gharib89/skills/commit/9b84fb6742875c2fd9dda08f0010b31f200bb5ab))


## v11.1.3 (2026-09-26)

### Bug Fixes

- **ship**: Merge the base in once the branch is pushed, not rebase
  ([#332](https://github.com/Gharib89/skills/pull/332),
  [`5a3ecc4`](https://github.com/Gharib89/skills/commit/5a3ecc44be5d8a65fb71a753ab2616e33a450c62))


## v11.1.2 (2026-09-26)

### Bug Fixes

- **ship**: A multi-line thread lead ends in the truncation marker
  ([#330](https://github.com/Gharib89/skills/pull/330),
  [`90f2ad5`](https://github.com/Gharib89/skills/commit/90f2ad593da4bc8126e405e34b4941ea03719402))


## v11.1.1 (2026-09-26)

### Bug Fixes

- **ship**: Poll-pr --brief --full lifts a clipped thread lead
  ([#329](https://github.com/Gharib89/skills/pull/329),
  [`5e05ffc`](https://github.com/Gharib89/skills/commit/5e05ffcb565b863cffd97629147acbd85aedcd04))


## v11.1.0 (2026-09-26)

### Features

- **update-skills**: Dated branch, loop-safe installs, retired terms, readable PR body
  ([#325](https://github.com/Gharib89/skills/pull/325),
  [`ab84817`](https://github.com/Gharib89/skills/commit/ab84817eeee5d3ba3dec5c12c4f6f9eb27d66a30))


## v11.0.1 (2026-09-26)

### Bug Fixes

- **ship**: Preflight prunes only a PR's own leftover worktree
  ([#324](https://github.com/Gharib89/skills/pull/324),
  [`6a6ba9b`](https://github.com/Gharib89/skills/commit/6a6ba9b545fb52e7e4b628a246fe6dd995f5d237))


## v11.0.0 (2026-09-26)

### Bug Fixes

- **ship**: Move show-me to ca7c808 ([#319](https://github.com/Gharib89/skills/pull/319),
  [`33061b2`](https://github.com/Gharib89/skills/commit/33061b21f99791ae686b005f3055995d174f2537))

### Breaking Changes

- **ship**: Preflight refuses a consumer whose show-me is still at 6ab9013; refresh it at ca7c808.


## v10.0.0 (2026-09-26)

### Features

- **ship**: Pin composed skills and route Ship defects to the source repo
  ([#316](https://github.com/Gharib89/skills/pull/316),
  [`34c2c35`](https://github.com/Gharib89/skills/commit/34c2c35bb8f03e52a9ffbafb25734eea1f1df46e))

### Breaking Changes

- **ship**: Ship's metadata.composes entries are <owner>/<repo>#<sha>:<skill>; a consumer's composed
  skills are refreshed at those pins.


## v9.0.1 (2026-09-25)

### Refactoring

- **ship**: A prose deletion pass and the deletes-at-least-as-much rule
  ([#311](https://github.com/Gharib89/skills/pull/311),
  [`1794420`](https://github.com/Gharib89/skills/commit/1794420591f3d8cc41efd36d891771f5c51f7a65))


## v9.0.0 (2026-09-25)

### Features

- **ship**: A narrower mechanic rule and a best-effort reviewer loop
  ([#309](https://github.com/Gharib89/skills/pull/309),
  [`38fcbc5`](https://github.com/Gharib89/skills/commit/38fcbc57869018b4602888a3f2be4cf991326189))

### Breaking Changes

- **ship**: Poll-pr drops --free-round, --review-on-push and the never_queued and degraded fields,
  and gains not_reviewed.

- The phase-7 exit vocabulary the Review line and cloud-ship relay changes from converged/degraded
  to reviewed/not reviewed.


## v8.9.0 (2026-09-25)

### Features

- **ship**: A measured 200-line small lane, reviewer probes from preflight, parallel phase 4
  ([#300](https://github.com/Gharib89/skills/pull/300),
  [`38daa3f`](https://github.com/Gharib89/skills/commit/38daa3f04531f861e93e67362f4d26564f23b0f2))


## v8.8.0 (2026-09-25)

### Features

- **ship**: The Review line reports a Claude round's denied-call count
  ([#294](https://github.com/Gharib89/skills/pull/294),
  [`e5246b9`](https://github.com/Gharib89/skills/commit/e5246b91245f2f10f8ac5d42d7abe381bab2a2cd))


## v8.7.0 (2026-09-25)

### Features

- **ship**: Update-issue-body lands a tracker-issue docs-sync target after the merge
  ([#292](https://github.com/Gharib89/skills/pull/292),
  [`bd47489`](https://github.com/Gharib89/skills/commit/bd47489c43b1d696750b3efdab14efa9d6f9450f))


## v8.6.1 (2026-09-25)

### Bug Fixes

- **ship**: Trim the trailing space in host/ado.sh, and gate trailing whitespace
  ([#290](https://github.com/Gharib89/skills/pull/290),
  [`ea07543`](https://github.com/Gharib89/skills/commit/ea075437bb9e8941500c3d1eb59a838f8a9b27ee))


## v8.6.0 (2026-09-24)

### Features

- **ship**: The free-round poll closes on never_queued when the host queued no round
  ([#285](https://github.com/Gharib89/skills/pull/285),
  [`e0f8813`](https://github.com/Gharib89/skills/commit/e0f8813e4882d18ccc13826b4067423d8463a1bc))


## v8.5.3 (2026-09-24)

### Refactoring

- **ship**: The Azure DevOps adapter stops sending substantive
  ([#276](https://github.com/Gharib89/skills/pull/276),
  [`cd993fb`](https://github.com/Gharib89/skills/commit/cd993fbf93dfda05cb79b46797b217edb0616b84))


## v8.5.2 (2026-09-24)

### Documentation

- **ship**: The host contract names every per-host answer
  ([#274](https://github.com/Gharib89/skills/pull/274),
  [`d3247a2`](https://github.com/Gharib89/skills/commit/d3247a298ce489d0ce81ecdd551ed9dfee3d5cd1))


## v8.5.1 (2026-09-24)

### Bug Fixes

- **ship**: Poll-pr grades review rows above the Host seam
  ([#273](https://github.com/Gharib89/skills/pull/273),
  [`7d6791f`](https://github.com/Gharib89/skills/commit/7d6791f0c22823e8074ba6270f8451b561d134c8))


## v8.5.0 (2026-09-24)

### Features

- **ship**: An attended run in a cloud sandbox prepares it as an unattended run does
  ([#263](https://github.com/Gharib89/skills/pull/263),
  [`2101eae`](https://github.com/Gharib89/skills/commit/2101eae4be28ca49caa65dc32e47dcc817a734c4))


## v8.4.4 (2026-09-24)

### Bug Fixes

- **ship**: Read, reply to and resolve review threads in the cloud sandbox through the proxy's REST
  routes ([#261](https://github.com/Gharib89/skills/pull/261),
  [`7aaf64f`](https://github.com/Gharib89/skills/commit/7aaf64f2711fa8299a699f2eaff57cc7e6a67be2))


## v8.4.3 (2026-09-23)

### Bug Fixes

- **ship**: Admit a PR-comment quota notice under poll-pr's since rule
  ([#258](https://github.com/Gharib89/skills/pull/258),
  [`5e9bbbc`](https://github.com/Gharib89/skills/commit/5e9bbbc4d2417b72c1f3821cf00306a3487c1c52))


## v8.4.2 (2026-09-23)

### Bug Fixes

- **ship**: Read the composed show-me rather than invoking it
  ([#253](https://github.com/Gharib89/skills/pull/253),
  [`9f4c5ae`](https://github.com/Gharib89/skills/commit/9f4c5aef7db80237d8bc6706f08641a4454b68ed))


## v8.4.1 (2026-09-23)

### Bug Fixes

- **ship**: Read a Copilot review request back under the name GitHub records it as
  ([#250](https://github.com/Gharib89/skills/pull/250),
  [`35ec22b`](https://github.com/Gharib89/skills/commit/35ec22bd4063ba056c987e84139a86de295f5e32))


## v8.4.0 (2026-09-23)

### Features

- **ship**: Poll-pr and request-review take the Reviewer by name
  ([#248](https://github.com/Gharib89/skills/pull/248),
  [`ef35ae6`](https://github.com/Gharib89/skills/commit/ef35ae6b5805d05becf0aebc3abbe4857abbcbba))


## v8.3.1 (2026-09-23)

### Bug Fixes

- **ship**: A reviewer's quota refusal closes the poll and ends its loop at degraded: blocked
  ([#251](https://github.com/Gharib89/skills/pull/251),
  [`62c29e9`](https://github.com/Gharib89/skills/commit/62c29e90d098ac0e82716d53f0f45ed073db88df))


## v8.3.0 (2026-09-23)

### Features

- **ship**: A Host fake at the host seam, and the pass-through mechanics get behavioural tests
  ([#246](https://github.com/Gharib89/skills/pull/246),
  [`89cba9d`](https://github.com/Gharib89/skills/commit/89cba9dbcd97e064d16297927bc7fecfec09a676))


## v8.2.1 (2026-09-23)

### Documentation

- **ship**: Agent-facing is the one rule, subagents write their own Report files, derived-copy
  pointer rule ([#245](https://github.com/Gharib89/skills/pull/245),
  [`c3b3e89`](https://github.com/Gharib89/skills/commit/c3b3e8907859cc343442f05aad3dc4783f61b722))


## v8.2.0 (2026-09-23)

### Features

- **ship**: Comment-issue posts a comment on an existing issue on both hosts
  ([#243](https://github.com/Gharib89/skills/pull/243),
  [`e0d439f`](https://github.com/Gharib89/skills/commit/e0d439f3e73b38b5e27e6210ded4762174faac4f))


## v8.1.1 (2026-09-23)

### Bug Fixes

- **ship**: Drop dated prompt patterns found by a prompt audit
  ([`c4e475e`](https://github.com/Gharib89/skills/commit/c4e475e2d89af2ad7b81d594990ad695460c3522))


## v8.1.0 (2026-09-21)

### Features

- **ship**: The PR body states the door and the blast radius
  ([#237](https://github.com/Gharib89/skills/pull/237),
  [`5f6a870`](https://github.com/Gharib89/skills/commit/5f6a87093c08e94667b719c5a628d9375cdbeed4))


## v8.0.0 (2026-09-21)

### Bug Fixes

- **ship**: Re-stamp a skipped phase's reason and bound the role lookup
  ([#230](https://github.com/Gharib89/skills/pull/230),
  [`d25170f`](https://github.com/Gharib89/skills/commit/d25170f861ad56bb96bfe6e574fd6924cf52645c))

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
