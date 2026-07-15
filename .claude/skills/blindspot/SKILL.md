---
name: blindspot
description: Map the user's unknown-unknowns before work starts — a read-only blindspot pass over an unfamiliar codebase area, domain, or tool. Use when the user says "blindspot pass" or "unknown unknowns", discloses that the area is new or unfamiliar to them, or asks to be taught a domain so they can prompt better.
metadata:
  internal: true
---

# blindspot

A blindspot pass maps what the user doesn't know they don't know, then teaches them enough to prompt well. It is read-only: report and stop — implement nothing, edit nothing.

## 1 · Anchor the starting point

The pass is only as good as its picture of the user. Establish from the conversation — asking one or two questions only for what's genuinely missing — what they're trying to do, what they already know, and where they are in their thinking. Done when you can state their goal and experience level in one line each.

## 2 · Survey the territory

Explore what the user can't yet see. For a codebase area: structure, conventions, prior art (`git log`/`blame` on the relevant paths), and the gotchas the code implies. For a domain or tool: core concepts, what the quality ceiling looks like, common failure modes — pull current docs rather than trusting memory. Delegate broad sweeps to subagents; keep the synthesis here. Done when new digging stops changing the picture.

## 3 · Report the blindspots

Rank findings by how much each would change the user's approach:

- Questions they should be asking but aren't.
- What "good" looks like here — they may not know how good the result can be.
- Prior art and historical decisions that constrain or shortcut the work.
- Potholes: known failure modes, deceptive simplicities, things that look done but aren't.

For a taste domain — one where the user will only recognize what they want on sight (visual design, color grading) — teach the vocabulary instead: the named concepts and what varying each one looks like, so they can react to options rather than specify blind.

## 4 · Hand back the prompts

End with two or three prompts the user could now write, each incorporating something the pass surfaced. Done when every ranked blindspot either appears in a suggested prompt or is explicitly called ignorable for this task.
