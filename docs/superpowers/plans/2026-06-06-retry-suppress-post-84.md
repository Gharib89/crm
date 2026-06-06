# Plan — Client-side retry-suppression guard for non-idempotent POST creates (#84)

## Problem

`D365Backend.request()` auto-retries POSTs in two places, which can silently
double-commit a non-idempotent record create when the first response is lost:

1. **Transport branch** (`crm/utils/d365_backend.py` ~588): retries any method
   (incl. POST) on retryable transport exceptions.
2. **Response branch** via `_is_response_retryable(resp, method)` (~878): returns
   `True` for POST on **429** and **503**, so `request()` re-sends the POST.

`$batch` (`D365Backend.batch()`, ~700-724) has its **own** retry loop on 429/503
that is independent of `request()` / `_is_response_retryable`. Retrying a $batch
POST is the caller's documented responsibility — it must be **preserved**.

## Goal / acceptance criteria (from issue #84)

- POST record-creates are NOT auto-retried on transport error or 429/503 **by default**.
- `$batch` POST retry preserved.
- `--retry-on-ambiguous` opt-in restores retry for POST.
- SKILL.md documents create-retry semantics + the upsert-by-id escape hatch.
- Tests cover both the transport-branch and response-retry gating for POST creates.

## Locked design

A POST is treated as non-idempotent regardless of what it does (create vs action),
so the gate keys on `method == "POST"`, matching the issue body. Idempotent verbs
(GET/PUT/PATCH/DELETE) keep their current retry behavior unchanged.

### Backend (`crm/utils/d365_backend.py`)

1. `D365Backend.__init__`: add param `retry_on_ambiguous: bool = False`; store
   ```python
   self._retry_on_ambiguous = retry_on_ambiguous or _resolve_bool_env("CRM_RETRY_ON_AMBIGUOUS")
   ```
   The env `CRM_RETRY_ON_AMBIGUOUS` is an alternative opt-in (mirrors the
   `CRM_NO_RETRY` / `CRM_RETRY_MAX` env-driven retry family + keeps tests simple).

2. `_is_response_retryable(resp, method, retry_on_ambiguous: bool = False)`:
   add the param and gate POST first:
   ```python
   status = resp.status_code
   method_upper = method.upper()
   if method_upper == "POST" and not retry_on_ambiguous:
       return False
   if status == 429:
       return True
   if status == 503 and method_upper == "POST":
       return True
   if status in (502, 503, 504) and method_upper in ("GET", "PUT", "PATCH", "DELETE"):
       return True
   return False
   ```
   Update the call site (~604) to pass `self._retry_on_ambiguous`.

3. Transport branch (~588-596): block POST retry unless opted in:
   ```python
   except requests.RequestException as exc:
       post_blocks_retry = method.upper() == "POST" and not self._retry_on_ambiguous
       if attempt >= max_retries or post_blocks_retry or not _is_transport_retryable(exc):
           raise D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: {exc}") from exc
       ...
   ```

4. `batch()` — **no change**. Its inline 429/503 retry stays, preserving the AC.

### CLI (`crm/cli.py`)

- `CLIContext.__init__`: add `self.retry_on_ambiguous: bool = False`.
- Backend cache key (currently `(profile_name, password, dry_run, auth_scheme)`,
  annotated `tuple[str|None, str|None, bool, str|None] | None`): append
  `self.retry_on_ambiguous` → 5-tuple; update the `_backend_key` annotation too.
- `backend()`: pass `retry_on_ambiguous=self.retry_on_ambiguous` to `D365Backend(...)`.
- Root `cli()` group: add
  `@click.option("--retry-on-ambiguous", "retry_on_ambiguous", is_flag=True,
   help="Re-enable auto-retry of non-idempotent POST creates on transport "
        "error / 429 / 503 (env: CRM_RETRY_ON_AMBIGUOUS). Off by default: a "
        "lost POST response may have committed.")`
  and set `cli_ctx.retry_on_ambiguous = retry_on_ambiguous` (non-sticky, like
  `--dry-run`).
- `docs/reference/cli.md` is auto-generated via mkdocs-click — the flag appears
  with no manual edit.

## Tasks

### Task 1 — Backend retry-suppression + CLI flag (TDD)
Files: `crm/utils/d365_backend.py`, `crm/cli.py`, `crm/tests/test_resilience.py`
(+ a CLI smoke test if a natural home exists).

Test changes (these existing tests assert the OLD behavior and MUST flip):
- `TestIsResponseRetryable.test_truth_table`: `("POST", 429)` and `("POST", 503)`
  now expect `False` by default. Add opt-in coverage: with
  `retry_on_ambiguous=True`, POST 429/503 → `True`.
- `test_post_does_retry_on_503` → now POST does NOT retry on 503 by default
  (`m.call_count == 1`, raises). Add `test_post_retries_on_503_with_opt_in`.

New coverage:
- Transport branch: POST transport error NOT retried by default (call_count==1,
  raises `D365Error` with the transport prefix); retried with opt-in.
- Regression guard: GET/PATCH/PUT/DELETE still retry on their transient classes
  (unchanged).
- `$batch` POST still retries on 503 (preserve AC) — backend.batch() with a
  mocked 503→200 sequence, assert 2 calls.
- Opt-in via env `CRM_RETRY_ON_AMBIGUOUS=1` enables POST retry; via constructor
  param too.

Verify: `pytest crm/tests/test_resilience.py -q` green; full `pytest` green;
`pyright --pythonpath .venv/bin/python` clean on the changed strict file.

### Task 2 — Documentation sync
Files: `crm/skills/SKILL.md`, `README.md`, `CHANGELOG.md`, and the natural
how-to home (`docs/how-to/entity.md` and/or `docs/how-to/connection.md`).

- SKILL.md: a "create-retry semantics" note near the create / dry-run section —
  a POST whose response is lost may have committed; POST creates are NOT
  auto-retried by default; use upsert-by-id (`entity upsert <set> <id>`, a
  create-if-missing PATCH) for idempotent writes; `--retry-on-ambiguous` (or `CRM_RETRY_ON_AMBIGUOUS=1`) opts
  back in; `$batch` retry is the caller's responsibility (preserved).
- README.md: brief note in the errors/resilience area (~line 219-222).
- CHANGELOG.md `## [Unreleased]`: **Changed** entry (behavior change — POST no
  longer auto-retried by default) + **Added** entry (the `--retry-on-ambiguous`
  flag / env). Reference (#84).
- Keep repo generic (Contoso placeholders only; no real org names).

Verify: `mkdocs build --strict` clean.

## Out of scope
- Distinguishing create-POST from action-POST (gate is method-level by design).
- Changing `$batch` retry semantics.
- New profile fields.
