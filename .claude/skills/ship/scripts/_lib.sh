#!/usr/bin/env bash
# Shared helpers for ship's generic mechanics. Sourced by them, and by them
# alone: the file is read into a mechanic's shell and run in no other way, being
# a library rather than a mechanic, answering no command line of its own, and a
# repo's local gate stands on its own. `contract-check.sh` skips it by name for
# that reason.
#
# What a mechanic answers, exit codes, --help, the vocabulary a read comes back
# in and all, is ../reference/mechanics.md. It is the one copy: a second one
# here would be the copy that goes stale.
#
# The library is split by concern into siblings under lib/. This file is the one
# entry point that sources them, by a path relative to its own location, for the
# 30+ callers of `source _lib.sh` (mechanics, host adapters and tests), so none
# of them and no consumer install needs a change:
#   lib/core.sh     exit shapes, grades, argument handling, labels
#   lib/repo.sh     branch convention, checkout, worktree and base-ref reads
#   lib/host.sh     the host adapter interface, --repo targeting
#   lib/skills.sh   skill frontmatter and the composed-skill pin check
#   lib/body.sh     issue and PR body text, fence-aware rewrites, body checks
#   lib/outline.sh  the Change outline check: changed paths the body names
#   lib/jq.sh       the id list and the jq programs the review mechanics embed
#   lib/profile.sh  the ship profile's path and lines
#   lib/poll.sh     a poll loop's host read, cursor, stale head, reviewer rows
#   lib/review.sh   derived reviewer rules, stale-base and PR-state reasons, brief
# Host detection and loading (ship_load_host) and SHIP_AWK_FENCE stay here: the
# contract gate holds each to this one file.
#
#
# SHIP_HOST_ADAPTER is test-only: set, it names the file ship_load_host sources
# in place of host/$SHIP_HOST.sh; ship_detect_host still runs first. Ship's
# source repo points it at a fixture-driven adapter in its test suite, so a
# mechanic runs as a script without a host. Nothing under skills/ outside this

SHIP_SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly SHIP_SCRIPTS

# shellcheck source=lib/core.sh
source "$SHIP_SCRIPTS/lib/core.sh"
# shellcheck source=lib/repo.sh
source "$SHIP_SCRIPTS/lib/repo.sh"
# shellcheck source=lib/host.sh
source "$SHIP_SCRIPTS/lib/host.sh"
# shellcheck source=lib/skills.sh
source "$SHIP_SCRIPTS/lib/skills.sh"
# shellcheck source=lib/body.sh
source "$SHIP_SCRIPTS/lib/body.sh"
# shellcheck source=lib/outline.sh
source "$SHIP_SCRIPTS/lib/outline.sh"
# shellcheck source=lib/jq.sh
source "$SHIP_SCRIPTS/lib/jq.sh"
# shellcheck source=lib/profile.sh
source "$SHIP_SCRIPTS/lib/profile.sh"
# shellcheck source=lib/poll.sh
source "$SHIP_SCRIPTS/lib/poll.sh"
# shellcheck source=lib/review.sh
source "$SHIP_SCRIPTS/lib/review.sh"

# Host detection from the origin remote. Sets SHIP_HOST and the host's
# identifiers; no --host flag and no env var, because every Bash call is a fresh
# shell and a per-call flag is one more thing prose can get wrong.
ship_detect_host() {
  local url
  url=$(git remote get-url origin 2>/dev/null) || return 1
  case $url in
    *github.com[:/]*)
      SHIP_HOST=github
      local p=${url#*github.com}; p=${p#[:/]}; p=${p%.git}; p=${p%/}
      SHIP_OWNER=${p%%/*}; SHIP_REPO=${p#*/}
      SHIP_REPO_SLUG="$SHIP_OWNER/$SHIP_REPO" ;;
    *dev.azure.com/*)
      # https://[user@]dev.azure.com/<org>/<project>/_git/<repo>
      SHIP_HOST=ado
      local p=${url#*dev.azure.com/}; p=${p%.git}
      SHIP_ORG=${p%%/*}; p=${p#*/}
      SHIP_PROJECT=${p%%/_git/*}; SHIP_REPO=${p##*/_git/} ;;
    *@vs-ssh.visualstudio.com:v3/*|*ssh.dev.azure.com:v3/*)
      # git@ssh.dev.azure.com:v3/<org>/<project>/<repo> and the visualstudio.com twin
      SHIP_HOST=ado
      local p=${url#*:v3/}
      SHIP_ORG=${p%%/*}; p=${p#*/}
      SHIP_PROJECT=${p%%/*}; SHIP_REPO=${p#*/} ;;
    *.visualstudio.com/*)
      # https://<org>.visualstudio.com/<project>/_git/<repo> (or DefaultCollection/)
      SHIP_HOST=ado
      local hostpart=${url#*://}; hostpart=${hostpart#*@}
      SHIP_ORG=${hostpart%%.visualstudio.com*}
      local p=${url#*.visualstudio.com/}; p=${p#DefaultCollection/}; p=${p%.git}
      SHIP_PROJECT=${p%%/_git/*}; SHIP_REPO=${p##*/_git/} ;;
    *) return 1 ;;
  esac
  # Before the %20 decode below: the slug keeps the project as the remote spells it.
  if [ "$SHIP_HOST" = ado ]; then
    SHIP_ORG_URL="https://dev.azure.com/$SHIP_ORG"
    SHIP_REPO_SLUG="$SHIP_ORG/$SHIP_PROJECT/$SHIP_REPO"
  fi
  SHIP_PROJECT=$(printf '%s' "${SHIP_PROJECT:-}" | sed 's/%20/ /g')
  export SHIP_HOST SHIP_OWNER SHIP_REPO SHIP_REPO_SLUG SHIP_ORG SHIP_PROJECT SHIP_ORG_URL
}

