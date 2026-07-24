---
id: security-assign-role-team
domain: security
tier: 2
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=68ce9905-ffc8-4aec-ac9d-260a2330ccd1
# Security demand cluster (#898), pilot harvest #885 row 12 — "bulk assign Security Roles to a
# large user population", the densest security ask (4+ sibling forum threads + API-flavor twin
# SO 51238107, role via $ref association). The platform-idiomatic, scalable answer to "add this
# role to thousands of users" is NOT a per-user assignment loop but the team indirection: assign
# the role once to an owner team, then add users to the team (they inherit it). This task models
# the load-bearing half — role authored, team created, role assigned to the team — because that
# is the part a static predicate can verify. User membership is keyed by run-time systemuser
# GUIDs and needs provisioned (licensed) users the harness cannot self-seed, so it is left to the
# agent to describe but is not scored (same limitation the older security-role-create task notes
# for direct user assignment).
# Verifier: the role->team assignment is an M:N intersect that IS reachable through the
# `teamroles_association` navigation property on `teams` — filtering teams by
# `teamroles_association/any(r:r/name eq '<role>')` returns the team once (and only once) the role
# is assigned. Verified live on agent-cloud: the filter returned [] before the assign and exactly
# the one team after. The role<->privilege intersect, by contrast, is NOT an exposed entity set
# (`query odata roleprivileges` 404s), which is why privilege-state security tasks are modeled as
# feasibility, not do. count:1 rules out did-nothing (0 — the team starts with no role) and a
# create-role-but-forgot-to-assign partial. Host-agnostic (owner teams + the M:N nav filter work
# on cloud and on-prem v9.1) -> either. T2, not a baseline-trivial T1: create-role (with its
# solution-scoping requirement) + owner-team create (business-unit + administrator binds) +
# assign-role is a real multi-step workflow. The authoring-time calibration proxy is the tier;
# the live baseline-3/3 / skill-0/3 filter runs post-hoc on paired-run results, not here.
target: either
kind: do
end_state:
  query:
    - query
    - odata
    - teams
    - --filter
    - "name eq 'EvalSec898 Sales Team' and teamroles_association/any(r:r/name eq 'EvalSec898 Sales Role')"
    - --select
    - name
  expect:
    count: 1
    row:
      name: EvalSec898 Sales Team
cleanup:
  - entity: teams
    id_field: teamid
    filter: "name eq 'EvalSec898 Sales Team'"
  - entity: roles
    id_field: roleid
    filter: "name eq 'EvalSec898 Sales Role'"
---

Working against the connected Dynamics 365 org, a newly created security role has to be rolled
out to a large population of users. Assigning it one user at a time does not scale, so use the
platform's team indirection instead.

First create a security role named `EvalSec898 Sales Role` that grants organization-level read
access to accounts. Then create an owner team named `EvalSec898 Sales Team` in the same business
unit as the role. Assign the `EvalSec898 Sales Role` role to the `EvalSec898 Sales Team` team so
that every user added to the team will inherit it. Confirm that the team now carries the role.
