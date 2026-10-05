# Structure Annotator 0.5.1 validation

Status: completed for Windows PyMOL 3.1.3.1 / PyQt5 on 2026-09-16.
Runtime, regression, final ZIP and real Plugin Manager installation gates pass.

## Contract

`save_on_label_click` defaults to false; `auto_advance` remains independent.
Both are available in the review more-actions menu and Settings / Annotations.
Single-choice button/dropdown user actions may save one candidate, but only
after all required fields are complete. Multiple-choice fields, batch targets
and Shift+click remain drafts until an explicit save. Restoring widgets does
not write annotations. Failed writes must not advance or discard the draft.
Preferences persist in dataset state and global configuration history.

## Investigation

- The 0.4.0 ZIP's `review_controller.py` confirms `quick_label` immediately
  saved unless Shift was held. In 0.5.0 the click path only stages values, while
  `auto_advance` still applies after an explicit Save. This explains why the
  existing setting did not restore one-click annotation.
- The 0.4.0/current layout comparison retains next-unreviewed, jump, reload,
  undo, filter/sort, field configuration, command hooks, preloading and history
  through menus/settings. A broader diff after the user's request to self-audit
  identified two further losses: source-row/status columns were unconditionally
  hidden without a toggle, and the old all-saved-annotations summary had no
  replacement for fields outside the current configuration.
- The 0.4.0 `quick_digit` delegated to the primary label handler, which returned
  after toggling a multiple-choice value. The 0.5.0 unconditional shortcut Save
  changed that behavior. Restore multiple-choice Alt+digit as a draft toggle;
  single-choice shortcuts still save exactly once.
- Restore persisted Source row / File status actions in More / List columns,
  defaulting to hidden; toggles must not reload or reorder the dataset. Structure
  details now includes all saved annotations with full-value tooltips.
- The read-only scout returned upstream HTTP 502 without edits. The main thread
  performed the bounded archive/current-source comparison and owns integration.
- Existing dirty changes are preserved; no commit or user configuration edit
  is part of this task.

## Planned verification

- Core configuration and history tests: both boolean values, invalid values,
  legacy defaults and per-dataset restoration.
- Real Windows `tests/host/annotator_layout_smoke.py`: existing visible workflow
  and normal/small layouts plus the click-save contract above. Fixtures, history,
  mappings and cache are isolated in a Windows temporary directory.
- Existing Windows workflow, 50,000-row review and import checks, followed by
  `python3 -m unittest discover -s tests -v`.
- `just package`; audit the final `dist/PymolPlugins-0.5.1.zip` using the existing
  source-byte/CRC/version package test and real Windows Plugin Manager installer.

Windows checks run serially from `/mnt/c` using
`/mnt/d/Softwares/Pymol3.1/python.exe -I` and repository UNC paths. Logs, JSON and
screenshots use the existing `output/` directory. Each submitted process is
supervised with blocking terminal waits, then its exit and outputs are checked.
PyMOL 2.x and other Qt bindings are not established by Windows PyQt5 results.

## Execution record

- Core preference/history session 44318 passed 77 tests in 1.005 s.
- Windows session 51718 passed click-save, required-field gating and save-without-
  advance, then failed at a settings checkbox gesture. The screenshot confirms
  the harness clicked the blank center of the full-width checkbox row; it now
  clicks the native checkbox indicator. Menu/checkbox synchronization remains
  asserted rather than bypassed with programmatic state changes.
- Session 28956 stopped before column assertions because the synthetic hover did
  not open the native submenu. The harness now clicks the visible List columns
  menu item, then clicks its checkbox action. No production behavior was changed
  in response to that harness failure.
- Session 77102 passed the full Windows layout/click-save suite, including
  optional columns without loading/reordering, all-saved-label details, jump,
  next-unreviewed, reload, write-failure recovery and preference persistence.
  The final follow-up adds explicit multiple-choice shortcut coverage and
  screenshot/header checks before release.
- Session 92657 exited 0 and passed the final layout/click-save suite, including
  multiple-choice shortcuts, dropdown user selection versus programmatic
  restoration, failure/retry, history/dataset preference round trips, optional
  columns, complete annotation details, jump, next-unreviewed and reload.
  Both 1000 x 720 and 760 x 560 windows pass; the OpenGL sample has 83 colors.
  Save-option and annotation-details screenshots were visually checked.
- Session 4381 exited 0: full Windows workflow (`--full-gui`), 200 rows x 128
  columns; import 0.137 s, warm Next median/p95/max 2.09/3.10/4.91 ms, zero
  click-handler load/stat calls. Shortcuts across panel/viewport/console,
  completion, filters, failed-import preservation and cancellation all pass.
- Session 68193 exited 0: 50,000-row Windows review (`--rows 50000`),
  Shift/Ctrl selection, required/optional/multiple fields, batch save/undo,
  score filtering and export pass. Batch operations made no structure loads;
  warm Next median/max was 2.77/3.56 ms.
- Session 51122 exited 0: explicit history restore, chosen-snapshot precedence,
  configuration-only isolation, 341-character directories, 421-character
  structure paths, direct/cache loading and live-session preservation pass.
- Session 53153 exited 0: profile create/test/save/duplicate/delete, invalid
  regex preservation, preview isolation, format restoration, command drafts,
  warm/cold parser failure recovery and camera restoration pass. Profile
  geometry and visible path feedback pass at 980 x 740 and 760 x 560.
