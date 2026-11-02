#!/usr/bin/env bash
# Ship library: issue and PR body text: fence-aware closing and section
# rewrites, title candidates, and the body-section checks. Sourced by ../_lib.sh,
# never run.

# ship_unfenced <text>: <text> with every fenced block taken out, opener and
# closer included, by SHIP_AWK_FENCE. What is left is the text a closing test
# reads: a "Closes #n" inside a fence is example text, not a claim.
ship_unfenced() { # ship_unfenced <text>
  awk "$SHIP_AWK_FENCE"'
    { was = fenced; fenced = ship_fence($0); if (!was && !fenced) print }' <<<"$1"
}

# Closing-keyword test, the same on both hosts: does <body> claim to close
# <issue>? Fenced blocks and inline code come out first (a PR quoting
# "Closes #n" while discussing another PR mentions the issue, it does not claim
# it).
#
# The fenced blocks come out by SHIP_AWK_FENCE, so this agrees with the two
# heading transformations on what a fence is: a tilde-fenced example carrying
# "Closes #n" reads as a mention here too, and host_pr_create, which asks this
# before it places a closing line, still places one on a body that only mentions
# the issue.
#
# Code spans come out after, and stay in jq, because a span opens mid-line where
# a line-oriented pass cannot see it. One rule for every run length: a span
# closes on a backtick run as long as the one that opened it, which the
# backreference says directly, and a multi-line ```md span is that rule at
# length three rather than a fence form of its own.
ship_body_closes() { # ship_body_closes <body> <issue> -> exit 0 when it does
  local unfenced
  unfenced=$(ship_unfenced "$1")
  jq -e -n --arg body "$unfenced" --arg n "$2" --arg re "$SHIP_CLOSES_RE" '
    $body
    | gsub("(?s)(`+).*?\\1"; "")
    | test($re + $n + "\\b"; "i")' >/dev/null
}

# ship_body_closing_line <text>: the first line of <text> that claims to close
# an issue, any issue, or nothing where no line does. The same vocabulary and
# the same fence and code-span rules as ship_body_closes, answering with the
# line itself: `ship_body_replace_preamble` carries the line over rather than
# rebuilding it, so it needs the line and not a yes or no.
#
# The spans come out of the whole text at once, exactly as ship_body_closes
# takes them out, so a multi-line span is inert for both. Each span leaves its
# own newlines behind, which keeps the stripped text line for line with the
# original: the test reads the stripped line and the answer is the original one,
# backticks and all. Stripping per line instead would read a line inside a
# multi-line span as a claim, and carrying that line over lifts a quoted
# `Closes #n` out of its span and makes it a real one.
ship_body_closing_line() { # ship_body_closing_line <text>
  local unfenced
  unfenced=$(ship_unfenced "$1")
  jq -rn --arg body "$unfenced" --arg re "$SHIP_CLOSES_RE" '
    ($body | split("\n")) as $lines
    | ($body | gsub("(?s)(?<b>`+)(?<c>.*?)\\1"; (.c | gsub("[^\n]"; "")))
       | split("\n")) as $bare
    | first(range($lines | length)
            | select($bare[.] | test($re + "[0-9]+\\b"; "i")))
    | $lines[.] // empty'
}

