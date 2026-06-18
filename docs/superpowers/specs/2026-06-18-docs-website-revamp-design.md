# Docs website revamp — design

**Date:** 2026-06-18
**Status:** Approved (brainstorming)
**Scope:** Documentation site only (`docs/`, `mkdocs.yml`, `docs/stylesheets/extra.css`). No CLI code changes.

## Goal

Make the docs site (`https://crm-cli-docs.pages.dev/`) approachable for first-time users and more attractive on landing. Two focuses:

1. A beginner-friendly **Getting started** section that walks the real first-run journey end to end.
2. A **home page** that pitches the product and looks like a product page, not a link list.

Everything is built from Material for MkDocs primitives and the existing ITWorx indigo/red brand (`docs/stylesheets/extra.css`). No new theme, no custom JS, no PyPI/SEO work.

## Constraints (from CLAUDE.md)

- **Docs-in-sync rule:** Getting-started pages state the *workflow*; the `how-to/*` pages remain the source of truth for *flags/choices/defaults*. Getting-started must **not** restate flag tables — it links down. This keeps the skill-sync gate happy and avoids drift.
- **No content deleted.** `how-to/self-update.md`, `how-to/completion.md`, `how-to/skill.md` stay as the full flag reference; the new getting-started pages are beginner-framed entry points that link to them.
- **`mkdocs build --strict` must pass** (CI gate). Every internal link must resolve; no orphaned nav entries.
- Facts are drawn from existing `docs/`, `README.md`, and `crm/skills/SKILL.md` — no behavior invented. Doc claims are factual and must match `--help` / code.

## Section 1 — Information architecture

New `nav` for Getting started (replaces the current 3-entry block). Order mirrors a real first run:

```
Home
Getting started
  ├─ Quickstart                 (NEW)
  ├─ Concepts                   (NEW)
  ├─ Install                    (rework of getting-started/install.md)
  ├─ Update                     (NEW; beginner framing of how-to/self-update)
  ├─ Add a profile              (rework of getting-started/initialize.md)
  ├─ Configure & switch         (rework of getting-started/configure.md)
  ├─ Verify it works            (NEW)
  ├─ Tab completion (optional)  (NEW; beginner framing of how-to/completion)
  ├─ Install the skill          (NEW; beginner framing of how-to/skill)
  ├─ Use /crm with an agent     (NEW)
  └─ Troubleshooting            (NEW)
Guides / How-to / Reference / Contributing   (unchanged)
```

File mapping under `docs/getting-started/`:

| Page | File | Source material |
|------|------|-----------------|
| Quickstart | `quickstart.md` | install.md + initialize.md (happy path only) |
| Concepts | `concepts.md` | configure.md + SKILL.md (synthesised) |
| Install | `install.md` | existing (rework) |
| Update | `update.md` | how-to/self-update.md |
| Add a profile | `add-profile.md` (rename of `initialize.md`) | existing initialize.md |
| Configure & switch | `configure.md` | existing (extend) |
| Verify it works | `verify.md` | how-to/connection.md |
| Tab completion | `completion.md` | how-to/completion.md |
| Install the skill | `skill.md` | how-to/skill.md |
| Use /crm with an agent | `agent.md` | SKILL.md |
| Troubleshooting | `troubleshooting.md` | install.md + configure.md + scattered notes |

> Note on file renames: `initialize.md` → `add-profile.md`. Grep the whole tree for inbound links to `getting-started/initialize.md` / `getting-started/configure.md` before renaming and fix every one (`mkdocs --strict` will fail otherwise). `llmstxt` plugin section list in `mkdocs.yml` also references these paths — update it.

## Section 2 — Home page (`docs/index.md`)

Direction A (approved): hero + feature-card grid + first-taste code. Built from Material primitives only.

1. **Hero** — centered. Logo image, `# crm`, tagline: *"Drive Dynamics 365 CE from your shell — on-prem (NTLM) or Dataverse (OAuth), one CLI."* Two Material buttons (`.md-button`): **Get started** → `getting-started/quickstart.md` (primary, `.md-button--primary`), **View on GitHub** → repo (ghost).
2. **Feature card grid** — Material `grid cards` extension, 6 cards, each linking to its how-to:
   - Records — CRUD + bulk → `how-to/entity.md`
   - Queries — OData / FetchXML → `how-to/query.md`
   - Solutions — full lifecycle → `how-to/solution.md`
   - Metadata — read + write → `how-to/metadata.md`
   - Plugins — assemblies & steps → `how-to/plugin.md`
   - `/crm` agent skill → `getting-started/agent.md`
3. **First taste** — one fenced bash block with `content.code.annotate` annotations: `crm profile add` then `crm query whoami --json`.
4. **Why crm** — 4 bullets from README "Why": `--json` everywhere; `--dry-run` previews mutations; same commands hit on-prem and cloud; optional metadata cache for repeated agent calls.
5. **For agents** — short strip linking `/llms.txt` and `/llms-full.txt` and the CLI reference.

CSS additions to `extra.css`: hero block centering, button row spacing, logo sizing. Material's `grid cards` styling is used as-is.

