# Phase 3: verify where it failed

Each entry under `## Verification` has seven lines. You judged `Applies when:`
at classification; here you run the applicable ones.

- **Run** its `Run:` line scoped to what you touched, the phase-2 regression
  test in that run and the rest of the suite left to the local gate. Pass is
  generic: green for the touched scope on the named environment. There is no
  per-verification pass rule.
- **Where.** On the environment the issue was reported against. A different
  environment may auto-heal the bug, so green is not fixed unless it is green
  where it failed. Where this machine cannot prove the claim (an OS the issue
  names, a matrix leg), `Also proven by CI:` names the leg that does; write the
  test so that leg proves it and watch it in phase 8.
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
  deferred-to-ci | unavailable | unexercised`. The fifth is phase 3's alone,
  because a gate check always has a subject. Phase 5 admits `pass`,
  `deferred-to-ci` and `unexercised` only, so a verification still without a
  result is finished here, by running it or by taking its `Without it:` line;
  the merge gate is for reading a summary, not finishing phase 3.
- **`unexercised`** is the verification whose `Needs:` were satisfied and whose
  every applicable path lacked a **subject** to drive, where the subject is one
  another actor creates (a reviewer's thread on this run's PR), as opposed to
  one ship could have created itself. The human weighs it at the merge gate,
  where [merge-gate.md](merge-gate.md) says how the row reads, and an unattended
  run proceeds on it, because there is nothing to hand back for. Three cases it
  is the wrong word for: a **prerequisite** that failed its detection, which
  `Without it:` still owns; a path the run **skipped**, which is an unrun
  verification; and a verification where **at least one applicable path ran**,
  which is unchanged, the paths that ran giving the result and `<what ran>`
  naming the unexercised one.
- `docs` class and the small lane skip this phase entirely.
