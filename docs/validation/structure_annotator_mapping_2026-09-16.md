# Structure Annotator 0.4.0 validation

Status: passed; installable release 0.4.0 is packaged and installer-verified.

## Change and acceptance contract

- Named server profiles persist ordered prefix/regex rewrites, Windows/POSIX
  root mappings, source/computer formats and explicit WSL distribution names.
  Profile sample tests and import previews do not write the mapping store.
- Import previews use a separate window. The active review, scene and candidate
  multi-selection remain available. Worker transfer is an explicit file-access
  choice, with its root and alias grouped together.
- On-step and bucket edits remain drafts until Apply commands. Invalid
  placeholders cannot replace applied commands. Checked PyMOL dispatch reports
  parser failures; failed navigation restores cursor, object visibility and
  camera without re-running the failing template or deleting the old object.

## Automated regression

`python3 -m unittest discover -s tests -v` passes **714 tests in 23.617 s**.
Coverage includes ordered rewrites, invalid regex/replacement diagnostics,
bidirectional root mappings, WSL drive/distribution conversions, POSIX case
sensitivity, old-schema reading, preservation of invalid/future configuration,
worker paths after text rewriting, and navigation rollback without preloading.
`compileall` and `git diff --check` also pass.

Evidence: `output/annotation-0.4.0-full-suite.log`. Final full-suite terminal
session 30595 and Windows acceptance session 84761 both exited successfully;
their expected JSON artifacts were checked before accepting the result.

## Windows desktop acceptance

Windows PyMOL **3.1.3.1**, bundled Python **3.10.16**, PyQt5 and the actual
OpenGL window were used. Every gate passes:

| Gate | Result |
| --- | --- |
| Create, sample-test, save, duplicate and delete a server profile | Passed |
| Invalid regex leaves the saved mapping file unchanged | Passed |
| Preview preserves dataset, scene and candidate multi-selection | Passed |
| Per-dataset computer-format override survives reopening | Passed |
| Unapplied and incomplete command edits cannot replace applied commands | Passed |
| Actual missing-selection parser error, warm and cold navigation | Passed; prior cursor, object visibility and camera restored |
| Current-object selection and Python-style `util.cba` commands | Passed |
| Directory/CSV history restore and 421-character Windows paths | Passed |
| 50,000-row review, Shift/Ctrl selection, required/multiple fields, batch save/undo | Passed; zero batch structure loads |
| Filters, column selection, native completion, panel/viewport/docked/floating console shortcuts | Passed |
| Cancellation, close/late-result isolation, failed-import preservation | Passed |

The 200-row × 128-column CSV imported in **0.086 s**; maximum import heartbeat
gap was **34.87 ms**. Warm Next median/p95/maximum was **1.51/2.18/2.20 ms**, with
**zero load/stat calls**. The separate prefixed-path profile fixture imported
in **0.058 s**. The 50,000-row review had median/maximum Next **1.50/2.09 ms**.

Records and screenshots:

- `output/annotation-mapping-0.4.0.json`, `.png`, `-profile.png`, `-import.png`
- `output/annotation-workflow-0.4.0.json`, `.png`
- `output/annotation-import-0.4.0.json`, `.png`, `-history.png`, `-long-path.png`
- `output/annotation-review-0.4.0.json`, `.png`, `-batch.png`

Run from a Windows-local working directory with PyMOL's bundled Python:

```text
python.exe -I tests/host/annotator_mapping_smoke.py --output <mapping.json>
python.exe -I tests/host/annotator_workflow_smoke.py --full-gui --output <workflow.json>
python.exe -I tests/host/annotator_import_smoke.py --output <import.json>
python.exe -I tests/host/annotator_review_smoke.py --rows 50000 --output <review.json>
```

The scripts accept absolute repository/UNC paths. Expected negative cases print
tracebacks before the test checks the visible error and preserved state. The
first mapping harness closed PyMOL before writing JSON; its zero exit code was
not counted as success. The corrected harness writes results before `cmd.quit`.

## Package

`python3 scripts/package_plugin.py` (also `just package`) built
`dist/PymolPlugins-0.4.0.zip`: **91 files, 393,904 bytes**.

SHA-256: `19e01dffd2c139c1669cb3faa6a5d43b700d2e06355d7cedf30ec4a83fc2d484`.

Archive CRC verification passed. All **89 bundled source files** match the
working tree byte for byte; the other two files are the generated plugin entry
and installation instructions. Windows PyMOL's actual Plugin Manager installed
the archive in a temporary directory outside the repository: version **0.4.0**,
**11 canonical modules**, **71 canonical commands**, **8 compatibility commands**
and **5 menus**. Structure Annotator imported from the installed archive.
Evidence: `output/plugin-package-0.4.0-host.json` (terminal session 87494 passed).

Install the ZIP through Plugin > Plugin Manager > Install New Plugin, then
restart PyMOL when replacing an earlier version.

## Boundaries

The desktop checks cover PyMOL 3.1.3.1. PyMOL 2.x and other Qt bindings remain
separate compatibility gates. WSL format conversion and rewritten worker
coordinates have regression coverage; a Windows-PyMOL-to-worker network transfer
was not part of these desktop checks. The installer harness supplies a temporary
GUI container; the four acceptance scripts above exercise the desktop window.

Scout/worker delegation returned upstream HTTP 502 without file changes;
implementation, review and final integration were completed in the main thread.

All GUI fixtures, history, mapping files, structures and cache writes are
isolated in a Windows-local temporary directory. Existing user configuration
and production structure files are not changed by these checks.
