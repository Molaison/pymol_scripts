# Filter completion and Enter

Status: reproduced, fixed and verified on Windows PyMOL 3.1.3.1, 2026-09-17.

When the filter's column completion popup is open, Enter, keypad Enter and Tab
must accept the highlighted column and leave the dialog open. Without a popup,
Enter in the filter field applies the expression. Mouse Apply remains explicit.

Reproduce with the actual popup as key-event receiver in the existing Windows
`tests/host/annotator_workflow_smoke.py --full-gui` harness. The previous test
sent Tab only to the line edit and did not cover popup-owned Enter events.
The gate also checks a highlighted suggestion inside a multi-column expression,
filter submission without suggestions, unchanged data/cursor during completion,
and the existing import/filter/command/shortcut/cancellation workflow.

The unchanged 0.8.0 code fails the real Windows test at
`Accepting a column completion submitted the filter`, after completing the
column text but also closing the filter dialog. Session `75515`, exit 1;
`output/annotation-filter-enter-before-0.8.1.json` and `.log`.

The existing `ColumnCompletion.eventFilter` now also owns key events sent to
the completion popup, consuming acceptance before QCompleter can hide the
popup and forward Enter to the line edit's `returnPressed` signal.

After the fix, the same Windows `annotator_workflow_smoke.py --full-gui`
passes in session `83784`, exit 0. The 200-row/128-column test covers popup
Return/keypad Enter, input-field Return/Tab, arrow-highlighted selection inside
an expression, completion without submission and Enter submission without a
popup. Existing filter results, empty/invalid conditions, import, navigation,
command completion, shortcuts and cancellation checks also pass. Evidence:
`output/annotation-filter-enter-0.8.1.json`, `.log` and `.png`.

`python3 scripts/package_plugin.py` generated `dist/PymolPlugins-0.8.1.zip`
(423995 bytes). Both `tests.architecture.test_plugin_package` tests pass in
0.685 s, checking CRC, exact source bytes and isolated bootstrap/menus. Session
`58030`; log `output/annotation-package-tests-0.8.1.log`. No user installation
was changed. Install the ZIP through Plugin Manager and restart PyMOL.
