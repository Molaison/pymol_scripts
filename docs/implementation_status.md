# Implementation Status

## 连续审阅 — 0.12.2

已交付：显式恢复最近数据源/筛选/候选位置；独立的“稍后复核”状态与 Next unreviewed / Next marked for later 路线；不污染质量标签的 review handoff JSON；分数相对排名/绝对规则提示；SSH/路径错误的直接恢复入口。
最终 `output/12_continuous_review_20261001.json` 在 Windows PyMOL 3.1.3.1、全新隔离设置和临时安装包中 passed：首次导入、保存并继续、later路线、handoff导出、恢复入口可用、760×560布局及源文件不变。未做真人上手计时或长期断线实验；完整记录见 `docs/validation/01_sampling_reference_2026-09-29.md` 末尾。

## 新手上手 — 0.12.1

已交付：Open data三步入口、可选Quick start、空SSH列表行动提示、Connections里的首次连接说明；README/包内安装说明先给操作、再给高级信息。
最终临时安装包在Windows PyMOL3.1.3.1和全新隔离设置下通过：打开帮助、3行CSV/首结构、点击标签不自动保存、Save and next写入并继续、760×560主按钮可见，4份源文件不变。证据output/11_onboarding_20260930.json及同名前缀截图，完整状态在validation/01_sampling_reference_2026-09-29.md末尾。未做真人上手计时；保留外部SSH准备条件，不修改用户当前安装。

## SSH体验优化 — 0.12.0

已交付：SSH阶段/计时/字节与速率反馈、取消、轻量压缩、原子流式落盘和非gzip直接缓存；保留原profile、OTP程序与持久会话。真实Windows3.1.3.1及x服务器600行/56列通过；首连加读表3.46秒，热导入0.412秒，TSV/CIF载荷分别减少约88%/68%。这些是单次实测，不是旧版吞吐提升倍数或每次OTP的承诺。
21项传输测试、原生SSH/取消、最终0.12.0 ZIP临时安装均passed。完整证据、失败保留及下一步统一见validation/01_sampling_reference_2026-09-29.md末尾SSH章节；08/09/10系列JSON是对应原生输出。没有更改用户当前安装或远端原始文件。

Snapshot date: 2026-07-27 (Asia/Shanghai)
Specification: [development_specification.md](development_specification.md)

This file separates code existence, WSL evidence, automated Windows Host
evidence, interactive Host evidence, and optional licensed-backend evidence.

## AI per-step命令草稿 — 0.11.0，2026-09-29

Settings > Automation内增加AI草稿；固定test/gpt-6-astra，复用Debian WSL既有Responses配置和认证。准确prompt加JSON schema，严格解析后按on_open/after_label触发框填入，确认覆盖后仍需Apply，不自动执行；标签命令支持多行并随review.override保存重开。

`output/07_ai_installed_20260929.json` passed：真实Plugin Manager临时安装后，在Windows PyMOL3.1.3.1完成实际provider请求（6.743秒）、2个step的生成/编辑/确认、2399原子的原生命令执行、取消（约20毫秒）、保存重开和紧凑GUI。11类坏文档与真实拒绝连接的网络错误属于明确标记的故障注入，均不污染原框。完整prompt、模型原始JSON文字/usage、状态、PSE和原生截图保留，无认证内容。600行原验收未重跑。

安装包459007字节，SHA256 `66126cc3bae9c50c8816a25a87312c5ad6eddf2f9b6f168a98eadbb22147670a`。同一[权威记录](validation/01_sampling_reference_2026-09-29.md)列明失败修复及未验证边界；不是所有外部依赖或PyMOL版本已通过。

## 全量概览、分层抽样与 reference — 0.10.0，2026-09-29

现有候选表加入 Overview / sample 和 reference 列；不创建第二个导航游标。
全集/筛选/样本分别计数，组和最多五个分位层交叉取代表样本，支持恢复全量。
评分百分位不因取样而改变。reference 复用既有解析、缓存和后台任务，
只对齐独立的 reference 对象，候选坐标不变；配置可保存并重开。

本轮 Windows PyMOL 3.1.3.1 使用真实 RNA 数据（600 行/56 列）验证。
维护恢复后未重跑600行流程。最终ZIP已在真实Plugin Manager临时安装通过；
命令验收原14组分项及新增语法项已有通过证据，修复了引号正则、原子表示位读取和完成标记残留。
受影响的共享批处理补验56对/112输出，最大坐标误差0，51份源文件不变。
这是分项证据的汇总；保留含失败项的原始JSON，不把它们标成整次passed。
完整状态、失败修正与范围见[唯一执行记录和验收矩阵](validation/01_sampling_reference_2026-09-29.md)。
下文较早的功能记录仍保留其原验证边界，不表示全部在本轮重测。

## Real SSH source and simplified opening - 0.9.1, 2026-09-18

Fixes the Windows Python 3.10 empty-query failure for ordinary SSH URLs and
reuses the existing SSH login program instead of bypassing OTP authentication.
The source page now has one Open from selector, one path and one Open button;
table type is detected and advanced options remain available. Exact-case aliases
win completion, and failed operations retain their full details.

One authenticated SSH connection handles the source reads, avoiding repeated
OTP-window waits. Real Windows PyMOL opens the user's 600-row, 56-column CSV and
its 2399-atom first structure with unchanged source hashes. The 788-test suite
passes; the authenticated import takes 0.429 s in the recorded run.

Validation is recorded in
[the real-source follow-up](validation/structure_annotator_source_ui_2026-09-17.md).

## SSH sources and fuzzy selection - 0.9.0, 2026-09-17

Implemented: OpenSSH Host profiles, remote directory/table browsing and reads,
remote-relative CSV paths, local annotation/configuration stores, cached
structure transfer, shared fuzzy completion and default Ctrl+click save/Next.
Existing asynchronous source replacement, required fields, explicit batches,
Shift, retry and popup acceptance behavior are retained.

