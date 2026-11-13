---
name: crm-migration
description: Migrate a legacy Dynamics CRM org to Dataverse online, by the staged path or cloud-first after another team ran the upgrade hops. Use when planning the migration or its upgrade path, inventorying a legacy org, mapping its customizations to source code, assessing compatibility, or proving a repaired component works in the target before calling it done.
---

# crm-migration

**Load the `crm` skill with the Skill tool before you run any `crm` command.**
This skill never gives a command's flags, and a verb guessed without it fails.

This skill decides **what to do and when the evidence is enough**; the `crm`
skill owns **how to run each command** (profiles, flags, JSON output).

## What this skill does not cover

It covers customizations: the metadata, code and configuration of the org. Hand
the rest off by name, and record each as an unknown or an open item:

- **Business data (rows).** No `crm` command reads a row from a `crm2011`
  profile. An ETL tool that reads the 2011 SOAP endpoint moves rows; crm has no
  row mover.
- **Users, security and licences.** No command lists users or security roles
  from a `crm2011` profile. The tenant administrator owns identities and licences.
- **Cutover.** The project lead owns the date, the freeze and the rollback.

## Two routes: pick one first

Ask who ran the upgrade hops.

- **Staged path:** you own the hops. The database moves one stage at a time:
  1. **CRM 2011** (the source, read over SOAP through a `crm2011` profile);
  2. the supported **intermediate on-premises upgrades**;
  3. **on-premises 9.1**;
  4. **Dataverse online** (the cloud target).

  Discovery and the behavior baseline happen on the source, before any upgrade.
  After each upgrade stage, extract the inventory again and `crm inventory diff`
  it against the previous stage's, so a component lost in the upgrade surfaces
  before cloud work starts. Cloud repairs are proven in the migrated dev or test
  environment.
- **Cloud-first route:** another team already moved the database through every
  stage. You hold the CRM 2011 source and the finished cloud org, and
  no stage between them. Follow the next section.

Test: can you reach each intermediate stage yourself? Yes: staged path. No:
cloud-first route.

## The cloud-first route

Work these in order; each ends on its **Done when**. They replace Workflow
steps 1 to 2, source mapping included (step 6 below); the Workflow governs the
rest.

1. **Confirm both orgs.** The 2011 source and the cloud org
   (`reference/environments.md`). Done when each has a confirmed identity,
   version and access mode.
2. **Extract the 2011 source with usage.** Read usage so unused components
   surface (`reference/inventory.md`). Done when each kind reads `complete` or
   is a listed unknown.
3. **Extract the cloud org with usage and the same scope.** A diff across two
   scopes compares different things. Done when each kind reads `complete` or is
   a listed unknown, and both manifests state the same scope.
4. **Diff the two.** `crm inventory diff` 2011 against cloud. With no stage
   observed between them, read `removed` and `changed` as
   `reference/inventory.md` says. Then run the arrival checklist
   (`reference/arrival.md`) on the cloud org, for state the diff does not show.
   Stop the route when the diff shows the cloud org is not a migrated copy
   (`reference/inventory.md`). Done when each entry has an explanation, a
   question for the team that ran the hops, or an unknown, and each checklist
   item is answered or an unknown.
5. **Request the handover.** Ask that team for each item
   `reference/handover.md` lists, and record each answer, or its absence as an unknown. A 9.1 extract, if they
   supply one, replaces the single diff with two (2011 to 9.1, 9.1 to cloud).
   Done when every handover item is answered or recorded as an unknown.
6. **Map, assess, package and convert from the cloud inventory.** First
   reconcile the cloud extract with the application repository
   (`crm inventory map-source`, `reference/source-mapping.md`). Then
   `crm inventory assess`, `packages` and `convert` read the **cloud** extract,
   not the 2011 one: it records the code actually deployed, and the OData v2 rewrite needs
   the entity set names only a v9 extract records. Done when Workflow steps 3
   and 4 hold against the cloud inventory and every component reconciles with
   the repository or is an unknown.
7. **Patch, build and verify on the cloud dev or test org.** Workflow steps
   5 to 7, unchanged. Done when they hold.

The staged path's rule to hold before the next stage does not apply on this
route: there is no next stage.

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
   rules shipped with crm and gives every component of a covered kind a finding,
   and each kind no rule covers one `uncovered_kinds` entry
   (`reference/assessment.md`), which also covers `clear` and `unknown`. For
   classic workflows, `crm workflow migration-assess` adds the real-time mode
   check (it reads a Web API org, so it refuses a `crm2011` profile); wait conditions and custom activities are optional modernization.
   `crm inventory readiness` rolls an `--target online` file up as a per-gate checklist
   for the move to online (`reference/assessment.md`).
   Done when every component carries one of the four classes with the rule or
   evidence behind it: each `clear` is judged from its source or runtime
   evidence; a component stays `unknown` only with its `reason` and a
   question to the user, a manual-review entry or a gap carried forward; and
   each `uncovered_kinds` entry has one manual-review entry for its kind, as
   "Turn every unknown into a question or a manual-review entry" in
   `reference/assessment.md` says.
4. **Plan remediation.** Group the inventory into dependency-ordered work
   packages with `crm inventory packages`, passing the `assess` file as
   `--findings` so each package lists its compatibility findings. Run
   `crm inventory convert` to write the patches and the `ledger.json` of what it
   could not patch. Write each package task's six fields as
   `reference/remediation.md` lists them. Done when that page's Done-when
   holds.
5. **Patch and build.** Make small changes, each tracing to one package task,
   by "The loop after convert" in `reference/remediation.md`: review and apply
   each patch, then `crm inventory check-applied`. Done when `check-applied`
   exits 0 with `checked` above 0 (or, with no application repository, you
   recorded that nothing was proven, per `reference/remediation.md`) and
   the repository's own builds and checks pass on the patched tree.
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

- **Read an on-premises source through a read-only profile.**
- **Target writes happen only in a named dev or test environment**, after
  `crm connection whoami` has confirmed the profile reaches that org.
- **Success is not equivalence.** A command exiting 0 or a solution importing
  cleanly proves the platform accepted it, not that the component behaves as it
  did on the source. Equivalence comes only from the tests in step 6.
- **Complete means the `complete` verdict.** Report a work item complete only
  when `crm inventory verify` gives its package that verdict. Report every other
  state as it stands, and name each unknown plainly: a gap in coverage or
  evidence is an unknown, never a pass.
