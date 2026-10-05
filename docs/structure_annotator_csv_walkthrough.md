# Structure Annotator CSV / Path / Cache Walkthrough

## 连续审阅

已有任务可用 **Data > Continue last review** 恢复最近的数据源、筛选和候选位置。它是显式操作，不会在启动时自动连接远端。

暂时不能判断时，在 **More > Mark current for later** 标记当前候选；它不会写入 Good/Bad 等质量字段。**Next unreviewed** 会继续未审阅路线，**Next marked for later** 用于集中回看。交接给同事时使用 **Data > Export review handoff…**；JSON只包含来源、筛选、进度和标签摘要，不包含结构文件。

This walkthrough is intended to be followed one checkpoint at a time in the
real Windows Host PyMOL. It covers a score CSV, independent row annotations,
the default `K:` mapping, and optional WSL-only file staging. Use disposable
inputs first; do not start with the only copy of a production score table.

## 新用户快速开始

**0.12.4** 打开页只保留必要控件。右上角 **?** 可查看帮助；没有服务器可选时，点 **Set up SSH… > First SSH connection…**。SSH 登录程序在 **Advanced** 中按需展开。

1. 在 PyMOL 的 **Plugin Manager** 安装原始 ZIP，不要解压；安装或升级后重启 PyMOL。
2. 打开 **Plugin > PymolPlugins: Structure Annotator**。
3. 在 **Open from** 选择 **This computer**，或选择已经存在的 SSH **Host** 别名。
4. 在 **Path** 粘贴真实的结构文件夹、CSV 或 TSV 路径；需要时使用 **Browse…**。
5. 点 **Open**，通常不用改 **Import options**。如需 **Structure column**，选包含 PDB/CIF 文件路径的列，不是分数或名称列。
6. 在 **Review** 查看结构和分数，选择标签，再点 **Save and next**。普通点选只是草稿；快捷键、Alt+数字和 Ctrl+click 都是可选操作。

SSH profile 是服务器的保存名称，不是密码。Windows PyMOL 默认读取 `%USERPROFILE%/.ssh/config`，不是 WSL 的 `~/.ssh/config`。**Connections…** 只在需要时选择已有 config 或登录 program；默认配置可直接使用。

开始远端审阅前，先在运行 PyMOL 的 Windows 电脑上确认 `ssh my-lab` 能连接，并准备好主机信任、密钥或 OTP。插件不创建服务器账号、不管理密码，也不自动修改全局 SSH 配置。远端需要 `python3`，但不需要 SSHFS 或 WSL worker。标注和用户设置保存在本地，远端原文件不变。

```sshconfig
Host my-lab
  HostName server.example.org
  User your-username
```

| 错误 | 下一步 |
| --- | --- |
| 找不到 SSH profile | 检查 Windows SSH config 的 `Host` 拼写；若使用其他配置文件，在 **Connections…** 选择它。 |
| `Permission denied`、密钥或 OTP 失败 | 先让 Windows 终端中的 `ssh my-lab` 成功；联系管理员处理账号和服务器权限。 |
| 远端没有 Python 3 | 确认 `ssh my-lab python3 --version` 可用，必要时请管理员安装。 |
| CSV/TSV 没有加载结构 | 选择包含 `.pdb`、`.cif` 或其 `.gz` 路径的 **Structure column**。 |
| 等待时间较长 | 查看连接/认证、队列、读取或下载阶段；可取消后修复连接、认证或路径再重试。 |

下面保留完整的路径、缓存、映射、状态和验收说明；第一次使用不需要先配置这些高级选项。

Version 0.9.1 fixes default-config SSH URLs and reuses the configured SSH login
program, including the installed OTP helper. Open data has one connection,
path and Open action. It also supports direct SSH profiles and remote paths, fuzzy search,
and default Ctrl+click save-and-next for multiple-choice fields. It supports CSV/TSV tables, adjacent review configuration and
cancellable background folder imports. It uses one review workspace with separate import and settings pages,
optional click-saving, and configurable source-row/file-status columns.
Install the ZIP built
by `just package` through Plugin Manager and restart PyMOL after upgrading.
The current GUI/performance evidence is in
[the SSH and interaction record](validation/structure_annotator_ssh_fuzzy_2026-09-17.md).