The 781-test regression and real Windows PyMOL 3.1.3.1 / local OpenSSH workflow
pass. The tested server uses an ephemeral profile/key; this is not a measurement
of a production cluster or cold long-distance network performance. Final
packaging and clean-exit Plugin Manager installation checks also pass; evidence is in
[the validation record](validation/structure_annotator_ssh_fuzzy_2026-09-17.md).

## Filter Enter completion - 0.8.1, 2026-09-17

Enter/keypad Enter/Tab accept the highlighted column completion without
submitting or closing the filter dialog. Enter without an open popup applies
the expression. The popup now uses the existing completion key handler, so
QCompleter cannot forward the same acceptance key into filter submission.

The regression fails on 0.8.0 and passes after the fix in real Windows PyMOL
3.1.3.1, including the full 200-row/128-column GUI workflow. The 0.8.1 ZIP passes
both source-byte/CRC and isolated bootstrap tests. Evidence is recorded in
[the Enter validation record](validation/structure_annotator_filter_enter_2026-09-17.md).

## Review bundles and responsive imports - 0.8.0, 2026-09-17

CSV/TSV imports automatically apply adjacent `review.json` followed by
`review.override.json`. User edits save only to the override; ordered metrics,
descriptive source labels and shared command templates are supported. Mapping
roots accept `K:` as `K:\`. History suggestions only insert paths on explicit
acceptance. Folder discovery/legacy annotation reads and table settings writes
prepare off the Qt thread, with cancellation and transactional scene commit.

All 767 regression tests pass in 26.578 s; 20 focused review/history/package
checks pass after final changes. Windows PyMOL 3.1.3.1 passes review/override
reopen, CSV/TSV pasted-path switching, invalid-config preservation, drive-root
mapping, 421-character paths and the existing 200-row/128-column GUI workflow.
The real K: source passes 600-row TSV import in 0.281 s after header discovery
and 600-structure folder import in 16.151 s. Maximum Qt heartbeat gaps are
46.99/58.41 ms, with no main-thread remote stat/list/scandir calls. Source table
and state hashes are unchanged. The separate 203-file blocked-I/O test verifies
responsive cancellation and suppressed late results. The 0.8.0 ZIP passes
source-byte/CRC checks and real Plugin Manager installation in an isolated
temporary directory. Detailed results are in
[the import validation record](validation/structure_annotator_import_2026-09-17.md).

## Embedded and floating workspaces - 0.7.0, 2026-09-17

Implemented and packaged: Structure Annotator and Batch Processing default to
native left-side PyMOL docks. A shared window-mode icon offers independent
floating windows. Each tool remembers its mode, side/width and floating
position/size; same-side tools share tabs. Switches preserve drafts, selection,
preparation and running work. Existing close guards and shortcut ownership stay
active. Original viewport minimum/corner constraints are restored after use.

All 757 regression tests pass in 27.348 s. Windows PyMOL 3.1.3.1 / PyQt5 passes
native/menu mode switching, real save/preparation/run switches, close protection,
reopen geometry, full-frame off-screen recovery, explicit settings errors and
1440 x 900 / 1280 x 720 layouts.
The smaller window retains a 320 x 406 viewport. Existing annotation layout,
click-save and full asynchronous workflow checks pass. Full batch regression
verifies 56 pairs, 112 outputs and 51 unchanged inputs, with coordinate error 0.0.

`dist/PymolPlugins-0.7.0.zip` passes source-byte/CRC/version checks and real
Plugin Manager installation in an isolated temporary directory, including both
installed tools' modes, shared styling and tabification. No user installation
was changed. Evidence and standalone-harness capture/shutdown boundaries are in
[workspace window modes](workspace_window_modes.md). PyMOL 2.x and other Qt
bindings remain unverified.

## Batch UI consistency - 0.6.1, 2026-09-16

Batch Processing now uses the same local stylesheet as Structure Annotator:
Segoe UI controls, Consolas command editors, shared primary/error roles,
36-pixel table rows, 34 x 34 icon tools and a thin progress line. It reuses the
File locations collapsible control and preserves the application-wide style.
The input area and table remain usable at 1000 x 720 and 760 x 560.

Real Windows PyMOL 3.1.3.1 passes standalone/annotation-entry style comparisons,
rendered button colors, both layouts and all 56-pair/112-output processing
checks. Annotation layout, click-save/advance, failure recovery and the full
200-row, 128-column GUI workflow pass. Pairing and execution logic are unchanged.
All 757 regression tests pass in 24.629 s. The final 0.6.1 ZIP passes CRC,
source-byte/version checks and real Plugin Manager installation, including the
installed dialog's shared theme: 11 modules, 72 canonical + 8 compatibility
commands and 6 menus. Evidence is tracked in
[the UI validation record](validation/batch_ui_consistency_2026-09-16.md).
PyMOL 2.x and other Qt bindings remain unverified.

## File/CSV batch processing - 0.6.0, 2026-09-16

Implemented: `pp_batch` and the Batch Processing dialog accept folder or CSV
structure files, all against one reference or ID-matched A/B pairs. Matching
supports literal templates, wildcard ID captures and named/single-group regexes.
Ordered editable commands run on the PyMOL thread; both processed A and B save
by default. Preview is read-only, outputs never overwrite sources/existing
automatic files, and stop/continue/cancel results are recorded in JSON/JSONL.
CSV parsing and mapping reuse canonical APIs. Each pair has unique object names,
and the fixed reference is loaded once and copied before each pair's commands.

The full 757-test suite passes in 23.406 s. Windows PyMOL 3.1.3.1 / PyQt5 passes all matching
modes, both CSV modes, 36 continuous pairs, real two-step commands, cancellation,
stop/continue-on-error, the annotation entry point and compact layouts. The final
run completed 56 pairs and verified 112 A/B outputs with maximum coordinate
error 0.0; all 51 source-file hashes were unchanged. PDB/mmCIF, gzip outputs and
431-character output paths pass actual read-back, and native undo remains
enabled. Annotation click-save/advance and the full 200-row, 128-column workflow
also pass. The final 0.6.0 ZIP passes CRC/source-byte/version checks and real
Windows Plugin Manager installation in an isolated temporary directory:
11 modules, 72 canonical + 8 compatibility commands, 6 menus, and a working
Batch Processing dialog with both-output defaults and all three matching modes.
No release blocker remains for this target; PyMOL 2.x and other Qt bindings
remain unverified. Evidence is tracked in
[the 0.6.0 validation record](validation/batch_processing_2026-09-16.md).

## Optional label-click saving and restored controls - 0.5.1, 2026-09-16

Implemented: independent persisted click-save and auto-advance controls in
Settings / Annotations and More actions. Click-save defaults off; single-choice
buttons/dropdowns save one candidate only after required values are complete.
Shift, multiple-choice and batch clicks remain drafts. Failed writes preserve
drafts/cursor, and single-choice shortcuts save once. Multiple-choice Alt+digit
again toggles draft values without saving. Source row / File status are optional
persisted list columns; Structure details shows every stored annotation,
including fields absent from the current configuration.

The full 716-test suite passes in 24.241 s. Real Windows PyMOL 3.1.3.1 / PyQt5
passes visible click-save/manual/stay controls, required/batch/Shift cases,
write-failure recovery, shortcuts, dropdown restoration, all-saved-label details,
column toggles without loading/reordering, and preference reopen. Main windows
pass at 1000 x 720 and 760 x 560; the OpenGL sample contains 83 colors. Full
workflow, 50,000-row review, import/history/long paths and mapping/command
recovery also pass. Warm Next median/p95: 2.09/3.10 ms, zero load/stat calls.
The final 0.5.1 ZIP passes CRC/source-byte/version checks and the real Windows
Plugin Manager installer: 11 modules, 71 canonical + 8 compatibility commands,
5 menus. Installation used an isolated temporary plugin directory. No release
blocker remains for this target; PyMOL 2.x and other Qt bindings are unverified.
Evidence:
[the 0.5.1 validation record](validation/structure_annotator_click_save_2026-09-16.md).

## Review workspace - 0.5.0, 2026-09-16

Implemented: the candidate list and current structure are the primary workspace;
Data, Filter, Settings and the more-actions menu expose secondary controls on
demand. Mouse choices remain drafts, required fields gate Save, and navigation
or opening configuration preserves drafts. Save stays outside the scrolling
score/field content. A source change clears the previous source's draft values.

The 714-test regression passes. Real Windows PyMOL 3.1.3.1 passes visible-control
import, draft/save/advance, batch/menu undo, empty/reset filters, 1000 x 720 and
760 x 560 geometry, long names, six scores, and a nonblank OpenGL framebuffer.
The complete workflow also passes shortcuts in the panel, viewport and both
console modes, completion, cancellation and failed-import preservation. Warm
Next median/p95 is 1.34/2.19 ms with zero load/stat calls. The 50,000-row review,
profile/command recovery and long-path import gates also pass. Profile and
configuration table text fit in normal and compact windows, and Test path
scrolls the result into view. The final 0.5.0 ZIP passes source-byte/CRC checks
and the real Windows installer (11 modules, 71 canonical + 8 compatibility
commands, 5 menus). PyMOL 2.x and other Qt bindings remain unverified. Evidence:
[the 0.5.0 validation record](validation/structure_annotator_layout_2026-09-16.md).

## Mapping profiles and switching — 0.4.0, 2026-09-16

Implemented: named server profiles, ordered literal-prefix/regex rewrites,
Windows/POSIX root mappings, per-dataset computer-format overrides and WSL
distribution selection. Sample tests and preview use read-only configuration;
preview has a separate window and preserves the active review and multi-selection.
Optional worker transfer is grouped under file access. Command editors keep
drafts until Apply commands; checked parser dispatch reports the failing line,
and failed warm/cold navigation restores cursor, visibility and camera.

The full **714-test** suite passes. Real Windows PyMOL **3.1.3.1** passes profile
editing/round-trip, preview isolation, selection-error recovery, 421-character
paths, import/history/shortcuts/cancellation, and 50,000-row batch review.
Warm Next median/p95 is **1.51/2.18 ms** with zero load/stat calls. Evidence,
package details and remaining compatibility boundaries are in
[the 0.4.0 validation record](validation/structure_annotator_mapping_2026-09-16.md).

## Multi-field review and score guidance — 0.3.0

Implemented: Shift/Ctrl multi-row selection, required/optional single/multiple/
text fields, atomic batch events and whole-batch undo, multi-field export, and
filtered candidate score cards with compact formatting and explicit quality
rules. Explicit directory/CSV import and searchable manual history replace
automatic startup restoration. Windows long-path discovery/materialization is
verified with the supplied 72-file K: directory and 421-character test paths.
The 50,000-row real Windows/PyMOL review test passes with cached relative
ranks, no batch structure loading, and warm navigation below 5 ms in that run.
See [the feature record](annotation_fields_and_score_guidance.md) for the final
validation and storage-compatibility details.

## Structure Annotator 0.2.1 update — 2026-09-15

The integrated review UI, schema-based column completion/selection, typed
filters, native command completion, and Qt shortcuts are implemented. Real
Windows PyMOL 3.1.3.1 tests cover the viewport and both console docking modes,
failure recovery, cancellation/late-result isolation, and the current K-drive
CSV. The full Python suite passes 687 tests. See the
[current validation record](validation/structure_annotator_usability_2026-09-15.md)
for measurements, package acceptance, and the precise evidence boundaries.

The historical three-tab/history-completion and sandbox-blocked statements
below describe the earlier snapshot, not the updated annotation workflow.

Current evidence anchors:

- WSL current revision: the complete non-loopback subset passes 619/619 on
  Python 3.12.13 and 619/619 on Python 3.13.14. The four loopback HTTP modules
  contain 60 additional discovered tests; their sandbox run reaches 43 socket
  bind points and every one fails with `PermissionError: [Errno 1] Operation
  not permitted`, with no assertion failure. They remain an explicit
  environment gate rather than being counted as passed.
- Windows Host historical baseline: the latest reviewed automated PyMOL
  3.1.3.1 record passed 13/13 and is
  [automatic pass / manual pending](validation/structure_annotator_optimization_2026-07-26.md).
  It covers the former 63-command surface. A 2026-07-27 attempt to launch the
  current 71-command harness 1.5.0 failed before PowerShell/PyMOL startup because
  this sandbox cannot connect to the outer WSL interop socket
  (`UtilConnectUnix` / `UtilBindVsockAnyPort`); it is **not passed**.
- Structure Annotator: `HOST-ANNOTATION-CSV-01` and the real PyQt5 smoke pass;
  real Windows `K:` FUSE mapping/cache passes, and the PyMOL-independent
  allowlisted WSL staging client/cache/materializer chain passes a real worker
  cold/warm E2E. The batch-stat and 50,000-row evidence is recorded in
  [the 2026-07-26 optimization record](validation/structure_annotator_optimization_2026-07-26.md).
- WSL worker historical evidence: a real loopback run on Python 3.12.13
  returned `status=ok` with PLIP 3.0.0 available; the reviewed Windows Host
  baseline reached that worker through localhost forwarding and verified its
  then-current 63 canonical commands.
- Real PLIP/OpenBabel numerical validation passes both direct-backend and HTTP
  paths on RCSB 1A4W. A separate Windows PyMOL -> WSL PLIP -> exact-ligand Host
  render produced one QWE site, 14 interactions, and 12 manifest-owned outputs.
  PyRosetta remains absent and no PyRosetta numerical result is claimed.

Status vocabulary:

- **WSL verified**: covered by current pure/fake-command tests.
- **Host smoke verified (3.1.3.1)**: covered by the versioned automatic Host
  record, within that check's stated limits.
- **Implemented; interactive Host pending**: code/tests exist, but mouse,
  callback, Qt, rendering, or another interactive behavior is not recorded.
- **Blocked by optional licensed environment**: code/mock contract exists, but
  a legal installed backend is required for numerical validation.

### Task-oriented command UX

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| HELP-01 | WSL verified; Host pending | Unicode NFKC/casefold, exact/full-token ranking, Chinese/natural-language task aliases, a 0.50 low-confidence cutoff, and nonempty `no_match` behavior are regression-tested. |
| TASK-01 | WSL verified; Host pending | The eight declared, console-adapted facades delegate to existing state owners. `pp_orient`/`pp_metrics` require explicit targets, and navigation controls reject start-only options. |
| UNDO-01 | WSL verified; Host pending | Unified references delegate the exact stored canonical reference and mark undone only after success. `pp_show` snapshots all states and uses a hidden same-session object marker when supported. Harness 1.5.0 covers only the older state-1 Host round trip; the all-state/marker Host gate remains open. |
| METRIC-UX-01 | WSL verified; licensed backend pending | `pp_metrics` requires explicit source and disjoint target/binder chains. `preview=1` resolves the single molecule and output plan without structure export or worker access. GUI-default submission is asynchronous without a synchronous capabilities probe; readiness/backend failures remain inspectable in `pp_jobs` history. |

PyMOL 2.x compatibility remains open even when the 3.1.3.1 smoke check passes.

## Acceptance matrix

### Navigation

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| NAV-01, NAV-02 | WSL verified | Empty/single/final-short batches, wrap, stride, and forward/back symmetry are covered. |
| NAV-03 | Host smoke verified (3.1.3.1) | Nested root group remained atomic with molecule, static CGO, map, measurement, and multistate leaves; an unmanaged overlay was preserved. |
| NAV-04 | Implemented; Host file-failure pending | Fake tests prove ordinary load rollback plus transactional `replace=1`: the prior controller, visible scene, adopted file objects, and key lease survive a failed first load. A real Host file-load failure remains manual. |
| NAV-05 | WSL verified | Both legacy facades share the canonical hook/pin functions and controller; dual load-order tests prove overlapping aliases resolve to the same callable, and neither adapter binds keys at import. |
| NAV-06 | WSL verified | Shared console booleans reject unknown tokens and parse false strings correctly. |

Key lease restoration, third-party rebinding preservation, structured hook
context/offset, pin clearing, and explicit session replacement are also tested.

### Structure Annotator state and history

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| ANNO-01, ANNO-02 | Host smoke verified (3.1.3.1) + WSL verified | Version-2 dataset-local state is schema-checked, size-bounded, and atomically replaced. Tests cover directory/CSV isolation, version-1 directory migration, On-step/bucket/preload/cache restoration, relative-file or stable-entry cursor recovery, missing-entry fallback, corrupt JSON repair, and exclusion of undo/object/materialized-cache state. Host restored `b.pdb`, the complete config, and commands from a quoted path containing spaces; the Windows hidden attribute was set. |
| ANNO-03 | WSL verified; fresh Host Qt rerun pending | Global history snapshots and 17 field histories are bounded, de-duplicated, atomically written, corrupt-file quarantined, and fuzzy-ranked by textual relevance plus frequency/recency. The current GUI exposes 15 static fuzzy-history bindings, plus token completion for display columns. Headless PyMOL wrote the global schema through a temporary profile path and resolved `cartn` to the complete command. A prior real Host PyQt5 smoke created the three-tab panel, observed both popups, accepted On-step by mouse and bucket command by keyboard, and verified the then-current fixed/dynamic completers plus preview-source isolation; it predates the current 17-field/15-binding surface and is not evidence for this revision. A latest snapshot reopens its source by default; a fresh Host Qt rerun and fresh-process visual check of the opt-out remain pending. |
| ANNO-04 | Host smoke verified (3.1.3.1) | `HOST-ANNOTATION-STATE-01` is registered and passed. It uses a quoted directory containing spaces and a temporary global history path, and asserts Windows hidden state, schema/config/cursor restoration, and fuzzy command lookup without polluting the real user profile. Removing `cmd.sync()` from the parser callback eliminated the observed self-wait `wait_queue` timeout on rerun. |

### Structure Annotator CSV, mapping, and cache

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| ANNO-CSV-01 | WSL verified | CSV/TSV parsing covers BOM, auto/explicit delimiters, exact headers, quoted newlines/physical row numbers, ragged rows, empty/duplicate IDs, duplicate structure paths, portable basename-namespaced entry IDs, numeric typing, and immutable row metadata. |
| ANNO-CSV-02, ANNO-CSV-03 | WSL verified + Host transaction success | Preview does not write scene/session/state/history/mapping data and performs staging `stat` when configured; preview rows cannot navigate an old source. Directory/CSV/filter switches preflight the new entry, park old managed objects, and either commit or restore the complete prior scene. Tri-state source settings restore per-CSV values, while a newly selected CSV inherits no parser/ID/display/base/staging values from the old CSV. Host `HOST-ANNOTATION-CSV-01` exercises the successful quoted-path transaction. A deliberately injected real Host load failure remains a manual gate; all rollback branches are fake-PyMOL tested. |
| ANNO-CSV-04 | Host smoke verified (3.1.3.1) + WSL verified | Append-only row/label JSONL supports fsync, torn-tail recovery, bounded validation and undo. Unchanged reads use a file-signature cache, progress uses one label snapshot, and a local fsynced append updates the in-memory replay state incrementally instead of rereading the full log. Source CSV/PDB bytes remain unchanged. Export refuses source overwrite, writes `pymol_annotation_<label>` without duplicate headers, preserves ragged source values, and is re-importable. Duplicate source paths remain independently annotated. |
| ANNO-CSV-05 | Real Windows `K:` FUSE verified | `/xcfhome/zpzeng/mount_local`, `/xcfhome/zpzeng`, and literal `~/` resolve to `K:\`; all three aliases share one physical-target cache key (`cold=false`, then two warm hits). Direct `K:\...` is recognized as a configured remote drive and shows its own cold/warm persistent-cache pair. The 75,609-byte source SHA-256/size/mtime stayed unchanged, and cache objects were on Windows-local `C:`. |
| ANNO-CSV-06 | Real WSL worker core verified | The PyMOL-independent Bearer-authenticated client/cache/materializer chain with root alias `structures` produced exactly `POST /v1/stage/stat`, `POST /v1/stage/file`, `POST /v1/stage/stat`. Cold materialization missed, the second stat hit a cache key containing the complete `v1-...` metadata token, and a reopened cache also hit; source bytes were unchanged. Controller tests verify a CSV load reuses its fresh resolution stat rather than issuing a redundant preflight stat. Unit tests additionally cover wrong bearer, missing, symlink/traversal/regular-file/size/deadline checks and response validation. |
| ANNO-CSV-07 | Host smoke + real PyQt5 verified | `HOST-ANNOTATION-CSV-01` loads a Windows-local CSV/path containing spaces through real `cmd.do`, keeps duplicate path rows independent, annotates, exports, restores state/cursor, and verifies source bytes. The Qt smoke validates Source/Annotate/Scores widgets and that clicking a preview row makes zero `sa_goto` calls. Windows-PyMOL-to-WSL controller staging and PyMOL 2.x are still manual gates. |
| ANNO-CSV-08 | Real WSL worker + WSL verified | The allowlisted worker exposes bounded `POST /v1/stage/stat-batch` with at most 256 unique canonical paths, ordered per-item source/error results, one request deadline, strict envelope validation, and no server paths. The Host client additionally chunks at 64 KiB, bounds responses at 2 MiB, and falls back only for an old worker's exact `404/not_found`. A real foreground worker returned one available and one missing result in one batch request; the old single stat/file endpoints then returned matching identity and 62,712 bytes. |
| ANNO-CSV-09 | Real Windows PyQt5 performance gate | Scores uses `QTableView + QAbstractTableModel`; the small metadata table remains `QTableWidget`. Dataset/navigation generations and entry-ID maps remove full-row signature scans, stale clicks refresh without navigating, same-source reload refreshed `0.0 -> 9.9` and `available -> missing`, and annotation emitted only cell `(0,3)`. On Windows bundled PyQt5, 50,000 x 8 used 12.3 MiB additional RSS versus 199.8 MiB for the legacy widget (93.85% reduction); 200 cursor refreshes had p95 0.258 ms and max 0.615 ms. |
| ANNO-CSV-10 | WSL performance + regression verified | One resolve snapshot caches only truly repeated `(raw path, source base)` keys (with a homogeneous-base string fast path) and separately reuses canonical Windows mapped/drive/UNC candidate probes. Duplicate local rows perform one `isfile/access` pair while row IDs/metadata remain independent; heterogeneous bases cannot leak resolutions, and the cache is rebuilt on reload. A 50,000-row audit retained duplicate-path speedups without the all-unique regression caused by populating a full resolution dictionary. |
| ANNO-CSV-11 | Real Windows PyQt5 + Host transaction + WSL verified | Console preview/load remain synchronous. Qt Preview/Load use immutable worker preparation and queued main-thread completion; live `STATE`, widgets and `cmd.*` stay out of the worker. Cancel/close/latest-epoch/source+config fingerprints/dataset+annotation generations suppress late results without rollback, worker-side CSV fingerprints reject mid-prepare changes, and commit takes its rollback snapshot at completion time. The queued slot performs no K:/UNC/WSL filesystem probe. The real PyQt5 gate observed 10 heartbeats while a worker was blocked, 0.20 ms cancel, late-result/config-change/annotation-change isolation, and both preview completion and load commit on the application thread. Restore-last recovers config/intent without probing the source before panel construction. Cancel is result suppression rather than thread termination: two non-cooperative blocked reads can occupy the dedicated two-thread pool until their timeouts, so a later request waits without mutating UI, `STATE`, or scene. |
| ANNO-CSV-12 | Windows bundled Python + WSL verified | Batch request planning serializes the fixed JSON envelope once and each path token once, then uses exact UTF-8 token/comma accounting while preserving the greedy 256-item/64-KiB chunks. Reference tests and 5,000 randomized cases cover quotes, backslashes, control characters, CJK and non-BMP Unicode, exact-limit acceptance/rejection, empty input, and partial-yield errors. A 50,000-path benchmark retained the same 196 chunks and reduced median planning time from 0.9596 s to 0.0302 s (31.8x); all 23 staging-client tests also passed under PyMOL 3.1.3.1's bundled Windows Python 3.10.16. |
| ANNO-CSV-13 | Host smoke + Windows bundled Python + WSL verified | CSV column specs are inferred from raw-row metadata before each final immutable `AnnotationEntry` is constructed exactly once. Constructor-count and full `asdict` legacy-pipeline parity cover IDs, errors, raw/typed metadata and ordering; directory/model helpers remain compatible. On a 50,000 x 8 CSV, median load fell from 0.5746 s to 0.4082 s (29.0%) and tracemalloc peak from 118.7 MiB to 96.4 MiB (18.8%) while retained dataset memory stayed unchanged. Real Host `HOST-ANNOTATION-CSV-01` and 21 bundled-Windows-Python dataset tests pass. |
| ANNO-CSV-14 | Real Windows PyQt5 + Host smoke + WSL verified | Cancel atomically unregisters the job before setting its worker-owned event, immediately releasing registry-held panel/runnable metadata; queued or late completion for that ID is a no-op. A real blocked-worker Qt test covers both ordinary Cancel and closing a second live panel, then releases the worker and proves no callback/table/dataset mutation. Cancel returned in 0.19 ms, the registry remained empty, and PyQt5 reported runnable ownership transferring to `QThreadPool`. The two-thread non-cooperative-I/O timeout boundary and PyMOL 2.x/PySide2 gate remain explicit. |

### Object/group targets

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| TARGET-01..05 | Host smoke verified (3.1.3.1) + WSL verified | Exact/glob/contains/regex/fuzzy matching, deterministic ordering, preview-only default, and explicit commit are tested. Real Host `cmd.do` covers a quoted template and quoted glob target; expansion of one nested group covered molecule, static CGO, map, measurement, and multistate leaves while preserving an unrelated overlay. Same-line completion markers detect the real parser's swallowed unknown-command/CmdException failures, enforce stop/continue, and leave no scene names behind. |

### Group follow

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| FOLLOW-01..03, FOLLOW-06 | Host translation smoke + WSL rigid tests | Programmatic translation and undo pass for molecule/static-CGO/map/measurement/multistate under `matrix_mode=0/1/2`. WSL tests cover non-identity baselines, 180-degree rotation, repeated apply, and rebase. Real mouse rotation/translation remains manual. |
| FOLLOW-04 | Implemented; callback Host pending | `axes_overlay.py` automatically registers callback ownership; safe default is skip, with explicit static/rebuild policies and apply/undo hook tests. A live view-callback record remains manual. |
| FOLLOW-05 | WSL verified | External follower/group drift and stale undo are rejected before writes; `force=1` is explicit, and failed multi-follower apply rolls back prior followers. |

### Sequence and rigid alignment

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| SEQ-01, SEQ-02 | Host core smoke + WSL verified | Host creates an exact chain-specific motif selection and verifies its four source atom identities. Blank/multi-character chains, insertion/negative `resi`, altloc, missing CA, modified residues, overlap, bounded gaps, ambiguity, and multi-object identity remain covered by WSL fixtures. |
| ALIGN-01, ALIGN-02 | Host success paths + WSL failure tests | Real Host anchor, motif, and ligand fits reach zero RMSD, then commit and undo their object TTTs. WSL tests reject scale/reflection/collinearity; duplicate ligand keys produce a typed ambiguity and verified order remains explicit. |
| ALIGN-03, ALIGN-04 | Host smoke verified (3.1.3.1) + WSL verified | Host proves preview is non-mutating, commit/undo restores the payload, motif anchors move a whole object, and a detached ligand key remains fixed while a separate payload moves. Group leaves, atomic rollback, stale checks, hashes, state, parameters, and backend provenance are WSL-tested. |

### Geometry and membrane

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| GEO-01 | Host orientation/undo + WSL edge tests | Real Host smoke executes PCA and cyclic orientation preview/commit/undo in addition to mass-weighted PCA and explicit vector/two-point axes. Axis/membrane JSON records include input hashes, parameters, backend/version, warnings, and confidence; secondary roll, confidence gates, antiparallel rotation, atomic rollback, and export replacement remain WSL-tested. |
| GEO-02, GEO-03 | Host smoke verified (3.1.3.1) for C2/C3/C6 calculation | C2 uses paired rotation; C3/C6 pass Host. WSL fixtures reject/diagnose nonuniform and helical arrangements. |
| MEM-01 | Host numeric/CGO smoke + WSL geometry tests | Host creates a vector-normal membrane CGO and measures 30 A plane separation; WSL tests cover plane geometry and projected auto extent. Visual inspection remains open. |
| MEM-02 | Implemented; Host slab-follow pending | The slab is a static CGO and generic static-CGO follow passes Host; the actual slab object still needs an interactive record. |
| MEM-03 | WSL adapter verified; real ANVIL pending | DUM layers cover clean/noisy/reordered/missing-layer behavior. The shell-free executable adapter is tested with a fake executable, validates exact protein identity, rejects non-rigid coordinate changes, and maps planes back after rigid reorientation. No third-party ANVIL binary is vendored or claimed as executed. |

### Styles

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| STYLE-01..04 | Host core smoke + WSL registry tests | One composed scoped style and full snapshot revert pass on Host; registry validation, normalized colors, no implicit render, load-order determinism, and detailed atom/background/representation restoration are WSL-covered. |
| STYLE-05 | WSL verified; Host render pending | `pp_style_render` is the only canonical ray/PNG entry. A reviewed Host PNG record remains open. |

### Interactions

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| INT-01..05 | Host local core + WSL failure tests | Host covers exact ligand boundary, structured result, source exclusion, manifest output, and exact clear. WSL additionally covers renderer rollback, failed-replacement restoration, output provenance fields, and `polar_qc` without global-setting mutation. |
| INT-06 | WSL verified | Blank/insertion identity, multiple ligands, state forwarding, water/metal exclusion, long names, zero results, all eight PLIP types, and malformed data are covered. |
| INT-07 | Headless Host success verified; interactive Qt pending | PLIP PDB/TER atom mapping passes Host smoke. A synchronous headless Windows PyMOL request completed the full WSL PLIP POST, exact QWE ligand filter, and Host render. Async HTTP is Qt-worker-only; timeout/unreachable/cancel create no pre-response output, but GUI responsiveness/cancel remain manual. |

`plip_overview` and `plip_publication` now dispatch through the canonical WSL
endpoint. The worker returns `pymolplugins.interactions/1.0`; Host filtering
keeps only interactions containing the exact requested ligand identities.
Real 1A4W PLIP counts were 3 sites / 18 interactions before Host filtering;
the exact `QWE:H:373` Host selection retained 14 interactions: 6 hydrogen
bonds, 4 hydrophobic contacts, 1 pi stack, 1 salt bridge, and 2 water bridges.

### Rosetta/worker service

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| ROS-01 | WSL verified and live loopback verified | Worker starts without optional packages, health stays `ok`, and capabilities expose backend state and effective transport limits. |
| ROS-02 | Blocked by optional licensed environment | Initialize-once and serialization are mock-tested; repeatability requires a user-supplied legal PyRosetta environment. |
| ROS-03 | WSL mock-contract verified | CMS, bound heavy-atom bunsat, and interface-delta bunsat remain separate typed fields with explicit provenance. Numerical semantics need a real installed build. |
| ROS-04 | WSL verified at contract boundary | Strict fields/types/options, duplicate metrics, missing/overlapping chains, auth/schema, unreachable/timeout, body/response limits, and error typing are covered. Hard C++ cancellation is explicitly not claimed. |
| ROS-05 | Host connection verified; analysis POST pending | Windows PyMOL reached WSL `/v1/health` through the shared client. Metric requests send inline PDB only; redirects are refused and paths/commands/XML are rejected. A licensed analysis POST remains unavailable. |

No PyRosetta wheel, token, ANVIL/MSMS executable, or licensed binary was
downloaded or added.

## Experience and infrastructure

| Feature | Status |
| --- | --- |
| Unified plugin bootstrap (`pp_load`) and `pp_help` | Former 63-command bootstrap Host-verified (3.1.3.1); current 71-command/task-help registry is WSL-verified and Host rerun pending. |
| Feature-oriented package ownership | Implemented and WSL architecture-tested: canonical code lives under `pymolplugins/`; AST tests reject imports back into historical namespaces; old paths are thin compatibility shims and standalone tools live under `extras/`. |
| Runtime doctor and `pp_capabilities` | Host smoke verified for complete commands/API/cache/UNC and live PLIP worker state; configured ANVIL state is reported. |
| Transactional interaction replacement | Implemented; a failed new render restores the prior exact result instead of leaving a blank group. |
| Windows fixture/smoke automation | Implemented with versioned JSON, PowerShell gate, local Windows cwd, configurable exe/repository/output paths, and exact cleanup. |
| WSL worker operational docs/probe/start wrapper | Implemented and live-loopback verified, including authenticated allowlisted `stat/file` staging and cleanup. |
| Structure source freshness/caching | Implemented: physical-path/version-token identities, atomic persistent cache, LRU limits/pins, mapped/remote-drive/gzip materialization, and WSL staging cold/warm lookup. |
| Analysis-result freshness/caching | Not implemented; PLIP/Rosetta result caching remains a follow-up optimization. |
| Navigation-triggered automatic analysis | Hook substrate exists; policy/caching orchestration remains follow-up work. |
| Interaction table/copy/legend UI | Not implemented; structured JSON and PyMOL output tree are available now. |
| Repository license | Missing; do not infer redistribution permission. |

## Phase status

| Phase | Code status | Open evidence boundary |
| --- | --- | --- |
| Phase 0: foundations/correctness | Implemented | Real mouse/callback follow and PyMOL 2.x. |
| Phase 1: navigation/alignment | Implemented | Host file-failure, deeper interactive selection records, and PyMOL 2.x. |
| Phase 2: styles/interactions | Implemented | Host PNG and interactive Qt timeout/cancel remain; real PLIP/OpenBabel succeeds. |
| Phase 3: geometry/membrane | Implemented code/contract | Host orientation/undo and slab pass; a real user-supplied ANVIL visual record remains. |
| Phase 4: WSL services/integration | PLIP implemented; Rosetta contract implemented | Windows health and PLIP analysis forwarding pass; a legal PyRosetta numerical environment remains. |

## Verification commands

```bash
python3 -m unittest discover -s tests -v
python3 scripts/probe_worker_env.py --pretty
scripts/start_worker.sh --help
rg --files -0 -g '*.py' -g '!output/**' -g '!tmp/**' | xargs -0 python3 -m py_compile
sh -n scripts/start_worker.sh
git diff --check
```

Use `scripts/windows/run_host_smoke.ps1` for the automated Host subset and
[windows_host_acceptance.md](windows_host_acceptance.md) for the remaining
interactive and optional-backend gates.


## SSH desktop authentication fix — 0.12.3 (2026-10-02)

**Fixed:** the remote worker could print ready in an external window while the plugin stayed at authenticate. Windows desktop PyMOL uses pythonw.exe; its Python OTP wrapper's nested subprocess did not return inherited SSH stdout to the plugin pipe. Earlier console-Python acceptance did not cover this desktop boundary.

Python login scripts now use python.exe from the same installation when the host is pythonw.exe. The original wrapper, profile, OTP rules and CREATE_NO_WINDOW remain unchanged. A missing sibling interpreter produces an actionable error, not an authentication-client or environment fallback. SSH Host acceptance now defaults to pythonw.

| Boundary | Result |
| --- | --- |
| Focused protocol/interpreter regression | 23 passed; output/13_ssh_ready_unit_20261002.log |
| Desktop pythonw + Windows Qt + real temporary OpenSSH | Passed; output/13_ssh_ready_gui_20261002.json; 14 off-Qt-thread SSH operations, cache/directory/cancellation |
| Desktop pythonw + original x profile/OTP + real remote data | Passed; output/13_ssh_ready_real_20261002.json; 600 rows, 56 columns, 2399 atoms, one reused connection |
| Final 0.12.3 ZIP installed through temporary Plugin Manager | Passed; output/13_ssh_ready_install_20261002.json; Windows PyMOL 3.1.3.1, modules/menus/workspace/SSH controls |

One live measurement: first connection plus table read 1.597213 s; warm structure download 0.0428855 s; warm GUI import 0.407005 s. These are not isolated authentication timings, before/after benchmarks, or guaranteed WAN/OTP performance.

Delivery: dist/PymolPlugins-0.12.3.zip. All execution handles are terminal. The user's current installation and running PyMOL were not changed; install the ZIP, fully exit, then restart PyMOL. The authoritative completion record and package hash are in docs/validation/01_sampling_reference_2026-09-29.md.


## Quiet default UI — 0.12.4 (2026-10-02)

Removed persistent onboarding paragraphs, SSH guidance, the review color legend, score disclaimers and redundant later-status confirmations. Default pages now use contextual setup actions, local/network icons, optional help, collapsed advanced SSH settings and a Later badge. Filter/sample counts appear only when needed. Score targets use badges, while relative metrics stay neutral and use normal-size text. Errors, progress/cancellation and unsaved-state feedback remain visible.

Windows pythonw/PyMOL 3.1.3.1 acceptance from the final Plugin Manager ZIP passed: output/14_quiet_ui_20261002.json. The native 760×560 first-use path covers actual table/structure import and saving, help/settings interaction, advanced disclosure, score help, later status and conditional filter counts. Open/review screenshots and compact settings/help screenshots were inspected. Initial oversized-dialog evidence is retained; content-driven dialog sizing now passes at 576×207 and 586×430. This is automated native UI evidence, not a human usability study or a new WAN benchmark.

Deliverable: dist/PymolPlugins-0.12.4.zip. All handles are terminal. The user's installed plugin and running PyMOL were not modified. Install the ZIP and fully restart PyMOL. Future UI additions should use layout and control states before explanatory prose; preserve actionable errors and operation feedback.


## Configurable AI and viewing/path UX — 0.13.0 (2026-10-02)

| Feature | Result and evidence boundary |
| --- | --- |
| AI connection settings | Explicit Codex/Custom API; editable URL/key/model, model refresh and Responses/Chat Completions selection. Windows Host loaded 31 models and completed a real custom/native Responses request using the unchanged configured test/gpt-6-astra model in 8.46 s. |
| Key storage | Session-only by default; optional Windows current-user DPAPI persistence. Native encrypted-key roundtrip and removal passed; no keys in prompt, dataset state or request logs. |
| Draft recovery | 14 supported command forms, object-scoped set/spectrum and element colors; per-trigger empty-result reason, failed-retry preview preservation and manual blank/comment editing. Confirmation/Apply and no-generation-execution boundaries retained. |
| Viewing presets | Cartoon, PPI, PLI, oligomer, membrane, nucleic complex and surface; actual Windows structures (1BRS/1A4W/2RH1 and existing RNA complex), unchanged coordinates. PLI occlusion found visually and corrected with transparent receptor/context and pocket orientation. No membrane placement inference. |
| Fuzzy remote browser | Ranked fuzzy matching, keyboard selection, query clearing on successful directory change, rollback and restored controls after read failure. Real temporary OpenSSH + Windows passed. |
| New-source isolation | Fixed previous dataset reference_column leaking into another CSV import; explicit new-source/history settings remain authoritative. |

Evidence: output/15_path_ux_20261002.json; the AI-complete portion of 15_command_ux_20261002.json (its later source-switch failure is preserved); repaired 15_viewing_presets_20261002.json and final-package 15_pli_view_20261002.json passed. Focused tests: 6 command-template, 9 provider HTTP-contract, 6 fuzzy. Chat Completions was exercised against a real local HTTP server, not claimed as an external-model call. Native connection/draft and structure screenshots were inspected.

Delivery: dist/PymolPlugins-0.13.0.zip. All processes and workers are terminal; the current user installation and global provider/SSH/proxy settings were not changed. Install and fully restart PyMOL. Reproducible fetch and staged native acceptance commands are in justfile and the authoritative completion record.


## Named review filters and generation prompt — 0.14.0 (2026-10-02)

Optional review.json filter_presets provide named expression/sort views. The Filter arrow offers one-click switching with the active group shown; Filter edits, saves, creates and deletes definitions. Views may overlap and can be empty; they do not assign quality labels or alter input files. Changes persist through the existing atomic review.override.json owner. Old bundles remain valid and changing sources clears unrelated presets. New preset fields require 0.14.0+.

Final installed Windows PyMOL 3.1.3.1 evidence: output/16_filter_groups_20261002.json passed. Three input groups, two matched rows, one overlapping row, zero-row group, edit to one row, create/delete, override reopening, invalid-edit preservation, source isolation and unchanged inputs passed. Native Filter/menu/current-group screenshots were inspected. Seven pure contract tests and direct parsing of the documented prompt example passed. Two earlier native-menu harness failures are retained; no AI/WAN/scientific batch was repeated.

The copyable artifact-generation prompt is in docs/structure_annotator_csv_walkthrough.md under 产物整理prompt（含筛选分组）. It retains hardlinks and raw-score aggregation, adds justified literal thresholds/group membership checks, and protects human overrides. No actual run-output organization is claimed without source paths. Deliverable: dist/PymolPlugins-0.14.0.zip; all handles terminal, user installation unchanged.
