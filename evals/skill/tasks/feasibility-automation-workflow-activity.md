---
id: feasibility-automation-workflow-activity
domain: automation
tier: 2
source:
  type: firsthand
  url: https://github.com/Gharib89/crm/issues/866
# Firsthand feasibility batch (#900): the ~10-slot feasibility target is under-produced by
# community sources (pilot #885: 6/26), so it is topped up from this repo's own issues /
# DISCOVERED_BUGS (`source: firsthand`, a traceable secondary — taskspec.SOURCE_TYPES). This
# one encodes crm#866: registering a custom workflow activity (a compiled CodeActivity) so it
# is usable as a step in the classic workflow designer, WITHOUT the Plugin Registration Tool.
# Covered (reference/automation.md), not uncovered. cli_achievable is TRUE: `plugin
# register-assembly` uploads the .dll bytes and `plugin register-type` names the plugintype
# under it — and the plugintype's `name` is exactly what the classic designer shows as the
# Add-Step label (crm#866: a null name showed an empty, unusable label; fixed). T2, not T1:
# the discriminator is the two-command workflow (assembly + type) AND that a workflow activity
# needs NO register-step — steps are the plugin path; a workflow activity surfaces in the
# designer from its named plugintype alone, so an agent that reaches for register-step has
# taken the wrong branch. Host-agnostic (assembly + type registration work on cloud and
# on-prem v9.1) -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  # Bare, distinctive verb stems (not group-prefixed paths): evaluate_feasibility scores an
  # answer-key item as a substring of an emitted entry, so "register-assembly" matches whether
  # the agent writes `crm plugin register-assembly` or the bare verb — a group-prefixed needle
  # would false-fail the bare form.
  required_commands:
    - register-assembly
    - register-type
evidence:
  - "`crm plugin register-assembly <path.dll>` registers a plug-in assembly from a .dll file, uploading its bytes — verified live via `--help` ('Register a plug-in assembly from a .dll file (uploads its bytes)'). A custom workflow activity ships in exactly such an assembly, so this is step one."
  - "`crm plugin register-type --assembly <name> --type <FQN> --name <label>` registers the plugintype under that assembly — verified live via `--help`, which states 'Workflow activities use this [--name] as the classic-designer Add-Step label — a null name shows an empty, unusable label.' Naming the type is what makes the activity selectable in the classic workflow designer; this is the crm#866 fix (register-type previously left plugintypes.name null)."
  - "A workflow activity needs NO `plugin register-step`: steps (sdkmessageprocessingstep) bind PLUGINS to messages, whereas a workflow activity is picked up by the classic-workflow designer from its named plugintype. So the achievable path is register-assembly + register-type only — naming register-step is the wrong branch, which is why this grades as a workflow (T2) rather than a single-command lookup."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"We have a custom workflow activity — a C# CodeActivity compiled into an
assembly DLL — and we want it to appear as a selectable step in the classic (background) workflow
designer. Can we register the assembly and the activity type with the `crm` CLI, without using the
Plugin Registration Tool, and if so, which commands?"*

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
  path (e.g. `"plugin register-type"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
