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
  (OAuth) Dataverse, resolving from a **`D365_*` env-seeded ephemeral profile under a
  temporary `CRM_HOME` — never the user's real `CRM_HOME` / active profile**. This is a
  hard safety invariant (see Safety invariants): the suite creates and deletes entities,
  publishes customizations, and imports solutions; pointed at the wrong org (e.g. a
  read-only gov tenant) it is destructive.

## Safety invariants

The plan MUST preserve these — they are not optional polish:

- The suite resolves credentials **only** from the env-seeded throwaway profile the
  fixture writes into a `tmp_path` `CRM_HOME`. It never reads or mutates the developer's
  real `CRM_HOME` or active profile.
- The suite runs **only** when opt-in is explicit. Require `D365_E2E=1` in addition to the
  `D365_*` credential vars; absent it, the live fixtures hard-skip. This prevents an
  accidental `pytest -m e2e` against whatever creds happen to be in the environment.
- Recommended additional rail: the live fixture hard-fails (not skips) if `D365_URL`'s
  host matches a known-production pattern, forcing a deliberate allow-list for the test
  org. (Implementation detail for the plan; the `D365_E2E=1` gate is the minimum.)

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
crm/tests/
  test_e2e_coverage_gate.py  # the gate — OFFLINE, NOT under e2e/ (see below)
  e2e/
    __init__.py
    conftest.py          # live-profile + backend fixtures (lifted from test_full_e2e.py),
                         # scaffolding fixtures, capability markers, e2e auto-marking
    coverage.py          # @covers decorator, COVERED registry, LOCAL_COMMANDS, E2E_SKIP,
                         # walk_commands(); imported by the gate
    test_connection.py   # connection whoami|test|doctor|status, top-level doctor
    test_entity.py
    test_query.py
    test_metadata.py     # metadata read verbs + create/delete-entity, add/update/delete-attribute
    test_optionset.py    # metadata {create,update,delete,get,list}-optionset (subs of metadata, not a top-level group)
    test_relationship.py # metadata create-one-to-many|many-to-many|update|delete-relationship (subs of metadata)
    test_apply.py        # apply (apply_spec — D365-touching)
    test_solution.py     # incl. service-document classification
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
    test_describe.py     # describe (D365-touching: reads a live record)
    test_journeys.py     # complement: realistic multi-step user journeys
