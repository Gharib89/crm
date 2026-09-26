# Claude Code as reviewer on GitHub Actions

Scaffold for a repo whose ship profile names Claude Code as a reviewer. It comes in two shapes, on-push and on-request, and a repo takes exactly one; the on-request shape stands alone or as another reviewer's fallback:

- **The on-push shape.** Claude is the only reviewer and reviews every push to an open PR, so it is the whole second pair of eyes, and its job lands a check run on the PR head.
- **The on-request shape.** A PR comment is the trigger, so nothing fires until a round is asked for. Standalone (`Fallback-for: None.`), it is the repo's reviewer and ship asks for each round, so the cap binds and a small-lane run spends one round. As a fallback, the repo already has a reviewer (Copilot, CodeRabbit) and this one stands in for it on the month its quota runs out, so nothing fires while the primary is healthy. See [ADR 0002](https://github.com/Gharib89/skills/blob/main/docs/adr/0002-fallback-reviewer-is-on-request-and-conditional.md) for why a fallback is on-request rather than on-push.

What both shapes do the same way, because ship reads a round off the host alone:

- **One formal pull request review per round**, submitted in a single call, with every inline finding attached to it, and a body of exactly `no findings` when the PR is clean. A reviewer that stays silent on a clean PR cannot be told apart from one that failed, and the run waits out its whole poll window either way.
- **`--model opus`.** A review is judgment work: a cheaper tier reads the diff and misses the standards violation in it. The alias tracks the tier's current model rather than pinning one release.
- **`claude_code_oauth_token`** from the `CLAUDE_CODE_OAUTH_TOKEN` secret, minted by `claude setup-token`, so the review is billed to a Claude subscription rather than to API credit.
- **`actions/checkout` before the action.** The action does not clone the repo, and it runs `git diff --name-only -z --relative --ignore-submodules HEAD --` in the workspace of its own accord, before the prompt runs. With no checkout that fails the job outright, `Action failed with error: ... warning: Not a git repository.`, and the job dies before the prompt runs: no review, no findings, and nothing on the PR to say why. Step 1 of the prompt needs it a second time, to read the instructions file off the filesystem.
- **`--max-turns 60`.** A round reads a brief, a spec, a whole diff and then builds one POST, and a 30-turn cap does not cover a mid-size PR: the round exhausts it, logs `error_max_turns`, posts nothing, and still bills the turns it spent.
- **An allowlist that covers every step of the prompt.** A tool the round needs and cannot call is a denied call and a spent turn, so the list is derived from what the prompt's own steps ask for, plus the read-only verbs a round reaches for on a large diff, and the denial step below names any call it still misses. `Read`, `Grep` and `Glob` serve step 1's brief and standards; `Bash(gh pr view:*)` step 2's read of the PR; `Bash(gh pr diff:*)` step 3's diff; `Bash(gh api:*)` step 2's two filtered reads of the linked issue, the POST and step 3's read of a changed file at the PR head; and `Bash(head:*)`, `Bash(tail:*)` and `Bash(wc:*)` because a pipe is refused unless **both** of its commands are granted, so `gh pr diff | wc -l` needs `wc` named too. Those three size a diff and leave the reviewing to step 3: step 3 says review what the one `gh pr diff` returned, and a `head` that truncates it leaves the tail unreviewed with nothing saying so. `Bash(grep:*)` is there for the diff Claude Code saves rather than returns: past about 30 KB, `gh pr diff` comes back as a preview and the path of a `tool-results/*.txt` file, and a round greps that file for its `diff --git` headers. `sed` and `awk` stay denied on purpose, though a round reaches for them on that same file: measured 2026-09-24 on Claude Code 2.1.282, `Bash(sed:*)` admitted `sed -n '1e touch PWNED_sed' probe.txt` and ran the embedded command, and while the harness refused awk's `system()`, awk also executes through `print | "cmd"` and `"cmd" | getline`. The round reads diff text an injected prompt can shape, with the Claude OAuth token in the runner and the app installation token the next bullet describes behind `gh`, so a verb that can execute is a shell an injected prompt gets. Deliberately no `git` entry, though [`ado-claude-review.md`](ado-claude-review.md) grants two: that prompt reaches for `git diff` because Azure DevOps has no `gh`, while both shapes here check out at `fetch-depth: 1` and read the diff through `gh pr diff`, so `git log` would have no history to read and neither prompt asks for either. Nothing here writes to the checkout, the one write the prompt asks for is the review POST the round exists for, and nothing reaches the network beyond `gh`. Narrow it and the round spends its turns on denied calls and posts nothing.
- **Who may put text in front of the round.** The round's own `gh` runs on the Claude app's installation token, which the action gets through OIDC (`id-token: write`) because no `github_token` input is passed. The app's installation sets its scopes, `contents`, `pull-requests` and `issues` at write, and no `permissions:` key narrows them, so `Bash(gh api:*)` admits any REST call that token allows, a release tag on the PR commit included: a tag pushed with an app token triggers workflows, where one pushed with `github.token` does not. Both shapes therefore keep an outsider's diff out of the round: the on-push shape skips a PR from a fork, and the on-request shape admits only a PR whose author is `OWNER`, `MEMBER` or `COLLABORATOR`. Step 2 reads the linked issue through a `--jq` filter that keeps only the body and comments an `OWNER`, `MEMBER` or `COLLABORATOR` wrote, so the reads the prompt prescribes carry no outsider's issue text, and a trusted triage brief on an outsider-filed issue still gets through. That filter binds only the prescribed reads, and the prompt tells the round an empty result is the answer: `Bash(gh api:*)` stays broad for the POST and the head-file read, so a round that goes off-script can still reach ungated text through an unfiltered `gh api` or `gh pr view --comments`.
- **A review POST of typed `-F` fields.** Claude Code's Bash security check refuses a heredoc of JSON before the allowlist is read, so a prompt prescribing `--input -` spends the round's closing turns on a refused POST and a hunt for a shape that is allowed. Every field, each inline comment's four `comments[][...]` fields included, is an `-F`, and the prompt carries the four traps that change what `-F` sends without an error.
- **A step that names denied calls.** The action counts refusals and hides them, so a round that spent its turns on denied calls reads as clean. An always-run step reads the round's `permission_denials` from the action's `execution_file`, prints one `denied: <tool> <truncated input>` line per denial to the run log, which REST can read, and raises one warning annotation with the count; at zero it prints nothing, and a file it cannot read raises a warning saying so. The input is model-written, so the prefix keeps a line from starting a `::` command and every `#` in it is written as the JSON escape `\u0023`, because the runner honours a `##[` command anywhere in a line.
- **A step that speaks when the job fails.** The action failing posts no review and no comment, so a ship run polling the PR reads `not reviewed: silent` under the on-push shape and cannot tell it from a reviewer that did not fire, which is the indistinguishability [ADR 0002](https://github.com/Gharib89/skills/blob/main/docs/adr/0002-fallback-reviewer-is-on-request-and-conditional.md) exists to prevent. Under the on-request shape the run read settles it, `poll-pr --reviewer` awaiting the run the block's `Workflow:` names and reading the failed run as `infra-error` with its URL, and the `if: failure()` step below leaves that URL on the PR for both shapes, with the failure subtype where the action left one and `unknown` where it did not. [`ado-claude-review.md`](ado-claude-review.md) carries the same step as a `condition: failed()` one posting a closed PR thread. It needs no permission the job did not already have: a comment on a pull request is posted to `/issues/{n}/comments`, and `pull-requests: write` admits that endpoint on a pull request, with no `issues` scope at all (probed on a runner, [run on #174](https://github.com/Gharib89/skills/pull/174)). The workflow's `permissions:` block scopes only `github.token`, which checkout and this step use; the round's own `gh`, step 2's two reads of the linked issue included, runs on the app installation token the bullet above describes, which that block does not narrow. So no step on `github.token` reads issues, and `issues: read` serves nothing here: a repo may drop it, and the scaffold keeps it only because no round has yet run without it.

Replace `__INSTRUCTIONS__` with the profile's `Instructions:` path for this reviewer: the repo's reviewer brief where it has one (a repo with Copilot keeps it at `.github/copilot-instructions.md`), else the `## Coding standards` path. The reviewer reads that file itself, rather than a copy of it. Where `__INSTRUCTIONS__` is the standards path itself, delete `Read the standards file it points at as well.` from the prompt.

The two YAML blocks below are each complete on purpose: a consumer copies one of them whole, and a shared block plus a list of substitutions is where a workflow that fails on indentation comes from.

Two human steps belong to both shapes, so each checklist below carries only what is its own:

1. Settings > Secrets and variables > Actions > New repository secret: `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token` on your own machine. The token expires: a reviewer that stops arriving with no change to the workflow is an expired token, re-minted the same way.
2. Settings > Actions > General > Workflow permissions: the workflow-level `permissions:` block in the shape you copied grants what the job needs, and `pull-requests: write` covers the failure comment. The review POST runs on the Claude app's installation token, whose scopes the app's installation sets. Where the org caps what a workflow may grant, an org admin must allow that one for this repo.

## The on-push shape

A `pull_request` event from a fork sees no secret, so the job below skips fork PRs by comparing the head repo to this one. Without that guard the job still starts, the empty token fails the action, and the PR head carries a red `review` check: with step 2 below naming the job on `Legs:` and step 3 making it required, a fork PR can no longer be merged. Skipping is what keeps a fork PR merely unreviewed, and `## Reviewers` unchanged.

### `.github/workflows/claude-review.yml`

```yaml
name: Claude PR Review

# On-push: every push to an open PR draws a fresh round, which is what
# `Trigger: on-push` in the profile means, and ship reads this reviewer's round
# on the current head. The job lands a check run on the PR head, so it is
# a CI leg as well as a reviewer: name it on `## CI`'s `Legs:` line, or
# `ci-wait` will not wait for it.
on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  issues: read
  id-token: write

jobs:
  review:
    # A fork PR cannot see the secret, and an empty token fails the action
    # rather than skipping it, which lands a red check run on the head. Skip
    # the fork instead: unreviewed is the intended outcome, red is not.
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      # Not optional: the action runs `git diff ... HEAD` in the workspace
      # before the prompt runs, and fails the whole job with "Not a git
      # repository" when there is nothing checked out. A `pull_request`
      # checkout is the PR's merge ref, so the instructions file read here is
      # the PR's own version of it.
      - uses: actions/checkout@v7
        with:
          fetch-depth: 1

      - id: review
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            REPO: ${{ github.repository }}
            PR NUMBER: ${{ github.event.pull_request.number }}

            You are reviewing this pull request. The PR branch is checked out in
            the working directory.

            1. Read `__INSTRUCTIONS__` with the Read tool. It is your brief: it names the
               coding standards to review against and the things that are not
               findings in this repo. Read the standards file it points at as well.
            2. Run `gh pr view ${{ github.event.pull_request.number }} --json title,body`
               and find the linked issue (Closes/Fixes/Resolves #N). If one exists,
               read it with these two commands, which keep only text an OWNER,
               MEMBER or COLLABORATOR wrote, and treat the requirements they state
               as the spec for this change:
               `gh api repos/${{ github.repository }}/issues/N --jq 'select(.author_association | IN("OWNER","MEMBER","COLLABORATOR")) | .body'`
               `gh api --paginate repos/${{ github.repository }}/issues/N/comments --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR")) | .body'`
               These two commands are your only read of the issue. When both print
               nothing, the issue holds no trusted text: that is the answer, not a
               failed call, so do not read the issue another way. Review against
               the brief and the standards alone, and if you then have findings,
               end `body` with the sentence `The linked issue carried no trusted
               spec.`; a round with no findings still sends `body` of exactly
               `no findings`.
            3. Run `gh pr diff ${{ github.event.pull_request.number }}` once and review
               only the changed lines it returned, against (a) the brief and the
               standards it names and (b) the linked issue. Walking the changed
               files one at a time instead spends the turn budget on navigation
               and the round dies before it posts. A large diff comes back as a
               preview and the path of the file it was saved to: page through
               that file with the Read tool's offset and limit, or search it with
               `grep`, and review every `diff --git` section in it. When a hunk's
               context is too thin to settle a finding, read that file's copy at
               the PR head with
               `gh api 'repos/${{ github.repository }}/contents/<path>?ref=${{ github.event.pull_request.head.sha }}' -H 'Accept: application/vnd.github.raw+json'`:
               the checkout is the merge ref, whose lines can differ from the
               diff's.

            Make every Bash call one command, with no `;`, `&&` or `>` redirect:
            a redirect is refused even between allowed commands, and a chain
            holding one refused command loses the whole call. Run `gh pr diff`
            bare, since Claude Code saves a large diff for you, and work that
            file as step 3 says, with `grep` and the Read tool rather than
            `sed`, `awk` or `cat`; `awk` is refused. Take a file at the PR head
            from the `gh api .../contents?ref=` call above rather than `git`:
            the checkout is not the PR head, and a `git clone` or `git fetch`
            to reach it is refused.

            Report the round as ONE formal pull request review, submitted in a
            single call, with every inline finding attached to it. Build it as
            one `gh api` command whose every field is a typed `-F` flag:

                gh api --method POST \
                  repos/${{ github.repository }}/pulls/${{ github.event.pull_request.number }}/reviews \
                  -F 'event=COMMENT' \
                  -F 'body=<text of the round, or exactly no findings>' \
                  -F 'comments[][path]=<file>' \
                  -F 'comments[][line]=<line in the new file>' \
                  -F 'comments[][side]=RIGHT' \
                  -F 'comments[][body]=<one finding>'

            Repeat the four `comments[][...]` flags, in that order, once per
            finding; each new `path` starts the next comment. Single-quote every
            value, writing an apostrophe inside one as '\''. Four traps:

            - Use `-F` for every field, never `-f`: gh sends every `-f` field
              ahead of every `-F` one, so mixing them detaches a `line` from its
              comment.
            - A value starting with `@` is read as a file path, so open each
              value with a word: `the @types/node bump ...`, not
              `@types/node bump ...`.
            - gh fills in `{owner}`, `{repo}` and `{branch}` anywhere in a value,
              so a finding quoting code that contains one writes it without the
              braces (`repos/OWNER/REPO/pulls`).
            - A value of exactly `true`, `false`, `null` or an integer is sent as
              that JSON type: that is what makes `line` an integer, and a `body`
              that is only a number or one of those words needs a word added.

            The `-F` flags are the whole call: one command that starts with
            `gh api` and reads nothing from stdin. That is the shape both gates
            admit: Claude Code's Bash check refuses a heredoc of JSON before
            `--allowedTools` is read, and `Bash(gh api:*)` below matches only a
            command that starts with `gh api`, so piping from `echo` is refused
            too. A refused call is a round that stops before the PR.

            One entry in `comments` per finding tied to a line, each naming the
            rule or issue requirement broken and the concrete fix. A finding you
            cannot anchor to a line in the diff goes in `body` rather than
            `comments`: GitHub rejects the entire call when one `comments` entry
            names a line outside the diff, and a rejected call is a round the
            run cannot see.

            Submit exactly one review, even when you found nothing: a reviewer
            that stays silent on a clean PR cannot be told apart from one that
            failed, and the run waits out its whole poll window either way. With
            nothing actionable, send `body` of exactly `no findings` and no
            `comments` flags. Open `body` with the findings: the run reads its
            first 2000 characters, and a summary of the diff or praise ahead of
            them pushes them past that cut.
          claude_args: |
            --model opus
            --max-turns 60
            --allowedTools "Read,Grep,Glob,Bash(gh api:*),Bash(gh pr diff:*),Bash(gh pr view:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*)"

      # The action logs `permission_denials_count` and hides which calls were
      # refused, so a round that spent turns on denied calls looks clean. This
      # names each one in the run log, which REST can read, and warns once with
      # the count. The input is model-written and can quote PR text: the
      # `denied: ` prefix keeps a line from starting a `::` command, and every
      # `#` is written as its JSON escape because the runner also honours a
      # `##[` command anywhere in a line.
      - name: Name the denied tool calls
        if: always() && steps.review.outputs.execution_file != ''
        env:
          EXECUTION_FILE: ${{ steps.review.outputs.execution_file }}
        run: |
          set -uo pipefail
          [ -f "$EXECUTION_FILE" ] || exit 0
          if ! denials="$(jq -rs 'flatten | map(select(.type == "result")) | last
                                 | .permission_denials // [] | .[]
                                 | "\(.tool_name) \(.tool_input | tojson | .[0:300])"
                                 | gsub("#"; "\\u0023")' "$EXECUTION_FILE")"; then
            echo "::warning title=claude-review::the denied tool calls could not be read from the execution file"
            exit 0
          fi
          [ -n "$denials" ] || exit 0
          n=$(printf '%s\n' "$denials" | wc -l)
          printf '%s\n' "$denials" | sed 's/^/denied: /'
          echo "::warning title=claude-review::$n tool call(s) denied; this step's log names each on a denied: line"

      # A failed round otherwise leaves nothing on the PR: no review, no comment,
      # and a ship run reads that as `not reviewed: silent`, indistinguishable from a
      # reviewer that did not fire. Costs one comment per failed round; drop it and
      # the silent failure comes back.
      - if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
          EXECUTION_FILE: ${{ steps.review.outputs.execution_file }}
        run: |
          set -uo pipefail
          reason=unknown
          if [ -n "$EXECUTION_FILE" ] && [ -f "$EXECUTION_FILE" ]; then
            reason="$(jq -rs 'flatten | map(select(.type == "result")) | last
                              | .subtype // ""' "$EXECUTION_FILE" 2>/dev/null || echo "")"
          fi
          reason=$(printf '%s\n' "$reason" | head -n 1)
          case $reason in ''|success) reason=unknown ;; esac
          gh api --method POST \
            "repos/${{ github.repository }}/issues/${{ github.event.pull_request.number }}/comments" \
            -f body="The Claude review job failed: \`$reason\`. Check the PR for a review from this round before reading the reviewer as silent. Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

### Human checklist

Both shared steps above, then:

1. Merge the workflow to the default branch. A `pull_request` event runs the workflow from the PR's own merge ref, so the PR that adds this file is reviewed by it, unlike the on-request shape.
2. Add the job to `## CI`'s `Legs:` in `docs/agents/ship.md`. It lands a check run on the PR head, and `Legs:` is where a run reads what that check proves.
3. For `Gating: yes`: Settings > Branches (or Rules) > require the `review` check to pass before merging.
4. Read the first round's `Name the denied tool calls` step. No `claude-review` warning means no call was denied, and this step is done. Otherwise, for each `denied:` line, either widen `--allowedTools` to admit the call or record why it stays denied in a YAML comment above `claude_args:` (a `#` line inside the `claude_args` block reaches the CLI as an argument), so every denial has an owner from the first round on.
5. Decide whether the `if: failure()` step stays. It costs one PR comment per failed round and nothing on a round that succeeds; dropping it is what restores the silent failure the bullet above describes.

### Profile block this produces

```markdown
### Claude Code
Login: claude[bot]
Trigger: on-push
Request: None.
Workflow: None.
Cap: <the cap the walk settled; recommend 3>
Resolve: resolve-thread
Gating: no
Fallback-for: None.
Instructions: __INSTRUCTIONS__
```

`Cap:` is advisory under on-push: every push draws a round whatever it says, human pushes included, so the number bounds only how long ship waits.

## The on-request shape

Replace `__PHRASE__` with the phrase that triggers a round, `@claude` unless something else in the repo already answers to it, and, in the fallback block, `__PRIMARY__` with the `### <name>` of the reviewer this one stands in for, exactly as the profile spells it. Both must agree with the profile: ship posts `__PHRASE__` as a PR comment and nothing else starts a round, and it requests a fallback only when `__PRIMARY__` exits `not reviewed`. The `if:` tests for the phrase anywhere in a comment body, so any comment that merely mentions it, a quote of an earlier request included, spends a round: pick a phrase nobody types in passing.

### `.github/workflows/claude-review.yml`

```yaml
name: Claude PR Review

# On-request: one round per request, and the request is a PR comment carrying
# `__PHRASE__`, which is what `Request: comment __PHRASE__` in the profile asks
# for. There is deliberately no `pull_request` trigger: it would fire on every
# push, and a reviewer that cannot be withheld cannot be a fallback. It also
# keeps this workflow off the PR head, so it lands no check run and is not a
# CI leg.
on:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: read
  id-token: write

jobs:
  review:
    # An issue_comment fires on issues too, so the PR test comes first. A run
    # that starts and finds nothing to review still costs minutes and still
    # shows up in the Actions tab as a review that happened. No commenter test:
    # the identity a run requests a round under has to pass whatever this `if:`
    # says, and an `if:` that declines it costs the round. Ship grades that
    # `never-queued`, whether the host records no run or one concluded
    # `skipped`, so the loss is named rather than silent; naming it is not
    # reviewing the PR, which is what this reviewer is for. The checklist's
    # "Decide who may spend the token" step is where that is narrowed on
    # purpose. The PR author is tested instead: the round runs on the Claude
    # app's installation token, which `permissions:` does not narrow, so an
    # outsider's PR text must not reach it. The on-push shape's fork skip,
    # there for the secret, has the same effect.
    if: >-
      github.event.issue.pull_request != null &&
      contains(github.event.comment.body, '__PHRASE__') &&
      contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.issue.author_association)
    runs-on: ubuntu-latest
    steps:
      # Not optional: the action runs `git diff ... HEAD` in the workspace
      # before the prompt runs, and fails the whole job with "Not a git
      # repository" when there is nothing checked out. An issue_comment
      # checkout is the default branch rather than the PR head, and that is
      # the right instructions file to review against: the canonical one, not
      # the version the PR under review proposes. The PR head is not checked
      # out; the reviewed diff comes from `gh pr diff`.
      - uses: actions/checkout@v7
        with:
          fetch-depth: 1

      - id: review
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            REPO: ${{ github.repository }}
            PR NUMBER: ${{ github.event.issue.number }}

            You are reviewing this pull request.

            1. Read `__INSTRUCTIONS__` with the Read tool. It is your brief: it names the
               coding standards to review against and the things that are not
               findings in this repo. Read the standards file it points at as well.
            2. Run `gh pr view ${{ github.event.issue.number }} --json title,body,headRefOid`
               and find the linked issue (Closes/Fixes/Resolves #N). If one exists,
               read it with these two commands, which keep only text an OWNER,
               MEMBER or COLLABORATOR wrote, and treat the requirements they state
               as the spec for this change:
               `gh api repos/${{ github.repository }}/issues/N --jq 'select(.author_association | IN("OWNER","MEMBER","COLLABORATOR")) | .body'`
               `gh api --paginate repos/${{ github.repository }}/issues/N/comments --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR")) | .body'`
               These two commands are your only read of the issue. When both print
               nothing, the issue holds no trusted text: that is the answer, not a
               failed call, so do not read the issue another way. Review against
               the brief and the standards alone, and if you then have findings,
               end `body` with the sentence `The linked issue carried no trusted
               spec.`; a round with no findings still sends `body` of exactly
               `no findings`.
            3. Run `gh pr diff ${{ github.event.issue.number }}` once and review
               only the changed lines it returned, against (a) the brief and the
               standards it names and (b) the linked issue. Walking the changed
               files one at a time instead spends the turn budget on navigation
               and the round dies before it posts. A large diff comes back as a
               preview and the path of the file it was saved to: page through
               that file with the Read tool's offset and limit, or search it with
               `grep`, and review every `diff --git` section in it. When a hunk's
               context is too thin to settle a finding, read that file's copy at
               the PR head with
               `gh api 'repos/${{ github.repository }}/contents/<path>?ref=<headRefOid>' -H 'Accept: application/vnd.github.raw+json'`,
               `<headRefOid>` being the one step 2 returned: the checkout is the
               default branch, so a Read there is its copy, not the PR's.

            Make every Bash call one command, with no `;`, `&&` or `>` redirect:
            a redirect is refused even between allowed commands, and a chain
            holding one refused command loses the whole call. Run `gh pr diff`
            bare, since Claude Code saves a large diff for you, and work that
            file as step 3 says, with `grep` and the Read tool rather than
            `sed`, `awk` or `cat`; `awk` is refused. Take a file at the PR head
            from the `gh api .../contents?ref=` call above rather than `git`:
            the checkout is not the PR head, and a `git clone` or `git fetch`
            to reach it is refused.

            Report the round as ONE formal pull request review, submitted in a
            single call, with every inline finding attached to it. Build it as
            one `gh api` command whose every field is a typed `-F` flag:

                gh api --method POST \
                  repos/${{ github.repository }}/pulls/${{ github.event.issue.number }}/reviews \
                  -F 'event=COMMENT' \
                  -F 'body=<text of the round, or exactly no findings>' \
                  -F 'comments[][path]=<file>' \
                  -F 'comments[][line]=<line in the new file>' \
                  -F 'comments[][side]=RIGHT' \
                  -F 'comments[][body]=<one finding>'

            Repeat the four `comments[][...]` flags, in that order, once per
            finding; each new `path` starts the next comment. Single-quote every
            value, writing an apostrophe inside one as '\''. Four traps:

            - Use `-F` for every field, never `-f`: gh sends every `-f` field
              ahead of every `-F` one, so mixing them detaches a `line` from its
              comment.
            - A value starting with `@` is read as a file path, so open each
              value with a word: `the @types/node bump ...`, not
              `@types/node bump ...`.
            - gh fills in `{owner}`, `{repo}` and `{branch}` anywhere in a value,
              so a finding quoting code that contains one writes it without the
              braces (`repos/OWNER/REPO/pulls`).
            - A value of exactly `true`, `false`, `null` or an integer is sent as
              that JSON type: that is what makes `line` an integer, and a `body`
              that is only a number or one of those words needs a word added.

            The `-F` flags are the whole call: one command that starts with
            `gh api` and reads nothing from stdin. That is the shape both gates
            admit: Claude Code's Bash check refuses a heredoc of JSON before
            `--allowedTools` is read, and `Bash(gh api:*)` below matches only a
            command that starts with `gh api`, so piping from `echo` is refused
            too. A refused call is a round that stops before the PR.

            One entry in `comments` per finding tied to a line, each naming the
            rule or issue requirement broken and the concrete fix. A finding you
            cannot anchor to a line in the diff goes in `body` rather than
            `comments`: GitHub rejects the entire call when one `comments` entry
            names a line outside the diff, and a rejected call is a round the
            run cannot see.

            Submit exactly one review, even when you found nothing: a
            reviewer that stays silent on a clean PR cannot be told apart from one
            that failed, and the run waits out its whole poll window either way.
            With nothing actionable, send `body` of exactly `no findings` and no
            `comments` flags. Open `body` with the findings: the run reads
            its first 2000 characters, and a summary of the diff or praise ahead
            of them pushes them past that cut.
          claude_args: |
            --model opus
            --max-turns 60
            --allowedTools "Read,Grep,Glob,Bash(gh api:*),Bash(gh pr diff:*),Bash(gh pr view:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*)"

      # The action logs `permission_denials_count` and hides which calls were
      # refused, so a round that spent turns on denied calls looks clean. This
      # names each one in the run log, which REST can read, and warns once with
      # the count. The input is model-written and can quote PR text: the
      # `denied: ` prefix keeps a line from starting a `::` command, and every
      # `#` is written as its JSON escape because the runner also honours a
      # `##[` command anywhere in a line.
      - name: Name the denied tool calls
        if: always() && steps.review.outputs.execution_file != ''
        env:
          EXECUTION_FILE: ${{ steps.review.outputs.execution_file }}
        run: |
          set -uo pipefail
          [ -f "$EXECUTION_FILE" ] || exit 0
          if ! denials="$(jq -rs 'flatten | map(select(.type == "result")) | last
                                 | .permission_denials // [] | .[]
                                 | "\(.tool_name) \(.tool_input | tojson | .[0:300])"
                                 | gsub("#"; "\\u0023")' "$EXECUTION_FILE")"; then
            echo "::warning title=claude-review::the denied tool calls could not be read from the execution file"
            exit 0
          fi
          [ -n "$denials" ] || exit 0
          n=$(printf '%s\n' "$denials" | wc -l)
          printf '%s\n' "$denials" | sed 's/^/denied: /'
          echo "::warning title=claude-review::$n tool call(s) denied; this step's log names each on a denied: line"

      # A failed round otherwise leaves nothing on the PR: no review, no comment.
      # Ship grades it from the run read either way: the profile block driving
      # this workflow carries `Workflow:`, so `poll-pr --reviewer` reads the
      # failed run itself as `infra-error`. The step is what puts the run URL on
      # the PR, with the failure subtype beside it where the action left one and
      # `unknown` where it did not, so the human reading the PR has the reason or
      # the link that carries it. Costs one comment per failed round.
      - if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
          EXECUTION_FILE: ${{ steps.review.outputs.execution_file }}
        run: |
          set -uo pipefail
          reason=unknown
          if [ -n "$EXECUTION_FILE" ] && [ -f "$EXECUTION_FILE" ]; then
            reason="$(jq -rs 'flatten | map(select(.type == "result")) | last
                              | .subtype // ""' "$EXECUTION_FILE" 2>/dev/null || echo "")"
          fi
          reason=$(printf '%s\n' "$reason" | head -n 1)
          case $reason in ''|success) reason=unknown ;; esac
          gh api --method POST \
            "repos/${{ github.repository }}/issues/${{ github.event.issue.number }}/comments" \
            -f body="The Claude review job failed: \`$reason\`. Check the PR for a review from this round before reading the reviewer as silent. Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
