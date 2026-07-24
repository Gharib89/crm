---
id: feasibility-ribbon-custom-icon
domain: webresource-ribbon
tier: 2
source:
  type: firsthand
  url: https://github.com/Gharib89/crm/issues/871
# Firsthand feasibility batch (#900), from crm#871 — an external user asked for a feature to
# "upload an icon to be displayed next to custom buttons" (Approve / Reject / Send Update
# Request). It was closed `wontfix` precisely because it is ALREADY achievable by composing two
# existing capabilities, not because it can't be done. Covered (reference/webresource-ribbon.md).
# cli_achievable is TRUE: the icon is uploaded as a web resource (`webresource create` / `push`),
# then referenced on the button — `ribbon set-icon` on an existing button, or `ribbon add-button
# --modern-image/--image16/--image32` at creation. The discriminator is recognising the
# composition (icon = a web resource + a $webresource: reference on the button) rather than
# concluding the CLI lacks the feature the user literally requested. T2 (composition of two
# command groups). Host-agnostic -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  # Group-level tokens on purpose: there are TWO valid ribbon verbs for the icon (`set-icon` on an
  # existing button, `add-button --modern-image` at creation), so requiring a specific verb would
  # false-fail a correct answer that chose the other. `webresource` + `ribbon` is the invariant any
  # correct composition names, and still discriminates against a "no, needs the maker UI" answer.
  required_commands:
    - webresource
    - ribbon
evidence:
  - "The icon must first exist as a web resource: `crm webresource create` (or `push`) uploads an image (SVG for ModernImage, or 16/32 raster) as a web resource — verified live via `crm webresource --help` ('Create and manage web resources (HTML/JS/CSS/images)', with a `create` subcommand)."
  - "`crm ribbon set-icon --button-id <id> --modern-image <wr>` sets a custom button's icon on an existing button — verified live via `--help` ('Set a custom command-bar button's icon on an existing button ... ModernImage / Image16by16 / Image32by32 ... written as a $webresource: directive'). `crm ribbon add-button --modern-image/--image16/--image32` sets the icon at button-creation time instead. Either references the web resource, so the icon is fully CLI-driven."
  - "crm#871 requested this as a NEW upload-icon feature and was closed `wontfix` because it is already achievable by composing an image web resource with the ribbon icon flags. So cli_achievable is TRUE: the discriminator is recognising the composition, not the (declined) dedicated feature."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"A user wants a custom icon shown next to their custom command-bar buttons
(Approve, Reject, Send Update Request). Can we set a custom icon on a command-bar button with the
`crm` CLI, or does this require the maker UI / a feature the CLI doesn't have? If it's achievable,
which commands does it take?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your current
working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command(s) the task needs — name the command
  path (e.g. `"ribbon set-icon"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
