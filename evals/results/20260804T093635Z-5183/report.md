# skill-eval report — 20260804T093635Z-5183

## Metadata

- **model**: sonnet
- **target**: cloud
- **k**: 3
- **preset**: full
- **paired**: True
- **subset**: False
- **skill_sha**: 8fe498bb2ca8d5f55f8d0809f506bc4dc90512a0
- **reportable**: true

## Per-task results

Per-trial verdicts in order (✓ pass · ✗ fail · ⊘ cap-hit).

| task | skill trials | bare trials | skill % | bare % | Hake gain |
| --- | --- | --- | --- | --- | --- |
| authoring-chart-create | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| bulk-delete-from-list | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| bulk-delete-population | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| bulk-update-delta | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| connectionrole-create | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| customizations-view-edit | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| dup-rule-create | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-apps-scripted-app | ✓✗✗ | ✗✗✗ | 33% | 0% | +0.33 |
| feasibility-automation-workflow-activity | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-bulk-dedupe-merge | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-bulk-load-verify | ✓✓✓ | ✗✗✓ | 100% | 33% | +1.00 |
| feasibility-forms-clone-cross-entity | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-metadata-autonumber | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-metadata-field-type-change | ✗✗✗ | ✗✗✗ | 0% | 0% | +0.00 |
| feasibility-metadata-optionset-read | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-records-paging-count | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-ribbon-custom-icon | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-security-privilege-toggle | ✗✗✗ | ✗✓✓ | 0% | 67% | -2.00 |
| feasibility-solutions-managed-component-delete | ✗✗✗ | ✓✗✗ | 0% | 33% | -0.50 |
| feasibility-solutions-missing-dependency | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-solutions-unmanaged-to-managed | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feasibility-uncovered-impersonation | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| feedback-note-create | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| fieldsec-profile-create | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| records-create-verify | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| records-note-attach | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| records-reassign-parent | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| records-validate-write | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| security-assign-role-team | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |
| security-role-create | ✓✓✓ | ✓✓✓ | 100% | 100% | N/A |

## Macro

- **with-skill macro pass rate**: 88%
- **bare macro pass rate**: 88%
- **macro lift**: +0.0 pp
- **mean Hake gain** (tasks with headroom): -0.23

## Invocation vs success (skill leg)

Whether the agent loaded the `crm` skill, measured separately from whether it passed.

| | passed | failed |
| --- | --- | --- |
| invoked | 79 | 11 |
| not invoked | 0 | 0 |

## Regression vs baseline

- no reportable baseline for this series yet (advisory)

## Flipped tasks (all-pass → all-fail vs baseline)

- none
