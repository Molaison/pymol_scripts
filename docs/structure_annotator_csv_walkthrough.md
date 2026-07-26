# Structure Annotator CSV / Path / Cache Walkthrough

This walkthrough is intended to be followed one checkpoint at a time in the
real Windows Host PyMOL. It covers a score CSV, independent row annotations,
the default `K:` mapping, and optional WSL-only file staging. Use disposable
inputs first; do not start with the only copy of a production score table.

## 1. Runtime boundary and preflight

- PyMOL, Qt, structure loading, and all `cmd.*` calls run in Windows.
- The repository may be loaded through `\\wsl.localhost\Debian\...`, but WSL
  Python does not contain PyMOL and cannot run the annotator controller.
- The source CSV is read-only. State and annotations are separate hidden
  sidecars, and merged CSV output always uses a new path.
- WSL staging transfers structure files only. The CSV itself must be visible to
  Windows, for example on `C:`, `K:`, or a `\\wsl.localhost\...` path.

Load the plugin in a disposable PyMOL session:

```pymol
run \\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\plugins\structure_annotator.py
show_structure_annotator_panel
```

The panel must contain **Source**, **Annotate**, and **Scores** tabs. If the
panel was already open from older code, close it, rerun the entry script, and
open it again.

## 2. Prepare a minimal score CSV

Use an exact, unique ID column when possible. Duplicate structure paths are
allowed and remain independent rows; duplicate non-empty IDs are row errors.

```csv
design_id,structure_path,iptm,plddt,notes
design_a,structures/model a.pdb,0.82,91.4,first pass
design_b,structures/model a.pdb,0.76,88.2,same file but independent row
design_c,structures/model c.pdb,0.91,94.7,top candidate
design_missing,structures/not-there.pdb,0.40,61.0,expected missing row
```

Put the structures relative to the CSV as shown, or set an explicit **Relative
base**. Before testing, record hashes so source preservation can be checked:

```powershell
Get-FileHash 'C:\path with spaces\scores.csv' -Algorithm SHA256
Get-FileHash 'C:\path with spaces\structures\model a.pdb' -Algorithm SHA256
```

Expected checkpoint: there is one CSV header, four data rows, one intentionally
missing structure, and two rows referencing the same PDB.

## 3. Preview without changing the current scene

First load any ordinary object or directory dataset so there is an existing
scene that Preview could accidentally disturb. Then in **Source**:

1. Select **CSV + scores**.
2. Choose the CSV.
3. Set **Path column** to `structure_path`.
4. Set **ID column** to `design_id`.
5. Set **Display columns** to `design_id;iptm;plddt;notes`.
6. Keep encoding `utf-8-sig`, delimiter `auto`, mapping `x_k`, and WSL staging
   empty for this local test.
7. Click **Preview mapping**.

Expected checkpoint:

- four rows appear in **Scores**;
- three are `available` and one is `missing`;
- clicking a preview row shows raw/resolved path and metadata on the right;
- clicking it does not load an object or navigate the previously active source;
- no CSV state, annotation sidecar, or history update has been created;
- the mapping file is byte-for-byte unchanged from immediately before Preview.

Opening the panel itself populates the mapping-profile combo and may initialize
or repair the global mapping store. Therefore compare the store immediately
before and after **Preview**; do not use panel construction as the Preview
baseline.

The equivalent console diagnostic is:

```pymol
sa_csv_preview "C:\path with spaces\scores.csv", structure_path, design_id, "design_id;iptm;plddt;notes"
```

The returned JSON includes `status_counts`, `rule_counts`, and the first 50
rows. Preview may perform read-only file checks or WSL `stage/stat`; it never
writes mapping/state/history/session data.

The panel runs CSV parsing, path probes and WSL requests in a background
prepare task. While it is running the same button becomes **Cancel preview**;
the panel and PyMOL navigation must remain responsive. Cancel immediately
unregisters the panel job and suppresses the result. Closing the panel follows
the same path, so a late completion cannot call back into the closed widget.
An already-blocked FUSE/HTTP read may still occupy one of the two worker slots
until its timeout. The console `sa_csv_preview` command intentionally remains
synchronous for script compatibility.

## 4. Load, inspect scores, filter, and sort

Click **Load CSV**. The first valid row should load only after parsing, path
resolution, and materialization have succeeded. A failed source switch must
leave the previous active source and managed scene unchanged.

During preparation the button becomes **Cancel load**. Cancellation is allowed
only before `Committing…`; the final scene transaction and `cmd.load` stay on
the PyMOL main thread and cannot be interrupted halfway. If another dataset,
filter, or source argument changes while the worker is running, its stale
result is discarded rather than applied or rolled back over the newer scene.

In **Scores**:

- the left table shows row, status, structure, active annotation, and selected
  source score columns;
- the right table shows entry ID, raw/resolved path, provider, mapping rule,
  materialized path, cache hit, error, and all metadata;
- `iptm` and `plddt` sort numerically rather than lexicographically;
- **Filter** is a case-insensitive substring search across display name, raw and
  resolved paths, and all metadata;