```

**File layout is organizational, not the coverage list.** The authoritative set of
commands needing coverage is whatever `walk_commands(cli)` returns at runtime, minus
`LOCAL_COMMANDS` and `E2E_SKIP` — *not* this file list. The plan-writer must let the gate
(rollout step 3) enumerate the real gap rather than trusting this enumeration, which is
known-incomplete (e.g. `apply`, `describe`, top-level `doctor`, `batch service-document`
were missed in the first cut and only surfaced by walking the tree). `optionset` and
`relationship` are **not** top-level groups — they are subcommands of `metadata`; their
files test a slice of that group.

The 13 live tests in `crm/tests/test_full_e2e.py` migrate into the per-group files.
The **two offline CLI-smoke tests** currently living in that file
(`TestDeleteEntityCli`, `TestAddAttributeBooleanDefaultParsing`) are unit tests — they
relocate to a unit test module so `e2e/` is purely live. `test_full_e2e.py` is then
removed.

The gate lives at `crm/tests/test_e2e_coverage_gate.py`, **outside** `e2e/`, so the
`conftest.py` auto-marking hook can stay uniform ("everything under `e2e/` is `e2e`")
with zero special-casing, while the gate itself runs offline in the default CI.

### 2. Markers and the on-demand switch (`pyproject.toml`)

Registered markers under `[tool.pytest.ini_options]`:

- `e2e` — auto-applied to every item collected under `e2e/` via a
  `pytest_collection_modifyitems` hook in `e2e/conftest.py`. The coverage gate is *not*
  under `e2e/`, so it is never marked and runs in the fast offline CI with no exemption
  logic.
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

`addopts = -m 'not e2e'` keeps plain `pytest` fast and offline. No
`[tool.pytest.ini_options]` exists today and `build.yml` runs plain `pytest -q`, so that
offline CI is unaffected. `pytest -m e2e` is the full on-demand live run (last `-m` wins,
so it overrides the default filter).

**Documented gotchas** (go in TEST.md):

- `pytest crm/tests/e2e/test_entity.py` alone silently deselects everything (the default
  `-m 'not e2e'` filter still applies) and exits 5 ("no tests ran"). Use
  `pytest -m e2e crm/tests/e2e/test_entity.py`.
- `pytest -m slow` overrides the default filter and *will* select slow e2e tests. Intended
  forms are `pytest -m e2e` (all live) and `pytest -m "e2e and not slow"` (quick live pass).

### 3. Fixtures (`e2e/conftest.py`)

Lifted from the current inline implementation (already target-agnostic — reads
`D365_AUTH`):

- `live_profile` (**session**-scoped autouse) — seeds a temporary `CRM_HOME` profile from
  `D365_*` env, activates it. The CLI resolves from this throwaway profile, never the
  developer's real `CRM_HOME`. Hard-skips unless `D365_E2E=1` and the credential vars are
  present (Safety invariants).
- `backend` — `D365Backend` built from resolved credentials.

New shared fixtures:

- `cli` — subprocess runner helper (the existing `_run` / `_resolve_cli` logic, shared).
- `unique` — uuid-derived suffix for collision-free entity/solution names.
- `ephemeral_entity` (**session**-scoped) — creates one uniquely-named custom entity,
  yields its logical name, deletes it once. Backs attribute / relationship / form /
  ribbon tests across multiple modules; session scope avoids paying the slow on-prem
  create+publish cycle 4–5 times. Drop to module scope only for the narrow set of tests
  that mutate the entity in conflicting ways.
- `ephemeral_solution` (module-scoped) — creates a throwaway publisher + unmanaged
  solution, yields, uninstalls. Backs solution-component tests.
- `target` — resolves `cloud`/`onprem` from the active profile for capability markers.

The read-only **diagnostics** (`connection whoami|test|doctor|status`, top-level
`doctor`) stay **covered** by live tests, not dumped into `LOCAL_COMMANDS` — they are
D365-touching (they call WhoAmI / metadata) and are the cheapest live tests, already
green today.

**Teardown discipline:** every mutating test registers `request.addfinalizer` cleanup,
guarded best-effort, and **`pytest.xfail`s on cleanup failure** so leftover artifacts are
surfaced, never silently hidden (matches the current pattern in `TestSpecDMetadataWriteLive`).

### 4. The coverage gate (enforcement engine)

`coverage.py`:

- `@covers(*paths)` — decorator stamping a test; appends **one or more** declared command
  paths to a module-level `COVERED` set at import time. Multiple paths are required, not
  optional: one lifecycle test legitimately owns several verbs
  (`@covers("entity create", "entity get", "entity delete")`). Forcing 1:1 would split
  tests artificially.
- `LOCAL_COMMANDS` — frozenset of non-D365 leaf paths (`profile`, `session`, `skill`,
  `self-update`, `repl`, `scaffold`). Out of scope. Does **not** include the
  `connection`/`doctor` diagnostics — those stay covered.
- `E2E_SKIP` — `dict[command_path, reason]` for D365-touching verbs that genuinely cannot
  be auto-tested yet (see Constraints). The gate forces the reason to be written down.
- `walk_commands(cli)` — returns the set of full leaf command paths
  (`"metadata add-attribute"`, `"workflow activate"`, …).

  **Blocker — lazy group.** `crm.cli.cli` is a `_LazyJsonAwareGroup`: `group.commands` is
  **empty until subcommands are loaded**, so a naive `.commands` recursion returns **0
  leaves and the gate passes vacuously** — silently asserting full coverage over an empty
  set. The walker MUST drive the lazy loader explicitly with a `click.Context`:

  ```python
  def walk_commands(cmd, ctx, prefix=()):
      for name in cmd.list_commands(ctx):          # triggers lazy load
          sub = cmd.get_command(ctx, name)         # materializes the command
          path = (*prefix, name)
          if isinstance(sub, click.Group):
              yield from walk_commands(sub, ctx, path)
          else:
              yield " ".join(path)
  ```

  Verified against the repo: naive `.commands` walk → 0 leaves; `list_commands` +
  `get_command` walk → 29 groups / 140 leaves.

`test_e2e_coverage_gate.py`:

```python
walked   = set(walk_commands(cli, ctx))
assert len(walked) > 100, (                      # sanity floor — the keystone rail
    f"walk_commands returned only {len(walked)} leaves; lazy loading likely broke. "
    "Refusing to run a vacuous gate.")
