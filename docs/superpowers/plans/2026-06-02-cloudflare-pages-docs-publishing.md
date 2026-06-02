# Cloudflare Pages Docs Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the MkDocs docs site publicly and for free via Cloudflare Pages, without making the private code repo public.

**Architecture:** The existing `docs.yml` CI keeps building the site. On push to `main`, the `deploy` job stops writing the unserved `gh-pages` branch and instead direct-uploads the built `site/` directory to Cloudflare Pages using `cloudflare/wrangler-action`. Cloudflare serves it at `crm-docs.pages.dev`. Only built HTML leaves the repo; markdown source stays private.

**Tech Stack:** GitHub Actions, MkDocs Material, Cloudflare Pages, `cloudflare/wrangler-action@v4` (wrangler 4.97.0).

**Spec:** `docs/superpowers/specs/2026-06-02-cloudflare-pages-docs-publishing-design.md`

---

## File Structure

- `mkdocs.yml` — add `site_url`. No other change (`repo_url` stays per design).
- `.github/workflows/docs.yml` — rewrite the `deploy` job: drop `mkdocs gh-deploy`, add wrangler Pages upload. The `build` job is untouched.

No new files. No source code changes.

---

## Task 0: Cloudflare prerequisites (MAINTAINER-ONLY — cannot be automated)

These require Cloudflare credentials and GitHub repo admin. An AFK agent **cannot** do these; they block live verification (Task 4) but **not** the code changes (Tasks 1–3). Do these in parallel with the code tasks.

- [ ] **Step 1: Create a Cloudflare account** (if none) at https://dash.cloudflare.com/sign-up.

- [ ] **Step 2: Create the Pages project named `crm-docs`.**

Either via dashboard (Workers & Pages → Create → Pages → Direct upload, name it `crm-docs`), or via CLI once authenticated locally:

```bash
npx wrangler@4.97.0 pages project create crm-docs --production-branch=main
```

Expected: project `crm-docs` exists; its production URL is `https://crm-docs.pages.dev`.

- [ ] **Step 3: Create an API token** at https://dash.cloudflare.com/profile/api-tokens → Create Token → Custom token with permission **Account → Cloudflare Pages → Edit**. Copy the token value. Note your **Account ID** (Workers & Pages overview, right sidebar).

- [ ] **Step 4: Add GitHub Actions repository secrets.**

```bash
gh secret set CLOUDFLARE_API_TOKEN   # paste token when prompted
gh secret set CLOUDFLARE_ACCOUNT_ID  # paste account id when prompted
```

Run: `gh secret list`
Expected: both `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` listed.

---

## Task 1: Add `site_url` to mkdocs.yml

**Files:**
- Modify: `mkdocs.yml:2` (insert after the `site_description:` line)

- [ ] **Step 1: Add the `site_url` line**

In `mkdocs.yml`, immediately after the `site_description:` line, add:

```yaml
site_url: https://crm-docs.pages.dev/
```

Result — the top of `mkdocs.yml` reads:

```yaml
site_name: crm — D365 CE on-prem CLI
site_description: CLI + agent guide for Dynamics 365 CE on-premises customizations
site_url: https://crm-docs.pages.dev/
repo_url: https://github.com/Gharib89/crm
repo_name: Gharib89/crm
docs_dir: docs
```

- [ ] **Step 2: Verify the strict build still passes**

Run: `pip install -e ".[docs]" && mkdocs build --strict`
Expected: build succeeds, no warnings; `site/` directory produced; `site/sitemap.xml` now contains `https://crm-docs.pages.dev/` URLs.

- [ ] **Step 3: Commit**

```bash
git add mkdocs.yml
git commit -m "docs(mkdocs): set site_url for Cloudflare Pages canonical/sitemap (#42)"
```

---

## Task 2: Replace the gh-pages deploy with a Cloudflare Pages upload

