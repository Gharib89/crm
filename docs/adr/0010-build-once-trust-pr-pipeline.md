# CI/CD: build binaries once, trust the PR as the gate

The CI/CD pipeline is restructured so that the PR is the single enforced gate and PyInstaller binaries are built exactly once, at the release tag. Previously a single feature PR that shipped a release ran PyInstaller **8 times** (PR build, push-to-main build, push-of-release-commit build, release-tag build — each across two OSes) and pytest ~6–8 times, because `build.yml` ran on both `pull_request` and `push: main`, the `chore(release)` commit re-triggered everything, and `release.yml` rebuilt from scratch.

Decision:

- **`build.yml` (renamed `ci.yml`) runs on `pull_request` only**, split into parallel `lint` (pyright, once, ubuntu), `test` (pytest, both OS), and `package` (PyInstaller + smoke, both OS) jobs, with `concurrency` cancel-in-progress so stale PR pushes are abandoned.
- **No build/test runs on `push: main`.** The PR is trusted because a branch ruleset now *enforces* it: PR required to merge, the `ci` jobs (`lint`, `test`, `package`) + `docs` required as status checks, direct and force pushes to main blocked.
- **`release.yml` (tag `v*`) is the only binary build**, keeping a `pytest` gate before publishing since the tagged commit is the first time that exact SHA hits CI.
- **`semantic-release.yml` skips its own no-op re-run** on the `chore(release)` commit via `if: !startsWith(github.event.head_commit.message, 'chore(release):')`.

## Considered Options

- **Re-test on push to main (status quo).** Rejected: for a single-maintainer repo with a low parallel-merge rate, the squash-drift risk a main-push re-test guards against is negligible, and it triples binary builds.
- **A merge queue** to eliminate squash-drift properly. Rejected as overkill at this scale.
- **`[skip ci]` in the PSR commit message** to suppress the release-commit re-trigger. Rejected: the tag points at that commit, so `[skip ci]` would also suppress the tag-triggered `release.yml` and break releases. A job-level `if` guard is surgical instead.
- **Keep PyInstaller off PRs** (build only at release). Rejected: packaging breaks (broken `.spec`, missing `hiddenimports`) are recurring, and catching them only at the release tag means a failed build *after* PSR has already pushed the tag (delete-and-recut). The PR `package` job catches them pre-merge.

## Consequences

- The 7-day "latest main" onedir artifact `build.yml` uploaded on push is gone; only tagged release binaries are published.
- Removing the main-push gate is only safe *because* the ruleset enforces the PR — the two changes are a package and must ship together.
- `ci.yml` is a **new** workflow file, so it does not run on the PR that introduces it (GitHub runs only `pull_request` workflows already on the default branch). That PR is validated by the local gate (`pytest`, `pyright`, `mkdocs --strict`, `pyinstaller` + smoke); `ci.yml` itself is first exercised on the next `pull_request` after it lands on main, and the enforcing ruleset is configured once those check names are known.