# Replace one `## <section>` of <body> with <body-file>'s content, appending the
# section when the body has none. Every other line is untouched, including a
# `Closes` line above the first heading. Prints the new body; exit 0 replaced,
# 1 created, the way ship_body_closes answers with its exit code.
#
# The heading match is anchored at column 0 and skips fenced blocks and
# `<details>` records by `ship_inert`, the rule _gh_add_closes reads too, so the
# two agree on what a section boundary is. A record in the section's old content
# is never deleted: it is carried through verbatim, in order, below the new
# content, each one followed by a blank line.
#
# It compares the line to `## <section>` LITERALLY rather
# than building an ERE around the name: `--section` takes any name, and a `.` or
# a `+` in one would otherwise match a heading nobody asked for, silently
# rewriting the wrong section of a PR body. Both sides of that comparison have
# their trailing blanks trimmed, the name included, so a `--section 'Review '`
# still matches `## Review` and the heading this appends is the one the next
# write will match.
#
# The name reaches awk through the environment rather than `-v`, which decodes
# backslash escapes in its value: a `--section 'Review\name'` arrived as two
# lines, so the real section was left alone and a mangled heading appended.
#
# The body file carries the section's CONTENT. A file that opens with the
# section's own heading, and the blank line under it, has both dropped rather
# than printed under the heading this writes: two `## <section>` lines make a
# body no mechanic can repair afterwards, because both of them match here and a
# later clean write puts the content under each. Any other opening line is
# content, `## <other>` included.
#
# A repeat met while the section just placed is still open is swallowed for the
# same reason, which is what repairs a body already carrying the duplicate. A
# `## <section>` after a DIFFERENT heading has closed that state is a section of
# its own and is replaced, so a malformed body with the section twice over still
# gets both.
#
# The strip trims a trailing CR before that comparison where the boundary match
# does not, on purpose: they read different inputs. A CRLF heading left in the
# file becomes a second `## <section>` in the body that the boundary match misses
# and rule 3 then treats as the section's end, which is a body no later
# write can repair. The boundary match reads the body the host returns and is the
# pre-existing rule `_gh_add_closes` agrees with.
ship_body_replace_section() { # ship_body_replace_section <body> <section> <body-file>
  SHIP_SECTION="$2" awk -v file="$3" "$SHIP_AWK_FENCE"'
    function trimmed(line, cr) {
      if (cr) sub(/[ \t\r]*$/, "", line); else sub(/[ \t]*$/, "", line)
      return line
    }
    function dump(   n, i, line, start, buf) {
      n = 0
      while ((getline line < file) > 0) { n++; buf[n] = line }
      close(file)
      start = 1
      if (n >= 1 && trimmed(buf[1], 1) == hd) {
        start = 2
        if (n >= 2 && buf[2] ~ /^[ \t\r]*$/) start = 3
      }
      for (i = start; i <= n; i++) print buf[i]
    }
    BEGIN { hd = trimmed("## " ENVIRON["SHIP_SECTION"], 1) }
    { inert = ship_inert($0) }
    !inert && trimmed($0, 0) == hd {
      if (skip) next
      print; print ""; dump(); print ""; skip=1; placed=1; next }
    skip && !inert && /^## / { skip=0 }
    skip && inert == 2 { print; if (!_record) print ""; next }
    !skip { print }
    END { if (!placed) { printf "\n%s\n\n", hd; dump(); exit 1 } }' <<<"$1"
}

