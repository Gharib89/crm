# Claude Code as reviewer on Azure Pipelines

Scaffold for an Azure DevOps repo whose ship profile names Claude Code as a reviewer. A build validation policy runs the pipeline on every PR update (Azure Repos Git has no YAML `pr:` trigger; the policy is the trigger), Claude emits findings as JSON, the pipeline posts each as a PR thread and fails the build on a `critical` finding. Maps to the profile as `Trigger: on-push`, `Gating: yes` when the policy is required: a declined critical is `converged, override needed`. Threads are Azure DevOps PR threads with `fixed | closed` status. Replace `__INSTRUCTIONS__` with the profile's `Instructions:` path for this reviewer, which is the repo's reviewer brief where it has one and the `## Coding standards` path where it has none. The reviewer reads that file itself, rather than a copy of it.

## `azure-pipelines-claude-review.yml`

```yaml
# Runs through a Build Validation branch policy, which is its one trigger.
trigger: none

pool:
  vmImage: ubuntu-latest

variables:
  INSTRUCTIONS_PATH: __INSTRUCTIONS__

steps:
- checkout: self
  fetchDepth: 0   # PR builds check out the merge commit; HEAD^1 is the target branch, HEAD^2 the PR tip

- script: npm install -g @anthropic-ai/claude-code
  displayName: Install Claude Code

- bash: |
    set -euo pipefail
    SCHEMA='{"type":"object","properties":{"findings":{"type":"array","items":{"type":"object","properties":{"severity":{"type":"string","enum":["critical","major","minor"]},"file":{"type":"string"},"line":{"type":"integer"},"message":{"type":"string"}},"required":["severity","file","message"]}}},"required":["findings"]}'
    PROMPT="You are reviewing Azure Repos pull request $(System.PullRequest.PullRequestId).
    HEAD is the PR merge commit; HEAD^1 is the target branch.
    1. Read your review instructions at $INSTRUCTIONS_PATH, and the coding standards file they name where they name one.
    2. Run 'git diff HEAD^1 HEAD' and review only the changed lines against those instructions and the standards they name.
    3. Run 'git log --format=%B HEAD^2 -n 20' and, if a work item is referenced (#1234 or AB#1234),
       treat its intent as the spec for this change.
    Return only findings that require a code change. Severity: critical = must fix before merge
    (correctness, security, data loss, a MUST rule in the standards); major = a standards rule;
    minor = style. file is repo-relative, line is the line in the new file.
    Files under .claude/skills/ are derived copies installed verbatim by the skills CLI and recorded in
    skills-lock.json: do not review their content line by line; report only a change to them with no
    matching skills-lock.json update.
    If nothing is actionable, return {\"findings\": []}."
    claude --bare -p "$PROMPT" \
      --output-format json \
      --json-schema "$SCHEMA" \
      --allowedTools "Read,Grep,Glob,Bash(git diff *),Bash(git log *)" \
      --permission-mode dontAsk \
      --model opus \
      --max-turns 60 \
      --max-budget-usd 5 \
      | tee "$(Build.ArtifactStagingDirectory)/result.json" \
      | jq '.structured_output' > "$(Build.ArtifactStagingDirectory)/findings.json"
    cat "$(Build.ArtifactStagingDirectory)/findings.json"
  displayName: Claude review (JSON findings)
  condition: eq(variables['Build.Reason'], 'PullRequest')
  env:
    ANTHROPIC_API_KEY: $(ANTHROPIC_API_KEY)   # secret variables reach scripts only through an explicit env mapping

- bash: |
    set -uo pipefail
    R="$(Build.ArtifactStagingDirectory)/result.json"
    [ -f "$R" ] || exit 0
    # Model-written input: `#` goes out as its JSON escape, because the agent
    # honours a `##vso[` command anywhere in a line.
    if ! denials="$(jq -r '.permission_denials // [] | .[]
                          | "\(.tool_name) \(.tool_input | tojson | .[0:300])"
                          | gsub("#"; "\\u0023")' "$R")"; then
      echo "##vso[task.logissue type=warning]the denied tool calls could not be read from result.json"
      exit 0
    fi
    [ -n "$denials" ] || exit 0
    n=$(printf '%s\n' "$denials" | wc -l)
    printf '%s\n' "$denials" | sed 's/^/denied: /'
    echo "##vso[task.logissue type=warning]$n tool call(s) denied; this step's log names each on a denied: line"
  displayName: Name the denied tool calls
  condition: and(always(), eq(variables['Build.Reason'], 'PullRequest'))

