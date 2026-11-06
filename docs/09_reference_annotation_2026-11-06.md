# Multi-selection, annotation fields, and candidate scores

> 历史详细参考：本页保留 2026-11-06 重写前的条款、示例和验收范围。旧版本措辞和测试结果不自动适用于当前版。操作请从 [现行页面](annotation_fields_and_score_guidance.md) 开始。

在 Review 中选择标签、填写备注并点击 **Save and next**；多选候选后可批量保存。评分卡区分当前筛选集内的相对排名与用户配置的绝对规则，不自行判断生物学优劣。操作入口见 [快速开始](structure_annotator_quickstart.md)，下方保留字段、存储和历史验收细节。

## 配置标注并保存

打开 **Settings → Annotations**，按需要配置单选、多选或文字字段，并指定哪些字段必填。返回 Review 后，普通点选先形成草稿；点击 **Save and next** 保存并前进。必填项不完整或写入失败时，草稿和当前位置保留。

在候选列表中用 Shift 选择范围、Ctrl 切换单行，再显式保存，可将标注应用到所选行。批量保存前会检查必填项；它不会逐个加载其他结构，也不会对未预览对象执行标签命令。**More actions → Undo last save** 撤销上一次完整保存。

需要加快单条操作时，可在 **Settings → Annotations** 启用 **Save on label click**，并独立设置保存后是否前进。单个候选的 Ctrl+click 默认保存并前进；多选字段的普通点击仍是草稿，Alt+数字对单选会保存，对多选只切换草稿。选中多行时仍需显式保存。

## 解释评分卡

选择真实 CSV 评分列后，卡片最多显示六项数值指标。相对排名针对当前筛选候选集；改变筛选范围后排名可能变化。绝对规则需要用户提供方向、阈值或通过列；方向未知时保持中性。评分高低本身不能证明结构质量。

结构详情显示完整值和已保存标注。源表中的描述性 `label` 与人工标注分开；需要导出时使用新路径。相同结构路径的多行可拥有独立标注，见 [CSV 完整指南](structure_annotator_csv_walkthrough.md)。

Status: implemented, validated, packaged and installation-checked as 0.3.0, 2026-09-15.

The 0.5.1 workspace keeps these storage and scoring contracts. Mouse choices
remain drafts by default. **Save on label click** is optional and independent
of **Go to the next structure after saving**; both are in **Settings > Annotations**
and **More actions**. Alt+digit saves a single-choice label once, while a
multiple-choice shortcut only toggles a draft value. Import, filtering and
configuration use on-demand pages/dialogs. See
[the current validation record](validation/structure_annotator_click_save_2026-09-16.md).

Version 0.9.0 adds a default **Ctrl+click** gesture for one candidate: choose a
single-choice value, or toggle a multiple-choice item and apply all staged
choices, then save and advance on success. Ctrl+click **Apply** in the choice
popup also saves and advances. This works when ordinary click-saving and
automatic advance are off. Shift stages values, required fields still gate
saving, failures retain the draft/cursor, and selected batches still need
explicit Save. Column and choice searches accept noncontiguous fuzzy matches.

## Accepted behavior

- Shift selects a range of visible candidate rows; Ctrl toggles individual rows.
  Selection gestures do not load every selected structure. The currently
  previewed candidate remains explicit and supplies the footer score values.
- Annotation fields are configurable as single-choice, multiple-choice, or
  text, with required/optional status. Existing quality labels remain usable;
  notes are optional. Required fields gate a completed save, not browsing.
- Saving selected rows validates all resulting required fields before writing.
  A batch is one durable operation and one undo operation. It does not load
  other structures or run per-object bucket commands against unpreviewed rows.
- Optional click-saving commits only one candidate from a single-choice
  button/dropdown after all required fields are complete. Shift+click,
  ordinary multiple-choice clicks and batch clicks remain drafts. Failed writes retain
  the current candidate and unsaved choices; programmatic form restoration
  never submits annotations.
- Ctrl+click is an explicit save-and-next gesture for one candidate, including
  secondary multiple-choice fields. It never auto-saves a multi-row selection.
- Score cards next to annotation use selected CSV columns and the current
  filtered candidate cohort. At most six numeric metrics are shown. Formatting
  is compact, while full precision remains in tooltips and exports.
- Relative ranks are cached when the filter or columns change. Cursor movement
  does not rescan the table or access files. Unknown score direction remains
  neutral; users can specify higher/lower, thresholds, or a CSV pass column.
- Absolute guidance and cohort-relative rank are labeled separately. A high
  numeric value is not automatically described as biologically better.
