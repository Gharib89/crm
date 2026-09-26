# Phase 6: the PR body

## Contents

- [What the body carries](#what-the-body-carries)
- [The Change outline](#the-change-outline)
- [Special things to note: the folded deviations](#special-things-to-note-the-folded-deviations)
- [Needs attention: what the run left for someone](#needs-attention-what-the-run-left-for-someone)
- [Writing and reading it back](#writing-and-reading-it-back)

The body is written once at `open-pr` and rewritten a section at a time after
that, so it is the one artifact of the run a human reads without the
transcript. It is written **for the reviewer**, not as the run's record: the
merge summary is the record, so nothing the body leaves out is lost. Phase 6
opens it; phase 7 rewrites three of its sections at exit; phase 9 reads it at
the gate. The three top headings are taken from the `visual-pr` skill's PR
template (humanlayer/skills): its template text is the source, its workflow is
not, and ship does not compose it.

## What the body carries

The repo's template per the profile's `## PR`, filled through its own headings;
**with no template, the run writes the same seven headings itself**, because
`open-pr` synthesizes none and a body that skips one is a body phase 7 cannot
write into. Every variant carries:

- **`Closes #<issue>` on its own line above the first `## ` heading**, where no
  section rewrite reaches it. The mechanic translates it for the host, and puts
  it there itself when the body arrives without one.
- **`## Why the change`**: exactly one sentence, the problem and what becomes
  possible now.
- **`## Change outline`**: the behavioural fence below.
- **`## Special things to note`**: the Door line first, then the reviewer's
  warnings and the folded deviations, so it is never `None.`.
- **`## Needs attention`**: every issue the run filed or linked and every Ship
  defect it met, `None.` when there are neither.
- **`## Verification`**: one line per applicable verification in the merge
  summary's `Verification` row format, or `None applicable: <reason>`, with three
  such reasons and no fourth: `class docs`, `small lane`, and a full-lane change
  where no `Applies when:` line matches (a profile listing zero verifications
  is that case).
- **`## Review`**, one placeholder line per reviewer at open, filled at the
  phase-7 exit in the fixed shape [review-loop.md](review-loop.md) carries.
- **A count with the command that produced it**, wherever the body measures the
  tree, under the rule [merge-gate.md](merge-gate.md) carries.
- **The attribution footer**, where the environment provides one for pull
  request descriptions, in the body file `open-pr` is handed. The body **ends**
  with it, under a `## ` heading of its own that ship adds where no template
  carries one (`## Attribution`), after every section a later phase rewrites;
  [update-pr-body](../scripts/update-pr-body.sh) carries why that placement
  survives.

## The Change outline

Draw it from the diff at phase 6, not from phase 2's design; a redraw is not a
deviation.

It is a `diff` fence over a call tree, control flow, pseudocode or component
tree, showing what the change **does**. Text forms only: Azure DevOps renders
neither mermaid nor HTML, and the `diff` fence shows the before and the after in
one view. **One behavioural fence per PR**, about 15 lines or fewer; a change
that needs two is a PR spanning two concerns. A **carrier file tree** may follow
in a second fence, only when the same edit lands in more than two files; it is
not behavioural, so the count and the budget are the first fence's alone. Every
node is a real symbol, each tree's root carries its file path, and no line
carries a line number: the first review-round push rots them.

**The hatch is narrow.** The visible line `Shape: none, mechanical (<kind>).`
replaces the fence only where the reviewer's question is "did the text change
correctly", never "what does X now do", and it is visible so the self-review and
the reviewer can dispute the call. A comment reword that changes which outcome a
reader expects has a control-flow shape even though the diff is comments. The
small lane takes no exemption. `show-me` supplies the form: read it from
`.claude/skills/show-me/SKILL.md` rather than load it, because its upstream sets
`disable-model-invocation`, which bars the Skill tool and not a read; the
constraints are ship's, and its menu of other uses is not.

## Special things to note: the folded deviations

What the reviewer must know before reading the diff: warnings, migrations,
compatibility constraints, deliberate omissions. At most five bullets below the
Door line, one sentence each; a sixth means the body is becoming the record
again.

**The first bullet is always the Door line**, in this shape:

```text
- Door: <one-way|two-way>. Blast radius: <one clause>.
```

One-way is a merge nobody can walk back: a released breaking change consumers
have already adopted, a data migration, a deleted artifact. Two-way is a
revert. The blast radius names who else feels it rather than grading it. The
line sits **outside the five-bullet ceiling**: a reviewer deciding whether to
approve reads it first, and a change carrying five warnings is exactly the one
that needs it.

The deviations log stays verbatim in the merge summary. What reaches this
section is the subset that would change how the reviewer reads the diff,
**grouped by the claim or decision they share**: three departures forced by one
wrong number in the issue are one bullet about that number. A deviation nobody
reviewing the diff would act on does not appear.

## Needs attention: what the run left for someone

One line per issue the run filed or linked, `- #<n> <title>: <one line why it
matters>`, and one per Ship defect met, `- Ship defect: <what>`. `None.` when
there are neither. Nothing else belongs here: an adjacent find is filed or fixed
inline by phase 2's dispositions, so an observation parked here is a find that
skipped its disposition. It is written from the Run file's record, not the
deviations log.

## Writing and reading it back

`update-pr-body <pr> --section <name> --body-file <path>` replaces one section
and writes its `## <name>` line itself, so the file holds the content alone.
`--preamble` replaces everything above the first heading, where the closing line
sits, carrying the old closing line over when the new content lacks one; a body
with no heading is preamble entire.

**Every title or body write to an open PR ends with `read-pr <pr>`**, whose
`## ` headings are checked against the ones the body owes: a section a rewrite
swallowed is missing there, and that is the only place a swallowed `##
Attribution` shows while the PR is open. `update-pr-body`'s `sections` list is
the same check one write earlier. Read the headings fence-aware: a Change
outline fence over a markdown change carries `## ` lines of its own, which are
example text and not sections.
