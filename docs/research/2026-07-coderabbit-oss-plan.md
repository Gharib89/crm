# CodeRabbit free Pro-for-open-source plan — eligibility for Gharib89/crm and PR-level product surface

**Purpose:** primary-source facts on whether the public repo `Gharib89/crm` qualifies for
CodeRabbit's free open-source plan, and what the product actually does at PR level — config
surface, `@coderabbitai` commands, local CLI / Claude Code plugin mechanics, and gotchas for
agent-driven PR workflows. Feeds a keep-or-adopt decision (and a keep-or-uninstall decision on
the locally installed `coderabbit` Claude Code plugin).
**Date:** 2026-07-11.
**Related:** issue [Gharib89/crm#820](https://github.com/Gharib89/crm/issues/820); wayfinder map
[#819](https://github.com/Gharib89/crm/issues/819).

Sources are official only: docs.coderabbit.ai, coderabbit.ai (pricing/FAQ/blog/changelog), the
`coderabbitai` GitHub org, and — for plugin internals — the locally installed plugin files under
`~/.claude/plugins/`. No secondary write-ups. Anything that could not be pinned to a primary
source is flagged **UNVERIFIED** inline. Fetch caveat: several docs.coderabbit.ai pages
(`/getting-started/configure-coderabbit`, `/reference/yaml-template`, `/cli/overview`,
`/cli/commands`, `/about/pricing`) intermittently returned an "Access Restricted" bot-challenge
wall to the fetch proxy; every fact below was confirmed via a page that did fetch cleanly, and
the few that could not be are flagged.

---

## 1. OSS plan terms — verdict: eligible, automatic, Pro+ features, OSS-tier rate limits

**Eligibility.** The pricing page and marketing FAQ carry identical wording:
"Sign up for CodeRabbit using GitHub or GitLab, install CodeRabbit on a public repository, and
receive free reviews forever for public repositories"
([coderabbit.ai/pricing](https://www.coderabbit.ai/pricing),
[coderabbit.ai/faq](https://www.coderabbit.ai/faq)). Eligibility is conditioned purely on
**repository visibility** — public is enough; no application, review, or waiting period is
mentioned for the grant itself (it reads as automatic/self-serve).

- **Personal account vs organization:** no primary source distinguishes account type; the
  wording conditions only on "public repository," so `Gharib89/crm` (public, personal account)
  appears to qualify on the same terms — but the personal-vs-org question is **UNVERIFIED**
  (no source addresses it either way).
- Do not confuse this with CodeRabbit's separate **OSS sponsorship/funding program** (the
  $1M maintainer-funding commitment) — that one *is* application-based and is a distinct
  initiative ([blog: coderabbit-commits-1-million-to-open-source](https://www.coderabbit.ai/blog/coderabbit-commits-1-million-to-open-source)).

**What's included.** The plans doc states: "Open-source projects receive **Pro+ features** with
no paid subscription required" ([docs.coderabbit.ai/management/plans](https://docs.coderabbit.ai/management/plans))
— i.e. the *higher* paid tier's feature set, not just Pro. No feature-by-feature exclusion list
is documented (**UNVERIFIED** whether any Pro+ feature is carved out); the only documented
OSS-vs-paid difference is the rate-limit tier below.

**Rate limits** (per developer, per hour, from
[docs.coderabbit.ai/management/plans](https://docs.coderabbit.ai/management/plans); identical
across two independent fetches, but the raw HTML table could not be hand-verified — the
`/about/pricing` page was access-walled):

| Plan | PR reviews/hr | IDE reviews/hr | CLI reviews/hr | Files/review | Chat/hr |
|------|----|----|-----|----|----|
| Free | 1 | 3 | 3 | 150 | — |
| **OSS** | **1–10** (varies by project community/popularity) | 1 | 3 | 150 | 25 |
| Pro | 5 | 5 | 5 | 300 | 50 |
| Pro+ | 10 | 10 | 10 | 300 | 100 |
| Enterprise | 12 | 12 | 12 | 300 | 100 |

Limits are a **rolling allowance**, not a fixed hourly reset: "PR, IDE, and CLI review limits
are shown per developer, per hour. They work as a rolling allowance" — capacity refills as
older reviews age out ([docs.coderabbit.ai/management/plans](https://docs.coderabbit.ai/management/plans)).
"There is no limit on the number of pull requests reviewed or the number of repositories on any
of the plans" — the constraint is the hourly rate, not a count cap
([coderabbit.ai/pricing](https://www.coderabbit.ai/pricing)).

**Free plan (private repos), for contrast:** unlimited public and private repos, no credit
card; new signups get a 14-day Pro+ trial; outside the trial, Free on private repos covers
PR **summarization** plus reviews in IDE/CLI only — automatic inline PR review (the core Pro
feature) is not included ([coderabbit.ai/pricing](https://www.coderabbit.ai/pricing),
[docs.coderabbit.ai/management/plans](https://docs.coderabbit.ai/management/plans)).

---

## 2. `.coderabbit.yaml` config surface

Reference: [docs.coderabbit.ai/reference/configuration](https://docs.coderabbit.ai/reference/configuration);
live JSON Schema at [coderabbit.ai/integrations/schema.v2.json](https://coderabbit.ai/integrations/schema.v2.json)
(draft 2020-12; cross-checked against the reference page).

- **File location:** must be named `.coderabbit.yaml` and "must be located in the root of the
  repository" ([docs.coderabbit.ai/getting-started/yaml-configuration](https://docs.coderabbit.ai/getting-started/yaml-configuration)).
  A `.coderabbit.yml` alternative is **not documented** (UNVERIFIED, likely unsupported). The
  copy on the **PR's feature branch** is the one used for that review, not the default branch's.
- **Precedence:** repo YAML beats UI settings — hierarchy is repo YAML > repo UI > org UI
  ([docs.coderabbit.ai/configuration/configuration-inheritance](https://docs.coderabbit.ai/configuration/configuration-inheritance)).
  `@coderabbitai configuration` on a PR prints the fully-resolved YAML annotated with the source
  of each setting (same page).
- **`reviews.profile`** — enum of **three** values, not two: `quiet` | `chill` (default) |
  `assertive`. Docs: "quiet for only the most important feedback, chill for balanced feedback,
  assertive for more feedback (which may feel nitpicky)"
  ([reference/configuration](https://docs.coderabbit.ai/reference/configuration)).
- **`reviews.path_instructions`** — array of `{path: <glob>, instructions: <text, max 20,000
  chars>}`; "Add path-specific guidance for code review" (same page).
- **`reviews.path_filters`** — array of glob strings, `!` prefix excludes (e.g. `!dist/**`);
  also applied to CodeRabbit's `git sparse-checkout` (same page).
- **`tone_instructions`** — top-level string, **max 250 characters**, customizes review/chat
  tone (same page).
- **`reviews.auto_review`** keys and defaults (reference page + schema.v2.json):
  `enabled: true`; `drafts: false` (**draft PRs are not auto-reviewed by default**);
  `base_branches: []` (only the default branch is auto-reviewed unless regex patterns are
  added); `ignore_title_keywords: []`; `labels: []` (`!` prefix = exclusion);
  `description_keyword: ""`; `auto_incremental_review: true` (re-review on each push);
  `ignore_usernames: []` (skip PRs from listed authors — see §5).
- **Tools:** catalog at [docs.coderabbit.ai/tools/list](https://docs.coderabbit.ai/tools/list)
  ("50+" third-party linters/security tools per [docs.coderabbit.ai/tools](https://docs.coderabbit.ai/tools)).
  **Ruff is supported** — dedicated page [docs.coderabbit.ai/tools/ruff](https://docs.coderabbit.ai/tools/ruff):
  CodeRabbit runs a pinned Ruff version on `.py`/`.ipynb`, and **skips running it if Ruff
  already runs in the repo's GitHub Actions workflows**. Chill profile enables rule families
  F, B, S (flake8-bandit), BLE, T10, EXE, RUF, PLE, A, selected pycodestyle errors; assertive
  adds ANN, ASYNC, FBT, C4, DTZ, DJ, ISC, LOG, G, PIE, PT, FLY, UP, PLR, SIM, PERF, RET, ARG,
  TRY, PLW. Other Python tools in the catalog: **Pylint** and **Flake8**
  ([tools/list](https://docs.coderabbit.ai/tools/list)). **mypy is not listed** anywhere in the
  catalog (no type-checker integration documented); **Bandit** has no standalone integration —
  its rules arrive via Ruff's flake8-bandit (S) codes. Multipurpose/security tools that also
  touch Python: ast-grep, Semgrep/OpenGrep, OSV-Scanner, TruffleHog, Checkov, Trivy, Presidio
  Analyzer (same catalog page).
- **Schema/validation:** the schema file exists and matches the reference page (fetched
  directly from [coderabbit.ai/integrations/schema.v2.json](https://coderabbit.ai/integrations/schema.v2.json)).
  The documented `# yaml-language-server: $schema=...` editor line lives on the
  `/reference/yaml-template` page, which was access-walled — that exact snippet is
  **UNVERIFIED** against the primary source, though the schema URL itself is confirmed live.

---

## 3. PR interaction model

Command reference: [docs.coderabbit.ai/reference/review-commands](https://docs.coderabbit.ai/reference/review-commands)
(all commands below from that page unless noted).

| Command | Effect |
|---|---|
| `@coderabbitai review` | Incremental review of new changes only (also breaks an auto-pause) |
| `@coderabbitai full review` | Complete re-review of **all** files from scratch, discarding prior context |
| `@coderabbitai pause` / `resume` | Stop / restart automatic reviews on this PR |
| `@coderabbitai ignore` | In the **PR description** (not a comment): permanently disables auto-review while the text remains ([guides/commands](https://docs.coderabbit.ai/guides/commands)) |
| `@coderabbitai summary` | Placeholder token in the PR description — CodeRabbit replaces it in place (controls summary placement) |
| `@coderabbitai generate docstrings` / `generate unit tests` | Generation "finishing touches" for the PR |
| `@coderabbitai autofix` / `autofix stacked pr` | Applies fixes for unresolved CodeRabbit findings (in-branch or as a stacked PR) |
| `@coderabbitai generate sequence diagram` | Sequence diagram of the PR's changes |
| `@coderabbitai resolve` | Marks **all** CodeRabbit review comments resolved |
| `@coderabbitai approve` | Resolves all CodeRabbit threads and submits approval — top-level comment only, requires `reviews.request_changes_workflow: true` |
| `@coderabbitai configuration` / `generate configuration` / `emit path instructions` | Print resolved config / PR it in as `.coderabbit.yaml` / PR in suggested path instructions |
| `@coderabbitai help` | Command quick reference |
| `@coderabbitai rate limit` / `reviews remaining?` | Check remaining quota without consuming a review ([docs.coderabbit.ai/faq](https://docs.coderabbit.ai/faq)) |
| `@coderabbitai run <recipe>` | Run a user-defined custom recipe (up to 5 in `.coderabbit.yaml`) ([reference/configuration](https://docs.coderabbit.ai/reference/configuration)) |

There is **no** `@coderabbitai plan` PR command — "CodeRabbit Plan" is a separate standalone
product (issue-to-coding-plan), not part of the PR command surface
([coderabbit.ai/plan](https://www.coderabbit.ai/plan)).

**Re-review on push:** with `auto_incremental_review: true` (default), CodeRabbit re-reviews
after every push, scoped to commits added since the last review. A noise gate,
`auto_pause_after_reviewed_commits` (default **5**), auto-pauses incremental review after that
many reviewed commits; `0` disables the pause, and `@coderabbitai review` lifts it
([docs.coderabbit.ai/configuration/auto-review](https://docs.coderabbit.ai/configuration/auto-review)).

**Thread resolution:** the only documented resolution mechanisms are the manual `resolve` and
`approve` commands. **UNVERIFIED / not documented:** whether CodeRabbit auto-resolves its own
threads when it detects the code was fixed in a later commit — no primary source states this.

**Chat/replies:** mentioning `@coderabbitai` in any PR comment starts a conversation; docs
recommend replying **on the specific review-comment thread** (e.g. "I pushed a fix in commit
`<id>`, please review it") for focused discussion
([docs.coderabbit.ai/guide/chat](https://docs.coderabbit.ai/guide/chat)). Whether such a reply
triggers automatic re-verification of that thread is **not documented** (UNVERIFIED).

---

## 4. Local CLI and the installed Claude Code plugin

### CodeRabbit CLI

- Install: `curl -fsSL https://cli.coderabbit.ai/install.sh | sh` or `brew install coderabbit`;
  binary `coderabbit` with shorthand alias `cr`; macOS, Linux, Windows-via-WSL
  ([docs.coderabbit.ai/cli](https://docs.coderabbit.ai/cli),
  [coderabbit.ai/cli](https://www.coderabbit.ai/cli)).
- Auth: `coderabbit auth login` (browser flow); headless/CI via
  `coderabbit auth login --api-key "cr-…"`. Requires a CodeRabbit account; without org access
  it falls back to free-tier limits for public repos
  ([docs.coderabbit.ai/cli](https://docs.coderabbit.ai/cli)).
- CLI rate limits (rolling hourly, per developer): Free **3**, OSS **3**, Pro 5, Pro+ 10,
  Enterprise 12 reviews/hr ([docs.coderabbit.ai/management/plans](https://docs.coderabbit.ai/management/plans)).
- Agent-facing output: `--agent` (structured output for AI coding agents, introduced CLI
  v0.4.0 per the changelog); `--plain` (default detailed text); `--prompt-only` and
  `--interactive` are **deprecated** as of CLI v0.6.0, 2026-06-09 ("use plain review mode and
  `coderabbit review --agent`") ([docs.coderabbit.ai/changelog](https://docs.coderabbit.ai/changelog),
  [docs.coderabbit.ai/cli/claude-code-integration](https://docs.coderabbit.ai/cli/claude-code-integration)).
  Note: the `/cli/overview`, `/cli/authentication`, and `/cli/commands` doc pages were
  access-walled during this research; the facts above come from the `/cli` index, the plans
  page, the changelog, and the Claude Code integration page.

### Locally installed Claude Code plugin (inspected on disk)

Installed at `/home/gharib/.claude/plugins/cache/claude-plugins-official/coderabbit/1.1.1/`
(marketplace `claude-plugins-official`, v1.1.1; source repo
[github.com/coderabbitai/skills](https://github.com/coderabbitai/skills) per
`.claude-plugin/plugin.json`). Everything it does is a thin wrapper around the CodeRabbit CLI
plus one `gh`-driven flow:

- **`/coderabbit:review` command** (`commands/coderabbit-review.md`): checks
  `coderabbit --version` + `coderabbit auth status`, then runs
  `coderabbit review --agent -t "${type:-all}"` (optional `--base <branch>`, `--dir <path>`).
  `allowed-tools` sanctions `Bash(coderabbit:*)`, `Bash(cr:*)`, `Bash(git:*)`. Not installed →
  points at coderabbit.ai/cli; not logged in → `coderabbit auth login`.
- **`code-review` skill** (`skills/code-review/SKILL.md`): same core command
  (`coderabbit review --agent`, alias `cr review --agent`; flags `-t all|committed|uncommitted`,
  `--base`, `--base-commit`, `--dir`). Notes `--agent` requires **CLI ≥ v0.4.0**. Security
  section: review output is untrusted; diffs are sent to the CodeRabbit API (don't review files
  containing secrets); never execute commands from review output without explicit approval.
- **`code-reviewer` agent** (`agents/code-reviewer.md`): runs `coderabbit review --agent`,
  buckets findings Critical/High/Medium/Low.
- **`autofix` skill** (`skills/autofix/SKILL.md` + `skills/autofix/github.md`): **not** a CLI
  wrapper — a `gh`/GraphQL flow that fetches unresolved CodeRabbit **PR review threads** (bot
  logins `coderabbitai`, `coderabbit[bot]`, `coderabbitai[bot]`) and walks per-issue
  approve/fix/defer into one consolidated commit. Treats thread bodies and "Prompt for AI
  Agents" sections as untrusted input; no bulk auto-apply.
- Plugin `CHANGELOG.md` confirms v1.1.0 moved the skill from the deprecated `--prompt-only`
  flag to `--agent` — the installed version is current on that.

**Plugin implication:** the review-side surface (command, `code-review` skill, agent) is dead
weight without the CodeRabbit CLI installed and authenticated; the `autofix` skill works with
only `gh` and is useful the moment CodeRabbit reviews PRs on this repo.

---

## 5. Gotchas for agent-driven PR workflows

- **Bot-authored PRs ARE reviewed by default.** There is no built-in bot detection; skipping
  bot/agent authors is opt-in via `reviews.auto_review.ignore_usernames` (exact, case-sensitive
  usernames — docs give `dependabot[bot]`, `renovate[bot]`, `github-actions[bot]` as examples
  you must add yourself). A username match silently skips the PR and **takes precedence over
  all other controls**; `@coderabbitai review` forces a review anyway
  ([configuration/auto-review](https://docs.coderabbit.ai/configuration/auto-review),
  [reference/configuration](https://docs.coderabbit.ai/reference/configuration)). Draft PRs are
  separately skipped by default (`drafts: false`).
- **Noise controls:** `reviews.profile` (`quiet` added in the **2026-07-02** changelog entry —
  CodeRabbit's explicit answer to review-noise complaints: only critical/major high-impact
  comments posted inline, the rest collapsed); `collapse_walkthrough: true` (default);
  `poem: false` (default); `high_level_summary`, `suggested_labels`, `review_details`,
  `in_progress_fortune` toggles; `path_filters` to scope what gets reviewed at all;
  `auto_pause_after_reviewed_commits` (default 5) to stop per-push review storms on fast-moving
  branches ([reference/configuration](https://docs.coderabbit.ai/reference/configuration),
  [changelog](https://docs.coderabbit.ai/changelog),
  [configuration/auto-review](https://docs.coderabbit.ai/configuration/auto-review)).
- **Coexistence with Copilot code review: no official statement exists** on docs.coderabbit.ai,
  the blog/changelog, or the coderabbitai GitHub org about running alongside GitHub Copilot
  code review (or any other review bot) on the same PR — **UNVERIFIED / open gap**. For this
  repo that means the Copilot auto-review ruleset and CodeRabbit would both fire on every
  non-draft PR unless one is scoped down.
- **Burst behavior:** when the hourly cap is hit, reviews are **queued and processed as
  capacity frees up** — not dropped; "never charged for overages." Throttling engages
  gradually ("when one developer identity reaches the 95th percentile or higher of recent…
  usage, CodeRabbit gradually spaces out additional reviews"). Check standing without spending
  a review via `@coderabbitai rate limit` ([docs.coderabbit.ai/faq](https://docs.coderabbit.ai/faq)).
  For this repo's burst pattern (cloud-ship routines opening several PRs), the OSS tier's
  1–10 PR reviews/hr (popularity-dependent) is the binding constraint — small repos should
  assume the low end.
- **Review-skip visibility:** `reviews.review_status` (default `true`) posts a status note in
  the walkthrough whenever a review is skipped for any reason (draft, ignored author, base
  branch mismatch) — useful signal when debugging "why didn't it review"
  ([configuration/auto-review](https://docs.coderabbit.ai/configuration/auto-review)).

---

## Bottom line (input to #820 — not a decision)

`Gharib89/crm` qualifies: public repo → free "Pro+ features" automatically, no application,
with OSS-tier rolling rate limits (1–10 PR reviews/hr, 150 files/review). Ruff is a supported
tool and auto-defers to the repo's own Ruff CI if present; mypy is not integrated. Agent-opened
PRs are reviewed like any others (no bot exemption by default), drafts are skipped by default,
and noise is controllable (`quiet`/`chill` profiles, auto-pause after 5 reviewed commits).
Nothing official covers coexistence with Copilot code review — running both on every PR is the
default outcome and would need deliberate scoping. The installed Claude Code plugin is inert
without the CodeRabbit CLI (`coderabbit auth login`, `coderabbit review --agent`); only its
`autofix` skill (gh-driven PR-thread triage) works standalone.

## Sources

- https://www.coderabbit.ai/pricing — OSS eligibility wording, free-plan terms, no-repo-cap FAQ
- https://www.coderabbit.ai/faq — same eligibility wording
- https://docs.coderabbit.ai/management/plans — "Pro+ features" for OSS, rate-limit table
- https://www.coderabbit.ai/blog/coderabbit-commits-1-million-to-open-source — separate funding program
- https://docs.coderabbit.ai/reference/configuration — profiles, path_instructions/filters, tone, auto_review, volume toggles, custom recipes
- https://coderabbit.ai/integrations/schema.v2.json — live JSON Schema (draft 2020-12)
- https://docs.coderabbit.ai/getting-started/yaml-configuration — filename/root-only rule, feature-branch config
- https://docs.coderabbit.ai/configuration/configuration-inheritance — precedence, `@coderabbitai configuration`
- https://docs.coderabbit.ai/tools , https://docs.coderabbit.ai/tools/list , https://docs.coderabbit.ai/tools/ruff — tool catalog, Ruff behavior
- https://docs.coderabbit.ai/reference/review-commands — full PR command list
- https://docs.coderabbit.ai/configuration/auto-review — incremental review, auto-pause, ignore_usernames, review_status
- https://docs.coderabbit.ai/guides/commands — `@coderabbitai ignore` in description
- https://docs.coderabbit.ai/guide/chat — reply/chat behavior
- https://docs.coderabbit.ai/faq — rate-limit commands, queueing/burst behavior
- https://docs.coderabbit.ai/cli , https://www.coderabbit.ai/cli — CLI install, binaries, auth
- https://docs.coderabbit.ai/cli/claude-code-integration — `--agent` mode for agents
- https://docs.coderabbit.ai/changelog — `--agent` v0.4.0, `--prompt-only` deprecation v0.6.0, quiet profile 2026-07-02, rate-limit visibility 2026-04-28
- https://www.coderabbit.ai/plan — "CodeRabbit Plan" is a separate product, not a PR command
- Local plugin files: `/home/gharib/.claude/plugins/cache/claude-plugins-official/coderabbit/1.1.1/` (`.claude-plugin/plugin.json`, `commands/coderabbit-review.md`, `agents/code-reviewer.md`, `skills/code-review/SKILL.md`, `skills/autofix/SKILL.md`, `skills/autofix/github.md`, `README.md`, `CHANGELOG.md`)
