# Ship profile

Schema: 3

Every repo-specific fact `/ship` needs, one section per axis. Fourteen `##` headings, always present and in this order; a defaulted axis reads `None.` or `Default.` under its own heading. Facts sit on `Label:` lines and nowhere else, and the prose under a heading explains them. The `Schema:` line above is the profile schema `ship` checks at preflight; only a `setup-skills` re-run moves it. Vocabulary: the `ship` skill's source repo, `Gharib89/skills`, [CONTEXT.md](https://github.com/Gharib89/skills/blob/main/CONTEXT.md).

<!-- setup-skills: fill every `Label:` line, replace each <...>, delete these comments. -->

## Host

Host: <github | ado>

<!-- Derived from the origin remote and cross-checked at preflight. Org, project and repo always derive from the remote. -->

## Worktree

Carry: <comma-separated gitignored files copied one-way into the worktree, or None.>
Bootstrap: <command run once after isolate in every lane, or None.>

<!-- Location is core: <parent>/<repo>.worktrees/<slug>-<issue>. Only small dotfiles belong on Carry (.env, *.local); dependency install belongs to the local gate. -->

## Local gate

Location: <scripts/local-gate.sh>
Small node: <the syntax of one test node for --small, with an example; then a docs-class line giving the path of the changed document, with an example>
Tripwires: <anything that must run before the gate, e.g. a bundle rebuild, or None.>

<!-- Output contract, flags and statuses are core. -->

## CI

Legs: <one `name: what it proves` per line; these names are what `## Verification` may defer to>
No-checks legal: <yes | no, with the reason: a path-filtered workflow may legitimately report no checks, and a repo with no PR workflow reports none ever; `yes` with `Legs: None.` is what drops `ci-wait`'s no-checks grace to zero>
Push policy: <e.g. one push per review round; minutes are metered, or Default.>

<!-- Only workflows with a pull_request trigger are legs. Name other workflows in prose here as non-PR. -->

## Reviewers

<!-- Zero or more `### <name>` blocks, or the single line `None.` Trigger fixes mechanics and convergence; brand decides nothing. -->

### <reviewer name>

Login: <the login(s) it reviews under>
Trigger: <auto-once | on-push | on-request>
Request: <on-request only: the mechanic that requests a round, or `comment <phrase>` for a reviewer a PR comment triggers, else None.>
Workflow: <`comment <phrase>` only: the repo-relative path, from the checkout root, of the workflow file that comment starts, whose run is the round's window, else None.>
Cap: <on-request: max rounds, required, no default; on-push: max rounds, or None. for an uncapped loop; auto-once: None.>
Resolve: <on-push and on-request: how a dispositioned thread is resolved, else None.>
Gating: <yes | no>
Fallback-for: <the reviewer this one stands in for, driven only when that reviewer exits degraded; on-request only, else None.>
Instructions: <path of the file this reviewer reads, or None.>

## Coding standards

<one doc path, e.g. docs/contributing/coding-standards.md>

<!-- The canonical file the self-review's Standards axis and every reviewer decline derive from. -->

## Verification

<!-- Zero or more `### <name>` blocks with exactly these seven lines, or the single line `None.` -->

### <verification name>

Proves: <what this proves against the real thing the repo integrates with>
Applies when: <prose the agent judges at classification, not path globs>
Run: <the command>
Needs: <the environment it needs and how to detect it>
Without it: <hand-off | defer-to-ci | blocked>
Also proven by CI: <a leg named in ## CI; required when Without it is defer-to-ci, else None.>
Claims to probe: <the external-system claims to check before building on them>

## Versioning and changelog

Tooling: <semantic-release | changesets | manual | none>
Reads: <what release tooling reads: the squash subject, a tag, a file, or None.>
In-PR requirement: <anything that must land in the PR itself, e.g. a version bump CI enforces, or None.>
Subject constraints: <reserved prefixes or formats for the squash subject, or None.>

## PR

Template: <path; fill it through its own headings rather than a raw body that bypasses it> | None.

<!-- `Closes #<issue>` and the seven sections `## Why the change`, `## Change outline`, `## Special things to note`, `## Needs attention`, `## Verification`, `## Review` and `## Attribution` are core in every repo, in that order. The closing reference sits above the first `## ` heading, where a section rewrite cannot reach it. -->

## Public surface

Default.

<!-- Default. means exported or published API, CLI flags, config schema, file formats. Enumerate instead when the repo's surface is wider (gate rules, palettes, bundle inputs). -->

## Triage

File as an issue labelled `<the needs-triage label from docs/agents/triage-labels.md>`.

## Docs sync

Targets: <artifacts coupled to a change: README.md, docs/, CONTEXT.md, a shipped skill, examples/, an issue such as map issue #<n>, whose `## ` sections ship rewrites after the merge>
Agent-facing: <every path here whose reader is an agent, whether or not it is also a docs-sync target: docs/agents/, .claude/skills/>

## Current docs

Sources: <context7, plus Microsoft Learn where the stack is Microsoft>
Pinned: <libraries whose installed version matters, or None.>

## Cloud lane

PR cap: <number, default 3, or none>
Bootstrap: <path of a sandbox setup script, safe to rerun and on a workstation, or None.>

<!-- The cloud bootstrap repairs the sandbox image; it runs at the start of every run in a cloud sandbox, attended or unattended, and of any `--unattended` run, local included, so it must be safe on a workstation. PR cap: is read by the unattended lane alone. -->
