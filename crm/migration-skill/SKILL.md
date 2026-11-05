---
name: crm-migration
description: Assess and verify a Microsoft Dynamics CRM migration from CRM 2011 or on-premises Dynamics 365 to Dataverse online, through the crm CLI. Use when planning or running the staged upgrade (CRM 2011, intermediate on-prem versions, on-prem 9.1, then cloud), inventorying what a legacy org holds, mapping its customizations to source code, assessing compatibility, or proving a repaired component works in the target before calling it done.
---

# crm-migration

A migration skill beside the `crm` skill. This one decides **what to do and when
the evidence is enough**; the `crm` skill owns **how to run each command**. Load
the `crm` skill for exact CLI operations (profiles, flags, JSON output) rather
than guessing them here, and run `crm <group> --help` for any verb named below.

## The staged path

The database moves in stages, never in one jump:

1. **CRM 2011** (the source, read over SOAP through a `crm2011` profile);
2. the supported **intermediate on-premises upgrades**;
3. **on-premises 9.1**;
4. **Dataverse online** (the cloud target).

Discovery and the behavior baseline happen on the source, before any upgrade.
Cloud repairs are proven in the migrated development or test environment, not
on the source.

## Workflow

Work the steps in order; each ends on the evidence named, and a step whose
evidence is missing stays open.

1. **Establish the environments.** Confirm each org's identity and version, and
   which profile reaches it: `reference/environments.md`. Done when every org
   the work touches has a confirmed identity, version and access mode.
2. **Inventory the source.** Extract the complete migration inventory with
   `crm inventory extract`, and reconcile deployed components against the
   application repository with `crm inventory map-source`. An org summary is
   orientation, never the baseline. Done when the inventory reads every kind
   `complete`, or each incomplete kind is reported as an unknown.
3. **Assess compatibility.** Sort each component into confirmed blocker,
   deprecated but working, optional modernization, or unknown, from documented
   rules and source or runtime evidence. For classic workflows,
   `crm workflow migration-assess` flags the cloud-flow blockers. A component no
   rule covers is unknown, never compatible.
4. **Plan remediation.** Group the inventory into dependency-ordered work
   packages with `crm inventory packages`. Each task names its component and
   source references, its consumers, the Microsoft documentation behind it, the
   proposed change, its confidence and unknowns, and the tests that will prove
   it.
5. **Patch and build.** Make small changes and run the repository's own builds
   and checks.
6. **Verify on the target.** Run the required tests (unit, browser,
   integration, permission, business scenario) in the named dev or test
   environment, collecting errors and traces.
7. **Record the outcome.** Record each requirement's result with its evidence,
   then judge the packages with `crm inventory verify`. Report every result as
   pass, fail, blocked or not tested, and keep a checkpoint of the unresolved
   work so the next session resumes from it.

Comparing two inventories (one stage against the next) is `crm inventory diff`.

## Boundaries

- **The source is read-only.** A `crm2011` profile is always read-only; the CLI
  cannot write through it. Read an on-premises source through a read-only
  profile too.
- **Target writes happen only in a named dev or test environment**, after
  `crm connection whoami` has confirmed the profile reaches that org. Production
  is never a test target.
- **Success is not equivalence.** A command exiting 0 or a solution importing
  cleanly proves the platform accepted it, not that the component behaves as it
  did on the source. Equivalence comes only from the tests in step 6.
- **Complete means `verify` said `complete`.** Report a work item complete only
  when `crm inventory verify` gives its package the `complete` verdict. Report
  every other state as it stands, and name each unknown plainly: a gap in
  coverage or evidence is an unknown, never a pass.

## Coming

Planned steps that have no command yet; until they land, do the work from the
evidence you have and report it as manual:

- version-specific compatibility rules shipped as data, and a command that runs
  them over an inventory;
- compatibility findings merged into the work packages;
- reference pages for inventory and source mapping, assessment and
  remediation, and cloud verification with checkpoints.
