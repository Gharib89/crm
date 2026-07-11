# Python support floor evaluation — 3.9 → 3.10 / 3.12 / 3.13 / 3.14?

Research for issue #847. Date: 2026-07-11. All claims sourced from primary sources
(python.org / devguide, docs.python.org What's New, pyinstaller.org changelog +
GitHub `CHANGES.rst`, PyPI JSON metadata, docs.astral.sh, pyright docs, SPEC 0).
Unverifiable claims are flagged in the last section.

**Headline finding:** the declared 3.9 floor is already fictional. `setup.py` pins
`click>=8.4`, and every click release ≥ 8.2.0 declares `requires_python >=3.10`
(PyPI metadata, verified for 8.2.0 and 8.4.0). `pip install crm` on Python 3.9
cannot resolve — the *effective* floor is 3.10 today. Likewise `requests>=2.28`
resolves to 2.32.5 on 3.9 (2.33.0+ requires ≥3.10), so a 3.9 install would also
be silently frozen on old requests even if click didn't hard-fail. Python 3.9
itself has been EOL since 2025-10-31.

## TL;DR table

| Candidate floor | EOL runway (from 2026-07) | Interpreter perf vs current floor | PyInstaller | Runtime dep compat | Syntax gained |
|---|---|---|---|---|---|
| **3.9 (status quo)** | **EOL 2025-10-31 — none** | baseline | fine (≥3.8) | **broken**: click≥8.4 needs ≥3.10 | — |
| **3.10** | security-only, EOL **2026-10** (~3 months) | ~ none over 3.9 | fine | all deps OK; matches click/requests floor | `match`, PEP 604 `X\|Y`, parenthesized `with`, ParamSpec |
| **3.12** | security-only, EOL 2028-10 | 3.11's ~25% avg + 10-15% faster startup (frozen imports), 3.12 targeted wins (PEP 709) | supported since 5.13.0 (2023-06) | all deps OK, cp312 wheels everywhere | + ExceptionGroup, `Self`, `tomllib` (3.11); PEP 695 `type`/generics, PEP 701 f-strings (3.12) |
| **3.13** | bugfix until ~2026-10ish, EOL 2029-10 | + ~33% faster `typing` import, faster `functools`/`enum`/`importlib.metadata` imports | supported since 6.10.0 (2024-08) | all deps OK, cp313 wheels everywhere | + PEP 594 dead batteries gone, colored tracebacks, new REPL |
| **3.14** | bugfix, EOL 2030-10; current stable (3.14.6) | + tail-call interp 3-5% (build-flag, Clang-only); JIT still **not** default | supported since 6.15.0 (2025-08) | all deps OK, cp314 wheels everywhere | + `-X importtime=2`, t-strings, deferred annotations (PEP 649/749) |

Repo pins today: `python_requires=">=3.9"`, ruff `target-version = "py39"`, pyright
`pythonVersion 3.9`; CI tests on 3.11, e2e on 3.12, **release binaries built on
3.11** (PyInstaller onefile), dev venv 3.13.12. Most users run the PyInstaller
binary, which bundles its own interpreter — for them "the floor" is really
"which Python the release workflow builds with", not `python_requires`.

## 1. EOL timelines (devguide.python.org/versions, python.org/downloads)

| Version | Status (2026-07) | First release | EOL |
|---|---|---|---|
| 3.9 | **end-of-life** (final release 3.9.25) | 2020-10 | **2025-10-31** |
| 3.10 | security-only | 2021-10 | **2026-10** |
| 3.11 | security-only | 2022-10 | 2027-10 |
| 3.12 | security-only | 2023-10 | 2028-10 |
| 3.13 | bugfix | 2024-10 | 2029-10 |
| 3.14 | bugfix | 2025-10 | 2030-10 |
| 3.15 | prerelease | expected 2026-10 | 2031-10 |

Newest stable release today: **Python 3.14.6 (2026-06-10)**. 3.15.0 lands ~Oct 2026,
at which point 3.10 goes EOL and 3.13 drops to security-only.

Sources: <https://devguide.python.org/versions/>, <https://www.python.org/downloads/>.

## 2. Interpreter performance (official What's New pages)

The cold-start-bound picture (what matters for this CLI):