## 1. Runtime boundary and preflight

### AI命令：先生成草稿，再确认

入口：**Settings → Automation → AI command drafts…**。先加载真实candidate，勾选“打开结构”或指定“保存标签后”触发位置，输入需求后生成。每个触发位置有自己的可编辑多行预览。确认插入后仍需原来的 **Apply commands**；生成不会自动执行。保存标签后的框也支持多行，仍按顺序逐行执行。

在 **AI settings…** 选择连接：默认 **Codex (Debian WSL)** 使用现有provider/model和Responses配置；**Custom API** 可编辑URL、API Key和模型，明确选择Responses或Chat Completions，由PyMOL同安装Python直接请求，不需要WSL。**Refresh**读取模型列表，也可手动输入ID。Key默认仅保留到PyMOL退出；可勾选Remember key使用Windows当前用户加密保存，不进入数据集配置、prompt或日志。生成中可取消，失败保留编辑框和已有预览。

唯一输出契约如下；示例中的`Good`必须是当前界面的真实标签值。`on_open`不是源表的step列；`after_label/Good`准确对应Good命令框。`commands`数组每项一行，填框时按数组顺序以换行连接，不从自由文本截取JSON。

```json
{"schema":"pymolplugins.ai-commands/1","steps":[{"step_id":"on_open","commands":["show cartoon, {object}","zoom {selection}"],"note":""},{"step_id":"after_label/Good","commands":["color cyan, {object}","show sticks, {selection}"],"note":""}]}
```

`{selection}`等于`{object}`，随导航指向当前candidate；还支持现有`{file}`、`{index}`（从0开始）、`{count}`、`{entry_id}`、`{row_number}`。标签触发额外有`{label}`和`{value}`。没有`{reference}`、`{A}`、`{B}`；比较对象必须使用当前真实对象名。AI prompt包含准确契约、当前对象、所选step、合法示例和禁止Markdown/额外文字的要求；结构化API参数只是附加约束。

AI草稿支持show/hide/color、util.cbc/cnc/cbag/cbac、zoom/orient/center/align/super，以及对象范围内的set和spectrum明确子集，不支持任意Python、文件/系统操作或分号串联。原生解析和当前选择检查不等于保证今后每个结构都适用；例如某条链在后续结构缺失，仍由现有执行错误反馈报告。所有手写命令保持原能力。

**Viewing presets…** 提供cartoon、PPI界面（链间5 Å邻域）、PLI口袋（配体5 Å邻域）、oligomer亚基、membrane蛋白/脂质、DNA/RNA复合物和surface。无需API，也走预览、编辑、确认插入、Apply流程。PPI需要至少两条蛋白链；PLI需要有机配体。膜预设显示现有脂质并突出疏水残基，不推断膜位置，也不改变原朝向。预设按当前结构生成；后续不同链组成的候选仍需检查选择表达式。

应用后的多行模板保存在现有状态文件；CSV重开也会从`review.override.json`恢复`step_cmd`和`bucket_cmds`（标签值→多行字符串）。生成的`review.json`及结构文件不改写。

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

The panel opens **Open data** initially and shows the review workspace after
import. **Data** and **Settings** open the other pages; **Back** returns to review.
Restart PyMOL after replacing an installed plugin.

Choose **This computer** or a server in **Open from**, paste a folder or table
path, and click **Open**. Table extensions are recognized automatically;
**Browse…** offers **Table file…** and **Folder…**. Mapped-drive and UNC paths are accepted, including structure paths
longer than Windows' traditional 260-character limit.

### Direct SSH sources

Select a Host alias directly in **Open from**. **Connections…** holds optional
**SSH config** and **SSH program** paths. The default config is on the PyMOL
computer. The installed `.ssh/ssh_otp/ssh_totp.py` login program is reused when
present, including its OTP handling and connection settings. A program can also
be selected explicitly or through `PYMOLPLUGINS_SSH_PROGRAM`; executables and
Python login scripts accept normal SSH arguments. A failed program is reported
without retrying through another client. The remote host must provide Python 3. No worker
service or SSHFS mount is required.

