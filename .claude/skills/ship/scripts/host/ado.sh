#!/usr/bin/env bash
# Azure DevOps adapter: the host_* interface from _lib.sh over the `az` CLI with
# the azure-devops extension, in preference order: `az repos` / `az boards`
# where a subcommand exists; `az devops invoke` for what they lack (PR threads,
# iterations, statuses, the json-patch tag removal); `az rest` for the PR
# completion, whose route invoke cannot reach. Every host API call goes
# through the CLI, so none of them is a curl (the CLI holds the credential for
# all but the PR completion, which hands az the PAT itself where one is set); the only curl in this file is the installer download in
# `host_tooling_install`, which is what puts the CLI on the machine. One
# credential covers the run: `az login` (Entra) or AZURE_DEVOPS_EXT_PAT. Sourced
# by _lib.sh's ship_load_host; needs SHIP_ORG_URL, SHIP_PROJECT and SHIP_REPO
# set.
#
# Contract mapping (Host interface decision): label = tag, claim = Assigned To,
# issue comment = discussion entry, "Closes #n" = --work-items link plus
# transition on completion, PR comment = thread with status closed, thread
# resolved = status fixed|closed|wontFix|byDesign, CI = policy evaluations plus
# PR statuses, blocked-by = Predecessor links, merge = PR PATCH status completed
# with lastMergeSourceCommit.
#
# Verified live on the onboarding run (map ticket "Onboard the Azure DevOps repo
# and run ship attended"): every mechanic from preflight to merge and cleanup
# completed one attended run against Azure Repos + Boards + Pipelines.

ORG=(--org "$SHIP_ORG_URL")
PRJ=("${ORG[@]}" --project "$SHIP_PROJECT")
# WIQL's @project macro resolves to nothing through `az boards query`, so every
# clause using it matched zero rows and the caller read that as an empty
# tracker. The project name goes in literally, single quotes doubled the way
# the tag filter does.
_pr_url() { printf '%s/%s/_git/%s/pullrequest/%s' "$SHIP_ORG_URL" "$(jq -rn --arg p "$SHIP_PROJECT" '$p | @uri')" "$SHIP_REPO" "$1"; }

# Reads retry once after 2 s; az is slow, so creates re-read rather than retry blindly.
azx() { az "$@" -o json 2>/dev/null || { sleep 2; az "$@" -o json; }; }
invoke() { # <http-method> <area> <resource> <api-version> [--route-parameters ...] [--in-file f]
  local m=$1 area=$2 res=$3 v=$4; shift 4
  azx devops invoke "${ORG[@]}" --http-method "$m" --area "$area" --resource "$res" --api-version "$v" "$@"
}

# The work item type for new items and the closed state come from the repo's
# tracker doc (fixed flag lines the setup skill writes), not the ship profile.
_tracker_flag() {
  local doc; doc="$(ship_main_checkout)/docs/agents/issue-tracker.md"
  grep -oE "\*\*$1: [^*]+\*\*" "$doc" 2>/dev/null | head -1 | sed -E "s/^\*\*$1: (.*)\.\*\*$/\1/"
}
ADO_WIT=$(_tracker_flag "Work item type"); ADO_WIT=${ADO_WIT:-Issue}
ADO_CLOSED=$(_tracker_flag "Closed state"); ADO_CLOSED=${ADO_CLOSED:-Done}

host_tooling_reasons() {
  command -v az  >/dev/null || echo "az not installed"
  command -v jq  >/dev/null || echo "jq not installed"
  command -v git >/dev/null || echo "git not installed"
  command -v az >/dev/null && ! az extension show --name azure-devops >/dev/null 2>&1 \
    && echo "azure-devops extension missing: az extension add --name azure-devops"
}
# Microsoft's documented Debian install plus the extension. Not yet measured
# behind the cloud sandbox proxy (map ticket "Probe a cloud session against the
# Azure DevOps repo"); on a developer machine the tooling reasons already name it.
host_tooling_install() {
  local sudo=""; [ "$(id -u)" -eq 0 ] || sudo="sudo -n"
  command -v az >/dev/null || curl -fsSL --connect-timeout 30 --max-time 120 https://aka.ms/InstallAzureCLIDeb | $sudo bash
  az extension show --name azure-devops >/dev/null 2>&1 || az extension add --name azure-devops
}
# Entra login first; a PAT session has no `az account`, so fall back to the
# connection data of the authenticated user.
host_identity() {
  local id
  id=$(az account show --query user.name -o tsv 2>/dev/null) && [ -n "$id" ] && { printf '%s' "$id"; return 0; }
  invoke GET core connectionData 7.1 --query 'authenticatedUser.properties.Account.$value' -o tsv 2>/dev/null \
    | tr -d '"' | grep .
}
# No cheap, reliable push probe exists on ADO without a security-namespace
# walk; preflight reports unknown and the merge tells.
host_can_push() { printf 'unknown'; }

