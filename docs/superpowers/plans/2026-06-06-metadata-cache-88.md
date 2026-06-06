# Plan: persistent on-disk schema/metadata cache (#88)

Opt-in persistent read-only cache of entity definitions, per profile, for fast
one-shot agent calls. Default OFF and byte-identical. Source: issue #88.

## Design decisions (controller-owned)

- **Cache content**: the minimal `[{logical, set_name}]` list produced by
  `crm/core/metadata.py::list_entity_definitions` (the 2-field reader). The
  richer 5-field `list_entities` is NOT cached (spec pins the 2-field list).
- **Cache file**: `<CRM_HOME or ~/.crm>/cache/<profile-name>/entitydefs.json`,
  keyed by `backend.profile.name`. Envelope:
  `{"url", "api_version", "cached_at", "definitions": [...]}`.
- **Miss conditions** (any → treat as miss): file absent, JSON corrupt/malformed,
  `url` mismatch, `api_version` mismatch, expired (`now - cached_at > TTL`).
- **TTL backstop**: `TTL_SECONDS = 900` (15 min).
- **Read path served from cache**: `crm metadata entities` (in cache mode) and
  REPL name completion. In cache mode `metadata entities` returns the 2-field
  cached rows (LogicalName, EntitySetName) + `meta.cache`. Default (no flag) is
  unchanged 5-field output, byte-identical, no `meta.cache`.
- **Opt-in flags** (global, on the root group):
  - `--cache-metadata` (default OFF; sticky; env `CRM_CACHE_METADATA`).
  - `--refresh-metadata` (force fetch + rewrite; not sticky).
  - Effective: `use_cache = cache_metadata or refresh_metadata`;
    `refresh = refresh_metadata`.
- **`meta.cache`** ∈ `{"hit","miss","refreshed"}` emitted by the cached read path.
- **Invalidation (mandatory)**: every schema-write core function busts the
  active profile's cache file on success (and only when NOT dry-run). Scope =
  schema-definition writes: entity create/delete/update, attribute
  add/delete/update, optionset create/update/delete, relationship
  create×2/delete (+ update), and publish-all/publish-xml. Solution-record,
  publisher, and webresource writes are NOT schema-shape changes and are out of
  scope. Invalidation never raises (a failed unlink must not fail a good write).
- **Decoupling**: the cache module takes a `ConnectionProfile` + an injected
  `fetch` callable (no import of `metadata`), so `metadata` → `metadata_cache`
  is the only dependency edge (no cycle). Tests need no network.
- **`crm metadata cache-clear`**: deletes the active profile's cache file,
  reports `{cleared: bool}`.

## File structure

- NEW `crm/core/metadata_cache.py` (pyright strict) — persistence + orchestration.
- NEW `crm/tests/test_metadata_cache_disk.py` — cache module unit tests.
- EDIT `crm/cli.py` — two global flags + two `CLIContext` fields.
- EDIT `crm/commands/metadata.py` — cache-aware `entities`, new `cache-clear`.
- EDIT `crm/core/metadata.py`, `metadata_attrs.py`, `metadata_update.py`,
  `optionsets.py`, `relationships.py`, `solution.py` — invalidation on success.
- EDIT `crm/commands/repl.py` — `MetadataCache` routes through disk cache.
- EDIT tests: new command/invalidation/repl tests.
- EDIT docs: README.md, CHANGELOG.md, docs/how-to/metadata.md,
  docs/reference/cli.md, crm/skills/SKILL.md.

---

## Task 1 — Cache module + unit tests

Create `crm/core/metadata_cache.py` (pyright **strict**). Pure persistence +
orchestration; no Click, no network.

API:
```python
TTL_SECONDS: int = 900

@dataclass(frozen=True)
class CacheLookup:
    definitions: list[dict[str, str]]
    status: str            # "hit" | "miss" | "refreshed"

def cache_file(profile: ConnectionProfile) -> Path
def read_definitions(profile: ConnectionProfile, *, now: float) -> list[dict[str, str]] | None
def write_definitions(profile: ConnectionProfile, definitions: list[dict[str, str]], *, now: float) -> None
def clear(profile: ConnectionProfile) -> bool          # True if a file existed
def invalidate(profile: ConnectionProfile) -> None     # clear() but swallow OSError
def load_definitions(
    profile: ConnectionProfile,
    fetch: Callable[[], list[dict[str, str]]],
    *,
    refresh: bool,
    now: float,
) -> CacheLookup
```

