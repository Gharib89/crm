# The drift checklist

Seven repo-specific gates, distilled from CLAUDE.md and the feedback record.
Every item ends in **pass / fail / n-a with evidence** — a file path, a command
output, or a quoted line. "Looks fine" is not evidence.

## 1 · Docs-sync

If the diff changes the public CLI surface (command / flag / choice / default /
output shape / documented behavior):

- README, `docs/how-to/<group>.md`, and `docs/reference/cli.md` updated in this PR.
- `crm/skills/` (the shipped agent skill) updated — and still **self-contained**:
  no links to repo paths (`docs/**`, `CONTEXT.md`), and it states only what
  `crm describe` / `--help` cannot (workflows, gotchas, the JSON contract) —
  never restated flags/choices/defaults.

No surface change → n-a, one line saying why.

## 2 · E2E coverage gate

Every new or changed D365-touching command in the diff has a live e2e test
stamped `@covers("<group> <verb>")` under `crm/tests/e2e/`, **or** an `E2E_SKIP`
entry with a reason in `crm/tests/e2e/coverage.py`. For an `E2E_SKIP`, judge the
reason itself: platform walls have been falsified before (a "cloud rejects this"
claim that a clone-of-real-payload disproved) — a suspicious reason is a fail, not
a pass.

## 3 · Test classification

- A `@requires_cloud` / `@requires_onprem` gate added or removed → the live-run
  table in `crm/tests/TEST.md` updated in the same diff.
- A defect tracked in `crm/tests/e2e/DISCOVERED_BUGS.md` fixed or reclassified →
  that entry updated in the same diff.

## 4 · Bump discipline

The PR title is the squash subject python-semantic-release reads. Check both:

- **Well-formed** Conventional Commit line.
- **Right tier**: `feat:` only for a genuinely new command, query mode, or
  materially new capability (minor bump). A new flag/alias on an existing
  command, a tweak, a polish → `fix:`/`perf:` (patch). `refactor:`/`chore:`/
  `test:`/`docs:` → no bump. Over-tiered titles inflate the version — fix the
  title, don't merge it wrong.

## 5 · Emit contract

New or changed command output honors the CLI contract (`CONTEXT.md`):

- Envelope shape `{ok, data?, error?, meta?}` under `--json`; `data` is curated,
  not raw-OData passthrough (protocol keys stripped or relocated to `meta`).
- JSON-only fields gated on `ctx.json_mode` — `meta=` renders in **human** mode
  too, so ungated meta leaks into human output.
- Failures produce a clean error envelope with a stable `code`; no fail-silent
  paths, no swallowed exceptions; file IO wrapped per the error-contract
  conventions; exit 2 for usage errors.

## 6 · Genericity

Public repo. Run `scripts/genericity-scan.sh` (the `moce` grep + a broad
GUID-shape scan over the changed files) and **classify every hit**
(platform-constant FormXml classids are legal and stay; org-fingerprint GUIDs,
real hostnames, or tenant IDs are a fail). Placeholders are Contoso-style.
Credential-key names follow the named-constant convention that keeps secret
scanners quiet.

## 7 · Scope discipline

Every changed hunk traces to the linked issue / agent brief (spec precedence: a
later authoritative comment supersedes the body). Adjacent refactors, drive-by
cleanups, and unrequested capability are findings — either trivially revertable
(fix in place: drop the hunk) or design-level (gate-failed). Check the inverse
too: acceptance criteria in the brief with no corresponding change or test.
