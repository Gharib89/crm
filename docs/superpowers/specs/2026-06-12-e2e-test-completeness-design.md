# E2E Test Completeness — Design

**Date:** 2026-06-12
**Status:** Approved (design); implementation pending

## Problem

The live end-to-end suite is shallow and ad-hoc. Today `crm/tests/test_full_e2e.py`
holds ~13 live tests covering only `connection`, `entity`, `query`, `metadata`,
`optionset`, `relationship`, `solution` (export), and `plugin` (image). The CLI
exposes **29 command groups / 140+ subcommands** — the long tail (`workflow`,
`security`, `data`, `form`, `ribbon`, `view`, `webresource`, `translation`, `app`,
`action`, `sla`, `async`, `batch`) has **no live coverage**.

Three gaps:

1. **Coverage** — most D365-touching verbs are never exercised against a real server,
   so regressions that only manifest over the wire (header casing, `@odata.bind`
   navigation-property casing, async job polling, on-prem vs cloud divergence) escape
   the offline `requests_mock` suite.
2. **Enforcement** — nothing forces a new command or behaviour change to ship with an
   e2e test. Coverage decays silently.
3. **Operability** — there is no `e2e` marker and no separate run target. Live tests
   are gated by an ad-hoc `_have_live_env()` `skipif`, and CI runs `pytest -q` (offline
   only). The full suite is slow by nature (metadata create/publish, solution async
   import/export) and must be *opt-in*, but it must also be *reliably available* to gate
   releases.

## Goals

- A live e2e test for **every D365-touching CLI verb**, each owning its
  `create → verify → cleanup` lifecycle.
- **Self-maintaining enforcement**: a new command with no e2e test (or no documented,
  reasoned skip) fails CI.
- **On-demand execution**: `pytest -m e2e` locally; a manual `workflow_dispatch` GitHub
  Action for cloud runs. Default unit CI stays fast and offline.
- **Target-agnostic**: the same suite runs against on-prem (NTLM) crmworx *or* cloud
  (OAuth) Dataverse, resolving from whatever profile is active — exactly as the current
  fixtures already do.

## Non-goals

- Live coverage of purely-local / meta commands (`profile`, `session`, `skill`,
  `self-update`, `repl`, `scaffold`). These touch no Web API and are already covered by
  unit tests. They are explicitly out of scope (`LOCAL_COMMANDS`).
- Running the full live suite in the default PR CI. It is on-demand only.
- A test `.dll` build pipeline for plugin-assembly registration in this iteration (see
  Constraints).

## Architecture

### 1. Directory layout — split the monolith

```
crm/tests/e2e/
  __init__.py
  conftest.py            # live-profile + backend fixtures (lifted from test_full_e2e.py),
                         # scaffolding fixtures, capability markers, e2e auto-marking
  coverage.py            # @covers decorator, COVERED registry, LOCAL_COMMANDS, E2E_SKIP,
                         # Click-tree walker
  test_coverage_gate.py  # the gate — introspection only, runs OFFLINE in normal CI
  test_connection.py
  test_entity.py
  test_query.py
  test_metadata.py
  test_optionset.py
  test_relationship.py
  test_solution.py
  test_workflow.py
  test_plugin.py
  test_security.py
  test_data.py
  test_form.py
  test_ribbon.py
  test_view.py
  test_webresource.py
  test_translation.py
  test_app.py
  test_action.py
  test_sla.py
  test_async.py
  test_batch.py
  test_journeys.py       # complement: realistic multi-step user journeys
```

The 13 live tests in `crm/tests/test_full_e2e.py` migrate into the per-group files.
The **two offline CLI-smoke tests** currently living in that file
(`TestDeleteEntityCli`, `TestAddAttributeBooleanDefaultParsing`) are unit tests — they
relocate to a unit test module so `e2e/` is purely live. `test_full_e2e.py` is then
removed.

### 2. Markers and the on-demand switch (`pyproject.toml`)

Registered markers under `[tool.pytest.ini_options]`:

- `e2e` — auto-applied to every item collected under `e2e/` via a
  `pytest_collection_modifyitems` hook in `e2e/conftest.py`. The coverage gate is the
  one exception: it is **not** marked `e2e` so it runs in the fast offline CI.
- `requires_cloud` / `requires_onprem` — capability gating. A `target` fixture reads the
  active profile's `auth_scheme` (`oauth` → cloud, `ntlm` → on-prem) and skips tests
  marked for the other target. This handles known divergences (e.g. on-prem v9.1
  `EntityDefinitions` rejects server-side `$top`; some features are cloud-only).
- `slow` — metadata/solution/async operations, for optional `-m "e2e and not slow"`.

```toml
[tool.pytest.ini_options]
markers = [
  "e2e: live test requiring a reachable D365 server",
  "requires_cloud: only runs against an OAuth/Dataverse target",
  "requires_onprem: only runs against an NTLM/on-prem target",
  "slow: long-running (metadata publish, solution async)",
]
addopts = "-m 'not e2e'"
```

`addopts = -m 'not e2e'` keeps plain `pytest` fast and offline (current CI behaviour
preserved). `pytest -m e2e` is the full on-demand live run.

### 3. Fixtures (`e2e/conftest.py`)

Lifted from the current inline implementation (already target-agnostic — reads
`D365_AUTH`):

- `live_profile` (module/session autouse) — seeds a temporary `CRM_HOME` profile from
  `D365_*` env, activates it. The CLI resolves from the profile, not env.
- `backend` — `D365Backend` built from resolved credentials.

New shared fixtures:

