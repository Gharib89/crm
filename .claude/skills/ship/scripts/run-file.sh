#!/usr/bin/env bash
# The Run file: a ship run's record, outside every checkout, in the user's
# state directory. This mechanic owns the file's shape and every flip of
# it, so a stamp is a measurement the mechanic took rather than a time a run
# recalled, and the merge summary's `Timing` item is arithmetic rather than
# mental subtraction.
#
#   run-file init <issue|slug> [--scratchpad <dir>] [--rebuild]
#                [--state <n>=open|done|done:<HH:MM→HH:MM>|skipped:<reason>]...
#                [--tripwires <t>] [--verifications <v>] [--reviewers <r>] [--legs <l>]
#                [--from-profile [<path>]] [--criteria <file|->]
#   run-file open|next <n> <where>
#   run-file close <n> [--result <name>=<word>[: <note>]]... [--head <sha>] <where>
#   run-file skip <n> <reason> <where>
#   run-file grade <patch|minor|breaking> <where>
#   run-file gate record <file|-> [--head <sha>] <where>
#   run-file gate read --head <sha> <where>
#   run-file gate clean <ci-file|-> --head <sha> <where>
#   run-file gate summary <pr> <where>
#   run-file timing <where>
#   run-file ran <name>... <where>
#   run-file prove <test> <path>... [--fix <commit>] <where> [-- <command>...]
#   run-file probe <ref> <where> -- <command>...
#   run-file call <why> <where> -- <command>...
#   run-file note <section> <line> <where>
#   <where>: --file <path> | --issue <n|slug> [--scratchpad <dir>]
#
# The record root is `${XDG_STATE_HOME:-~/.local/state}/ship/<repo key>`,
# shared by the main checkout and every worktree of it; `ship_record_root` in
# _lib.sh says why it sits there. `--scratchpad <dir>` names another root;
# outside a git checkout it is the only one. Under the root,
# `ship-<issue>/run.md` is the Run file and `scratch-<issue>/` is the run's
# Scratch directory, which `init` creates and returns as `scratch`; the roles'
# subdirectories are the callers'. `cleanup` removes both.
#
# The `--issue` form reads the layout `init` wrote. `init` owns that layout, so
# it is the mechanic that resolves it: a run whose context was compacted still
# has the issue it was invoked on, and called `close` without a path twice for
# want of the rest (#218). An explicit `--file` wins, and neither given is the
# usage error it always was.
#
# `init` writes the ten items and returns them, one per harness task the run
# then creates, and opens phase 0 (`opened`, `mirror`) unless a `--state` states
# the run itself; `--rebuild` with `--state` is the recovery from a Run file a
# subagent overwrote or removed, and `--rebuild` alone replaces the record of an
# issue whose earlier run stopped with a fresh one. A `--state` set holds one
# open phase, or 3 and 4 both, the overlap `open 4` allows. `--from-profile`
# fills `--tripwires`, `--verifications`, `--reviewers` and `--legs` from the
# ship profile (the `Tripwires:` line under `## Local gate`, the `###` headings
# under `## Verification` and `## Reviewers`, the leg names under `## CI`); a
# flag given beside it wins. Each `###` heading under `## Verification` is one
# Verification name whatever it holds, spaces and punctuation included, except
# `=`, which `--result` splits on, and a result word as a later `: ` field
# (`Build: pass`). A flip returns the `mirror` value for that phase's task, and
# `next <n>` is the close of the one open phase and the open of <n> in one
# call, refused whole when either half would be. `open 4` is the
# one open admitted while phase 3 is open, the overlap the run has; any other
# open over an open phase is refused.
# `--criteria <file|->` takes the issue's spec text (`-` is stdin; the text
# `null`, a failed read, is refused) and records each of its checkbox lines
# (`-`, `*`, `+` or numbered) outside a fence as `- <n>: <text>` under
# `## Criteria`, the full set a run owes; where a criteria heading lists
# top-level lines but no checkbox, those, to the next heading at its level or
# above. The run attests each as `Criterion <n>: met: <proof>` or
# `Criterion <n>: not met: <reason>` (the last such line deciding, a line
# anywhere in the file like `Stop:`); `gate clean` holds on an entry with no
# line and on any `<n>`, entry or not, whose last line is not met. No criteria
# writes no section and warns naming `run-file note Criteria`. An init
# on an issue number with `--from-profile` and no `--criteria` reads the issue
# and, where it holds criteria, warns on stderr naming the flag; `gate summary`
# holds the same run
# (`criteria: the Run file has no ## Criteria but issue #<n> has <k> criteria`).
# Below the checklist it writes sections the run fills: `Verification results`
# where `--from-profile` took the names from the profile's headings, or
# `--verifications` names a comma list of name-shaped words (one
# `- <name>: pending` each, which `close 3 --result <name>=<word>` settles with
# one of pass, fail, deferred-to-ci, unavailable, unexercised or n/a, written
# `- <name>: <word> at <sha>[: <note>]`, <sha> the `--head` close 3 was given or
# else the head the last `ran <name>` recorded (`Ran: <name> at <sha>` under
# `## Evidence`, written by `ran` when the verification's `Run:` line ran), or
# the checkout's HEAD when none was, and which
# `close 3` will not pass while any is pending), then `Design and plan`,
# `Deviations log`, and `Direct calls`, one line per direct call the run made
# straight through the host's REST form, no mechanic covering it, so one that
# recurs across runs is visible as a mechanic to promote. `skip 3 <reason>`
# settles each still-pending result as `n/a: <reason>`; `skip` of 5 to 8 is
# refused, naming phase 3, while phase 3 is open. `close 3 --result` also
# takes a name the Run file lacks when the checkout profile has it as a
# `## Verification` heading, one the PR added after init: it applies init's
# heading checks to the name, then appends its entry. Once phase 3 has closed or
# been skipped, `close 3 --result` still records, without re-opening phase 3,
# and leaves phase 3's row as it was. `gate record`
# appends the local gate's verdict, the head it ran on and the verdict JSON's
# `gates` object (compact, one line, absent when the JSON has none) to a
# `Local gate` section; `gate read` answers whether the head the run is at is
# still the one that verdict covers, and returns the gates the merge gate cites
# when it does not re-run the gate. Both take `--head` as a full or abbreviated
# sha, 7 to 64 hex digits.
#
# `close 4`, `close 5` and `close 7` hold the self-review, the criteria and the
# review loop to the evidence they leave in the Run file (`close 5` reads only
# the `## Criteria` entries and their `Criterion` lines, below). They are
# completeness guards: they check
# that each line is present and in its shape, and the self-review keeps the
# judgment of what a line says. The red `Reverted-fix:` line is produced, written
# by `prove`, the verb that ran the proof. The rest are attested, written by the
# run and taken at its word: the `n/a` `Reverted-fix:` line, and the `Round:`
# and `Stop:` lines of `close 4`'s self-review and `close 7`. A refused close
# writes nothing, names everything missing in one message, and `next` over
# any of the three phases is held the same way. A gate line is a line anywhere in the file,
# an optional leading `- ` accepted, matched by exact prefix at the line start.
# `close 4` reads the checkout's own diff (the working tree, untracked files
# included, against the merge base of HEAD and origin/HEAD); it needs:
#   `Reverted-fix: <test path>: red at <sha> reverting <path>...` (produced) or
#     `Reverted-fix: <test path>: n/a: <reason>` (attested) for each test file
#     the diff adds or changes, deletions aside. A red line stands while neither
#     the test nor a path it lists has changed since <sha> in the working tree,
#     untracked files included; a hand-written `red` or `red: <text>` is
#     refused. A test file has a path component `tests`, `test` or `__tests__`,
#     or a basename matching `*.test.*`, `*.spec.*`, `*_test.*` or `test_*`.
#   the self-review loop in close 7's vocabulary: at least one
#     `Round: self-review <n>: <text>`, <n> a positive integer, and
#     `Stop: self-review: <reason>`, the last such line deciding, the reason
#     tree unchanged, cap or `cap at <sha>`.
# `close 7` reads the reviewers from the phase's checklist row (nothing is held
# where the row names none, or names what is not a list of names) and needs a
# `Stop: <reviewer>: <reason>` for each, the last such line deciding, the reason
# one of cap, `cap at <sha>` (read as cap), `tree unchanged at <sha>` (<sha>
# the PR head the round read; a bare `tree unchanged` is refused), small lane,
# inline lane, auto-once, not landed, not reviewed (also spelled `not reviewed:
# <reason>`, read as the same stop here and in `gate clean`) or `not invoked:
# <primary> reviewed` (a fallback whose primary, a reviewer on the row, stopped
# on none of not reviewed, not landed, inline lane or not invoked), and unless
# it is `not reviewed`, `not invoked: <primary> reviewed` or `inline lane` at
# least one `Round: <reviewer> <n>: <text>`, <n> a positive integer. `grade`
# writes `Grade: <word>` under the `## Grade` section, replacing the line a call
# before it wrote.
#
# `ran <name>... <where>` marks, for each verification name the Run file's
# results section knows, that its `Run:` line has just run: it appends
# `Ran: <name> at <sha>` under `## Evidence`, <sha> the HEAD now, and writes
# nothing if any name is malformed. A name may begin with `--`: the names end at
# the first `--file`, `--issue`, `--scratchpad`, `--result` or `--head`. Call it
# as each run ends, not at the close.
# `prove` runs the sibling `revert-red` on <test> and its <path>s, passing any
# `--fix <commit>` (a review fix's commit: only its own change to the paths is
# taken out, not the merge base's copy) and any
# `-- <command>...` tail to it as typed (`revert-red --help` says what the
# command must be). On its exit 0
# it appends `Reverted-fix: <test>: red at <sha> reverting <path>...` under
# `## Evidence`, <sha> the HEAD revert-red ran on; on its exit 1 or 2 it writes
# nothing and exits with that code, passing revert-red's answer through. It
# writes each path top-relative with no leading `./`, the form `close 4` looks
# it up by, and refuses a reverted path holding whitespace, which the line could
# not be read back from. `probe` runs <command> as argv, never through a shell
# string, at the checkout top with stdin from /dev/null, and appends
# `Probe: <ref>: <command> => exit <n> at <sha>: <last output line>`, <command>
# shell-quoted word by word (`printf %q`) so it re-runs as recorded, under
# `## Evidence`, the last nonblank line of stdout and stderr together, or
# `(no output)`. It exits 0 whatever the command's exit; a ref holding `: `
# blurs where the ref ends for the line's readers and is refused. `call` runs
# nothing: it appends
# `- <command> · <why>` under `## Direct calls`, <command> quoted as `probe`
# quotes it, before the run makes the call, and refuses (exit 1) a command that
# does not open on `gh api` or `az rest` and an argument, a `gh api` naming
# the `graphql` endpoint (the cloud sandbox's proxy refuses GitHub GraphQL,
# which `gh issue edit` and its kin use, #254), or one carrying an
# `Authorization` header, whose credential the line would publish. `note`
# appends <line> under `## <section>`, creating the section at the end of the
# file when the record has none: the one way a run writes its `Round:`, `Stop:`,
# `Criterion` and evidence lines, since a heredoc edit of the record from a
# worktree-isolated session is refused (#787). <section> is the heading's name
# without its `## `, and <line> is one line that opens on neither `-` nor `#`,
# so it is never a flag and never a heading. A <line> opening on `Criterion`
# that is not `Criterion <n>: met: <proof>` or `Criterion <n>: not met:
# <reason>` is refused (exit 1) naming both forms, since `gate clean` reads any
# other as unmet (#853: `met at <sha>:`). Under `Criteria`, a <line> not opening
# on `Criterion <n>: ` is written `- <n>: <line>`, <n> the number after the
# highest entry, the new criterion a phase-1 rewrite owes (#863).
#
# Reaches no host and writes only the record root, except through the command
# `probe` is given, which may do either. `close 4` reads the checkout's git
# state, `prove` runs the sibling `revert-red`, and a read that fails is exit 2,
# never a clean diff.
#
# stdout: one JSON object per call
#   init: {run_file, id, scratch, items[]}, plus {opened: 0, mirror} when it opened phase 0
#     and {criteria: <entries recorded>} when given --criteria
#   open, close, skip: {run_file, phase, state, line, mirror[, reason]}
#   close 3 --result on a closed or skipped phase 3: {run_file, phase, state, line}
#     with state "recorded", the row unchanged and no mirror
#   ran: {run_file, ran: [{name, head}]}
#   grade: {run_file, grade}
#   next: {run_file, closed: {phase, line, mirror}, opened: {phase, line, mirror}};
#     closed is null where no phase was open
#   timing: {run_file, start_to_pr, pr_to_gate, phases{}, row}; a skipped phase
#     reads `skipped` and a phase with no range `unverified`
#   prove: {test, paths, red, command[, fix], run_file, head}, revert-red's verdict plus the
#     record and the head, its paths as typed where the line normalises them;
#     on a refusal revert-red's own answer. <test> must be one of the words of
#     <command>, character for character, or a node ID under it (<test>::<name>)
#   probe: {run_file, ref, command, exit, head, last}
#   call: {run_file, command, why}
#   note: {run_file, section, line}; a `Criterion`, `Past-cap` or `Docs-only`
#     line is refused unless in the form `gate clean` reads
#   gate record: {run_file, head, verdict}
#   gate read: {run_file, verdict, head, gates, current, behind}; gates is null
#     for a record made without one; behind is null where git cannot count the
#     commits between the recorded head and <sha>
#   gate summary: {clean, head, held_by[], criteria_recorded, unmet_criteria[],
#     unmet_criteria_line (`unverified: ...` where no `## Criteria` was read),
#     clean_gate_held_by_line}, `gate clean`'s evaluation (exit 0 clean, 1 held)
#     of the head `read-pr <pr>` returns and the final `ci-wait` answer saved as
#     `ci-wait.json` beside the Run file; the merge summary's `Unmet criteria`
#     and `Clean gate held by` lines are its two `_line` fields. It refuses,
#     naming the step to run, with exit 2 (no verdict) where that answer or
#     the head is absent.
#   close 5 refuses while a `## Criteria` entry has no `Criterion <n>:` line or
#     its last line is a `not met` whose reason is a placeholder (pending, todo,
#     tbd, later, wip, open or unknown): one that settles later is written
#     `not met: <what is outstanding and the step that settles it>`
#   gate clean: {clean, held_by[]}; clean means the current gate passed or deferred to CI every
#     check, every profile CI leg succeeded on head or, where No-checks legal:
#     says yes and no local gate deferred to CI, ran no check there,
#     verifications passed at a covered head or were
#     inapplicable or deferred to a green associated CI leg, and each
#     reviewer's loop stopped on tree unchanged at a covered head with a
#     dispositioned Round, or on cap at a docs-only end (dispositioned Rounds
#     1 through n, n short of any Cap:, none re-raising a declined finding)
#     whose last `Docs-only:` line is
#     stamped at a covered head (an uncovered one holds, naming the range), or
#     on inline lane for an on-request reviewer while the last Lane: line
#     reads inline, the round `--inline` waived, which covers no primary. A
#     not-reviewed or not-landed primary, or one that spent its Cap: (dispositioned Round
#     lines numbered 1 through Cap) and stopped at cap, may be covered by a
#     fallback that stopped so, and a fallback is owed its own stop only after
#     such a primary. Other cap,
#     small lane and auto-once stops, a deferred gate beside a non-green check
#     outside Legs:, nonblank Override evidence, tracker drafts beside the
#     Run file (excluding .base.md), and a last `Stop: self-review: cap`
#     where no reviewer stopped on tree unchanged at a covered head with a
#     dispositioned Round hold the gate; a last `cap at <S>` is also lifted by
#     a reviewer whose last stop is stamped at <S> or a descendant. So does
#     each `## Criteria` entry that is not met or has no `Criterion <n>:`
#     line, and a reviewer with a Round whose text opens `re-raised declined: `
#     stopping on tree unchanged: it stops on cap, which that round also keeps
#     from owing a fallback. An ordinary deviation, a Ship-defect line or a
#     defect draft does not hold it: a defect that breaks a gating step fails
#     that step's evidence. This read is independent of Merge.
#     A stamp covers <sha> when it is <sha>, or when every later commit on
#     <sha>'s first-parent line is a clean base merge: two parents, the second
#     an ancestor of the local origin/HEAD (fetch first, or an unfetched base
#     breaks coverage), its tree the one `git merge-tree --write-tree` gives;
#     a walk that cannot read origin/HEAD, a commit's parents, the ancestry
#     check or merge-tree exits 2.
#     A pass also covers <sha> when its Verification's `Paths:` globs,
#     comma-separated git `:(glob)` pathspecs, each matching some file, match
#     no file that differs between its stamp and <sha>. A stop or pass with no
#     stamp, or one not covering <sha>, holds, named with <sha> and its stamp
#     where it has one; the Local gate record still needs <sha> itself.
# exit: 0 ok · 1 the mechanic's own refusal (a gate's missing evidence among
#   them, a held clean gate, or a test that stayed green under `prove`) · 2
#   malformed invocation, a read a gate needs that failed, or revert-red's own
#   tooling refusal under `prove`
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: run-file init <issue|slug> [--scratchpad <dir>] [--rebuild] [--state <n>=<spec>] [--tripwires <t>] [--verifications <v>] [--reviewers <r>] [--legs <l>] [--from-profile [<path>]] [--criteria <file|->] | open <n> | next <n> | close <n> [--result <name>=<word>[: <note>]] [--head <sha>] | skip <n> <reason> | grade <patch|minor|breaking> | gate record <file|-> [--head <sha>] | gate read --head <sha> | gate clean <ci-file|-> --head <sha> | gate summary <pr> | timing | ran <name>... <where> | prove <test> <path>... [--fix <commit>] <where> [-- <command>...] | probe <ref> <where> -- <command>... | call <why> <where> -- <command>... | note <section> <line>, each taking <where>: --file <path> or --issue <n|slug> [--scratchpad <dir>, default ${XDG_STATE_HOME:-~/.local/state}/ship/<repo key>] resolving <root>/ship-<issue>/run.md'
ship_help "$usage" "$@"
ship_args "$usage" arg "$@"
verb=$1; shift

# The implementation lives in sibling files under lib/, sourced in the order
# their pieces depend on each other: helpers, then the gates, then the verbs.
# shellcheck source=lib/run-file-items.sh
source "$SHIP_SCRIPTS/lib/run-file-items.sh"
# shellcheck source=lib/run-file-criteria.sh
source "$SHIP_SCRIPTS/lib/run-file-criteria.sh"
# shellcheck source=lib/run-file-phase-gates.sh
source "$SHIP_SCRIPTS/lib/run-file-phase-gates.sh"
# shellcheck source=lib/run-file-clean-gate.sh
source "$SHIP_SCRIPTS/lib/run-file-clean-gate.sh"

case $verb in
init | open | next | close | skip | grade)
  # shellcheck source=lib/run-file-verbs-record.sh
  source "$SHIP_SCRIPTS/lib/run-file-verbs-record.sh"
  ;;
*)
  # shellcheck source=lib/run-file-verbs-gate.sh
  source "$SHIP_SCRIPTS/lib/run-file-verbs-gate.sh"
  ;;
esac