Behavior:
- `cache_file`: `<CRM_HOME or ~/.crm>/cache/<profile.name>/entitydefs.json`.
  Resolve home self-contained: `Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()`
  (mirrors `session._state_root`'s CRM_HOME contract; do not import the private helper).
- `write_definitions`: mkdir parents; atomic write (tmp + `os.replace`) of
  `{"url": profile.url.rstrip("/"), "api_version": profile.api_version,
    "cached_at": now, "definitions": definitions}`, utf-8, indent=2.
- `read_definitions`: return None on absent file, `OSError`,
  `json.JSONDecodeError`/`ValueError`, non-dict payload, `url` !=
  `profile.url.rstrip("/")`, `api_version` mismatch, or `now - cached_at > TTL_SECONDS`.
  Otherwise return the `definitions` list (validate it is a list of dicts;
  coerce defensively, else miss).
- `load_definitions`: if `refresh` → `defs = fetch()`, write, `status="refreshed"`.
  Else `cached = read_definitions(...)`; if not None → `status="hit"`;
  else `defs = fetch()`, write, `status="miss"`.
- `clear`: unlink if exists, return whether it existed.
- `invalidate`: `try: clear(profile) except OSError: pass`.

Tests (`crm/tests/test_metadata_cache_disk.py`, `# pyright: basic`,
isolate `CRM_HOME` to `tmp_path` via monkeypatch.setenv so the real `~/.crm`
is never touched):
- roundtrip write→read = hit (returns same definitions).
- url mismatch → None; api_version mismatch → None.
- TTL matrix: `now - cached_at` of 0, 899, 900, 901 → hit, hit, hit/miss boundary
  (define `> TTL` as miss so 900 = hit, 901 = miss), absent → None.
- corrupt file (write garbage) → None (no raise).
- non-dict / missing-keys payload → None.
- `clear` returns True then False; file removed.
- `invalidate` on a non-existent dir does not raise.
- `load_definitions`: refresh → calls fetch once, status "refreshed", file rewritten;
  cold → fetch once, "miss", file written; warm → fetch NOT called, "hit".
  (Use a counter closure as `fetch`; no requests_mock needed.)
- per-profile isolation: two profiles (different `.name`) → independent files.

Verify: `pytest crm/tests/test_metadata_cache_disk.py` green; `pyright` clean.

---

## Task 2 — CLI flags + cache-aware `metadata entities` + `cache-clear`

EDIT `crm/cli.py`:
- `CLIContext.__init__`: add `self.cache_metadata: bool = False` and
  `self.refresh_metadata: bool = False`.
- Root group: add
  `--cache-metadata` (is_flag, help: "Use the persistent on-disk metadata cache
  (env: CRM_CACHE_METADATA). Default off.") and
  `--refresh-metadata` (is_flag, help: "Force-refresh the on-disk metadata cache
  on this call.").
- In `cli()`: sticky cache flag like `stage_only`:
  `env_cache = os.environ.get("CRM_CACHE_METADATA","").lower() in ("1","true","yes","on")`;
  `cli_ctx.cache_metadata = cli_ctx.cache_metadata or cache_metadata or env_cache`.
  `cli_ctx.refresh_metadata = refresh_metadata` (per-invocation, not sticky).

EDIT `crm/commands/metadata.py`:
- `metadata_entities`: compute `use_cache = ctx.cache_metadata or ctx.refresh_metadata`.
  - If `use_cache` and `custom_only`: `raise click.UsageError("--custom-only is not
    supported with --cache-metadata (the cache stores only logical/set names)")`
    (exit 2 — matches the codebase mutually-exclusive-flags convention).
  - If `use_cache`:
    ```python
    import time
    from crm.core import metadata_cache as mc_mod
    backend = ctx.backend()
    lookup = mc_mod.load_definitions(
        backend.profile,
        fetch=lambda: meta_mod.list_entity_definitions(backend),
        refresh=ctx.refresh_metadata,
        now=time.time(),
    )
    rows = lookup.definitions
    if top is not None:
        if top < 1: raise D365Error("--top must be >= 1")
        rows = rows[:top]
    ```
    Wrap the fetch in the existing `try/except D365Error → _handle_d365_error`.
    JSON mode: `ctx.emit(True, data=rows, meta={"cache": lookup.status, "count": len(rows)})`.
    Human mode: table headers `["LogicalName","EntitySetName"]`, rows from
    `(r["logical"], r["set_name"])`, `meta={"cache": lookup.status, "count": len(rows)}`.
  - Else (default): existing 5-field path UNCHANGED (byte-identical).
- New `@metadata_group.command("cache-clear")`:
  ```python
  @metadata_group.command("cache-clear")
  @pass_ctx
  def metadata_cache_clear(ctx: CLIContext):
      """Delete the active profile's on-disk metadata cache."""
      from crm.core import metadata_cache as mc_mod
      try:
          backend = ctx.backend()
      except D365Error as exc:
          _handle_d365_error(ctx, exc); return
      cleared = mc_mod.clear(backend.profile)
      ctx.emit(True, data={"cleared": cleared})
  ```

Tests (`crm/tests/test_metadata_cache_cmd.py`, `# pyright: basic`, CliRunner +
requests_mock + `CRM_HOME`→tmp + `CRM_DOTENV`→noop, env-snapshot per the test
isolation pattern):
- `metadata entities --cache-metadata --json` cold → `meta.cache=="miss"`, data is
  2-field rows; second identical call → `meta.cache=="hit"` and the mock GET is hit
  only once across the two calls (assert call count) — proves disk hit skips network.
- `--refresh-metadata` → `meta.cache=="refreshed"`, network hit even when warm.
- default `metadata entities --json` (no flags) → no `meta.cache` key, 5-field rows
  (`SchemaName` present) — byte-identical guard.
- `metadata entities --cache-metadata --custom-only` → exit 2 (UsageError).
- `metadata entities --cache-metadata --top 1 --json` → 1 row, `meta.cache` present.
- `metadata cache-clear --json` after a write → `data.cleared==true`; again → `false`.

Verify: new tests green; full suite green; `pyright` clean.

---

## Task 3 — Write-path invalidation + tests

Insert cache invalidation on successful, non-dry-run completion of each
schema-write core function. Pattern at each success return:
```python
if not backend.dry_run:
    from crm.core import metadata_cache as mc_mod
    mc_mod.invalidate(backend.profile)
```
(Place immediately before the function's success `return`, after the write call
returned without raising. Use a module-level `from crm.core import metadata_cache`
import where it does not create an import cycle; `metadata.py` may need a
function-local import to avoid a cycle with `metadata_cache`'s typing import —
verify and choose accordingly. `metadata_cache` must NOT import these modules at
top level.)

Functions to wire (16):
- `crm/core/metadata.py`: `create_entity`, `delete_entity`.
- `crm/core/metadata_attrs.py`: `add_attribute`, `delete_attribute`.
- `crm/core/metadata_update.py`: `update_entity`, `update_attribute`, `update_relationship`.
- `crm/core/optionsets.py`: `create_optionset`, `update_optionset`, `delete_optionset`.
- `crm/core/relationships.py`: `create_one_to_many`, `create_many_to_many`, `delete_relationship`.
- `crm/core/solution.py`: `publish_all`, `publish_xml`.

Tests (`crm/tests/test_metadata_cache_invalidation.py`, `# pyright: basic`,
`CRM_HOME`→tmp):
- Helper that seeds a cache file for a profile via `metadata_cache.write_definitions`.
- For a representative subset spanning every edited module — at minimum
  `create_entity`, `delete_entity`, `add_attribute`, `update_entity`,
  `create_optionset`, `create_one_to_many`, `publish_all` — with requests_mock
  mocking the write: seed cache → run fn → assert `cache_file(profile)` gone.
- dry-run guard: `D365Backend(profile, dry_run=True)` → run `create_entity`
  (or any) → cache file SURVIVES (no invalidation on a preview).

Verify: new tests green; full suite green; `pyright` clean.

---

## Task 4 — REPL persistent-cache wiring + tests

EDIT `crm/commands/repl.py`:
- `MetadataCache.__init__(self, *, use_cache: bool = False, refresh: bool = False)`.
- `_load`: when `use_cache`, route through
  `metadata_cache.load_definitions(backend.profile,
   fetch=lambda: list_entity_definitions(backend), refresh=self._refresh,
   now=time.time())` and populate `_logical`/`_set_names` from
  `lookup.definitions`; else current direct `list_entity_definitions(backend)`.
  (After first load, set `self._refresh = False` so a one-shot refresh does not
  re-fetch on every completion within the session.)
- `repl()` bootstrap: `cache = MetadataCache(use_cache=ctx.cache_metadata,
  refresh=ctx.refresh_metadata)`.

Tests (extend `crm/tests/test_metadata_cache.py` or new
`test_repl_disk_cache.py`, `# pyright: basic`, `CRM_HOME`→tmp):
- `MetadataCache(use_cache=True)` cold → reads from network once, writes disk;
  a fresh `MetadataCache(use_cache=True)` then serves from disk without a 2nd
  network call (assert mock call count). Default `MetadataCache()` → always live.

Verify: new tests green; full suite green.

---

## Task 5 — Docs sync

Per CLAUDE.md "keep docs in sync" — same change ships the docs.
- `CHANGELOG.md`: `## [Unreleased]` **Added** bullet for the cache + flags +
  `cache-clear`, referencing #88.
- `README.md`: short capability note (opt-in metadata cache, flags, cache-clear).
- `docs/how-to/metadata.md`: how-to for `--cache-metadata`/`--refresh-metadata`/
  `cache-clear`, the per-profile file location, TTL, invalidation, and the
  cache-mode column-reduction caveat on `metadata entities`.
- `docs/reference/cli.md`: document the two global flags and the `cache-clear`
  subcommand.
- `crm/skills/SKILL.md`: add the flags + `cache-clear` so the shipped agent skill
  matches the CLI (SKILL ↔ CLI sync rule).

Verify: `mkdocs build --strict` clean; `git grep -ni moce` empty (generic-repo rule).

---

## Final gate

- Full `pytest` green; `pyright` clean; `mkdocs build --strict` clean.
- Final whole-diff code review.
- Branch `metadata-cache-88` → PR → `@copilot` review (substantial feature).
