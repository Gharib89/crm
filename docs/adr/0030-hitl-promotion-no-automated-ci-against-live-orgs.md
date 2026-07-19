---
status: accepted
---

# Promotion is human-in-the-loop; automated CI never talks to a live org

No automated pipeline (hosted CI job, scheduled drift check, deploy workflow)
talks to a live Dynamics 365 org. Promotion/verification flows are
**human-in-the-loop**: `plan` → a human approves the plan artifact (in a PR,
per [ADR 0022](0022-plan-artifact-approval-gated-apply.md)) → `--from-plan`
apply runs from a VPN-connected machine. Automated CI is limited to
**org-less checks**: spec/zip validation, unit tests, docs. Standing stance
stated 2026-07-10 (capability map
[#800](https://github.com/Gharib89/crm/issues/800)).

## Why

- **On-prem orgs are VPN-locked.** The priority target (see CLAUDE.md) sits
  behind a VPN; hosted CI runners have no network path to it — ever. Any
  pipeline design that assumes CI can reach the org is dead on arrival for the
  deployments that matter most here.
- **A cloud org doesn't fix it.** A throwaway cloud org is at most an optional
  dev-time sandbox; making it a required CI lane would validate against a
  target that differs from the one being promoted to (version, features,
  provenance gates) and adds an org-lifecycle burden (trials expire, dev envs
  idle-disable).
- **The audience is not CI/CD-native.** D365 customizers ship through
  solution files and admin UIs, not pipelines. Keeping promotion HITL-simple
  (plan artifact a human can read and approve) matches how the tool is
  actually operated.

## Consequences

- Any future proposal involving CI + live orgs (e2e in pipelines, deploy
  jobs, scheduled drift checks) leads with the HITL / org-less alternative.
- The live e2e suite stays a **local, human-triggered** activity
  (`D365_E2E=1 pytest -m e2e` from a connected machine), never a CI job.
- Release/CI health is judged entirely by org-less gates (lint, tests, docs,
  packaging smoke).