The plugin reuses one authenticated SSH connection across header, configuration,
table and structure reads. Existing OTP code-allocation rules remain in effect;
only the initial login needs authentication. Cancelling a transfer closes that
owned connection, and a later explicit operation can connect again.

Enter the actual remote folder/table path, for example `/data/review/scores.tsv`
or `~/review/structures`, or use **Browse…**. Absolute and home-relative paths
refer to the remote host. Relative CSV structure paths use the remote CSV
folder, with an optional **File locations > Base folder** override. Path-mapping
controls are hidden for SSH sources. Raw CSV cells are never changed. **Import
options** contains input type, file filters, path mappings and parser settings.

Column discovery reads a bounded sample and the complete first CSV record on
the server. The complete table is transferred once for parsing; structures are
downloaded on demand into the existing local cache. Cache identities include
the SSH source and remote file version. Background operations support Cancel;
connection/read failures preserve the active dataset and scene. Individual
downloads are limited to 512 MiB and directory browsing to 100,000 entries.

Configuration, local review overrides and annotations are saved under the
PyMOL user's `~/.pymolplugins/ssh_sources/<source identity>/`, with separate
stores for different profiles, config files and source paths. Remote
`review.json` and `review.override.json` are read first; local overrides take
precedence. Nothing is written back over SSH. Existing mapped-drive sidecars
and embedded structure annotations are not migrated into this separate SSH
workspace automatically. `PYMOLPLUGINS_SSH_STATE_ROOT` selects an isolated
metadata root for tests or deployment. Recent configurations retain the SSH
profile and path, and path history suggestions are scoped to that profile.

The reproducible Windows/OpenSSH check is `just test-annotator-ssh`. It creates
an ephemeral local SSH server/profile/key and verifies source hashes after the
GUI workflow; it does not use the user's private keys or change their config.

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

Put the structures relative to the CSV as shown, or set **File locations > Base
folder**. Before testing, record hashes so source preservation can be checked:

```powershell
Get-FileHash 'C:\path with spaces\scores.csv' -Algorithm SHA256
Get-FileHash 'C:\path with spaces\structures\model a.pdb' -Algorithm SHA256
```

Expected checkpoint: there is one CSV header, four data rows, one intentionally
missing structure, and two rows referencing the same PDB.

## 3. Preview without changing the current scene

For a prepared results bundle, put `review.json` beside `sum_scores.csv` (or a
TSV table). Choose the table and click **Open table**; Structure Annotator reads
the configuration automatically:

```json
{
  "path_column": "structure_path",
  "display_columns": ["iptm", "plddt"],
  "label_column": "label",
  "step_cmd": "hide everything, {object}\nshow cartoon, {object}\norient {object}\nzoom {object}"
}
```

Choose real column names from that table, normally the 4–6 metrics most useful
for manual inspection, in viewing order. `label_column` is optional; omit it or
use `""` if the table has no descriptive labels. A nonempty label becomes the
candidate title; an empty cell retains its row ID or filename. Source labels do
not count as manual review annotations and need not be unique.

Loading combines `review.json` and then `review.override.json`, field by field.
Explicit import selections take precedence. Applied changes to the structure
column, display columns, label column or shared commands save as
`review.override.json`. Merely opening or previewing an unchanged bundle does
not create an override. The generated `review.json`, table and structures stay
unchanged; manual annotations continue to use the existing JSONL sidecar.
These two review files apply to tables in the same folder. Use a separate
folder for each independent review bundle.

Invalid JSON, unknown columns or invalid command placeholders report an import
error and keep the current dataset. The command template runs for each loaded
structure with its actual `{object}`. CSV, TSV, tab, semicolon and pipe separated
tables use the same importer; the default separator is detected from the table.

The remaining checkpoints also work without a review file.

First load any ordinary object or directory dataset so there is an existing
scene that Preview could accidentally disturb. Then open **Data**:

