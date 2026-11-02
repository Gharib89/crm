# Phase 4 evidence lines

`run-file close 4` refuses until the Run file carries these lines, each with an
optional `- ` prefix, written as its check settles. The check is a
**completeness guard**: it holds each line to being present and in its shape,
and the self-review keeps the judgment of what a line says. The red
`Reverted-fix:` line is **produced**, written under the Run file's
`## Evidence` by the `run-file` verb that ran the proof; every other line is
**attested**, written by the run and taken at its word. They are kept because
something reads them: the red line proves a new test fails on the old code, and
`run-file gate clean` reads the loop's `Stop:` line. What the depth checks
found (removed blocks, matcher cases, declines and their probes, per
[the depth checks](implement.md#phase-4-triage-and-depth-checks)) is the
self-review's judgment, recorded in the Run file where it helps the summary.

- **Reverted-fix**, one per test file the diff adds or changes.
  - Produced: `run-file prove <test> <path>... --issue <issue>` runs
    `revert-red` on them and, on its exit 0 (the test went red with the fix
    reverted), writes `Reverted-fix: <test>: red at <sha> reverting <path>...`,
    `<sha>` the head it ran on. Commit the test and the fix first: `revert-red`
    reads committed state. The line stands while neither the test nor a listed
    path has changed since `<sha>` in the working tree, so a fix landed after
    it means proving again; a hand-written `red` is refused.
  - Exit 1 means the test stayed green: it proves nothing, so fix the test. It
    is also exit 1 when its log names a file or module the fix added as
    missing, whatever else failed: any test that reads the file goes red
    without it. The route out: commit the new file with the lines the test
    reads stubbed, then commit their real body and prove that second commit
    with `--fix <commit>`; or revert a path the fix changed and the test
    reads.
  - A fix commit from a self-review round or a phase-7 round is proven alone
    with `--fix <fix commit>`: only that commit's own change to each path is
    taken out, not the merge base's copy, so each of two fix commits is judged
    on its own change. A later commit editing the same lines is exit 2.
  - A test that is not a `.sh` file passes its runner's command as argv after
    `--`, run in place of `bash <test>` at the top of a fresh temporary
    worktree with nothing installed; it names the test as one of its words or
    a node ID under it: `run-file prove tests/test_x.py src/x.py --issue 12 --
    uv run pytest tests/test_x.py` (`uv run` builds its own environment). An
    install step goes in as `bash -c 'bun install && bun test "./$0"' <test>`
    (`./`: `bun test` reads a bare path as a filter). The written line names
    no command; the JSON answer's `command` records what ran.
  - Attested: `Reverted-fix: <test>: n/a: <reason>` where there is no fix to
    revert, the test needs a database or service the command cannot start in a
    fresh checkout, its runner cannot run that one file on its own, or it is
    already red before the revert, since none of these can show the test going
    red for the fix's absence.

  A test file is a path with a `tests`, `test` or `__tests__` component, or a
  basename `*.test.*`, `*.spec.*`, `*_test.*` or `test_*`.
- **Round** and **Stop**, attested, for the
  [self-review loop](implement.md#phase-4-the-self-review-loop): at least one
  `Round: self-review <n>: <text>`, `<n>` a positive integer, and a
  `Stop: self-review: <tree unchanged|cap at <sha>>`, the last such line
  deciding; a capped stop names `<sha>` as that loop defines it, and a bare
  `cap` is still admitted.

`close 7`'s `Round:` and `Stop:` lines
([review-loop.md](review-loop.md#the-exit)) are attested the same way.