- missing/invalid rows stay represented in the backing dataset but do not enter
  navigation.

Console equivalents:

```pymol
sa_load_csv "C:\path with spaces\scores.csv", structure_path, design_id, "design_id;iptm;plddt;notes"
sa_filter "top candidate", iptm, 1
sa_filter "", iptm, 1
sa_goto 1
sa_next
sa_prev
```

Expected checkpoint: sorting/filtering changes the active navigation view, the
current entry is preserved by stable ID when it remains visible, and a filter
with no navigable result reports an error without destroying the old scene.

## 5. Annotate duplicate-path rows independently

In **Annotate** set, for example:

```text
Label name: review
Values: Keep,Maybe,Reject
On step: show cartoon, {object}
Keep command: color green, {object}
Reject command: color red, {object}
```

`On step` and bucket commands are executable PyMOL configuration. Only restore
state from a trusted dataset. Both receive `{object}`, `{file}`, `{index}`,
`{count}`, `{entry_id}`, and `{row_number}`. Bucket commands additionally
receive `{label}` and `{value}`; those two placeholders are not available to
`On step`.

Annotate the two rows that point to `model a.pdb` with different values:

```pymol
sa_goto 1
sa_annotate Keep
sa_goto 2
sa_annotate Reject
sa_undo
sa_annotate Maybe
```

Expected checkpoint: the two rows show different annotations despite sharing a
PDB path; undo affects only the last `(entry_id, label)` mutation. The CSV mode
sidecar is:

```text
<csv-parent>/.pymolplugins/
  structure_annotator.<csv-id>.annotations.jsonl
```

It is append-only JSONL with fsynced events. Do not hand-edit it while PyMOL is
open. The original PDB is never used as an annotation store in CSV mode.

## 6. Export and re-import

Export to a new destination:

```pymol
sa_export_csv "C:\path with spaces\scores_reviewed.csv"
```

Expected checkpoint:

- overwriting the source CSV is refused;
- the source headers/rows/scores are preserved;
- there is exactly one `pymol_annotation_review` column;
- duplicate structure paths retain their independent values;
- `scores_reviewed.csv` can be selected and loaded as a new CSV source.

Re-run the PowerShell hashes from step 2. The source CSV and source PDB hashes,
sizes, and modification times must be unchanged.

## 7. Verify per-CSV state and global fuzzy history

Choose a non-default sort/filter and cursor, then run:

```pymol
sa_save_state
sa_state_status
sa_history_status
sa_history_search step_cmd, cartn
```

CSV state is stored beside the CSV:

```text
<csv-parent>/.pymolplugins/structure_annotator.<csv-id>.json
```

It records CSV parser columns, mapping/staging choices, filter/sort, annotation
configuration, and the stable current entry ID. Open a second CSV with different
headers/settings, then return to the first. Expected checkpoint: each CSV
restores only its own source-specific values; a newly selected CSV never inherits
the old CSV's ID/display/delimiter/base/staging settings.

Close and reopen the panel, then test a fresh PyMOL session. With **Restore last
dataset when this panel opens** enabled, the source, configuration, scores view,
and cursor restore. Disable that checkbox, save/close, and confirm a fresh open
restores configuration without auto-loading.

For tri-state console arguments, an explicit empty ID/base/stage string clears
that saved optional value. Empty `display_columns` means automatic/all score
columns; pass the JSON text `"[]"` when deliberately clearing every display
column.

## 8. Test the default `K:` SSHFS/FUSE mapping and cache

The built-in `x_k` profile maps:

```text
/xcfhome/zpzeng/mount_local/... -> K:\...
/xcfhome/zpzeng/...             -> K:\...
~/...                           -> K:\...
```

Initialize (if needed) and inspect the global mapping/cache stores:

```pymol
sa_mapping_list
sa_cache_status 1
```

These two diagnostic commands are not read-only setup probes:
`sa_mapping_list` may initialize or repair the mapping file, and
`sa_cache_status` may create the cache root/manifest. `sa_csv_preview` itself
uses the read-only mapping lookup.

Create five CSV rows that point to the same real file: the three mapped
spellings above, then two separate rows containing the same direct `K:\...`
path. Preview must resolve all five to the same physical Host path. With **Use
persistent Windows-local cache** enabled, load row 1 and navigate through each
distinct row once:

- the first mapped spelling is a cold copy;
- the other mapped spellings use the same cache key and are warm hits;
- the first direct `K:\...` row is a separate cold entry because its provider
  differs from mapped aliases, and the second duplicate direct row is its warm
  hit;
- `materialized_path` is under a Windows-local directory, normally
  `%LOCALAPPDATA%\PymolPlugins\structure-cache\v1`, not on `K:` or UNC;
- the source hash/size/mtime do not change.

Revisiting the same entry in one loaded session returns its memoized
`MaterializedStructure`; it intentionally does not query the persistent cache
again and retains that entry's original `cache_hit` value. Use duplicate rows,
reload the CSV, or start a fresh session when validating a persistent warm hit.

