#!/usr/bin/env bash
# The harness profile's grammar at Schema 3, checked: the `Schema:` line before
# the first `##`, the eight headings present and in order, and each heading's
# `Label:` lines present with a value in their vocabulary. Lines that are not a
# known label under their heading are the human's prose and are not read.
# setup-harness runs this on every profile it writes; harness-schema.md is the
# migration log it follows when a profile trails.
#
#   harness-profile-check.sh <profile>
#
# stdout: one line per violation, nothing when the profile is valid
# exit: 0 valid · 1 a violation · 2 tooling
set -uo pipefail
usage="usage: harness-profile-check.sh <profile>"
case ${1:-} in
  -h | --help) echo "$usage"; exit 0 ;;
  '') echo "$usage" >&2; exit 2 ;;
esac
[ -r "$1" ] || { echo "cannot read $1" >&2; exit 2; }
# The schema this checker reads, moved with the skill's metadata.harness-schema
# by the bump rule in harness-schema.md.
schema=3

awk -v reads="$schema" '
function bad(m) { print m; rc = 1 }
function oneof(label, v, ok, want) { if (!ok) bad("## " h ": " label ": want " want ", got " v) }
BEGIN {
  order = "Claude Code|Check entry point|Budgets|Cloud|Excluded|Roots|Local-only|Declined"
  need["Claude Code"] = "Floor"
  need["Check entry point"] = "Location"
  need["Budgets"] = "Edit|Turn|Commit|Full|Cloud setup"
  need["Cloud"] = "Verdict|Setup|Allowlist|Proof"
  need["Excluded"] = "Excluded"
  need["Roots"] = "Root"
  need["Local-only"] = "Local-only"
  need["Declined"] = "Declined"
  ph["Excluded"] = "<path prefix>"; ph["Roots"] = "<manifest>"; ph["Local-only"] = "<part>"; ph["Declined"] = "<proposal>"
}
/^## / {
  if (!schema) { bad("missing Schema: line before the first ## heading"); schema = "none" }
  h = substr($0, 4); seen = seen (seen == "" ? "" : "|") h; next
}
/^Schema:/ && h == "" {
  schema = $2
  if (schema != reads) { bad("Schema: " schema "; this checker reads Schema " reads) }
  next
}
h != "" {
  n = split(need[h], labels, "|")
  for (i = 1; i <= n; i++) {
    l = labels[i]
    if (index($0, l ": ") != 1 && $0 != l ":") continue
    v = substr($0, length(l) + 3); got[h, l] = 1
    if (h == "Claude Code") oneof(l, v, v ~ /^[0-9]+\.[0-9]+\.[0-9]+$/, "<major>.<minor>.<patch>")
    else if (h == "Budgets") oneof(l, v, v == "default" || v ~ /^override [0-9]+s: ./, "default or override <N>s: <reason>")
    else if (l == "Verdict") oneof(l, v, v == "cloud-first" || v ~ /^local-only: ./, "cloud-first or local-only: <reason>")
    else if (l == "Proof") oneof(l, v, v == "unproven" || (v ~ /^[0-9a-f]+$/ && length(v) >= 7 && length(v) <= 40), "<sha> or unproven")
    else if (h == "Excluded") oneof(l, v, v == "None." || v ~ /^[^:]+\/: ./, ph[h] ": <reason> or None., the prefix ending in /")
    else if (h in ph) oneof(l, v, v == "None." || v ~ /^[^:]+: ./, ph[h] ": <reason> or None.")
    else if (v == "") bad("## " h ": " l ": empty")
  }
}
END {
  if (!schema) bad("missing Schema: line before the first ## heading")
  if (seen != order) { gsub(/\|/, ", ", order); bad("headings out of order or missing: want " order) }
  else for (h in need) {
    n = split(need[h], labels, "|")
    for (i = 1; i <= n; i++) if (!got[h, labels[i]]) bad("## " h ": missing " labels[i] ":")
  }
  exit rc
}' "$1"
