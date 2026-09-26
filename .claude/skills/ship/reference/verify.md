# Phase 3: verify where it failed

Each entry under `## Verification` has seven lines. You judged `Applies when:`
at classification; here you run the applicable ones.

- **Run** its `Run:` line scoped to what you touched, the phase-2 regression
  test in that run and the rest of the suite left to the local gate. Pass is
  green for the touched scope on the named environment; there is no
  per-verification pass rule.
- **Where.** On the environment the issue was reported against: a different
  environment may auto-heal the bug. Where this machine cannot prove the claim
  (an OS the issue names, a matrix leg), `Also proven by CI:` names the leg that
  does; write the test so that leg proves it and watch it in phase 8.
- **Prerequisite missing** (`Needs:` fails its detection): the disposition is
  the entry's `Without it:` line, one of:
  - `hand-off`: attended, print the exact command and setup, wait for the human
    to run or confirm it, resume; the claim holds. Unattended: hand back with
    the same command in the reason.
  - `defer-to-ci`: continue; legal only because `Also proven by CI:` names a
    leg, which the merge summary then names. The only unattended-safe
    disposition.
  - `blocked`: cannot be verified anywhere without the prerequisite. Stop
    `blocked-verification`.
- **Result words** are the local gate's plus `unexercised`: `pass | fail |
  deferred-to-ci | unavailable | unexercised`. Phase 5 admits `pass`,
  `deferred-to-ci` and `unexercised` only, so a verification still without a
  result is finished here, by running it or by taking its `Without it:` line.
- **`unexercised`** is the verification whose `Needs:` were satisfied and whose
  every applicable path lacked a **subject** another actor creates (a
  reviewer's thread on this run's PR), as opposed to one ship could have
  created itself. The human weighs it at the merge gate, and an unattended run
  proceeds on it. It is the wrong word for a **prerequisite** that failed its
  detection (`Without it:` owns that), a path the run **skipped** (an unrun
  verification), and a verification where **at least one applicable path ran**:
  the paths that ran give the result and `<what ran>` names the unexercised one.
- `docs` class and the small lane skip this phase entirely.
