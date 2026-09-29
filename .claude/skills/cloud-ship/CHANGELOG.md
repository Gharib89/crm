# CHANGELOG

Every entry here is written by the release run on main, which grades the bump
from the squash subject's Conventional-Commit type and cuts one entry per
released version. See
[docs/adr/0003-version-and-changelog-cut-on-merge.md](https://github.com/Gharib89/skills/blob/main/docs/adr/0003-version-and-changelog-cut-on-merge.md).

<!-- version list -->

**The renumber to 0.x.** Every entry below predates it and keeps the number it was released under. `cloud-ship` was never publicly released, so on 2026-09-28 its version moved from 2.0.0 to 0.2.0: the old major is now the minor. The release run writes new entries above this note, counting on from 0.2.0. See [ADR 0005](https://github.com/Gharib89/skills/blob/main/docs/adr/0005-skills-stay-0x-until-public-release.md).

## v2.0.0 (2026-09-25)

### Features

- **ship**: A narrower mechanic rule and a best-effort reviewer loop
  ([#309](https://github.com/Gharib89/skills/pull/309),
  [`38fcbc5`](https://github.com/Gharib89/skills/commit/38fcbc57869018b4602888a3f2be4cf991326189))

### Breaking Changes

- **ship**: Poll-pr drops --free-round, --review-on-push and the never_queued and degraded fields,
  and gains not_reviewed.

- The phase-7 exit vocabulary the Review line and cloud-ship relay changes from converged/degraded
  to reviewed/not reviewed.


## v1.1.0 (2026-09-21)

### Features

- **ci**: Cut the version bump and per-skill changelog on merge
  ([#224](https://github.com/Gharib89/skills/pull/224),
  [`612baa8`](https://github.com/Gharib89/skills/commit/612baa85c93acbe697872ca324396722afe14ca2))
