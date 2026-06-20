---
name: cloud-ship
description: >-
  Run one scheduled cloud-routine fire: bootstrap the sandbox, pick the oldest
  open `ready-for-agent` issue, ship it to a merge-ready PR via the `ship` skill,
  and STOP at the merge gate without merging. Composes `ship`. Use only when
  running the scheduled cloud ship routine for Gharib89/crm (the routine prompt
  invokes this skill by name); not for an interactive `/ship`.
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

- `ship`'s phase 9 is a merge gate that **waits for a human to type "merge."**
  That instruction is written for an interactive CLI session. **You never reach a
  human here.** Do not wait, do not poll, do not merge — when the PR is
  merge-ready, post it and **end the fire** (step 5).
- A fire that can't finish must not strand the issue. Either `ship` reaches
  merge-ready (step 5) or you hand it to a human (step 4). Never leave it spinning.

## Compose, don't inline

Run `ship` by **invoking the Skill tool** (skill `ship`) — never paraphrase or
hand-roll its phases from this skill. `ship` in turn composes `tdd` and `review`;
when it reaches those phases, invoke `tdd` / `review` via the Skill tool too. All
four ship as sibling skills in the clone's `.claude/skills/`.

## The fire

**1 · Bootstrap.** From the repo root run `bash scripts/cloud-ship-bootstrap.sh`.
It installs the crm CLI, builds the active `agent-cloud` profile from the
environment's `D365_*` variables, and confirms the cloud org via WhoAmI.
**Completion:** it exits 0. Non-zero → report the failure and **STOP** the fire.

**2 · Pick the work item.** `gh` reads `GH_TOKEN` from the environment:

```
NUM=$(gh issue list --repo Gharib89/crm --label ready-for-agent --state open \
      --json number --jq 'sort_by(.number)[0].number // empty')
```

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
forever). Hand it to a human and **STOP**:

```
gh issue edit "$NUM" --repo Gharib89/crm --remove-label agent-working --add-label ready-for-human
gh issue comment "$NUM" --repo Gharib89/crm --body "<one-line reason it is blocked>"
```

**5 · End at the merge gate — do not merge.** On success `ship` reaches its merge
gate and will try to **wait** for a human "merge." **Override it:** there is no
in-session human. The moment the PR is merge-ready — CI green, Copilot review
addressed within the ≤3-round ceiling, `mergeable` — **post the PR link + a
disposition summary and END the fire.** Do not wait, poll, or merge. A human
merges out of band later; the squash `Closes #$NUM` closes the issue then. **Leave
the issue `agent-working`** — it carries the open PR, so later fires skip it until
the merge closes it.

## Cloud sandbox quirks

- **Task tools may be absent — even via `ToolSearch`.** `ship`'s first action is
  to create its phase task list. If `TaskCreate`/`TaskUpdate`/`TaskList` aren't
  loaded, try `ToolSearch` (`select:TaskCreate,TaskUpdate,TaskList`) once; if that
  returns nothing, track the phases in a **markdown checklist** instead and keep
  going. The list is a progress / resume aid, **not a gate**.
- **`gh` and `git push` hit GitHub directly** (the GitHub MCP connector is
  brokered separately). The claim state machine and `ship`'s PR/CI steps are all
  `gh`-native and depend on the env's allowed-domains + `GH_TOKEN` — assume `gh`
  works; if it 401s, the env's PAT or network policy is wrong → report and STOP.

## Working standards

The operator's global coding philosophy is **not** in the clone (only the repo's
own `CLAUDE.md` is). Read **[reference/working-standards.md](reference/working-standards.md)**
before `ship` implements and hold it through the whole fire — `ship`, `tdd`, and
the repo `CLAUDE.md` cover tests / gates / merge flow; this fills the judgment
layer they assume.

## Reference files

- `reference/working-standards.md` — the operator's engineering standards (absent
  from the clone): build the right thing, simplicity, surgical changes, root cause,
  comments, concise PRs.
