# Claude Code reviewer, the on-request shape: checklist and profile

The human checklist and the profile blocks for the on-request workflow in [github-claude-review.md](./github-claude-review.md), which holds the workflow and what both shapes share.

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
6. Decide whether the `if: failure()` step stays. It costs one PR comment per failed round and nothing on a round that succeeds. Ship grades a failed round here `infra-error` from the run read either way, so dropping the step costs the link on the PR: the human then goes to the Actions tab to find the run themselves. A run asks for this reviewer when it needs the review, standalone or when its primary meets one of the fallback cases in the on-request bullet at the top, so it is the last reviewer whose failures should send you there.

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

`Workflow:` is the file this shape writes, in either block. A round reaches this reviewer through a comment, so its run is what tells a round still being written from one that will not come, and it is the run `poll-pr --reviewer` awaits for this block. Rename the file and this line moves with it.

`Login:` is `claude[bot]` in both shapes: the round is posted by `anthropics/claude-code-action` under the Claude GitHub App its `claude_code_oauth_token` authenticates, not under the Actions identity. Only the `if: failure()` step runs on `github.token` and lands as `github-actions[bot]`, and that comment is not a round, so the login a run awaits is the app's.
