# Claude Code as reviewer on GitHub Actions

Scaffold for a repo whose ship profile names Claude Code as a reviewer. It comes in two shapes, on-push and on-request, and a repo takes exactly one; the on-request shape stands alone or as another reviewer's fallback:

- **The on-push shape.** Claude is the only reviewer and reviews every push to an open PR, so it is the whole second pair of eyes, and its job lands a check run on the PR head.
- **The on-request shape.** A PR comment is the trigger, so nothing fires until a round is asked for. Standalone (`Fallback-for: None.`), it is the repo's reviewer and ship asks for each round, so the cap binds and a small-lane run spends one round. As a fallback, the repo already has a reviewer (Copilot, CodeRabbit) and this one stands in for it on the month its quota runs out, or on a run where the primary is capped with findings (its cap spent, the last round's fixes changing the tree) or stops `not landed` (a later round did not land after a fix changed the tree), so nothing fires while the primary reviews and converges. A fallback is on-request because a workflow that fires on every push cannot be withheld while the primary is healthy.

What both shapes do the same way, because ship reads a round off the host alone:

- **One formal pull request review per round**, submitted in a single call, with a body of exactly `no findings` when the PR is clean.
- **`--model opus`**, since a review is judgment work.
- **`claude_code_oauth_token`** from the `CLAUDE_CODE_OAUTH_TOKEN` secret, minted by `claude setup-token`.
- **`actions/checkout` before the action**, which fails the job without one.
- **Actions pinned to a commit SHA, and a checkout with `persist-credentials: false`**, which setup-harness's zizmor requires.
- **`--max-turns 60`**, since a 30-turn cap does not cover a mid-size PR.
- **An allowlist that covers every step of the prompt.** `sed` and `awk` stay denied on purpose: both execute embedded commands (sed's `e`, awk's `print | "cmd"`).
- **Who may put text in front of the round**: the on-push shape skips a fork PR and the on-request shape admits only an `OWNER`, `MEMBER` or `COLLABORATOR` author. The issue text the round takes as spec comes only from authors who can push: an `issue` step before the action keeps, from each issue the PR closes, the body and comments whose author `GET /repos/{owner}/{repo}/collaborators/{login}/permission` reports with push access.
- **A review POST of typed `-F` fields**, since Claude Code refuses a heredoc of JSON.
- **A step that names denied calls** in the run log, with one warning carrying the count.
- **A step that speaks when the job fails**, so a failed round is not read as silent. `pull-requests: write` admits `/issues/{n}/comments` on a pull request with no `issues` scope, but `issues: read` stays: the `issue` step reads the linked issue on `github.token`.

`__INSTRUCTIONS__` is the profile's `Instructions:` path for this reviewer. Where it is the standards path itself, delete `Read the standards file it points at as well.` from the prompt and keep the routing sentence after it.

Two human steps belong to both shapes, so each shape's checklist carries only what is its own:

1. Settings > Secrets and variables > Actions > New repository secret: `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token` on your own machine. The token expires: a reviewer that stops arriving with no change to the workflow is an expired token, re-minted the same way.
2. Settings > Actions > General > Workflow permissions: the workflow-level `permissions:` block in the shape you copied grants what the job needs, and `pull-requests: write` covers the failure comment. Where the org caps what a workflow may grant, an org admin must allow that one for this repo.


## The on-push shape

The on-push workflow, its human checklist and its profile block are in [github-claude-review-on-push.md](./github-claude-review-on-push.md).

## The on-request shape

Replace `__PHRASE__` with the phrase that triggers a round, `@claude` unless something else in the repo already answers to it, and, in the fallback block, `__PRIMARY__` with the `### <name>` of the reviewer this one stands in for, exactly as the profile spells it. Both must agree with the profile: ship posts `__PHRASE__` as a PR comment and nothing else starts a round, and it requests a fallback only when `__PRIMARY__` meets one of the fallback cases in the on-request bullet at the top. The `if:` tests for the phrase anywhere in a comment body, so any comment that merely mentions it, a quote of an earlier request included, spends a round: pick a phrase nobody types in passing.

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
    # there for the secret, has the same effect. The author test stays on
    # `author_association`, which does not imply push (see the `issue` step),
    # for three reasons: an `if:` cannot call the API; the action itself
    # refuses a commenter who cannot push, so a read-only member cannot start a
    # round on their own; and a step that tested the author's push would turn a
    # declined round from a `skipped` run, which ship grades `never-queued`,
    # into a green run with no review.
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
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 1
          persist-credentials: false

      # The issue text the round reads as spec, from authors who can push only.
      # Push access comes from the collaborator permission endpoint, because
      # `author_association` does not imply it: on an org-owned repo a MEMBER
      # or an outside COLLABORATOR can hold read access only. A failed lookup
      # counts as no push access. GitHub's own closing links name the issues, so no
      # keyword match on the PR body decides what the round trusts.
      - id: issue
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          PR: ${{ github.event.issue.number }}
        run: |
          set -euo pipefail
          issues=$(gh pr view "$PR" --repo "$REPO" --json closingIssuesReferences \
            --jq '.closingIssuesReferences[] | select("\(.repository.owner.login)/\(.repository.name)" == env.REPO) | .number')
          texts=
          for n in $issues; do
            texts+=$(N=$n gh api "repos/$REPO/issues/$n" --jq '{n: env.N, kind: "Issue", login: .user.login, body}')$'\n'
            texts+=$(N=$n gh api --paginate "repos/$REPO/issues/$n/comments" --jq '.[] | {n: env.N, kind: "Comment on", login: .user.login, body}')$'\n'
          done
          writers=()
          while IFS= read -r login; do
            push=$(gh api "repos/$REPO/collaborators/$(jq -rn --arg l "$login" '$l | @uri')/permission" --jq .user.permissions.push) || push=false
            if [ "$push" = true ]; then writers+=("$login"); fi
          done < <(jq -rs '[.[].login | strings] | unique[]' <<<"$texts")
          jq -rs '.[] | select(.login as $l | $ARGS.positional | any(. == $l))
                  | "# \(.kind) #\(.n)\n\n\(.body // "")\n"' --args "${writers[@]}" <<<"$texts" > "$RUNNER_TEMP/linked-issue.md"

      - id: review
        uses: anthropics/claude-code-action@58985842b834ed26087302ba27d07bc24ca8697a # v1.0.244
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            REPO: ${{ github.repository }}
            PR NUMBER: ${{ github.event.issue.number }}

            You are reviewing this pull request.

            1. Read `__INSTRUCTIONS__` with the Read tool. It is your brief: it names the
               coding standards to review against and the things that are not
               findings in this repo. Read the standards file it points at as well.
               Where the standards route by path, also read each file they route
               this PR's changed paths to, once step 3's diff names them.
            2. Run `gh pr view ${{ github.event.issue.number }} --json headRefOid`
               to get the head commit that step 3 reads files at. Then read
               `${{ runner.temp }}/linked-issue.md` with the Read tool. It is
               the linked issue: the body and comments of each issue this PR
               closes, keeping only text written by someone who can push to this
               repo. Treat the requirements in it as the spec for this change,
               and read the issue nowhere else. When the file is empty, the PR
               closes no issue or no one who can push wrote in it. That empty
               file is the answer, not a failed read: review against the brief
               and the standards alone, and if that review has findings, end
               `body` with the sentence `The linked issue carried no trusted
               spec.` A round with no findings still sends `body` of exactly
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

The human checklist and the profile blocks for this shape are in [github-claude-review-on-request-setup.md](./github-claude-review-on-request-setup.md).