- **3.11** is the big one: Faster CPython — "an average of 25% faster than 3.10"
  (pyperformance, 10-60% depending on workload), and — directly relevant —
  **"interpreter startup is now 10-15% faster"** via frozen core modules
  (statically allocated code objects instead of read-pycache→unmarshal→heap-alloc).
  The doc explicitly calls out "a major impact for short-running programs".
  Also: cheaper lazy frames (3-7%), inlined Python-to-Python calls, PEP 659
  specializing adaptive interpreter.
  Source: <https://docs.python.org/3/whatsnew/3.11.html>.
- **3.12**: no overall speedup figure stated; targeted wins — PEP 709 comprehension
  inlining (up to 2x on comprehensions), `isinstance()` vs runtime-checkable
  protocols 2-20x, re.sub 2-3x, BOLT support 1-5%. Nothing stated about startup.
  Source: <https://docs.python.org/3/whatsnew/3.12.html>.
- **3.13**: JIT is **experimental, disabled by default** (`--enable-experimental-jit`
  build flag, `PYTHON_JIT` env), "modest" gains expected. Free-threading experimental
  (separate `python3.13t` executable). **Import-time wins that matter here:
  `typing` module import ~33% faster; `email.utils`, `enum`, `functools`,
  `importlib.metadata`, `threading` import improvements.**
  Source: <https://docs.python.org/3/whatsnew/3.13.html>.
- **3.14**: JIT still **experimental and not enabled by default** (Windows/macOS
  binaries ship it built-in but off). New tail-call interpreter: 3-5% geomean on
  pyperformance, but opt-in at build time (`--with-tail-call-interp`) and
  Clang 19+ only. Free-threaded build officially supported (PEP 779) but costs
  5-10% single-threaded — irrelevant/negative for this CLI. No startup figures
  stated vs 3.13. `-X importtime=2` now reports cached modules — a diagnostics
  improvement for exactly this repo's cold-start work.
  Source: <https://docs.python.org/3/whatsnew/3.14.html>.

**JIT verdict for a cold-start-bound CLI:** the JIT is off by default in both 3.13
and 3.14, and it optimizes hot loops in long-running code — it does nothing for
(and would add warm-up cost to) a process that lives for one command. The
interpreter-level wins that actually help this CLI are 3.11's startup/frozen-modules
work and 3.13's stdlib import-time reductions.

Caveat: the runtime floor and the **binary's** interpreter are independent — the
release workflow already builds on 3.11, so binary users already get the 3.11
startup gains regardless of where `python_requires` sits. Raising the floor only
changes pip/source users; raising the **build** Python (e.g. to 3.13) is what
moves binary cold-start, and can be done independently of the floor.

## 3. PyInstaller (pyinstaller.org changelog / GitHub CHANGES.rst)

| Python | First PyInstaller release with support | Date |
|---|---|---|
| 3.12 | 5.13.0 | 2023-06-24 |
| 3.13 | 6.10.0 | 2024-08-10 |
| 3.14 | 6.15.0 | 2025-08-03 |
| 3.15 | 6.21.0 | 2026-06-13 |

Latest release: **6.21.0 (2026-06-13)**, `requires_python >=3.8,<3.16` (PyPI).
So 3.12/3.13 support is mature (2-3 years old), 3.14 support is ~11 months old
and has had several point releases since. The repo's dev pin `pyinstaller>=6.0`
resolves to 6.21.0. Known-issue scan of the changelog: a `multiprocessing`
forkserver fix for 3.13.13/3.14.4 landed in 6.20.0 (crm doesn't use
multiprocessing); the onefile deprecation warning applies only to **macOS .app
bundles** (v7.0 will block onefile+.app) — plain onefile on Windows/Linux, which
is what `scripts/build.ps1`/`build.sh` produce, is unaffected.

Sources: <https://pyinstaller.org/en/stable/CHANGES.html>,
<https://raw.githubusercontent.com/pyinstaller/pyinstaller/develop/doc/CHANGES.rst>,
<https://pypi.org/pypi/pyinstaller/json>.

## 4. Dependency floors (PyPI `requires_python`, verified 2026-07-11)

Runtime deps from `setup.py`:

