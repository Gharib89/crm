---
name: audit-crm-skill
description: Audit the shipped crm skill (crm/skills/) against the live CLI surface, the repo's skill house rules, and Microsoft D365 best practices — then apply the surviving findings as one docs PR.
disable-model-invocation: true
---

# audit-crm-skill

Review and update `crm/skills/` (the shipped agent skill: `SKILL.md` + `reference/*.md`).
The audit hunts four defect classes — **phantom** (skill names a command/flag the CLI
doesn't have), **gap** (CLI capability or live-confirmed gotcha the skill never
teaches), **restatement** (prose that repeats what `--help` already says), and
**stale** (claim contradicted by current code) — then fixes what survives
verification.

Invoke the **`writing-great-skills`** skill before editing anything: it supplies the
vocabulary (duplication, no-op, sediment, sprawl, progressive disclosure) the whole
pass is judged with.

## 1. Build the ground truth

Dump the CLI catalogue and flatten it to one line per leaf command:

```bash
.venv/bin/python -m crm --json describe > "$SCRATCH/describe.json"
```

```python
# flatten: describe.json "commands" is a FLAT list; nodes carry path/is_group/params
import json
S = "<scratchpad>"
d = json.load(open(f"{S}/describe.json"))["data"]
lines = []
for n in d["commands"]:
    if n.get("is_group"):
        continue
    opts = [x for p in n.get("params", [])
            for x in p.get("opts", []) + p.get("secondary_opts", [])
            if x.startswith("--")]
    h = (n.get("help") or "").split("\n")[0]
    lines.append(f'{n["path"]} :: {h} :: OPTS: {" ".join(opts)}')
open(f"{S}/commands.txt", "w").write("\n".join(sorted(lines)))
print(len(lines), "leaf commands")   # sanity floor: expect > 200
```

Never run the `crm` binary for inspection — a repo hook blocks it; always
`.venv/bin/python -m crm`. `--json` is a global option: it goes **before**
`describe`.

**Done when:** `commands.txt` exists and its count clears the sanity floor.

## 2. Fan out the audits (parallel subagents)

Launch in one message; give every agent the `commands.txt` path and the house
rules from CLAUDE.md → "Keep docs in sync with code" → the SKILL ↔ CLI bullet
(self-contained, never restate flags/choices/defaults, single source of truth).

1. **Coverage** (sonnet) — every command group in `commands.txt` accounted for:
   covered, deliberately-thin local/meta, or a gap. Grep the skill tree for every
   `crm <group> <verb>` mention and verify it exists (phantoms). Check every
   reference file is reachable from SKILL.md's router table and every
   D365-touching group has a router row.
2. **Reference audits** (sonnet, split the 22 files across two agents) — per
   file: restatements vs `--help`, duplication across files, stale claims
   (reproduce against `commands.txt` / `--help` / source before flagging),
   no-ops, sprawl, repo-path leaks (links to sibling `reference/*.md` and
   SKILL.md are fine — they ship together).
3. **Best practices** (opus) — check the guidance files (customization-lifecycle,
   customizations-as-code, authoring, solutions, metadata-writes) against
   Microsoft Learn (MCP tools via ToolSearch). Every finding cites a fetched Learn
   URL and carries an applicability tag: **on-prem / cloud / both** — on-prem is
   this project's priority target, so cloud-only advice (elastic tables, Power
   Automate-instead-of-classic-workflows, Power Fx) is *correctly absent*, not a
   gap.

**Done when:** all agents have reported and every one of the 39-ish command
groups appears in exactly one coverage bucket.

## 3. Verify before editing

Findings are claims, not facts. Before an edit lands:

- A **stale/contradiction** finding must be reproduced against the pinned code
  (`--help`, `crm/core/*`), not accepted from the agent's say-so.
- An **impossibility claim** (in the skill or in a finding) gets special
  suspicion in both directions: check whether the *platform* allows what the
  *CLI* doesn't expose (and vice versa) before calling either side wrong.
- Cross-check the local quirks memory (`project-d365-api-quirks` in this
  project's memory dir, when present) — live-confirmed gotchas missing from the
  skill are the highest-value gaps.

**Done when:** every finding is marked apply / decline-with-evidence.

## 4. Apply as one docs PR

Worktree per CLAUDE.md branch discipline. While editing, hold the house rules:
fix a restatement by **deleting**, not rewording; a new gotcha goes in the file
that owns its domain (single source — other files point); never cite issue
numbers or repo paths inside `crm/skills/` content.

Gates before the PR:

```bash
grep -rn 'docs/\|CONTEXT.md\|CLAUDE.md\|crm/tests' crm/skills/   # self-containment: expect no hits
.venv/bin/pytest -q -k "skill"                                    # offline skill-tree tests
mkdocs build --strict                                             # docs CI parity (crm/** triggers it)
```

Commit as `docs(skills): …` (no release bump). PR ends the run — merging is the
maintainer's call.

**Done when:** gates green, PR open, PR body lists applied findings *and*
declined ones with their evidence.
