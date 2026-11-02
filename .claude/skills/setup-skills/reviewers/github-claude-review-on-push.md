# Claude Code reviewer, the on-push shape

The on-push shape of [github-claude-review.md](./github-claude-review.md), which holds what both shapes share and the on-request shape. Read it first: the human checklist below begins with its two shared steps.

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
          PR: ${{ github.event.pull_request.number }}
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
            PR NUMBER: ${{ github.event.pull_request.number }}

            You are reviewing this pull request. The PR branch is checked out in
            the working directory.

            1. Read `__INSTRUCTIONS__` with the Read tool. It is your brief: it names the
               coding standards to review against and the things that are not
               findings in this repo. Read the standards file it points at as well.
               Where the standards route by path, also read each file they route
               this PR's changed paths to, once step 3's diff names them.
            2. Read `${{ runner.temp }}/linked-issue.md` with the Read tool. It
               is the linked issue: the body and comments of each issue this PR
               closes, keeping only text written by someone who can push to this
               repo. Treat the requirements in it as the spec for this change,
               and read the issue nowhere else. When the file is empty, the PR
               closes no issue or no one who can push wrote in it. That empty
               file is the answer, not a failed read: review against the brief
               and the standards alone, and if that review has findings, end
               `body` with the sentence `The linked issue carried no trusted
               spec.` A round with no findings still sends `body` of exactly
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
            --allowedTools "Read,Grep,Glob,Bash(gh api:*),Bash(gh pr diff:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*)"

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
