# Environments: identity, version, access

Step 1 of the workflow. A migration touches several orgs (the CRM 2011 source,
each intermediate on-premises stage, the cloud dev or test target), and every
finding is only as good as the claim about which org produced it.

## One profile per org

Give each org its own saved profile, named for its role and stage, so a command
names the org it reads. The `crm` skill covers creating and switching profiles.

- **CRM 2011 source:** a `crm2011` profile; record the build
  `crm legacy version` answers.
- **On-premises stages:** mark the profile read-only while it serves as a
  source.
- **Cloud target:** a profile for the named dev or test environment only.

## Confirm before trusting

Before recording anything against an org:

- confirm the org and user the profile answers are the ones the work names:
  `crm legacy whoami` on the `crm2011` source, `crm connection whoami` on every
  other org;
- compare `crm legacy whoami` with ids, not names: it returns the user,
  business unit and organization ids only. Ask the org's administrator for the
  ids of the org and user the work names, and match on those;
- record the org's version beside every inventory or finding taken from it, so
  a later stage's diff compares the stages you think it does;
- stop and tell the user when an org answers a different identity or version
  than expected.

## Supported scope

The `crm` skill's legacy section sets what the CLI can read from CRM 2011 and
on which build it is verified. For migration evidence, a read the CLI refuses,
or any read on an unverified build, is an **unknown** in the inventory, never a
gap filled by guess.
