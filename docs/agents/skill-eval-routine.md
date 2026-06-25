# Skill-eval baseline routine

The **periodic** half of the behavioral skill eval (ADR 0015, Machine B). A scheduled run
fires the both-targets runner and appends a dated per-target pass-rate row to the tracked
`evals/skill/baseline.md`, so **effectiveness drift surfaces as a sliding trend** a human
reads. It is **never a CI gate** and no threshold blocks anything — this is monitoring, not
enforcement.

The one fire does:

```bash
D365_E2E_ALLOW_HOST=<cloud-host> CRM_EVAL_AGENT_CMD='claude -p' \
    python -m evals.skill.both_runner --repeat 3 --update-baseline
```

then opens a PR with the appended `baseline.md` row(s). `both_runner` runs the set against
each **reachable** target (`agent-cloud`, then `agent-on-prem`), unions the coverage, and
**skips** an unreachable one with a `—` row rather than failing (see
`evals/skill/README.md`). `--repeat 3` runs each task three times so the recorded pass-rate
is a fraction that smooths run-to-run variance.

## Two cadences (pick by what reaches both targets)

On-prem is VPN-gated, so **only a host on the VPN sees both targets**:

- **Local schedule (covers both targets).** A `cron`/`launchd`/Task Scheduler entry on a
  maintainer machine that has the `crm` binary, the installed skill, a Claude login, the
  `agent-cloud` + `agent-on-prem` profiles, and the VPN. This is the one cadence that lands
  **both** legs' rows. Example weekday-morning cron:

  ```cron
  30 6 * * 1-5  cd ~/wip/projects/crm && git switch -c chore/baseline-$(date +%F) && \
      D365_E2E_ALLOW_HOST=<cloud-host> CRM_EVAL_AGENT_CMD='claude -p' \
          python -m evals.skill.both_runner --repeat 3 --update-baseline ; \
      git add evals/skill/baseline.md && git commit -m "chore(evals): skill-eval baseline $(date +%F)" && \
      gh pr create --fill --base main   # main is gated; land the trend via PR, not a direct push
  ```

  The runner is sequenced with `;`, **not** `&&`, before the commit: `--update-baseline`
  appends the row *before* the process exits, and a run with task failures exits non-zero —
  but a low pass-rate is exactly the drift the trend exists to record, so the commit/PR must
  land the row **regardless** of the eval's exit code.

- **claude.ai routine (cloud leg only).** Mirrors the `cloud-ship` routine
  (`docs/agents/cloud-ship-routine.md`): a scheduled routine whose environment is configured
  for the cloud org. Its sandbox **cannot reach on-prem** (no VPN), so the on-prem leg skips
  there — the routine keeps the always-on **cloud** trend fresh; the local schedule above
  fills the on-prem rows. Configure the routine environment as for cloud-ship, plus
  `CRM_EVAL_AGENT_CMD=claude -p`, `D365_E2E_PROFILE=agent-cloud`, and
  `D365_E2E_ALLOW_HOST=<cloud-host>`; point its prompt at the one-fire command above and
  have it open the PR.

Either way the baseline row lands via a **PR** — `main` is protected, so a scheduled job
must not push to it directly.

## Why a fraction, and why no gate

Absolute pass-rate measures "can an agent do these tasks," not "because of the skill" (ADR
0015 deliberately drops the A/B control arm), so the number is only meaningful **as a
trend**. `--repeat` turns each task's verdict into a fraction so one flaky run doesn't read
as a cliff. A drop sustained across several dated rows is the signal to investigate the
skill; a single dip is noise. Nothing automated acts on it — a human reads `baseline.md`.

## Cost note

A fire runs the full task set through a real `claude -p` agent against live orgs, once per
`--repeat`. That is minutes of agent time and real tokens per target — appropriate for a
weekly/weekday cadence, **not** per-PR (which is exactly why ADR 0015 keeps it out of CI).
