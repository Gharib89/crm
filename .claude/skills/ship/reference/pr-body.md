# Phase 6: the PR body

## Contents

- [What the body carries](#what-the-body-carries)
- [The Change outline](#the-change-outline)
- [Special things to note: the folded deviations](#special-things-to-note-the-folded-deviations)
- [Needs attention: what the run left for someone](#needs-attention-what-the-run-left-for-someone)
- [Two halves, and a mechanic reaches each](#two-halves-and-a-mechanic-reaches-each)
- [Reading the body back](#reading-the-body-back)

The body is written once at `open-pr` and rewritten a section at a time after
that, so it is the one artifact of the run a human reads without the
transcript. It is written **for the reviewer**, not as the run's record: the
merge summary is the record, and it already carries the deviations log verbatim,
a per-finding outcome for every reviewer, the issues filed and linked, and the
Ship defects. Nothing the body leaves out is lost, so the body may leave it out.
Phase 6 opens it; phase 7 rewrites three of its sections at exit
([review-loop.md](review-loop.md)); phase 9 reads it at the gate.

The three top headings are taken from the `visual-pr` skill's PR template
(humanlayer/skills): its template text is the source, its workflow is not, and
ship does not compose it.

## What the body carries

The repo's template per the profile's `## PR`, filled honestly through its own
headings rather than a raw body that bypasses it. **With no template, the run
writes the same seven headings itself**: `open-pr` synthesizes none of them, so
a plain body that skips one is a body phase 7 cannot write into and a reviewer
reads without the section. Every variant carries:

- **`Closes #<issue>` on its own line above the first `## ` heading.** The
  mechanic translates it for the host, and puts it there itself when the body
  arrives without one. Above the first heading is where no section rewrite
  reaches it.
- **A `## Why the change` section**: exactly one sentence, the problem and what
  becomes possible now.
- **A `## Change outline` section**: the behavioural fence below.
- **A `## Special things to note` section**: the Door line first, then the
  reviewer's warnings and the folded deviations below. The Door line is always
  there, so this section is never `None.`. Phase 7 rewrites it where a round
  grew the log.
- **A `## Needs attention` section**: every issue the run filed or linked and
  every Ship defect it met, `None.` when there are neither. Phase 7 rewrites it
  where a round added to any of the three.
- **A `## Verification` section**: one line per applicable verification, read
  from the phase-3 results in the merge summary's `Verification` row format, or
  `None applicable: <reason>` where none applied. There are three such reasons
  and no fourth: `class docs` and `small lane` skip the phase; a full-lane
  change where no `Applies when:` line matches is the third, and a profile
  listing zero verifications is that case. [merge-gate.md](merge-gate.md) reads
  the same result into the summary, so the two agree by construction.
- **A `## Review` section**, one placeholder line per reviewer at open, filled
  at phase-7 exit with that reviewer's line in the fixed shape
  [review-loop.md](review-loop.md) carries.
- **A count with the command that produced it**, wherever the body **measures
  the tree**, run on the PR head. A tally of the run's own work is the
  exception, which is what the `## Review` line is made of;
  [merge-gate.md](merge-gate.md) carries the rule and its exception, and the
  Run-file record the tallies come from.
- **The attribution footer**, where the environment provides one for pull
  request descriptions. It belongs to **the body file `open-pr` is handed**, and
  the body **ends** with it, under a `## ` heading of its own that ship adds
  where no template carries one (`## Attribution`), placed after every section a
  later phase rewrites. That placement is the whole rule, and
  [update-pr-body](../scripts/update-pr-body.sh) carries why it is the one that
  survives. The footer's lines are the environment's; ship says where they sit.

## The Change outline

Draw it from the diff at phase 6, not from phase 2's design; a redraw is not a
deviation.

It is a `diff` fence over a call tree, control flow, pseudocode or component
tree, showing what the change **does**. Text forms only, which rules out mermaid
and HTML: Azure DevOps renders neither, and the `diff` fence is the one form
that shows the before and the after in a single view. **One behavioural fence
per PR**, about 15 lines or fewer; a change that needs two is a PR spanning two
concerns. A **carrier file tree** may follow it in a second fence, and only when
the same edit lands in more than two files, where naming them one by one in
prose costs more than the tree; that second fence is not behavioural, so the
count and the line budget above are the behavioural one's alone. Every node is a
real symbol, each tree's root node carries its file path, and no line carries a
line number: the first review-round push rots them and nothing rewrites the
outline.

**The hatch is narrow.** The visible line `Shape: none, mechanical (<kind>).`
replaces the fence only where the reviewer's question about the change is "did
the text change correctly", and never where it is "what does X now do"; it is
visible rather than a silent omission so the self-review and the reviewer can
dispute the call. A comment reword that changes which degraded reason a reader
expects has a control-flow shape even though the diff is comments, and taking
the hatch there leaves the reviewer without the one view that answers its
question. The small lane takes no exemption either: a
one-line behaviour fix is where four lines of control-flow diff pay for
themselves. `show-me` supplies the form: read it from
`.claude/skills/show-me/SKILL.md` rather than load it, because its upstream
sets `disable-model-invocation`, which bars the Skill tool and not a read.
These constraints are ship's, and `show-me`'s menu of other uses is
not.

## Special things to note: the folded deviations

What the reviewer must know before reading the diff: warnings, migrations,
compatibility constraints, deliberate omissions. At most five bullets below
the Door line, one sentence each; a sixth means the body is becoming the record
again.

**The first bullet is always the Door line**, in this shape:

```text
- Door: <one-way|two-way>. Blast radius: <one clause>.
```

One-way is a merge nobody can walk back: a released breaking change consumers
have already adopted, a data migration, a deleted artifact. Two-way is a
revert. The blast radius names who else feels it rather than grading it. The
line sits **outside the five-bullet ceiling**, because a reviewer deciding
whether to approve reads it before the warnings, and a change carrying five
warnings is exactly the one that needs it. It is the bullet the section always
carries, which is why the section has no `None.` form.

The phase-2 deviations log stays **verbatim in the merge summary**. What reaches
this section is the subset that would change how the reviewer reads the diff,
**grouped by the claim or decision they share** rather than one bullet per
event: three departures forced by one wrong number in the issue are one bullet
about that number. A deviation nobody reviewing the diff would act on does not
appear here at all.

## Needs attention: what the run left for someone

One line per issue the run filed or linked, `- #<n> <title>: <one line why it
matters>`, and one per Ship defect met, `- Ship defect: <what>`. `None.` when
there are neither.

Nothing else belongs here. An adjacent find is filed or fixed inline by phase
2's dispositions, so an unfiled observation parked in this section is a find
that skipped its disposition. Phase 6 writes the section from the run's record
of what it filed, linked and met, which is the Run file and not the deviations
log, under the recording rule [merge-gate.md](merge-gate.md) carries; the
phase-7 exit rewrites it where a round added to any of the three, the same
pattern `## Special things to note` follows.

## Two halves, and a mechanic reaches each

`update-pr-body <pr> --section <name> --body-file <path>` replaces one section.
`--preamble` replaces everything above the first heading, where the closing line
sits. A body with no heading at all is preamble entire, so a `--preamble` write
replaces the whole of it. A preamble write carries the old closing line over
when the new content lacks one, so the link to the issue survives the rewrite.
Each `--body-file` holds the section's content and not its heading, per
[review-loop.md](review-loop.md).

## Reading the body back

**Every title or body write to an open PR ends with `read-pr <pr>`.** After a
body write, check the `## ` headings of the body it returns against the ones the
body is supposed to carry: a section the rewrite swallowed is missing from them,
and that is the only place a swallowed `## Attribution` shows while the PR is
still open. `update-pr-body` answers with a `sections` list, which is that same
check one write earlier. Read the headings the way the slice does, fence-aware:
the fence trap is a Change outline fence over a markdown change carrying `## `
lines of its own, which are example text and not sections.
