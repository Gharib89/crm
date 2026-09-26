---
name: update-skills
description: "Refresh this repo's skills in one PR: the Gharib89/skills skills, every skill they compose at its pinned ref, and any other repo-scoped skill the owner picks; report upstream drift to the source repo and summarise what changed. In the source repo, move the drifted pins instead. Attended only."
disable-model-invocation: true
metadata:
  version: 1.0.0
---

# update-skills

Refresh a repo's skills as one PR the repo's own CI, reviewers and merge gate
see, with a body that says what moved. Vocabulary:
[CONTEXT.md](https://github.com/Gharib89/skills/blob/main/CONTEXT.md) of the
source repo (source repo, consumer repo, derived copy, pinned ref, upstream
drift, refresh).

**Attended only.** It asks the repo owner which skills to take and re-runs
`/setup-skills`, which interviews; no cloud routine runs it.

**It calls Ship's generic mechanics by path** from the worktree
([ADR 0004](https://github.com/Gharib89/skills/blob/main/docs/adr/0004-cross-repo-writes-reach-the-source-repo-on-the-humans-word.md)): `$S` below stands for
`.claude/skills/ship/scripts` and `$U` for `.claude/skills/update-skills/scripts`.
After step 2 those are the scripts it just installed, so it never drives a Ship
other than the one the PR ships. Each mechanic answers `--help` with its flags.
Plan files and body files go in your scratchpad, outside the repo. Shell
variables do not survive between tool calls: a value one step prints, later
steps write out in full.

## Process

### 1. Isolate

From the main checkout, `$S/isolate.sh none chore update-skills`, then work
from the path it prints. Note `git rev-parse HEAD` there as `<old>`: the tree
before the refresh, which the plan reads as the old side.

### 2. Refresh the source-repo skills

In a consumer repo:

```sh
npx skills add Gharib89/skills --skill ship --skill cloud-ship --skill setup-skills --skill update-skills --agent claude-code -y
```

In the source repo, whose `skills-lock.json` records `ship`'s source as `.`,
run the refresh line its CLAUDE.md carries instead. Project scope always: no
`-g`.

### 3. Plan

```sh
$U/heads.sh . > <scratch>/heads.json
$U/plan.sh . <scratch>/heads.json --old <old> > <scratch>/plan.json
```

`heads` reads GitHub's public API with curl, so it needs no credentials. Its
`unreachable` rows are skills whose upstream did not answer: no head, so never
drift or an offered update. Carry them to step 8. The plan's fields drive every
later step; the header comment in `plan.sh` defines each one.

Nothing moved (every `source_skills` entry at its old version, every
`composed` entry's `old_ref` equal to its `pin`, `drift`, `others` and
`sections` empty, and no `unreachable` row): tell the owner the repo is
current, run `$S/cleanup.sh none` from the main checkout, and stop.

### 4. Composed skills at their pins

Run every `composed[].install` line, then `$S/preflight.sh none` and keep its
`reasons`. A `worktree exists` reason naming this run's own worktree is
expected; a profile reason is step 6's; any other carries the line that repairs
it: run that, then preflight again. A consumer repo never installs a drift
row's `head`: that version is one nobody tested Ship against, and the row
reaches the source repo in step 7 instead.

### 5. Other skills

`others` lists the repo-scoped lock entries, neither from the source repo nor
composed, whose upstream moved. Non-empty: ask once, with one multi-select
question per four skills, four questions to an AskUserQuestion call, each
option the skill's name with `<old_ref, or "no ref"> → <head>` as its
description. Run the
`install` line of each skill ticked. Personal skills under `~/.claude/skills`
are never in the lock and never touched.

### 6. setup-skills sections

`/setup-skills` re-runs when step 4's `reasons` carry `profile missing` or
`profile invalid`, or `sections` is non-empty. Ask the owner to run it from a
new session opened at the worktree, and wait: the Skill tool refuses it
(`disable-model-invocation`), and this session's copy predates the refresh.
Tell them what to redo: its Re-run path for a profile refusal, and for each
section only the step-5 item its template feeds:

| `section` | setup-skills item |
|---|---|
| `pr-template` | **PR template** |
| `reviewer-scaffolding` | **Reviewer scaffolding** |
| `local-gate` | **Local gate** |
| `coding-standards` | **Coding standards** |
| `dimension-labels` | **Triage labels on the host** and **The `## Dimension labels` section** |
| `ado-tracker-doc` | step 1.1's Azure DevOps tracker doc |

A change to setup-skills' `SKILL.md` alone is not a section: its prose moving
costs no interview. When the owner says it finished, run `$S/preflight.sh none`
again: the step is done when this run's `worktree exists` is the only reason
left. Note the profile schema move for step 8: the `Schema:` line of
`docs/agents/ship.md` at `<old>` against the one now.

### 7. Report upstream drift

`drift` non-empty: the source repo keeps one open issue for it. In the source
repo, run step 9 first, then this step, then step 8. Write a body file holding
a `## Drift` section and nothing else, its table
`| Skill | Pinned | Upstream head |` with one row per `drift` entry. The source
repo is public, so the body carries skill names and refs only: not this repo's
name, nor anything else about it ([ADR 0004](https://github.com/Gharib89/skills/blob/main/docs/adr/0004-cross-repo-writes-reach-the-source-repo-on-the-humans-word.md)). Then:

```sh
$S/file-issue.sh --repo Gharib89/skills --title "Upstream drift: composed skills" --body-file <body> --label needs-triage
```

- `filed: true`: that is the drift issue.
- `filed: false`: the candidate titled exactly
  `Upstream drift: composed skills` is the drift issue. Rewrite its table in
  place, with a file holding the table alone:

  ```sh
  $S/update-issue-body.sh <n> --repo Gharib89/skills --section Drift --body-file <table>
  ```

  With no candidate of that exact title, re-run `file-issue` with
  `--distinct-from` naming every candidate.
- Exit 1: the source repo is unreachable from here, which is every Azure
  DevOps repo without GitHub credentials. Print the answer's `command` for the
  owner, verbatim, and carry it to step 8. No retry.

### 8. Summary and PR

Commit the refresh and every setup-skills write. The PR body carries these
sections in this order:

1. `## Source-repo skills`: for each `source_skills` entry whose version moved,
   the sections of the file at its `changelog` path above `old_version` up to
   `new_version`, verbatim, breaking changes first within this section. A null
   `old_version` is a skill new to this repo: its version alone.
2. `## Composed skills`: for each `composed` entry whose `old_ref` differs from
   its `pin`, `<skill>: <old_ref> → <pin>`, then the upstream commit subjects
   that touched its folder between the two. A null `old_ref` reads "previously
   unpinned → `<pin>`", with no subjects.
3. `## setup-skills`: the sections step 6 re-ran and the profile schema move,
   or "none".
4. `## Other skills`: each skill step 5 took, `<old_ref> → <head>`, with its
   subjects the same way.
5. `## Upstream drift`: the drift issue's link and the `drift` rows, held at
   their pins in a consumer repo and moved in the source repo; or the printed
   command; or "none". Then each `unreachable` row as
   "drift not checked: <skill>: <error>".

The subjects are a read, not a gate: the folder is the lock's `skillPath`
without `/SKILL.md`, and the commits are those of
`https://api.github.com/repos/<source>/commits?sha=<new>&path=<folder>` newer
than the old ref, which `.../commits/<old_ref>` dates. A null `old_ref` has no
subjects; a read that fails writes "subjects unavailable" and goes on.

Open it with `$S/open-pr.sh none --title "<subject>" --body-file <body>`, a
Conventional-Commit subject honouring the profile's `Subject constraints:`
(`chore(skills): refresh derived skills` where it has none), then
`$S/read-pr.sh <pr>`. Hand the owner the link and stop: the PR merges the way
the repo merges anything. After the merge, `$S/cleanup.sh none` from the main
checkout removes the worktree.

### 9. Source-repo mode

`mode` is `source`: this is Gharib89/skills, and each `drift` row is a pin to
move, in this one PR. It runs before step 7, which then files or finds the
drift issue this PR closes; steps 5, 6 and 8 run unchanged. Per row:

1. Read what moved: `https://api.github.com/repos/<source>/compare/<pin>...<head>`,
   the files under the skill's folder with their patches.
2. Re-add the skill at the head, its install line with `#<head>` in place of
   the pin, and move the pin on the `composes` line that names it
   (`skills/ship/SKILL.md` or `skills/setup-skills/SKILL.md`, the composing
   skill), plus the sha on every printed install line in setup-skills' step
   1.2 that installs it. A printed line installing several skills at one sha is
   split when their pins part.
3. Judge the upstream diff against what the composing skill's prose relies
   on where it composes the skill. Where it breaks a reliance, rework that
   prose through `/writing-for-agents` in this same change: no skill ships
   against a pin it does not fit.

Then the refresh line, and `scripts/local-gate.sh`, whose `derived-copies` gate
holds every pin to the lock. In step 8 the PR body opens with `Closes #<n>`,
the drift issue step 7 filed or found, and the title is scoped to the composing
skill, so the release run records the move in that skill's CHANGELOG. A pin
moved on ship's `composes` line is a breaking change to ship, since preflight
refuses every consumer still at the old ref (`skill off pin`): the title takes
`!`, e.g. `fix(ship)!: move show-me to <short sha>`, and the maintainer applies
the `major` label. A pin moved on setup-skills' line alone refuses nothing and
takes no `!`.