# Replace the PREAMBLE of <body>, everything above its first `## ` heading, with
# <body-file>'s content, and print the new body. A body with no heading is all
# preamble. The preamble is where `open-pr` puts the closing line, and under
# this repo's standard it holds nothing else. Nothing under scripts/ could
# rewrite that half before #173, so an accepted body-shape finding in phase 7 was
# reported and left standing; this is what makes it a fix like any other.
#
# The boundary is the same column-0 `^## ` outside a fence or a `<details>`
# record that ship_body_replace_section and _gh_add_closes read, so the two
# halves of a body meet exactly and neither can reach into the other.
#
# Unlike a section, a preamble is always present: a body that opens on its first
# heading has an empty one, and the content is placed above that heading. So
# there is no created case and no exit-1 answer.
#
# A closing line the OLD preamble carried and the new content does not is
# carried over, last, where `open-pr` puts it. `open-pr` places it above the
# first heading precisely so no section rewrite reaches it; this rewrite does
# reach it, and a rewrite that says nothing about closing should not drop the
# link the PR was opened with.
#
# Content that carries a closing line of its own is left exactly as written,
# whichever issues that line names. The test is per line, not per issue number:
# the line is the caller saying what this PR closes, and merging the old line's
# references into it would put back an issue they had just taken out, which the
# caller cannot see in the file they wrote.
#
# The content reaches awk through the environment rather than a file, because
# the carried line is appended to it first, and rather than `-v`, which decodes
# backslash escapes in its value the way it did to a `--section` name.
ship_body_replace_preamble() { # ship_body_replace_preamble <body> <body-file>
  local content carried pre
  content=$(cat "$2")
  pre=$(awk "$SHIP_AWK_FENCE"'
    { inert = ship_inert($0) }
    !inert && /^## / { exit }
    { print }' <<<"$1")
  carried=$(ship_body_closing_line "$pre")
  if [ -n "$carried" ] && [ -z "$(ship_body_closing_line "$content")" ]; then
    if [ -n "$content" ]; then content=$(printf '%s\n\n%s' "$content" "$carried")
    else content=$carried
    fi
  fi
  SHIP_PREAMBLE="$content" awk "$SHIP_AWK_FENCE"'
    function dump(   n, i, a) {
      n = split(ENVIRON["SHIP_PREAMBLE"], a, "\n")
      # Trailing blanks come off, so the one blank line under the preamble is
      # placed here however the content file ended.
      while (n > 0 && a[n] ~ /^[ \t\r]*$/) n--
      for (i = 1; i <= n; i++) print a[i]
      return n
    }
    { inert = ship_inert($0) }
    !placed && !inert && /^## / { if (dump() > 0) print ""; placed = 1 }
    placed { print }
    END { if (!placed) dump() }' <<<"$1"
}

# Generic English function words of four or more characters; shorter ones the
# length rule already drops. No repo-specific word belongs here: the candidate
# check is generic, and an over-eager candidate costs one `--distinct-from`
# while a missed one costs a second issue for a find already filed.
readonly SHIP_TITLE_STOPWORDS='about also been both does each else from have here into just like made make more most much must only over same some such than that their them then there these they this those very were what when where which while will with would your'

# Titles that look like <title> among <open-issues>, for file-issue's candidate
# check. Compares lowercased tokens with punctuation as a separator, dropping
# tokens under four characters and the stopwords above; three or more shared
# tokens is a candidate. <exclude> is the JSON array of numbers the caller has
# read and judged different. Prints the candidates as compact JSON.
ship_title_candidates() { # ship_title_candidates <title> <open-issues-json> <exclude-json>
  jq -c --arg t "$1" --argjson x "$3" --arg s "$SHIP_TITLE_STOPWORDS" '
    def tokens: ascii_downcase | [splits("[^a-z0-9]+")]
      | map(select(length >= 4)) | unique | . - ($s | split(" "));
    ($t | tokens) as $new
    | [ .[]
        | select(([.number] - $x) != [])
        | select((($new - ($new - (.title | tokens))) | length) >= 3)
        | {number, title, url} ]' <<<"$2"
}

# ship_fence_unclosed <text>: does the text end inside a fenced block or a
# `<details>` record? Prints `line <n>: <run>` naming the opener still open, or
# nothing when both are balanced. `update-pr-body` asks before it rewrites a
# section: an open fence inverts the in-fence state for the rest of the body,
# so every `## ` heading after it reads as example text and the rewrite
# swallows the sections between them (run #121 lost four that way). An open
# record hides every heading after it the same way, so it is reported too,
# naming the outermost opener as `line <n>: <details>`. This is the one reader
# of `ship_inert`'s globals rather than its answer: it asks which of the two is
# still open at the end, which the per-line answer does not say.
ship_fence_unclosed() {
  awk "$SHIP_AWK_FENCE"'
    { was = _fenced; was_rec = _record; ship_inert($0)
      if (!was && _fenced) {
        open_line = NR; open_run = $0
        open_run = ship_deindent(open_run); sub(/[^`~].*$/, "", open_run)
      }
      if (!was_rec && _record) rec_line = NR }
    END {
      if (_fenced) printf "line %d: %s\n", open_line, open_run
      else if (_record) printf "line %d: <details>\n", rec_line }' <<<"$1"
}

# ship_body_headings <body>: the body's `## ` section headings, heading text
# only, one per line, in order. The same `ship_inert` rule as every
# transformation above, so a `## ` inside a fence is example text here too, and
# one inside a `<details>` record is not a section. `update-pr-body`
# reports this after the write, where a swallowed section is visible in the JSON
# rather than eight minutes later in a review.
ship_body_headings() {
  awk "$SHIP_AWK_FENCE"'
    { inert = ship_inert($0) }
    !inert && /^## / { sub(/^## /, ""); sub(/[ \t\r]+$/, ""); print }' <<<"$1"
}

# ship_template_missing <body>: the headings of the profile's `## PR`
# `Template:` file that <body> lacks, as a JSON array in template order, read by
# `ship_body_headings` on both sides, so a heading inside a fence or a
# `<details>` record is not present. `[]` where there is no profile or the
# Template: is `None.`. Returns 1, printing the path it could not read, when the
# profile or the Template: file (from the checkout root) cannot be read: no
# answer, not a body that lacks nothing. One rule for `open-pr`, which refuses
# on it, and `read-pr`, which reports it as `missing`.
ship_template_missing() { # <body>
  local profile template top tbody
  profile=$(ship_profile_path) && [ -f "$profile" ] || { echo '[]'; return 0; }
  template=$(awk '/^## /{f = ($0 ~ /^## PR[ \t\r]*$/)} f && /^Template:/{sub(/^Template:[ \t]*/, ""); sub(/[ \t\r]+$/, ""); print; exit}' "$profile" 2>/dev/null) \
    || { printf '%s\n' "$profile"; return 1; }
  case $template in ''|None|None.) echo '[]'; return 0 ;; esac
  if ! top=$(git rev-parse --show-toplevel) || ! tbody=$(cat "$top/$template" 2>/dev/null); then
    printf '%s\n' "$template"; return 1
  fi
  jq -cn --argjson w "$(ship_body_headings "$tbody" | jq -R . | jq -sc .)" \
    --argjson h "$(ship_body_headings "$1" | jq -R . | jq -sc .)" '$w - $h'
}

# ship_require_body_check <file>: run the repo's body checker
# (`ship_body_check_path`) on <file>, from the checkout root with the file as its
# one argument. One gate for `open-pr`, before the push, and `update-pr-body`,
# before the write, so the two cannot drift. No checker returns 0. The checker's
# exit 1 is a refusal (`ship_fail`, its stdout's last 40 lines); any other exit,
# an unreadable profile or no checkout is tooling (`ship_tooling`, its stderr's
# last 40 lines). A relative <file> is the caller's, so it is resolved first.
ship_require_body_check() { # <file>
  local check top file err out rc why
  check=$(ship_body_check_path) || ship_tooling "cannot read the ship profile for its Body-check: line"
  [ -n "$check" ] || return 0
  top=$(git rev-parse --show-toplevel) || ship_tooling "not inside a git checkout"
  case $1 in /*) file=$1 ;; *) file=$PWD/$1 ;; esac
  # Read and removed before the case: ship_fail and ship_tooling exit, and an
  # exit never fires the RETURN trap.
  err=$(mktemp) || ship_tooling "cannot create a temp file for the body check"
  trap 'rm -f "$err"; trap - RETURN' RETURN
  out=$(cd "$top" && "./$check" "$file" 2>"$err"); rc=$?
  why=$(tail -n 40 "$err"); rm -f "$err"
  case $rc in
    0) ;;
    1) ship_fail "PR body fails the repo's body check ($check): $(printf '%s\n' "$out" | tail -n 40)" ;;
    *) ship_tooling "body check $check failed (exit $rc): $why" ;;
  esac
}

# ship_paths_cited <text> <paths>: the paths, one per line, that the text cites.
# <paths> is newline-separated. A path is cited as a whole token, wherever it
# sits, a code span, a quote or a link included: it is not preceded by a path
# character ([A-Za-z0-9_./-], bar a leading `./`) and not followed by one
# ([A-Za-z0-9_/-], or a `.` that opens an extension). So `scripts/run` does not
# cite `scripts/run-file.sh`, `a/skills/x.sh` does not cite `skills/x.sh`, and a
# sentence's closing `.` or a `:12` line suffix does not hide a path. A grep that
# fails (exit 2, as against 1 for no match) is no answer: exit 2, never "uncited".
ship_paths_cited() { # <text> <paths>
  local p re rc
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    re=$(printf '%s' "$p" | sed 's/[]\\.[*^$+?(){}|]/\\&/g')
    re='(^|[^A-Za-z0-9_./-])(\./)?'$re'($|[^A-Za-z0-9_/.-]|\.($|[^A-Za-z0-9_]))'
    grep -Eq -e "$re" <<<"$1"; rc=$?
    case $rc in 0) printf '%s\n' "$p" ;; 1) ;; *) return 2 ;; esac
  done <<<"$2"
  return 0
}
