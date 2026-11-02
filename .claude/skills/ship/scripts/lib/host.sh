#!/usr/bin/env bash
# Ship library: the host adapter interface every adapter defines, and the
# --repo targeting. Sourced by ../_lib.sh, never run. Host detection and loading
# stay in ../_lib.sh.

# Host adapter interface. Each mechanic sources exactly one of host/github.sh or
# host/ado.sh, chosen from the origin remote rather than from a flag. An adapter
# defines every function below, in that read vocabulary. An entry marked
# `(fails with {status})` prints {status} on failure where the adapter reports
# one, and the mechanic carries it into its verdict's `status`; `az` reports
# none, so on Azure DevOps that verdict reads `status: null`.
#
# file may read it, which that repo's contract gate enforces.
#
#   host_tooling_reasons                 -> one missing-tool reason per line
#   host_tooling_install                 -> install the host CLI where absent; non-zero = could not
#   host_identity                        -> the login the claim is written as
#                                           (fails with {status}, which reaches a verdict only through
#                                           the GitHub writes that open with this read,
#                                           host_pr_comment and host_pr_reply_thread)
#   host_can_push                        -> true | false on GitHub; always unknown on Azure
#                                           DevOps, which has no cheap push probe
#   host_copilot_login                   -> the login `copilot_code_review` governs, or
#                                           nothing on Azure DevOps, which has no Copilot reviewer
#   host_copilot_review_on_push          -> true | false, whether a push to an open PR
#                                           draws a fresh Copilot round on the default
#                                           branch; non-zero and silent where the host
#                                           cannot answer (always, on Azure DevOps, which has
#                                           no such ruleset), which preflight warns on
#   host_issue_get <n>                   -> {number,title,body,state,is_pr,labels[],assignees[],created_at,url}
#   host_issue_comments <n>              -> [{author,body,created_at,writer}]
#                                           writer: true/false, null where the host cannot
#                                           answer (always, on Azure DevOps)
#   host_issue_blockers_open <n>         -> [n, ...] open blockers; non-zero exit = query unavailable
#   host_issue_linked_prs <n>            -> {closing:[{number,state}],
#                                           mentions:[{number,kind,state}]}
#                                           closing: live PRs whose body closes <n>; on Azure
#                                           DevOps, every live linked PR, since a linked PR
#                                           closes the work item on completion.
#                                           mentions: everything else that names it,
#                                           kind "pr" or "issue"; [] on Azure DevOps,
#                                           which records work-item links rather than
#                                           mentions.
#   host_issue_assign <n> <identity>
#   host_issue_unassign <n> <identity>
#   host_issue_add_label <n> <label>
#   host_issue_remove_label <n> <label>  (no-op success when absent)
#   host_issue_comment <n> <body>        (fails with {status})
#   host_issue_close <n>
#   host_issue_create <title> <body-file> <label> -> {number,url}
#   host_issue_body <n>                  -> {body} the body as markdown. On Azure DevOps, a
#                                           description that is the one `<pre>` block
#                                           host_issue_create writes, unwrapped; any other
#                                           shape fails with {reason}.
#   host_issue_set_body <n> <body-file>  (fails with {status}); on Azure DevOps, written as
#                                           that one `<pre>` block.
#   host_issues_open                     -> [{number,title,url}] every open issue, newest first,
#                                           issues alone. Narrows only on Azure DevOps, where WIQL
#                                           refuses a set past 20000 rows, and says so on stderr
#                                           when it does.
#   host_pr_create <head> <base> <title> <body-file> <issue> -> {number,url,created_at}
#   host_pr_get <pr>                     -> {number,url,title,body,head_sha,head_ref,base_ref,draft,state,mergeable}
#   host_pr_for_branch <branch>          -> {number,state,head_sha} of the newest PR whose head branch is <branch>, or null
#   host_pr_checks <pr> <head_sha>       -> [{name,status}]
#                                           status: pending | success | failure.
#   host_check_job <pr> <head_sha> <name>-> {job_id,attempt,log_tail} the latest check of that
#                                           name on the head, as the host's re-runnable job.
#                                           attempt: how many times the job has run on this head,
#                                           so 1 is not yet re-run. log_tail: the job log's last 40
#                                           lines as one string, null where the log could not be
#                                           read. A check that is no job (a commit status, a
#                                           third-party app) answers job_id, attempt and log_tail
#                                           all null. Non-zero where the host could not answer or
#                                           has no job to re-run; always non-zero on Azure DevOps,
#                                           which has no re-run of a policy evaluation here.
#                                           ci-wait --rerun-failed reports that as "unavailable".
#   host_check_rerun <job_id>            (fails with {status}) re-runs that one job and its
#                                           dependents; ci-wait calls it once per failing leg check
#                                           whose attempt is 1. Always non-zero on Azure DevOps.
#   host_pr_reviews <pr> <head_sha> [<full-ids-json>]
#                                        -> {on_head:[REVIEW],all:[REVIEW],total}
#                                           REVIEW = {id,login,state,submitted_at,body}
#                                           state: approved, changes or comment.
#                                           An Azure DevOps reviewer vote is a state rather than a
#                                           written, timed round, so a vote's row carries id null,
#                                           body "" and submitted_at null.
#                                           Adapters send no `substantive`; poll-pr
#                                           adds it (`SHIP_SUBSTANTIVE`).
#                                           id: what the host knows the round by (a GitHub review,
#                                           an Azure DevOps thread), null for a vote. poll-pr --full
#                                           names ids from here.
#                                           body: the round's text. Phase 7 triages from it.
#                                           Past 2000 chars it is clipped and marked
#                                           "...[truncated]", unless <full-ids-json> names its id;
#                                           "" for a vote.
#                                           all: every round across heads, for poll-pr --since.
#                                           submitted_at: one UTC spelling, or null for a vote, which
#                                           the --since rule therefore cannot admit.
#   host_pr_threads <pr>                 -> [{id,resolved,replied,ship_last,author,path,body}]; non-zero
#                                           exit = unavailable. replied: this identity has a comment in
#                                           the thread, which is how phase 7 skips a thread it already
#                                           dispositioned in an earlier round.
#                                           ship_last: the thread's latest comment (system and deleted
#                                           comments aside) carries SHIP_COMMENT_MARKER, so a
#                                           rework's preflight skips a thread ship declined until
#                                           someone replies.
#                                           `replied` cannot serve: ship posts as its owner's identity.
#                                           id: poll-pr --full names ids from here too, to read
#                                           that thread's first comment whole under --brief.
#                                           GitHub rows also carry comment_id, outdated and url,
#                                           which no mechanic reads. comment_id is the thread's first
#                                           review comment, the REST target GitHub's reply is keyed
#                                           to; the thread id is that same id, as a string.
#                                           outdated: the thread sits on a superseded diff. url:
#                                           that first comment's page. Azure DevOps rows carry none
#                                           of the three: the thread id is that reply target already.
#   host_pr_comments <pr>                -> [{author,body,created_at,writer}] the PR's
#                                           conversation comments, created_at in the
#                                           one UTC spelling, writer graded as
#                                           host_issue_comments grades it (null on Azure
#                                           DevOps). On Azure DevOps a comment is a PR-level
#                                           thread's first comment.
#   host_pr_pushed_at <pr> <head_ref> <head_sha>
#                                        -> the UTC time (YYYY-MM-DDTHH:MM:SSZ), alone on a line,
#                                           of the latest push that put <head_sha> at the PR's
#                                           head. Non-zero where the host has no such push to read.
#   host_pr_reviewer_blocked <pr> <login>-> {line, at} | null: that login's latest
#                                           quota or rate-limit notice line, from its review
#                                           bodies or its PR comments, and the UTC time the row
#                                           carrying it was posted. null on Azure DevOps, where
#                                           every comment is a thread that host_pr_reviews already
#                                           carries, so a notice there reaches poll-pr as a review
#                                           row, graded by SHIP_SUBSTANTIVE and refused through
#                                           SHIP_ROUND_BY.
#   host_workflow_runs <file> <since-iso>-> [{status,conclusion,created_at,url,title}] the runs
#                                           of that workflow file, for the event a comment
#                                           transport starts, created at or
#                                           after <since>. status is the host's own vocabulary,
#                                           of which `completed` is the one word ship tests for:
#                                           every other status is a run still able to deliver,
#                                           conclusion the host's own word or null while it runs,
#                                           title the issue or PR the triggering event sits on,
#                                           which is what narrows the runs to one PR. Non-zero where
#                                           the host could not answer; always non-zero and silent on
#                                           Azure DevOps, which has no such read. poll-pr reports
#                                           either as "unavailable" and holds the window to the
#                                           constant on.
#   host_pr_native_activity <pr> <since-iso> <phrase>
#                                        -> {comments,pr_reactions,request_reactions} what a
#                                           reviewer integration with no workflow run leaves on
#                                           the PR: comments, every PR comment as
#                                           {id,login,created_at,updated_at,body,url};
#                                           pr_reactions, the reactions on the PR itself, and
#                                           request_reactions, those on the latest comment
#                                           opening with <phrase> at or after <since> ([] where
#                                           none does), each {content,login,created_at}. content
#                                           is the host's reaction name (`eyes`, `+1`). Non-zero
#                                           where the host could not answer; always non-zero and
#                                           silent on Azure DevOps, which has no native reviewer.
#                                           poll-pr reports either as "unavailable".
#   host_run_denials <run-url>           -> {denied} the count of tool calls the round in
#                                           that completed run was refused: the leading
#                                           numbers of its jobs' `claude-review` warning
#                                           annotations, summed, 0 where none was raised.
#                                           Non-zero where the host could not answer or a
#                                           warning leads with no number; always non-zero and
#                                           silent on Azure DevOps, which awaits no run.
#                                           poll-pr reports a failure as a `denied` of null
#                                           and leaves the run read standing.
#   host_pr_request_review <pr> <login>  -> {requested,readback[],requested_at}
#                                           readback: the host's own names for the PR's reviewers:
#                                           logins on GitHub, each reviewer's uniqueName and
#                                           displayName in one flat list on Azure DevOps. The adapter
#                                           matches <login> against it for `requested`; request-review
#                                           passes it through and no mechanic reads it.
#                                           requested_at: ISO-8601 time of the request event, or,
#                                           where the host records none (always, on Azure DevOps),
#                                           the wall clock, stamped before the call.
#   host_pr_requested_at <pr> <login> [<phrase>]
#                                        -> the UTC time (YYYY-MM-DDTHH:MM:SSZ), alone on a line, the
#                                           reviewer was last asked for a round, which `poll-pr
#                                           --reviewer` takes as its --since when none is given: the
#                                           latest review_requested timeline event for <login>, or,
#                                           with <phrase> (a comment transport), the latest PR
#                                           comment opening with it, by anyone. Non-zero and silent
#                                           where there is none; always, on Azure DevOps, which
#                                           records no request to read back.
#   host_pr_comment <pr> <body-file>     -> {id,url,created_at}
#                                           (fails with {status})
#                                           id: the comment's id on GitHub, the thread's id on
#                                           Azure DevOps, where a PR comment is a thread of its own.
#                                           created_at: the host's own creation time for the
#                                           comment, one UTC spelling, or null where the host
#                                           records none, the same way submitted_at is null for
#                                           a vote. `request-review`'s comment transport
#                                           reports it as `requested_at` and answers the null
#                                           with a wall clock read before the post, so --since
#                                           always has a bound to compare against.
#   host_pr_set_body <pr> <body-file>    (fails with {status})
#   host_pr_set_title <pr> <title>       (fails with {status})
#   host_pr_reply_thread <pr> <thread> <body-file> -> {replied,url}
#                                           (fails with {status})
#                                           A reply inside the thread, leaving its status alone.
#   host_pr_resolve_thread <pr> <thread> -> {resolved}
#                                           A failure may print {resolved,detail}, resolved false,
#                                           which resolve-thread reports as its error ("no such thread").
#   host_pr_merge <pr> <subject> <head>  -> exit 0 once the host reports merged
#                                           The host refuses the merge when the PR's head is
#                                           not <head>, the head the merge gate read.
#   host_prs_open                        -> [{number,title,head_ref,author,url,created_at}]
#   host_issues_ready <label>            -> [{number,title,created_at}] oldest first, unassigned, not PRs

# ship_repo_arg <value>: whether a --repo value is `<owner>/<repo>`. The slug
# lands in a REST path, so an owner is GitHub's alphanumerics and inner hyphens,
# and a repo name that is all dots is a traversal, not a name.
ship_repo_arg() {
  [[ ${1:-} =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$ ]] || return 1
  case ${1#*/} in .|..) return 1 ;; esac
}

# ship_reach_repo <repo> <mechanic-path> <args...>: under --repo, prove the
# named repo's host answers before any read or write, and make every exit 1
# after it carry the invocation that performs the write, shell-quoted, as
# `command`, for the human to run where it succeeds. An Azure DevOps run carries
# no GitHub credentials, and a token that authenticates can still be refused the
# write; either way the write is still the human's to make.
ship_reach_repo() {
  local repo=$1; shift
  SHIP_BY_HAND=$(printf '%q ' "$@"); SHIP_BY_HAND=${SHIP_BY_HAND% }
  host_identity >/dev/null 2>&1 || ship_fail "$repo is unreachable from here"
}