- bash: |
    set -euo pipefail
    F="$(Build.ArtifactStagingDirectory)/findings.json"
    URL="$(System.CollectionUri)$(System.TeamProjectId)/_apis/git/repositories/$(Build.Repository.ID)/pullRequests/$(System.PullRequest.PullRequestId)/threads?api-version=7.1"
    jq -c '.findings[]' "$F" | while read -r f; do
      body=$(jq -n --argjson f "$f" '
        { comments: [{ parentCommentId: 0, commentType: 1,
                       content: ("**" + ($f.severity|ascii_upcase) + "** " + $f.message) }],
          status: 1 }
        + (if $f.line then
            { threadContext: {
                filePath: ("/" + ($f.file|ltrimstr("/"))),
                rightFileStart: { line: $f.line, offset: 1 },
                rightFileEnd:   { line: $f.line, offset: 1 } } }
           else {} end)')
      curl -sS --fail -X POST "$URL" \
        -H "Authorization: Bearer $SYSTEM_ACCESSTOKEN" \
        -H "Content-Type: application/json" \
        -d "$body" > /dev/null
    done
    touch "$(Build.ArtifactStagingDirectory)/posted.ok"   # every finding is on the PR; only the gate below can fail now
    crit=$(jq '[.findings[] | select(.severity=="critical")] | length' "$F")
    echo "critical findings: $crit"
    [ "$crit" -eq 0 ] || exit 1
  displayName: Post PR threads, fail on critical
  condition: and(succeeded(), eq(variables['Build.Reason'], 'PullRequest'))
  env:
    SYSTEM_ACCESSTOKEN: $(System.AccessToken)   # the YAML form of "allow scripts to access the OAuth token"

# A failed round otherwise leaves nothing on the PR: no thread, no comment, and
# a ship run reads that as `degraded: silent`, indistinguishable from a reviewer
# that did not fire. Costs one thread per failed round; drop it and the silent
# failure comes back. `-e` is off on purpose: a step that dies reading the reason
# posts nothing, which is the silence it exists to break.
- bash: |
    set -uo pipefail
    # A `critical` finding fails the step above by design, so failed() is true on
    # a round that already posted everything it found. The marker is what tells
    # that apart from a round that died: the verdict cannot, because Claude can
    # succeed and the posting loop still fail after it.
    [ -f "$(Build.ArtifactStagingDirectory)/posted.ok" ] && exit 0
    R="$(Build.ArtifactStagingDirectory)/result.json"
    reason=""
    [ -f "$R" ] && reason=$(jq -r '.subtype // ""' "$R" 2>/dev/null || echo "")
    reason=$(printf '%s\n' "$reason" | head -n 1)
    case $reason in ''|success) reason=unknown ;; esac
    BUILD="$(System.CollectionUri)$(System.TeamProjectId)/_build/results?buildId=$(Build.BuildId)"
    URL="$(System.CollectionUri)$(System.TeamProjectId)/_apis/git/repositories/$(Build.Repository.ID)/pullRequests/$(System.PullRequest.PullRequestId)/threads?api-version=7.1"
    body=$(jq -n --arg r "$reason" --arg u "$BUILD" '
      { comments: [{ parentCommentId: 0, commentType: 1,
                     content: ("The Claude review job failed: `" + $r + "`. Check the PR for a review from this round before reading the reviewer as silent. Run: " + $u) }],
        status: "closed" }')
    curl -sS --fail -X POST "$URL" \
      -H "Authorization: Bearer $SYSTEM_ACCESSTOKEN" \
      -H "Content-Type: application/json" \
      -d "$body" > /dev/null
  displayName: Speak when the round failed
  condition: and(failed(), eq(variables['Build.Reason'], 'PullRequest'))
  env:
    SYSTEM_ACCESSTOKEN: $(System.AccessToken)
```

A skill-install PR runs to dozens of files once the derived copies are in it, and exhausts a 30-turn cap before any verdict: the build fails with `error_max_turns` and no threads, and a gating policy rejects the PR. Keep the derived copies in the diff (excluding them removes the only review gate on files that drive agent actions) and rely on the prompt line above plus the 60-turn cap. `--model opus` pins the tier, as [`github-claude-review.md`](github-claude-review.md) does: a review is judgment work, and a cheaper tier reads the diff and misses the standards violation in it; the alias tracks the tier's current model rather than one release. The `--max-budget-usd 5` beside it was measured before that pin and has not been re-measured against Opus, so a round that stops on budget rather than on a verdict is the number to revisit first. `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` works in place of `ANTHROPIC_API_KEY`.

`Speak when the round failed` is what keeps a dead round distinguishable. Claude failing before a verdict posts no thread and fails the build, so a ship run polling the PR reads `degraded: silent` and cannot tell it from a reviewer that did not fire, the indistinguishability [ADR 0002](https://github.com/Gharib89/skills/blob/main/docs/adr/0002-fallback-reviewer-is-on-request-and-conditional.md) exists to prevent; the first-run note above is that case. It costs one closed thread per failed round and nothing on a round that succeeds, the `critical` finding included: that one fails the build by design, and the `posted.ok` marker the posting step leaves behind its loop is what keeps the last step quiet. The marker and not the verdict, because Claude can return `success` and the posting loop still die on a 403 or a malformed findings file, and that round is as silent as one that reviewed nothing. Dropping the step restores the silent failure. It needs no permission the job did not already have: the same threads endpoint under the same `System.AccessToken` the post-findings step uses. The `tee` in the review step is what feeds it, keeping Claude's raw result on disk for the `subtype` while `jq` still reads the structured output off the same stream and `pipefail` still fails the step when Claude does. `succeeded()` on the post-findings step is what keeps the two from both firing: a bare `condition:` replaces the implicit `succeeded()` rather than adding to it, so without it a failed round runs the posting loop against a findings file no step wrote. The review step keeps its bare condition: a failed install leaves no verdict either way, which is a round the last step is right to report.

`Name the denied tool calls` is what makes a refusal visible. The result JSON lists a round's `permission_denials` and nothing prints them, so a round that spent its turns on denied calls reads as clean. The step reads the same `result.json` the `tee` keeps, prints one `denied: <tool> <truncated input>` line per denial, and raises one `logissue` warning with the count; at zero it prints nothing, and a file it cannot read raises a warning saying so. `always()` runs it after a round that failed too, since a round that died on refusals is the one most worth naming. The heredoc refusal the GitHub prompt works around does not arise here: a script posts the findings and the model makes no POST call.

Known gaps, to settle on the first real run. The failure-speaking step is unverified: no run in the source repo can execute an Azure Pipelines step, so its condition, its `subtype` read and its thread POST are proven only by the first failed round on a real pipeline. The denial step shares that gap for its `always()` condition and its `logissue` warning; its `jq` read is the GitHub step's without the slurp-and-select, because `--output-format json` writes a single result object where the action writes a stream. The other gap is older: the threads API documents `pullRequestThreadContext.changeTrackingId` as required for line anchoring on PRs with iterations. If threads land at PR level instead of on the line, look the id up from the PR iterations API and add it to the body.

## Human checklist

1. Pipelines > New pipeline > Azure Repos Git > this repo > Existing Azure Pipelines YAML file > `azure-pipelines-claude-review.yml` > Save (not Run). Note the `definitionId` in the URL.
2. That pipeline > Edit > Variables > New variable `ANTHROPIC_API_KEY`, "Keep this value secret" > Save.
3. Project Settings > Repositories > this repo > Security > `<Project> Build Service (<Org>)` (or `Project Collection Build Service (<Org>)` when the job authorization scope is collection) > **Contribute to pull requests**: Allow. A `TF401027 ... PullRequestContribute` 403 on the POST means this step was missed. It admits the failure thread too: that step posts to the same endpoint.
4. Branch policy on the default branch (Project Administrator):
   ```sh
   az repos policy build create \
     --org https://dev.azure.com/<org> --project <project> \
     --repository-id <repoGuid> --branch main --branch-match-type exact \
     --build-definition-id <definitionId> --display-name "Claude PR review" \
     --blocking true --enabled true \
     --manual-queue-only false --queue-on-source-update-only true --valid-duration 0
   ```
   `--blocking true` is what makes the reviewer gating; `false` gives `Gating: no`.
5. Read the first round's `Name the denied tool calls` step. No warning there means no call was denied, and this step is done. Otherwise, for each `denied:` line, either widen `--allowedTools` to admit the call or record why it stays denied in a comment line above `claude --bare -p` (a `#` after a `\` continuation ends the command), so every denial has an owner from the first round on.

## Profile block this produces

```markdown
### Claude Code
Login: <Project> Build Service (<Org>)
Trigger: on-push
Request: None.
Workflow: None.
Cap: 3
Resolve: threads are set to `fixed` once a finding is dispositioned; the build re-runs on the next push
Gating: yes
Fallback-for: None.
Instructions: __INSTRUCTIONS__
```