expected = walked - LOCAL_COMMANDS - set(E2E_SKIP)
missing  = expected - COVERED
assert not missing, f"D365 commands lacking e2e coverage: {sorted(missing)}"
```

The **sanity floor is the single point of failure of the whole enforcement story**: any
future lazy-loading change that empties the walk would otherwise make `expected = ∅` and
pass the gate with zero coverage. `assert len(walked) > 100` makes that failure loud.

Import mechanics the plan must honour:

- The gate **auto-discovers** e2e modules with `pkgutil.walk_packages` over
  `crm.tests.e2e` and imports each to populate `COVERED` — never a hand-kept module list,
  or a new `@covers` test file is silently uncounted.
- e2e test modules MUST be **side-effect-free at import**: no module-level `skipif` that
  touches the live env, no connection at import. The gate imports them offline, and the
  offline CI also collects them (deselected by `-m 'not e2e'`), so import must be clean
  regardless.
- The gate only introspects — no live server. It is **unmarked** and runs in default
  offline CI. A new command with no test and no `E2E_SKIP` entry fails fast CI
  immediately. This is the cheap regression net; live execution stays on-demand.

A staleness assertion guards the reverse direction: `E2E_SKIP` keys and `LOCAL_COMMANDS`
entries that no longer exist in `walked` fail the gate (no dangling skips).

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

(`self-update` is **not** here — it is in `LOCAL_COMMANDS`, the single source of truth for
out-of-scope verbs.)

### 6. CI, docs, commit hygiene

- `.github/workflows/e2e.yml` — `workflow_dispatch` trigger (optional nightly `schedule`).
  Reads `D365_*` + `D365_E2E=1` from repo **secrets** (a disposable cloud OAuth test
  tenant), installs `pip install -e .[dev]`, runs `pytest -m e2e`. On-prem crmworx is
  documented as a **local-only** run (not reachable from cloud runners).
  - **`concurrency: { group: e2e-live, cancel-in-progress: false }`** — two simultaneous
    dispatches against one tenant collide on org-level ops (`publish-all`, solution
    import) even though `unique` handles per-record name collisions. Serialize them.
  - Public-repo note: `workflow_dispatch`/`schedule` do **not** expose secrets to fork
    PRs, so secrets are safe here.
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

### Coverage semantics across targets

The gate counts a command "covered" if a `@covers` test exists for it, **even if that
test `skip`s on the target actually run** (`requires_cloud` / `requires_onprem`).
Accepted limitation. State it explicitly: **full coverage = the union of an on-prem run
and a cloud run.** The `TEST.md` run record must note **which target** produced each
result, so a green run isn't misread as covering target-specific verbs it skipped.

### Runtime budget

100+ live tests, many `slow` (metadata create+publish, solution async import/export). The
first full run records **per-group timings** in `TEST.md`. Document the quick pass
`pytest -m "e2e and not slow"` for fast feedback, with `pytest -m e2e` reserved for the
pre-release / nightly full sweep.

## Rollout order (for the implementation plan)

1. Scaffold `e2e/` package, move fixtures into `conftest.py` (session-scoped
   `live_profile` with the `D365_E2E=1` safety gate), add markers + `addopts`, add the
   `pytest_collection_modifyitems` auto-marking hook.
2. Migrate the existing 13 live tests into per-group files; relocate the 2 offline tests;
   remove `test_full_e2e.py`.
3. Add `coverage.py` (lazy `walk_commands` via `list_commands`/`get_command`,
   `pkgutil` auto-discovery, multi-path `@covers`) + `crm/tests/test_e2e_coverage_gate.py`
   with the **`len(walked) > 100` sanity floor**, `LOCAL_COMMANDS` seeded, empty
   `E2E_SKIP`. Run it and let it **enumerate the authoritative gap** — do not trust the
   file layout list.
4. Fill in per-group e2e tests until the gate passes (or the verb lands in `E2E_SKIP` with
   a reason). Classify `apply`, `describe`, `doctor`, `service-document` as found by the walk.
5. Add the CI workflow (with `concurrency: e2e-live`), docs, PR-template line, CLAUDE.md rule.
6. Run `pytest -m e2e` live once per target; record per-group timings + which target in `TEST.md`.
```
