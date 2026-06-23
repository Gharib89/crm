# Reset the release version to 1.0.0 and gate minor/major bumps behind labels

Before its first public release, `crm` resets its version from the inflated
`v4.31.x` line down to `1.0.0`, and installs a permanent guard so the minor and
major digits only move on intentional, labelled bumps.

The 4.x number is misleading: it reads as a long-lived, mature product, but `crm`
has no public install base and has never been released to end users. The
inflation was not a versioning-config bug — `python-semantic-release` (PSR) is
configured for correct semver (`feat:` → minor, `fix:`/`perf:` → patch). It came
from labelling *every* PR `feat:`, so each issue bumped the minor digit; ~30
minors accumulated during development. Going backwards is safe **only while the
install base is empty** — this window closes the moment anyone installs a 4.x
build, so the reset happens now.

## How the reset works

PSR picks the next version from the **highest-semver** existing tag, not the most
recent one (verified against PSR's `version/algorithm.html`: it sorts all tags
descending by semver and takes the latest non-prerelease). So tagging a fresh
`v1.0.0` on top of `v4.31.x` does nothing — PSR still sees 4.31.x as latest and
bumps to 4.32.0. To land on 1.0.0 the **highest visible tag must be `< 1.0.0`**,
which requires deleting the existing `vX.Y.Z` tags.

The reset is therefore:

1. Delete **all** `v*` git tags, local and on `origin` (refs only — no commit is
   rewritten, so main's history stays append-only and open PRs are not
   invalidated; no force-push).
2. With zero tags and a history full of `feat:`/`fix:` commits,
   `allow_zero_version = false` (in `pyproject.toml`) **forces PSR to auto-cut
   exactly `v1.0.0`** on the next push to `main`: it writes the version files,
   inserts `## v1.0.0` at the changelog marker, commits `chore(release): v1.0.0`,
   and pushes the tag via `RELEASE_PAT`, which fires `release.yml` (binaries, R2,
   GitHub release).
3. Delete the now-dangling old GitHub Releases and R2 artifacts (cosmetic).

PSR cutting `v1.0.0` itself means there is **no manual `git tag` step** — a manual
`v1.0.0` would collide with PSR's auto-cut and double-release. Ordering is
load-bearing: the tags must be gone **before** the triggering push, or PSR sees
`v4.31.x` and bumps `4.32.0` instead.

`crm/core/update.py` resolves "latest" from a single `<base_url>/latest/VERSION`
file, not a tag-sorted list, so deleting tags/Releases/R2 does not break
self-update; an already-installed 4.x binary merely considers itself newest
(cosmetic, install base empty).

## The permanent bump policy

A **major** bump must be a deliberate maintainer action, never an agent's
auto-bump — so only the major digit is label-gated:

- The default bump is **patch** (`fix:`/`perf:`/`docs:`/`chore:`/…).
- A `feat:` PR bumps **minor** with **no label required**.
- A breaking PR (`!` or `BREAKING CHANGE`) requires the maintainer-applied
  **`major`** label.

A `bump-guard` workflow (`.github/workflows/bump-guard.yml`) validates the PR
title on every PR and fails it when the title implies a **major** bump without
the `major` label. The check logic lives in `scripts/check_bump_label.py` (a
testable seam, unit-tested in `crm/tests/test_check_bump_label.py`). To make the
squash subject the guard validated be exactly what reaches PSR, the repo's
`squash_merge_commit_title` is set to `PR_TITLE`. Standard semver semantics are
kept in PSR — the gate lives at PR time, not in a `commit_parser` remap.

> **Update (#500):** the original policy also gated `feat:` behind a **`minor`**
> label. That gate was removed — it stalled AFK/cloud agents (their `feat:` PRs
> failed CI until a human applied `minor`) while adding no real protection:
> `feat:` → minor is already correct semver, and guarding against minor-digit
> inflation is a commit-type-discipline matter (use `feat:` only for real
> capability — see CLAUDE.md "Bump discipline"), not a labelling one. Only the
> **`major`** gate — the bump that genuinely needs maintainer intent — is kept,
> and the now-inert `minor` label is retired.

## Considered options

- **Change `tag_format` so old tags stop matching** (non-destructive: PSR sees no
  matching history → forced to 1.0.0). Rejected as primary: it leaves mixed tag
  formats, and `release.yml` / `check_tag_version.py` / the R2 scripts all assume
  the `v{version}` format. The destructive tag deletion is cleaner and keeps one
  tag format.
- **Manually tag `v1.0.0`.** Rejected: collides with PSR's auto-cut (double
  release). Let PSR cut it from the no-tags state.
- **Remap bump semantics inside PSR (`commit_parser`).** Rejected: keeps the gate
  far from where authors see it. A PR-time title/label check is visible and
  enforceable as a required status check.
- **Wipe the v0.x→v4.x changelog entirely.** Rejected: erases real development
  history. The body is instead archived under a `## Pre-1.0 development history`
  heading below the changelog marker, preserving the record while letting PSR
  insert `## v1.0.0` above it.

## Consequences

- The reset (tag/Release/R2 deletion, ruleset change, label creation, squash-title
  flip) is irreversible, depends on `RELEASE_PAT` + Cloudflare/R2 credentials, and
  is a one-shot launch event — it is executed by a maintainer under supervision,
  not delegated.
- After the reset every merged PR bumps from `1.x`: a `fix:` → `1.0.1`, a `feat:`
  → `1.1.0` (no label needed), a `major`-labelled breaking change → `2.0.0`.
- The changelog gains a permanent `## Pre-1.0 development history` section; new
  releases stack above it.
- `bump-guard` is **not** path-filtered, so it can become a required status check
  on `main` (done as part of the reset).
