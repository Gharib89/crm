---
name: live-e2e
description: Run the live e2e suite or verify a change against a live D365 org. Use when running `pytest -m e2e` / `D365_E2E=1`, picking a live target or profile (agent-cloud, agent-on-prem, agent-cs-trial), or exercising worktree code against a live org.
---

# Live e2e runs

This is the operational recipe — target, creds, invocation, tripwires. Suite internals (fixtures, capability gates, coverage gate, discovered bugs) live in `crm/tests/TEST.md`.

Done = the run's org was confirmed via `crm connection whoami --profile <name>` and the result is reported **with its target** — cloud-green ≠ on-prem-green.

## 1. Pick the target

Two standing profiles, plus one ephemeral:

- **`agent-cloud`** (OAuth / Dataverse online) — prefer for general verification: always reachable, no VPN.
- **`agent-on-prem`** (NTLM on-prem v9.1 test org) — needed for `requires_onprem` and target-divergent checks. VPN required.
- **`agent-cs-trial`** (ADR 0012) — an **ephemeral** OAuth profile pointing at a Customer-Service-provisioned Dataverse trial, standing in for CS-dependent verbs the general `agent-cloud` org can't host (`sla create`/`add-kpi`, `audit detail`, `workflow run`). A self-service CS trial expires (≤60 days), so **`agent-cloud` stays *the* cloud target and CI stays pointed at it** — never re-point `agent-cloud` or the CI cloud secret at the trial. The CS-verb tests skip-with-instructions when the trial is absent, so they run only on local, opportunistic `--profile agent-cs-trial` runs while the trial lives. The trial's `*.dynamics.com` host changes each time one is provisioned (kept in local memory, not committed) — set `D365_E2E_ALLOW_HOST` to that host for a local run.

Pin `--profile <name>` on any live command and confirm the org via `crm connection whoami` before reporting target-specific facts.

**A bug reported on a specific target must be verified on THAT target.** Cloud silently reassigns server-side ids and quietly rewrites/accepts inputs that on-prem v9.x rejects, so a cloud-green run can mask an on-prem-only failure. For an on-prem-reported bug, run the on-prem leg.

## 2. Point the suite at it

The suite is opt-in: `D365_E2E=1`, plus one of two cred sources:

- **A named profile (preferred locally)** — `D365_E2E_PROFILE=<name>`, a profile created with `crm profile add`. Its creds + secret are read **read-only** from your real `CRM_HOME` and re-seeded into a throwaway, isolated `CRM_HOME` (real profiles/session never mutated). The **target is inferred from the profile's auth scheme**: OAuth → cloud, NTLM → on-prem. No `D365_*` cred env needed.
- **Flat `D365_*` env** — `D365_URL` + creds directly (NTLM: `D365_USERNAME`/`D365_PASSWORD`; OAuth: `D365_AUTH=oauth` + `D365_CLIENT_ID`/`D365_TENANT_ID`/`D365_CLIENT_SECRET`). This is how **CI** runs (secrets → env). Used automatically when `D365_E2E_PROFILE` is unset.

**Both targets** = run the suite once per profile (`D365_E2E_PROFILE=<cloud>` then `=<onprem>`); coverage is the union — there is no single-process "both".

**Cloud needs a host-guard override.** The suite refuses destructive runs against a `*.dynamics.com` host; pass `D365_E2E_ALLOW_HOST=<exact host>` to opt the designated test org in.

**On-prem unreachable → session skips** with a "VPN down?" message (any HTTP response, incl 401/403, counts as reachable). Tripwire: the *first* on-prem run right after connecting the VPN can false-skip — a cold NTLM probe can exceed the reachability timeout. Re-run once before concluding the target is down.

## 3. Worktree code through the `cli` fixture

The fixture resolves `shutil.which("crm")` → the installed **PyInstaller binary** (`~/.local/bin/crm`), which **ignores `PYTHONPATH`** (it bundles its own code), so an e2e run silently exercises the OLD installed code, not your worktree fix. The venv console-script `.venv/bin/crm` is *also* on PATH and *does* honor `PYTHONPATH`, so stripping only `~/.local/bin` isn't enough. Strip **both** crm dirs so `which` returns nothing and the fixture falls back to `[sys.executable, "-m", "crm"]`:

```bash
NEWPATH=$(echo "$PATH"|tr ':' '\n'|grep -vE '/\.local/bin|/crm/\.venv/bin'|paste -sd:)
cd $WT && D365_E2E=1 D365_E2E_PROFILE=<p> PATH=$NEWPATH PYTHONPATH=$WT <main-venv>/bin/python -m pytest -m e2e <node>
```

**cwd beats `PYTHONPATH` — `PYTHONPATH=$WT` is NOT sufficient on its own.** `python -m crm` (the fixture fallback, spawned as a subprocess) and any `-m`/`-c` invocation put cwd as `''` at `sys.path[0]`, *ahead* of `PYTHONPATH` (`sys.path[1]`). If the process cwd is the main checkout (which holds a `crm/` package — and the agent shell's default cwd is the main checkout), `import crm` resolves to MAIN and the worktree fix silently never runs. Run from `$WT` (the `cd $WT` above), or front-load it explicitly (`sys.path.insert(0, $WT)`); the editable-install meta-path finder is *appended* (last in `sys.meta_path`), so it is **not** the cause — cwd is. `pytest` collecting test modules is immune (rootdir insertion), but the `cli`-fixture subprocess it spawns is not.

Tripwire: an e2e that *should* pass with your fix fails **identically to pre-fix** → you're running the frozen binary, OR `import crm` resolved to the main checkout because cwd ≠ `$WT`.

## Writing e2e fixtures

**Never embed real-org identifiers** (public repo). Don't copy GUIDs from a live export into test constants — a captured form/record/role GUID can carry the org's machine fingerprint (a stable MAC-derived 12-hex-digit suffix shared across that org's server-generated GUIDs). Use obvious placeholders (`1111…`, `cccc…`).
