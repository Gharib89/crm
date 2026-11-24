# Phase 4 evidence lines

`run-file close 4` refuses until the Run file carries a `Reverted-fix:` line for
each test file the diff adds or changes, and the self-review loop's attested
`Round:` and `Stop:` lines; each refusal names the line missing or misshapen and
its shape. The check holds a line's presence and shape, and the self-review
keeps the judgment of what it says. The lines exist because something reads
them: the red line proves a new test fails on the old code, and `run-file gate
clean` reads the loop's `Stop:` line.

- **The red line** is produced: `run-file prove <test> <path>... --issue
  <issue>` runs `revert-red` on committed state, so commit the test and the fix
  first, and on its exit 0 writes the line under `## Evidence`. A fix landed
  after it means proving again.
  - Exit 1 means the test stayed green: it proves nothing, so fix the test. It
    is also exit 1 when its log names a file or module the fix added as
    missing, whatever else failed: any test that reads the file goes red
    without it. The route out: commit the new file with the lines the test
    reads stubbed, then commit their real body and prove that second commit
    with `--fix <commit>`; or revert a path the fix changed and the test
    reads.
  - A fix commit from a self-review round or a phase-7 round is proven alone
    with `--fix <fix commit>`: only that commit's own change to each path is
    taken out, so each of two fix commits is judged on its own change. A later
    commit editing the same lines is exit 2.
  - A test that is not a `.sh` file passes its runner's command after `--`,
    run at the top of a fresh temporary worktree with nothing installed:
    `run-file prove tests/test_x.py src/x.py --issue 12 -- uv run pytest
    tests/test_x.py`. An install step goes in as `bash -c 'bun install && bun
    test "./$0"' <test>` (`./`: `bun test` reads a bare path as a filter).
- **The `n/a` line**, `Reverted-fix: <test>: n/a: <reason>`, is attested,
  taken at the run's word, where there is no fix to revert, the test needs a
  database or service a fresh checkout cannot start, its runner cannot run that
  one file on its own, or it is already red before the revert: none of these
  can show the test going red for the fix's absence.