1. Select **CSV / TSV table**.
2. Choose the CSV or TSV.
3. Wait for header discovery, then select **Structure column** `structure_path`.
4. Expand **Table options** and set **Row ID column** to `design_id`.
5. In **Table options**, open **Columns**, search and check `design_id`, `iptm`, `plddt`, and `notes`,
   then choose **Apply**. **Select all** and **Clear all** apply to the full list.
6. Keep encoding `utf-8-sig` and separator `auto`. Under **File locations**, keep
   server profile `custom`, **This computer** set to **Custom**, and file access
   **Open mapped / local files**.
7. Click **Check file paths**.

Expected checkpoint:

- four rows appear in a separate **Path mapping preview** window;
- three are `available` and one is `missing`;
- raw/resolved paths, row status and errors appear in the preview table;
- selecting a preview row does not load an object, replace the active review
  table or change candidate selection;
- no CSV state, annotation sidecar, or history update has been created;
- the mapping file is byte-for-byte unchanged from immediately before Preview.

Opening the panel and testing a profile sample use read-only mapping lookup.
Profiles are written when **Save profile** is chosen. The existing console
`sa_mapping_list` command retains its store-initialization behavior.

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

Click **Open table**. The first valid row should load only after parsing, path
mapping, and preparation of that structure have succeeded. GUI loading checks
other structure paths as they are needed; unvisited paths can remain `unchecked`.
**Check file paths** is the explicit full-path diagnostic, while console
`sa_load_csv` retains its complete synchronous validation. A failed source switch must
leave the previous active source and managed scene unchanged.

During preparation the button becomes **Cancel load**. Cancellation is allowed
only before `Committing…`; the final scene transaction and `cmd.load` stay on
the PyMOL main thread and cannot be interrupted halfway. If another dataset,
filter, or source argument changes while the worker is running, its stale
result is discarded rather than applied or rolled back over the newer scene.

In the review workspace:

- the left table shows structure, active annotation, and selected
  source score columns;
- **More actions > List columns** optionally shows **Source row** and
  **File status** without reloading or reordering candidates; these preferences
  persist in dataset state and configuration history;
- **More actions > Structure details…** opens the resizable table of entry ID, raw/resolved path, provider, mapping rule,
  materialized path, cache hit, error, selected metadata, and all saved
  annotations, including fields no longer in the current form;
- `iptm` and `plddt` sort numerically rather than lexicographically;
- **Filter > Text search** is a case-insensitive substring search across display name, raw and
  resolved paths, and all metadata;
- **Column conditions** accepts expressions such as `iptm >= 0.8 and plddt > 90`,
  `contains(notes, "candidate")`, and `iptm is not None`. Column names complete
  while typing. Backticks quote names with spaces or punctuation.
- When column suggestions are open, **Enter**, keypad **Enter** and **Tab**
  insert the highlighted column and keep the dialog open. With suggestions
  closed, **Enter** applies the filter. **Apply filter** always submits explicitly.
- Filters and sorting may use columns hidden from the table. Empty results are
  displayed explicitly and navigation is disabled; invalid expressions retain
  the old view and show an error.
- Row-validation errors remain in the backing dataset. An unreadable structure
  is reported when prepared, without replacing the current scene.

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

In **Settings > Annotations**, configure fields; optional commands are under
**Automation**. Return with **Back** to annotate while viewing scores. Mouse
choices remain drafts by default until **Save and next** (or **Save** when
advance is off). To restore one-click annotation, enable **Save on label click**
and **Go to the next structure after saving** in **Settings > Annotations** or
**More actions**. The two settings are independent: disable advancement to
save a click while staying on the current candidate.

Click-saving applies to single-choice buttons and dropdowns for one candidate.
Required fields must be complete; otherwise the choice remains a draft.
Shift+click, ordinary multiple-choice clicks, and batch selection do not auto-save.
For one candidate, **Ctrl+click** a choice saves and advances by default,
independently of the ordinary click-save/advance settings. In a multiple-choice
popup, plain clicks toggle choices; Ctrl+click a choice or **Apply** saves all
staged choices. Incomplete required fields and failed saves keep the draft and
cursor. Shift remains a staging gesture and batches still require explicit Save.
Alt+digit saves a single-choice label once; for multiple-choice labels it only
toggles the draft value. A failed write preserves the draft and current
candidate. **More actions > Undo last save** restores the last complete save.
For example:

