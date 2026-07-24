# PyMOL Plugin Manager entries

These files only bootstrap the repository root and delegate to canonical code
under `pymolplugins/host`, `pymolplugins/annotation`, and
`pymolplugins/styles`. They remain at stable paths for Windows Plugin Manager.

`structure_annotator.py` persists each loaded dataset under
`<dataset>/.pymolplugins/structure_annotator.json` and keeps de-duplicated
cross-dataset input history under
`~/.pymolplugins/structure_annotator_history.json`. Opening the panel restores
the last finalized dataset unless its **Restore last dataset** checkbox was
disabled. Directory, pattern, label, values, On-step, and bucket-command fields
use that global history for automatic fuzzy completion. Because restored
On-step/bucket templates are executable PyMOL commands, use annotator state
only from trusted datasets.
