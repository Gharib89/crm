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
opens it; phase 7 rewrites three of its sections at exit, a fourth, the Change
outline, where it fell short; phase 9 reads it at the gate.

## What the body carries

The repo's template per the profile's `## PR`, filled through its own headings,
each kept verbatim: `open-pr` refuses a body lacking any, naming each, because
once the PR is open a section write cannot rename a reworded heading: it
appends the template's as a second section and leaves the reworded one;
**with no template, the run writes the headings below itself**, because
`open-pr` synthesizes none and a body that skips one is a body phase 7 cannot
write into. Every variant carries:

- **`Closes #<issue>` on its own line above the first `## ` heading**, where no
  section rewrite reaches it. The mechanic translates it for the host, and puts
  it there itself when the body arrives without one.
- **`## Why the change`**: the problem and what becomes possible now.
- **`## Change outline`**: the behavioural fence below.
- **`## Special things to note`**: the Door line first, then the reviewer's
  warnings, the folded adjacent finds and the folded deviations, so it is never
  `None.`.
- **`## Needs attention`**: every issue the run filed or linked, every Ship
  defect it met and every Ship note.
- **`## Verification`**: each applicable verification's phase-3 result and what
  ran, or `None applicable: <reason>`.
- **`## Review`**, one placeholder line per reviewer at open, filled at the
  phase-7 exit with its exit word, what its rounds found and what became of
  the findings ([review-loop.md](review-loop.md#the-exit)).
- **The attribution footer**, where the environment provides one for pull
  request descriptions, in the body file `open-pr` is handed. The body **ends**
  with it, under a `## ` heading of its own that ship adds where no template
  carries one (`## Attribution`), after every section a later phase rewrites.

The `Closes` line, the headings and the attribution verbatim, attribution last
and the Door line are the body's rules, because the host's close, `open-pr`,
later section writes and the reviewer read them. The Change outline keeps the
shape [below](#the-change-outline), and `Needs attention`, `Verification` and
`Review` keep one line per entry, with `None.` or `None applicable: <reason>`
when there is none; past those, what a section says, and at what length, is
written for its reader.

## The Change outline

Draw it from the diff at phase 6, not from phase 2's design; a redraw is not a
deviation. `read-pr`'s `outline_missing` lists the changed paths the outline
does not mention, and is the read-back check for it. A vendored skill's files
(its `skills-lock.json` `source` not `.`) are mentioned by their folder,
`.claude/skills/<name>/`, since they have no source twin to name.

It is a `diff` fence over a call tree, control flow, pseudocode or component
tree, showing what the change **does**. Text forms only: Azure DevOps renders
neither mermaid nor HTML, and the `diff` fence shows the before and the after in
one view. **One behavioural fence per PR**, preferably 15 lines or fewer.
A **carrier file tree** may follow in a second fence, only when the same edit
lands in more than two files; it is not behavioural, so the count and the budget
are the first fence's alone. Every
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
compatibility constraints, deliberate omissions, and each adjacent find folded
into this PR ([implement.md](implement.md#phase-2-adjacent-finds)). **Every
material compatibility or operational risk is included**, related ones grouped
into one bullet.

**The first bullet is always the Door line**, in one of these shapes:

```text
- 🟢 Door: two-way. Blast radius: <who else feels it>.
- 🔴 Door: one-way. Blast radius: <who else feels it>.
```

One-way is a merge nobody can walk back: a released breaking change consumers
have already adopted, a data migration, a deleted artifact. Two-way is a
revert. The blast radius names who else feels it rather than grading it. The
circle lets a reviewer deciding whether to approve see the door at a glance, and
it renders as text on GitHub and Azure DevOps alike.

The deviations log stays verbatim in the merge summary. What reaches this
section is the subset that would change how the reviewer reads the diff,
**grouped by the claim or decision they share**: three departures forced by one
wrong number in the issue are one bullet about that number. A deviation nobody
reviewing the diff would act on does not appear.

## Needs attention: what the run left for someone

One line per issue the run filed or linked, `- #<n> <title>: <one line why it
matters>`, one per Ship defect met, `- Ship defect: <what>`, and one per Ship
note, `- Ship note: <what>`, beside them, so a retro finds the friction after
the run's files are gone. `None.` when there are none. An adjacent find appears
here only once filed: a folded one goes under `## Special things to note`, and a
noted one stays in the merge summary. It is written from the Run file's record,
not the deviations log.

## Writing and reading it back

`update-pr-body <pr> --section <name> --body-file <path>` replaces one section
and writes its `## <name>` line itself, so the file holds the content alone.
`--preamble` replaces everything above the first heading, where the closing line
sits, carrying the old closing line over when the new content lacks one; a body
with no heading is preamble entire. A column-0 `<details>` block is a
`<details>` record, whose rule [mechanics.md](mechanics.md#section-surgery)
states.

A section a rewrite swallowed is missing from `read-pr`'s `headings`, the only
place a swallowed `## Attribution` shows while the PR is open;
`update-pr-body`'s `sections` list is the same check one write earlier.

`open-pr` runs the repo's body checker, the profile's `## PR` `Body-check:`
line, on the body file before the push, and `update-pr-body` on the body it
would publish; on a refusal, fix what the checker's output names and re-run
the call it refused.
