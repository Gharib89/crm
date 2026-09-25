#!/usr/bin/env bash
# ship phase 0: is <issue> actionable, and can this account finish the run?
#
# Push permission first: every read-only call succeeds for an account that
# cannot push, so a wrong login stays invisible until the phase-9 merge answers
# 404 with the whole run already spent. Then the profile (headings, the Schema
# line against this ship's metadata.profile-schema, and the Host cross-check),
# then every not-actionable reason, collected rather than
# first-hit so the human reads one full stop report.
#
# Once the run holds the claim and the worktree, `preflight <issue>` collects
# `already claimed` plus `worktree exists` and exits 1. The profile is checked
# on every call, so an invalid one lands its own reason and a valid one lands
# nothing: a green profile is read out of that `ok: false` by elimination,
# exactly those two reasons and nothing beside them. A run that edits the
# profile mid-run re-validates it with `preflight none`, which has no issue to
# collect a claim, PR or branch reason from, so what is left in `reasons` is the
# profile's and the host's.
#
#   preflight <issue|none> [--unattended]
#
# `none` as the issue argument is the task-spec run: there is no issue to read,
# so the issue block is skipped and `none` is the literal branch and worktree
# suffix. Every other check runs unchanged.
#
# stdout: {host, repo, identity, profile, ok, reasons[], mentions[], mentioned_by[],
#          pruned[], reviewers[]}
#   reasons use the stop names verbatim, detail after a colon:
#   closed · is a pull request · already claimed · existing PR · existing branch
#   · worktree exists · not triaged: run /triage first · ready-for-human:
#   attended only · profile missing · profile invalid: <detail> · skill missing:
#   <detail>
#   mentions[] lists live PRs that name the issue without closing it: context
#   for phase 1, and no kind of stop. mentioned_by[] is the same rows widened to
#   {number, kind: issue|pr, state}, so a run learns whether a mention is an
#   open issue or a merged PR without reaching for the host CLI; both lists
#   carry live cross-references only, open issues and open or merged PRs. pruned[] lists worktrees removed because their PR is merged or
#   closed. reviewers[] is one {name, review_on_push} row per reviewer block:
#   the copilot_code_review ruleset's true or false for the block posting under
#   the Copilot login, null for every other block and where the host could not
#   answer. Phase 7 passes a false to `poll-pr --free-round --review-on-push`.
# exit: 0 actionable · 1 not actionable · 2 tooling, or host-unreachable
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh" || { printf '{"error":"cannot source _lib.sh"}\n'; exit 2; }

usage='usage: preflight <issue|none> [--unattended]'
ship_help "$usage" "$@"
[ -n "${1:-}" ] || ship_tooling "$usage"
n=$1; shift
case $n in -*) ship_tooling "$usage" ;; esac
unattended=false
while [ $# -gt 0 ]; do
  case $1 in
    --unattended) unattended=true; shift ;;
    *) ship_tooling "unknown flag: $1" ;;
  esac
done

ship_load_host
identity=null
unreachable() { # <detail>
  jq -n --arg h "$SHIP_HOST" --arg r "$SHIP_REPO_SLUG" --argjson id "$identity" --arg d "$1" \
    '{host: $h, repo: $r, identity: $id, ok: false, reasons: ["host-unreachable: " + $d],
      mentions: [], mentioned_by: [], pruned: [], reviewers: []}'
  exit 2
}

missing=$(host_tooling_reasons)
[ -z "$missing" ] || unreachable "$(paste -sd';' <<<"$missing")"
me=$(host_identity) || unreachable "cannot read the signed-in identity"
identity=$(jq -n --arg m "$me" '$m')
push=$(host_can_push) || unreachable "cannot read repository permissions"
case $push in
  true) ;;
  unknown) echo "push permission could not be proven on $SHIP_HOST; the merge will tell" >&2 ;;
  *) unreachable "$me cannot push to $SHIP_REPO_SLUG; switch to the account with access" ;;
esac

root=$(ship_main_checkout) || ship_tooling "not inside a git checkout"
reasons=()
reviewers='[]'

