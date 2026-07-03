---
status: accepted
---

# Read-only profile: a TTY-gated guardrail, not a security boundary

A profile that can only read is a common operational wish: point a coding agent
or a CI job at a production org for inspection without risking a stray write. The
CLI has no in-process hard boundary that can enforce this against the same OS
user — the agent runs as that user and can hand-edit `CRM_HOME/profiles/*.json`,
clone the profile into a fresh `CRM_HOME`, or (WSL plaintext fallback) read the
stored `_secret` and mint a new writable profile. So the honest framing is a
**guardrail** that prevents accidental writes, not a **security boundary** that
withstands a determined same-user process (issue #665).

## Decision

Add a per-profile `read_only` flag enforced at the **backend request seam**, with
an asymmetric flip UX.

- **Storage.** `ConnectionProfile` gains `read_only: bool = False`. Absent in an
  existing profile JSON → `False` (back-compat, no migration).
- **Enforcement — one seam.** In `D365Backend.request()`, beside the dry-run
  gate: if the profile is read-only and the method is not GET and the path is not
  a read-safe action, raise `D365Error` (operational failure, `ok:false`,
  exit 1). The message names the profile and the fix. `batch()` (`$batch`, the
  bulk-write path) is refused unconditionally — no GET-only batch use exists
  today. No verb-level gating (a new command would slip through) and no
  forced-dry-run (that fakes success — exit 0 would lie to an agent).
- **Order: dry-run first.** The dry-run check runs before the read-only check, so
  `--dry-run` on a read-only profile returns previews (dry-run never hits the
  wire — strictly safer). A read-only refusal must never masquerade as a dry-run
  preview.
- **Read-safe action allowlist.** One named constant (`READ_SAFE_ACTIONS`) at the
  seam exempts the export POST actions that extract rather than mutate:
  `ExportSolution`, `ExportSolutionAsync`, `DownloadSolutionExportData`,
  `ExportTranslation`. The async export variants create a transient server-side
  `asyncoperation` row — accepted, conceptually a read.
- **Asymmetric flip UX.** Setting the flag on is unrestricted — `profile add
  --read-only`, `profile edit --read-only`, and a wizard y/N step (default N) —
  so you can tighten from anywhere, including non-interactively. Clearing it
  (`profile edit --no-read-only`) requires a real TTY plus a y/N confirmation;
  under `--json` / no-TTY it errors cleanly and tells the user to run it from
  their terminal. So a coding agent (no TTY on its Bash tool) cannot flip the
  guardrail off via the CLI. `profile rm` / `set-password` / `delete-password`
  stay ungated (gating them solves nothing — an agent with creds can mint a fresh
  profile, and CI legitimately creates and deletes read-only profiles).
- **Visibility is passive.** `profile list` (human marker + JSON `read_only`),
  `connection status` / `whoami`, and `profile add`/`edit` output surface the
  flag. No per-command banner — the loud refusal is the teaching moment.

## Considered options

- **A soft flag / signed flag / keyring trick.** Rejected: same-OS-user access
  makes any client-side tamper-proofing theater. The flag is a guardrail; the
  honest hard boundary is a server-side read-only security role, which the docs
  and `--help` point to.
- **Verb-level gating** (mark which commands write). Rejected: a newly added
  command would slip through silently. The backend seam sees every call.
- **Forced dry-run under read-only.** Rejected: it fakes success (exit 0) and
  would let an agent believe a write landed when it didn't.
- **Gating `rm` / a per-run global `--read-only` flag.** Rejected: gating `rm`
  solves nothing (mint a fresh profile), and a per-run flag is a different feature
  (a run-scoped safety, not a durable profile property) that wasn't asked for.
- **Shipping a harness hook** to block edits to the profile JSON. Out of scope —
  the repo's `.claude/hooks/` stays dev-only; not shipped to users.

## Consequences

- Real enforcement against a determined same-user process still requires a
  **server-side read-only role** — the flag is explicitly documented as a
  guardrail, not a boundary.
- No new D365-touching verb is added (`profile` is a local group) — the gate is a
  client-side refusal proven **offline** (the mutation raises before any HTTP).
  No new live e2e `@covers` is warranted.
- A read-only refusal is distinct from a dry-run preview: refusal = `ok:false`
  exit 1; preview = `ok:true` exit 0. Tooling that reads the envelope can tell
  them apart.