Markdown extensions this needs that are **not** currently in `mkdocs.yml` (verified against the current config — present: `admonition`, `pymdownx.details`, `pymdownx.superfences`, `pymdownx.highlight`, `tables`, `mkdocs-click`):

- `attr_list` — for `.md-button` / `.md-button--primary` button classes and the card-list `grid cards` form.
- `md_in_html` — for the `<div class="grid cards" markdown>` HTML-in-markdown form.
- `pymdownx.tabbed` (with `alternate_style: true`) — for the Windows/Linux `content.tabs` blocks in Quickstart and Install. The theme already enables the `content.tabs.link` *feature*, but the rendering *extension* is absent, so tab syntax would render as plain text without it.

## Section 3 — Getting-started page contents

Each page is short, single-purpose, beginner-framed. Content is drawn from the cited sources; nothing invented.

- **Quickstart** — 4 numbered steps, copy-paste, with `content.tabs` for Windows / Linux: (1) install one-liner, (2) open a new shell, (3) `crm profile add` (wizard), (4) `crm query whoami` to confirm. Ends with "Next:" links to Install the skill and the How-to index.
- **Concepts** — prose + one table contrasting **on-prem (NTLM)** vs **cloud / Dataverse (OAuth)** (the URL shape decides: `*.dynamics.com` → OAuth, else NTLM). Defines a **profile** (URL + auth scheme + identity + secret; stored under `~/.crm/`, relocatable via `CRM_HOME`) and **solution** + **publisher prefix** (why metadata-write commands need them). No flags.
- **Install** — keep the current content (install script, `uv tool install`, from source, integrity verification). Add `content.tabs` Windows/Linux framing and a closing "verify: `crm --version`". Minimal change.
- **Update** — beginner framing of `crm self-update --check` and `crm self-update`; note that pip/uv/source installs are pointed at `pip install -U` instead of an in-place swap; mention the passive once-per-24h update notice and the `CRM_NO_UPDATE_CHECK` opt-out. Links to `how-to/self-update.md` for the `data.skills` per-destination detail.
- **Add a profile** — the interactive wizard (from `initialize.md`), then the non-interactive flag form; both NTLM and OAuth examples. Note the auto-launch-wizard-on-first-connection behavior.
- **Configure & switch** — NTLM vs OAuth field reference (from `configure.md`), `--default-solution` / `--publisher-prefix`, secret-resolution order, plus day-2 verbs `crm profile use | list | set-password`. Links to `how-to/profile.md`.
- **Verify it works** — `crm connection whoami`, `crm connection test`, `crm connection doctor`; what good output looks like; one failing example that points to Troubleshooting.
- **Tab completion (optional)** — per-shell install (`crm completion install --shell …`), the single `source` line to add, the "cached file, not `eval`" note. Links to `how-to/completion.md`.
- **Install the skill** — `crm skill install --target claude|copilot|cursor`, where the tree lands, and that `crm self-update` keeps it in sync. Links to `how-to/skill.md`.
- **Use /crm with an agent** — the payoff page. Steps: install the skill → open a skill-aware agent (Claude Code / Copilot CLI) → it triggers on D365/Dataverse/FetchXML phrasing. Then a **static text transcript** styled as a terminal block: user asks "list the top 5 accounts", agent runs `crm query account --top 5 --json`, result shown. Closes by explaining the `--json` contract the agent relies on.
- **Troubleshooting** — a problem→fix table:
  - Unsigned/blocked binary (SmartScreen / Defender ASR / AppLocker) → use `uv tool install`.
  - On-prem unreachable / hangs → VPN down (any HTTP response, incl. 401/403, counts as reachable).
  - Secret not in keyring → automatic `0600` plaintext fallback on WSL/headless; `--store-password-plaintext` to force.
  - `WhoAmI` 401/403 → wrong identity or missing application user / security role.
  - v9.2 returns HTTP 501 on-prem → CLI auto-steps-down to v9.1; omit `--api-version`.

## Section 4 — `mkdocs.yml` changes

- Replace the `Getting started` `nav` block with the 12-entry structure above.
- Update the `llmstxt` plugin `sections."Getting started"` list to the new file paths (currently references `install.md` / `initialize.md` / `configure.md`).
- Add `attr_list`, `md_in_html`, and `pymdownx.tabbed` (`alternate_style: true`) to `markdown_extensions` (see Section 2 for why each is needed).
- No theme or plugin additions otherwise.

## Out of scope (YAGNI)

- Recorded asciinema/GIF assets (the agent page uses a text transcript; can add a cast later).
- Any CLI code, new commands, or flag changes.
- SEO, analytics, PyPI publishing, versioned docs.
- Restructuring How-to / Reference / Guides / Contributing.

## Verification

- `mkdocs build --strict` passes (no broken links, no orphan nav).
- `git grep -n 'getting-started/initialize\|getting-started/configure'` returns only intended, updated references after the rename.
- Every new page renders and its "Next/links" resolve.
- Manual read-through: a newcomer can go Home → Get started → Quickstart → working `whoami` without leaving Getting started.

## Implementation note

Per CLAUDE.md branch discipline, implement in a git worktree on a `docs/website-revamp` branch (not the shared main checkout), and ship via PR. `mkdocs build --strict` is the local gate before pushing.
