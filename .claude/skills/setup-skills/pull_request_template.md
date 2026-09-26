Closes #

## Why the change

<!-- Exactly one sentence: the problem, and what becomes possible now. The reader has the diff for the how. -->

## Change outline

<!-- One behavioural `diff` fence: call tree, control flow, pseudocode or component tree, text only (no mermaid, no HTML), about 15 lines or fewer. Every node a real symbol, each tree's root carrying its file path, no line numbers. A carrier file tree may follow it, only when the same edit lands in more than two files. `Shape: none, mechanical (<kind>).` replaces the fence only when the reviewer's question is "did the text change correctly", never when it is "what does X now do". -->

## Special things to note

<!-- First bullet, always: `- Door: <one-way|two-way>. Blast radius: <one clause>.` One-way is a merge nobody can walk back; the blast radius names who else feels it. Then reviewer warnings, migrations, compatibility constraints, deliberate omissions, and any deviation from the plan that would change how the reviewer reads the diff: at most five of those, one sentence each, grouped by the claim they share, with the Door line outside that count. No `None.` form, the Door line is always there; the full deviations log stays in the merge summary. -->

## Needs attention

<!-- One line per issue this run filed or linked (`- #<n> <title>: <why it matters>`) and one per Ship defect met (`- Ship defect: <what>`). `None.` when empty; an adjacent find is filed or fixed inline, never left here as an observation. -->

## Verification

<!-- One line per applicable verification the ship profile names, in the merge summary's row format: `- <name>: <phase-3 result>   <what ran>`; ship fills these from the phase-3 results. `None applicable: <reason>` when none was. -->

## Review

<!-- One line per reviewer the ship profile names, in the fixed shape `- <reviewer>: <exit word>, <n> rounds, <raised> findings: <accepted> accepted, <declined> declined, <filed> filed`, with one trailing clause only when the reader must know. A fallback whose primary reviewed takes the second form, `- <fallback>: not invoked: <primary> reviewed`, and states no counts, having none. Ship fills these. `None.` when the repo names no reviewer. -->

## Attribution

<!-- The environment's footer for pull request descriptions, verbatim. It ends the body, below every section a later phase rewrites, which is what keeps a section rewrite from swallowing it. -->
