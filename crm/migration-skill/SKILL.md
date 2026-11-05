---
name: crm-migration
description: Migrate a Dynamics CRM org (CRM 2011 or on-premises) to Dataverse online in stages. Use when planning the migration or its upgrade path, inventorying a legacy org, mapping its customizations to source code, assessing compatibility, or proving a repaired component works in the target before calling it done.
---

# crm-migration

This skill decides **what to do and when the evidence is enough**; the `crm`
skill owns **how to run each command** (profiles, flags, JSON output). Load it
before the first command.

## The staged path

The database moves one stage at a time:

1. **CRM 2011** (the source, read over SOAP through a `crm2011` profile);
2. the supported **intermediate on-premises upgrades**;
3. **on-premises 9.1**;
4. **Dataverse online** (the cloud target).

Discovery and the behavior baseline happen on the source, before any upgrade.
After each upgrade stage, extract the inventory again and `crm inventory diff`
it against the previous stage's, so a component lost in the upgrade surfaces
before cloud work starts. Cloud repairs are proven in the migrated dev or test
environment.

## Workflow

Work the steps in order. Each ends on its **Done when**; a step whose evidence
is missing stays open.

1. **Establish the environments.** Confirm each org's identity and version, and
   which profile reaches it: `reference/environments.md`. Done when every org
   the work touches has a confirmed identity, version and access mode.
2. **Inventory the source.** Extract the complete migration inventory with
   `crm inventory extract` (`reference/inventory.md`, which also reads stage
   diffs), and reconcile deployed components against the application repository
   with `crm inventory map-source` (`reference/source-mapping.md`). Done when the inventory reads every kind
   `complete`, or each incomplete kind is reported as an unknown.
3. **Assess compatibility.** Sort each component into confirmed blocker,
   deprecated but working, optional modernization, or unknown, from documented
   rules and source or runtime evidence. `crm inventory assess` runs the
   rules shipped with crm and gives every component a finding
   (`reference/assessment.md`), which also covers `clear` and `unknown`. For
   classic workflows, `crm workflow migration-assess` flags the cloud-flow
   blockers. Done when
   every component carries one of the four classes, a `clear` or `unknown`
   from `crm inventory assess` included, once evidence settles it, with the
   rule or evidence behind it.
4. **Plan remediation.** Group the inventory into dependency-ordered work
   packages with `crm inventory packages`, passing the `assess` file as
   `--findings` so each package lists its compatibility findings
   and write each package task's six fields as
   `reference/remediation.md` lists them. Done when that page's Done-when holds.
5. **Patch and build.** Make small changes, each tracing to one package task
   (`reference/remediation.md`). Done when the repository's own builds and checks pass on the patched tree.
6. **Verify on the target.** Follow `reference/verification.md`: confirm the
   target, then run the required tests (unit, browser, integration, permission,
   business scenario) in the named dev or test environment, collecting errors
   and traces. Done when every requirement has an **outcome** (pass, fail,
   blocked, not tested) with its evidence.
7. **Record the result.** Record each requirement's outcome with its evidence,
   then run `crm inventory verify` and report each package's **verdict** as it
   gives it (`reference/verification.md`). Write a checkpoint beside the verify
   report listing each package not `complete`, its verdict and its next action,
   so the next session resumes from it. Done when every package has a verdict
   and the checkpoint is written.

## Boundaries

- **The source is read-only.** A `crm2011` profile is always read-only; the CLI
  cannot write through it. Read an on-premises source through a read-only
  profile too.
- **Target writes happen only in a named dev or test environment**, after
  `crm connection whoami` has confirmed the profile reaches that org.
- **Success is not equivalence.** A command exiting 0 or a solution importing
  cleanly proves the platform accepted it, not that the component behaves as it
  did on the source. Equivalence comes only from the tests in step 6.
- **Complete means the `complete` verdict.** Report a work item complete only
  when `crm inventory verify` gives its package that verdict. Report every other
  state as it stands, and name each unknown plainly: a gap in coverage or
  evidence is an unknown, never a pass.