- The details view also respects column choices, and changing candidates or
  filters cannot leave score cards attached to an old candidate/cohort.
  All saved annotation fields remain visible in Structure details regardless
  of the current field configuration, with full values in tooltips.
- Opening the panel never restores a historical source or configuration,
  including old snapshots with `restore_last_dataset=true`. Existing data in
  the live PyMOL session remains available. The Data action opens
  the Folder/CSV table inputs; a searchable Recent configurations dialog explicitly restores
  configuration or restores and loads the selected source.
- Restoring configuration does not load a structure or change the active
  source identity. Explicit imports use the chosen UI configuration, without
  silently replacing it with the last sidecar configuration.
- Directory discovery and structure materialization support Windows paths
  longer than MAX_PATH, including mapped-drive files and UNC paths.

## Validation and delivery

Cover Shift/Ctrl selection, mixed existing values, required and optional fields,
multi-valued labels, atomic batch/replay/undo, save/reload/export, score formatting,
filter-dependent ranks, missing values, and explicit quality rules. Use the real
Windows Qt/PyMOL workflow and retain the preloaded-navigation latency check.
Build and install-check a new Plugin Manager ZIP after completing validation.

## Execution record

- Existing 0.2.1 implementation and file ownership were inspected.
- The storage scout returned upstream 502 without editing files; integration
  and implementation continue in the main thread.
- Implemented atomic JSONL batch events, whole-batch undo, configurable field
  definitions, required-field validation, and multi-value encoding. Existing
  scalar schema 1.0/1.1 events remain readable; batch events use schema 1.2.
- Added UI selection/draft coordination and cached score summaries. Details and
  footer metrics follow selected columns; relative ranks follow the current row
  filter. Unknown directions remain neutral, with explicit rule configuration.
- The 200-row real Windows/PyMOL gate passes Shift/Ctrl selection, required and
  optional fields, multiple choices, batch save/undo without structure loads,
  filtered rank changes, details filtering, config persistence, and multi-field
  CSV export. Warm navigation with the footer measured median 2.43 ms and
  maximum 3.41 ms in the first successful run.
- The full suite passed 695 tests before the last incremental update and
  source-configuration refinements. The expanded suite and 50,000-row review
  gate are being rerun. Release packaging and final documentation remain.
- The latest review GUI run passed; 697-test regression exposed one stale
  expectation about console reload configuration. GUI and console configuration
  precedence are being made explicit while adding the history browser.
- The supplied K: source exists (215-character directory, 72 PDB files, maximum
  full path length 288). Windows plain `isfile` reports false but extended paths
  succeed. The canonical DirectoryDatasetSource currently drops these files;
  the plugin-specific discovery finds 72 and loads the first via its cache.
- Two fresh bounded agents returned upstream 502 without changes; the main
  thread owns implementation and validation of import/history and long paths.
- The final full suite passes 700 tests in 24.661 s. The current 50,000-row
  Windows/PyMOL review run passes all field/batch/score/filter/export checks;
  warm Next median is 2.51 ms and maximum is 4.55 ms, with no batch structure
  loads. The 200-row/128-column compatibility workflow also passes all keyboard,
  completion, filter, cancellation and scene-preservation checks.
- Explicit history restore was tested against a deliberately newer conflicting
  sidecar. Startup ignores even legacy automatic-restore flags; configuration
  restore makes no structure load and retains the active source identity.
- Windows long-path acceptance passes a 341-character directory, 421-character
  structure path, direct/cache materialization, and all 72 structures in the
  supplied K: directory. Source size/mtime and sidecar-directory presence are
  unchanged. The first host attempt passed the feature checks but exposed the
  test harness's unprefixed temporary-directory cleanup; extended-path cleanup
  fixed it and the complete rerun passed.
- `just` built `dist/PymolPlugins-0.3.0.zip` (88 files, 384,039 bytes). Archive
  CRC and source-byte parity pass. Windows PyMOL's real Plugin Manager installed
  it outside the repository and loaded version 0.3.0, all 11 canonical modules,
  71 canonical plus 8 compatibility commands, and Structure Annotator.
  Final evidence is recorded in
  [the validation record](validation/structure_annotator_review_2026-09-15.md).

## Storage compatibility

Batch records are single newline-terminated, fsynced schema-1.2 events. Undo
checks every affected key before restoring the group. The event bound is
16 MiB and the existing log bound is 64 MiB; oversized operations fail before
appending. Older 0.2.x readers cannot read batch events. Use the new plugin for
logs containing batch annotations; existing 1.0/1.1 logs require no conversion.
