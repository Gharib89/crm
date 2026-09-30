#!/usr/bin/env bash
# .claude/skills/harness-jdtls-lsp/jdtls-launch.sh: the vendored jdtls plugin's
# launcher, written by setup-harness. Its `.lsp.json` launches it with the
# pinned version and sha256; re-run setup-harness to move them together.
#
#   jdtls-launch.sh <version> <sha256> [jdtls args]
#
# The first launch of a version downloads its build from repo.eclipse.org into
# a per-user cache and keeps it only when its sha256 is the pin's: the
# repository publishes sha1 and md5 alone, so the install check computed the
# sha256 when it pinned the version. Every launch execs the cached build's
# bin/jdtls, which needs a JDK 21 and python3 on PATH.
#
# exit: 1 the download's sha256 is not the pin's · 2 usage, download or unpack failure
set -uo pipefail
[ $# -ge 2 ] || { echo "usage: jdtls-launch.sh <version> <sha256> [jdtls args]" >&2; exit 2; }
version=$1 sha256=$2
shift 2
cache=${XDG_CACHE_HOME:-$HOME/.cache}/harness-jdtls/$version
if [ ! -x "$cache/bin/jdtls" ]; then
  mkdir -p "${cache%/*}" || exit 2
  tmp=$(mktemp -d "$cache.XXXXXX") || exit 2
  trap 'rm -rf "$tmp"' EXIT
  url=https://repo.eclipse.org/content/repositories/jdtls-releases/org/eclipse/jdt/ls/org.eclipse.jdt.ls.product/$version/org.eclipse.jdt.ls.product-$version.tar.gz
  curl -fsSL "$url" > "$tmp/build.tar.gz" || exit 2
  got=$( (sha256sum 2>/dev/null || shasum -a 256) < "$tmp/build.tar.gz" | cut -d' ' -f1)
  [ "$got" = "$sha256" ] || { echo "jdtls-launch: $url has sha256 $got, pinned $sha256" >&2; exit 1; }
  mkdir "$tmp/build" && tar xzf "$tmp/build.tar.gz" -C "$tmp/build" || exit 2
  # A rival first launch may have cached the version meanwhile, and mv onto an
  # existing directory nests inside it; the rival's copy is kept.
  [ -x "$cache/bin/jdtls" ] || mv "$tmp/build" "$cache" || exit 2
  rm -rf "$tmp" # exec below skips the EXIT trap
fi
exec "$cache/bin/jdtls" "$@"