| Dep (pin) | Latest | `requires_python` | Notes |
|---|---|---|---|
| click>=8.4 | 8.4.2 | **>=3.10** | dropped 3.9 at 8.2.0; **makes the 3.9 floor uninstallable** |
| requests>=2.28 | 2.34.2 | **>=3.10** | 2.32.5 was the last ≥3.9 release; 2.33.0+ is ≥3.10 |
| requests_ntlm>=1.2 | 1.3.0 | >=3.8 | transitive pyspnego 0.12.1 ≥3.9, cryptography 49.0.0 ≥3.9 (excl. 3.9.0/3.9.1) |
| prompt_toolkit>=3.0 | 3.0.52 | >=3.8 | |
| questionary>=2.0 | 2.1.1 | >=3.9 | |
| msal>=1.20 | 1.37.0 | >=3.9 | |
| PyYAML>=6.0 | 6.0.3 | >=3.8 | wheels: cp38–**cp314** ✓ |
| keyring>=24 | 25.7.0 | >=3.9 | |
| jq>=1.8 | 1.12.0 | >=3.8 | wheels: cp38–**cp314** ✓ (C extension — the wheel matrix is the real constraint, and it covers 3.14) |
| defusedxml>=0.7 | 0.7.1 | >=2.7 (pure) | |

**Already dropped 3.9:** click, requests (both now ≥3.10).
**Still support 3.9:** everything else (moot — 3.9 is EOL).
**Caps below 3.13/3.14:** none. Every C-extension dep (jq, PyYAML, transitively
cryptography) ships cp313 and cp314 wheels. PyInstaller caps at <3.16, i.e. it
allows 3.14/3.15.

Source: `https://pypi.org/pypi/<pkg>/json` per package (and per-version endpoints
for click 8.1.8/8.2.0/8.4.0, requests 2.32.5/2.33.0).

## 5. Language / tooling gains per step

**py39 → py310** (ruff `target-version = "py310"`):

- ruff **UP007** (non-pep604-annotation-union) and **UP045**
  (non-pep604-annotation-optional) activate: auto-rewrite `Union[X, Y]` → `X | Y`
  and `Optional[X]` → `X | None` across the tree (fix is "safe" at py310+;
  it is only marked unsafe below 3.10 or when comments sit inside the annotation).
  Sources: <https://docs.astral.sh/ruff/rules/non-pep604-annotation-union/>,
  <https://docs.astral.sh/ruff/rules/non-pep604-annotation-optional/>.
- Hand-written (not auto-fixed): `match` structural pattern matching,
  parenthesized multi-line context managers, ParamSpec/TypeAlias/TypeGuard from
  `typing` proper. Source: <https://docs.python.org/3/whatsnew/3.10.html>.

**py310 → py312/py313:**

- 3.11: ExceptionGroup/`except*`, `typing.Self`, `tomllib`, fine-grained error
  locations in tracebacks. Source: <https://docs.python.org/3/whatsnew/3.11.html>.
- 3.12: ruff **UP040** (non-pep695-type-alias) activates at py312 — auto-rewrites
  `X: TypeAlias = ...` → `type X = ...` (unsafe fix outside stubs); UP046/UP047
  modernize generic classes/functions to PEP 695 syntax. PEP 701 f-strings
  (nesting/quote-reuse). Sources:
  <https://docs.astral.sh/ruff/rules/non-pep695-type-alias/>,
  <https://docs.python.org/3/whatsnew/3.12.html>.
- 3.13: little new *syntax*; gains are runtime UX (REPL, colored tracebacks,
  error suggestions) and the PEP 594 module removals (crm imports none of the
  removed dead-battery modules). Source: <https://docs.python.org/3/whatsnew/3.13.html>.

**pyright:** `pythonVersion` "generates errors if the source code makes use of
language features that are not supported in that version" and "tailor[s] its use
of type stub files, which conditionalizes type definitions based on the version"
— i.e. raising it *permits* newer syntax and selects newer stdlib stub branches;
it does not add strictness checks per se. The current 3.9 pin exists precisely
to stop 3.10+ symbols masking runtime ImportErrors (see project memory); once the
floor moves, the pin should move with it, in lockstep.
Source: <https://raw.githubusercontent.com/microsoft/pyright/main/docs/configuration.md>.

## 6. Ecosystem norms

