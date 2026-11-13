# A tracker issue on Targets:

Read this file only when the profile's `Targets:` names an issue by number:
it carries the draft in phase 4, the write after the merge and the unattended
form.

A `Targets:` entry naming an issue by number (`#<n>`, such as `map issue #1`) is
met in phase 4 by a **drafted section**, not a file edit: the new content of
each `## ` section the change affects, one draft per section, written to
`tracker-<n>-<k>.md` beside the Run file (`<n>` the
tracker issue, `<k>` the draft's ordinal) and named with its heading in the Run
file's `## Design and plan`. Beside each draft, save the section as `read-issue`
returned it, as `tracker-<n>-<k>.base.md`. On Azure DevOps that body is the
description's HTML: for the `<pre>` block ship writes, the markdown is the text
inside it, entities decoded. The summary's `Tracker` item shows each draft
verbatim, so the human approves the text with the merge. The unit is the whole
section, because `update-issue-body` replaces nothing smaller.

A drafted section is written after the merge and never before, so no issue
records code that has not landed: once `merge` answers `merged: true`, and
before `cleanup`, run `update-issue-body <n> --section "<section>" --body-file
<draft>` per tracker draft, `<section>` verbatim from the heading `read-issue`
returned. Re-read the issue first: a section that no longer matches its base is
redrafted, posted, and written on the human's explicit "yes". `created: true`
for a section the draft meant to replace means the name missed: re-run with the
returned heading, and name the stray section as a Ship defect. Exit 1 means
nothing was written: a Ship defect for the summary, with the tracker draft
attached.

The unattended lane runs no merge, so under each draft the summary gives the
command a human runs after merging, from a file they save the draft to,
`.claude/skills/ship/scripts/update-issue-body.sh <n> --section "<section>"
--body-file <file>`, once they have checked the section has not changed.