- `cli` — subprocess runner helper (the existing `_run` / `_resolve_cli` logic, shared).
- `unique` — uuid-derived suffix for collision-free entity/solution names.
- `ephemeral_entity` (module-scoped) — creates one custom entity, yields its logical
  name, deletes it. Backs attribute / relationship / form / ribbon tests.
- `ephemeral_solution` (module-scoped) — creates a throwaway publisher + unmanaged
  solution, yields, uninstalls. Backs solution-component tests.
- `target` — resolves `cloud`/`onprem` from the active profile for capability markers.

**Teardown discipline:** every mutating test registers `request.addfinalizer` cleanup,
guarded best-effort, and **`pytest.xfail`s on cleanup failure** so leftover artifacts are
surfaced, never silently hidden (matches the current pattern in `TestSpecDMetadataWriteLive`).

### 4. The coverage gate (enforcement engine)

`coverage.py`:

- `@covers("entity create")` — decorator stamping a test; appends each declared command
  path to a module-level `COVERED` set at import time.
- `LOCAL_COMMANDS` — frozenset of non-D365 leaf paths (profile/session/skill/self-update/
  repl/scaffold and the read-only diagnostics that don't merit a live test). Out of scope.
- `E2E_SKIP` — `dict[command_path, reason]` for D365-touching verbs that genuinely cannot
  be auto-tested yet (see Constraints). The gate forces the reason to be written down.
- `walk_commands(cli)` — recursively walks the `crm.cli.cli` Click group, returning the
  set of full leaf command paths (`"metadata add-attribute"`, `"workflow activate"`, …).

`test_coverage_gate.py`:

```
expected = walk_commands(cli) - LOCAL_COMMANDS - set(E2E_SKIP)
missing  = expected - COVERED
assert not missing, f"D365 commands lacking e2e coverage: {sorted(missing)}"
```

The gate **imports the e2e test modules to populate `COVERED`** but only introspects —
no live server needed. It is therefore **unmarked** and runs in the default offline CI.
A new command with no test and no `E2E_SKIP` entry fails fast CI immediately. This is the
cheap regression net; the actual live execution stays on-demand.

A second assertion guards staleness: `E2E_SKIP` keys and `LOCAL_COMMANDS` entries that no
longer exist in the Click tree fail the gate (no dangling skips).

### 5. Realistic constraints — the tail of "all verbs"

Some D365-touching verbs cannot be cleanly auto-e2e'd in this iteration. Each gets a
documented `E2E_SKIP` entry (reason enforced by the gate) and, where warranted, a tracking
issue:

- `plugin register-assembly` / `register-step` — need a prebuilt **signed test `.dll`**.
  The current plugin e2e sidesteps this by attaching an image to an *existing* step.
  Decision: **skip-with-reason + tracking issue** this round; do not build a `.dll`
  pipeline now.
- `solution uninstall` / `stage-and-upgrade` — require a managed solution installed first;
  heavy and org-stateful. Covered partially via the `ephemeral_solution` lifecycle where
  feasible; the managed-upgrade path is skip-with-reason.
- `security assign-role` — needs a throwaway user, not creatable on all orgs. Test against
  an existing user where possible; else skip-with-reason.
- `workflow run` — async side effects on real records; assert dispatch only, or
  skip-with-reason.
- `self-update` — replaces the binary; `LOCAL_COMMANDS`/skip.

### 6. CI, docs, commit hygiene

- `.github/workflows/e2e.yml` — `workflow_dispatch` trigger (optional nightly `schedule`).
  Reads `D365_*` from repo **secrets** (a disposable cloud OAuth test tenant), installs
  `pip install -e .[dev]`, runs `pytest -m e2e`. On-prem crmworx is documented as a
  **local-only** run (not reachable from cloud runners).
- Docs in the same change (per the repo's "keep docs in sync" rule):
  - Rewrite the e2e section of `crm/tests/TEST.md` to describe the new structure, marker,
    and gate.
  - CONTRIBUTING note + **PR-template line**: "new D365-touching command → add an e2e test
    with `@covers`, or add an `E2E_SKIP` entry with a reason; the coverage gate enforces this."
  - Add the gate rule to project `CLAUDE.md` "Keep docs in sync with code".
- **Commit type** `test:` / `ci:` so `python-semantic-release` does not bump the version.
- Work happens in a **git worktree** on a fresh branch (`feat/e2e-completeness` or
  `test/e2e-completeness`) per branch discipline. Verify in-worktree with
  `PYTHONPATH=$WT <main-venv>/bin/python -m pytest`. Only **Contoso** placeholders in any
  committed artifact — no real org names or GUIDs.

## Testing the test infra

- The coverage gate runs offline in CI and is itself the meta-test.
- The full `pytest -m e2e` run is executed once against a live target (crmworx on-prem
  and/or cloud) before the PR merges, per the "e2e before PR" discipline, and the result
  recorded in `TEST.md`.

## Rollout order (for the implementation plan)

1. Scaffold `e2e/` package, move fixtures into `conftest.py`, add markers + `addopts`.
2. Migrate the existing 13 live tests into per-group files; relocate the 2 offline tests.
3. Add `coverage.py` + `test_coverage_gate.py` with `LOCAL_COMMANDS` seeded and an empty
   `E2E_SKIP`; let it fail and enumerate the real gap.
4. Fill in per-group e2e tests until the gate passes (or the verb lands in `E2E_SKIP` with
   a reason).
5. Add the CI workflow, docs, PR-template line, CLAUDE.md rule.
6. Run `pytest -m e2e` live once; record results.
```