- Session 87736 exited 0: `python3 -m unittest discover -s tests -v`,
  716 tests pass in 24.241 s. Log: `output/annotation-0.5.1-full-suite.log`.
- The final read-only code scout also returned upstream HTTP 502 without edits.
  Main-thread review checked label signal routing, save completion, preference
  persistence/fingerprints, column visibility and all-saved-annotation details.
  Final workflow, review and compact profile-result screenshots were inspected.

## Old-feature audit

The comparison used the 0.4.0 archive and the current canonical implementation.
Entries marked retained were moved in the 0.5.0 layout rather than removed.

| Feature | Finding and 0.5.1 entry point | Evidence |
| --- | --- | --- |
| Label-click save/next | Restored as optional Save on label click; independent auto-advance in Settings / Annotations and More actions | Layout click-save/manual/stay/Shift/required/batch/failure checks |
| Multiple-choice Alt+digit | Restored draft-only toggling; single-choice shortcuts still save once | Layout shortcut checks and full workflow focus checks |
| Source row and file status | Restored configurable columns under More actions / List columns | Layout visibility, persistence and no-load/no-reorder checks |
| All stored annotations | Restored in More actions / Structure details, including fields no longer configured | Layout historical-label details and close/reopen checks |
| Jump, next-unreviewed and reload | Retained in More actions | Visible layout menu checks |
| Save/whole-batch undo | Retained as primary Save and More actions / Undo last save | Layout and 50,000-row review |
| Filtering, sorting and score columns | Retained in Filter and Scores | Workflow and 50,000-row review |
| Field configuration and score rules | Retained in Settings / Annotations and More actions | Layout dialog geometry and review field/score checks |
| On-step/bucket commands and command history | Retained in Settings / Automation; Apply commands is explicit | Workflow completion and mapping command-recovery checks |
| Keep previous, compressed input, skip annotated, preload/cache options | Retained in Settings / Loading and preloading | Control/config audit; warm-navigation checks cover preload behavior |
| Open/export/save configuration and history | Retained in Data; file mapping is under Open data / File locations | Import/history, mapping and review export checks |

## Final runtime evidence

All terminal sessions below exited 0. Their JSON status and expected artifacts
were inspected after completion. Runs use isolated Windows temporary fixtures,
history, mappings and cache; no user installation or production source was edited.

| Gate | Session | Evidence in `output/` |
| --- | --- | --- |
| Click-save, restored controls and normal/compact layouts | 92657 | `annotation-layout-0.5.1.json`, `.log` and named PNGs |
| Full Windows workflow | 4381 | `annotation-workflow-0.5.1.json`, `.log`, `.png` |
| 50,000-row review | 68193 | `annotation-review-0.5.1.json`, `.log`, `.png` |
| Import, history and long paths | 51122 | `annotation-import-0.5.1.json`, `.log` and named PNGs |
| Profile editing and command recovery | 53153 | `annotation-mapping-0.5.1.json`, `.log` and named PNGs |
| Full regression, 716 tests | 87736 | `annotation-0.5.1-full-suite.log` |
| Final ZIP build | 93138 | `package-0.5.1.log` |
| Final ZIP source bytes, CRC and version | 68802 | `package-0.5.1-audit.json` |
| Real Windows Plugin Manager installation | 9384 | `plugin-package-0.5.1.json`, `.log` |

Host commands, run from `/mnt/c` with the paths quoted to preserve backslashes:

```sh
repo='\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins'
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_layout_smoke.py" --output "$repo\output\annotation-layout-0.5.1.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_workflow_smoke.py" --full-gui --output "$repo\output\annotation-workflow-0.5.1.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_review_smoke.py" --rows 50000 --output "$repo\output\annotation-review-0.5.1.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_import_smoke.py" --output "$repo\output\annotation-import-0.5.1.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_mapping_smoke.py" --output "$repo\output\annotation-mapping-0.5.1.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\plugin_package_smoke.py" "$repo\dist\PymolPlugins-0.5.1.zip" --output "$repo\output\plugin-package-0.5.1.json"
```

## Final package and boundary

- Build: `just package`, session 93138, exit 0. Existing Bash startup warnings
  about missing environment files and `zoxide` remain in the build log; they
  did not prevent packaging.
- Artifact: `dist/PymolPlugins-0.5.1.zip`, 399161 bytes, 92 entries.
- SHA256: `e9b3743fa8662ab71ca2874904ac2f85b1be5dd578e271be6127903d46517f42`.
- Archive audit: session 68802, exit 0. Reused
  `PluginPackageTests.test_single_plugin_package_contains_complete_runtime`
  with the instance's `archive` set to the delivered ZIP. CRC, versioned
  bootstrap, every runtime/entry-point Python source byte and absence of
  compiled artifacts pass; this did not validate a separately rebuilt archive.
- Windows installer: session 9384, exit 0. The real Plugin Manager installed
  version 0.5.1 and loaded 11 canonical modules, 71 canonical commands,
  8 compatibility commands, 5 menus and Structure Annotator successfully.
  The temporary plugin directory and Qt menu container isolate the check from
  the user's installed plugin and preferences. GUI interaction checks used
  the byte-matched source tree.
- `git diff --check` passes. Existing working-tree changes remain preserved
  and uncommitted. Upgrade by installing this ZIP and restarting PyMOL.

No implementation or release blocker remains for the tested Windows
PyMOL 3.1.3.1 / PyQt5 target. PyMOL 2.x, other Qt bindings and optional external
scientific backends are not validated by these gates.
