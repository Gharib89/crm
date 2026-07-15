---
name: cloud-ship
description: >-
  Run one scheduled cloud-routine fire: pick the oldest open `ready-for-agent`
  issue, drive it to a merge-ready PR, and STOP at the merge gate without merging.
  Composes `ship`. Use only when running the scheduled cloud ship routine for
  Gharib89/crm (the routine prompt invokes this skill by name); not for an
  interactive `/ship`.
metadata:
  internal: true
---

# cloud-ship

One **fire** = one issue → one merge-ready PR → stop. This skill is the
orchestration the scheduled cloud routine runs unattended each hour; it
**composes `ship`** for the actual issue→PR work and adds only what `ship` is
deliberately generic about: the sandbox bootstrap, the issue picker, the
cloud-only target, the blocked hand-off, and — the crux — the **no-human
merge-gate override**.

## The fire is one-shot and unattended

There is **no in-session human** during a fire. That single fact drives the two
things this skill exists to enforce on top of `ship`:

- `ship`'s phase-9 merge gate waits for a human "merge" — a human you never
  reach. The override is step 5.
- A fire that can't finish must not strand the issue. Either `ship` reaches
  merge-ready (step 5) or you hand it to a human (step 4). Never leave it spinning.

## Compose, don't inline

Run `ship` by **invoking the Skill tool** (skill `ship`) — never paraphrase or
hand-roll its phases from this skill. `ship` in turn composes `tdd` and `code-review`;
when it reaches those phases, invoke `tdd` / `code-review` via the Skill tool too. All
four ship as sibling skills in the clone's `.claude/skills/`.

## The fire

**1 · Bootstrap.** From the repo root run `bash scripts/cloud-ship-bootstrap.sh`.
It installs the crm CLI, builds the active `agent-cloud` profile from the
environment's `D365_*` variables, and confirms the cloud org via WhoAmI.
**Completion:** it exits 0. Non-zero → report the failure and **STOP** the fire.

**2 · Pick the work item.** Use the **GitHub MCP connector** (`gh`'s repo/PR/issue
REST endpoints are gated in the cloud sandbox — see *GitHub access in a fire* below):

```
mcp__github__list_issues  owner=Gharib89 repo=crm
    labels=["ready-for-agent"] state=OPEN
    orderBy=CREATED_AT direction=ASC perPage=1
```

Record `NUM` = `issues[0].number` (empty result → nothing ready).

**Completion:** `NUM` holds the oldest open `ready-for-agent` issue. Empty →
report "nothing ready" and **STOP** — do not open a PR. **Do not claim it here** —
`ship` claims it in its phase 1 (removes `ready-for-agent`, adds `agent-working`,
comments). Because the claim drops `ready-for-agent`, this picker never returns an
issue another fire already owns or that has an open PR.

**3 · Branch, then ship.** A fire starts you on an auto `claude/<random>` branch.
Switch to the repo's semantic convention **before any commit** so the PR branch
isn't the `claude/...` name — `<type>/<slug>-$NUM`, `<type>` = `fix` for a bug,
`feat` otherwise, `<slug>` a short kebab summary of the issue title:

```
git switch -c fix/<slug>-$NUM   # or feat/<slug>-$NUM
```

Then **invoke the `ship` skill on issue $NUM**. While it runs:

- **This branch in the sandbox clone IS `ship`'s phase-0 isolation** — don't
  create a worktree inside it; treat phase 0 as satisfied (its pre-flight
  already-in-flight check still applies).
- **Pin `--profile agent-cloud` on every crm command** — this is a cloud
  Dataverse org **only**. An issue that can only be verified on-prem counts as
  **blocked** (step 4); never touch on-prem.
- Put **`Closes #$NUM`** in the PR body so the squash-merge auto-closes the issue
  and drops it from the queue.
- Follow the clone's `CLAUDE.md` for test / gate / docs-sync / commit rules, and
  the **working standards** below.

**Completion:** `ship` reaches its merge gate (→ step 5) or cannot (→ step 4).

**4 · Blocked hand-off.** If `ship` **cannot** produce a merge-ready PR —
ambiguous / underspecified, on-prem-only, or CI can't be made green — do not leave
it `agent-working` and do not return it to `ready-for-agent` (that loops it
forever). Hand it to a human and **STOP**. MCP `issue_write` **replaces** the
whole label set (unlike `gh issue edit`'s surgical `--add`/`--remove-label`), so
read the current labels first, then write the modified set — never a bare
`["ready-for-human"]`, which would drop the issue's `area`/`size`/`type` labels:

```
mcp__github__issue_read   method=get_labels owner=Gharib89 repo=crm issue_number=$NUM
    → LABELS = its label names
mcp__github__issue_write  method=update owner=Gharib89 repo=crm issue_number=$NUM
    labels = LABELS, with "agent-working" and "ready-for-agent" removed and "ready-for-human" added
mcp__github__add_issue_comment  owner=Gharib89 repo=crm issue_number=$NUM
    body="<one-line reason it is blocked>"
```

**5 · End at the merge gate — do not merge.** On success `ship` reaches its merge
gate and will try to **wait** for a human "merge." **Override it.** The moment the
PR is merge-ready — CI green, Copilot's round-1 threads dispositioned (never
re-requested), every CodeRabbit thread dispositioned (fixed or declined with
evidence) and then resolved (`@coderabbitai resolve`), CodeRabbit quiet on the
latest push, `mergeable` — **post the PR link + a
per-reviewer disposition summary and END the fire.** Do
not wait, poll, or merge. A human merges out of band later; the squash
`Closes #$NUM` closes the issue then. **Leave the issue `agent-working`** — it
carries the open PR, so later fires skip it until the merge closes it.

