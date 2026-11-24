#!/usr/bin/env bash
# Ship library: --repo targeting, which host.sh's adapter interface is reached
# through. Sourced by ../_lib.sh, never run.

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
