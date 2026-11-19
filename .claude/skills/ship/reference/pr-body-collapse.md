# Phases 6 and 7: collapsing long PR body sections

Read this file only when a `## Review` or `## Verification` entry carries more
than three lines of detail. The body itself is [pr-body.md](pr-body.md).

## Collapsing long sections

A reviewer reads the short summary first, so the detail behind it folds away.
The host is the profile's Host. On a GitHub host, the long part of
`## Review` and `## Verification` goes inside a column-0 `<details>` block with
a one-line `<summary>`. The long part is whatever an entry carries beyond its
one line: a reviewer's per-round findings under Review, and a verification's
command output or multi-line note on what ran under Verification. Wrap it when
any entry carries more than three lines of detail; a section within that bound
stays plain. The `## ` heading, the exit word and any one-line entry stay
outside the block. So the one line per entry stays visible first (each
reviewer's exit-word line, each verification's result line, or the
`None applicable: <reason>` line), and the block follows them with the
detail. The section surgery treats the block as the section's own content,
never as a heading ([mechanics.md](mechanics.md#section-surgery)).
`## Why the change` and the Door line are never wrapped. On an Azure DevOps
host, write no `<details>` block: it renders no HTML, as [the Change
outline](pr-body.md#the-change-outline) notes.

Write a wrapped section once, in the write that finishes it. Only that write
carries the block: the `## Review` placeholder written at open and any interim
write carry none. Review is finished at the phase-7 exit, and Verification at
the write that carries its phase-3 results. A section write keeps the old
section's `<details>` blocks verbatim below the new content by default, so a
plain second write of a wrapped section leaves the first block behind. Where a
finished wrapped section must change anyway, rewrite it with
`--section <name> --drop-records` and a body file carrying the whole new
`<details>` block: every old block is dropped, so the body file must carry all
the section keeps.