## Cloud sandbox quirks

- **Task-list tools (`TaskCreate`/`TaskUpdate`/`TaskList`) are absent — even via
  `ToolSearch`.** Go straight to `ship`'s markdown-checklist fallback for the
  phase list; don't burn a search. The list is a progress / resume aid, **not a
  gate**.
- **Subagent tools are absent too.** `ship`'s delegation rule and model-tier
  table are inert in a fire — run everything inline in the main thread (the
  `code-review` skill's two axes included), and compensate by projecting every
  GitHub / CLI call harder, since nothing can be offloaded.

## GitHub access in a fire

**Route all GitHub API reads and writes through the `mcp__github__*` connector;
use the `git` CLI for local repository work (branch/`switch`, status, diff,
commit, fetch, push).** The cloud sandbox's egress proxy
gates `gh`'s repo/PR/issue REST endpoints (`api.github.com`) — they return
`403 "GitHub access is not enabled for this session"` regardless of `GH_TOKEN`,
so **every** `gh` command the fire would otherwise run fails here — not just the
ones in the composed **`ship`** skill and its `reference/*.md`, but also those in
the **repo docs `ship` follows** (`docs/agents/issue-tracker.md`,
`docs/agents/triage-labels.md`), notably the phase-1 `agent-working` claim
(label edit + comment). The GitHub **MCP** connector is brokered through
Anthropic (exempt from the network policy) and `git` over `github.com` uses
brokered credentials — both work. **This section outranks every literal `gh`
command in `ship`, its references, and any repo doc it follows for the duration
of a fire.** The table maps every `gh` command a fire actually reaches (through
the merge gate). The merge / post-merge commands in `reference/merge-gate.md`
(`gh pr merge`, `gh pr view --json state,mergedAt`, `gh issue view`) are **out of
a fire's path** — step 5 ends the fire *before* merging — so they need no
mapping. Translate each command below to its MCP equivalent:

| Where the fire would run `gh …` | Use instead |
|---|---|
| `gh issue view <n> --comments` — read the spec (ship phase 1) | `mcp__github__issue_read` `method=get` (+ `get_comments` / `get_labels`) |
| `gh issue list … --label …` | `mcp__github__list_issues` (`labels`, `state`, `orderBy`) |
| `gh issue edit <n> --add-label/--remove-label` — the phase-1 claim (−`ready-for-agent` +`agent-working`) and the step-4 hand-off | `issue_read method=get_labels` → `issue_write method=update` with the **full** modified label set (it replaces, not merges) |
| `gh issue comment <n>` — phase-1 claim comment, PR-reflect, block reason | `mcp__github__add_issue_comment` |
| `gh pr create` / "open a ready PR" (ship phase 6) | `mcp__github__create_pull_request` (`draft` omitted) |
| `gh pr view <n> --json mergeable,mergeStateStatus` (conflict check, ship phase 8) | `mcp__github__pull_request_read method=get` |
| `gh pr view <n> --json reviews,statusCheckRollup` (poll, copilot-loop) | `pull_request_read` `method=get_reviews` + `get_check_runs` (or `get_status`) |
| `@coderabbitai review` (lift an auto-pause) / `@coderabbitai resolve` | `mcp__github__add_issue_comment` on the PR |

Poll CI/reviews by re-calling `pull_request_read` (not `gh`) within the bounded
poll window `ship`'s `reference/copilot-loop.md` already defines (Copilot is
round-1-only, never re-requested; CodeRabbit owns iteration via push re-reviews) — a short delay between polls, a capped number of attempts; never a
detached/background monitor. Reaching the bound is **not** a licence to proceed:
end at step 5 only when the PR is genuinely merge-ready (CI green, reviews
addressed, `mergeable`). If the bound is hit while CI is red/incomplete or can't
be made green, that is the **step-4 blocked hand-off** — never continue
unattended past a red or unfinished gate. If the `mcp__github__*` tools are
absent or every call is denied at fire start, the connector isn't wired → report
and STOP (the fire cannot reach GitHub).

## Working standards

The operator's global coding philosophy does **not** live in the repo's own
`CLAUDE.md` (the clone carries only that) — so it's reproduced here. Read
**[reference/working-standards.md](reference/working-standards.md)** before `ship`
implements and hold it through the whole fire — `ship`, `tdd`, and the repo
`CLAUDE.md` cover tests / gates / merge flow; this fills the judgment layer they
assume.
