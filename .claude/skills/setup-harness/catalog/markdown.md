# Markdown

## Signals
Kind: file kind
Names: None.
Paths: None.
Extensions: .md .markdown
Shebangs: None.

## lint

### markdownlint-cli2
Publisher: DavidAnson
Tier: 2: https://github.com/DavidAnson/markdownlint-cli2
Evidence: `.markdownlint-cli2.*`, `.markdownlint.*`
Rung: edit
Run: `markdownlint-cli2 --fix {files}`
Hook: local
Pin: package npm markdownlint-cli2
Route: `npm install -g markdownlint-cli2@{version}`; Blocked: None.
Constraints: needs Node 22 or later (the image's default). Beside Prettier, the run writes `.markdownlint.jsonc` at the repo root extending `markdownlint/style/prettier`, which turns off the rules Prettier's output breaks; a repo's own markdownlint config gains that `extends` instead. Every run that wires it, Prettier or not, also writes `.markdownlint-cli2.jsonc` at the repo root whose `ignores` holds `.github/pull_request_template.md` and `.azuredevops/pull_request_template.md`; a repo's own `.markdownlint-cli2.*` gains the two entries instead, keeping its settings, and a re-run that finds both writes nothing.
Traps: setup-skills lands a PR template whose first line is `Closes #` above its first `## ` heading, which MD041 fails (`allow_preamble` only moves the error to that heading), and setup-harness usually runs before the template exists, so the exclusion is unconditional. Without the Prettier pairing the two fight over line length, list indent and emphasis style, and MD013 alone fires on every line over 80 columns. markdownlint-cli2 reads `.markdownlint-cli2.*` before `.markdownlint.*`, takes `ignores` only from the former (holding even for a file passed by name, as the edit rung passes `{files}`), and ignores markdownlint-cli's `.markdownlintrc` and `.markdownlintignore`, so a repo on markdownlint-cli is a tool this entry does not list and keeps it.

## format

### Prettier
Publisher: Prettier
Tier: 2: https://github.com/prettier/prettier
Evidence: `.prettierrc*`, `prettier.config.*`, `prettier` dev dependency
Rung: edit
Run: `prettier --write {files}`
Hook: local
Pin: package npm prettier
Route: `npm install -g prettier@{version}`; Blocked: None.
Constraints: None.
Traps: `prettier/pre-commit` and `pre-commit/mirrors-prettier` are both archived, so Prettier runs as the `local` hook above.
