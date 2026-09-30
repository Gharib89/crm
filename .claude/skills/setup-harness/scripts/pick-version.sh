#!/usr/bin/env bash
# The install check's version choice: the newest non-prerelease whose registry publish
# time is at least 7 days old, so a just-published compromised release never
# lands. Newest by version order, not publish order. A yanked PyPI release is
# passed over. apt is exempt (its versions are the distribution's) and takes
# no call here.
#
#   pick-version.sh <npm|pypi|go|crates|nuget|maven|dockerhub> <name>
#   pick-version.sh maven <repository> <group>:<artifact>
#
# <name> is the package name; for go the package path `go install` takes, its
# module found by asking the proxy for each prefix in turn; for maven
# `<group>:<artifact>` on Maven Central, or on the Maven repository whose base
# URL comes before it; for dockerhub the image repository. A yanked crate and
# an unlisted NuGet release are passed over as a yanked PyPI release is. For
# maven the publish time is the release pom's Last-Modified; for dockerhub it is
# the tag's last push, so a re-pushed tag counts from its re-push.
#
# stdout: the version
# exit: 0 picked · 1 no release qualifies · 2 usage, registry unreachable, or name unknown
set -uo pipefail
usage="usage: pick-version.sh <npm|pypi|go|crates|nuget|maven|dockerhub> <name> | maven <repository> <group>:<artifact>"
case ${1:-} in
  -h | --help) echo "$usage"; exit 0 ;;
  maven) [ -n "${2:-}" ] && [ $# -le 3 ] || { echo "$usage" >&2; exit 2; } ;;
  npm | pypi | go | crates | nuget | dockerhub) [ -n "${2:-}" ] && [ $# = 2 ] || { echo "$usage" >&2; exit 2; } ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
registry=$1 name=$2 repo=https://repo1.maven.org/maven2
[ "$registry" = maven ] && [ -n "${3:-}" ] && repo=${2%/} name=$3
# [curl args] <url>: the body. crates.io refuses a request without a User-Agent
# naming its sender. Maven Central's Cloudflare front answers a cloud session's
# requests 429 now and then, and curl's own --retry kept getting 429 on every
# attempt where a fresh curl got 200 (measured), so a 429 is retried here, each
# attempt a new curl; any other failure returns at once.
fetch() {
  local i body err
  err=$(mktemp) || return 1
  trap 'rm -f "$err"; trap - RETURN' RETURN
  for i in 1 2 3 4 5 6; do
    if body=$(curl -fsSL --compressed --max-time 30 -A 'setup-harness pick-version (https://github.com/Gharib89/skills)' "$@" 2>"$err"); then
      printf '%s' "$body"; return 0
    fi
    grep -q 'error: 429$' "$err" || break
    [ "$i" = 6 ] || sleep 5
  done
  cat "$err" >&2; return 1
}

# stdin: one `<version> <iso time>` line per release; prints the pick.
choose() {
  python3 -c '
import datetime, re, sys
cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
best = None
for line in sys.stdin:
    parts = line.split()
    if len(parts) != 2:
        continue  # a release the registry gives no publish time
    v, t = parts
    if not re.fullmatch(r"v?\d+(\.\d+)*", v):
        continue  # a prerelease or a local version
    if datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) > cut:
        continue
    key = tuple(int(p) for p in v.lstrip("v").split("."))
    if best is None or key > best[0]:
        best = (key, v)
if best is None:
    sys.exit(1)
print(best[1])'
}

