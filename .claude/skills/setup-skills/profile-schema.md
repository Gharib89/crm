# Ship profile schema

One `## Schema N` entry per number, oldest first, each listing the structural changes from N-1.

Optional lines: a line `ship` accepts without expecting it takes no entry and no bump. Under `## PR`, `Merge: on-clean-gate` opts an attended run into merging when its recorded gate is clean; `Merge: Default.` or an absent line keeps the human stop. A re-run preserves an existing `Merge:` choice and leaves an absent line absent, including in new profiles. A `### <verification>` block under `## Verification` may carry `Timebox: <n> minutes` or `Timebox: <n> min`, after `Claims to probe:`, bounding that verification's run; a block without it has no bound, and `ship` reads a profile either way.

## Schema 1

The first numbered schema. Profiles written before it carry no `Schema:` line.

- `Schema: 1` directly under the `# Ship profile` title, before the first `##`.
- Fourteen `##` headings, always present, in this order: Host, Worktree, Local gate, CI, Reviewers, Coding standards, Verification, Versioning and changelog, PR, Public surface, Triage, Docs sync, Current docs, Cloud lane.
- The `Label:` lines under each heading as [ship-profile.md](./ship-profile.md) lists them at this schema.

Migration from an unnumbered profile: add the line. Nothing else moves.

## Schema 2

The fallback reviewer: a reviewer that stands in for another one is on-request and conditional, driven only on the runs where its primary exits not reviewed. Only the reviewer block moves; the fourteen headings and every other `Label:` line are unchanged from Schema 1.

- Each `### <reviewer>` block under `## Reviewers` gains a `Fallback-for:` line, between `Gating:` and `Instructions:`. It names the reviewer this one stands in for, or `None.` where it stands in for nobody, which is every reviewer a Schema 1 profile had. Migration writes `Fallback-for: None.` on every existing block.
- `Request:` gains the value `comment <phrase>`, beside the existing mechanic name and `None.`: the transport that asks a comment-triggered reviewer for a round by posting `<phrase>` as a PR comment.

## Schema 3

The workflow file a comment-transport reviewer's round comes from, on the block instead of in the prose under it. Only the reviewer block moves; the fourteen headings and every other `Label:` line are unchanged from Schema 2.

- Each `### <reviewer>` block under `## Reviewers` gains a `Workflow:` line, directly after `Request:`, the field it qualifies. It names the workflow file that reviewer's round comes from as a **repo-relative path from the checkout root** (e.g. `.github/workflows/claude-review.yml`), where `Request:` reads `comment <phrase>`, and `None.` on every other block.

Migration from Schema 2: **a block whose `Request:` is `comment <phrase>` cannot receive `Workflow: None.`**, or the migrated profile is refused by the first preflight that reads it. Lift the path from that block's prose paragraph when exactly one file path is named there; when the prose names none, or several, stop and ask the human which file it is. Every other block gets `Workflow: None.` The prose keeps whatever it said: it explains the reviewer, and the field is the value a run reads.

## Schema 4

A comment-transport reviewer whose round comes from no workflow run: native Codex review, which the ChatGPT Codex Connector GitHub app posts itself. Only the `Workflow:` vocabulary moves; the fourteen headings and every `Label:` line are unchanged from Schema 3.

- `Workflow:` gains the value `native <integration>`, beside a workflow path and `None.`, on a block whose `Request:` is `comment <phrase>`. It names the integration the round's status is read from instead of a run; `native codex` is the one ship reads. The value names no file, so preflight does not stat it. It is GitHub-only: on Azure DevOps the read always answers `unavailable`, so setup-skills drafts it only where `Host:` is `github`.

Migration from Schema 3: rewrite the `Schema:` line. No block changes, since no Schema 3 profile could carry the new value.