```text
Label name: review
Values: Keep,Maybe,Reject
On step: show cartoon, {object}
Keep command: color green, {object}
Reject command: color red, {object}
```

`On step` and bucket commands are executable PyMOL configuration. Only restore
state from a trusted dataset. Click **Apply commands** after editing; unfinished
text is a draft and is not executed by Next, Previous or annotation actions.
Invalid placeholders leave the previously applied configuration intact. A
parser failure displays its line and command and restores the previous cursor,
object visibility and camera. Arbitrary custom commands can still have their
own side effects; those are not generic undo operations.

Both receive `{object}`, `{selection}`, `{file}`, `{index}`,
`{count}`, `{entry_id}`, and `{row_number}`. Bucket commands additionally
receive `{label}` and `{value}`; those two placeholders are not available to
`On step`. `{selection}` is an alias for the explicit current object name.

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
headers/settings, then return to the first. Expected checkpoint: a newly chosen
CSV reads its headers without applying saved parser/ID/display/base/staging
settings. The settings visible in the GUI determine the import.

Close and reopen the panel, then test a fresh PyMOL session. A fresh panel must
show the import page without applying historical settings or starting a load.
Reopening the panel in a session with loaded data preserves that live session.
Open **Data > Recent configurations…**, search a saved path or command, and select a
configuration. **Restore configuration** changes workflow settings and pending
import inputs without loading or replacing the active source. **Restore and
load** uses exactly the chosen configuration, including its selected scores
and filter, even when a newer sidecar configuration exists.

For tri-state console arguments, an explicit empty ID/base/stage string clears
that saved optional value. Empty `display_columns` means automatic/all score
columns; pass the JSON text `"[]"` when deliberately clearing every display
column.

## 8. Configure server profiles and test the `K:` mapping

The GUI starts with **custom** and **Custom** format. Under **File locations**, use **Edit profiles…** to
create a named server profile, edit one or duplicate it for another mount.
Select the server's path format and this computer's default format. The main
import page can override the computer format for one dataset without modifying
the saved profile. Import and configuration history retain that override.

Rules run in this order:

1. Text rewrites run top to bottom: remove a literal prefix, add a prefix or
   replace one regex match. Reorder them with **Move up/down**. Regex replacement
   supports `\1` and `\g<name>` capture groups.
2. Root mappings match complete directory boundaries. The longest root wins,
   followed by priority; equal-precedence alternatives remain explicit.
   Both roots can use Windows/UNC or POSIX spelling.
3. The chosen computer format normalizes the result. WSL converts mounted
   drives to/from `/mnt/<drive>/`; WSL Linux roots viewed from Windows require
   an explicit distribution. Ordinary Linux paths never guess a Windows drive.

For example, remove `artifact:`, then replace regex `^job-(\d+):` with
`/remote/run\1/`, then map `/remote/run7` to `D:/models`. The sample
`artifact:job-7:model.pdb` resolves to `D:\models\model.pdb`. **Test path** shows
each end of that transformation without reading the filesystem or saving.
**Save profile** persists it; **Cancel** discards the edits. Mapping schema 1.1
reads existing schema 1.0 profiles. Unsupported future schemas remain untouched.

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
`/home/admin/structures/run_01/model.pdb`. In **File locations > File access**,
choose **Download through configured worker** and set:

```text
Server root: /home/admin/structures
Worker root alias: structures
```

Preview performs its own read-only stat, so note it separately or clear the
observation log before measuring load. With two or more unique WSL paths it
uses `stage/stat-batch`; a duplicate-only fixture has one unique path and keeps
the single `stage/stat` fast path. On a cold **Open table**, the controller
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


## 筛选分组（0.14.0）

