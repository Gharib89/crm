# Ship profile schema

One `## Schema N` entry per number, oldest first, each listing the structural changes from N-1. `setup-skills` re-run reads this file to migrate a profile whose `Schema:` line trails the installed ship's `metadata.profile-schema`: apply every entry between the two numbers in order, walk only the rows an entry adds or whose vocabulary it moves, leave the prose under existing headings to the human, and rewrite the `Schema:` line last.

Bump rule: a ship PR that changes what the profile must contain (a heading or `Label:` line added, renamed or removed; a `Label:` vocabulary changed) adds an entry here, moves `metadata.profile-schema` in `skills/ship/SKILL.md` and the `Schema:` line in [ship-profile.md](./ship-profile.md), and is graded a ship major, which the source repo's `## Versioning and changelog` axis turns into a number. A PR that edits `ship-profile.md`'s structure without an entry here is incomplete.

## Schema 1

The first numbered schema. Profiles written before it carry no `Schema:` line.

- `Schema: 1` directly under the `# Ship profile` title, before the first `##`.
- Fourteen `##` headings, always present, in this order: Host, Worktree, Local gate, CI, Reviewers, Coding standards, Verification, Versioning and changelog, PR, Public surface, Triage, Docs sync, Current docs, Cloud lane.
- The `Label:` lines under each heading as [ship-profile.md](./ship-profile.md) lists them at this schema.

## Schema 2

The fallback reviewer: a reviewer that stands in for another one is on-request and conditional, driven only on the runs where its primary exits degraded. Only the reviewer block moves; the fourteen headings and every other `Label:` line are unchanged from Schema 1.

- Each `### <reviewer>` block under `## Reviewers` gains a `Fallback-for:` line, between `Gating:` and `Instructions:`. It names the reviewer this one stands in for, or `None.` where it stands in for nobody, which is every reviewer a Schema 1 profile had. Migration writes `Fallback-for: None.` on every existing block.
- `Request:` gains the value `comment <phrase>`, beside the existing mechanic name and `None.`: the transport that asks a comment-triggered reviewer for a round by posting `<phrase>` as a PR comment.
- Three refusals Schema 2 added at preflight, so a migrated profile is checked rather than trusted (ship has grown others since; the source repo's [CONTEXT.md](https://github.com/Gharib89/skills/blob/main/CONTEXT.md) carries the current set): `Fallback-for:` on a reviewer whose `Trigger:` is not `on-request`, `Fallback-for:` naming a reviewer the profile does not list, and an on-request reviewer with no `Cap:`.

Migration from an unnumbered profile: add the line. Nothing else moves.

## Schema 3

The workflow file a comment-transport reviewer's round comes from, on the block instead of in the prose under it. Only the reviewer block moves; the fourteen headings and every other `Label:` line are unchanged from Schema 2.

- Each `### <reviewer>` block under `## Reviewers` gains a `Workflow:` line, directly after `Request:`, the field it qualifies. It names the workflow file that reviewer's round comes from as a **repo-relative path from the checkout root** (e.g. `.github/workflows/claude-review.yml`), where `Request:` reads `comment <phrase>`, and `None.` on every other block. The path form is canonical, and it is the one the refusal below stats: GitHub knows a workflow by its file name alone, which the adapter derives from this path; a bare name is not a path the checkout can be asked about, and on a host whose pipeline files sit anywhere it names nothing in particular. The ship run's poll of that reviewer awaits that file's run, which is what separates a round still being written from one that will not come; before this schema the run read that path out of the prose paragraph under the block, where nothing checked it.
- Four refusals Schema 3 added at preflight, so a migrated profile is checked rather than trusted (ship has grown others since; the source repo's [CONTEXT.md](https://github.com/Gharib89/skills/blob/main/CONTEXT.md) carries the current set): `Request: comment <phrase>` with no `Workflow:` line or `Workflow: None.`, a `Workflow:` naming a file on a block whose `Request:` is not `comment <phrase>`, a `Workflow:` naming a file the checkout does not carry (a path climbing out of it with `..`, or an absolute one, included), and the `Request:` value the pair keys off: the bare word `comment` with no phrase, which the comment transport has nothing to post.

Migration from Schema 2: **a block whose `Request:` is `comment <phrase>` cannot receive `Workflow: None.`**, or the migrated profile is refused by the first preflight that reads it. Lift the path from that block's prose paragraph when exactly one file path is named there; when the prose names none, or several, stop and ask the human which file it is. Every other block gets `Workflow: None.` The prose keeps whatever it said: it explains the reviewer, and the field is the value a run reads.