```

### Human checklist

Both shared steps above, then:

1. **Merge the workflow to the default branch before expecting a round.** GitHub dispatches an `issue_comment` workflow from the default branch only, so this file reviews nothing while it is still on a branch: the PR that adds it cannot be reviewed by it, and the first round is on the next PR.
2. Nothing to configure as a check or a policy. The job lands no check run on the PR head, so `## CI` names no leg for it and `No-checks legal:` is unaffected.
3. Post `__PHRASE__` on an open PR by hand once, after the merge, and confirm one formal review comes back. That proves the secret, the permissions and the trigger phrase in one go, and it is the only proof before a run needs this reviewer for real.
4. Read that round's `Name the denied tool calls` step. No `claude-review` warning means no call was denied, and this step is done. Otherwise, for each `denied:` line, either widen `--allowedTools` to admit the call or record why it stays denied in a YAML comment above `claude_args:` (a `#` line inside the `claude_args` block reaches the CLI as an argument), so every denial has an owner from the first round on.
5. Decide who may spend the token. The `if:` above fires for any commenter, a drive-by on a public repo included. To narrow it, replace the whole `if:` with this one. The author line is already in the `if:` above and stays; this step adds only the commenter line:

   ```yaml
       if: >-
         github.event.issue.pull_request != null &&
         contains(github.event.comment.body, '__PHRASE__') &&
         contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.issue.author_association) &&
         contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association)
   ```

   Weigh it first: whatever identity a ship run requests a round under must fall inside that list, and an unattended run whose identity does not gets no review. Ship grades that `never-queued` rather than reading the reviewer as silent, so the loss is named; naming it is not reviewing the PR, which is what this reviewer is there for. A private repo where every commenter can already push needs no commenter line.
