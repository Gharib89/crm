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
Constraints: needs Node 22 or later (the image's default). Beside Prettier, the run writes `.markdownlint.jsonc` at the repo root extending `markdownlint/style/prettier`, which turns off the rules Prettier's output breaks; a repo's own markdownlint config gains that `extends` instead.
Traps: without that pairing the two fight over line length, list indent and emphasis style, and MD013 alone fires on every line over 80 columns (62 findings on one 300-line glossary). It reads `.markdownlint-cli2.*` before `.markdownlint.*` and ignores markdownlint-cli's `.markdownlintrc` and `.markdownlintignore`, so a repo on markdownlint-cli is a tool this entry does not list and keeps it.

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