_wi_norm() {
  jq --arg closed "$ADO_CLOSED" '{number: .id, title: .fields["System.Title"],
    body: (.fields["System.Description"] // ""),
    state: (if (.fields["System.State"] == $closed) or (.fields["System.State"] == "Removed") then "closed" else "open" end),
    is_pr: false,
    labels: ((.fields["System.Tags"] // "") | split(";") | map(gsub("^\\s+|\\s+$"; "")) | map(select(. != ""))),
    assignees: [.fields["System.AssignedTo"].uniqueName // empty],
    created_at: .fields["System.CreatedDate"],
    url: (._links.html.href // ($org + "/" + ($project | @uri) + "/_workitems/edit/" + (.id | tostring)))}' \
    --arg org "$SHIP_ORG_URL" --arg project "$SHIP_PROJECT"
}
_wi_show() { azx boards work-item show "${ORG[@]}" --id "$1" --expand relations; }
host_issue_get() { _wi_show "$1" | _wi_norm; }
# `writer` is always null: the author's write access is an ACL read
# (accesscontrollists, Git namespace) that needs their identity descriptor, and
# only vssps.dev.azure.com resolves one, a host outside the cloud sandbox's
# default allowlist; the org host 404s the identities and graph routes and reads
# the comment's own subject descriptor as no permission at all (measured on #605).
host_issue_comments() {
  # "7.1-preview" exactly: az parses the version as a float and chokes on "7.1-preview.4".
  invoke GET wit comments 7.1-preview --route-parameters project="$SHIP_PROJECT" workItemId="$1" \
    | jq '[.comments[] | {author: .createdBy.uniqueName, body: .text, created_at: .createdDate, writer: null}]'
}
# Predecessor links (System.LinkTypes.Dependency-Reverse) are the blockers; ADO
# has the concept, so a failed read is "unavailable" rather than empty.
host_issue_blockers_open() {
  local ids out='[]' id
  ids=$(_wi_show "$1" | jq -r '[.relations[]? | select(.rel == "System.LinkTypes.Dependency-Reverse") | .url | split("/") | last] | .[]') || return 1
  for id in $ids; do
    st=$(host_issue_get "$id" | jq -r .state) || return 1
    [ "$st" = open ] && out=$(jq --argjson i "$id" '. + [$i]' <<<"$out")
  done
  printf '%s\n' "$out"
}
# A linked PR closes the work item on completion, so every live linked PR is
# "closing"; ADO has no undirected mention to separate out.
host_issue_linked_prs() {
  local ids out='{"closing":[],"mentions":[]}' id st
  ids=$(_wi_show "$1" | jq -r '[.relations[]? | select(.rel == "ArtifactLink" and (.url | startswith("vstfs:///Git/PullRequestId/")))
          | .url | split("%2F") | last] | .[]') || return 1
  for id in $ids; do
    st=$(azx repos pr show "${ORG[@]}" --id "$id" | jq -r .status) || return 1
    case $st in
      active)    out=$(jq --argjson i "$id" '.closing += [{number: $i, state: "open"}]' <<<"$out") ;;
      completed) out=$(jq --argjson i "$id" '.closing += [{number: $i, state: "merged"}]' <<<"$out") ;;
    esac
  done
  printf '%s\n' "$out"
}
host_issue_assign()   { azx boards work-item update "${ORG[@]}" --id "$1" --assigned-to "$2" >/dev/null; }
host_issue_unassign() { azx boards work-item update "${ORG[@]}" --id "$1" --fields "System.AssignedTo=" >/dev/null; }
host_issue_add_label() {
  local cur t
  cur=$(host_issue_get "$1") || return 1
  jq -e --arg l "$2" 'any(.labels[]; . == $l)' <<<"$cur" >/dev/null && return 0
  t=$(jq -r '.labels | join("; ")' <<<"$cur")
  azx boards work-item update "${ORG[@]}" --id "$1" --fields "System.Tags=${t:+$t; }$2" >/dev/null
}
# A System.Tags write through `--fields` (an `add` op) merges into the tags
# already there, so dropping a tag needs a json-patch `replace` with the rest,
# and dropping the LAST one a `remove`: the server ignores an empty value however
# it is sent. `az devops invoke` cannot reach the work item's PATCH route (it
# resolves `wit/workItems` to the create route), so the patch rides in a
# `wit/$batch` request, which works on either credential where `az rest` needs
# an `az login`. A batch answers 200 whatever its request did, so the request's
# own code decides.
host_issue_remove_label() {
  local cur f
  cur=$(host_issue_get "$1") || return 1
  jq -e --arg l "$2" 'any(.labels[]; . == $l)' <<<"$cur" >/dev/null || return 0
  f=$(mktemp); trap 'rm -f "$f"; trap - RETURN' RETURN
  jq -c --arg l "$2" --arg u "/_apis/wit/workitems/$1?api-version=7.1" '[.labels[] | select(. != $l)] | join("; ")
    | if . == "" then [{op: "remove", path: "/fields/System.Tags"}]
      else [{op: "replace", path: "/fields/System.Tags", value: .}] end
    | [{method: "PATCH", uri: $u, headers: {"Content-Type": "application/json-patch+json"}, body: .}]' <<<"$cur" > "$f"
  invoke POST wit batch 7.1 --in-file "$f" | jq -e '.value[0].code | . >= 200 and . < 300' >/dev/null
}
host_issue_comment() { azx boards work-item update "${ORG[@]}" --id "$1" --discussion "$2" >/dev/null; }
host_issue_close()   { azx boards work-item update "${ORG[@]}" --id "$1" --state "$ADO_CLOSED" >/dev/null; }
# Markdown body goes in as preformatted HTML: the description field is HTML.
_html_pre() { jq -Rs '"<pre>" + (. | gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;")) + "</pre>"' -r "$1"; }
# The inverse, for a description that is exactly one such block: no raw `<`
# between the tags, since _html_pre escapes every one. The lab's sanitizer hands
# a `_html_pre` write back with `"` as &quot; and U+00A0 as &nbsp; too (probed
# on #289), so those decode as well, and &amp; last, so each decodes once. Any
# other shape is HTML ship did not write, which nothing here edits. The anchors
# are \A and \z because Oniguruma's `$` also matches before a final newline,
# which let `<pre>x</pre>\n` through to a slice that then cut the wrong bytes.
host_issue_body() {
  local out
  out=$(host_issue_get "$1" | jq '.body | if . == "" or test("\\A<pre>[^<]*</pre>\\z")
    then {body: (.[5:-6] | gsub("&lt;"; "<") | gsub("&gt;"; ">") | gsub("&quot;"; "\"")
                         | gsub("&nbsp;"; "\u00a0") | gsub("&amp;"; "&"))}
    else {reason: "the description is not the one <pre> block ship writes, and no HTML is edited"} end') || return 1
  printf '%s\n' "$out"
  jq -e 'has("body")' <<<"$out" >/dev/null
}
host_issue_set_body() { azx boards work-item update "${ORG[@]}" --id "$1" --description "$(_html_pre "$2")" >/dev/null; }
host_issue_create() { # <title> <body-file> <label>
  local out
  out=$(azx boards work-item create "${PRJ[@]}" --type "$ADO_WIT" --title "$1" --description "$(_html_pre "$2")" \
        ${3:+--fields "System.Tags=$3"}) \
    || out=$(azx boards query "${PRJ[@]}" --wiql "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '${SHIP_PROJECT//\'/\'\'}' AND [System.Title] = '${1//\'/\'\'}' AND [System.CreatedBy] = @me ORDER BY [System.CreatedDate] DESC" \
             | jq 'first | select(. != null)') || return 1
  [ -n "$out" ] || return 1
  # Same fallback as _wi_norm: `work-item create` returns no _links, and the
  # retry path selects only System.Id, so href is absent on both branches.
  jq --arg org "$SHIP_ORG_URL" --arg project "$SHIP_PROJECT" \
    '{number: .id,
      url: (._links.html.href // ($org + "/" + ($project | @uri) + "/_workitems/edit/" + (.id | tostring)))}' <<<"$out"
}

# The adapter's remaining functions, by concern. Sourced here, after the helpers above, so each piece sees them.
# shellcheck source=ado.d/prs.sh
source "$(dirname "${BASH_SOURCE[0]}")/ado.d/prs.sh"
# shellcheck source=ado.d/threads-and-lists.sh
source "$(dirname "${BASH_SOURCE[0]}")/ado.d/threads-and-lists.sh"
