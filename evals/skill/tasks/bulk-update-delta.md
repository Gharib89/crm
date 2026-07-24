---
id: bulk-update-delta
domain: bulk
tier: 2
source:
  type: so
  url: https://stackoverflow.com/questions/8531185
# Host-agnostic: the efficient bulk-write path is `crm data import` over `$batch`, which
# works on cloud and on-prem (CreateMultiple/UpsertMultiple are cloud-only, but the skill
# steers to `$batch`), so `either`.
target: either
kind: do
# The delta update landed on ALL 20 rows iff exactly 20 accounts carry BOTH the marker
# name and the NEW phone. A did-nothing agent leaves 0 matches (the seeded value is the
# old phone); an agent that seeds but never updates also leaves 0 — so count:20 has no
# did-nothing false-pass.
#
# Verifier scope (per ADR 0028's L1/L2 split): an org-state predicate proves the goal
# *state*, not the *path* — the end state "20 rows carry the new phone" is also reachable
# by creating them with that value directly, which no final-state check can distinguish
# (the two paths are state-identical by definition). Seeding a distinct old value makes a
# cooperative agent following the prompt do a real update; judging whether the method was
# an efficient bulk update vs a per-row loop is the advisory L2 judge's job, and a task a
# bare agent trivially satisfies is exactly what the baseline-3/3 calibration filter demotes.
end_state:
  query:
    - query
    - odata
    - accounts
    - --filter
    - "name eq 'EvalBulk895 Delta Co' and telephone1 eq '0200000000'"
    - --select
    - name,telephone1
  expect:
    count: 20
cleanup:
  - entity: accounts
    id_field: accountid
    filter: "name eq 'EvalBulk895 Delta Co'"
---

Working against the connected Dynamics 365 org, you maintain a set of accounts that all
share the name `EvalBulk895 Delta Co`. First create 20 such accounts, each with
`telephone1` set to `0100000000`. Then apply a delta update that changes `telephone1` to
`0200000000` on every account with that name — using the most efficient mechanism the
platform offers, not one request per record. Verify the update landed on all of them.