**Files:**
- Modify: `.github/workflows/docs.yml:34-50` (the entire `deploy` job)

- [ ] **Step 1: Replace the `deploy` job**

Replace the existing `deploy` job (everything from `  deploy:` to the end of the file) with:

```yaml
  deploy:
    if: github.event_name == 'push'
    needs: build
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - name: Install docs deps
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[docs]"
      - name: Build site
        run: mkdocs build
      - name: Deploy to Cloudflare Pages
        uses: cloudflare/wrangler-action@v4
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          wranglerVersion: '4.97.0'
          command: pages deploy site --project-name=crm-docs
```

Notes for the implementer:
- The `permissions: contents: write` block from the old `deploy` job is **removed** — the job no longer pushes a branch; it inherits the top-level `permissions: contents: read`.
- The `build` job (lines 20–33) and the `on:`/`permissions:` blocks (lines 1–19) are **unchanged**.
- `mkdocs build` here is non-strict on purpose: the `build` job already ran `--strict` as the gate (`needs: build`).

- [ ] **Step 2: Verify the workflow YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/docs.yml')); print('YAML OK')"`
Expected: `YAML OK` (no exception).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "ci(docs): publish to Cloudflare Pages instead of dead gh-pages (#42)"
```

---

## Task 3: Open PR and confirm the build gate

**Files:** none (PR mechanics).

- [ ] **Step 1: Push the branch and open the PR**

```bash
git push -u origin docs/cloudflare-pages-publish
gh pr create --fill --base main
```

- [ ] **Step 2: Request Copilot review** (project convention)

```bash
gh pr edit <pr-number> --add-reviewer @copilot
```

- [ ] **Step 3: Confirm the `docs / build` check is green on the PR**

Run: `gh pr checks <pr-number>`
Expected: the `build` job passes (it runs on `pull_request`). The `deploy` job does **not** run on PRs (`if: github.event_name == 'push'`) — its absence on the PR is expected, not a failure.

---

## Task 4: Merge and verify the live site (needs Task 0 complete)

**Files:** none (verification).

- [ ] **Step 1: Merge on green**

```bash
gh pr merge <pr-number> --squash --auto
```

- [ ] **Step 2: Watch the post-merge deploy job**

Run: `gh run watch $(gh run list --workflow=docs.yml --branch=main --limit=1 --json databaseId --jq '.[0].databaseId')`
Expected: the `deploy` job succeeds; the wrangler step prints the deployment URL `https://crm-docs.pages.dev`.

- [ ] **Step 3: Confirm the live site serves**

Run: `curl -s -o /dev/null -w "%{http_code}\n" https://crm-docs.pages.dev/`
Expected: `200`.

- [ ] **Step 4: Manual smoke check**

Open `https://crm-docs.pages.dev/` in a browser. Confirm: home page renders, left nav works, search returns results, and a code block's copy button works. (The header GitHub link 404s for anonymous viewers — expected, accepted per the design.)

---

## Self-Review

- **Spec coverage:** Host = Cloudflare Pages direct upload (Task 2). URL = `crm-docs.pages.dev` (Tasks 0, 1, 2). `repo_url` unchanged (Task 1 touches only `site_url`). Source stays private (only `site/` uploaded). `gh-deploy` replaced not augmented (Task 2). Secrets `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` (Tasks 0, 2). `permissions: contents: write` dropped (Task 2). PR-no-deploy guard kept (Task 2/3). Verification = CI green + 200 + smoke (Task 4). All spec sections covered.
- **Placeholder scan:** `<pr-number>` is a runtime value the implementer fills from `gh pr create` output, not an unspecified requirement — acceptable. No TBD/TODO/"handle edge cases".
- **Type/name consistency:** project name `crm-docs`, secrets `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`, build dir `site`, branch `docs/cloudflare-pages-publish`, wrangler `4.97.0`, action `cloudflare/wrangler-action@v4` — consistent across all tasks.