Other mounted drives can be declared before launching PyMOL:

```powershell
$env:PYMOLPLUGINS_REMOTE_DRIVES = 'K,L'
$env:PYMOLPLUGINS_STRUCTURE_CACHE_DIR = 'D:\PymolPluginsCache'
```

Add a custom mapping with forward slashes on the console to avoid a trailing
backslash ambiguity:

```pymol
sa_mapping_add lab_l, /remote/lab, L:/, provider=sshfs-win, priority=100
sa_mapping_remove lab_l, /remote/lab
```

Mappings are global configuration under the Windows Host user's
`~/.pymolplugins`, whereas state/annotations are dataset-local. Preview does not
create a missing mapping store or quarantine a damaged one.

## 9. Test WSL-only structure staging

Use this mode when Windows cannot efficiently/readably open the structure path.
The transfer still crosses the WSL/Host boundary once on a cold miss; subsequent
loads use the Windows-local cache.

In WSL, use a dedicated root and token:

```bash
export PYMOLPLUGINS_WORKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
scripts/start_worker.sh \
  --port 8765 \
  --stage-root structures=/home/admin/structures \
  --max-stage-mb 256 \
  --stage-timeout 60
```

Before launching Windows PyMOL, set the same values in PowerShell:

```powershell
$env:PYMOLPLUGINS_WORKER_URL = 'http://127.0.0.1:8765'
$env:PYMOLPLUGINS_WORKER_TOKEN = '<same token>'
```

Create two independently identified CSV rows containing the same
`/home/admin/structures/run_01/model.pdb`. In the Source tab set:

```text
WSL root: /home/admin/structures
alias: structures
```

Preview performs its own read-only stat, so note it separately or clear the
observation log before measuring load. With two or more unique WSL paths it
uses `stage/stat-batch`; a duplicate-only fixture has one unique path and keeps
the single `stage/stat` fast path. On a cold **Load CSV**, the controller
resolves the unique path once and reuses that fresh stat identity
for preflight, then downloads on the miss. Navigate to the second duplicate row
(or reload the CSV) to force a fresh stat and persistent-cache lookup. Excluding
the optional Preview request, the expected controller sequence is:

```text
POST /v1/stage/stat 200
POST /v1/stage/file 200
POST /v1/stage/stat 200
```

Expected checkpoint: first distinct entry is cold, the duplicate/reload is
warm, and the cache entry identity includes the complete opaque `v1-...`
metadata token. Revisiting the identical entry without reloading is session
memoization and produces no HTTP request. A wrong Bearer,
missing file, directory, symlink escape, `..` traversal, oversized file, or path
outside the configured root is rejected without replacing the active scene.
Stop the foreground worker with `Ctrl-C` after the test.

The worker has no directory listing, absolute-path request, upload, write,
delete, arbitrary Python, or shell endpoint. Root aliases are explicit startup
configuration and their server paths are not exposed by capabilities.

The reviewed real three-request E2E exercised the PyMOL-independent
staging/client/cache/materializer chain under WSL. Controller stat reuse and
session memoization are automated in fake-PyMOL tests. A second real foreground
worker run exercised one mixed-result `stage/stat-batch` followed by the legacy
single stat/file endpoints. The Windows-PyMOL-to-WSL version of this step
remains a manual Host acceptance gate.

## 10. Diagnostics and safe cleanup

```pymol
sa_state_status
sa_history_status
sa_mapping_list
sa_cache_status 1
sa_unbind_keys
```

`sa_cache_clear` removes unpinned entries. Use `sa_cache_clear 1` only after
finishing the active dataset when deliberately clearing pinned entries too.
Back up before deleting `.pymolplugins`: it contains resumable configuration and
CSV annotations, not disposable cache data.

If a load/filter/materialization fails, verify the previously active managed
objects, enabled states, source, cursor, and `keep_previous` scene first. They
should be unchanged because source switches are transactional.

## 11. Current limits to record during testing

- The Scores table is lazy (`QTableView + QAbstractTableModel`) and has a
  repeatable 50,000 x 8 Windows-Qt performance gate. CSV parsing, path
  resolution, and filesystem/network latency can still dominate initial load;
  the table result does not promise that every 50,000-file mount is fast.
- WSL paths are deduplicated and sent through `stage/stat-batch` in chunks of
  at most 256 paths and 64 KiB. An old worker is used sequentially only after
  the batch endpoint returns the exact compatibility signal `404/not_found`.
- Annotation JSONL locking is process-local; do not edit the same sidecar from
  two PyMOL processes concurrently.
- The reviewed Host build is PyMOL 3.1.3.1. PyMOL 2.x remains a separate gate.
- The persistent structure cache accelerates structure input. PLIP/Rosetta
  analysis-result caching is a separate, not-yet-implemented feature.

The reviewed commands and measured real-environment results are recorded in
[windows_host_smoke_2026-07-25.md](validation/windows_host_smoke_2026-07-25.md).
