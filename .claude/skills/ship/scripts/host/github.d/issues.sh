#!/usr/bin/env bash
# GitHub adapter: issue reads and writes. Sourced by ../github.sh, never run.
# shellcheck disable=SC2034,SC2154 # variables the sourcing script sets and reads

_norm_issue='{number, title, body: (.body // ""),
  state: (if .state == "open" then "open" else "closed" end),
  is_pr: (.pull_request != null),
  labels: [.labels[].name], assignees: [.assignees[].login],
  created_at, url: .html_url}'

host_issue_get()      { api "$R/issues/$1" --jq "$_norm_issue"; }
# `writer` is the author's `push` on the repo, asked once per distinct author;
# a failed call, or a comment with no author, is null. Not `author_association`:
# an org MEMBER or an outside COLLABORATOR can hold read access only.
host_issue_comments() {
  local c login push perms='{}'
  c=$(api "$R/issues/$1/comments" --paginate --jq '.[] | {author: .user.login, body, created_at}' | jq -s .) || return 1
  while IFS= read -r login; do
    push=$(api "$R/collaborators/$(jq -rn --arg l "$login" '$l | @uri')/permission" --jq '.user.permissions.push')
    perms=$(jq -c --arg l "$login" --arg p "$push" '. + {($l): $p}' <<<"$perms")
  done < <(jq -r '[.[].author | strings] | unique[]' <<<"$c")
  jq --argjson p "$perms" 'map(. + {writer: ({"true": true, "false": false}[$p[.author // ""] // ""])})' <<<"$c"
}
# GitHub has native issue dependencies, so a failed query is "unavailable" rather than empty.
host_issue_blockers_open() {
  api "$R/issues/$1/dependencies/blocked_by" --paginate --jq '.[] | select(.state == "open") | .number' | jq -s .
}
# Cross-referenced timeline events hydrate each source PR in full, so the split
# between "claims to close this issue" and "merely mentions it" costs no extra call.
host_issue_linked_prs() {
  local n=$1 raw
  raw=$(api "$R/issues/$n/timeline" --paginate --jq '
      .[] | select(.event == "cross-referenced") | .source.issue
      | select(if .pull_request != null then (.state == "open" or .pull_request.merged_at != null)
               else .state == "open" end)
      | {number,
         kind: (if .pull_request != null then "pr" else "issue" end),
         state: (if .pull_request != null and .pull_request.merged_at != null then "merged" else .state end),
         body: (.body // "")}' \
    | jq -s 'unique_by(.number)') || return 1
  local out='{"closing":[],"mentions":[]}' row
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    # Only a PR can close an issue: an issue whose body says "closes #n" is a
    # mention, and reading it as a closing link would stop every run on this
    # issue with `existing PR`.
    if [ "$(jq -r .kind <<<"$row")" = pr ] && ship_body_closes "$(jq -r .body <<<"$row")" "$n"; then
      out=$(jq --argjson r "$row" '.closing += [$r | {number, state}]' <<<"$out")
    else
      out=$(jq --argjson r "$row" '.mentions += [$r | {number, kind, state}]' <<<"$out")
    fi
  done < <(jq -c '.[]' <<<"$raw")
  printf '%s\n' "$out"
}
host_issue_assign()   { api -X POST   "$R/issues/$1/assignees" -f "assignees[]=$2" >/dev/null; }
host_issue_unassign() { api -X DELETE "$R/issues/$1/assignees" -f "assignees[]=$2" >/dev/null; }
host_issue_add_label(){ api -X POST "$R/issues/$1/labels" -f "labels[]=$2" >/dev/null; }
# Check first: a DELETE 404s the same way whether the label was already gone or
# the call itself failed, and a removal reported on a label still there is
# exactly the residue this exists to stop. A read that failed is no verdict, so
# it fails the removal rather than skip the DELETE, as the Azure DevOps one does.
host_issue_remove_label() {
  ship_issue_has_label "$1" "$2"
  case $? in 0) ;; 1) return 0 ;; *) return 1 ;; esac
  api -X DELETE "$R/issues/$1/labels/$(jq -rn --arg l "$2" '$l | @uri')" >/dev/null
}
# A create, so it is create-then-verify: a 5xx may have landed the comment, and a
# blind retry would post it twice. An issue comment is the same REST route as a
# PR comment, so it is host_pr_comment over a body file. Nothing on success, as
# on Azure DevOps; {"status": <n|null>} on failure, which `comment-issue` reads.
host_issue_comment() {
  local f out rc
  f=$(mktemp) || return 2
  trap 'rm -f "$f"; trap - RETURN' RETURN
  printf '%s' "$2" > "$f"
  out=$(host_pr_comment "$1" "$f"); rc=$?
  [ "$rc" -eq 0 ] || printf '%s\n' "$out"
  return $rc
}
host_issue_close()    { api -X PATCH "$R/issues/$1" -f state=closed -f state_reason=completed >/dev/null; }
host_issue_body()     { host_issue_get "$1" | jq '{body}'; }
# Nothing on success, {"status": <n|null>} on failure, as host_pr_set_body.
host_issue_set_body() { jq -n --rawfile b "$2" '{body: $b}' | _gh_write -X PATCH "$R/issues/$1" --input -; }

# Create-then-verify, the shape every create in this adapter has: attempt the
# POST once, and on failure look for the row a slow success would have left
# before posting again, so a flake costs a read rather than a second row. <post>
# performs the one POST and prints the row; <find> prints that row, or nothing
# when there is none. A <find> that fails is not an absent row: it answers
# unknown, so the caller gets the failure rather than a second POST. A <find>
# that ends in a pipe answers that way only under the `pipefail` every mechanic
# sets, which is where a failed `api` upstream of a `jq` becomes the pipeline's
# exit status.
#
# A <post> goes through `_gh_create`, not `api`: `api` retries on its own, and a
# create is retried only after the re-read.
#
# Whichever way this fails, what it prints last is the failed POST's status, so
# the mechanic's verdict names the status of the WRITE. Where the re-read failed
# too, that is the first POST's, held from before the read: the read's own
# status says nothing about whether the create landed.
_gh_create_verify() { # <post-fn> <find-fn>
  local out posted
  if out=$("$1"); then printf '%s\n' "$out"; return 0; fi
  posted=$out
  sleep 2
  out=$("$2") || { [ -n "$posted" ] && printf '%s\n' "$posted"; return 1; }
  if [ -n "$out" ]; then printf '%s\n' "$out"; return 0; fi
  "$1"
}

host_issue_create() { # <title> <body-file> <label>
  local title=$1 file=$2 label=$3 me
  me=$(host_identity) || return 1
  _issue_post() {
    jq -n --arg t "$title" --rawfile b "$file" --arg l "$label" \
      '{title: $t, body: $b, labels: (if $l == "" then [] else [$l] end)}' \
      | _gh_create -X POST "$R/issues" --input - --jq '{number, url: .html_url}'
  }
  _issue_find() {
    api "$R/issues?state=open&creator=$me&sort=created&direction=desc&per_page=20" --jq '.[]' \
      | jq -s --arg t "$title" '[.[] | select(.pull_request == null and .title == $t)] | first | select(. != null) | {number, url: .html_url}'
  }
  _gh_create_verify _issue_post _issue_find
}
