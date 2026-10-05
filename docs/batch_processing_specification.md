# Batch structure processing - 0.6.0

Window hosting in 0.7.0 is shared with Structure Annotator: embedded in PyMOL
by default, with a top-right Window mode menu and remembered floating geometry.
Changing mode preserves preparation, preview and running work. See
[workspace window modes](workspace_window_modes.md).

Status: implemented and release-validated on Windows PyMOL 3.1.3.1 / PyQt5,
2026-09-16. User request: all against one reference, or one A/B pair per ID
with additional operations between A and B.
Existing 0.5.1 work and unrelated dirty changes remain preserved.

## Contract

- The user confirmed folder/CSV structure files as the input scope. Support
  file patterns and explicitly chosen CSV ID/A/B columns. Do not require all
  structures to be loaded into PyMOL first.
- Reference mode resolves an A set and one exact B reference. Exclude the
  reference itself from the A set, and reject an empty resulting plan.
- ID-pair mode supports literal `{id}` templates, wildcard patterns (one `*`
  captures the ID; an explicit `{id}` permits other wildcards), and filename
  regexes with `(?P<id>...)` or exactly one capture group. Regex mode takes two
  directories and two separate expressions, matched against complete filenames.
  Pair equal IDs, use natural ID order for files, preserve CSV row order, and
  allow an explicit ID subset in the requested order.
  Missing partners and duplicate IDs are errors, not skipped or position-paired.
- A per-pair recipe contains ordered PyMOL command lines. `{A}`, `{B}`, `{id}`,
  `{index}`, `{A_source}` and `{B_source}` identify the current pair; file mode
  also exposes its output path. Alignment is an editable initial recipe, not
  the only supported operation. Validate all templates before any mutation.
- Preview lists exact pairs and rendered steps without loading structures,
  changing the scene, or writing files. Editing inputs invalidates the preview.
- Execute sequentially on PyMOL's main thread, reusing the checked parser
  dispatch already used by `pp_foreach`. GUI execution yields between steps;
  Stop takes effect at a step boundary, not in the middle of a PyMOL call.
- Failures report pair ID, step, command and traceback. Stop on error is the
  default; continuing with the next pair is explicit. Completed arbitrary
  commands are not claimed to be transactional or generically undoable.
- Load only owned temporary objects and automatically save both processed A and
  B structures by default, as explicitly requested, retaining their file format.
  Never overwrite source structures or existing automatic output files. Release
  only owned temporaries. Every pair gets unique temporary object names; a fixed
  reference source stays loaded once and is copied into a fresh B for each pair.
- Provide a discoverable Batch processing dialog and `pp_batch` command,
  preview/run/stop controls, per-pair progress and JSON result export. Reuse
  existing target resolution, command parsing and Windows path helpers rather
  than adding a navigation cursor, transform journal or cache implementation.

## Acceptance

1. Pure planning tests: fixed reference, self-exclusion, natural ID order,
   subsets, missing/duplicate IDs, template/glob/regex captures and path handling.
2. Runner tests: exact pair/step order, preflight with zero mutation, stop versus
   continue, cancellation, source/output isolation and owned-only cleanup.
3. Real Windows PyMOL: visible dialog preview/run for all matching modes; actual
   alignment plus a second A/B operation; parser failure reports; file outputs
   and unchanged input bytes; compact geometry and screenshot review.
4. Existing regression plus new tests, relevant annotation smoke,
   final ZIP source-byte/CRC/version audit and real Plugin Manager installation.

Implementation evidence belongs in `docs/validation/batch_processing_2026-09-16.md`;
logs, JSON and screenshots use the existing `output/` directory. Windows tests
run serially with the bundled PyMOL Python from `/mnt/c`, isolated temporary
fixtures, and blocking terminal supervision. PyMOL 2.x and other Qt bindings
remain separate compatibility gates.

## Investigation

`pp_foreach` resolves one target at a time and has checked same-line parser
completion, but no pair identity or per-pair multi-step recipe. The annotator's
batch annotation writes labels to selected rows; it is not a structure-processing
loop. The read-only scout returned upstream HTTP 502 without editing files;
the main thread owns the integration decisions.

## Usage

Open **Plugin > PymolPlugins: Batch Processing**, run `pp_batch`, or choose
**Data > Batch processing...** inside Structure Annotator. The annotation entry
copies the active source and path-mapping settings without moving its cursor.

### All against one reference

