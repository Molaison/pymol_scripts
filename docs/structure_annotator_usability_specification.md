# Structure Annotator usability specification

## 2026-09-17 SSH sources and selection gestures - 0.9.0

- Use native OpenSSH Host profiles for remote folders, CSV/TSV headers and
  files. Preserve raw path spelling, remote relative/home paths and stable
  source identity. Browse asynchronously and reuse the existing structure
  cache/preload flow. Connection failures and cancellation preserve the scene.
- Keep SSH annotation/configuration state locally per source. Read remote
  review defaults and persist user overrides locally; remote inputs are unchanged.
- Share deterministic fuzzy matching across header/condition completion,
  column/choice search and source history. Accept exact original values with
  Tab/Enter without forwarding the acceptance key into filter submission.
- Multiple-choice plain clicks stage choices. Ctrl+click saves one candidate
  and advances after success by default, independent of ordinary click-save
  settings. Preserve required-field gating, Shift, explicit batches and retries.
- Evidence: [SSH and interaction validation](validation/structure_annotator_ssh_fuzzy_2026-09-17.md).

## 2026-09-17 embedded and floating workspaces - 0.7.0

The annotator and batch tool now share native PyMOL docking, a Window mode
menu and independent persisted geometry. Mode changes keep the same content,
drafts and active work. Close guards, shortcut ownership and background-result
isolation remain in their existing controllers. The full contract and real
Windows acceptance results are in [workspace window modes](workspace_window_modes.md).

## 2026-09-16 optional click-to-save restoration - 0.5.1

Status: implemented, packaged and installation-checked. The 716-test regression,
real Windows PyMOL 3.1.3.1 workflows, ZIP source-byte/CRC checks and real Plugin
Manager installation pass. Artifact: `dist/PymolPlugins-0.5.1.zip`.

- Add a persisted `save_on_label_click` setting, off by default to retain 0.5.0
  manual-save behavior. Expose it in Settings / Annotations and the review
  more-actions menu, alongside the independent existing `auto_advance` setting.
- With click-to-save enabled, choosing a primary single-choice label saves one
  candidate and advances only if auto-advance is enabled. Buttons and long-list
  dropdowns use the same save pipeline. Programmatic field restoration must not
  submit annotations, and single-choice Alt+digit must still save exactly once.
  Multiple-choice Alt+digit only toggles the draft value, as in 0.4.0.
- Required fields gate every save. Batch selections and multiple-choice fields
  remain explicit saves. Shift+click stages a value without committing, matching
  the 0.4.0 interaction. A failed write keeps the cursor and draft intact.
- Restore optional source-row/file-status list columns and all saved annotations
  in Structure details. Simplifying the default UI must not remove capabilities;
  preserve them through visible menus or settings and verify the moved controls.
- Check persistence in dataset state and global history, visible menu/settings
  controls, manual/automatic/stay modes, Shift, required fields, batches, write
  failure/retry, dropdowns and reopen in Windows PyMOL. Run the final regression
  and build/install-check 0.5.1. Evidence belongs in
  [the click-save record](validation/structure_annotator_click_save_2026-09-16.md).

## 2026-09-16 visual hierarchy redesign — 0.5.0

Status: implemented, visually checked and packaged. The final 714-test suite,
Windows PyMOL 3.1.3.1 workflows and real Plugin Manager installation pass.
Evidence: [0.5.0 validation](validation/structure_annotator_layout_2026-09-16.md).

Design contract:

- The review page has a quiet candidate list and an unframed current-candidate pane.
  Candidate name, progress and annotation state each appear once. One filled
  primary button saves the visible choices and describes whether it advances.
- Mouse choices stage values. Save is explicit; the existing Alt+digit quick-save
  shortcuts remain available. Optional fields are opened on demand. Required
  fields stay visible, and incomplete values cannot be committed.
- File operations live in the dataset menu; Settings is a separate page with
  a Back action. Remove the permanent tab strip, duplicate import/history
  buttons, normal-state log text and inline tutorials.
- Filters/sorting, metadata, jump/reload, field setup and score rules are
  available from named controls or menus when needed. They do not occupy
  permanent rows around the main annotation task.
- Opening data initially asks only for folder/CSV and its file location.
  CSV column selection appears after reading the header. Mapping and parser
  options remain accessible through compact disclosures. Loading does not
  automatically open another configuration popup.
- Use consistent native-sized typography, spacing, subdued borders and one
  accent color. Apply styling locally to the annotator, not the PyMOL window.