- **SPEC 0** (Scientific Python): "support for Python versions be dropped 3 years
  after their initial release" — stricter than NEP 29's 42 months. Under SPEC 0,
  3.10 support ended Q4 2024, 3.11 ends Q4 2025, 3.12 Q4 2026. By this norm the
  floor today would be **3.12**. Source: <https://scientific-python.org/specs/spec-0000/>.
- **pip** 26.1.2: `requires_python >=3.10` (PyPI metadata) — the most-installed
  Python CLI has already dropped 3.9.
- **click** (this repo's own CLI framework): ≥3.10 since 8.2.0.
- **httpie** 3.2.4: still `>=3.7`, but that release predates 2025 (project is
  slow-moving; weak signal).
- **aws-cli v2**: `pyproject.toml` on the `v2` branch declares `requires-python
  ">=3.9"`; v2's official installers distribute a bundled interpreter, so the
  pip floor is secondary for its users — the same shape as crm's binary-first
  distribution. Source: <https://github.com/aws/aws-cli/blob/v2/pyproject.toml>.

## Options matrix (no recommendation — maintainer decides)

| Option | Pros | Cons |
|---|---|---|
| **Stay 3.9** | Zero work | Floor is a lie: EOL interpreter + `click>=8.4` already requires 3.10, so 3.9 installs fail at resolution. Ruff/pyright pinned to a dialect no supported dep targets. |
| **3.10** | Matches the *actual* resolvable floor (click, requests); honest metadata; UP007/UP045 auto-modernization (`X \| Y`, `X \| None`) repo-wide; `match` available; zero user breakage (nobody on 3.9 can install today anyway) | 3.10 itself is EOL in ~3 months (2026-10) — buys one year before the same conversation recurs; no interpreter perf change for source users |
| **3.12** | Aligns with SPEC 0 norm (3.12 is the oldest SPEC-0-supported version until Q4 2026); source users guaranteed ≥3.11 startup gains (10-15% faster startup, 25% avg perf); PEP 695 `type` aliases + PEP 701 f-strings; e2e CI already on 3.12; 2+ years of runway (EOL 2028-10) | Cuts off distro-default 3.10/3.11 users on the pip path (Ubuntu 22.04 = 3.10, 24.04 = 3.12); CI/release build on 3.11 would need bumping; still security-only status |
| **3.13** | First floor still in *bugfix* status today; 3.13's `typing`/stdlib import-time cuts land for source users (cold-start relevant); matches dev venv (3.13.12); PyInstaller support ~2 years mature; ~3.3 years runway | Drops to security-only ~Oct 2026 anyway; excludes every current LTS-distro default Python on the pip path; forces CI 3.11 + release-build 3.11 + e2e 3.12 all to move at once |
| **3.14** | Longest runway (2030-10); current stable; `-X importtime=2` for cold-start diagnostics; all deps + PyInstaller (6.15+) verified compatible | Newest = least field-tested PyInstaller/toolchain combo; JIT/tail-call bring nothing to a one-shot CLI (JIT off by default; tail-call is a build flag the python.org builds don't guarantee); most aggressive cut for pip users; ruff py314 target adds no auto-fix value beyond py312 |

Orthogonal lever worth separating from the floor decision: **bump the PyInstaller
build Python** (3.11 → 3.13) to give binary users the 3.13 import-time gains
without touching `python_requires` at all — binary users never see the floor.

## Claims NOT verified from primary sources

- **SPEC 0 drop quarters for 3.10/3.11** — computed from SPEC 0's stated 3-year
  rule + python.org release dates; the fetched page excerpt only listed 3.11+
  quarters explicitly.
- **aws-cli v2 bundling its own Python** — inferred common knowledge; the
  `requires-python ">=3.9"` in its v2 `pyproject.toml` *is* verified, the
  bundled-interpreter claim was not re-verified against AWS docs in this pass.
- **Startup-time deltas for 3.12→3.13→3.14** — CPython's What's New pages publish
  no interpreter-startup figures after 3.11; the 3.13 import-time claims are
  per-module (`typing` ~33%), not a whole-startup number. No primary benchmark of
  end-to-end CLI cold start across versions exists on python.org.
- **Which pip release dropped 3.9** — only the current floor (26.1.2 ≥3.10) was
  verified, not the release where it changed.
- **httpie's floor freshness** — metadata verified (>=3.7) but the project's last
  release predates the 3.9 EOL, so it says little about current norms.
