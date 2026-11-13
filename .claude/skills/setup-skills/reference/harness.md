# Harness

Harness rules for [setup-skills](../SKILL.md), read only where the repo carries a harness profile: step 3 runs [Probe](#probe); step 5's **Cloud bootstrap** item writes [Cloud bootstrap](#cloud-bootstrap).

## Probe

The harness profile, `docs/agents/harness.md`, is the one signal: a bare `scripts/check.sh` or a harness `CLAUDE.md` block is not. Read two of its lines, `Location:` under `## Check entry point` and `Setup:` under `## Cloud`, and run `<Location> full` once from the repo root, where `<Location>` is that harness line, not the ship profile's.

The entry point conforms when that one run exits 0 to 3 with one JSON line on stdout carrying `rung`, `verdict` and a `checks` object. The exits are 0 pass, 1 a red check, 2 a check whose tool is unavailable and 3 over budget; each is a conforming answer, because the local gate maps every check from `checks` and an unavailable one still names itself there. A setup machine missing one tool therefore conforms: report the unavailable check so the human can install its tool, and go on. An exit above 3, or an exit of 2 with no JSON line (a tooling error), does not conform.

A non-conforming entry point is reported as `harness present, check entry point non-conforming: re-run /setup-harness`, and **Local gate over the harness** stops there: no gate is written or rewritten, since a gate over that entry point would map nothing. An existing gate is still run once in step 5, and the ship profile's `Location:` records it; with none, `Location:` records `scripts/local-gate.sh`, the path a setup-skills re-run writes once `/setup-harness` has fixed the entry point, and step 7 tells the user that no local gate exists until then. Every other item goes on, **Cloud bootstrap** included, which never calls `check.sh`.

## Cloud bootstrap

Write `scripts/cloud-ship-bootstrap.sh` from [cloud-ship-bootstrap.sh](../cloud-ship-bootstrap.sh). It reads `Setup:` from `docs/agents/harness.md` at run time and runs that cloud setup, so nothing is filled for it. Fill `SCANNER` and `SCANNER_INSTALL` as the template's configuration comment says. Never fill `SCANNER_INSTALL` with a GitHub release-asset download, which a cloud session's network blocks, so an existing bootstrap's pinned download is replaced rather than kept. Fill the marked slot with the repo's Ship-only steps. A step belongs to the harness cloud setup unless it needs something only Ship has, such as live-e2e credentials or profiles.

An existing bootstrap is proposed as a rewrite onto the template: drop each step the cloud setup covers, matched by what it installs, keep the rest as Ship-only steps, list each ambiguous one for the human and keep or drop each on their answer, show the diff, write on confirm.