6. Decide whether the `if: failure()` step stays. It costs one PR comment per failed round and nothing on a round that succeeds. Ship grades a failed round here `infra-error` from the run read either way, so dropping the step costs the link on the PR: the human then goes to the Actions tab to find the run themselves. A run asks for this reviewer when it needs the review, standalone or covering a primary that was not reviewed, so it is the last reviewer whose failures should send you there.

### Profile blocks this produces

Standalone, the repo's one reviewer:

```markdown
### Claude Code
Login: claude[bot]
Trigger: on-request
Request: comment __PHRASE__
Workflow: .github/workflows/claude-review.yml
Cap: <the cap the walk settled; recommend 2>
Resolve: resolve-thread
Gating: no
Fallback-for: None.
Instructions: __INSTRUCTIONS__
```

As a fallback for another reviewer:

```markdown
### Claude Code
Login: claude[bot]
Trigger: on-request
Request: comment __PHRASE__
Workflow: .github/workflows/claude-review.yml
Cap: <the cap the walk settled; recommend 2>
Resolve: resolve-thread
Gating: no
Fallback-for: __PRIMARY__
Instructions: __INSTRUCTIONS__
```

`Workflow:` is the file this shape writes, in either block. A round reaches this reviewer through a comment, so its run is what tells a round still being written from one that will not come, and it is the run `poll-pr --reviewer` awaits for this block. Rename the file and this line moves with it. Preflight refuses four shapes before the claim, rather than leaving the run polling on the constant: `Request: comment` with `__PHRASE__` left unsubstituted or substituted blank, so the transport has no phrase to post; this block with no `Workflow:`; a `Workflow:` on a block whose `Request:` is not a comment transport (the on-push shape above, which reads `None.`); and a `Workflow:` naming a file the checkout does not carry.

`Login:` is `claude[bot]` in both shapes: the round is posted by `anthropics/claude-code-action` under the Claude GitHub App its `claude_code_oauth_token` authenticates, not under the Actions identity. Only the `if: failure()` step runs on `github.token` and lands as `github-actions[bot]`, and that comment is not a round, so the login a run awaits is the app's.

`Resolve:` is `resolve-thread` in both shapes, because the action attaches its per-file findings as inline threads on the review whichever trigger drew it. A finding that names no file stays on the review body and is answered with `comment-pr`, which leaves nothing to resolve.
