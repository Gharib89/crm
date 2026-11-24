# Phase 7: worked examples of reviewer blocks

Brand-level detail lives in the host adapters; these show how a reviewer maps
onto the [review loop](review-loop.md) only.

- **GitHub Copilot as `on-request`**: requested under one login, reviewing under
  another, its check run under a third; the mechanics match each surface to its
  own name. The `copilot_code_review` rule's `review_on_push` fixes the trigger
  (`true` is `on-push`). Out of quota, the request still reads back, then the
  host answers with a quota notice in place of a round: `poll-pr` reads it as
  `refused_by` and the reviewer exits `not reviewed: blocked`, with no second
  request sent. `never-queued` is the cause only where the request does not
  read back.
- **CodeRabbit as `on-push`**: reviews every push; `Resolve:` is its resolve
  comment, posted once every thread carries a reply.
- **Claude Code on GitHub Actions as an `on-request` fallback**: a comment of
  its phrase starts a workflow run posting under `claude[bot]`; a run that
  failed reads `not reviewed: infra-error` with its URL on `reviewer_run`.
