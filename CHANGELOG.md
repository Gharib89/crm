# Changelog

All notable changes to `crm` are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases from 0.12.0 on are cut automatically by python-semantic-release from
Conventional Commit messages; new version sections are inserted below this line.

<!-- version list -->

## v1.35.0 (2026-06-25)

### Features

- **apply**: Opt-in --prune to delete solution components absent from the spec
  ([#553](https://github.com/Gharib89/crm/pull/553),
  [`5c8670b`](https://github.com/Gharib89/crm/commit/5c8670bfdf397101d4f1279b1eb4178a7ad08509))


## v1.34.0 (2026-06-25)

### Documentation

- Rebrand docs site and README to the gradient CRM logo
  ([#560](https://github.com/Gharib89/crm/pull/560),
  [`cf6cb75`](https://github.com/Gharib89/crm/commit/cf6cb757c0113a5b70e2433fbfe9eb549067caf8))

### Features

- **apply**: Declare plug-ins (assembly/types/steps/images) in the spec
  ([#561](https://github.com/Gharib89/crm/pull/561),
  [`743d7de`](https://github.com/Gharib89/crm/commit/743d7de4ebd878d6b76f541f675e4ca511d2cee7))


## v1.33.0 (2026-06-25)

### Bug Fixes

- **apply**: Fail-fast spec validation + accurate publish/convergence docs (review round 2)
  ([#559](https://github.com/Gharib89/crm/pull/559),
  [`8025d9e`](https://github.com/Gharib89/crm/commit/8025d9eeaee4eefe20e67c8e82a92aeffa6dbad9))

- **apply**: Harden spec validation and dry-run contract (review round 1)
  ([#559](https://github.com/Gharib89/crm/pull/559),
  [`8025d9e`](https://github.com/Gharib89/crm/commit/8025d9eeaee4eefe20e67c8e82a92aeffa6dbad9))

### Documentation

- **apply**: Document web resource & security role kinds; review fixes
  ([#559](https://github.com/Gharib89/crm/pull/559),
  [`8025d9e`](https://github.com/Gharib89/crm/commit/8025d9eeaee4eefe20e67c8e82a92aeffa6dbad9))

### Features

- **apply**: Declare web resources and security roles in the spec
  ([#559](https://github.com/Gharib89/crm/pull/559),
  [`8025d9e`](https://github.com/Gharib89/crm/commit/8025d9eeaee4eefe20e67c8e82a92aeffa6dbad9))


## v1.32.0 (2026-06-25)

### Features

- **apply**: --dry-run emits a full drift report
  ([`b1fe805`](https://github.com/Gharib89/crm/commit/b1fe805abdcf3746b56bc8c70f86ca4f6373c5c4))


## v1.31.0 (2026-06-25)

### Chores

- Relicense under PolyForm Noncommercial 1.0.0
  ([`5963212`](https://github.com/Gharib89/crm/commit/5963212a36528ad79600eb87a72ed3bebe82ba6f))

### Documentation

- **adr**: Record 0004 reconsideration — codegen stays external
  ([`e2c17f8`](https://github.com/Gharib89/crm/commit/e2c17f8ce6862e4650f212b0adc934e25f2399f2))

### Features

- **apply**: Convergent desired-state reconciliation for entity/attribute/optionset
  ([#556](https://github.com/Gharib89/crm/pull/556),
  [`0f74931`](https://github.com/Gharib89/crm/commit/0f749319b2118da845a08079beb84d99fb60ec7c))


## v1.30.1 (2026-06-24)

### Bug Fixes

- Note .NET SDK prereq in pac install hint
  ([`1de97dd`](https://github.com/Gharib89/crm/commit/1de97dd91be498054112417b3276e620af272486))

### Refactoring

- De-duplicate four byte-identical helpers (#543) ([#546](https://github.com/Gharib89/crm/pull/546),
  [`4f1a1a3`](https://github.com/Gharib89/crm/commit/4f1a1a34cc089eb637cbdb1dcd5042cf043d55c8))

- Remove dead TypedDicts, REPL skin cruft, metadata leftovers
  ([#545](https://github.com/Gharib89/crm/pull/545),
  [`f88f0be`](https://github.com/Gharib89/crm/commit/f88f0be2b1179c2941ccc1c69a75f0997fd8167f))


## v1.30.0 (2026-06-24)

### Documentation

- Add ADR 0013 + Provenance wall term for on-prem workflow xaml editing
  ([`73189b5`](https://github.com/Gharib89/crm/commit/73189b58ce944a16d876dba827fafda0efd4dc41))

- Add workflow create/update feasibility spec
  ([`bb63a73`](https://github.com/Gharib89/crm/commit/bb63a7351c66b6663d2997acba4ea5cee1352e9c))

- Enrich workflow feasibility spec with deep-research findings
  ([`0153878`](https://github.com/Gharib89/crm/commit/01538781f0c631963c33cb648185c7aa55598398))

- **context**: Add Failure enrichment term to the CLI contract
  ([`218a5fd`](https://github.com/Gharib89/crm/commit/218a5fd21c09b524c96604e1f86096f1a988a798))

### Features

- Add `workflow update` for editing workflow metadata on both targets
  ([#542](https://github.com/Gharib89/crm/pull/542),
  [`ca9f371`](https://github.com/Gharib89/crm/commit/ca9f37146613bea9dc3f92976575689d035abd48))

### Refactoring

- Deepen d365_errors with an additive enrich(exc) callback
  ([#533](https://github.com/Gharib89/crm/pull/533),
  [`6d575b3`](https://github.com/Gharib89/crm/commit/6d575b35d1f137628490d82457e83c36d9d6414e))

### Testing

- Add live e2e coverage for workflow clone/import/delete
  ([#534](https://github.com/Gharib89/crm/pull/534),
  [`39c16e9`](https://github.com/Gharib89/crm/commit/39c16e960682311a1e78fb7a70088ddfba7c6bf7))

- Add offline gate validating doc CLI examples against describe
  ([#532](https://github.com/Gharib89/crm/pull/532),
  [`b87f2d7`](https://github.com/Gharib89/crm/commit/b87f2d7d981335843c848abf1c5122cf96eb7dfd))


## v1.29.0 (2026-06-23)

### Documentation

- Fix e2e worktree-CLI note — cwd beats PYTHONPATH, not the editable finder
  ([`329ae1e`](https://github.com/Gharib89/crm/commit/329ae1ec9f11e021392944e39e4d2e38838ccaad))

### Features

- **plugin**: Add register-type verb; correct auto-created-plugintype docstrings
  ([`e94956e`](https://github.com/Gharib89/crm/commit/e94956e9bba089bcc5eca69d60e6bf514b740679))

### Testing

- **e2e**: Provision pac in CI + live offline solution pack/extract roundtrip
  ([#530](https://github.com/Gharib89/crm/pull/530),
  [`2867320`](https://github.com/Gharib89/crm/commit/28673201d90d9555330c6590ba8a30e2e15347e0))


## v1.28.0 (2026-06-23)

### Documentation

- Fix non-working CLI examples in quickstart/index/agent
  ([`41ba0ba`](https://github.com/Gharib89/crm/commit/41ba0ba2087645ea6be33ccbef6439e248e26941))

### Features

- **solution**: Migrate extract/pack to cross-platform `pac solution`
  ([#527](https://github.com/Gharib89/crm/pull/527),
  [`1c6c508`](https://github.com/Gharib89/crm/commit/1c6c508ceb133b004f7775e0f123035e0dba546f))


## v1.27.6 (2026-06-23)

### Bug Fixes

- Bump-guard messages reflect title-or-body breaking change
  ([#526](https://github.com/Gharib89/crm/pull/526),
  [`fc3ec66`](https://github.com/Gharib89/crm/commit/fc3ec66b444647eda9baf0ff0604bcb41389737c))

- Stop bump-guard stalling agent feat PRs; only major is label-gated
  ([#526](https://github.com/Gharib89/crm/pull/526),
  [`fc3ec66`](https://github.com/Gharib89/crm/commit/fc3ec66b444647eda9baf0ff0604bcb41389737c))

### Testing

- **e2e**: Cover solution stage-and-upgrade + apply-upgrade (single-org managed recipe)
  ([#525](https://github.com/Gharib89/crm/pull/525),
  [`8cc88db`](https://github.com/Gharib89/crm/commit/8cc88dbaf588f541534bb898d5d2caabf7f07b02))


## v1.27.5 (2026-06-22)

### Bug Fixes

- Sla create writes numeric ObjectTypeCode to objecttypecode
  ([#524](https://github.com/Gharib89/crm/pull/524),
  [`0605420`](https://github.com/Gharib89/crm/commit/06054207f9eeee9f6c3c44662a31bafd802ada9e))

### Testing

- **e2e**: Assert persisted cancreate is not allowed
  ([#522](https://github.com/Gharib89/crm/pull/522),
  [`bb6a42f`](https://github.com/Gharib89/crm/commit/bb6a42f0a5d973ad6f1c096f995cb65204770e34))

- **e2e**: Cover fieldsec add-permission on a secured custom column
  ([#522](https://github.com/Gharib89/crm/pull/522),
  [`bb6a42f`](https://github.com/Gharib89/crm/commit/bb6a42f0a5d973ad6f1c096f995cb65204770e34))

- **e2e**: Cover workflow run dispatch-only vs seeded on-demand workflow
  ([`1cde24b`](https://github.com/Gharib89/crm/commit/1cde24b6a8406c965a86727681f8cf0a6450cf0b))


## v1.27.4 (2026-06-22)

### Bug Fixes

- Workflow run surfaces async_operation_id from asyncoperation entity
  ([#521](https://github.com/Gharib89/crm/pull/521),
  [`2383f1c`](https://github.com/Gharib89/crm/commit/2383f1c0fb1272c1090511c0dd887f8f54b30315))

### Continuous Integration

- **e2e**: Add .NET SDK step + document dedicated CS trial as cloud e2e target (ADR 0012)
  ([#515](https://github.com/Gharib89/crm/pull/515),
  [`26e4505`](https://github.com/Gharib89/crm/commit/26e4505c1b065e314952d08fbec9e81a56363abb))

### Documentation

- Clarify CS-trial profile wording + workflow provisioning step
  ([#515](https://github.com/Gharib89/crm/pull/515),
  [`26e4505`](https://github.com/Gharib89/crm/commit/26e4505c1b065e314952d08fbec9e81a56363abb))

- Record 2026-06-22 e2e live run (on-prem + cloud) in TEST.md
  ([`d08407f`](https://github.com/Gharib89/crm/commit/d08407ffbbfaa336e54703ee23f4cfa356be3039))

- **adr**: Dedicated CS Dataverse sandbox as the cloud e2e target
  ([`4b42fae`](https://github.com/Gharib89/crm/commit/4b42faecba5b9c9a445f05dcca84e12e9dd746ab))

### Testing

- **e2e**: Address review on theme publish test ([#520](https://github.com/Gharib89/crm/pull/520),
  [`6433923`](https://github.com/Gharib89/crm/commit/6433923a32c8b755beb127cbc1241df3f228889e))

- **e2e**: Assert seed filter matches zero rows before submit
  ([#513](https://github.com/Gharib89/crm/pull/513),
  [`791f30e`](https://github.com/Gharib89/crm/commit/791f30eb6c86902882a662cb62fab82bf94c3fb3))

- **e2e**: Correct the 5 keep-skipped E2E_SKIP reasons + refresh TEST.md
  ([#516](https://github.com/Gharib89/crm/pull/516),
  [`e58728f`](https://github.com/Gharib89/crm/commit/e58728f842af655d9997704021152bbe5f9ee963))

- **e2e**: Cover async cancel + solution job-cancel via future-dated BulkDelete
  ([#513](https://github.com/Gharib89/crm/pull/513),
  [`791f30e`](https://github.com/Gharib89/crm/commit/791f30eb6c86902882a662cb62fab82bf94c3fb3))

- **e2e**: Cover audit detail by generating an audit row inline
  ([#519](https://github.com/Gharib89/crm/pull/519),
  [`e99ed3a`](https://github.com/Gharib89/crm/commit/e99ed3a034787fefaac76a217bbc1195eaa2d846))

- **e2e**: Cover plugin assembly register/unregister lifecycle (build signed .dll from C# source)
  ([`41b4349`](https://github.com/Gharib89/crm/commit/41b434958af7e4a0f2d4fe6ba0f8cbe8b4e4bb8b))

- **e2e**: Cover theme publish (capture -> publish throwaway -> restore)
  ([#520](https://github.com/Gharib89/crm/pull/520),
  [`6433923`](https://github.com/Gharib89/crm/commit/6433923a32c8b755beb127cbc1241df3f228889e))

- **e2e**: Poll audits table before skipping audit-detail test
  ([#519](https://github.com/Gharib89/crm/pull/519),
  [`e99ed3a`](https://github.com/Gharib89/crm/commit/e99ed3a034787fefaac76a217bbc1195eaa2d846))


## v1.27.3 (2026-06-22)

### Bug Fixes

- Skip uncreatable lookup companion columns in clone-entity spec
  ([#501](https://github.com/Gharib89/crm/pull/501),
  [`c4fa0c2`](https://github.com/Gharib89/crm/commit/c4fa0c2a2d5b9108635a58f03c59ff78dd0ca968))


## v1.27.2 (2026-06-22)

### Bug Fixes

- App create --if-exists skip swallows on-prem duplicate fault (0x80040216/500)
  ([#499](https://github.com/Gharib89/crm/pull/499),
  [`50b9b3a`](https://github.com/Gharib89/crm/commit/50b9b3a10dfa36d1ebbaaa191eb25e41653eb21f))


## v1.27.1 (2026-06-22)

### Bug Fixes

- Sitemap add-subarea --pass-params + validate --dashboard exists
  ([`a38f1c0`](https://github.com/Gharib89/crm/commit/a38f1c01312255bc09011f772432cf006d2a82b5))


## v1.27.0 (2026-06-22)

### Features

- Sitemap set-title / set-description (localized titles/descriptions)
  ([#492](https://github.com/Gharib89/crm/pull/492),
  [`563dcce`](https://github.com/Gharib89/crm/commit/563dcce5a549f32b1eda62c9b161b097001bf476))


## v1.26.0 (2026-06-22)

### Bug Fixes

- **view**: Cascade-prune emptied parent filters on remove-filter
  ([#493](https://github.com/Gharib89/crm/pull/493),
  [`c9a1295`](https://github.com/Gharib89/crm/commit/c9a129551350b4fd27146eb1244950b101dc041d))

- **view**: Harden remove-filter multi-condition path
  ([#493](https://github.com/Gharib89/crm/pull/493),
  [`c9a1295`](https://github.com/Gharib89/crm/commit/c9a129551350b4fd27146eb1244950b101dc041d))

### Features

- **view**: Add add-filter / remove-filter for FetchXML conditions
  ([#493](https://github.com/Gharib89/crm/pull/493),
  [`c9a1295`](https://github.com/Gharib89/crm/commit/c9a129551350b4fd27146eb1244950b101dc041d))


## v1.25.0 (2026-06-21)

### Documentation

- Document sitemap move-node (reorder) ([#491](https://github.com/Gharib89/crm/pull/491),
  [`408d7ab`](https://github.com/Gharib89/crm/commit/408d7ab3ef6e5b0afb1bec18b8a8ba01455bb318))

### Features

- Add sitemap move-node to reorder a navigation node
  ([#491](https://github.com/Gharib89/crm/pull/491),
  [`408d7ab`](https://github.com/Gharib89/crm/commit/408d7ab3ef6e5b0afb1bec18b8a8ba01455bb318))

- Add sitemap move-node to reorder a navigation node (B12)
  ([#491](https://github.com/Gharib89/crm/pull/491),
  [`408d7ab`](https://github.com/Gharib89/crm/commit/408d7ab3ef6e5b0afb1bec18b8a8ba01455bb318))

### Refactoring

- Tighten move-node same-type sibling semantics and test typing
  ([#491](https://github.com/Gharib89/crm/pull/491),
  [`408d7ab`](https://github.com/Gharib89/crm/commit/408d7ab3ef6e5b0afb1bec18b8a8ba01455bb318))


## v1.24.0 (2026-06-21)

### Bug Fixes

- Harden dashboard web-resource warning and correct skill JSON contract
  ([#490](https://github.com/Gharib89/crm/pull/490),
  [`f5b6d66`](https://github.com/Gharib89/crm/commit/f5b6d664c8d5869e9de99bc134634df31a60fe8e))

- Reject blank --url / --webresource in the command layer
  ([#490](https://github.com/Gharib89/crm/pull/490),
  [`f5b6d66`](https://github.com/Gharib89/crm/commit/f5b6d664c8d5869e9de99bc134634df31a60fe8e))

- Validate remove-component selectors in the command layer
  ([#490](https://github.com/Gharib89/crm/pull/490),
  [`f5b6d66`](https://github.com/Gharib89/crm/commit/f5b6d664c8d5869e9de99bc134634df31a60fe8e))

### Documentation

- Document dashboard add-iframe / add-webresource / remove-component
  ([#490](https://github.com/Gharib89/crm/pull/490),
  [`f5b6d66`](https://github.com/Gharib89/crm/commit/f5b6d664c8d5869e9de99bc134634df31a60fe8e))

### Features

- Add dashboard add-iframe / add-webresource / remove-component
  ([#490](https://github.com/Gharib89/crm/pull/490),
  [`f5b6d66`](https://github.com/Gharib89/crm/commit/f5b6d664c8d5869e9de99bc134634df31a60fe8e))

### Testing

- Use _rowspan helper to satisfy pyright on rowspan assertion
  ([#490](https://github.com/Gharib89/crm/pull/490),
  [`f5b6d66`](https://github.com/Gharib89/crm/commit/f5b6d664c8d5869e9de99bc134634df31a60fe8e))


## v1.23.0 (2026-06-21)

### Bug Fixes

- Address Copilot review on view editors ([#489](https://github.com/Gharib89/crm/pull/489),
  [`c65e815`](https://github.com/Gharib89/crm/commit/c65e8156566d1e23826ce11524d115cbc5fa6fb6))

- Insert new view column attribute before order/filter in fetchxml
  ([#489](https://github.com/Gharib89/crm/pull/489),
  [`c65e815`](https://github.com/Gharib89/crm/commit/c65e8156566d1e23826ce11524d115cbc5fa6fb6))

### Documentation

- Document view edit-columns / set-order (B7) ([#489](https://github.com/Gharib89/crm/pull/489),
  [`c65e815`](https://github.com/Gharib89/crm/commit/c65e8156566d1e23826ce11524d115cbc5fa6fb6))

### Features

- View edit-columns / set-order editors (B7) ([#489](https://github.com/Gharib89/crm/pull/489),
  [`c65e815`](https://github.com/Gharib89/crm/commit/c65e8156566d1e23826ce11524d115cbc5fa6fb6))

### Refactoring

- Hoist attribute_info_or_raise to metadata, reuse in views
  ([#489](https://github.com/Gharib89/crm/pull/489),
  [`c65e815`](https://github.com/Gharib89/crm/commit/c65e8156566d1e23826ce11524d115cbc5fa6fb6))

### Testing

- Fix pyright return-type on e2e _cells helper ([#489](https://github.com/Gharib89/crm/pull/489),
  [`c65e815`](https://github.com/Gharib89/crm/commit/c65e8156566d1e23826ce11524d115cbc5fa6fb6))


## v1.22.0 (2026-06-21)

### Bug Fixes

- Address review — usage-error for exclusive flags, cleaner JSON keys
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))

- Uniform global node-id uniqueness + parent-aware sitemap T3
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))

- Warn against chaining --no-publish sitemap edits; strip subarea flags
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))

### Documentation

- Sync sitemap live-edit verbs (README, how-to, skill) + bundle spec
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))

### Features

- Add sitemap live-edit verbs (add-area/group/subarea, remove-node)
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))

- Sitemap live-edit verbs (add-area/group/subarea, remove-node) (B6)
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))

### Refactoring

- Unquote annotations + cover comment-out T3 in sitemap e2e
  ([#486](https://github.com/Gharib89/crm/pull/486),
  [`f6d6fe2`](https://github.com/Gharib89/crm/commit/f6d6fe234b97fbc917372839ad599a5102948d44))


## v1.21.0 (2026-06-21)

### Documentation

- Clarify set-label solution requirement; e2e selects provisioned LCID
  ([#484](https://github.com/Gharib89/crm/pull/484),
  [`267129c`](https://github.com/Gharib89/crm/commit/267129c3056e6854600e316f612a891903c11558))

- Sync set-label docs + strengthen e2e LocLabel/tooltip assertions
  ([#484](https://github.com/Gharib89/crm/pull/484),
  [`267129c`](https://github.com/Gharib89/crm/commit/267129c3056e6854600e316f612a891903c11558))

### Features

- Add `ribbon set-label` to set custom-button labels/tooltips
  ([#484](https://github.com/Gharib89/crm/pull/484),
  [`267129c`](https://github.com/Gharib89/crm/commit/267129c3056e6854600e316f612a891903c11558))

- Add `ribbon set-label` to set custom-button labels/tooltips (B11)
  ([#484](https://github.com/Gharib89/crm/pull/484),
  [`267129c`](https://github.com/Gharib89/crm/commit/267129c3056e6854600e316f612a891903c11558))

### Testing

- Tighten set-label --lcid e2e assertion to resolved label or directive
  ([#484](https://github.com/Gharib89/crm/pull/484),
  [`267129c`](https://github.com/Gharib89/crm/commit/267129c3056e6854600e316f612a891903c11558))


## v1.20.1 (2026-06-21)

### Bug Fixes

- Add --solution to security create-role ([#485](https://github.com/Gharib89/crm/pull/485),
  [`fe292b1`](https://github.com/Gharib89/crm/commit/fe292b13bf598f1ea0017eb52ab700c152e881be))

- Document if-exists-skip rationale; pyright-clean entity test
  ([#485](https://github.com/Gharib89/crm/pull/485),
  [`fe292b1`](https://github.com/Gharib89/crm/commit/fe292b13bf598f1ea0017eb52ab700c152e881be))

### Documentation

- Document create-role --solution and if-exists-skip nuance
  ([#485](https://github.com/Gharib89/crm/pull/485),
  [`fe292b1`](https://github.com/Gharib89/crm/commit/fe292b13bf598f1ea0017eb52ab700c152e881be))

### Testing

- E2e for create-role --solution membership (cloud-verified)
  ([#485](https://github.com/Gharib89/crm/pull/485),
  [`fe292b1`](https://github.com/Gharib89/crm/commit/fe292b13bf598f1ea0017eb52ab700c152e881be))


## v1.20.0 (2026-06-21)

### Features

- Author custom security roles — `security create-role` + `security set-role-privileges`
  ([`3018c65`](https://github.com/Gharib89/crm/commit/3018c65e1cc849e02b16e12b602bab283f241bf1))


## v1.19.0 (2026-06-21)

### Bug Fixes

- Harden ribbon validate_rule_ids against invalid kind; clarify docs
  ([#481](https://github.com/Gharib89/crm/pull/481),
  [`6934f34`](https://github.com/Gharib89/crm/commit/6934f343f01a998213f78637b7e00b33bcf5c196))

- Reject mis-cased Mscrm.* ribbon rule ids (case-insensitive prefix guard)
  ([#481](https://github.com/Gharib89/crm/pull/481),
  [`6934f34`](https://github.com/Gharib89/crm/commit/6934f343f01a998213f78637b7e00b33bcf5c196))

### Documentation

- Ribbon set-rules / add-custom-rule (how-to, skill, README)
  ([#481](https://github.com/Gharib89/crm/pull/481),
  [`6934f34`](https://github.com/Gharib89/crm/commit/6934f343f01a998213f78637b7e00b33bcf5c196))

### Features

- Ribbon set-rules / add-custom-rule (enable/display rules)
  ([#481](https://github.com/Gharib89/crm/pull/481),
  [`6934f34`](https://github.com/Gharib89/crm/commit/6934f343f01a998213f78637b7e00b33bcf5c196))

### Testing

- Assert ElementTree.find results non-None in ribbon rule tests (pyright)
  ([#481](https://github.com/Gharib89/crm/pull/481),
  [`6934f34`](https://github.com/Gharib89/crm/commit/6934f343f01a998213f78637b7e00b33bcf5c196))

- Assert ribbon rule set by parsed value in e2e T3 (no drop/reorder)
  ([#481](https://github.com/Gharib89/crm/pull/481),
  [`6934f34`](https://github.com/Gharib89/crm/commit/6934f343f01a998213f78637b7e00b33bcf5c196))


## v1.18.0 (2026-06-21)

### Documentation

- Clarify hide-action confirmation UX (interactive prompt, --yes skips)
  ([#479](https://github.com/Gharib89/crm/pull/479),
  [`74aab32`](https://github.com/Gharib89/crm/commit/74aab32d809b4e404bcf5c4f7f527c3d19e2809e))

### Features

- Add `ribbon hide-button` to hide OOB command-bar buttons
  ([#479](https://github.com/Gharib89/crm/pull/479),
  [`74aab32`](https://github.com/Gharib89/crm/commit/74aab32d809b4e404bcf5c4f7f527c3d19e2809e))

### Testing

- Satisfy pyright basic-mode optional/attribute checks in ribbon tests
  ([#479](https://github.com/Gharib89/crm/pull/479),
  [`74aab32`](https://github.com/Gharib89/crm/commit/74aab32d809b4e404bcf5c4f7f527c3d19e2809e))


## v1.17.0 (2026-06-21)

### Features

- Chart editors — update/set-fetch/add-series/remove-series/set-groupby (B5)
  ([#478](https://github.com/Gharib89/crm/pull/478),
  [`16739c4`](https://github.com/Gharib89/crm/commit/16739c43cb6734aa1ab65d19421cb8ec8852a00d))


## v1.16.0 (2026-06-21)

### Bug Fixes

- Harden dashboard tile add per review (guard, occupied section, input ranges)
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))

- Top-align dashboard tile placement; clarify --section is empty-only
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))

### Documentation

- Explain why dashboard tile commit skips the in-process T3 read-back
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))

- Sync dashboard add-chart / add-view tile editors
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))

### Features

- Add dashboard add-chart / add-view (ChartGrid tiles)
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))

### Testing

- Satisfy pyright on dashboard tile tests (assert Element non-None)
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))

- Use valid placeholder GUIDs for existing dashboard cells
  ([#477](https://github.com/Gharib89/crm/pull/477),
  [`ba23f43`](https://github.com/Gharib89/crm/commit/ba23f43ef0e3da820ee34d55a6077e5b51e7eb52))


## v1.15.0 (2026-06-21)

### Features

- Add form set-field-props editor for field presentation properties
  ([#476](https://github.com/Gharib89/crm/pull/476),
  [`30bc45f`](https://github.com/Gharib89/crm/commit/30bc45f4cd5b817afdf8469d90a6525709f81c67))


## v1.14.0 (2026-06-21)

### Documentation

- Clarify sibling-guard message (PATCH not POST) and dry-run comment
  ([#475](https://github.com/Gharib89/crm/pull/475),
  [`64a1ae4`](https://github.com/Gharib89/crm/commit/64a1ae46df9f9ccc72101b1c68dd9dcf37b46545))

### Features

- Add form tab & section structure editors (B8) ([#475](https://github.com/Gharib89/crm/pull/475),
  [`64a1ae4`](https://github.com/Gharib89/crm/commit/64a1ae46df9f9ccc72101b1c68dd9dcf37b46545))

### Testing

- Add offline command-layer + dry-run coverage for form tab/section verbs
  ([#475](https://github.com/Gharib89/crm/pull/475),
  [`64a1ae4`](https://github.com/Gharib89/crm/commit/64a1ae46df9f9ccc72101b1c68dd9dcf37b46545))


## v1.13.0 (2026-06-21)

### Documentation

- Clarify assert_external_guids_intact compares a multiset
  ([#473](https://github.com/Gharib89/crm/pull/473),
  [`6c1357e`](https://github.com/Gharib89/crm/commit/6c1357ec2d583513c6f5ae4ba176d8c957096f38))

- **research**: Safe customization-XML editors feasibility study
  ([`e46f05e`](https://github.com/Gharib89/crm/commit/e46f05ec07deae3bca9541b789b8fa1b40b3dc49))

- **triage**: Document kind label axis (refactor, chore)
  ([`f05c757`](https://github.com/Gharib89/crm/commit/f05c757fc1b0da4299b3412959f8a6957ed7cb95))

- **triage**: Record SOAP-only messages as out-of-scope
  ([#453](https://github.com/Gharib89/crm/pull/453),
  [`f5e3928`](https://github.com/Gharib89/crm/commit/f5e3928340aed732abb2db35472dbdd52e6af084))

### Features

- Add form JS event/handler & library wiring verbs
  ([#474](https://github.com/Gharib89/crm/pull/474),
  [`a3cfdef`](https://github.com/Gharib89/crm/commit/a3cfdefff16651b7f471ef5f93448123fb340a01))

### Refactoring

- Enforce publish-before-read-back in commit_xml_patch
  ([#473](https://github.com/Gharib89/crm/pull/473),
  [`6c1357e`](https://github.com/Gharib89/crm/commit/6c1357ec2d583513c6f5ae4ba176d8c957096f38))

- Extract shared xml_edit primitives + generalized direct-PATCH commit
  ([#473](https://github.com/Gharib89/crm/pull/473),
  [`6c1357e`](https://github.com/Gharib89/crm/commit/6c1357ec2d583513c6f5ae4ba176d8c957096f38))

- Extract shared xml_edit primitives and generalize the direct-PATCH commit
  ([#473](https://github.com/Gharib89/crm/pull/473),
  [`6c1357e`](https://github.com/Gharib89/crm/commit/6c1357ec2d583513c6f5ae4ba176d8c957096f38))


## v1.12.0 (2026-06-20)

### Features

- **metadata**: Hierarchical relationships, virtual entities, can-relate
  ([#454](https://github.com/Gharib89/crm/pull/454),
  [`99e2815`](https://github.com/Gharib89/crm/commit/99e28151bd654528219014cf825698b0930eab04))


## v1.11.0 (2026-06-20)

### Bug Fixes

- Use UsageError + guard file I/O in discovery read commands
  ([#456](https://github.com/Gharib89/crm/pull/456),
  [`66380fb`](https://github.com/Gharib89/crm/commit/66380fb2a28151c8dba9eb8370dc442272330365))

### Chores

- Re-trigger CI (missed synchronize dispatch) ([#456](https://github.com/Gharib89/crm/pull/456),
  [`66380fb`](https://github.com/Gharib89/crm/commit/66380fb2a28151c8dba9eb8370dc442272330365))

### Documentation

- Sync README, how-to, skill, e2e for discovery reads
  ([#456](https://github.com/Gharib89/crm/pull/456),
  [`66380fb`](https://github.com/Gharib89/crm/commit/66380fb2a28151c8dba9eb8370dc442272330365))

### Features

- Add solution/metadata/ribbon discovery reads ([#456](https://github.com/Gharib89/crm/pull/456),
  [`66380fb`](https://github.com/Gharib89/crm/commit/66380fb2a28151c8dba9eb8370dc442272330365))

### Refactoring

- Address self-review for discovery reads ([#456](https://github.com/Gharib89/crm/pull/456),
  [`66380fb`](https://github.com/Gharib89/crm/commit/66380fb2a28151c8dba9eb8370dc442272330365))


## v1.10.0 (2026-06-20)

### Features

- **metadata**: Add 'metadata changes --since' for incremental metadata sync
  ([#455](https://github.com/Gharib89/crm/pull/455),
  [`cadc910`](https://github.com/Gharib89/crm/commit/cadc910b3b26e67b65e258550e5f0a7b6bd27082))


## v1.8.1 (2026-06-20)

### Bug Fixes

- **solution**: Add help string to 'solution info' command
  ([`468de62`](https://github.com/Gharib89/crm/commit/468de627d5b7164ea7fd4f1243a3c18a1769f3fd))

### Documentation

- Audit crm skill CLI coverage + slim router; add skill-authoring tooling
  ([`b7fe5ec`](https://github.com/Gharib89/crm/commit/b7fe5ecac82a9a3d666770aaa6f01ca1a31f0168))


## v1.3.1 (2026-06-20)

### Bug Fixes

- Add --type rollup|calculated and --formula-file to metadata add-attribute
  ([#443](https://github.com/Gharib89/crm/pull/443),
  [`edcbd0d`](https://github.com/Gharib89/crm/commit/edcbd0d5ebf0d256a6b304163102f4515e7359f7))


## v1.0.0 (2026-06-18)

- Initial Release

## Pre-1.0 development history

The sections below (`v0.12.0` → `v4.31.1`) record `crm`'s pre-launch development.
Before its first public release the project reset its version to `1.0.0` to shed
the misleading inflated number (see
[ADR 0011](https://github.com/Gharib89/crm/blob/main/docs/adr/0011-reset-to-1.0.0-and-label-gated-bumps.md));
this history is kept for the record. Releases from `1.0.0` on are inserted above
this line by python-semantic-release.

## v4.31.1 (2026-06-18)

### Bug Fixes

- **cli**: Align solution import publish/overwrite flags to opt-in pairs (#378)
  ([#414](https://github.com/Gharib89/crm/pull/414),
  [`c4a3969`](https://github.com/Gharib89/crm/commit/c4a3969df6010a114bb249cc3c91d9cc65477d80))


## v4.31.0 (2026-06-18)

### Bug Fixes

- **translation**: Add --publish to translation import (#377)
  ([#413](https://github.com/Gharib89/crm/pull/413),
  [`6f2af59`](https://github.com/Gharib89/crm/commit/6f2af59fd293125e4d16b26caa8ef306d5d53c60))

- **translation**: Restore publish-all warning when --publish skipped by dry-run
  ([#413](https://github.com/Gharib89/crm/pull/413),
  [`6f2af59`](https://github.com/Gharib89/crm/commit/6f2af59fd293125e4d16b26caa8ef306d5d53c60))

### Documentation

- **translation**: Document --publish flag on translation import
  ([#413](https://github.com/Gharib89/crm/pull/413),
  [`6f2af59`](https://github.com/Gharib89/crm/commit/6f2af59fd293125e4d16b26caa8ef306d5d53c60))

### Features

- **translation**: Add --publish flag to translation import
  ([#413](https://github.com/Gharib89/crm/pull/413),
  [`6f2af59`](https://github.com/Gharib89/crm/commit/6f2af59fd293125e4d16b26caa8ef306d5d53c60))


## v4.30.1 (2026-06-18)

### Bug Fixes

- **solution**: Add solution import --skip-dependency-check
  ([#412](https://github.com/Gharib89/crm/pull/412),
  [`8ceb564`](https://github.com/Gharib89/crm/commit/8ceb564b60281bff9d55b109607fad9f734ade97))


## v4.30.0 (2026-06-18)

### Features

- **async**: Async list --order-by/--filter
  ([`153f279`](https://github.com/Gharib89/crm/commit/153f279804358b43e157bad10aad4dea5b8bf60b))


## v4.29.0 (2026-06-18)

### Features

- **data**: First-class server-side BulkDelete verb
  ([#410](https://github.com/Gharib89/crm/pull/410),
  [`b9c7029`](https://github.com/Gharib89/crm/commit/b9c7029e74247f4e8de20e9ea222b68b6f684f1f))


## v4.28.0 (2026-06-18)

### Documentation

- **ship**: Address copilot review nits ([#407](https://github.com/Gharib89/crm/pull/407),
  [`2631c23`](https://github.com/Gharib89/crm/commit/2631c2352356ce43517fd408dde0408ade3a4a08))

- **ship**: Address copilot round-2 nit ([#407](https://github.com/Gharib89/crm/pull/407),
  [`2631c23`](https://github.com/Gharib89/crm/commit/2631c2352356ce43517fd408dde0408ade3a4a08))

- **ship**: Instruct phase 6 to fill the repo PR template
  ([#407](https://github.com/Gharib89/crm/pull/407),
  [`2631c23`](https://github.com/Gharib89/crm/commit/2631c2352356ce43517fd408dde0408ade3a4a08))

- **ship**: Trim SKILL.md, add Claude+Gemini model tiers
  ([#407](https://github.com/Gharib89/crm/pull/407),
  [`2631c23`](https://github.com/Gharib89/crm/commit/2631c2352356ce43517fd408dde0408ade3a4a08))

- **skill**: Note plugin set-step-state in automation reference
  ([#409](https://github.com/Gharib89/crm/pull/409),
  [`250f7cc`](https://github.com/Gharib89/crm/commit/250f7cc4600c7911eea4122082b3981c189535dd))

### Features

- Add plugin step and image registration options ([#409](https://github.com/Gharib89/crm/pull/409),
  [`250f7cc`](https://github.com/Gharib89/crm/commit/250f7cc4600c7911eea4122082b3981c189535dd))


## v4.27.1 (2026-06-18)

### Bug Fixes

- **metadata**: Add --auto-number-format and entities --filter/--managed-only
  ([#373](https://github.com/Gharib89/crm/pull/373),
  [`bc5007d`](https://github.com/Gharib89/crm/commit/bc5007d78285897d5910fc611e8a4ab5da97282e))

### Chores

- Expand PR template to cover all merge gates ([#408](https://github.com/Gharib89/crm/pull/408),
  [`b8011b0`](https://github.com/Gharib89/crm/commit/b8011b0c878677c23618a7ee302fd9d7de57b8ac))


## v4.27.0 (2026-06-18)

### Features

- **plugin**: Register webhook service endpoints and bind steps to them
  ([#406](https://github.com/Gharib89/crm/pull/406),
  [`ef2ec37`](https://github.com/Gharib89/crm/commit/ef2ec37c77a6b879528fd928a82671a6f5be28f9))


## v4.26.1 (2026-06-18)

### Bug Fixes

- **docs**: Correct phrasing in documentation regarding docs-sync subagent usage
  ([`fd653fd`](https://github.com/Gharib89/crm/commit/fd653fda4855cd48d3d07ab6fd1969fb7376f263))


## v4.26.0 (2026-06-18)

### Features

- **security**: Add --name-contains filter to list-roles, document direct-only scope on
  list-user-roles
  ([`75a4235`](https://github.com/Gharib89/crm/commit/75a42359a2442fa55a9f7a053277886d2a263839))


## v4.25.1 (2026-06-18)

### Bug Fixes

- **view**: Add --query-type and --description to view create
  ([#403](https://github.com/Gharib89/crm/pull/403),
  [`e1fa769`](https://github.com/Gharib89/crm/commit/e1fa769d273bc4f79658fdca85e08fac5e36a679))

### Documentation

- **ship**: Fold issue claim + PR-status reflection into the ship skill
  ([#401](https://github.com/Gharib89/crm/pull/401),
  [`1903d4d`](https://github.com/Gharib89/crm/commit/1903d4d4569edba1d9726ecbe783bc441e245172))


## v4.25.0 (2026-06-18)

### Documentation

- Add AGENTS.md pointing agents to CLAUDE.md
  ([`db08c31`](https://github.com/Gharib89/crm/commit/db08c31fde0250f58a1b4a8efda4fcde1234242f))

### Features

- **records**: Round out data-plane flags (--apply, --if-none-match, --mode delete)
  ([#402](https://github.com/Gharib89/crm/pull/402),
  [`ddcd419`](https://github.com/Gharib89/crm/commit/ddcd419df369b6e7aa88cc94b9ffa34a81de97b9))


## v4.24.0 (2026-06-18)

### Documentation

- Clarify version bump discipline (feat=minor for real features only)
  ([`95a829d`](https://github.com/Gharib89/crm/commit/95a829d4c43df0b67c483e574a916708c023a7e5))

### Features

- **metadata**: Add --kind customer composite lookup
  ([#400](https://github.com/Gharib89/crm/pull/400),
  [`124fab3`](https://github.com/Gharib89/crm/commit/124fab3b1e8669667908e51576adfabd4437bbd8))

### Refactoring

- **cli**: Unify the output-file flag to --output/-o
  ([#362](https://github.com/Gharib89/crm/pull/362),
  [`7482496`](https://github.com/Gharib89/crm/commit/7482496f0eabe60b10e37ca25768a7d345e3f221))


## v4.23.0 (2026-06-18)

### Features

- **security**: Add record sharing verbs (grant/revoke/list-access)
  ([#397](https://github.com/Gharib89/crm/pull/397),
  [`94e5a56`](https://github.com/Gharib89/crm/commit/94e5a56b4ec1b3cbb2ce6016600f97afcdbd2f01))


## v4.22.0 (2026-06-18)

### Features

- **action**: Parameter aliases and @odata.id record-reference params for action function
  ([#396](https://github.com/Gharib89/crm/pull/396),
  [`acece55`](https://github.com/Gharib89/crm/commit/acece555d43c336feaa45e7c777b97308617ed0f))


## v4.21.0 (2026-06-18)

### Features

- **query**: Add change tracking via --track-changes/--delta-token
  ([#395](https://github.com/Gharib89/crm/pull/395),
  [`052ea60`](https://github.com/Gharib89/crm/commit/052ea60100629011c750a11023c5a251ccdda03e))


## v4.20.0 (2026-06-18)

### Features

- **audit**: Retrieve server-side audit change history
  ([#394](https://github.com/Gharib89/crm/pull/394),
  [`82c063c`](https://github.com/Gharib89/crm/commit/82c063c356635861031e738ce348a011fc70c780))


## v4.19.0 (2026-06-18)

### Features

- **plugin**: Consistent --solution across register-assembly/step/image
  ([#393](https://github.com/Gharib89/crm/pull/393),
  [`14a734b`](https://github.com/Gharib89/crm/commit/14a734b86cf927b228ef90c51febb625d3911d8d))


## v4.18.0 (2026-06-18)

### Features

- **form**: Add --type/--all to form list to expose form types
  ([#392](https://github.com/Gharib89/crm/pull/392),
  [`5cf7c4a`](https://github.com/Gharib89/crm/commit/5cf7c4a2ac7d4aff1ac131f1b4f51a4704220297))

### Testing

- **form**: Make form list tests encoder- and org-independent
  ([#392](https://github.com/Gharib89/crm/pull/392),
  [`5cf7c4a`](https://github.com/Gharib89/crm/commit/5cf7c4a2ac7d4aff1ac131f1b4f51a4704220297))


## v4.17.0 (2026-06-18)

### Documentation

- **metadata**: Clarify datetime default — CLI sends Format, server defaults behavior
  ([#391](https://github.com/Gharib89/crm/pull/391),
  [`a46a059`](https://github.com/Gharib89/crm/commit/a46a059c6f1c7e6fa8bb5ecd75a76bbaf50c0601))

### Features

- **metadata**: Add-attribute --behavior to set DateTimeBehavior
  ([#391](https://github.com/Gharib89/crm/pull/391),
  [`a46a059`](https://github.com/Gharib89/crm/commit/a46a059c6f1c7e6fa8bb5ecd75a76bbaf50c0601))


## v4.16.1 (2026-06-18)

### Bug Fixes

- **query**: Default --annotations to True on query odata/fetchxml
  ([#390](https://github.com/Gharib89/crm/pull/390),
  [`0af693c`](https://github.com/Gharib89/crm/commit/0af693cc7a9ce63c9301f7f83d353fe45e40374e))


## v4.16.0 (2026-06-18)

### Features

- **app**: Add app remove-components (RemoveAppComponents)
  ([#389](https://github.com/Gharib89/crm/pull/389),
  [`e81967d`](https://github.com/Gharib89/crm/commit/e81967de6e4cf59faf795fb7e8c426a2ee4f8b20))


## v4.15.0 (2026-06-17)

### Features

- **security**: Add `security user-privileges` (RetrieveUserPrivileges)
  ([#388](https://github.com/Gharib89/crm/pull/388),
  [`17bac1b`](https://github.com/Gharib89/crm/commit/17bac1b2722a44c0865930b7b2b1a66bba315a12))


## v4.14.0 (2026-06-17)

### Features

- **solution**: Add standalone apply-upgrade (DeleteAndPromote)
  ([#387](https://github.com/Gharib89/crm/pull/387),
  [`7648343`](https://github.com/Gharib89/crm/commit/76483433f77650e8787f0fc25a67b814ee2f2b58))


## v4.13.0 (2026-06-17)

### Features

- **view**: Add view list <entity> ([#386](https://github.com/Gharib89/crm/pull/386),
  [`b616f0f`](https://github.com/Gharib89/crm/commit/b616f0f2c7ba4f16925f1673c6796fd3bac45d55))


## v4.12.0 (2026-06-17)

### Documentation

- **metadata**: Document is_bound/return_type/is_composable on list-actions/list-functions
  ([#385](https://github.com/Gharib89/crm/pull/385),
  [`a2ff49c`](https://github.com/Gharib89/crm/commit/a2ff49c8224b9a9cce692a7d75fe50da04af53d9))

### Features

- **metadata**: Surface is_bound/return_type/is_composable in list-actions/list-functions
  ([#385](https://github.com/Gharib89/crm/pull/385),
  [`a2ff49c`](https://github.com/Gharib89/crm/commit/a2ff49c8224b9a9cce692a7d75fe50da04af53d9))


## v4.11.0 (2026-06-17)

### Features

- **action**: Let action function invoke bound functions
  ([#384](https://github.com/Gharib89/crm/pull/384),
  [`7949fb8`](https://github.com/Gharib89/crm/commit/7949fb8e10aec8c079751da3c7c9989f29c51312))


## v4.10.0 (2026-06-17)

### Features

- **query**: Add --all/--max-records to follow @odata.nextLink
  ([#383](https://github.com/Gharib89/crm/pull/383),
  [`5b297e5`](https://github.com/Gharib89/crm/commit/5b297e51ac163de1e91e705fca6af7555a7c4a5e))


## v4.9.0 (2026-06-17)

### Features

- **metadata**: Create and delete alternate keys (EntityKeyMetadata)
  ([#382](https://github.com/Gharib89/crm/pull/382),
  [`99da3a6`](https://github.com/Gharib89/crm/commit/99da3a66f04b3de6752d2952fcfe1bcdf692cb2e))


## v4.8.2 (2026-06-17)

### Bug Fixes

- **cli**: Emit JSON envelope for form/ribbon export to stdout (#349)
  ([#381](https://github.com/Gharib89/crm/pull/381),
  [`2832b24`](https://github.com/Gharib89/crm/commit/2832b24def83692047dd496f558fa14c8c4629fa))


## v4.8.1 (2026-06-17)

### Bug Fixes

- **cli**: Guard entity disassociate/clear-lookup and workflow deactivate behind the destructive
  confirm ([#380](https://github.com/Gharib89/crm/pull/380),
  [`d9f3c72`](https://github.com/Gharib89/crm/commit/d9f3c72fa70531946d09e50c69221dbc526b79fb))


## v4.8.0 (2026-06-17)

### Bug Fixes

- **import**: Gate bulk alt-key enrichment on json mode (when-to-pay)
  ([#379](https://github.com/Gharib89/crm/pull/379),
  [`1847649`](https://github.com/Gharib89/crm/commit/1847649565038d674eceda170122dab5a40f6ea8))

### Continuous Integration

- Build binaries once, trust PR as the gate ([#344](https://github.com/Gharib89/crm/pull/344),
  [`9d94f10`](https://github.com/Gharib89/crm/commit/9d94f10e9d9d84bbd97366fded11d0843fae7d22))

### Documentation

- Add section on subagents for code exploration using the Explore agent
  ([`ed5b4df`](https://github.com/Gharib89/crm/commit/ed5b4df894e0c9c39d693082af2fb204e5001e3e))

- **adr**: Correct 0010 required-checks list + record PSR admin bypass
  ([`d8f9de8`](https://github.com/Gharib89/crm/commit/d8f9de85d45c02983c79a189bf1f97cae27f1ec2))

- **adr**: Record build-once, trust-PR CI/CD pipeline decision (0010)
  ([`c097b14`](https://github.com/Gharib89/crm/commit/c097b1457b4ed8e1eac9f067e2554707d60f71ca))

### Features

- **import**: Surface alternate-key collision hints on bulk data import failures
  ([#379](https://github.com/Gharib89/crm/pull/379),
  [`1847649`](https://github.com/Gharib89/crm/commit/1847649565038d674eceda170122dab5a40f6ea8))


## v4.7.0 (2026-06-16)

### Documentation

- Clarify RequiredLevel string vs null in skill contract
  ([#343](https://github.com/Gharib89/crm/pull/343),
  [`a4bed9d`](https://github.com/Gharib89/crm/commit/a4bed9db47fcd7ccea5925d5c9ca81e7df5c4d8b))

### Features

- Expose write/read validity + required level in metadata attributes
  ([#343](https://github.com/Gharib89/crm/pull/343),
  [`a4bed9d`](https://github.com/Gharib89/crm/commit/a4bed9db47fcd7ccea5925d5c9ca81e7df5c4d8b))


## v4.6.1 (2026-06-16)

### Bug Fixes

- **entity**: Point --data @file users to --data-file
  ([#342](https://github.com/Gharib89/crm/pull/342),
  [`79812af`](https://github.com/Gharib89/crm/commit/79812afc9f919aabf2cc836964ab54178756916a))


## v4.6.0 (2026-06-16)

### Bug Fixes

- Reject empty --key and stray alt_key; clarify docs (review)
  ([#341](https://github.com/Gharib89/crm/pull/341),
  [`0101e30`](https://github.com/Gharib89/crm/commit/0101e301ae668017f34de0777741b1c454c36979))

### Documentation

- Document upsert by alternate key (--key) ([#341](https://github.com/Gharib89/crm/pull/341),
  [`0101e30`](https://github.com/Gharib89/crm/commit/0101e301ae668017f34de0777741b1c454c36979))

### Features

- Upsert by alternate key (entity upsert --key / data import --key)
  ([#341](https://github.com/Gharib89/crm/pull/341),
  [`0101e30`](https://github.com/Gharib89/crm/commit/0101e301ae668017f34de0777741b1c454c36979))


## v4.5.2 (2026-06-16)

### Bug Fixes

- Import-side rebind of exported _<attr>_value lookups
  ([#333](https://github.com/Gharib89/crm/pull/333),
  [`ce27287`](https://github.com/Gharib89/crm/commit/ce272873b771aa499da04e8adb41fb813d000e1f))


## v4.5.1 (2026-06-16)

### Bug Fixes

- Data import reports per-record failures, not just counts
  ([#339](https://github.com/Gharib89/crm/pull/339),
  [`54712d1`](https://github.com/Gharib89/crm/commit/54712d19ed79c8f18015e3fc75a77d066fd30e66))

- Human-mode import failure line always leads with row index
  ([#339](https://github.com/Gharib89/crm/pull/339),
  [`54712d1`](https://github.com/Gharib89/crm/commit/54712d19ed79c8f18015e3fc75a77d066fd30e66))


## v4.5.0 (2026-06-16)

### Features

- **form**: Add field-editing verbs (add-field/remove-field/set-field)
  ([#338](https://github.com/Gharib89/crm/pull/338),
  [`be7353c`](https://github.com/Gharib89/crm/commit/be7353cfc128fac3dc07692d7aec2ae675c90a31))


## v4.4.0 (2026-06-15)

### Features

- Add `app delete` verb that sweeps FK-blocking dependents (#324)
  ([#331](https://github.com/Gharib89/crm/pull/331),
  [`f48e183`](https://github.com/Gharib89/crm/commit/f48e18322bc866d133d04de9e1a582c3efbaaf59))


## v4.3.0 (2026-06-15)

### Documentation

- Reword package-version note in module docstring ([#330](https://github.com/Gharib89/crm/pull/330),
  [`6bc55db`](https://github.com/Gharib89/crm/commit/6bc55db309d8a7548bbb0c5f41bc34546d886382))

### Features

- **solution**: Check package version ceiling in validate --against-org
  ([#330](https://github.com/Gharib89/crm/pull/330),
  [`6bc55db`](https://github.com/Gharib89/crm/commit/6bc55db309d8a7548bbb0c5f41bc34546d886382))


## v4.2.0 (2026-06-15)

### Features

- Add webresource delete verb ([#329](https://github.com/Gharib89/crm/pull/329),
  [`10913f1`](https://github.com/Gharib89/crm/commit/10913f153a7774446bb6f6a274561de49b684f78))


## v4.1.1 (2026-06-15)

### Bug Fixes

- App create --if-exists skip survives publish-before-read duplicate fault
  ([#328](https://github.com/Gharib89/crm/pull/328),
  [`6aa21c6`](https://github.com/Gharib89/crm/commit/6aa21c6d87e64f4f348f38c722f8686bec405917))

- Default max_length for string/memo attributes when omitted
  ([#327](https://github.com/Gharib89/crm/pull/327),
  [`fcf4bc1`](https://github.com/Gharib89/crm/commit/fcf4bc1305fae4315b32aecde2a2643edd10758e))

### Documentation

- **skill**: Add flag-free customization spines + ADR 0009
  ([#320](https://github.com/Gharib89/crm/pull/320),
  [`284725f`](https://github.com/Gharib89/crm/commit/284725fa78811688465f818703f8611a5ea38e30))


## v4.1.0 (2026-06-15)

### Chores

- **claude**: Add docs-sync subagent + edit-guard hooks, wire ship gate
  ([#318](https://github.com/Gharib89/crm/pull/318),
  [`7b63126`](https://github.com/Gharib89/crm/commit/7b631261ea44d2886ecdae2ecaee0516c2f16d43))

### Documentation

- **docs-sync**: Update model from opus to sonnet
  ([`f8ca3fd`](https://github.com/Gharib89/crm/commit/f8ca3fd707d179675556eecd3580192615d574b9))

- **ship-skill**: Add context-discipline guidance for long runs
  ([`f4fc41e`](https://github.com/Gharib89/crm/commit/f4fc41eff90e9178b76f3312fff483feee8245af))

### Features

- **output**: Concise human single-record render with --full escape hatch
  ([#319](https://github.com/Gharib89/crm/pull/319),
  [`3894a1c`](https://github.com/Gharib89/crm/commit/3894a1cf649418a47921723e3d17f17f4f79aa9d))


## v4.0.0 (2026-06-15)

### Features

- **output**: Curate the --json data contract (ADR 0008)
  ([#317](https://github.com/Gharib89/crm/pull/317),
  [`ae75bb3`](https://github.com/Gharib89/crm/commit/ae75bb3dd7b6c98e34f775486177b86fad7bdd99))

### Breaking Changes

- **output**: `query odata` `--json` rows move from `data.value` to a bare array in `data`, and
  `@odata.context`/`@odata.nextLink` leave `data` for `meta`. `entity delete` `--json` renames `id`
  to `_entity_id`. Single-record `--json` `data` has `@odata.*` stripped. Pre-1.0, permitted.


## v3.12.9 (2026-06-15)

### Bug Fixes

- Don't crash profile add when keyring backend panics at the Rust layer
  ([#316](https://github.com/Gharib89/crm/pull/316),
  [`8fc8281`](https://github.com/Gharib89/crm/commit/8fc8281ab0fccfe497cb47d4a9003eb37ee3809e))


## v3.12.8 (2026-06-15)

### Bug Fixes

- **output**: Render solution-component and relationship lists as tables
  ([#315](https://github.com/Gharib89/crm/pull/315),
  [`d7a51fc`](https://github.com/Gharib89/crm/commit/d7a51fc120fdfaaaf49194149c9e0d54824b7b1a))


## v3.12.7 (2026-06-15)

### Bug Fixes

- **query**: Count accepts the entity-set name and is case-insensitive
  ([#314](https://github.com/Gharib89/crm/pull/314),
  [`48a323e`](https://github.com/Gharib89/crm/commit/48a323e656ad90807e7a2448172b50638f18dfc8))

### Documentation

- **cloud-ship-routine**: Force Skill-tool invocation + task-tool fallback
  ([`6fca0fe`](https://github.com/Gharib89/crm/commit/6fca0fe99151802dafa88f2ed226d72094be9033))


## v3.12.6 (2026-06-15)

### Bug Fixes

- **skill**: Echo destination directory on uninstall no-op
  ([#313](https://github.com/Gharib89/crm/pull/313),
  [`d2cf6eb`](https://github.com/Gharib89/crm/commit/d2cf6eb479a951ef803c612a205cf8d9608e84cc))

### Testing

- **skill**: Assert uninstall exit codes before parsing JSON
  ([#313](https://github.com/Gharib89/crm/pull/313),
  [`d2cf6eb`](https://github.com/Gharib89/crm/commit/d2cf6eb479a951ef803c612a205cf8d9608e84cc))


## v3.12.5 (2026-06-15)

### Bug Fixes

- **repl**: Ignore an optional leading 'crm' prefix
  ([#312](https://github.com/Gharib89/crm/pull/312),
  [`620dba2`](https://github.com/Gharib89/crm/commit/620dba202ed16a39db5a66e901fceee4785c4584))


## v3.12.4 (2026-06-14)

### Bug Fixes

- **cli**: Clarify --profile help points at profiles/ under CRM_HOME
  ([#311](https://github.com/Gharib89/crm/pull/311),
  [`6fc5d83`](https://github.com/Gharib89/crm/commit/6fc5d8305ea9ca876912b822f4ae26f352e1f956))

- **cli**: Root help reflects cloud + on-prem; drop hardcoded --profile path
  ([#311](https://github.com/Gharib89/crm/pull/311),
  [`6fc5d83`](https://github.com/Gharib89/crm/commit/6fc5d8305ea9ca876912b822f4ae26f352e1f956))


## v3.12.3 (2026-06-14)

### Bug Fixes

- **cli**: Suggest near-miss top-level command names
  ([#310](https://github.com/Gharib89/crm/pull/310),
  [`3fb1f99`](https://github.com/Gharib89/crm/commit/3fb1f99b9e63b0a126a350f66c2fb718733fe56a))


## v3.12.2 (2026-06-14)

### Bug Fixes

- **cli**: Hint that global flags go before the subcommand
  ([#309](https://github.com/Gharib89/crm/pull/309),
  [`ece8923`](https://github.com/Gharib89/crm/commit/ece892359c8e9acb2f6e2604e0e7593c6fadac43))

### Chores

- **agents**: Cloud ship routine (vendored skills + bootstrap + runbook)
  ([`8f1ce23`](https://github.com/Gharib89/crm/commit/8f1ce2326c5dfd4ae1d9397317497aeac7b05cca))

- **agents**: Install gh in cloud routine + branch/merge-gate fixes
  ([`b1c3ff7`](https://github.com/Gharib89/crm/commit/b1c3ff7cc05b413fc9ffaf920510f96f6261c2ee))

- **metadata**: Drop unused _admin_header_options/_admin_kwargs imports
  ([`20b0668`](https://github.com/Gharib89/crm/commit/20b0668888436f1d8dfccff15ec5d98a0dcd2051))

### Documentation

- **adr**: Record CLI output contract + glossary terms (ADR 0008)
  ([`e0d366d`](https://github.com/Gharib89/crm/commit/e0d366d1856c6056a7a0be730a94475b68d7f067))

- **claude**: Note zsh output-capture traps when driving the CLI
  ([`3fcb54e`](https://github.com/Gharib89/crm/commit/3fcb54e6a5feb3320965e5cc1dc0baf8d641a27b))


## v3.12.1 (2026-06-14)

### Bug Fixes

- **commands**: Clean exit-2 on non-int value:label option input
  ([#296](https://github.com/Gharib89/crm/pull/296),
  [`2eb4102`](https://github.com/Gharib89/crm/commit/2eb410241edc1035a2660342a2cee5275d75bea4))

### Documentation

- Purge real test-org GUIDs from published docs ([#259](https://github.com/Gharib89/crm/pull/259),
  [`5ab576a`](https://github.com/Gharib89/crm/commit/5ab576a6097cc19eb86531b9c766f6f599d9b941))

- **claude**: Note single-test command and pytest e2e default
  ([`34c008e`](https://github.com/Gharib89/crm/commit/34c008ed8ad7ad23cff8ec97c3362327221c20f8))

- **metadata**: Align --update-option help with parser error wording
  ([#296](https://github.com/Gharib89/crm/pull/296),
  [`2eb4102`](https://github.com/Gharib89/crm/commit/2eb410241edc1035a2660342a2cee5275d75bea4))

### Refactoring

- **commands**: Composable seams for per-verb boilerplate
  ([#264](https://github.com/Gharib89/crm/pull/264),
  [`aeee16f`](https://github.com/Gharib89/crm/commit/aeee16f31444a7b6c8b7a79335fca322d3eac0d6))

- **commands**: Split _helpers grab-bag into a package
  ([#271](https://github.com/Gharib89/crm/pull/271),
  [`8cdacce`](https://github.com/Gharib89/crm/commit/8cdacce0b921dd9dd24e0a7d5f2f1e16f9b2b3d6))

- **core**: Single seam for metadata constraint vocabularies
  ([#295](https://github.com/Gharib89/crm/pull/295),
  [`3cca964`](https://github.com/Gharib89/crm/commit/3cca96427f38d0ed06c7992205e6744103e56572))

- **core**: Split solution.py into lifecycle / components / transfer
  ([#265](https://github.com/Gharib89/crm/pull/265),
  [`8e88986`](https://github.com/Gharib89/crm/commit/8e889869691deb52bfac537a92dd36708aef38dc))


## v3.12.0 (2026-06-14)

### Bug Fixes

- **completion**: Quote PS dot-source path; positive-guard the completer loop
  ([#288](https://github.com/Gharib89/crm/pull/288),
  [`a533710`](https://github.com/Gharib89/crm/commit/a5337102a038f09a220d2fdc338ad794cb3ee485))

### Features

- **completion**: Add PowerShell tab-completion ([#288](https://github.com/Gharib89/crm/pull/288),
  [`a533710`](https://github.com/Gharib89/crm/commit/a5337102a038f09a220d2fdc338ad794cb3ee485))


## v3.11.0 (2026-06-14)

### Documentation

- **skill**: Add customization-lifecycle reference + query count example
  ([`ac87ece`](https://github.com/Gharib89/crm/commit/ac87ece842ff5894ae525dd7cf5720d5c22cfe5e))

### Features

- **dry-run**: Resolve & report referenced objects on name-taking writes
  ([#286](https://github.com/Gharib89/crm/pull/286),
  [`d2e54aa`](https://github.com/Gharib89/crm/commit/d2e54aae435274514ad5a5df276655125397c024))

### Refactoring

- Consolidate entity-name resolution into one cache-backed seam
  ([#261](https://github.com/Gharib89/crm/pull/261),
  [`95e1d84`](https://github.com/Gharib89/crm/commit/95e1d84a2151522f24531c94fca74288f4cbb092))

- Deepen the OData response surface on D365Backend
  ([#280](https://github.com/Gharib89/crm/pull/280),
  [`509f564`](https://github.com/Gharib89/crm/commit/509f564ac5421f29a204dee760734c8b0f32b49e))

### Testing

- Finish shared-conftest dedup — drop remaining fixture shadows
  ([#285](https://github.com/Gharib89/crm/pull/285),
  [`4930753`](https://github.com/Gharib89/crm/commit/4930753fd61d669cc351f1b7897433053e7ec86d))

- Shared unit-suite conftest + canonical FakeBackend adapter
  ([#283](https://github.com/Gharib89/crm/pull/283),
  [`2714a06`](https://github.com/Gharib89/crm/commit/2714a063df69e6152f11932e16a1b712ea62e59d))


## v3.10.0 (2026-06-13)

### Features

- Add crm completion command + docs, auto-refresh on self-update
  ([#279](https://github.com/Gharib89/crm/pull/279),
  [`e666861`](https://github.com/Gharib89/crm/commit/e666861bb37b7eb53b2ebb7aa954570508e88310))


## v3.9.4 (2026-06-13)

### Bug Fixes

- **metadata**: Update-relationship writes to un-cast RelationshipDefinitions path
  ([#278](https://github.com/Gharib89/crm/pull/278),
  [`dac4ecc`](https://github.com/Gharib89/crm/commit/dac4ecc409b548a294b38150b661f347f1e2dbdb))


## v3.9.3 (2026-06-13)

### Bug Fixes

- **metadata**: Request $metadata CSDL with XML Accept header (#266)
  ([#277](https://github.com/Gharib89/crm/pull/277),
  [`5974c7c`](https://github.com/Gharib89/crm/commit/5974c7c40bdd741612b8eca55807df2a4d5e4fa2))

### Documentation

- Name agent-on-prem/agent-cloud as the live profiles, document Copilot auto-review
  ([`6e67adc`](https://github.com/Gharib89/crm/commit/6e67adcc891c66202a29dc2b495c120eba417880))


## v3.9.2 (2026-06-13)

### Bug Fixes

- **ribbon**: Unblock add-button/remove on round-trip solution import
  ([#276](https://github.com/Gharib89/crm/pull/276),
  [`cd9ea28`](https://github.com/Gharib89/crm/commit/cd9ea280cb95f83d8921334c78eb6c6e9f6ad451))

### Documentation

- Add worktree-e2e, host-guard, and test-target rules to CLAUDE.md
  ([`25a77e1`](https://github.com/Gharib89/crm/commit/25a77e110f64657093fc5c8781793cccabaf8e15))


## v3.9.1 (2026-06-13)

### Bug Fixes

- **form**: Regenerate internal ids on clone to avoid on-prem collisions
  ([#275](https://github.com/Gharib89/crm/pull/275),
  [`7477088`](https://github.com/Gharib89/crm/commit/74770880359b48d67c0ee56865e9dcc13e387038))

### Documentation

- **agents**: Document priority + effort label axes
  ([#272](https://github.com/Gharib89/crm/pull/272),
  [`30f6384`](https://github.com/Gharib89/crm/commit/30f6384eaeefa41a5e15fb69d1306172b564f4cd))

- **plans**: E2e test completeness implementation plan
  ([`44a69b4`](https://github.com/Gharib89/crm/commit/44a69b449ea0e016b39f4e63b9a9646a795077a6))

- **specs**: E2e test completeness design
  ([`143aa5d`](https://github.com/Gharib89/crm/commit/143aa5d49a517517555207a84771a096dc044c29))

- **specs**: Harden e2e design after review
  ([`3b440fa`](https://github.com/Gharib89/crm/commit/3b440faf3e6a6eba1aadd6ab59fd84b425570a3d))

### Testing

- **e2e**: Complete live coverage + offline enforcement gate
  ([#270](https://github.com/Gharib89/crm/pull/270),
  [`75f4ae7`](https://github.com/Gharib89/crm/commit/75f4ae7e2e93a6be03c6f38026a8cef628113702))

- **e2e**: Profile-based target + credential selection for live suite
  ([#274](https://github.com/Gharib89/crm/pull/274),
  [`df08be8`](https://github.com/Gharib89/crm/commit/df08be8348c508db38e7424ef730927b83f3e274))

- **form**: Use obvious placeholder GUIDs for external-ref constants
  ([#275](https://github.com/Gharib89/crm/pull/275),
  [`7477088`](https://github.com/Gharib89/crm/commit/74770880359b48d67c0ee56865e9dcc13e387038))


## v3.9.0 (2026-06-12)

### Features

- **entity**: Add `entity clone --with-children` (#256)
  ([#258](https://github.com/Gharib89/crm/pull/258),
  [`a053a89`](https://github.com/Gharib89/crm/commit/a053a89930f7e0da97923c486807a83f0c4c99a4))


## v3.8.0 (2026-06-11)

### Features

- **entity**: Add `entity clone` — single-record clone with overrides
  ([#255](https://github.com/Gharib89/crm/pull/255),
  [`dc757d2`](https://github.com/Gharib89/crm/commit/dc757d271ab443d8fcb2d613a5eac14c07ead517))


## v3.7.0 (2026-06-11)

### Bug Fixes

- **entity**: Narrow children $select; validate --filter-entities at CLI boundary
  ([#254](https://github.com/Gharib89/crm/pull/254),
  [`9a9cdd0`](https://github.com/Gharib89/crm/commit/9a9cdd00d98d79614e161fd43d7560427afa018b))

- **entity**: Run children counts under --dry-run; validate regex + chunk size
  ([#254](https://github.com/Gharib89/crm/pull/254),
  [`9a9cdd0`](https://github.com/Gharib89/crm/commit/9a9cdd00d98d79614e161fd43d7560427afa018b))

### Documentation

- **entity**: Say children uses chunked $batch, not "one batched call"
  ([#254](https://github.com/Gharib89/crm/pull/254),
  [`9a9cdd0`](https://github.com/Gharib89/crm/commit/9a9cdd00d98d79614e161fd43d7560427afa018b))

### Features

- **entity**: Add children — per-1:N related-record counts via $batch
  ([#254](https://github.com/Gharib89/crm/pull/254),
  [`9a9cdd0`](https://github.com/Gharib89/crm/commit/9a9cdd00d98d79614e161fd43d7560427afa018b))

- **entity**: Add children — per-1:N related-record counts via $batch (#234)
  ([#254](https://github.com/Gharib89/crm/pull/254),
  [`9a9cdd0`](https://github.com/Gharib89/crm/commit/9a9cdd00d98d79614e161fd43d7560427afa018b))

### Testing

- **e2e**: Seed custom solution + xfail bigint kind
  ([#252](https://github.com/Gharib89/crm/pull/252),
  [`d04f32e`](https://github.com/Gharib89/crm/commit/d04f32e346e4b2a512d7195b829ca2f7bd85dd66))


## v3.6.1 (2026-06-11)

### Performance Improvements

- Lazy-load the HTTP-transport stack so local commands skip the import tax
  ([`d88c754`](https://github.com/Gharib89/crm/commit/d88c754d02dc28f07f86f902820f597eb5acaf0a))


## v3.6.0 (2026-06-11)

### Chores

- **test**: Remove unused imports (call, D365Backend) in fetchxml test
  ([#250](https://github.com/Gharib89/crm/pull/250),
  [`0d70d73`](https://github.com/Gharib89/crm/commit/0d70d73884fa5ba2ef955f9a476243ccdafd0223))

### Features

- **query**: Make ENTITY_SET optional on fetchxml — derive from XML
  ([#250](https://github.com/Gharib89/crm/pull/250),
  [`0d70d73`](https://github.com/Gharib89/crm/commit/0d70d73884fa5ba2ef955f9a476243ccdafd0223))

- **query**: Make ENTITY_SET optional on fetchxml — derive from XML (#202)
  ([#250](https://github.com/Gharib89/crm/pull/250),
  [`0d70d73`](https://github.com/Gharib89/crm/commit/0d70d73884fa5ba2ef955f9a476243ccdafd0223))

### Refactoring

- **query**: Guard empty logical_name in resolver; drop type: ignore in fetchxml cmd
  ([#250](https://github.com/Gharib89/crm/pull/250),
  [`0d70d73`](https://github.com/Gharib89/crm/commit/0d70d73884fa5ba2ef955f9a476243ccdafd0223))

### Testing

- **query**: Strengthen fetchxml tests per round-3 review
  ([#250](https://github.com/Gharib89/crm/pull/250),
  [`0d70d73`](https://github.com/Gharib89/crm/commit/0d70d73884fa5ba2ef955f9a476243ccdafd0223))


## v3.5.0 (2026-06-11)

### Features

- **metadata**: Hint did_you_mean on describe 404 for set-name mistakes
  ([#249](https://github.com/Gharib89/crm/pull/249),
  [`ad7ca98`](https://github.com/Gharib89/crm/commit/ad7ca984cafcb049f60d7215d38fc6865a39b4f1))


## v3.4.0 (2026-06-11)

### Bug Fixes

- **entity**: Address Copilot review comments on duplicate-key enrichment
  ([#248](https://github.com/Gharib89/crm/pull/248),
  [`8aadbe7`](https://github.com/Gharib89/crm/commit/8aadbe76f86562b8e4a81d5f7ca285eb2a68c9f8))

### Features

- **metadata**: Add metadata keys command + enrich duplicate-key errors
  ([#248](https://github.com/Gharib89/crm/pull/248),
  [`8aadbe7`](https://github.com/Gharib89/crm/commit/8aadbe76f86562b8e4a81d5f7ca285eb2a68c9f8))


## v3.3.0 (2026-06-11)

### Features

- **workflow**: Accept friendly names for --category on workflow list
  ([#246](https://github.com/Gharib89/crm/pull/246),
  [`d51e87f`](https://github.com/Gharib89/crm/commit/d51e87f365827aa48f4b266e8b0c3c19d92d58bb))


## v3.2.0 (2026-06-11)

### Bug Fixes

- **emit**: Print warnings before error in human mode
  ([#244](https://github.com/Gharib89/crm/pull/244),
  [`f093b4f`](https://github.com/Gharib89/crm/commit/f093b4fc133464d123aa4b7c4527a162d922d22c))

- **entity**: Thread validate warnings through create D365Error path; simplify payload check
  ([#244](https://github.com/Gharib89/crm/pull/244),
  [`f093b4f`](https://github.com/Gharib89/crm/commit/f093b4fc133464d123aa4b7c4527a162d922d22c))

- **entity**: Tighten human-mode warning test; annotate payload type
  ([#244](https://github.com/Gharib89/crm/pull/244),
  [`f093b4f`](https://github.com/Gharib89/crm/commit/f093b4fc133464d123aa4b7c4527a162d922d22c))

### Documentation

- **entity**: Clarify validate_payload docstring — 1-3 GETs not always 3
  ([#244](https://github.com/Gharib89/crm/pull/244),
  [`f093b4f`](https://github.com/Gharib89/crm/commit/f093b4fc133464d123aa4b7c4527a162d922d22c))

### Features

- **entity**: Warn when create payload contains the primary id
  ([#244](https://github.com/Gharib89/crm/pull/244),
  [`f093b4f`](https://github.com/Gharib89/crm/commit/f093b4fc133464d123aa4b7c4527a162d922d22c))

- **entity**: Warn when create payload contains the primary id (#233)
  ([#244](https://github.com/Gharib89/crm/pull/244),
  [`f093b4f`](https://github.com/Gharib89/crm/commit/f093b4fc133464d123aa4b7c4527a162d922d22c))


## v3.1.5 (2026-06-11)

### Bug Fixes

- **self-update**: Fall back to current_version() when frozen install is up-to-date
  ([#245](https://github.com/Gharib89/crm/pull/245),
  [`b809468`](https://github.com/Gharib89/crm/commit/b8094685a9e1b9c0de9c75085119f4ea1485cfe6))

- **self-update**: Progress messages, clear version labels, expanded skills output
  ([#245](https://github.com/Gharib89/crm/pull/245),
  [`b809468`](https://github.com/Gharib89/crm/commit/b8094685a9e1b9c0de9c75085119f4ea1485cfe6))

- **self-update**: Show progress, fix misleading version label, expand skills output
  ([#245](https://github.com/Gharib89/crm/pull/245),
  [`b809468`](https://github.com/Gharib89/crm/commit/b8094685a9e1b9c0de9c75085119f4ea1485cfe6))


## v3.1.4 (2026-06-11)

### Bug Fixes

- **query**: Widen error message and test matrix to cover all three path forms
  ([#243](https://github.com/Gharib89/crm/pull/243),
  [`51a1687`](https://github.com/Gharib89/crm/commit/51a168720cf5f8fa9421514b1dee6b1f0c7b3360))

### Code Style

- **tests,docs**: Replace real-looking GUID with obvious placeholder
  ([#243](https://github.com/Gharib89/crm/pull/243),
  [`51a1687`](https://github.com/Gharib89/crm/commit/51a168720cf5f8fa9421514b1dee6b1f0c7b3360))

### Documentation

- **query**: Promote odata path-arg contract to documented, tested behavior
  ([#243](https://github.com/Gharib89/crm/pull/243),
  [`51a1687`](https://github.com/Gharib89/crm/commit/51a168720cf5f8fa9421514b1dee6b1f0c7b3360))


## v3.1.3 (2026-06-11)

### Bug Fixes

- **metadata**: Picklist command returns options for State/Status attributes (#229)
  ([#242](https://github.com/Gharib89/crm/pull/242),
  [`c82909c`](https://github.com/Gharib89/crm/commit/c82909c7a97fb6c7a7cbb39df927933907678e64))


## v3.1.2 (2026-06-11)

### Bug Fixes

- **entity**: Accept both --no-return and --return-record on create and update
  ([#241](https://github.com/Gharib89/crm/pull/241),
  [`ddfb1a9`](https://github.com/Gharib89/crm/commit/ddfb1a97e0bbff4736145ee7b6963ecebfc6ade6))


## v3.1.1 (2026-06-11)

### Bug Fixes

- **metadata**: Derive bind_key from referencing nav property, not referenced
  ([#240](https://github.com/Gharib89/crm/pull/240),
  [`e8fdb5e`](https://github.com/Gharib89/crm/commit/e8fdb5ebf17273cdfde088cb4015d529e5a0d988))

### Documentation

- **claude**: Mandate worktree-per-feature for shared checkout
  ([`59a7d97`](https://github.com/Gharib89/crm/commit/59a7d97551164d4b4d071c2bbeed7da8314c0d28))


## v3.1.0 (2026-06-11)

### Documentation

- Record-clone glossary terms + ADR 0007 (no rollback)
  ([#231](https://github.com/Gharib89/crm/pull/231),
  [`89956a3`](https://github.com/Gharib89/crm/commit/89956a38d34f1b33253e58ca4d71b1dd28c289f2))

### Features

- **solution**: Add layer-conflicts to detect unmanaged-layer overlap
  ([#239](https://github.com/Gharib89/crm/pull/239),
  [`93599ce`](https://github.com/Gharib89/crm/commit/93599ce4f0f721efe4b9c87f374fd5c232976a6d))


## v3.0.0 (2026-06-11)

### Documentation

- **out-of-scope**: Reject raw http passthrough verb
  ([#235](https://github.com/Gharib89/crm/pull/235),
  [`512275a`](https://github.com/Gharib89/crm/commit/512275ac1909c6647686cefeb2d239497c908c3a))

### Features

- Dry-run previews only mutations; reads execute ([#236](https://github.com/Gharib89/crm/pull/236),
  [`fb347fa`](https://github.com/Gharib89/crm/commit/fb347fa95ef2c47ed5511662fd09df4938482da8))

### Breaking Changes

- Read verbs under --dry-run (query odata, entity get, action function, …) now execute the real GET
  and return live data instead of returning the {_dry_run, method, url, …} request echo. The
  envelope still carries meta.dry_run: true. The root --dry-run help ("Preview HTTP request without
  issuing it") was a documented contract, so this is a breaking change.


## v2.12.0 (2026-06-11)

### Features

- **skill**: Default install to claude, confirm-overwrite, refresh on self-update
  ([`3f12923`](https://github.com/Gharib89/crm/commit/3f12923f756c6c8f7174581e147a3652f98dfb84))


## v2.11.1 (2026-06-11)

### Bug Fixes

- **self-update**: Suppress upgrade notice after self-update runs
  ([`c63890f`](https://github.com/Gharib89/crm/commit/c63890f1cfa9bb26ee10e0380caeda6748b6dce9))


## v2.11.0 (2026-06-11)

### Build System

- **docs**: Cap mkdocs deps below 2.0 to keep strict build reproducible
  ([`2b38ee9`](https://github.com/Gharib89/crm/commit/2b38ee95949fe84b95dc9029f663062672eeeda0))

### Documentation

- **adr**: Record self-update refreshes installed skills
  ([#225](https://github.com/Gharib89/crm/pull/225),
  [`902a03a`](https://github.com/Gharib89/crm/commit/902a03a17f2e5825825cfb045a6318767c62ac39))

- **adr**: Record stay-on-Material-9x, ProperDocs when forced
  ([#224](https://github.com/Gharib89/crm/pull/224),
  [`f8bf24f`](https://github.com/Gharib89/crm/commit/f8bf24f35fc385710de429288e7be68e0acbff9b))

### Features

- **profile**: Inline questionary pickers + --client-secret alias
  ([#226](https://github.com/Gharib89/crm/pull/226),
  [`48d3d06`](https://github.com/Gharib89/crm/commit/48d3d066be65857e71cd0d5f1536f0be81aad77f))


## v2.10.0 (2026-06-11)

### Features

- **self-update**: Add crm self-update + passive update notice
  ([#219](https://github.com/Gharib89/crm/pull/219),
  [`9959404`](https://github.com/Gharib89/crm/commit/9959404e6e1dffe6ded6e30529d678d40d3bdffd))


## v2.9.0 (2026-06-10)

### Features

- **workflow**: Assess classic workflows for cloud-flow migration
  ([#199](https://github.com/Gharib89/crm/pull/199),
  [`d6c91fc`](https://github.com/Gharib89/crm/commit/d6c91fca200d6a141f9e03a28e1c5a95fc07d8e4))


## v2.8.1 (2026-06-10)

### Bug Fixes

- **entity**: Rank did_you_mean for numbered field families
  ([#198](https://github.com/Gharib89/crm/pull/198),
  [`903b2c1`](https://github.com/Gharib89/crm/commit/903b2c118f730cde7625ca514a02762bbeaff463))


## v2.8.0 (2026-06-10)

### Documentation

- **scope**: Record environment-admin verbs as out-of-scope
  ([#201](https://github.com/Gharib89/crm/pull/201),
  [`93e1adc`](https://github.com/Gharib89/crm/commit/93e1adc313bee5c0b62228ed487a5346aa24e15f))

### Features

- **view**: Descending sort in view create --order
  ([#217](https://github.com/Gharib89/crm/pull/217),
  [`9b8937f`](https://github.com/Gharib89/crm/commit/9b8937f85d748d3475232c4ad07472e076ada221))


## v2.7.0 (2026-06-10)

### Documentation

- **skill**: Add `crm batch` standalone section to records.md
  ([#189](https://github.com/Gharib89/crm/pull/189),
  [`bc4cd98`](https://github.com/Gharib89/crm/commit/bc4cd985a617ac081d050f20e4ac574e20accff5))

- **skill**: Add edit-existing-view recipe to authoring.md
  ([#188](https://github.com/Gharib89/crm/pull/188),
  [`0d1ea0c`](https://github.com/Gharib89/crm/commit/0d1ea0cf914db06332c9b82d6bd1ede627078f0a))

- **skill**: Add plug-in trace-log debugging recipe to automation.md
  ([`2770f1d`](https://github.com/Gharib89/crm/commit/2770f1d9f4191c665cbb69a3e63ad2433f11c338))

- **skill**: Teach early-bound codegen toolchain + ADR no-wrapper
  ([#194](https://github.com/Gharib89/crm/pull/194),
  [`2664786`](https://github.com/Gharib89/crm/commit/2664786efe01c2fc9e661efd390357132dc014b7))

- **skill**: Teach role component-type + managed-import privilege strip
  ([#190](https://github.com/Gharib89/crm/pull/190),
  [`caa0326`](https://github.com/Gharib89/crm/commit/caa0326b7575eb9c8c76ebfc9a7be1fa863d002b))

- **skill**: Validate-first default + on-prem business-rule deactivate caveat
  ([`b6c91a3`](https://github.com/Gharib89/crm/commit/b6c91a37460f2525b4d154f91ee4451102a5a1d5))

### Features

- **solution**: Managed-lifecycle verbs clone-as-patch, stage-and-upgrade, uninstall
  ([#216](https://github.com/Gharib89/crm/pull/216),
  [`a42fa32`](https://github.com/Gharib89/crm/commit/a42fa32d0afca1578d1c88215a1f63a0540ee2f6))


## v2.6.2 (2026-06-10)

### Bug Fixes

- **batch**: Parse inner error from failing $batch, reject leading-slash op urls
  ([#212](https://github.com/Gharib89/crm/pull/212),
  [`b42f0dd`](https://github.com/Gharib89/crm/commit/b42f0dd797d97e4565c45b783bd679f29a364a85))


## v2.6.1 (2026-06-10)

### Bug Fixes

- Reject OData params in query odata entity-set arg client-side
  ([#185](https://github.com/Gharib89/crm/pull/185),
  [`056c5b2`](https://github.com/Gharib89/crm/commit/056c5b2d580e15c312fbe8a8f7672028a41f76bd))


## v2.6.0 (2026-06-10)

### Features

- **translation**: Add translation export/import verbs
  ([#210](https://github.com/Gharib89/crm/pull/210),
  [`c5355e7`](https://github.com/Gharib89/crm/commit/c5355e717f3315f140ddf2ec545b92dc1a412c33))


## v2.5.0 (2026-06-10)

### Documentation

- **skill**: Add failed-import investigation recipe to solutions reference
  ([#183](https://github.com/Gharib89/crm/pull/183),
  [`c72f4d4`](https://github.com/Gharib89/crm/commit/c72f4d43d94417bf34f5c273813d63493334aa72))

- **skill**: Add records-verb router to command discovery
  ([#184](https://github.com/Gharib89/crm/pull/184),
  [`7153e53`](https://github.com/Gharib89/crm/commit/7153e53558c9e00c86fdf1bbe2bb49d779ec507e))

### Features

- **plugin**: Add step-image registration verbs ([#193](https://github.com/Gharib89/crm/pull/193),
  [`f1560d7`](https://github.com/Gharib89/crm/commit/f1560d7bd79674aadce3452e5745bcc9fd71e46e))


## v2.4.4 (2026-06-10)

### Bug Fixes

- **solution**: Sync ImportSolution fallback so import-result is usable on-prem
  ([#182](https://github.com/Gharib89/crm/pull/182),
  [`6082d77`](https://github.com/Gharib89/crm/commit/6082d77375987af24be2c01d63c21b121a4eee81))


## v2.4.3 (2026-06-10)

### Bug Fixes

- **solution**: Remove-component sends SolutionComponent entity-reference shape
  ([#181](https://github.com/Gharib89/crm/pull/181),
  [`255fcd5`](https://github.com/Gharib89/crm/commit/255fcd56ba6c4c666e01f4dbf41774fa7fa8a667))


## v2.4.2 (2026-06-10)

### Bug Fixes

- **commands**: Wrap _load_payload JSON parse failure as UsageError
  ([#206](https://github.com/Gharib89/crm/pull/206),
  [`8bc4582`](https://github.com/Gharib89/crm/commit/8bc4582d82b4d8ed25a78050c3b5d21b85b3dc04))


## v2.4.1 (2026-06-10)

### Bug Fixes

- **metadata**: Get-optionset 400 — drop invalid $expand=Options
  ([#205](https://github.com/Gharib89/crm/pull/205),
  [`42683e9`](https://github.com/Gharib89/crm/commit/42683e902654b72f421489a9bb7b9b54ff476b52))

### Documentation

- **research**: Backlog filed — issue numbers recorded
  ([`3d597e3`](https://github.com/Gharib89/crm/commit/3d597e3a7b6ceb2785235d54df9ecb500f147c1c))

- **research**: Cleanup confirmation and final criteria check
  ([`336a2a4`](https://github.com/Gharib89/crm/commit/336a2a492328d9121082dbee361534cec13aed13))

- **research**: Coverage matrix (Phase 2 complete)
  ([`177903e`](https://github.com/Gharib89/crm/commit/177903e9942b1ead75d97366c44c2ea32326ee66))

- **research**: CRM developer scenarios report (Phase 4 report)
  ([`ca8a142`](https://github.com/Gharib89/crm/commit/ca8a14253805e595eabcc783dc7cd859670a09cb))

- **research**: Draft enhancement backlog for review
  ([`2bee836`](https://github.com/Gharib89/crm/commit/2bee836f6505268f976ce8fae0cc447c2db4c62d))

- **research**: Harvest scenario candidates from five source families
  ([`acb5566`](https://github.com/Gharib89/crm/commit/acb5566c4811ac3c11575f8c00353db070f4dd0e))

- **research**: Ingest prior art into scenario harvest
  ([`510fa70`](https://github.com/Gharib89/crm/commit/510fa7045daae399b6f70194d79fb8a9b9e465b1))

- **research**: Minimal repros for trial-discovered CLI bugs
  ([`2777245`](https://github.com/Gharib89/crm/commit/2777245d4bcf1af361a852187d356f4e42f9efe7))

- **research**: Scenario catalogue (Phase 1 complete)
  ([`d5738f4`](https://github.com/Gharib89/crm/commit/d5738f4f667cc15d3e6e929219dd129d7edea762))

- **research**: Skill trial log (Phase 3 complete)
  ([`3d18a8c`](https://github.com/Gharib89/crm/commit/3d18a8c0abdea5f273d17f3da3959a7a515a1abf))

- **research**: Trial plan and environment readiness
  ([`e7dc7b7`](https://github.com/Gharib89/crm/commit/e7dc7b7032e7ea5f359daebcae77fa296504c492))


## v2.4.0 (2026-06-10)

### Documentation

- **copilot**: Add custom instructions for Copilot code review
  ([`f50598c`](https://github.com/Gharib89/crm/commit/f50598c7a97a35bd22327df51d37afb97fffab15))

### Features

- **sla**: Add sla activate orchestrating backing workflows with structured compile-error reporting
  ([#168](https://github.com/Gharib89/crm/pull/168),
  [`9c010f1`](https://github.com/Gharib89/crm/commit/9c010f1decf527c6797037dc4e8c035218e85dec))


## v2.3.0 (2026-06-10)

### Documentation

- **plans**: CRM developer scenarios research implementation plan
  ([`694cd59`](https://github.com/Gharib89/crm/commit/694cd5984374453d7c81ccc7ce666c244189dd42))

- **specs**: Add CRM developer scenarios research design
  ([`68f45fc`](https://github.com/Gharib89/crm/commit/68f45fcba24525f3195a353a1e9c90f4ca421cd1))

- **workflow**: Add duplicate-definition detection recipe
  ([#165](https://github.com/Gharib89/crm/pull/165),
  [`ecde5ce`](https://github.com/Gharib89/crm/commit/ecde5ce6132788d2a25a7716fcb4b18f16a64364))

### Features

- **workflow**: Add delete verb resolving activation records to their definition
  ([#164](https://github.com/Gharib89/crm/pull/164),
  [`7f5d6de`](https://github.com/Gharib89/crm/commit/7f5d6de5b894f97ca8cbaee22cba2a0031bd8782))


## v2.2.0 (2026-06-10)

### Documentation

- Add ADR 0003 (Web API only, no SOAP) + workflow record glossary
  ([`1ab94e1`](https://github.com/Gharib89/crm/commit/1ab94e13764f870438a5b06e1ebd4b936fb008ee))

### Features

- **workflow**: Auto-resolve activation-record GUIDs to parent definition
  ([#176](https://github.com/Gharib89/crm/pull/176),
  [`8d966c7`](https://github.com/Gharib89/crm/commit/8d966c7250838b5ca1b0408bc5998ce9637a697e))


## v2.1.2 (2026-06-09)

### Bug Fixes

- **solution**: Validate --against-org detects BPF stage GUID collisions in Workflows XAML
  ([#163](https://github.com/Gharib89/crm/pull/163),
  [`7059231`](https://github.com/Gharib89/crm/commit/70592315eddfe050d80a9f9f2842d0092f7efa4c))

### Documentation

- **out-of-scope**: Reject plugin step clone-from-existing
  ([#169](https://github.com/Gharib89/crm/pull/169),
  [`f734374`](https://github.com/Gharib89/crm/commit/f73437454417a5a59121941d324a09b7d016998c))


## v2.1.1 (2026-06-09)

### Bug Fixes

- **query**: Detect unsupported OData 'in' operator, redirect to In function
  ([#174](https://github.com/Gharib89/crm/pull/174),
  [`7c6b2b3`](https://github.com/Gharib89/crm/commit/7c6b2b3c3eec388e6c6ca02b219b4147871cd9c0))

### Documentation

- **triage**: Record solution-clone as out-of-scope
  ([#166](https://github.com/Gharib89/crm/pull/166),
  [`d3e211d`](https://github.com/Gharib89/crm/commit/d3e211d9840fbecb7c18fbba330dcaf493b6e6da))


## v2.1.0 (2026-06-09)

### Features

- Entity delete emits deactivate-parent hint on workflow activation-record GUID
  ([#173](https://github.com/Gharib89/crm/pull/173),
  [`009732c`](https://github.com/Gharib89/crm/commit/009732c0d5cb0a13504d9f7b52ca5bc3bd84f6b9))

- Entity delete emits deactivate-parent hint on workflow activation-record GUID (#161)
  ([#173](https://github.com/Gharib89/crm/pull/173),
  [`009732c`](https://github.com/Gharib89/crm/commit/009732c0d5cb0a13504d9f7b52ca5bc3bd84f6b9))

### Refactoring

- Gate workflow import on exact code; fix overpromising delete-hint doc
  ([#173](https://github.com/Gharib89/crm/pull/173),
  [`009732c`](https://github.com/Gharib89/crm/commit/009732c0d5cb0a13504d9f7b52ca5bc3bd84f6b9))

- Lazy-import workflow module on entity delete error path
  ([#173](https://github.com/Gharib89/crm/pull/173),
  [`009732c`](https://github.com/Gharib89/crm/commit/009732c0d5cb0a13504d9f7b52ca5bc3bd84f6b9))


## v2.0.2 (2026-06-09)

### Bug Fixes

- Workflow activate/deactivate hint resolves parent draft on activation-record GUID
  ([#160](https://github.com/Gharib89/crm/pull/160),
  [`ada1bb5`](https://github.com/Gharib89/crm/commit/ada1bb506424391dffd16fc4377ebf7b2a7d1255))


## v2.0.1 (2026-06-09)

### Bug Fixes

- Register_step binds sdkmessageprocessingstep lookups with lowercase nav-property names
  ([#159](https://github.com/Gharib89/crm/pull/159),
  [`768231b`](https://github.com/Gharib89/crm/commit/768231be3820b37114dc4efcf9c1b77e0083b83d))


## v2.0.0 (2026-06-09)

### Documentation

- **plan**: Profile & credential UX revamp implementation plan
  ([`ebf862b`](https://github.com/Gharib89/crm/commit/ebf862b44e0aae7c907384630464ee9d1f91a048))

- **spec**: Crm update command + startup update nudge design
  ([`93b4281`](https://github.com/Gharib89/crm/commit/93b428156ad910ff042af13f240fc197198c34b4))

- **spec**: Profile & credential UX revamp design (v2.0.0)
  ([`1beca81`](https://github.com/Gharib89/crm/commit/1beca812d1101dfc27e0896a74ff94bef48c6f59))

### Features

- Profile-first credential UX — replace init/connect, drop .env/env-var creds
  ([#171](https://github.com/Gharib89/crm/pull/171),
  [`fa0df11`](https://github.com/Gharib89/crm/commit/fa0df11393089fa8024b413d80be686da106153a))

### Breaking Changes

- `crm init` and `crm connection connect` (plus the migrated/removed `profiles` / `disconnect` /
  `set-password` / `delete-password` verbs under `connection`) are removed — use `crm profile
  add|use|list|edit|rm|set-password|delete-password`. `.env` autoload and every `D365_*` / `CRM_*`
  credential env var (and the `CRM_AUTH_SCHEME` override) are no longer read. `CRM_HOME` is the only
  retained env knob.


## v1.9.0 (2026-06-09)

### Features

- **skill**: Split SKILL.md into thin router + on-demand reference files
  ([#158](https://github.com/Gharib89/crm/pull/158),
  [`d1be79a`](https://github.com/Gharib89/crm/commit/d1be79a9f0b8ed4290769f82358665dcdf6b7fbc))


## v1.8.0 (2026-06-09)

### Bug Fixes

- **clone**: Consolidate publish_all to single call after all opt-in layers
  ([#157](https://github.com/Gharib89/crm/pull/157),
  [`737f382`](https://github.com/Gharib89/crm/commit/737f382f300f3ba0e7a07558413485efe9428a2f))

### Documentation

- Update stale opt-in layer text to include charts
  ([#157](https://github.com/Gharib89/crm/pull/157),
  [`737f382`](https://github.com/Gharib89/crm/commit/737f382f300f3ba0e7a07558413485efe9428a2f))

### Features

- **metadata**: Add --with-charts to clone-entity ([#157](https://github.com/Gharib89/crm/pull/157),
  [`737f382`](https://github.com/Gharib89/crm/commit/737f382f300f3ba0e7a07558413485efe9428a2f))

- **metadata**: Add --with-charts to clone-entity (#153)
  ([#157](https://github.com/Gharib89/crm/pull/157),
  [`737f382`](https://github.com/Gharib89/crm/commit/737f382f300f3ba0e7a07558413485efe9428a2f))


## v1.7.0 (2026-06-09)

### Documentation

- **plans**: Add export-spec and cli-robustness implementation plan
  ([`473d5a7`](https://github.com/Gharib89/crm/commit/473d5a7ef3f7395ced807f63b7a2912248abb5f7))

### Features

- Add crm form command group (list/clone/export) ([#156](https://github.com/Gharib89/crm/pull/156),
  [`f180386`](https://github.com/Gharib89/crm/commit/f180386fd3ca0cfb76602c1c0fdd2d4b02d026cd))


## v1.6.1 (2026-06-09)

### Bug Fixes

- Export-spec warns on dropped columns; Windows UTF-8 output; metadata-update hint; apply adds
  referenced option sets
  ([`4816f97`](https://github.com/Gharib89/crm/commit/4816f97e26fded9bc8b062771307b7cce7772e30))


## v1.6.0 (2026-06-08)

### Chores

- Add .worktrees/ to .gitignore
  ([`2a01d5d`](https://github.com/Gharib89/crm/commit/2a01d5d7b63d3fad28f9ae76c9d2f018826eb15d))

### Features

- **metadata**: Add clone-entity command ([#143](https://github.com/Gharib89/crm/pull/143),
  [`a20ed6e`](https://github.com/Gharib89/crm/commit/a20ed6ef2283d84254d5592d84c3c76032ba5ce7))


## v1.5.0 (2026-06-08)

### Bug Fixes

- **workflow**: Address Copilot round-1 findings ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Address Copilot round-2 findings ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Address Copilot round-3 findings ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

### Documentation

- **plan**: Implementation plan for crm workflow clone/export/import
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Design for crm metadata clone-entity ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Design for crm workflow clone/export/import
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Link clone-entity spec to follow-up #151
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **spec**: Sequence #144 before #143; --with-workflows back in scope
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Document clone/export/import ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

### Features

- **workflow**: Add 'workflow clone' command ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add 'workflow export' and 'workflow import' commands
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add clone, export, and import commands (#144)
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add export_workflow/import_workflow round-trip
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add get_workflow definition retrieval
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Add retarget_xaml transform for clone
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

- **workflow**: Clone_workflow_to_entity with tiered category guard
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))

### Testing

- **workflow**: Align clone with live-org field requirements
  ([#152](https://github.com/Gharib89/crm/pull/152),
  [`a4f9fcd`](https://github.com/Gharib89/crm/commit/a4f9fcdd9ec71549e8fff82b457d298da8599c68))


## v1.4.0 (2026-06-08)

### Bug Fixes

- **ribbon**: Add dry-run support, fix pyright basic pragma in tests
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Fix remaining pyright errors in test_ribbon.py
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Guard empty slugify, replace assert with if-guard, remove unused code
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Guard None solution before _load_solution_ribbon_diff
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Harden ZIP decode, improve error messages, fix bundler spec
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

### Documentation

- **plan**: Implementation plan for crm ribbon command group
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: How-to, README capability, SKILL entry
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **spec**: Design for crm ribbon command group ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **spec**: Verify ribbon decode + group ids against live org
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

### Features

- **ribbon**: Add crm ribbon command group (#142) ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Add_custom_action injects button + command nodes
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Apply_ribbon_change export->validate->import->publish
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Customizations.xml entity + RibbonDiffXml navigation
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Decode RetrieveEntityRibbon CompressedEntityXml zip
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Group mapping + deterministic button id helpers
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Parse custom buttons from RibbonDiffXml
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Remove_custom_action drops action + orphaned command
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Retrieve_entity_ribbon via inline-literal RetrieveEntityRibbon
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon add-button with webresource pre-flight
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon export command + group registration
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon list reads custom buttons from solution
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))

- **ribbon**: Ribbon remove with destructive confirm
  ([#150](https://github.com/Gharib89/crm/pull/150),
  [`2f20ed3`](https://github.com/Gharib89/crm/commit/2f20ed3bfa18bd6667d026c0c800f4b7dee02216))


## v1.3.0 (2026-06-08)

### Chores

- **plan**: Add the plan file for issue 140
  ([`a7c26ec`](https://github.com/Gharib89/crm/commit/a7c26ecaab2e8202754f43a42586f05aa1ffeba0))

### Documentation

- **spec**: Design for crm solution validate ([#141](https://github.com/Gharib89/crm/pull/141),
  [`361510a`](https://github.com/Gharib89/crm/commit/361510a41305ef1e7785b53be3fb4b658c7ab05a))

### Features

- **solution**: Add crm solution validate for offline pre-import checks
  ([#141](https://github.com/Gharib89/crm/pull/141),
  [`4b1806a`](https://github.com/Gharib89/crm/commit/4b1806a37182e57cab8174844d3f1dfe3711a3aa))


## v1.2.0 (2026-06-08)

### Features

- **keyring**: Promote keyring to core dependency
  ([`3500745`](https://github.com/Gharib89/crm/commit/3500745df1f3c4f58bc6f7ba128a24936e2a51c0))


## v1.1.1 (2026-06-08)

### Bug Fixes

- **solution**: Omit ImportJobId on on-prem; recover server-assigned id from asyncop
  ([`2870969`](https://github.com/Gharib89/crm/commit/287096939e52f227f6d005afce86d47b8d993d40))

- **solution**: Suppress false Pyright unused-function warning on _import_job_id_rejected
  ([`ec37686`](https://github.com/Gharib89/crm/commit/ec376860d9184cfd5658f057ea79be198954675f))

### Documentation

- **install**: Add uv tool install option for ASR-blocked machines
  ([`bed9523`](https://github.com/Gharib89/crm/commit/bed952374cadfae49ae53abc01575858441cef86))


## v1.1.0 (2026-06-07)

### Documentation

- **connection**: Document 'set-password' for storing profile secrets
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))

- **plan**: Add set-password (#137) implementation plan
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))

### Features

- **connection**: Add 'set-password' to store a secret for any profile
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))

- **connection**: Add 'set-password' to store a secret for any profile (#137)
  ([#139](https://github.com/Gharib89/crm/pull/139),
  [`1dc7387`](https://github.com/Gharib89/crm/commit/1dc73876c89c839e70807988a0d8331f8cb3219d))


## v1.0.0 (2026-06-07)

### Documentation

- **claude**: CHANGELOG is PSR-owned, don't hand-edit
  ([`68b3101`](https://github.com/Gharib89/crm/commit/68b3101f362455dc7fe29c7b8e8792ba5d649c3d))

### Features

- **release**: Graduate to 1.0.0 with configure-once credentials
  ([#130](https://github.com/Gharib89/crm/pull/130),
  [`7be46b0`](https://github.com/Gharib89/crm/commit/7be46b03d5691fb39c5cdb023ad52da110421922))

### Breaking Changes

- **release**: Secrets can now be persisted (opt-in keyring/plaintext); profile resolution restores
  the session active_profile when no --profile is given. Flips allow_zero_version so PSR cuts
  v1.0.0.


## v0.13.1 (2026-06-06)

### Bug Fixes

- **repl**: Keep --session sticky across REPL command lines
  ([#128](https://github.com/Gharib89/crm/pull/128),
  [`a53577b`](https://github.com/Gharib89/crm/commit/a53577bc051405672e31606e32f9df8afc7b1b8d))


## v0.13.0 (2026-06-06)

### Features

- **solution**: Add 'solution dependencies' uninstall-blocker read
  ([#116](https://github.com/Gharib89/crm/pull/116),
  [`27b401f`](https://github.com/Gharib89/crm/commit/27b401f9949ed6bdc0f81b82189203d8051e4540))

## [0.12.0] — 2026-06-07

### Security
- Profile and session names are now validated as single path components at creation/load, preventing path traversal in on-disk profile/session/cache paths. (#126)

### Fixed
- `crm --profile <missing>` now emits the standard `{ok:false, ...}` error envelope (exit 1) instead of a raw `FileNotFoundError` traceback (#109).
- `async`: `list_async_operations` / `list_all_async_operations` now normalize the `owner_id` GUID filter to canonical form (braced/uppercase/urn inputs were sent to Dataverse verbatim). (#120)

### Added
- `metadata export-spec <logical_name> [--with-views] [--with-relationships] [-o FILE]` —
  project a live entity into the `crm apply -f` desired-state spec (round-trip:
  export-spec → apply re-creates the entity). Pure GETs. Without `-o` the spec is
  emitted under the standard JSON envelope; with `-o` the bare YAML is written
  directly to FILE. The projection is always re-appliable: a string column whose
  live format is `Json`/`RichText` (which `apply` cannot create) is exported
  without `format_name` and re-created as `Text`, and a string/decimal column
  whose deep read lacks the mandatory `max_length`/`precision` (sparse reads) is
  skipped rather than emitted unappliable (#92).
- `solution import` result (real and dry-run) now includes a `managed` field
  (`true` = managed solution, `false` = unmanaged, `null` when undeterminable)
  sniffed from `solution.xml` inside the zip. The sniff is best-effort and never
  blocks the import (#91).

## [0.11.0] — 2026-06-06

**Added**
- `crm scaffold table DISPLAY --column 'DISPLAY:KIND[:opts]' ...` — create an
  entity + N columns in a single publish by building a one-entity in-memory spec
  and running it through the `apply` engine. Column shorthand: `KIND` is one of
  `string`, `memo`, `integer`, `bigint`, `decimal`, `double`, `money`, `boolean`,
  `datetime`, `picklist`, `multiselect`, `lookup`, `image`, `file`; each resource
  is created with `if_exists=skip` (re-running is a no-op); honors global
  `--dry-run` and `--stage-only`; requires a `publisher_prefix` on the active
  profile (#90).
- Append-only JSONL audit journal of every mutating command. On success, each
  mutating CLI verb (entity create/update/upsert/delete/associate/disassociate/
  set-lookup/clear-lookup; all metadata create/update/delete-*; solution create/
  create-publisher/set-version/add-component/remove-component/publish/publish-all/
  import/job-cancel; batch; workflow activate/deactivate/run; action invoke;
  webresource create/update; app create/add-components/build-sitemap/set-sitemap;
  view create; data import; plugin register-assembly/register-step/
  unregister-assembly/unregister-step; security assign-role; async cancel; apply)
  appends one JSON line to
  `${CRM_HOME:-~/.crm}/audit/<session>.jsonl`. Each line carries: `ts` (ISO-8601
  UTC), `profile`, `command`, `target`, `solution`, `staged`, `dry_run`, `ok`, and
  `result_id`. Read/query/get/list/export verbs never write to the journal.
  `--dry-run` previews are journaled with `dry_run: true` so they are never
  mistaken for real changes. The request payload is never stored (#89).
- `crm session audit [--tail N] [--session NAME]` prints the current (or a named)
  session's audit journal; honors `--json` (#89).
- Opt-in, persistent, read-only on-disk cache of entity definitions, per connection
  profile, to speed up repeated one-shot agent invocations. Enable with
  `--cache-metadata` (global flag) or `CRM_CACHE_METADATA=1` (truthy: `1`/`true`/`yes`/`on`).
  Force a one-shot refresh with `--refresh-metadata`. When the cache is active,
  `crm metadata entities` emits `meta.cache` = `"hit"` | `"miss"` | `"refreshed"` in
  both `--json` and human output. Cache-mode caveat: with `--cache-metadata` the
  command returns only the 2-field rows (LogicalName / EntitySetName); the full
  5-field listing is unchanged when the flag is absent. `--custom-only` is
  incompatible with `--cache-metadata` (the cache lacks the custom flag) and errors
  (exit 2). `--top` works (client-side slice). Cache files live at
  `<CRM_HOME or ~/.crm>/cache/<profile-name>/entitydefs.json`. The cache stores
  the `{logical, set_name}` list plus the source `url`, `api_version`, and
  `cached_at` timestamp; a url/api_version mismatch is treated as a miss.
  Invalidation: any successful metadata write (entity/attribute/optionset/relationship
  create/update/delete, and publish-all/publish-xml) deletes the profile's cache
  file so a stale cache cannot outlive a schema change; a ~15-minute TTL backstop
  also forces a refresh. Cache misses and read errors degrade gracefully (fall back to
  a live fetch). Read-only schema only — records and secrets are never cached.
  When launched with `--cache-metadata`, the REPL's entity-name completion is
  served from the same on-disk cache. `crm metadata cache-clear` deletes the active
  profile's cache file; emits `{"cleared": true|false}` (#88).
- `crm entity get` and `crm metadata attribute` gain a repeatable `--expect
  ATTR=VALUE` flag — a field-comparison verify primitive. Each pair is split on
  the FIRST `=` (so a VALUE may itself contain `=`); every pair must match
  `str(record[ATTR]) == VALUE` (AND-gate, a missing key never matches). The first
  mismatch (CLI order) exits 1 with `{ok:false, error:"Expectation failed: …",
  meta:{attr, expected, actual}}` (`actual` is the raw value); all match passes
  through unchanged as `ok:true` (exit 0). For `entity get` the check runs against
  the full record before any `--minimal` projection. A malformed `--expect` (no
  `=`, or empty attr) is a usage error (exit 2) raised before any server call.
  Enables a create→publish→verify loop, e.g. `metadata add-attribute … &&
  solution publish-all && metadata attribute <entity> <attr> --expect
  AttributeType=String` (#86).
- `--minimal` on `crm query odata` / `fetchxml` / `saved` / `user` and
  `crm entity get` strips OData annotation keys (any key containing `@`:
  `@odata.etag`, `*@OData.Community.Display.V1.FormattedValue`,
  `*@Microsoft.Dynamics.CRM.lookuplogicalname`) from each record in `--json`
  output, keeping business fields, `_*_value` lookup GUIDs, and the primary id.
  Shallow prune (top-level record keys only; expanded records under `--expand`
  are untouched), and the `value`-list envelope (`@odata.count` / `@odata.nextLink`
  / `@odata.context`) is preserved. No-op in human/table mode. Raw output remains
  the default (non-breaking) — a token-efficient projection for agents (#85).
- `--retry-on-ambiguous` root flag (env: `CRM_RETRY_ON_AMBIGUOUS`) re-enables
  auto-retry of non-idempotent `POST` creates on transport error / `429` / `503`,
  opting back into the duplicate-create risk (#84).
- `crm solution components <name> --save <path>` writes a normalized component
  inventory (a bare JSON list, each entry `{"componenttype": int, "objectid": str,
  "rootcomponentbehavior": int|null}`) to `<path>`, creating parent dirs as needed.
  Emits `{"saved": "<path>", "count": N}`. `--diff <expected.json>` fetches live
  components and compares them against the saved file; exits non-zero (1) on drift.
  Components are keyed on the tuple `(componenttype, objectid, rootcomponentbehavior)`
  after normalisation — `missing` = in expected but not live, `unexpected` = in live
  but not expected. The two flags are mutually exclusive; bare `components <name>` is
  unchanged. The round-trip `--save` then `--diff` against the same org reports no
  drift (#82).
- `crm metadata picklist` and `crm metadata get-optionset` now emit a flattened
  `meta.options` list (`[{value, label}]`) in `--json` mode, so agents need not
  dig through `Label.UserLocalizedLabel.Label`. The raw `data` is unchanged (no
  contract break). Labels resolve via `UserLocalizedLabel` then `LocalizedLabels`.
  Boolean attributes have no `Options` array (`TrueOption` / `FalseOption`
  instead), so `meta.options` is empty for them — read those raw fields directly (#76).
- `crm security` command group: list and assign Dynamics 365 security roles.
  `list-roles` lists all security roles (optionally filtered to a business unit
  via `--business-unit GUID`). `list-user-roles USER_ID` and
  `list-team-roles TEAM_ID` return the roles currently assigned to a system user
  or team (both positional args are GUIDs). `assign-role ROLE_ID` assigns a
  security role to a user (`--to-user GUID`) or a team (`--to-team GUID`) —
  exactly one target flag is required. Assignment is cumulative and not cleanly
  reversible, so the command is gated by an interactive confirmation prompt
  (bypass with `--yes`) and the destructive-op PreToolUse hook. Standard
  admin-header options (`--as-user`, `--as-user-object-id`,
  `--suppress-dup-detection`, `--bypass-plugins`) are available on `assign-role`
  (#83).
- `crm metadata dependencies <target>` is a new read-only command that returns
  `can_delete` (bool) plus a `blockers[]` list for any metadata component. `--kind`
  selects the component type (`entity` / `attribute` / `optionset` / `relationship`;
  default `entity`); attribute targets use dotted notation (`entity.attribute`).
  `--for delete` (default) calls `RetrieveDependenciesForDelete` and lists what
  would block deletion; `--for dependents` calls `RetrieveDependentComponents` and
  lists what currently depends on the target (#81).
- `--check-dependencies` flag (default off) on `metadata delete-entity`,
  `delete-attribute`, `delete-relationship`, and `delete-optionset`: folds
  `can_delete` and `blockers[]` into the result via a pre-delete
  `RetrieveDependenciesForDelete` call. Pair with `--dry-run` for a non-destructive
  dependency preview without issuing the DELETE (#81).

**Changed**
- Extracted the destructive-verb classification (`DESTRUCTIVE`, `ROLE_VERBS`,
  `is_destructive()`) into a new dependency-free `crm/core/destructive.py`. The
  PreToolUse destructive-op gate (`.claude/hooks/destructive_op_gate.py`) keeps
  its standalone stdlib copy so it stays import-free and offline on every Bash
  call; a new `crm/tests/test_destructive_sync.py` asserts the two copies stay
  aligned (#87).
- Non-idempotent `POST` record-creates (and actions) are no longer auto-retried on
  transport error / `429` / `503` by default — a lost response may have already
  committed the record, so a blind re-send risks a duplicate (#84). Idempotent verbs
  (`GET`/`PUT`/`PATCH`/`DELETE`) are unchanged, and `$batch` keeps its own independent
  retry loop. Pass `--retry-on-ambiguous` (or set `CRM_RETRY_ON_AMBIGUOUS`) to restore
  the old behavior when the re-send risk is acceptable.

**Fixed**
- `crm metadata delete-entity`, `delete-attribute`, `delete-relationship`, and
  `delete-optionset` under `--dry-run` now return
  `{"_dry_run": true, "would_delete": true, ...}` instead of falsely reporting
  `{"deleted": true, ...}` (#81).
- `crm metadata picklist` (human/table mode) now resolves option labels via the
  full `UserLocalizedLabel` → `LocalizedLabels` fallback, matching `--json`
  `meta.options`. Previously the table read `UserLocalizedLabel` only, so an
  option whose label is carried solely via `LocalizedLabels` rendered blank (#76).

## [0.10.0] — 2026-06-05

**Added**
- `crm plugin` command group: register and manage Dynamics 365 plug-in assemblies
  and processing steps via the `pluginassemblies` / `plugintypes` /
  `sdkmessageprocessingsteps` Web API entity sets.
  `register-assembly PATH` uploads a `.dll` as a plug-in assembly (base64-encoded
  into `content`); `--update` re-uploads the binary of an existing assembly by name
  without touching its identity metadata. `list-types` lists platform-generated
  plug-in types (`typename`, `friendlyname`, `plugintypeid`), optionally filtered
  by `--assembly NAME`. `register-step` registers an `sdkmessageprocessingstep`
  bound to a message (`--message Create/Update/…`) and a fully qualified type
  (`--plugin-type`); stage (`prevalidation`/`preoperation`/`postoperation`) and
  mode (`sync`/`async`) are configurable — async forces `postoperation` (other
  combinations are rejected). The step name is auto-derived as
  `<typename>: <message> of <entity>` (pass `--name` when the derived string would
  exceed 256 chars). `unregister-assembly ASSEMBLY` cascades — it deletes dependent
  steps first, then the assembly. `unregister-step STEP` deletes a step by name or
  GUID; an ambiguous name errors. `--solution` sends `MSCRM.SolutionUniqueName` on
  the writes. `--dry-run` skips all writes (resolution GETs still fire). `--json`
  mode returns the standard `{ok, data, meta}` envelope (#80).
- `crm app build-sitemap <SITEMAP_NAME>` builds a valid SiteMapXml from
  structured input and creates the sitemap. Three repeatable options describe the
  tree: `--area 'id[:Title]'` (at least one required), `--group 'areaId/groupId[:Title]'`
  (nested under an area), and `--subarea 'areaId/groupId:entity=<logical>[:Title]'`,
  where a SubArea binds a table via the SiteMapXml `Entity=` attribute (Title
  optional — omit it and the platform derives the label from the entity). SubArea
  Ids are auto-allocated from the entity logical name to stay unique across the
  document, and every attribute value is XML-escaped; Area/Group Ids and the
  references between them are validated, so broken references or duplicate Ids
  fail with an error. After building the XML it delegates to the `set-sitemap`
  path so the POST body is byte-identical, then optionally publishes. `--unique-name`
  sets `sitemapnameunique` to auto-associate the sitemap with that app (same as
  `set-sitemap`); `--publish/--no-publish` (default: publish) runs PublishAllXml
  after creation. `crm --dry-run app build-sitemap ...` prints the generated
  SiteMapXml and issues no POST. Complements `set-sitemap`, which uploads a
  pre-built XML file (#79).
- `crm webresource create` / `update` / `get` / `list` manage web resources
  (`webresourceset`). `create` base64-encodes the `--file` bytes into the
  `content` column and infers the `webresourcetype` from the file extension
  (`.html`=1, `.css`=2, `.js`=3, `.xml`=4, `.png`=5, `.jpg`=6, `.gif`=7,
  `.xap`=8, `.xsl`=9, `.ico`=10, `.svg`=11, `.resx`=12 — the real D365
  `webresource_webresourcetype` option set, so CSS=2 and 8 is Silverlight, not
  the other way around); `--type <int>` overrides inference and an unknown
  extension without it is rejected. `--display-name` defaults to the name.
  `update <name>` resolves the resource by name and issues a plain PATCH of only
  the sent fields (content from `--file` and/or `--display-name`; at least one
  required) — not retrieve-merge-write. Both honor `--solution` (sent as
  `MSCRM.SolutionUniqueName`) and publish after the write (`--no-publish` /
  `--stage-only` suppress it). `list --custom-only` keeps unmanaged resources;
  `get <name>` prints a record (#78).
- `crm app create --icon-webresource <name|guid>` sets the app icon to a web
  resource: a GUID is used directly, a name is resolved to its id, and omitting
  the flag keeps the platform default icon (#78).
- `crm data import <ENTITY_SET> <INPUT_FILE>` bulk-imports records via the
  Dataverse `$batch` endpoint — the only on-prem bulk mechanism (`CreateMultiple`
  / `UpsertMultiple` are cloud-only). Supports JSONL and CSV input (format
  inferred from the file suffix; override with `--format`). Two modes:
  `--mode create` (POST, default) and `--mode upsert` (PATCH by GUID via
  `--id-column`). Records are sent in chunks of `--chunk-size` (default 100);
  each chunk is a transactional changeset (atomic, all-or-nothing) by default.
  `--no-transaction` sends each operation as a top-level batch operation instead.
  `--continue-on-error` sends `Prefer: odata.continue-on-error` to skip past
  individual failures — it requires `--no-transaction` (a changeset is itself
  all-or-nothing; combining the two is rejected with a usage error). CSV values
  are coerced best-effort (empty→null, `true`/`false`→bool, integers, floats);
  non-finite tokens (`NaN`/`inf`) and integer-looking strings (`"007"`) are not
  preserved — use JSONL for IDs, postal codes, and lookup binds. Dry-run via the
  global `crm --dry-run data import ...` produces zero writes; the summary carries
  `dry_run: true`. Output: `{imported, failed, chunks, entity_set, mode, dry_run,
  format}`; `failed > 0` surfaces a `meta.warnings` advisory; exit code is 0 on
  partial failure, consistent with `crm batch` (#75).
- `crm connection doctor` (also exposed as the top-level alias `crm doctor`)
  runs a live, ordered connection probe and renders a five-line checklist:
  `dns_tcp`, `tls`, `version` (the configured `api_version`), `auth`, and an
  informational `rate_limit`. Each layer's failure is classified distinctly
  (DNS vs TCP vs TLS vs wrong api_version vs 401/403) with an actionable hint.
  It is a read-only diagnostic — it never negotiates or mutates the profile, and
  the raw GETs run regardless of `--dry-run`. `--json` emits
  `{ok, data:{checks:[{check,ok,detail,hint}]}}`; overall `ok` (and the exit
  code) is the AND of the four diagnostic checks, `rate_limit` never affects it
  (#74).
- `crm solution extract` and `crm solution pack` bridge the CoreTools
  `SolutionPackager.exe` to turn an exported solution zip into a source-controllable
  folder tree and back (`git diff` on the extracted tree _is_ the solution diff).
  These are **offline** local-file transforms: no connection, profile, or backend
  is required. `--package-type` selects `Unmanaged` (default) / `Managed` / `Both`;
  the executable is resolved via `--solutionpackager-path` → `CRM_SOLUTIONPACKAGER`
  env → `PATH`, and an absent binary fails with an actionable error naming the
  `Microsoft.CrmSdk.CoreTools` NuGet package (no bundling or auto-download). The
  subprocess honors `--timeout` and the emitted envelope carries
  `{action, exit_code, folder, zipfile, stdout_tail}`; a non-zero SolutionPackager
  exit fails the command (#73).
- `crm entity create` and `crm entity update` accept an opt-in `--validate` flag
  that field-name-checks the payload before the write. It runs 1-3 read-only
  metadata GETs (resolve entity-set → logical name, the entity's attribute names,
  and the ManyToOne navigation-property names), then flags any payload key absent
  from the union with a `did_you_mean` suggestion. `<nav>@odata.bind` deep-link
  keys validate against the nav-name union, so a bound lookup is not a false
  positive. On a miss the write is blocked with
  `{ok:false, meta:{unknown_fields, did_you_mean}}`. Composable with `--dry-run`
  (the validation GETs run for real even under dry-run). Scope is field-NAME only;
  option-set values are not checked (#72).
- `crm solution add-component` and `crm solution remove-component` add or remove
  an existing component to/from an unmanaged solution via the `AddSolutionComponent`
  / `RemoveSolutionComponent` Web API actions. `--type` accepts a `componenttype`
  integer or a friendly name (`entity`, `attribute`, `relationship`, `optionset`,
  `webresource`, … — names are case- and separator-insensitive; pass a raw int for
  any type not in the map). Both pre-flight `solution_info` and refuse a managed
  target client-side. `add-component` is non-destructive and supports
  `--no-add-required` (`AddRequiredComponents: false`) and `--no-subcomponents`
  (`DoNotIncludeSubcomponents: true`). `remove-component` is gated as a destructive
  operation: it prompts for confirmation (aborting cleanly in a non-TTY context
  unless `--yes`), and the verb-name PreToolUse hook blocks it without `--yes` (#71).
- `crm solution import` now parses the import job's `data` column into a
  solution-level `result` (`success`/`warning`/`failure`) plus a `components`
  list (`{name, type, result, errorcode?, errortext?}` per imported component).
  Any non-success component adds a `meta.warnings` note, so a partial failure
  under an overall-succeeded async op (`status: succeeded`) is no longer masked.
  `crm solution import-result <import_job_id>` re-fetches a completed job and
  runs the same parser to verify a prior import without re-importing. Both accept
  `--formatted` to also attach the Excel-format `RetrieveFormattedImportJobResults`
  report verbatim under `formatted_results` (opt-in, a separate round-trip) (#70).
- `crm metadata delete-attribute <entity> <attribute>` and
  `crm metadata delete-relationship <schema-name>` delete a custom column or a
  custom relationship (1:N or N:N). Both pre-flight against the metadata to refuse
  managed and non-custom targets client-side; `delete-attribute` additionally
  refuses primary (id/name) and sub-attribute (`AttributeOf`-set) columns. Each
  honors `--solution` (sent as `MSCRM.SolutionUniqueName`) and is gated as a
  destructive operation: each prompts for confirmation, aborting cleanly in a
  non-TTY context unless `--yes` is passed, and the verb-name PreToolUse hook
  blocks them without `--yes`. Remaining-dependency conflicts are left to the
  server's 4xx (#69).
- `crm metadata describe <entity>` returns a one-shot, read-only write-readiness
  brief: the entity set name, primary id/name, and every writable attribute with
  its required level. Lookups carry `bind_key` (`<Nav>@odata.bind`, self-derived
  from `ManyToOne` relationship metadata) plus `targets[]` with both the logical
  name and the `EntitySetName` so the bind VALUE is usable; picklist / state /
  status attributes carry inline `{value, label}` options, and a picklist bound to
  a global option set also carries its `global_optionset_id` GUID (which on-prem
  9.1 needs to bind on create). Built from pure GETs, gated so only the attribute
  kinds an entity actually uses cost a round-trip (#68).
- `crm solution import` is now gated as a destructive operation: an overwrite
  import (the default) clobbers unmanaged customizations in the target org, so it
  prompts for confirmation and, in a non-TTY context, aborts cleanly (exit 1;
  under `--json` the body is the standard `{"ok": false, "error": "aborted by
  user"}` envelope, otherwise a human-formatted error) unless `--yes` is passed.
  The PreToolUse destructive-op gate also blocks any `crm solution import` without
  `--yes` (verb-only, so a `--no-overwrite` import is gated too — any import
  mutates the org). Default import semantics are unchanged (#67).
- `crm solution set-version <unique_name>` updates an unmanaged solution's
  `version` / `friendlyname` / `description` in place. At least one field is
  required and `--version` is validated as 4-part dotted numeric before any HTTP;
  managed solutions and patches are rejected client-side (the server returns
  `CannotUpdateSolutionPatch` for a patch). Delegates to the shared record-update
  path, so `If-Match:*` and `--dry-run` are reused with no new HTTP path (#66).
- Non-interactive REPL guard: bare `crm` (no subcommand) now fails fast with
  exit 2 and a usage message pointing at `crm --help` whenever the caller is
  non-interactive — under `--json`, with `CRM_NO_REPL` set (`1`/`true`/`yes`/`on`),
  or when stdin is not a TTY (piped/redirected, as agents and CI invoke it). Under
  `--json` the message is the standard `{ok:false,error}` envelope; otherwise it
  goes to stderr. An interactive human and explicit `crm repl` still launch the
  REPL. A proactive isatty probe so an agent invocation never hangs (#65).
- Structured warnings channel: the JSON envelope now carries a `meta.warnings`
  array — the single place to scan for advisories (staged-but-unpublished,
  created-but-read-back-failed, partial-optionset). `*_lookup_error` read-back
  keys are mirrored into it (and left in `data` for back-compat), and a
  non-transactional optionset update that fails mid-stage surfaces
  `meta.completed_steps` / `meta.failed_stage` on the error envelope so a partial
  mutation is observable. Every other error site is unchanged (#64).

**Changed**
- **Breaking (envelope):** the singular `meta.warning` scalar is replaced by the
  `meta.warnings` array, so multiple advisories no longer clobber each other (#64).

**Fixed**
- `crm metadata update-optionset --dry-run` previously returned `{"updated": true, ...}`,
  incorrectly signalling a completed write. Under dry-run it now returns
  `{"_dry_run": true, "name": ..., "diff": {...}, "actions": [...]}` (#77).
  The `diff` classifies each pending change as `inserts` / `updates` (with
  `old_label` / `new_label` looked up from the live option set) / `deletes` (with
  `old_label`) / `reorder` (`{old, new}` value lists). The live GET fires for real
  to build the diff; no POSTs are issued.

## [0.9.0] — 2026-06-04

**Added**
- `crm describe [GROUP]`: machine-readable command/option/choice discovery. Walks
  the live Click tree (no live D365 connection, no mkdocs-click dependency) and in
  `--json` mode emits `{ok, data:{root_options, commands:[{name, path, help,
  is_group, args, params}]}}`. Each option carries `name, opts, secondary_opts,
  type, required, is_flag, multiple, choices, default, envvar` (`secondary_opts`
  holds the off-form of a flag-pair, e.g. `--no-publish`); `Choice` enums (ownership, the 14
  attribute kinds, `if-exists`, cascade behaviors, …) are surfaced verbatim. Root
  sticky globals (`--json`, `--dry-run`, `--profile`, `--auth-scheme`,
  `--log-level`, `--stage-only`, `--session`, …) are listed under `root_options`.
  The interactive `repl` leaf is excluded. `describe <group>` scopes to one
  subtree, importing just that module (a lazy win over the full walk) (#63).
- Agent-readable docs surface: the docs site now publishes `llms.txt` (curated
  index, per [llmstxt.org](https://llmstxt.org/)) and `llms-full.txt` (every page
  concatenated into one fetch) at the site root, generated at build time by the
  `mkdocs-llmstxt` plugin from the existing `docs/` tree. Any agent with web
  access — not just skill-aware harnesses loading `crm/skills/SKILL.md` — can pull
  the full CLI reference and how-tos from `<site>/llms-full.txt`. Adds a docs-only
  dependency on `mkdocs-llmstxt`.
- `crm apply -f spec.yaml`: declarative desired-state from a single YAML/JSON
  spec. Orchestrates the existing metadata cores in dependency order (publisher →
  solution → entities → option sets → attributes → relationships → views), each
  with `if_exists=skip`, and runs `PublishAllXml` once at the end, so re-applying
  an unchanged spec is a no-op. Emits `{ok, data:{applied, skipped, planned,
  failed}, meta:{staged}}`. Honors `--dry-run` (greenfield specs report
  dependents as planned-create instead of erroring) and `--stage-only` (create
  without publishing). Metadata POSTs are non-transactional, so a failure
  aborts-and-reports, leaving staged-but-unpublished residue. The spec is
  validated up front. Adds a runtime dependency on PyYAML (#60).
- Machine-readable error taxonomy: in `--json` mode the error envelope now carries
  `meta.category` (a closed enum: `not_found`, `auth_failed`, `forbidden`,
  `concurrency_conflict`, `duplicate_detected`, `validation`, `throttled`,
  `server_error`, `transport_error`) and `meta.retryable`, alongside the existing
  `meta.status` / `meta.code`. Classification is status-first, with two D365 error
  codes (`0x80040217` → `not_found`, `0x80040237` → `duplicate_detected`) honored
  regardless of status; `retryable` is true only for the transient classes. The
  backend auto-retries the `transport_error` / `throttled` (429) / `server_error`
  (5xx) classes, so for those `retryable` is a post-exhaustion hint;
  `concurrency_conflict` (412) is not auto-retried — the caller refetches a fresh
  ETag and retries. The status-less transport path now carries a `transport_error`
  signal, and the fragile `MissingPrivilege` message-substring synthesis is
  subsumed (403 → `forbidden`) (#62).
- Canonical `meta.dry_run` signal: in `--json` mode every dry-run invocation now
  carries `meta.dry_run: true` in the envelope. It is keyed off the invocation-level
  `--dry-run` flag (not by sniffing the data for the `_dry_run` sentinel), so
  list-shaped batch previews and poll previews are covered uniformly and forced-real
  existence-probe GETs do not false-positive. Existing `meta` keys (e.g. `staged`)
  are preserved; the in-data `_dry_run` sentinel is retained for back-compat (#61).

**Changed**
- Bundled agent skill (`crm/skills/SKILL.md`) is now fully standalone — it reads
  correctly once `crm skill install` drops it into a harness skill dir, with no
  references to files that don't ship alongside it. Install section shows the
  per-host one-liners (`install.ps1` / `install.sh`) instead of `pip install -e .`;
  removed repo-only pointers (`D365.md`, `crm/tests/TEST.md`, `README.md`,
  `docs/adr/…`, `docs/how-to/apply.md`, the `.claude/hooks` gate) in favor of
  in-CLI discovery (`crm describe`, `crm <group> --help`); broadened the
  frontmatter description to cover both on-prem (NTLM) and Dataverse online (OAuth).
- Docs examples and test fixtures now use a neutral `contoso` org/publisher prefix
  (`contoso_`, host `internalcrm.contoso.local`) instead of an internal org name,
  so the published docs and shipped `SKILL.md` carry no environment-specific names.

## [0.8.0] — 2026-06-04

**Added**
- Installer SHA-256 integrity verification: `install.sh` / `install.ps1` verify the
  downloaded archive against a published `SHA256SUMS` (uploaded per release to
  `<tag>/` and `latest/` in R2) before extracting, and abort on a mismatch or if it
  can't be fetched. `CRM_SHA256` / `$env:CRM_SHA256` pins a hash out-of-band (#46).
- Cloud impersonation by Entra ID object id via the `CallerObjectId` header:
  new `--as-user-object-id <guid>` flag (alongside `--as-user`) and
  `CRM_AS_USER_OBJECT_ID` env default, on every command that already carries
  `--as-user`. Header selection is by which input you supply, independent of
  `auth_scheme`; `--as-user` (`MSCRMCallerID`) and `--as-user-object-id`
  (`CallerObjectId`) are mutually exclusive per request (#54).
- CHANGELOG is now published on the docs site at `/changelog/`, rendered
  from this file via `mkdocs-include-markdown-plugin`.

## 0.7.0 — Fast startup + R2 install

**Performance**
- CLI subcommands and the D365 backend stack now load lazily: `crm --version` and
  direct command invocations no longer import every command module (and their
  requests/NTLM/prompt_toolkit dependencies), cutting cold startup substantially.
  `crm --help` still loads all modules (accepted trade-off).

**Changed**
- PyInstaller builds switched from `--onefile` to `--onedir` (`dist/crm/`),
  eliminating per-launch self-extraction overhead.
- Install is now a one-line script served from a public Cloudflare R2 bucket
  (`irm …/install.ps1 | iex` on Windows, `curl …/install.sh | sh` on Linux),
  replacing the private-repo GitHub release URL that 404'd for users.

**Added**
- `scripts/install.ps1` (Windows) and `scripts/install.sh` (Linux): download the
  prebuilt onedir bundle from R2, install to a user dir, wire up PATH / a symlink,
  and support uninstall.

## 0.6.0 — Spec E: DX Polish

**Refactor**
- Split `crm/cli.py` (2098 lines) into focused modules under `crm/commands/`
  (one Click group per file). Pure refactor — zero behavior change.

**Added**
- `--log-level debug|info|warning|error` + `--log-format text|json-line` on
  the root CLI group (env: `CRM_LOG_LEVEL`, `CRM_LOG_FORMAT`).
- `--verbose` flag (alias for `--log-level debug`).
- `--auth-scheme ntlm|kerberos|negotiate` on the root CLI group
  (env: `CRM_AUTH_SCHEME`). Kerberos/Negotiate via `requests_negotiate_sspi`
  (install with `pip install crm[kerberos]`).
- `crm init` command: `--template` writes `.env.example`; no args runs an
  interactive profile wizard.
- `query count <entity>` — calls `RetrieveTotalRecordCount`.
- `metadata list-actions` — parses `$metadata` and lists OData actions.
- `metadata list-functions` — parses `$metadata` and lists OData functions.
- REPL tab completion for entity-name argument slots, backed by a lazy
  in-memory `MetadataCache`.

**Changed**
- `ConnectionProfile` gains an `auth_scheme` field (default `"ntlm"`,
  backward compatible).
- `crm/utils/repl_skin.py::create_prompt_session` accepts an optional
  `completer` argument.

## [0.5.0] — 2026-05-25

### Added

- `metadata add-attribute` — add columns to existing entities. Supports 14
  attribute kinds: string, memo, integer, bigint, decimal, double, money,
  boolean, datetime, picklist, multiselect, lookup, image, file.
- `metadata create-one-to-many` + `metadata create-many-to-many` — create
  1:N and N:N relationships via the dedicated Dataverse actions.
- Global option set CRUD: `metadata list-optionsets`, `get-optionset`,
  `create-optionset`, `update-optionset`, `delete-optionset`. `update`
  is granular: `--insert-option` / `--update-option` / `--delete-option`
  / `--reorder` flags map to the matching bound actions.
- `metadata delete-entity` — drop a custom table, guarded by interactive
  confirm + `--yes` skip + client-side `IsCustomEntity` + `IsManaged`
  pre-flight check.

All new write verbs accept `--solution <uniquename>` (header
`MSCRM.SolutionUniqueName`) and `--publish/--no-publish` (default ON),
matching `metadata create-entity`. Delete verbs skip publish.

## [0.4.0] — 2026-05-25

This release lands Spec C from the post-code-review roadmap: `$batch`
support, on-prem-correct impersonation via `MSCRMCallerID`, two admin
headers for write paths, an `asyncoperations` browse surface, and
explicit optimistic concurrency via `If-Match`. See
`docs/superpowers/specs/2026-05-24-spec-c-throughput-admin-design.md`
for the full design.

### Added

- `D365Backend.batch(operations, *, transactional=True, continue_on_error=False, timeout=None)` — execute a list of operations via POST `$batch`. Consecutive writes are auto-grouped into one changeset; GETs go as top-level operations.
- `crm batch <file.json>` CLI command with `--no-transaction`, `--continue-on-error`, `--output`, `--timeout` flags.
- Backend typed kwargs on every verb: `caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`, `etag`. Env defaults: `CRM_AS_USER`, `CRM_SUPPRESS_DUP`, `CRM_BYPASS_PLUGINS`.
- Per-command CLI flags on every write/action verb: `--as-user <guid>`, `--suppress-dup-detection`, `--bypass-plugins`. `--if-match <etag>` on `entity update` and `entity delete`.
- `crm async list/get/cancel` plus `crm solution job-status / job-cancel` aliases.
- New TypedDicts: `BatchOperation`, `BatchResult`, `AsyncOperationRow`.

### Changed

- HTTP `412` responses now map to `D365Error(code="PreconditionFailed")`.
- HTTP `403` responses whose body references `prvBypassCustomPluginExecution` map to `D365Error(code="MissingPrivilege")`.

### Deferred

- `CreateMultiple` / `UpdateMultiple` / `UpsertMultiple` — Dataverse cloud only; not present on Contoso 9.1.x on-prem.
- `CallerObjectId` impersonation header — requires Microsoft Entra ID; on-prem AD users use `MSCRMCallerID`.
- Server-side `$batch` size limits (typical Dataverse: 100 changesets per batch; 1000 ops per changeset) are not enforced client-side; the server's `MaxBatchSize` / `MaxChangesetSize` error surfaces verbatim.

### Notes for callers

- `POST $batch` is retried only on `429` and `503` (Spec B conservative-POST policy). A retried batch re-sends the assembled body verbatim — idempotency is the caller's responsibility.

## [0.3.0] — 2026-05-24

This release lands Spec B from the post-code-review roadmap: a retry
layer on every HTTP call plus a switch to the asynchronous variants of
`ImportSolution` and `ExportSolution`. See
`docs/superpowers/specs/2026-05-24-spec-b-resilience-design.md` for the
full design.

### Breaking

- **`crm.core.solution.import_solution` return shape changes.** Now
  returns `{import_job_id, async_operation_id, status, progress,
  started_on, completed_on, duration_ms}`. Any caller reading the old
  ImportSolution response keys (`ImportJobKey`, etc.) must switch.
- **`crm.core.solution.export_solution` return shape gains keys.**
  New fields: `async_operation_id`, `export_job_id`, `duration_ms`. The
  existing `output`, `bytes`, `managed`, `solution` keys are preserved.
- **Both functions can now block for up to `CRM_ASYNC_TIMEOUT` seconds
  (default 1800).** The sync versions blocked for up to
  `profile.timeout` seconds per HTTP call (default 120) with no
  client-side polling.

### Added

- `D365Backend.request` now retries on `429`, idempotent `5xx`
  (`502`/`503`/`504` on `GET`/`PUT`/`PATCH`/`DELETE`; `503` only on
  `POST`), and retryable transport errors (`ConnectionError`,
  `Timeout`, `ChunkedEncodingError`). Honors `Retry-After`; falls back
  to capped exponential backoff with full jitter.
- `D365Backend.poll_async_operation(async_operation_id, *, timeout,
  import_job_id, on_progress)` — blocks until an
  `asyncoperations(<id>)` row reaches `statecode=3`. Raises
  `D365Error` on failure (`statuscode=31`), cancellation (`32`), or
  timeout.
- `ConnectionProfile` gains seven new fields: `retry_max`,
  `retry_base_delay`, `retry_max_delay`, `retry_jitter`,
  `async_poll_initial`, `async_poll_max`, `async_timeout`.
- Env overrides: `CRM_RETRY_MAX`, `CRM_RETRY_BASE_DELAY`,
  `CRM_RETRY_MAX_DELAY`, `CRM_RETRY_JITTER`, `CRM_ASYNC_TIMEOUT`,
  `CRM_NO_RETRY`. Env wins over profile.
- New CLI flags on `crm solution export` and `crm solution import`:
  `--timeout N` (override `async_timeout` for this call), `--no-retry`
  (set `CRM_NO_RETRY=1` for this call). `crm solution import` also
  gets `--quiet` / `-q` to suppress per-tick progress lines.
- `x-ms-ratelimit-*` headers are logged to stderr on every retried 429,
  and on every response under `CRM_VERBOSE=1`.

### Changed

- `crm solution import` and `crm solution export` now block until the
  async operation reports completion, emitting per-tick progress to
  stderr (import only; suppress with `--quiet`).

[0.4.0]: https://github.com/Gharib89/crm/releases/tag/v0.4.0
[0.3.0]: https://github.com/Gharib89/crm/releases/tag/v0.3.0

## [0.2.0] — 2026-05-24

This release lands Spec A from the post-code-review roadmap: nine correctness
fixes plus pyright strict (zone-scoped) across `crm/core/*` and
`crm/utils/d365_backend.py`. See
`docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md` for
the full design.

### Breaking

- **Error envelope `meta.status` and `meta.code` now emit JSON `null`** when
  absent, instead of the literal string `"n/a"`. Scripts that string-match
  `"n/a"` must switch to a null check. (§3.5)

### Added

- `--export-setting <name>` flag on `crm solution export`, repeatable.
  Accepted names: `autonumbering`, `calendar`, `customizations`,
  `email-tracking`, `general`, `isv-config`, `marketing`, `outlook-sync`,
  `relationship-roles`, `sales`. (§3.6)
- `crm/utils/d365_types.py` — `TypedDict` shapes for Web API responses.
- `pyright` (>=1.1.380) as a dev dependency and a CI step in
  `.github/workflows/build.yml`. Strict mode on `crm/core/*` +
  `crm/utils/d365_backend.py`; basic mode (via file-level `# pyright: basic`
  pragma) on `crm/cli.py`, `crm/utils/repl_skin.py`, and `crm/tests/*`.

### Changed

- `metadata create-entity` now reads `EntitySetName` back from the server
  instead of guessing it via English pluralisation. Adds one round-trip per
  create call. On read-back failure the entity is still reported as created,
  with `entity_set_name: null` and a diagnostic `entity_set_lookup_error`
  field. (§3.3)
- REPL keeps a single `D365Backend` per session instead of rebuilding on
  every command. Invalidated by `connection connect` / `connection
  disconnect`. (§3.7)
- `$count` queries parse `text/plain` directly in one HTTP call on the
  happy path. Falls back to `?$count=true` if the body is missing or
  non-numeric. (§3.9)
- `fetchxml_query` passes the FetchXML via `params=` instead of manual URL
  concatenation. No on-wire change. (§3.4)

### Fixed

- `entity create` no longer sends the non-spec `If-None-Match: null` header
  on POST. (§3.1)
- `data export` CSV no longer leaks `_value` lookup columns and `@odata.*`
  annotations into headers — `_ordered_keys` boolean precedence bug. (§3.2)
- `.env` value parser is now pair-aware: `KEY="foo's bar"` resolves to
  `foo's bar`, not `foos bar`. (§3.8)

[0.2.0]: https://github.com/Gharib89/crm/releases/tag/v0.2.0
