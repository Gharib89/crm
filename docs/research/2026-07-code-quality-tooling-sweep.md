# Code-quality tooling sweep — what to add beyond CodeRabbit and CodeQL

**Date:** 2026-07-11
**Feeds:** wayfinder ticket #823 on map [#819](https://github.com/Gharib89/crm/issues/819) (code-review stack)
**Siblings:** `docs/research/2026-07-coderabbit-oss-plan.md` (#820), `docs/research/2026-07-codeql-evaluation.md` (#822)

**Question:** with CodeRabbit OSS (AI PR review; its pipeline runs ruff, not mypy) and CodeQL
(`security-extended`, taint/dataflow) already decided, and ruff+pre-commit (#828) and GitGuardian
already in the stack, what *additional* tooling would materially improve code quality or the
review experience for this repo — ranked by value-for-effort, with a shortlist?

## Method & scope

Primary sources only (official docs, tool GitHub repos/LICENSE files, pricing pages, rule-list
docs, changelogs — no blogs). Every overlap claim ("ruff already covers X") was checked against
the actual rule set. Repo facts were verified locally against `pyproject.toml`,
`pyrightconfig.json`, `.github/workflows/*`, and `crm/`. Anything not pinnable to a primary
source is flagged **UNVERIFIED**. Sources are collected at the end.

**Binding scope from map #819** — OUT OF SCOPE and non-shortlistable: dependabot/renovate;
mutation testing; complexity metrics (radon/xenon); new CI *gates* beyond ruff; coverage
*thresholds*; branch-protection restructuring. Coverage is in scope **report-only**. radon/xenon
are assessed for completeness in the "Excluded by map scope" section and cannot be shortlisted.

## Repo facts that drive the verdicts (verified locally)

- **6 workflow files, ~451 lines**, several with nontrivial inline `bash` in `run:` blocks
  (`ci.yml` provisions .NET + `pac`, greps exit codes; `release.yml`/`semantic-release.yml`).
  Third-party actions in use (`actions/setup-dotnet`, `actions/setup-python@v6`, checkout@v6) and
  a **`RELEASE_PAT`** drives the release tag push (CLAUDE.md "Release"). → real surface for
  workflow linting/security auditing.
- **The dry-run contract is a genuine, repo-wide house pattern**: `36 of 60` `crm/core/*` modules
  reference `backend.dry_run`/`_dry_run`, returning `{_dry_run, would_*}` previews (verified
  `grep`). This is exactly the kind of AST-shaped convention ruff structurally cannot express and
  a semgrep custom rule can.
- **No `.pre-commit-config.yaml` yet** (owned by #828). Several candidates below are pre-commit
  hooks that would ride that adoption at near-zero marginal cost.
- **GitHub native secret scanning + push protection are BOTH `disabled` on this public repo
  right now** (verified `gh api repos/Gharib89/crm`), despite being free — a live, unflipped gap.
- pyright strict only on `crm/core/*` + `d365_backend.py`; the other ~250 py files are basic mode.
- Ruff (incoming) will own the `D` (pydocstyle), `S` (flake8-bandit), and `UP` (pyupgrade)
  families — which decides three candidates below by redundancy.

---

## Per-candidate assessment

### 1. semgrep / Opengrep — custom rules for house conventions — **ADOPT (small, targeted)**

**What it does.** Pattern-based static analysis with a user-authorable YAML rule language
(`pattern`, `patterns`, `pattern-either`, `pattern-inside`, metavariables). The draw here is
**custom rules encoding house conventions** the LLM reviewers and ruff can't enforce
deterministically — first candidate: the dry-run contract (36/60 core modules), plus D365-backend
call conventions.

**Licensing (the thing that scared people off is a non-issue here).** The Dec-2024 change
relicensed the **Semgrep-maintained rules registry** (new "Semgrep Rules License v1.0" — internal
use OK, no redistribution/resale), **not** the engine. The OSS engine/CLI is still **LGPL-2.1**
(renamed Semgrep OSS → Semgrep **Community Edition**). Running `semgrep scan --config …` in CI on
a public repo for your own quality gate is squarely inside the permitted internal use. **Opengrep**
(LGPL-2.1 fork off Semgrep v1.100.0, Feb 2025, multi-vendor governance, releases every ~2–3 weeks,
latest 1.25.0 2026-07-01) is a drop-in for the engine + same rule syntax if you want to avoid the
registry-license question entirely — at the cost of no first-party GitHub Action (UNVERIFIED: no
official opengrep Action found; download-binary + upload-sarif is the pattern).

**Overlap.** Low by design. Ruff has **no custom/third-party rule mechanism** (Astral confirmed;
plugin system tracked-but-unshipped in astral-sh/ruff#283), so house-specific semantic rules are
exactly ruff's structural gap. CodeQL does deep taint but authoring a CodeQL query for a "return-
shape" convention is far heavier than a semgrep pattern.

**Effort.** CI path is free and tokenless: `semgrep scan --config p/python --config
ci/rules.yml --error` (no `SEMGREP_APP_TOKEN`, no AppSec Platform login). **Wrinkle:** current
`semgrep` on PyPI needs Python ≥3.10 — install it in its own step/venv or the Docker image, not
the repo's 3.9-pinned dev venv (irrelevant to what it *scans*). The dry-run rule itself is a
"control-flow-shape" rule (needs `pattern-inside` + return-statement metavariable capture, maybe
`metavariable-pattern`) — realistically an afternoon of Playground iteration, not 10 minutes; the
robust version may only catch the direct-literal-return case and leave indirect returns to review.

**Verdict.** Adopt, scoped small: one local `rules.yml` with 1–3 house-convention rules
(dry-run contract first), run tokenless in CI. Skip the registry rules initially — `p/python`
overlaps ruff/CodeQL and adds noise; the value is the custom rules ruff/CodeQL *can't* express.
This is the only candidate that improves the *agent* experience by turning a prose CLAUDE.md
convention into a machine-checked, low-noise gate.

### 2. SonarQube Cloud (ex-SonarCloud) — **SKIP**

**What it does.** Free for public repos (OSS plan: unlimited public analysis, all features; the
GitHub Marketplace listing confirms $0). Python analysis: bugs, vulnerabilities (taint), code
smells, security hotspots, **cognitive complexity**, **token-based duplication detection**, and a
**maintainability A–E rating**. Automatic analysis (no CI scanner, no token) is available for
Python; PR decoration posts a summary comment + inline annotations on **new** issues only. The
default "Sonar way" quality gate is on by default but can be made non-blocking (leave the status
check un-required; swap the gate to drop the ≥80% new-code-coverage condition).

**Overlap.** Heavy. Its bug/vulnerability detection re-treads ground CodeQL (taint) + ruff (lint)
+ pyright (types) already own, under different rule IDs — i.e. another redundant PR-comment source
on AI-authored PRs, cutting against the low-noise goal. Its genuinely *non-overlapping* delta is
**cognitive complexity + duplication + maintainability rating + quality gate** — and complexity
metrics and coverage gates are explicitly out of scope for this repo (#819).

**Verdict.** Skip. The one distinctive capability (duplication/complexity/health-score) is the
part scope has ruled out; everything else duplicates existing signal and adds noise. If
cross-file duplication detection is ever wanted in isolation, revisit — but not as a whole
platform.

### 3. interrogate (docstring coverage) — **SKIP (already covered twice over)**

**What it does.** Measures the *percentage* of modules/classes/functions with a docstring (badge,
`--fail-under`). MIT, maintained (1.7.0, Apr 2024). It measures presence only, not content.

**Overlap — double.** (a) Ruff's pydocstyle `D1xx` already enforces docstring **presence**
per-node (D100 module, D101 class, D102 method, D103 function, D104 package, D105 magic, D106
nested class, D107 `__init__`) — inline, with everything else. (b) **CodeRabbit already ships a
"Docstring Coverage" pre-merge check with an 80% default threshold** (per its Pre-Merge Checks
docs) — so PR-level docstring-coverage visibility arrives *with the CodeRabbit adoption already
decided*. interrogate's only unique output (an aggregate %/badge) is thus covered by CodeRabbit at
PR level and enforceable by ruff `D1` at line level.

**Verdict.** Skip. And note this is downstream of the still-open docstring-convention question
(#826) — decide the convention, then just turn on the ruff `D` subset you want; no third tool.

### 4. vulture (dead code) — **SKIP by default (FP model fights this repo)**

**What it does.** AST-based unused-code detection (functions, classes, vars, imports). MIT,
actively maintained (2.16, Mar 2026). Genuinely distinct category — nothing in the stack does
cross-module reachability.

**Why it fights this repo.** Vulture's README itself warns about exactly this architecture:
decorator-registered functions (Click commands) are FP-prone (mitigated only by
`--ignore-decorators`), dynamically/lazily-accessed symbols are FP-prone by design (Python's
dynamism), and `__all__` handling is unaddressed. This repo is **heavy Click decorators + lazy
imports** (CLAUDE.md architecture; the lazy-CLI-group walk is a documented gotcha) — the precise
FP pattern. Usable only at `--min-confidence 100` with a maintained whitelist file, which is an
ongoing tax (every new lazy command needs whitelisting). The repo also has documented **dead-code
audit pain** (memory: "audit dead-code: verify + provenance"), reinforcing that noisy dead-code
signal is a known cost here.

**Verdict.** Skip by default. If dead-code hunting is ever wanted, run it *ad hoc* at
`--min-confidence 100`, not as a standing gate — do not wire it into pre-commit/CI where it will
nag on every registered command.

### 5. pytest coverage reporting (report-only) — **ADOPT the bare baseline; skip Codecov**

**In scope report-only** (no threshold gate). Two tiers:

- **(a) coverage.py / pytest-cov XML artifact** — `pytest --cov=crm --cov-report=xml` (omit
  `--cov-fail-under` → pure report) + `actions/upload-artifact`. Zero external service, zero
  token, zero account. The report is generated whether or not a fail-under flag is present.
- **(b) Codecov free tier** — free for public repos; adds a PR comment with coverage delta +
  **patch (diff) coverage** + web file view. Can be forced non-blocking (`informational: true` on
  both `project` and `patch` in `codecov.yml`). **But** for the repo's own `main`-branch runs it
  now needs a `CODECOV_TOKEN` secret unless the org's tokenless setting is flipped (existing orgs
  default to token-required) — so "free" still means owning a secret + a `codecov.yml`.
- **(c) CodeRabbit** — **not a coverage tool**; its checks are docstring/PR-title/description/issue
  assessment, no test-coverage ingestion. Category error to count it here.

**Verdict.** Adopt (a) — the bare XML artifact is the best value-for-effort report-only option and
needs no secret. Skip (b) unless the team specifically wants the PR-comment/diff-coverage UX
enough to own a token + config; if adopted, set `informational: true` from day one so it can never
become an accidental gate (which would violate scope).

### 6. Secret scanning beyond GitGuardian — **ADOPT GitHub native + push protection; skip the rest**

**Live gap (verified).** `gh api repos/Gharib89/crm` reports `secret_scanning: disabled` and
`secret_scanning_push_protection: disabled` — despite both being **free for public repos** and
default-on for new personal-account public repos since Mar 2024 (this repo somehow sits disabled).
This is a real, currently-open, zero-cost gap.

- **GitHub native secret scanning** (free, public repos): scans history for known patterns,
  partner-pattern detection (notifies the partner to revoke), Security-tab alerts. *Detection
  after push.*
- **Push protection** (free, public repos): **blocks the push before the secret lands in remote
  history** — the categorical delta vs post-hoc alerts. On GitHub.com this is a **server-side**
  guarantee that works even if a contributor has no local tooling.
- **vs GitGuardian (already running).** Complementary, not redundant: GitGuardian markets broader
  detector coverage (500+) and multi-surface scanning (CI, containers, chat), but its push-side
  protection on **GitHub.com** is a *client-side* ggshield pre-commit hook (pre-receive works only
  on GitHub Enterprise Server, not cloud) — i.e. skippable if a dev hasn't installed it. GitHub's
  server-side push protection can't be bypassed that way. Different enforcement points.
- **gitleaks** (MIT CLI; the *Action* needs a paid license for orgs): pattern/entropy only, no
  live verification; README says feature-complete/security-patches-only. Overlaps ggshield.
  **Skip.**
- **trufflehog** (AGPL-3.0): unique free capability is **live credential verification** (tests the
  secret against the provider API to cut FPs). Not strictly redundant, but GitGuardian's paid
  validity-checks tier covers the same ground. **Defer** — only if native+GitGuardian FP noise
  becomes a real burden, and only if AGPL-3.0 for a CI subprocess is acceptable.

**Verdict.** Flip on GitHub native secret scanning + push protection today (free, server-side,
zero maintenance). Skip gitleaks. Keep trufflehog as a "maybe later," not now.

### 7. actionlint (workflow linter) — **ADOPT**

**What it does.** Static checker for GitHub Actions YAML: syntax, expression type-checking,
action-schema validation, and **shellcheck + pyflakes on `run:` script blocks**. MIT, active
(1.7.12, Mar 2026), first-party pre-commit hook + Docker Action.

**Overlap.** None — nothing in the stack lints workflow YAML or the shell inside it. Directly
relevant given 6 workflow files with nontrivial inline bash (unquoted-var / SIGPIPE-class bugs are
a *documented* pain point in this repo's own CLAUDE.md zsh-capture section — shellcheck catches
that class).

**Verdict.** Adopt. Trivial (one pre-commit hook / one Action), no config, no redundancy, catches
a bug class nothing else here does.

### 8. zizmor (GitHub Actions security auditor) — **ADOPT**

**What it does.** Security-focused Actions auditor: template injection, unpinned/mutable action
refs, impostor commits, typosquatting, ref confusion, excessive permissions, cache poisoning,
trusted-publishing, spoofable `github.actor` checks (39 audits). MIT, active (1.26.1, Jun 2026;
now under the zizmorcore org), first-party pre-commit + Action.

**Overlap (honest).** CodeQL's `actions` query pack (default+extended, 23 queries) *does* cover
injection, cache poisoning, excessive permissions, known-vulnerable actions, and unpinned-tag —
**if** CodeQL's actions language is enabled (the sibling #822 note scoped CodeQL to *Python*;
whether default setup auto-enables `actions` scanning here is **UNVERIFIED** — worth checking
after CodeQL lands). zizmor's *uncovered* territory is the supply-chain-integrity set: impostor
commits, typosquat-uses, ref confusion, stale refs, trusted-publishing, `secrets-inherit` /
overprovisioned secrets. Given third-party actions + a `RELEASE_PAT` tag-push, those checks are
directly on-point.

**Verdict.** Adopt. Overlap with CodeQL-actions is redundant-but-harmless; the supply-chain gaps
are the value driver and map onto this repo's real release surface. Pairs naturally with
actionlint (correctness) as the security half of workflow hygiene.

### 9. codespell (typos) — **ADOPT (light)**

**What it does.** Dictionary-based typo checker for source/comments/docs/strings. Tool is
**GPL-2.0** (dictionary CC-BY-SA-3.0) — stricter than the MIT/Apache rest of the stack; fine as a
dev-only dependency but worth a one-line license note. Active (2.4.2, Mar 2026), native pre-commit
hook.

**Overlap.** None — ruff/pyright/CodeQL don't spellcheck. Real value for an agent-authored repo
where typos in CLI help text / docstrings / error messages slip past logic-focused review. Needs a
D365-jargon ignore list from day one (Dataverse, FetchXML, entity names).

**Verdict.** Adopt as a light pre-commit hook. Low effort, distinct category. (Minor: GPL-2.0
dev-dep + jargon ignore-list upkeep.)

### 10. bandit — **SKIP (subsumed by ruff `S` + CodeQL)**

Ruff's flake8-bandit `S` family (~48 codes, S1xx–S7xx) maps ~1:1 onto bandit's plugin catalog
(B101→S101, B105-107→S105-107, B301-324→S301-324, B601-612→S601-612, …), and CodeQL
security-extended covers the deeper taint cases. Bandit's only non-ported checks are newer/niche
(B613 trojan-source, B614/B615 ML-artifact deserialization) — irrelevant to a CLI that loads no
pickled/model files. (Exact `S`-vs-bandit diff reconstructed from search paraphrase — directional,
UNVERIFIED to the code.) **Verdict: skip; ensure ruff `S` is in `select`.**

### 11. pip-audit — **ADOPT (small; not excluded by the dependabot scope)**

**What it does.** Audits installed/declared deps against the PyPA Advisory DB + OSV for known
CVEs; PyPA-official (Apache-2.0, Trail of Bits), active (2.10.1, Jun 2026), first-party
`pypa/gh-action-pip-audit`. (First-party pre-commit hook UNVERIFIED — many wire it as a `repo:
local` entry.)

**Why it's *not* excluded.** The scope bars **dependabot/renovate** — autonomous PR-opening bots
whose exclusion rationale is agent-PR noise + review burden. pip-audit opens **no PRs**; it's a
point-in-time CI *check* (fail on known-vuln dep), a different category. It's also distinct from
CodeQL (static code taint, not dependency-CVE lookup). It *is* a new CI check, so if "no new CI
gates beyond ruff" is read strictly it could be run report-only (`|| true` / warning) rather than
blocking — that keeps it inside scope either way.

**Verdict.** Adopt, small — the one supply-chain-CVE signal the stack otherwise lacks. Run
report-only if a hard gate is unwanted.

### 12. pyupgrade — **SKIP (fully subsumed by ruff `UP`)**

Ruff's `UP` family *is* pyupgrade (Astral's own positioning: ruff replaces "pyupgrade" among
others; UP036 etc. confirmed). Standalone pyupgrade adds nothing once `UP` is in ruff's `select`
with `target-version` set to py39. **Verdict: skip; enable ruff `UP`.**

---

## Ranked value-for-effort

| # | Candidate | Effort | Value delta over existing stack | Verdict |
|---|-----------|--------|--------------------------------|---------|
| 1 | **GitHub secret scanning + push protection** | ~zero (toggle) | Server-side pre-push block; live gap (both disabled now) | **Adopt now** |
| 2 | **actionlint** | Very low (hook) | Only workflow-YAML + shell-in-run linter; 6 nontrivial workflows | **Adopt** |
| 3 | **zizmor** | Very low (hook/Action) | Supply-chain Actions audits (impostor/typosquat/PAT) uncovered by CodeQL | **Adopt** |
| 4 | **coverage.py XML artifact** | Low (flag + upload) | Report-only coverage visibility, no secret | **Adopt (bare)** |
| 5 | **pip-audit** | Low (Action) | Dependency-CVE gate; not a bot → not scope-excluded | **Adopt (report-only ok)** |
| 6 | **codespell** | Low (hook) | Typo class nothing else catches; agent-authored text | **Adopt (light)** |
| 7 | **semgrep/Opengrep custom rules** | Medium (author rules) | Machine-checks house conventions (dry-run) ruff/CodeQL can't express | **Adopt (small, scoped)** |
| 8 | Codecov free tier | Medium (token+config) | PR-comment/diff-coverage UX over bare XML | Skip unless UX wanted |
| 9 | trufflehog | Medium (AGPL, config) | Live secret verification; GitGuardian paid tier overlaps | Defer |
| 10 | interrogate | Low | Aggregate docstring % — covered by ruff `D1` + CodeRabbit check | Skip |
| 11 | bandit | Low | ~95% = ruff `S`; rest niche | Skip |
| 12 | pyupgrade | Low | = ruff `UP` | Skip |
| 13 | vulture | Medium+ (whitelist tax) | Dead code, but FP model fights Click+lazy imports | Skip (ad-hoc only) |
| 14 | gitleaks | Low | Overlaps GitGuardian; no live verify; maint-mode | Skip |
| 15 | SonarQube Cloud | Medium (platform) | Unique delta = complexity/duplication/gate → **out of scope** | Skip |

---

## Shortlist recommendation

**Adopt now (free, ~zero maintenance, no scope conflict):**

1. **GitHub secret scanning + push protection** — free, server-side, and *currently disabled* on
   this public repo; the only true "flip a switch" win.
2. **actionlint** — the sole linter for the 6 workflow files' YAML and inline bash; catches the
   exact zsh/shell-quoting bug class the repo already documents.
3. **zizmor** — Actions supply-chain audits (impostor commits, typosquatting, PAT/secret
   exposure) that CodeQL's Actions pack doesn't cover; relevant given `RELEASE_PAT` + third-party
   actions.
4. **coverage.py XML artifact** — report-only coverage visibility with no external service, no
   token, no gate (scope-safe).
5. **pip-audit** — the missing dependency-CVE signal; a *check*, not a bot, so it clears the
   dependabot exclusion (run report-only if a hard gate is unwanted).
6. **codespell** — cheap typo net for agent-authored help text/docstrings; nothing else covers it.

Items 2, 3, 6 (and optionally 5) ride the pre-commit adoption already scheduled in #828 — batch
them there. Items 1 and 4 are independent one-liners.

**Adopt scoped, slightly higher effort:**

7. **semgrep or Opengrep, custom rules only** — one local `rules.yml` (start with the dry-run
   contract, 36/60 core modules), tokenless in CI, no registry rules. The only tool that upgrades
   a prose CLAUDE.md convention into a low-noise machine gate — the highest *agent-experience*
   payoff on the list, at an afternoon's authoring cost. Prefer Opengrep if the registry-license
   question is unwelcome; otherwise Semgrep CE (install outside the 3.9 venv).

**Skip — one line each:**

- **SonarQube Cloud** — its only non-overlapping value (complexity/duplication/quality-gate) is
  out of scope; the rest duplicates CodeQL/ruff/pyright and adds PR noise.
- **interrogate** — docstring presence is ruff `D1`, and CodeRabbit already ships an 80% docstring-
  coverage check.
- **bandit** — ~95% subsumed by ruff `S`; the remainder is ML/niche, irrelevant to this CLI.
- **pyupgrade** — it *is* ruff `UP`.
- **vulture** — its documented FP model (decorators, dynamic/lazy access) is precisely this repo's
  architecture; ad-hoc at `--min-confidence 100` only, never a standing gate.
- **gitleaks** — overlaps GitGuardian, pattern-only, upstream in maintenance mode.
- **Codecov / trufflehog** — deferred, not skipped: adopt only if the diff-coverage UX
  (Codecov) or live-verification FP reduction (trufflehog) is specifically wanted, each with a
  real cost (token+config / AGPL-3.0).

---

## Excluded by map scope (assessed, not shortlistable)

- **radon / xenon (complexity metrics)** — radon computes cyclomatic complexity, maintainability
  index, and Halstead metrics; xenon fails CI on complexity thresholds. Both are squarely
  **complexity metrics**, explicitly OUT OF SCOPE per map #819. Even setting scope aside, xenon is
  a threshold *gate* (also excluded) and the report-only radon numbers would duplicate the one
  distinctive thing SonarQube Cloud offers — neither is shortlistable here.
- **SonarQube Cloud's distinctive delta (cognitive complexity + duplication + maintainability
  rating + quality gate)** — hits the same complexity-metrics / coverage-gate scope wall; the
  tool is assessed above and skipped.
- **Codecov / any coverage `--fail-under`** — coverage *thresholds* are out of scope; coverage is
  adopted **report-only** (bare XML) instead.
- **dependabot / renovate** — out of scope (auto-PR bots). Note pip-audit is a *different
  category* (a check, no PRs) and is **not** excluded by this.
- **Mutation testing (mutmut/cosmic-ray), new CI gates beyond ruff, branch-protection
  restructuring** — out of scope; not assessed.

---

## Sources

**Repo (verified locally, read-only):** `.github/workflows/{ci,release,semantic-release,docs,e2e,bump-guard}.yml`,
`pyproject.toml`, `pyrightconfig.json`, `setup.py`, `crm/core/*` (`grep` dry-run spread 36/60);
`gh api repos/Gharib89/crm` (secret_scanning + push_protection both `disabled`, public repo).

**semgrep / Opengrep:** [semgrep/semgrep LICENSE (LGPL-2.1)](https://github.com/semgrep/semgrep/blob/develop/LICENSE) · [Semgrep Rules License v1.0](https://semgrep.dev/legal/rules-license/) · [Dec-2024 update blog](https://semgrep.dev/blog/2024/important-updates-to-semgrep-oss/) · [docs.semgrep.dev/licensing](https://docs.semgrep.dev/licensing) · [rule syntax](https://docs.semgrep.dev/writing-rules/rule-syntax) · [CLI reference](https://docs.semgrep.dev/cli-reference) · [returntocorp/semgrep-action (deprecated)](https://github.com/semgrep/semgrep-action) · [opengrep/opengrep](https://github.com/opengrep/opengrep) + [releases](https://github.com/opengrep/opengrep/releases) · [ruff has no custom rules — astral-sh/ruff#8409](https://github.com/astral-sh/ruff/discussions/8409)

**SonarQube Cloud:** [subscription plans](https://docs.sonarsource.com/sonarqube-cloud/administering-sonarcloud/managing-subscription/subscription-plans) · [open-source editions](https://www.sonarsource.com/open-source-editions/) · [GitHub Marketplace listing](https://github.com/marketplace/sonarcloud) · [Python analysis](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/languages/python) · [rules taxonomy](https://docs.sonarsource.com/sonarqube-cloud/standards/managing-rules/rules) · [S3776 cognitive complexity](https://github.com/SonarSource/sonar-python/blob/master/python-checks/src/main/resources/org/sonar/l10n/py/rules/python/S3776.html) · [quality gates](https://docs.sonarsource.com/sonarqube-cloud/standards/managing-quality-gates/introduction-to-quality-gates) · [automatic analysis](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/automatic-analysis) · [GitHub binding (status check)](https://docs.sonarsource.com/sonarqube-cloud/managing-your-projects/administering-your-projects/devops-platform-integration/github)

**Secret scanning:** [GitHub secret scanning (free, public)](https://docs.github.com/en/code-security/secret-scanning/about-secret-scanning) · [push protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection) · [default-on 2024-03-11](https://github.blog/changelog/2024-03-11-secret-scanning-and-push-protection-are-enabled-by-default-on-new-public-repositories/) · [GitGuardian ggshield](https://github.com/GitGuardian/ggshield) · [ggshield pre-receive (GHES only)](https://docs.gitguardian.com/ggshield-docs/integrations/git-hooks/pre-receive) · [gitleaks (MIT)](https://github.com/gitleaks/gitleaks) · [trufflehog (AGPL-3.0)](https://github.com/trufflesecurity/trufflehog)

**Python quality cluster:** [interrogate](https://github.com/econchick/interrogate) · ruff pydocstyle [D100](https://docs.astral.sh/ruff/rules/undocumented-public-module/)/[D107](https://docs.astral.sh/ruff/rules/undocumented-public-init/) · [vulture](https://github.com/jendrikseipp/vulture) · [codespell](https://github.com/codespell-project/codespell) · [bandit](https://github.com/PyCQA/bandit) + [plugins](https://bandit.readthedocs.io/en/latest/plugins/index.html) · [ruff flake8-bandit S](https://docs.astral.sh/ruff/rules/#flake8-bandit-s) · [pip-audit (PyPA)](https://github.com/pypa/pip-audit) · [actionlint](https://github.com/rhysd/actionlint) · [zizmor audits](https://docs.zizmor.sh/audits/) + [integrations](https://docs.zizmor.sh/integrations/) · [CodeQL Actions built-in queries](https://docs.github.com/en/code-security/code-scanning/managing-your-code-scanning-configuration/actions-built-in-queries) · [ruff UP036](https://docs.astral.sh/ruff/rules/outdated-version-block/)

**Coverage:** [pytest-cov reporting](https://pytest-cov.readthedocs.io/en/latest/reporting.html) + [config](https://pytest-cov.readthedocs.io/en/latest/config.html) · [coverage.py xml](https://coverage.readthedocs.io/en/latest/commands/cmd_xml.html) · [Codecov pricing](https://about.codecov.io/pricing/) · [Codecov tokens (public-repo requirement)](https://docs.codecov.com/docs/codecov-tokens) · [Codecov commit-status informational](https://docs.codecov.com/docs/commit-status) · [CodeRabbit pre-merge checks (docstring coverage)](https://docs.coderabbit.ai/pr-reviews/pre-merge-checks)