case $registry in
  npm)
    json=$(fetch "https://registry.npmjs.org/$name") || exit 2
    lines=$(printf '%s' "$json" | python3 -c '
import json, sys
for v, t in json.load(sys.stdin)["time"].items():
    if v not in ("created", "modified"):
        print(v, t)') || exit 2
    printf '%s\n' "$lines" | choose ;;
  pypi)
    json=$(fetch "https://pypi.org/pypi/$name/json") || exit 2
    lines=$(printf '%s' "$json" | python3 -c '
import json, sys
for v, files in json.load(sys.stdin)["releases"].items():
    if files and not any(f.get("yanked") for f in files):
        print(v, min(f["upload_time_iso_8601"] for f in files))') || exit 2
    printf '%s\n' "$lines" | choose ;;
  go)
    mod=$name list=''
    while :; do
      list=$(fetch "https://proxy.golang.org/$mod/@v/list" 2>/dev/null) && [ -n "$list" ] && break
      case $mod in */*) mod=${mod%/*} ;; *) exit 2 ;; esac
    done
    for v in $list; do
      case $v in *-*) continue ;; esac
      info=$(fetch "https://proxy.golang.org/$mod/@v/$v.info") || exit 2
      printf '%s %s\n' "$v" "$(printf '%s' "$info" | sed -n 's/.*"Time":"\([^"]*\)".*/\1/p')"
    done | choose ;;
  crates)
    url="https://crates.io/api/v1/crates/$name/versions?per_page=100"
    # Collected before choosing, so a page that fails exits 2 with no pick.
    lines=$(while [ -n "$url" ]; do
      json=$(fetch "$url") || exit 2
      printf '%s' "$json" | python3 -c '
import json, sys
for v in json.load(sys.stdin)["versions"]:
    if not v["yanked"]:
        print(v["num"], v["created_at"])' || exit 2
      next=$(printf '%s' "$json" | python3 -c 'import json, sys; print(json.load(sys.stdin)["meta"]["next_page"] or "")') || exit 2
      url=${next:+https://crates.io/api/v1/crates/$name/versions$next}
    done) || exit 2
    printf '%s\n' "$lines" | choose ;;
  nuget)
    # The index inlines small registration pages and links the rest by @id; a
    # linked page's own items are the releases.
    releases() {
      python3 -c '
import json, sys
for item in json.load(sys.stdin)["items"]:
    for leaf in item.get("items", [item] if "catalogEntry" in item else []):
        e = leaf["catalogEntry"]
        if e.get("listed", True):
            print(e["version"], e["published"])'
    }
    # The gz-semver2 hive is the only one listing SemVer 2.0 releases.
    index=$(fetch "https://api.nuget.org/v3/registration5-gz-semver2/$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')/index.json") || exit 2
    pages=$(printf '%s' "$index" | python3 -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    if "items" not in p:
        print(p["@id"])') || exit 2
    lines=$(printf '%s' "$index" | releases || exit 2
      for page in $pages; do
        json=$(fetch "$page") || exit 2
        printf '%s' "$json" | releases || exit 2
      done) || exit 2
    printf '%s\n' "$lines" | choose ;;
  maven)
    base="$repo/$(printf '%s' "${name%%:*}" | tr . /)/${name#*:}"
    xml=$(fetch "$base/maven-metadata.xml") || exit 2
    # A Maven repository dates a release only by its files' Last-Modified, one request per
    # version, so walk newest first and stop at the first old enough.
    lines=$(printf '%s' "$xml" | grep -o '<version>[^<]*</version>' | sed 's/<[^>]*>//g' | python3 -c '
import re, sys
vs = [v.strip() for v in sys.stdin if re.fullmatch(r"\d+(\.\d+)*", v.strip())]
print("\n".join(sorted(vs, key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True)))' |
      while IFS= read -r v; do
        mod=$(fetch -I "$base/$v/${name#*:}-$v.pom" | tr -d '\r' | sed -n 's/^[Ll]ast-[Mm]odified: *//p') || exit 2
        line=$(python3 -c 'import email.utils, sys; print(sys.argv[1], email.utils.parsedate_to_datetime(sys.argv[2]).isoformat())' "$v" "$mod") || exit 2
        printf '%s\n' "$line"
        if printf '%s\n' "$line" | choose >/dev/null; then break; fi
      done) || exit 2
    printf '%s\n' "$lines" | choose ;;
  dockerhub)
    url="https://hub.docker.com/v2/repositories/$name/tags?page_size=100"
    lines=$(while [ -n "$url" ]; do
      json=$(fetch "$url") || exit 2
      printf '%s' "$json" | python3 -c '
import json, sys
for t in json.load(sys.stdin)["results"]:
    if t["tag_last_pushed"]:
        print(t["name"], t["tag_last_pushed"])' || exit 2
      url=$(printf '%s' "$json" | python3 -c 'import json, sys; print(json.load(sys.stdin)["next"] or "")') || exit 2
    done) || exit 2
    printf '%s\n' "$lines" | choose ;;
esac
