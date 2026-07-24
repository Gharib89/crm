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
  # Concrete verbs, scoped to the stated scenario (existing custom buttons): `webresource create`
  # is the single-file upload verb for the icon (`push` walks a whole directory — not what a lone
  # icon needs), and `ribbon set-icon` is the verb for an EXISTING button (`add-button` CREATES a
  # button, a different operation). evaluate_feasibility recall has no OR-matching, so each needle
  # names the one verb this scenario requires; the create-time alternative (`add-button
  # --modern-image`) is documented in the evidence but is not the ask.
  required_commands:
    - webresource create
    - ribbon set-icon
evidence:
  - "The icon must first exist as a web resource: `crm webresource create` uploads a single image (SVG for ModernImage, or 16/32 raster) as a web resource — verified live via `crm webresource --help` (a `create` subcommand: 'Create a web resource'). (`webresource push` is the bulk directory-walk variant, not needed for one icon.)"
  - "`crm ribbon set-icon --button-id <id> --modern-image <wr>` sets a custom button's icon on an EXISTING button — verified live via `--help` ('Set a custom command-bar button's icon on an existing button ... ModernImage / Image16by16 / Image32by32 ... written as a $webresource: directive'). This is the verb for the stated scenario (the Approve/Reject/Send buttons already exist); `crm ribbon add-button --modern-image/--image16/--image32` is the alternative that sets an icon at button-creation time."
  - "crm#871 requested this as a NEW upload-icon feature and was closed `wontfix` because it is already achievable by composing an image web resource with the ribbon icon flags. So cli_achievable is TRUE: the discriminator is recognising the composition, not the (declined) dedicated feature."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"A user wants a custom icon shown next to their existing custom command-bar
buttons (Approve, Reject, Send Update Request). Can we set a custom icon on an existing command-bar
button with the `crm` CLI, or does this require the maker UI / a feature the CLI doesn't have? If
it's achievable, which commands does it take?"*

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