# ship_load_host [<owner>/<repo>]: detect and source the adapter, or exit 2 with
# the contract. With a repo, the host is GitHub at that repo whatever the origin
# names: the source repo, which a consumer on any host files a Ship defect to.
ship_load_host() {
  if [ -n "${1:-}" ]; then
    SHIP_HOST=github SHIP_OWNER=${1%%/*} SHIP_REPO=${1#*/} SHIP_REPO_SLUG=$1
    export SHIP_HOST SHIP_OWNER SHIP_REPO SHIP_REPO_SLUG
  else
    ship_detect_host || ship_tooling "cannot derive the host from the origin remote"
  fi
  # shellcheck source=/dev/null
  source "${SHIP_HOST_ADAPTER:-$SHIP_SCRIPTS/host/$SHIP_HOST.sh}" \
    || ship_tooling "cannot load host adapter ${SHIP_HOST_ADAPTER:-$SHIP_HOST}"
}

# The one fence rule every transformation here reads: ship_body_replace_section
# below, ship_body_closes under it, and _gh_add_closes in the GitHub adapter. A `## ` heading inside a fence
# is example text, so the two have to agree on where a fence starts and ends, or
# one rewrites the example and leaves the real section alone.
#
# CommonMark, as far as these two need it: an opening fence is three or more
# backticks or tildes under up to three leading spaces, and only a bare run of
# the same character at least as long closes it. The character rules a tilde
# fence in; the length keeps a ``` line inside a ```` fence from closing it and
# inverting the state for the rest of the body; the bareness keeps a ```js line
# inside a ``` fence from doing the same, since a closing fence carries no info
# string. A backtick opener carrying another backtick after its run is not a
# fence at all but paragraph text, which is the one asymmetry with tildes.
# Indented (four space) code blocks are not a fence form here.
#
# `ship_fence(line)` returns the in-fence state after the line: a fence line
# reads as fenced when it opens one and unfenced when it closes one. Prepend it
# to an awk program and call it once per line, before any heading test. It
# holds its state for the length of the input in the globals `_fenced`,
# `_fence_char` and `_fence_len`, so a host program leaves those three names to
# it, and reads no `RSTART` or `RLENGTH` of its own across a call, which
# `ship_deindent`'s `match` overwrites.
#
# `ship_deindent(s)` strips up to three leading spaces. It is a `match` because
# mawk, the default awk on Debian and Ubuntu, reads `sub(/^ ? ? ?/, ...)` as one
# optional space.
#
# `ship_inert(line)` is what every `## ` heading reader calls instead of
# `ship_fence`: 0 for a live line, 1 for a fenced one, 2 for a line of a
# `<details>` record. Such a record runs from a line opening on a whole
# `<details>` or `<details ...>` tag at column 0 (a `<details` with no `>` is
# prose) to its matching line opening on `</details>`, nesting counted, or is
# that one line where it ends on `</details>`: the superseded original a phase-1
# rewrite keeps, or a changelog a PR body folds away. Only a tag at column 0 counts, so prose inside a record that mentions
# the tag mid-line neither nests nor closes it. No line of a record is a
# heading, so the copy of the body's headings it repeats is never matched, never
# ends a section and never ends the preamble. A fence still wins: a `<details>`
# line inside a fence opens nothing, and a fenced `</details>` inside a record
# closes nothing. Its depth lives in the global `_record`, which the awk program
# embedding it leaves to it like the fence globals. The closing-keyword tests
# stay on `ship_fence`, since GitHub honours a `Closes #n` inside `<details>`.
readonly SHIP_AWK_FENCE='function ship_deindent(s) {
    if (match(s, /^ +/)) s = substr(s, (RLENGTH < 3 ? RLENGTH : 3) + 1)
    return s
  }
  function ship_fence(line,   s, c, n) {
    s = ship_deindent(line); c = substr(s, 1, 1)
    if (c != "`" && c != "~") return _fenced
    n = 0; while (substr(s, n + 1, 1) == c) n++
    if (n < 3) return _fenced
    if (!_fenced) {
      if (c == "`" && index(substr(s, n + 1), "`")) return _fenced
      _fenced = 1; _fence_char = c; _fence_len = n
    }
    else if (c == _fence_char && n >= _fence_len && substr(s, n + 1) ~ /^[ \t\r]*$/) _fenced = 0
    return _fenced
  }
  function ship_inert(line,   f, rec) {
    f = ship_fence(line); rec = _record
    if (f) return rec ? 2 : f
    if (line ~ /^<details(>|[ \t][^>]*>)/) {
      _record++; rec = 1
      if (line ~ /<\/details>[ \t\r]*$/) _record--
    }
    else if (_record && line ~ /^<\/details>/) _record--
    return rec ? 2 : 0
  }
'

# The closing-keyword vocabulary, read by both closing tests below so a keyword
# one recognizes is a keyword the other does. It ends on the `#` of the issue
# number: `ship_body_closes` anchors the one number it was asked about to it,
# `ship_body_closing_line` takes any number, since it asks whether a line closes
# anything at all. The "(#n, and #m)" run lets a multi-issue "Closes #75, #81"
# count for #81.
readonly SHIP_CLOSES_RE='\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing))\s+(#[0-9]+[\s,]+(and[\s,]+)?)*#'