Acceptance: walk through opening data, selecting a label, saving/advancing,
batch selection, undo, filtering, changing file mappings and returning from
settings in real Windows PyMOL, using visible controls. Check actual screenshots
at normal and small window sizes, primary-button count, lack of hidden-path
dependencies, and unchanged warm navigation/CSV cancellation behavior. Preserve
the canonical model, stored annotations and all 0.4.0 mapping capabilities.

Execution resumed from session `01a0a7ff-45e5-7481-9343-a153243d7f9f` after
the layout edits were interrupted. The main thread completed status refresh,
draft preservation, required-field gating and final UI integration. Delegation
attempts returned upstream HTTP 502 without edits. Existing changes are retained.

Completed gates: Save stays visible at 760 x 560 with six scores and long names;
visible import, filtering, settings, batch save/undo and all existing Windows
workflow checks pass. Profile rule tables use separate tabs and a scrolling
form with fixed Save; combo text, headers and both configuration dialogs fit
at normal and compact sizes. Test path scrolls its result into view. The
50,000-row review workflow, cancellation, command recovery, long paths and
714-test regression pass. The final ZIP passes CRC, source-byte equivalence
and real Windows installer checks. Tests used isolated temporary fixtures;
JSON, screenshots and logs are in the existing `output/` directory.

Artifact: `dist/PymolPlugins-0.5.0.zip`. No blocker remains for the tested
Windows PyMOL 3.1.3.1 / PyQt5 target. PyMOL 2.x and other Qt bindings remain
unverified; the package was not installed into the user's existing plugin folder.

## 2026-09-16 follow-up: import, mapping profiles and switching

Status: implemented. The 714-test suite and real Windows PyMOL 3.1.3.1 GUI
checks pass. Existing working-tree changes are retained. Evidence and package:
[0.4.0 validation](validation/structure_annotator_mapping_2026-09-16.md).

- Import inputs are pending configuration. Mapping previews have their own
  result window and never replace the active review table or selection.
- Choose a saved source-server profile and this computer's path format
  (Custom by default). Create, edit, duplicate and delete named profiles in the
  GUI. Persist ordered prefix removal, prefix addition and regex rewrites,
  root mappings, source/target formats and an explicit WSL distribution.
  Root mappings accept Windows and POSIX endpoints. Show a sample's raw and
  resolved path before saving. Existing x_k and console commands remain usable.
- WSL is a path-format choice. Optional HTTP file transfer belongs under an
  explicit access-method selector; its root/alias fields are hidden otherwise.
- Command edits remain drafts until Apply commands. Validate placeholders
  before application, pass the current object explicitly, and report parser
  failures with the command line. A failed switch restores the previous
  cursor and displayed object without re-running the failing command.
- Acceptance: mapping/store regressions; command failure and cursor recovery;
  real Windows PyMOL profile editing, preview isolation, import, warm/cold
  switching and draft-command checks; existing workflow gates; installable ZIP.
- Investigation evidence: Windows command history includes unfinished
  placeholders and commands without arguments; current UI synchronizes editor
  text on navigation. Current resolver only maps POSIX roots to Windows roots.
  Scout and worker delegation returned upstream HTTP 502 without edits.
- Final behavior is verified with profile editing, invalid-regex preservation,
  preview isolation including multi-selection, computer-format overrides,
  explicit command application, real missing-selection parser failures with
  warm/cold navigation and camera restoration, import/history/long paths, and
  the existing 50,000-row review workflow.


Date: 2026-09-14
Status: implemented and validated on 2026-09-15; release 0.2.1 is packaged.

## Execution record

- Implemented: lightweight CSV header discovery, early mapping validation,
  cancellable record parsing, shared schema completion, a searchable column
  picker, interpreted numeric/boolean column conditions, and a combined review
  and annotation page with separate source/settings pages.
- Implemented: immutable background configuration writes, cached annotation
  reads for display, deferred preload maintenance, asynchronous cold navigation,
  and PyMOL-native Tab completion without per-keystroke command history writes.
- Implemented: application-wide Qt annotation shortcuts; source-field edits
  remain pending until successful import. Preloads disable automatic zoom and
  no longer force a draw before hiding their objects.
- Current local checks: the full 687-test suite passes, including 172 annotation tests. New CSV-header/filter tests
  are included. The updated core stub now includes the new view summary label.
