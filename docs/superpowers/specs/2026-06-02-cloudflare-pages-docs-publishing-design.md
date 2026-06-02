# Publish the docs site via Cloudflare Pages

**Date:** 2026-06-02
**Issue:** [#42](https://github.com/Gharib89/crm/issues/42)
**Status:** Design approved

## Problem

The MkDocs Material site builds in CI (`.github/workflows/docs.yml`) and, on push to
`main`, runs `mkdocs gh-deploy --force` to write the `gh-pages` branch. But `crm` is a
**private** repo on the **free** plan, where GitHub Pages for private repos is
unavailable. So the `gh-pages` branch is built on every merge and served nowhere:

- `https://gharib89.github.io/crm/` → 404
- `GET repos/Gharib89/crm/pages` → 404 (Pages not enabled)

## Goal

Make the docs site publicly viewable, for free, **without** making the code repo public.

## Decisions

| Question | Decision |
|----------|----------|
| Host | **Cloudflare Pages**, direct upload via `wrangler` from the existing GitHub Actions CI |
| Public URL | Default **`crm-docs.pages.dev`** (custom domain deferred) |
| `repo_url` in header | **Unchanged** — left pointing at the private repo; anonymous clicks 404, accepted |
| Source exposure | **None** — only built HTML ships to Cloudflare; markdown source stays private |
| Existing `gh-deploy` job | **Replaced** by the wrangler deploy (the `gh-pages` branch serves nothing) |

## Approach

**Replace, don't augment.** The current `deploy` job's `mkdocs gh-deploy --force` step
produces an unserved `gh-pages` branch — pure dead output. Replace it with a Cloudflare
Pages deploy so there is a single, real publish path. (Augmenting — keeping `gh-deploy`
*and* adding wrangler — was rejected: it only pays off if GitHub Pages is later enabled,
and until then just rebuilds an unserved branch every merge.)

## Architecture / flow

Trigger and gating are unchanged; only the deploy target changes.

- **Pull request** → `build` job runs `mkdocs build --strict`. Gate only, no deploy.
  *(Already exists, untouched.)*
- **Push to `main`** → `deploy` job runs `mkdocs build`, then
  `wrangler pages deploy site --project-name=crm-docs`. Cloudflare serves the uploaded
  `site/` directory at `crm-docs.pages.dev`.

## Changes

### `.github/workflows/docs.yml`

- In the `deploy` job, replace the `mkdocs gh-deploy --force` step with:
  1. `mkdocs build` (produces `site/`).
  2. `wrangler pages deploy site --project-name=crm-docs`, authenticated via
     `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` env from GitHub Actions secrets.
- Pin the wrangler version for reproducibility.
- Drop `permissions: contents: write` from the `deploy` job — it no longer pushes a branch.
- Keep the `if: github.event_name == 'push'` guard so PRs never deploy.

### `mkdocs.yml`

- Add `site_url: https://crm-docs.pages.dev/` (canonical URL + sitemap generation;
  recommended by MkDocs Material).
- `repo_url` / `repo_name` left as-is per the decision above.

## Prerequisites (manual, one-time — performed by the maintainer, not CI)

1. Create a Cloudflare account.
2. Create the Pages project named `crm-docs` (dashboard, or
   `wrangler pages project create crm-docs --production-branch=main`).
3. Create an API token scoped **Cloudflare Pages → Edit**; note the **Account ID**.
4. Add both as GitHub Actions repository secrets: `CLOUDFLARE_API_TOKEN`,
   `CLOUDFLARE_ACCOUNT_ID`.

## Error handling / edge cases

- PR builds never deploy (existing push guard retained).
- Missing/invalid secrets → the deploy job fails loudly in CI. Acceptable: it surfaces
  misconfiguration rather than silently publishing nothing.
- Anonymous viewers who click the header repo link (or any inline link to the private
  repo / its issues) get a GitHub 404. Accepted per the `repo_url` decision.

## Out of scope (deferred)

- Custom domain (default `*.pages.dev` is sufficient for now).
- Retargeting `repo_url` or stripping it from the public build.
- Deleting the stale `gh-pages` branch (harmless; can be pruned later).

## Verification

1. CI `deploy` job is green after merge to `main`.
2. `https://crm-docs.pages.dev` returns 200.
3. Navigation, search, and code-copy work on the live site.