# Profile: presence, the fourteen headings in order, the Schema line, and the
# Host cross-check. `ship_profile_path` carries which checkout it is read from,
# and why.
here=$(git rev-parse --show-toplevel)
profile=$(ship_profile_path)
ship_skill="$SHIP_SCRIPTS/../SKILL.md"
if [ ! -f "$profile" ]; then
  reasons+=("profile missing: $profile; run /setup-skills")
else
  expected=$'Host\nWorktree\nLocal gate\nCI\nReviewers\nCoding standards\nVerification\nVersioning and changelog\nPR\nPublic surface\nTriage\nDocs sync\nCurrent docs\nCloud lane'
  actual=$(grep -E '^## ' "$profile" | sed 's/^## //; s/ *$//')
  if [ "$actual" != "$expected" ]; then
    detail=$(diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") | grep -E '^[<>]' | sed 's/^< /missing or misplaced: /; s/^> /unexpected: /' | paste -sd';')
    reasons+=("profile invalid: headings; $detail")
  fi
  reads=$(ship_frontmatter "$ship_skill" profile-schema)
  shipv=$(ship_frontmatter "$ship_skill" version)
  schema=$(awk '/^## /{exit} /^Schema: /{print $2; exit}' "$profile")
  case $schema in
    '') reasons+=("profile invalid: no Schema line; run /setup-skills") ;;
    "$reads") ;;
    *) if [ "$schema" -lt "$reads" ] 2>/dev/null; then
         reasons+=("profile invalid: schema $schema, ship expects $reads; run /setup-skills")
       else
         reasons+=("profile invalid: schema $schema, ship $shipv reads $reads; refresh ship")
       fi ;;
  esac
  declared=$(awk '/^## Host/{f=1;next} /^## /{f=0} f && /^Host:/{sub(/^Host: */,""); print; exit}' "$profile" | tr -d ' `')
  [ "$declared" = "$SHIP_HOST" ] || reasons+=("profile invalid: Host mismatch (profile says '${declared:-nothing}', remote is $SHIP_HOST)")

  # The reviewer blocks, through the one parser. Refused here rather than at
  # phase 7 because a run that reaches phase 7 has already spent itself, and
  # because a fallback naming nobody would sit there unfired: the loop would
  # read as healthy while no second reviewer ever ran. A read loop, not
  # mapfile, for the Bash 3.2 reason the composed-skills loop below gives.
  rows=$(ship_reviewers "$(cat "$profile")")
  while IFS= read -r reason; do
    [ -z "$reason" ] || reasons+=("$reason")
  done < <(ship_reviewer_reasons "$rows" "$here")

  # The one reviewer fact the host can settle. Every other check above reads the
  # block against itself; this one reads it against the setting that actually
  # drives the loop, because a `Trigger:` the ruleset disagrees with is what let
  # #182 spend nine rounds against `Cap: 3`.
  #
  # Scoped to the Copilot login, because `copilot_code_review` governs that
  # reviewer alone. An unreadable answer warns and continues: preflight refuses a
  # profile that is wrong, and admits one it could not check.
  copilot_row=$(ship_copilot_row "$(host_copilot_login)" "$rows")
  copilot_name=${copilot_row%%	*}
  copilot_trigger=${copilot_row#*	}
  review_on_push=""
  if [ -n "$copilot_trigger" ]; then
    if review_on_push=$(host_copilot_review_on_push); then
      reason=$(ship_copilot_trigger_reason "$copilot_name" "$copilot_trigger" "$review_on_push")
      [ -z "$reason" ] || reasons+=("$reason")
    else
      echo "warning: could not read the copilot_code_review ruleset; $copilot_name Trigger: $copilot_trigger is unchecked" >&2
    fi
  fi
  reviewers=$(jq -c --arg n "$copilot_name" --arg r "$review_on_push" \
    'map({name, review_on_push: (if .name == $n and ($r == "true" or $r == "false") then ($r == "true") else null end)})' <<<"$rows")
fi

# The skills ship loads through the Skill tool, from ship's own frontmatter:
# nothing else proves they are installed, so without this a run claims the
# issue and only discovers the absence at the phase that needs the skill.
# A read loop, not mapfile: the mechanics run wherever a consumer repo does,
# including macOS's Bash 3.2, where mapfile is not a builtin and, with no
# `set -e`, preflight would collect nothing and claim the issue anyway.
while IFS= read -r reason; do
  reasons+=("$reason")
done < <(ship_missing_skill_reasons "$here" "$(ship_frontmatter "$ship_skill" composes)")

# Prune sibling worktrees whose PR is merged or closed, leaving alone any whose
# PR is open or unknown.
pruned='[]'
container=$(ship_worktree_container)
if [ -d "$container" ]; then
  for wt in "$container"/*/; do
    [ -d "$wt" ] || continue
    wt=${wt%/}
    br=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null) || continue
    pr=$(host_pr_for_branch "$br") || continue
    case $(jq -r '.state // "none"' <<<"$pr") in
      merged|closed)
        git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1 \
          && git -C "$root" branch -D "$br" >/dev/null 2>&1
        pruned=$(jq --arg w "$wt" '. + [$w]' <<<"$pruned") ;;
    esac
  done
  git -C "$root" worktree prune >/dev/null 2>&1
fi

# The issue itself, unless this is a task-spec run.
mentions='[]'; mentioned_by='[]'
if [ "$n" = none ]; then :
elif ! issue=$(host_issue_get "$n"); then
  reasons+=("issue #$n not found or unreadable")
else
  [ "$(jq -r .state <<<"$issue")" = open ] || reasons+=("closed")
  [ "$(jq -r .is_pr <<<"$issue")" = false ] || reasons+=("is a pull request")
  assignees=$(jq -r '.assignees | join(", ")' <<<"$issue")
  [ -z "$assignees" ] || reasons+=("already claimed: $assignees")

  rfa=$(ship_triage_label ready-for-agent); rfh=$(ship_triage_label ready-for-human)
  if jq -e --arg l "$rfa" '.labels | index($l)' <<<"$issue" >/dev/null; then :
  elif jq -e --arg l "$rfh" '.labels | index($l)' <<<"$issue" >/dev/null; then
    [ "$unattended" = false ] || reasons+=("ready-for-human: attended only")
  else
    reasons+=("not triaged: run /triage first")
  fi

  if linked=$(host_issue_linked_prs "$n"); then
    closing=$(jq -r '[.closing[] | "#\(.number) (\(.state))"] | join(", ")' <<<"$linked")
    [ -z "$closing" ] || reasons+=("existing PR: $closing")
    mentions=$(jq -c '[.mentions[] | select(.kind == "pr") | .number]' <<<"$linked")
    mentioned_by=$(jq -c '.mentions' <<<"$linked")
  else
    reasons+=("existing PR: cross-references unreadable, cannot prove none")
  fi
fi

br=$(git -C "$root" ls-remote --heads origin "*-$n" 2>/dev/null | awk '{print $2}' | sed 's|refs/heads/||' | paste -sd, -) \
  || reasons+=("existing branch: remote unreadable, cannot prove none")
[ -z "$br" ] || reasons+=("existing branch: $br")

for wt in "$container"/*-"$n"/; do
  [ -d "$wt" ] && reasons+=("worktree exists: ${wt%/}")
done

ok=true; [ "${#reasons[@]}" -eq 0 ] || ok=false
printf '%s\n' "${reasons[@]+"${reasons[@]}"}" | jq -Rs --arg h "$SHIP_HOST" --arg r "$SHIP_REPO_SLUG" \
  --argjson id "$identity" --arg p "$profile" --argjson ok "$ok" --argjson m "$mentions" \
  --argjson mb "$mentioned_by" --argjson pr "$pruned" --argjson rv "$reviewers" \
  '{host: $h, repo: $r, identity: $id, profile: $p, ok: $ok,
    reasons: (split("\n") | map(select(. != ""))), mentions: $m, mentioned_by: $mb, pruned: $pr,
    reviewers: $rv}'
$ok
