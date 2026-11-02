# Reviewers

Reviewer rules for [setup-skills](../SKILL.md): step 3 runs [Detect](#detect); step 5 checks the draft against [Validate](#validate) and writes [Scaffold](#scaffold), which a setup section re-run naming `reviewer-scaffolding` also runs.

## Detect

Detect per kind, propose each with the trigger the evidence implies, and confirm every one:

- **CodeRabbit.** `.coderabbit.yaml` in the repo: `Trigger: on-push`.
- **Copilot.** `.github/copilot-instructions.md`, plus the `copilot_code_review` rule read from `gh api repos/{owner}/{repo}/rules/branches/{default_branch}` with the branch percent-encoded into that one segment, since a default branch may carry a slash and an unencoded one addresses a different route. That endpoint returns the rules the host has already resolved for that branch, so a rule scoped to another branch stays out of the answer, and it is the endpoint ship's own preflight reads: propose from the repo-wide ruleset list instead and setup writes a profile ship then refuses. The rule's `review_on_push` fixes the trigger: `true` is on-push; `false` or absent is auto-once where the PR's opening round is the only one wanted, or on-request where that same opening round is the loop's round 1 and the cap buys the rest, which is the shape to propose wherever the repo wants a bound it controls.
- **A reviewer with request history.** `review_requested` events on the last ten merged PRs: `on-request`.
- **Claude Code.** `claude-code-action` in a workflow, where the trigger the workflow declares is the trigger the profile takes: a `pull_request` trigger is on-push, an `issue_comment` trigger is on-request, the phrase its `if:` matches is the `Request: comment <phrase>` value, and the workflow file itself is the `Workflow:` value. A Claude review pipeline on Azure DevOps is on-push.
- **Codex.** On GitHub only, a comment by `chatgpt-codex-connector[bot]` on the last ten merged PRs, since a user token cannot read the connector's installation (`user/installations` answers 403): `Trigger: on-request`, `Request: comment @codex review`, `Workflow: native codex`, and `Instructions: AGENTS.md` where the root carries one, else `None.`.

Then, for each reviewer the draft names: `Resolve:` is walked where the proposed trigger is on-push or on-request and the reviewer's findings arrive as inline review comments, and it takes the mechanism that reviewer offers on that host. `resolve-thread` covers the GitHub reviewers that open review threads, Copilot, `claude-code-action` and Codex. A Claude review pipeline on Azure DevOps takes the line [its scaffold](../reviewers/ado-claude-review.md) writes instead, the PR thread's status set to `fixed` once a finding is dispositioned. A reviewer that posts its own resolve comment takes that, as CodeRabbit does. It reads `None.` otherwise, which is what auto-once always takes because its one round ends with every thread dispositioned and none resolved. `Workflow:` is taken from the evidence rather than asked: on each reviewer whose `Request:` is a comment transport, the path of the workflow file detection already read, or `native codex` for Codex, and `None.` on every other. `Cap:` is asked of every reviewer, always.

## Validate

The ten reviewer shapes ship's preflight refuses, which step 5's draft must be clear of:

- a reviewer named `self-review`, or starting `self-review ` and a space, which the Run file reserves for phase 4
- an on-request reviewer with no `Cap:`
- a `Cap:` that is neither a number nor `None.`
- a `Fallback-for:` on a reviewer whose `Trigger:` is not `on-request`
- a `Fallback-for:` naming a reviewer the draft does not list
- a `Request: comment` with no phrase for the transport to post
- a `Request: comment <phrase>` with no `Workflow:` naming the file its round comes from, or `native codex`
- a `Workflow:` on a block whose `Request:` is not a comment transport
- a `Workflow:` naming a file the checkout does not carry, a path climbing out of it with `..` included
- a `Workflow: native <integration>` naming an integration other than `codex`

## Scaffold

**Reviewer scaffolding**, for each reviewer the user named that is not installed: write the files the host needs and hand the human an inline checklist of the steps only they can do (secrets, app installs, branch policies). Claude Code as reviewer: [reviewers/github-claude-review.md](../reviewers/github-claude-review.md), in the shape step 4's one question settled (the on-push workflow, checklist and profile block are in [reviewers/github-claude-review-on-push.md](../reviewers/github-claude-review-on-push.md), the on-request checklist and profile blocks in [reviewers/github-claude-review-on-request-setup.md](../reviewers/github-claude-review-on-request-setup.md); write the shape's sibling along with the main file), or [reviewers/ado-claude-review.md](../reviewers/ado-claude-review.md) on Azure DevOps. Point the scaffold's `__INSTRUCTIONS__` at the profile's `Instructions:` path, which is the repo's reviewer brief where it has one and the coding-standards path where it has none, pointing at that file rather than a copy of it. Other bots (CodeRabbit, Copilot) are configured in their own UIs; the checklist names the setting. In a setup section re-run that names `reviewer-scaffolding`, an installed Claude workflow, the GitHub Actions file or the Azure DevOps pipeline, is compared against the whole current scaffold and each part the repo lacks is proposed, on the setup section re-run's rule in [re-run.md](re-run.md), and where none is installed the write above applies; a plain profile re-run does not run it, and the compare is done when every part of the scaffold is matched, proposed or reported kept. Read the fills, never ask: the shape (on-push or on-request) from the installed trigger, and `__INSTRUCTIONS__`, `__PHRASE__` and `__PRIMARY__` from the reviewer's profile block (`Instructions:`, `Request: comment <phrase>`, `Fallback-for:`). A value that cannot be read leaves that part kept and reported, never guessed. Keep the repo's deliberate departures, a dropped `issues: read` among them. In the GitHub file, propose a pin move only where a `uses:` ref is not a SHA or a checkout lacks `persist-credentials: false`; an existing SHA pin stays even where it differs from the scaffold's, so a refresh never downgrades.
