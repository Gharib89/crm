# Environments: identity, version, access

Step 1 of the workflow. A migration touches several orgs (the CRM 2011 source,
each intermediate on-premises stage, the cloud dev or test target), and every
finding is only as good as the claim about which org produced it.

## One profile per org

Give each org its own saved profile, named for its role and stage, so a command
names the org it reads. The `crm` skill covers creating and switching profiles.

- **CRM 2011 source:** `crm profile add` saves a `crm2011` profile, which is
  read-only by construction. Prove it reaches the org with
  `crm --json legacy version` and read the build it answers.
- **On-premises stages:** mark the profile read-only while it serves as a
  source.
- **Cloud target:** a profile for the named dev or test environment only.

## Confirm before trusting

Before recording anything against an org:

- run `crm connection whoami` on the profile and check the org and user it
  answers are the ones the work names;
- record the org's version beside every inventory or finding taken from it, so
  a later stage's diff compares the stages you think it does;
- stop and tell the user when an org answers a different identity or version
  than expected. Never continue on a guess about which org is behind a profile.

## Supported scope

The CLI reads CRM 2011 through a small allowlist of SOAP reads; a request it
refuses is a CLI limit, not an org permission problem. Report it as
unsupported. CRM 2011 support is verified on one build only: on another build,
tell the user the reads are untested there and treat their output as
unconfirmed until spot-checked.