Select **All against reference**. A is a directory or file glob; B is one
reference structure. With **CSV table**, choose the A-path column and optional
ID column. CSV paths resolve relative to the CSV directory unless a base folder
or mapping profile is chosen. Only local/mounted paths are loaded; this tool
does not silently submit remote staging jobs. The reference itself is excluded,
including paths which refer to the same physical file.

```pymol
pp_batch "D:/structures", "D:/reference.pdb", output="D:/processed", preview=1
```

### Pair by ID

Select **A / B by ID** and choose **Filename matching**:

| Matching | A | B | Extracted ID |
| --- | --- | --- | --- |
| ID template | `D:/A/A_{id}.pdb` | `D:/B/B_{id}.pdb` | Literal placeholder |
| Wildcard | `D:/A/A_*.pdb` | `D:/B/B_*.pdb` | The single `*` |
| Regular expression | Directory `D:/A`; expression `A_(?P<id>[0-9]+)[.]pdb` | Directory `D:/B`; expression `B_(?P<id>[0-9]+)[.]pdb` | Named `id` group |

Wildcard matching supports `?` and character classes. Multiple `*` require an
explicit `{id}`, for example `run_*/A_{id}.pdb`, so ID extraction is not guessed.
Regex mode scans files immediately inside each chosen directory, not all nested
directories. Expressions match the entire filename. With **CSV table**, choose
the ID, A-path and B-path columns instead of inferring IDs from filenames.

```pymol
pp_batch "D:/A/A_*.pdb", "D:/B/B_*.pdb", mode=paired, match=glob, output="D:/paired", preview=1
pp_batch "D:/A", "D:/B", mode=paired, match=regex, a_pattern="A_(?P<id>[0-9]+)[.]pdb", b_pattern="B_(?P<id>[0-9]+)[.]pdb", output="D:/paired", preview=1
pp_batch "D:/pairs.csv", B_path, source=csv, mode=paired, a_column=A_path, id_column=ID, output="D:/paired", preview=1
```

IDs are exact strings. `1-20,25` selects inclusive unpadded numeric IDs; use
explicit zero-padded IDs when the source contains them. Missing requested IDs,
missing partners, empty IDs and duplicate IDs are errors. Unmatched regex files
are outside the selected input set, not failed pairs.

### Commands and outputs

The **Commands per pair** tab accepts ordered PyMOL command lines. For example:

```pymol
align {A}, {B}, cycles=0
translate [0, 2, 0], {B}
```

`{A}` and `{B}` are the loaded objects, not file paths. Other fields are `{id}`,
`{index}`, `{A_source}`, `{B_source}`, `{output_A}`, `{output_B}`, `{output_dir}`
and `{output}` (alias of `{output_A}`). Quote path fields in commands. Blank
lines and lines beginning with `#` are ignored. `{output_B}` is invalid if the
user explicitly disables B output. All templates are checked before processing.

**Preview pairs** is read-only. The table shows exact source pairs; step and path
tooltips include all rendered commands and planned outputs. Editing any input
requires a new preview. **Run batch** executes sequentially on the PyMOL thread.
**Stop** takes effect between steps, not inside a running alignment/load/save.
Console execution is synchronous; repeat a preview command with `preview=0`
to run it. `encoding` and `delimiter` are also available to the console API.

Both processed structures save by default to a separate output directory:
`000001_A_<original filename>` and `000001_B_<original filename>`, retaining
extensions. Every batch also writes `batch-<run token>.jsonl` events and a final
`.json` report. **Export results** writes an additional report to a new path.
Existing automatic outputs and reports are never overwritten.

Failure stops the batch by default. **Continue with next pair** is an explicit
alternative. Results retain completed steps, output paths, the failed command,
traceback and any temporary objects that could not be removed. Cancellation or
failure can leave a partially processed pair with one already-saved output;
completed outputs are retained. The report distinguishes these states. Recipe
commands are trusted executable configuration: arbitrary user commands can have
side effects or write other files, and are not claimed to support rollback.

## Shared UI - 0.6.1

Batch Processing applies the same local theme as Structure Annotator from both
entry paths. Normal controls share its font, colors, spacing, table treatment,
primary action, error feedback and progress line; commands remain monospace.
File locations uses the same collapsible control. Icon tools have explicit
34 x 34 bounds, and compact input forms remain scrollable without clipping
their controls. Application-wide PyMOL styling and processing behavior are
unchanged. Real Qt comparisons and screenshots are recorded in
`docs/validation/batch_ui_consistency_2026-09-16.md`.
