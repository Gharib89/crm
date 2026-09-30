# Retired terms

Words and file names ship stopped using, one row each: the ship version that
retired it, the word, and the word that replaces it (`None.` where nothing
does). `update-skills` plans every row inside the version range a refresh
crosses, replaces the word in the consumer repo's own documents and renames a
file whose name is the word. A PR that retires a word adds its row here in the
same diff. A cell holds no `|`.

| Version | Term | Replacement |
|---|---|---|
| 0.9.0 | converged | reviewed |
| 0.9.0 | degraded | not reviewed |
| 0.9.0 | cap-hit | None. |
| 0.13.0 | CONTEXT.md | GLOSSARY.md |
| 0.13.0 | CONTEXT-MAP.md | GLOSSARY-MAP.md |