1. 点 **Filter** 旁箭头直接切组；**All candidates** 回到全集。
2. 点 **Filter**，在 **Group** 选择组，编辑条件和排序。**Save…** 使用同名更新，换名另存；**Delete** 删除组。
3. 修改写入当前数据源的 `review.override.json`；远端源仍写本地覆盖，不回写服务器。重新打开同一源会恢复修改。

`filter_presets` 是可选数组；元素为 `name`、`expression`，以及可选的 `sort_column`（默认空）、`sort_descending`（默认false）。名称非空且唯一；表达式和排序列必须属于源表。用户覆盖数组整体替代生成数组；空数组代表移除全部预设，不与生成列表逐项合并。

分组可重叠，不是质量标签，也不会移动/复制结构或修改CSV。源表的 `label` 仍表示代表性模式；人工Good/Bad保持独立。分组沿用当前筛选/抽样导航，抽样状态仍明确显示；删除组不删除数据。旧review.json没有该字段时行为不变。新的可选字段需要0.14.0或更新插件。

### 产物整理prompt（含筛选分组）

以下可直接交给整理产物的agent；示例字段必须替换为实际字段，不是固定评分阈值。

````text
请整理本次运行产物，目标是便于在 PyMOL Structure Annotator 中按组审阅：

1. 将结构文件 hardlink 到指定文件夹，不修改结构内容。跨文件系统无法 hardlink 时报告阻塞，不静默改成复制。避免同名文件覆盖。

2. 只从已有 raw 打分结果汇总 sum_scores.csv，不重新计算任何结构评分。每个结构一行，包含 structure_path（相对该CSV的路径）、absolute_path、label，以及结合此前对话确定的重要评分指标。评分列按查看重要性排列；保留缺失为空值，不用0代替。检查结构与评分行的对应关系，不遗漏或重复结构。

3. label 稀疏标记典型模式、异常或有解释价值的代表性结构，覆盖多种模式，不只选择最好/最差。label不是人工质量标签，也不必给每行赋值。

4. 同级生成 review.json，保留原有字段，并增加 filter_presets，供插件一键切换分组。格式示例：
{
  "path_column": "structure_path",
  "display_columns": ["score1", "score2", "score3", "score4"],
  "label_column": "label",
  "step_cmd": "hide everything, {object}\nshow cartoon, {object}\norient {object}\nzoom {object}",
  "filter_presets": [
    {"name": "代表结构", "expression": "label is not None"},
    {"name": "关键评分缺失", "expression": "score1 is None or score2 is None"}
  ]
}

display_columns 必须从实际CSV字段中选最值得人工查看的4–6项。step_cmd 是整批共用的命令模板；保留字面量 {object}，不要写死结构名，也不要逐结构生成命令。

filter_presets 请根据已有指标、已知判据和实际分布设计通常3–6个有意义的组，不照抄上面两个示例：覆盖优先审阅、指标间不一致、异常/缺失、代表性模式等实际存在的情况。名称短且唯一。每组需给出具体可执行的 expression；可指定 sort_column 和 sort_descending，让最值得看的条目靠前。

表达式只能使用CSV已有字段、数值/字符串/布尔常量、比较符、and/or/not、括号、contains(列, "文字")、is None / is not None。带空格或特殊字符的列名用反引号。不要使用Python代码、分位数函数、in、算术表达式或引用其他分组名；需要组合组时展开条件。

阈值优先采用此前约定或有依据的判据；若使用现有分数分布的分位数，先算成具体数值写入表达式，并说明只是探索性分层，不是已验证的通过标准。没有依据时不要发明“合格/不合格”阈值。分组默认允许重叠；若需要互斥，显式写出排除条件。

5. 用实际CSV逐项检查：display_columns和筛选/排序列存在；每个表达式可执行；统计每组命中数、重叠数及未覆盖数，检查空组是否有意保留。分组只改变查看范围，不修改原始分数、不自动写入人工标签，也不拆分/复制分组结构。

6. 附简洁 README.md：只保留数据来源、指标方向、各组条件/阈值依据和数量、必要异常、打开方式。说明在插件Filter页面可编辑/新增/删除分组。不要创建或覆盖已有 review.override.json，它属于人的修改；如它影响新的生成分组，明确提示，不擅自删除。
````
