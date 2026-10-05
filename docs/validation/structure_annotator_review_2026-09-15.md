# Structure Annotator 0.3.0 validation

Date: 2026-09-15
Specification: [annotation fields and score guidance](../annotation_fields_and_score_guidance.md)

## Changes accepted

- Shift selects candidate ranges and Ctrl toggles rows. Selection alone does
  not load the selected structures. Batch save is one durable operation and
  one undo operation, with required-field validation before writing.
- Single-choice, multiple-choice and text annotations support required and
  optional fields. Unedited mixed values remain intact. Export includes every
  saved annotation field.
- Candidate score cards and details respect selected CSV columns. Score ranks
  use the currently filtered candidate cohort and are computed off the GUI
  thread. Display values are rounded; tooltips and export retain precision.
  Absolute rules and relative ranks are labeled separately; unknown score
  directions remain neutral.
- Import data remains visible from every page. Directory and CSV sources are
  explicit. Startup never reads a history snapshot into the UI or loads its
  source, even if that snapshot contains the legacy auto-restore flag.
- Configuration history is searchable. Restore configuration fills import
  inputs and applies workflow settings without loading a structure or changing
  the active source identity. Restore and load applies the chosen snapshot,
  including its filter and columns, ahead of newer sidecar settings. Existing
  data in the live PyMOL session survives closing/reopening the panel.
- Windows directory discovery and direct/cache materialization accept extended
  paths. Failed directory imports display their error and preserve the scene.

## Automated regression

`python3 -m unittest discover -s tests -v` passes **700 tests in 24.661 s**.
This includes the 50,000-candidate/two-field atomic batch/undo case, old scalar
log replay, history search and immutability, source transactions, filtering,
completion contracts and installable package layout.

Evidence: `output/annotation-0.3.0-full-suite.log`.

## Windows desktop acceptance

These are real Windows PyMOL **3.1.3.1**, bundled Python **3.10.16**, PyQt5 and
OpenGL-window checks. Windows processes were launched from `/mnt/c`, not a WSL
UNC working directory. Expected negative cases print tracebacks before the
test verifies the visible error and preserved state.

| Gate | Result |
| --- | --- |
| 50,000-row review; Shift/Ctrl; required/optional/multiple fields | Passed |
| Batch save/undo, loads of unpreviewed structures | Passed; 0 loads |
| Filtered score cohort | 50,000 to 10,000 candidates |
| Same candidate's percentile after filtering | 80.5 to 2.5 |
| Review warm Next median / maximum | 2.51 / 4.55 ms |
| 200-row, 128-column import | 0.0774 s |
| Maximum import heartbeat gap | 33.67 ms |
| Compatibility warm Next median / p95 / maximum | 1.85 / 3.96 / 5.11 ms |
| Compatibility warm Next load/stat calls | 0 |
| Cancel action | 0.81 ms |

The compatibility gate also passes searchable column selection, column-name
completion, invalid/empty/numeric filters, native command completion, Alt+1/2
from panel/editor/viewport/docked/floating-console focus, failed-import scene
preservation, and cancellation/close suppression of late results. A reload
retains the current On-step configuration.

Evidence and screenshots:

- `output/annotation-review-0.3.0.json`, `.png`, `-batch.png`
- `output/annotation-workflow-0.3.0.json`, `.png`
- `output/annotation-import-history.json`, `.png`, `-history.png`, `-long-path.png`

The import/history gate seeded history with `restore_last_dataset=true` and a
newer sidecar containing a conflicting On-step command. Startup performed no
load or history application. Searching for and restoring the older snapshot
selected its command, columns and filter. Restoring settings alone made zero
`cmd.load` calls, including when another dataset was active.

The supplied K: directory has a **215-character directory path**, **72 PDB
files**, and a maximum full path length of **288**. Plain Windows `isfile`
incorrectly rejected the files; extended paths accepted them. Both the shared
directory source and real panel now import all 72; the first object has 200
atoms. The acceptance test redirected state writes to its temporary profile
and checked original size/mtime and sidecar-directory presence remained
unchanged. An additional **341-character directory / 421-character file**
fixture passed direct PyMOL loading, cached materialization and panel import.

Reproduce the desktop gates with Windows PyMOL's bundled `python.exe -I`:

```text
tests/host/annotator_review_smoke.py --rows 50000 --output <review.json>
tests/host/annotator_workflow_smoke.py --full-gui --output <workflow.json>
tests/host/annotator_import_smoke.py --source-directory <directory> --output <import.json>
```

## Package

`just` builds the same installable artifact as `just package`:
`dist/PymolPlugins-0.3.0.zip`, **88 files, 384,039 bytes**.

SHA-256: `19ad8ef596b40cf458bcadb3789cce86f80cd70edc66241ade2075b1b8f91529`.

Archive CRC validation passed. All 86 bundled source files match the current
working tree byte for byte; the two other files are the generated plugin entry
and installation instructions. Evidence:
`output/plugin-package-0.3.0-integrity.json`.

Windows PyMOL's actual Plugin Manager installed the ZIP into a temporary local
directory outside the repository. The installed plugin reported version
**0.3.0**, loaded **11 canonical modules**, registered **71 canonical and 8
compatibility commands**, created all five menus, and imported Structure
Annotator. Evidence: `output/plugin-package-0.3.0-host.json`. This installation
harness supplies a temporary GUI container for Plugin Manager; the review and
import gates above exercise the actual desktop window.

Install the original ZIP through Plugin > Plugin Manager > Install New Plugin,
then restart PyMOL when upgrading an existing installation.

## Compatibility

New batch records use JSONL schema 1.2. The new reader also reads existing 1.0
and 1.1 records without conversion. A 0.2.x reader cannot read a log once it
contains batch events, so keep using 0.3.0 for those annotation logs.

Timing measures the complete Python navigation action and PyMOL command calls;
it is not a general GPU frame-rate guarantee. Cold remote I/O depends on the
filesystem. These checks cover Windows PyMOL 3.1.3.1; PyMOL 2.x and other Qt
bindings are separate compatibility gates.