- Current real Windows check: `tests/host/annotator_workflow_smoke.py` passes
  import, preloaded navigation, column selection, numeric/boolean filters,
  visible empty/error filters, native command completion, and Alt+1/Alt+2 with
  panel/table/editor focus. A 200-row, 128-column synthetic CSV imported in
  0.314 s; warmed Next p95 was 3.21 ms with zero main-thread load/stat calls.
  These figures precede the latest preload-camera and failed-source-state fix.
- Evidence: `output/annotator-tests-current.log`,
  `output/annotator-workflow-current.json`, and its PNG screenshot.
- Final Windows checks pass in the actual PyMOL 3.1.3.1 window, including the
  viewport, docked/floating console, keyboard completion, cancellation, panel
  close, late-result isolation, and failed-import preservation of source state.
  The 50,000 x 128 import took 2.701 s; warm Next p95 was 3.10 ms. The current
  K-drive CSV (199 rows x 125 columns) imported in 0.143 s after copying the CSV
  for isolation while continuing to read real K-drive structures; warm Next
  p95 was 2.80 ms. Both warm-navigation checks made zero load/stat calls.
- `dist/PymolPlugins-0.2.1.zip` was built and installed successfully with the
  real Windows Plugin Manager installer. Final evidence and limitations are in
  [the validation record](validation/structure_annotator_usability_2026-09-15.md).
- No implementation or packaging blockers remain for the tested Windows
  PyMOL 3.1.3.1 target. PyMOL 2.x remains a separate compatibility gate.
- Implementation delegation was attempted; workers returned upstream 502 and
  did not edit files. All current implementation/integration is in this thread.

This specification incorporates both user feedback messages. It replaces the
old three-tab/all-columns/history-only completion assumptions for the proposed
UI, while retaining the canonical annotation and navigation owners.

## Problems and proposed behavior

| # | Problem as understood | Proposed behavior |
| --- | --- | --- |
| 1 | Import does not reliably reach a usable completed state. This is a correctness issue separate from import duration. | Show explicit reading/preparation/commit/success/error states. Keep diagnostics visible. A failed or cancelled replacement preserves the active dataset and scene. Reproduce the actual failing branch before claiming a root cause. |
| 2 | Scores and annotation are separated, forcing users to change views while judging a structure. | Put the score table, current row, annotation values, and navigation controls in one primary workspace. |
| 3 | Column fields have no useful completion from the current CSV. | Read the header without requiring a valid path-column selection first. Share that schema across path/ID selection, displayed columns, filter columns, and sorting; refresh it when the source changes. Historical column names are not the schema. |
| 4 | Large imports take much longer than expected. | Measure parsing, path resolution, cache preparation, commit, and first usable display separately. Validate headers before reading the whole file, reuse a current parsed snapshot, avoid repeated row scans, and prioritize current/nearby structure preparation instead of blocking usability on every remote file check. Unchecked paths remain explicitly unchecked. |
| 5 | All score columns appear initially; users need searchable multi-selection. | Initially show only row/status/structure/annotation information. Provide a searchable checkbox column menu with select all, clear all, selected count, and explicit apply. Persist the user's selection per source. Column visibility changes reuse loaded data. |
| 6 | Next remains slow even when the next structure has already been preloaded. | The preloaded navigation path enables the resident object, updates the current row, and returns without synchronous file reads, configuration writes, or full-table rebuilding. Refill preloads outside the click handler. Coalesce cursor/config saves in the background; keep annotation durability and errors explicit. Cold/unready structures have a visible preparation state. |
| 7 | Layout and flat settings obscure the main task and make interaction difficult. | Keep scores, annotation actions, position, previous/next, and undo prominent. Put filters and the column menu in the table toolbar; data-source input and grouped advanced settings have a separate settings surface. Details are collapsible, panes resizable, and small windows scroll settings without hiding primary actions. |
| 8 | Alt+1/Alt+2 work only after Ctrl+E floats the PyMOL output window. | Treat this as a focus/routing defect until a PyMOL-version defect is demonstrated. Use one owned Qt shortcut route for the PyMOL working windows, retain a single annotation action implementation, avoid duplicate dispatch, and release bindings on close. Test viewport, panel, and console focus without requiring Ctrl+E. |
| 9 | On-step completion accumulates copies while typing and does not behave like the PyMOL command line. | Reuse PyMOL's command/argument/selection completion semantics and complete the current token, including supported template placeholders. Keep one popup/model, replace and deduplicate candidates, and use Tab completion. Separate accepted command history from syntax completion; never record intermediate keystrokes as completed commands. |
| 10 | CSV filtering does not produce the expected view and lacks column-name completion. | Distinguish global text search from column conditions. Offer searchable schema-based columns and appropriate operators/values, numeric comparisons, explicit empty-value handling, visible result counts and errors, and clear/reset. The same filtered row order drives the table, navigation, and progress. Empty results are visible and suspend navigation. |

