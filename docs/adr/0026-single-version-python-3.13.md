---
status: accepted
---

# Single-version Python policy: floor = CI = e2e = build = 3.13

Decided in #847 after a primary-source research pass
(`docs/research/2026-07-python-floor-evaluation.md`) and local cold-start
benchmarks.

The declared `python_requires=">=3.9"` floor had been fictional for some time:
`click>=8.4` (pinned for `NoSuchCommand`, see `crm/cli.py`; since raised to
`>=8.4.1` for an unrelated 8.4.0 `get_parameter_source()` regression) declares
`requires_python >=3.10`, so a 3.9 `pip install crm` cannot resolve — and 3.9
itself went EOL 2025-10-31. Meanwhile nothing exercised the floor anyway: CI
tested on 3.11, e2e on 3.12, release binaries built on 3.11, dev venv on 3.13.
Four different interpreters, none of them the documented one.

## Decision

Every pinned interpreter converges on **3.13**:

- `setup.py` `python_requires=">=3.13"`
- CI, e2e, docs, bump-guard, and release workflows: `python-version: 3.13`
- `pyrightconfig.json` `pythonVersion: 3.13` (moves in lockstep with the floor
  — the pin exists to stop newer-version symbols masking runtime ImportErrors)
- ruff `target-version = "py313"` — lands together with its mechanical
  `UP` auto-fix sweep in a follow-up PR, because bumping the target activates
  UP007/UP045 as lint failures on the existing `Optional[X]`/`Union[X, Y]`
  annotations

One version everywhere means the floor is exercised by every CI run and is the
same interpreter users get in the PyInstaller binary — a claim the repo tests,
not an aspiration. Most users run the binary, which bundles its interpreter, so
the *build* Python is what moves their cold start; raising it 3.11 → 3.13
delivers the 3.11 frozen-modules startup work plus 3.13's stdlib import-time
cuts (`typing` ~33% faster to import) on the majority path.

## Considered options

- **3.10** — the honest minimum (matches the click/requests floor), but EOL
  2026-10: the same decision would recur within months.
- **3.12** — SPEC 0's norm today, Ubuntu 24.04's default. Rejected in favor of
  one-version simplicity: a 3.12 floor with a 3.13 build interpreter leaves the
  floor untested by the binary path, and 3.12 is security-only while 3.13 is
  still in bugfix.
- **3.14 (current stable)** — rejected on evidence: local `-X importtime`
  benchmarks of `import crm.cli` measured ~2x import cost on 3.14 vs 3.10–3.13
  (36 ms → 81 ms cumulative; the `click.types` → `inspect` → `traceback` stdlib
  chain roughly doubles, plausibly PEP 649's `annotationlib` machinery).
  Reproduced 3x, but evidence caveat: one WSL2 machine, uv-built CPython,
  editable install — a venv proxy, not a binary measurement. Beyond perf, 3.14
  offers nothing crm uses: the JIT is off by default and only helps hot loops in
  long-running processes (a one-shot CLI pays warmup for nothing); the tail-call
  interpreter is a Clang-19+ build flag python.org builds don't guarantee;
  t-strings have no consumer here; PEP 649 deferred annotations gain nothing
  (357 files already use `from __future__ import annotations`); ruff's py314
  target activates no auto-fixes beyond py312.

## Revisit triggers

- Python 3.15 lands (~Oct 2026) — re-benchmark, including whether the 3.14
  stdlib import regression was fixed upstream.
- A dependency raises its floor above 3.13, or PyInstaller support for a newer
  version matures.
- Contrary evidence on the 3.14 regression from a real binary benchmark
  (PyInstaller onefile timed on 3.13 vs 3.14) would reopen the ceiling choice.
