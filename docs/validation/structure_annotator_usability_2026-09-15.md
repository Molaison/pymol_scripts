# Structure Annotator 0.2.1 validation

Date: 2026-09-15
Specification: [usability specification](../structure_annotator_usability_specification.md)

## Delivered behavior

- Scores, current-row annotation, and navigation share one review page. Source
  input and grouped settings have separate pages; detailed metadata is hidden
  until requested.
- CSV headers are discovered without requiring a valid path-column selection.
  Column completion inserts only on acceptance, rather than rewriting partial
  input when a candidate is highlighted.
- A searchable checkbox menu controls visible columns, with select-all,
  clear-all, and explicit apply. New imports initially show required columns.
- Typed column expressions support numeric/boolean comparisons, and/or/not,
  parentheses, contains, and empty-value checks. Hidden columns remain usable
  for filtering and sorting. Empty results and invalid expressions are visible.
- Pending source edits do not overwrite the active source contract after a
  failed import. CSV parsing, path preparation, and captured settings writes
  are separated from GUI work.
- Warm navigation does not load files, stat files, or synchronously save
  settings. Preloads disable automatic zoom and do not force a draw before
  hiding their objects.
- Alt+1/2 and navigation shortcuts use one Qt route across the panel, viewport,
  and console. Closing the panel disables that route and restores PyMOL input.
- On-step and bucket commands use PyMOL's own Tab-completion entry point.
  Intermediate command edits do not become history entries.

## Automated checks

`python3 -m unittest discover -s tests -v` passed **687 tests** in 22.665 s.
This includes CSV parsing/identity parity, annotation persistence and undo,
source transactions, header discovery, filter interpretation, and package
layout. Evidence: `output/annotator-full-suite.log`.

The real Qt workflow uses Windows PyMOL 3.1.3.1, its bundled Python 3.10, PyQt5,
and an actual PyMOL Qt/OpenGL window. It uses disposable data and a temporary
annotation/history/cache profile. The negative checks deliberately emit
tracebacks for invalid filters and missing path-column mappings, then verify
that the old scene/source remains usable.

The compatibility command is:

```bash
/mnt/d/Softwares/Pymol3.1/python.exe -I \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\annotator_qt_smoke.py' \
  --full-gui \
  --output '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\output\annotator-qt-final.json'
```

Run Windows processes from a normal Windows-local working directory (`/mnt/c`
in these WSL invocations).

The 200-row, 128-column workflow passed:

| Measure | Result |
| --- | ---: |
| Import after header discovery | 0.0616 s |
| Maximum import heartbeat gap | 21.47 ms |
| Warm Next median / p95 / maximum | 1.44 / 3.37 / 3.87 ms |
| Main-thread load/stat calls inside warm Next | 0 |
| Cancel return | 0.89 ms |

The same run verifies keyboard column acceptance without duplicated input,
column search/check/apply/all/clear, hidden-column numeric sorting and filtering,
empty and erroneous filters, native command completion without history growth,
Alt+1/2 with panel/editor/viewport/docked/floating console focus, source-contract
preservation after failed import, cancelled old results arriving after a newer
load, and panel-close suppression of late results. The original PyMOL Alt+1
binding becomes available again after panel close.

## Actual mapped-drive input

`--from-history --full-gui` exercised the user's current `trunc_200.csv`:
**199 data rows and 125 columns**. The CSV was copied to a disposable local
directory, while its relative structure paths continued to resolve to the real
`K:` directory. This preserves the source CSV and original annotations.

| Measure | Result |
| --- | ---: |
| Read CSV from K: into the test copy | 0.0438 s |
| Import after header discovery | 0.1428 s |
| Warm Next median / p95 / maximum | 1.90 / 2.80 / 3.18 ms |
| Main-thread load/stat calls inside warm Next | 0 |

The source CSV hash remained unchanged. This run covers real CIF loading,
cache preparation, navigation, and the PyMOL window; its UI/filter checks are
explicitly marked `not_run`, since those use the controlled fixture above.
Evidence: `output/annotator-real-input.json` and its screenshot.

## Large input and package

The full-window **50,000-row, 128-column** import/navigation check passed:

| Measure | Result |
| --- | ---: |
| Import after header discovery | 2.7010 s |
| Maximum import heartbeat gap | 84.24 ms |
| Warm Next median / p95 / maximum | 1.44 / 3.10 / 3.62 ms |
| Main-thread load/stat calls inside warm Next | 0 |

The wide-table run uses `--rows 50000 --columns 128 --performance-only
--full-gui` with the workflow harness. Its UI-specific checks are marked
`not_run`, since the separate controlled workflow above covers them. Evidence:
`output/annotator-large-input.json` and its screenshot.

`just package` generated `dist/PymolPlugins-0.2.1.zip`: **82 files, 364,742 bytes**.
SHA-256: `ca07d06cc32602042bf30037988070d23def299133df8eea21e70ac154fd2606`.
Archive CRC validation passed, and the bundled annotation runtime matches the
working-tree sources byte for byte.

Windows PyMOL's real Plugin Manager installer installed that ZIP into a
temporary local plugin directory outside the repository. It reported version
0.2.1, loaded 11 canonical modules and 71 canonical plus 8 compatibility
commands, created all five toolkit menus, and imported Structure Annotator.
The install harness captures dialogs and supplies a temporary GUI container;
the separate workflow check above exercises the actual PyMOL desktop window.
Evidence: `output/plugin-package-0.2.1-host.json`.

All release checks passed. Install the ZIP in Plugin Manager and restart PyMOL
after replacing an older installation so its cached Python modules are replaced.

## Limits

The warm-navigation timings measure the complete Python action, including
PyMOL command calls, but are not a general GPU frame-rate claim. Cold remote
reads still depend on the filesystem/network. Cancellation suppresses results;
it does not forcibly terminate an already-blocked filesystem read. These checks
cover Windows PyMOL 3.1.3.1; PyMOL 2.x and other Qt bindings remain separate
compatibility gates.