## Shared architecture and consistency requirements

- One `AnnotationDataset` schema and stable entry IDs underpin scores,
  annotation, filtering, completion, and export. Do not duplicate row ownership.
- One filtered navigation view and current entry drive all primary controls.
  A displayed-column choice does not restrict which columns may be filtered.
- Displayed columns are view state. Choosing columns must not re-import the CSV,
  re-materialize a structure, or erase annotations.
- PyMOL object operations and Qt model updates stay on the main thread.
  Reading/parsing/path/cache preparation and captured configuration writes may
  run in workers. Workers do not access mutable live `STATE` or widgets.
- Preserve transactional source replacement, preview/source isolation,
  cancellation/epoch checks, row annotation identity, undo/export semantics,
  and ownership of existing user objects.
- Completion has two distinct sources: the active CSV schema for column fields,
  and PyMOL syntax for command fields. They do not use one generic history list.

## Evidence collected before implementation

- The current GUI creates Source, Annotate, and Scores tabs and defaults an
  empty display-column input to every non-path CSV column.
- Column completion currently uses `_HistoryCompletionBinding`, whose
  suggestions come from global configuration history rather than CSV headers.
- `_next` invokes `_sync_to_state()` with persistence enabled; the subsequent
  `_navigate_annotation()` saves dataset state again. These writes can target
  a mapped remote dataset directory. Table refresh also checks the annotation
  sidecar's revision.
- The On-step editor's `textChanged` signal starts a 300 ms timer whose
  `_save_ui_state` callback records global command history. This incorrectly
  mixes intermediate edits with finalized command history. Whether repeated
  signal binding also contributes to duplicate popups requires reproduction.
- `_entry_filter_match` is literal substring search across values, not a column
  expression evaluator. `_apply_view` ignores the filter operation result and
  immediately refreshes ordinary status, obscuring failure feedback.
- Existing baseline checks still pass: 166 annotation tests on WSL and the
  50,000-row Windows PyQt5 table benchmark. The latter's cursor-only refresh
  p95 is 0.0193 ms, which does not measure CSV import or the complete Next action.
  Evidence: `output/annotator-tests-before.log` and
  `output/annotator-qt-performance-before.json`.
- Read-only Windows history inspection confirms use of a `K:` CSV with
  `structure_file` as the path header and many metadata columns. No production
  CSV, annotation sidecar, or user history was changed by this investigation.
- The exact import-failure branch and focus-dependent shortcut behavior have
  not yet been reproduced in the real user workflow.

## Implementation and acceptance order

1. Record a consumer-style Windows reproduction covering real CSV import,
   filtering, annotation, preloaded navigation, and keyboard focus.
2. Fix import lifecycle and the navigation/persistence performance boundary.
3. Add shared CSV-header discovery, the column picker, and typed filtering.
4. Integrate the primary workspace, settings hierarchy, command completion,
   and shortcut routing without introducing parallel controllers.
5. Run focused core regressions and real Windows Qt/PyMOL workflow checks;
   measure import phases and full click latency, inspect the layout, and build
   the installable ZIP with `just package`.

For preloaded Next, instrument `cmd.load`, file probes, and persistence calls
inside the click handler as well as elapsed time. For import, test success,
failure, cancellation, source replacement, and wide/large CSVs. For filtering,
prove table/navigation agreement and visible empty/error results. Test shortcut
focus in the viewport, panel, and docked/floating console. Verify completion
lists neither accumulate copies nor write partial inputs to history.

## Design references

- Qt model thread ownership and queued updates:
  https://doc.qt.io/qt-6/qabstractitemmodel.html#thread-safety
- Qt header sizing and bounded content measurement:
  https://doc.qt.io/qt-6/qheaderview.html#resizeContentsPrecision
- Qt completion model:
  https://doc.qt.io/qt-5/qcompleter.html
- Column visibility management and bulk controls:
  https://mui.com/x/react-data-grid/column-visibility/
- Searchable column tools and deferred application:
  https://www.ag-grid.com/javascript-data-grid/column-tool-panel/

Qt 6 documentation is used for general model/view rules; implementation must
use APIs available in the target PyQt5/PySide2 runtime. The primary workspace
layout is a design decision for this annotation task, not an upstream mandate.
