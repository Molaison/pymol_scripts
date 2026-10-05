# PymolPlugins

## 第一次使用 Structure Annotator

当前版本 **0.14.0**。先完成下面这条路径；不用输入 PyMOL 命令，也不用先读开发文档。

连续审阅时可以点 **Data > Continue last review** 恢复最近的数据源、筛选范围和候选位置；对暂时无法判断的候选点 **More > Mark current for later**，之后用 **Next unreviewed** 继续。分数卡片会明确区分“当前审阅集中的相对排名”和已配置的绝对通过规则。

1. 在 PyMOL 打开 **Plugin > Plugin Manager > Install New Plugin**，选择下载的原始 ZIP；不要解压。安装完成后重启 PyMOL。
2. 打开 **Plugin > PymolPlugins: Structure Annotator**。右上角 **?** 随时可查看内置说明，不会在启动时强制弹出。
3. 在 **Open from** 选择 **This computer**，或选择已经配置好的 SSH **Host** 别名。
4. 在 **Path** 粘贴真实的结构文件夹、CSV 或 TSV 路径；也可以使用 **Browse…**。
5. 点 **Open**，一般不用改 **Import options**。如果要求选择 **Structure column**，选包含 `.pdb`、`.cif`、`.pdb.gz` 或 `.cif.gz` 文件路径的列，不是分数或名称列。
6. 在 **Review** 查看结构和表格信息，选择标签，然后点 **Save and next**。普通点选只修改草稿，不会立即保存；快捷键和 Ctrl+click 是可选的加速方式。

### 第一次使用 SSH

没有服务器可选时，点 **Set up SSH… > First SSH connection…** 查看准备步骤。远端 **Path** 用 `/data/review/scores.tsv` 或 `~/review/structures`，不是 Windows 盘符。

SSH profile 是保存的服务器名字，也就是 `~/.ssh/config` 中的 `Host` 别名；它不是密码。PyMOL 在 Windows 运行时，默认读取运行 PyMOL 的电脑上的 `%USERPROFILE%/.ssh/config`，不会默认读取 WSL 的 SSH 配置。

**Connections…** 可以选择已有的 SSH config 或登录 program；一般用户使用系统默认配置时无需修改。开始前，应先在同一台 Windows 电脑上确认 `ssh my-lab` 能连接，并准备好主机信任、密钥或 OTP。插件不负责创建服务器账号、管理密码，也不会自动修改全局 SSH 配置。

远端需要 `python3`。不需要 SSHFS，也不需要 WSL worker。远端表格和结构只读；标注、状态和用户覆盖保存在本地，原 CSV、TSV、PDB 和 CIF 文件不变。

无真实凭据的示例：

```sshconfig
Host my-lab
  HostName server.example.org
  User your-username
```

| 看到的错误 | 下一步 |
| --- | --- |
| SSH profile 找不到 | 检查 Windows SSH config 中是否有完全相同的 `Host`；自定义配置可在 **Connections…** 选择。 |
| `Permission denied`、密钥失败或 OTP 未就绪 | 先在 Windows 终端让 `ssh my-lab` 成功，再回到 PyMOL；账号、密钥和 OTP 由现有 SSH 环境或管理员提供。 |
| 远端提示缺少 Python 3 | 请管理员安装 `python3`，或先确认 `ssh my-lab python3 --version` 可用。 |
| 表格打开但没有可审阅结构 | 选择实际包含 PDB/CIF 路径的 **Structure column**，并检查相对路径是否相对于表格目录。 |
| 长时间停在连接、认证、队列或下载阶段 | 查看界面显示的阶段和等待秒数；可以取消，修复连接、认证或路径后再重试。 |

完整的 CSV、路径映射、缓存和 SSH 说明见 [Structure Annotator walkthrough](docs/structure_annotator_csv_walkthrough.md)。

## 开发与架构（可选）

PymolPlugins is a structure-inspection toolkit for PyMOL 2.x.  Its canonical
commands use the `pp_` prefix and are designed to compose safely in an existing
session: navigation owns only its managed objects, transforms have explicit
preview/commit/undo boundaries, styles can be reverted, and interaction output
is tracked by an exact manifest.

The project has two runtime surfaces:

- **Windows Host PyMOL** owns the GUI, objects, selections, Qt, CGO rendering,
  and all `cmd.*` mutations.
- **WSL2** is the development/test surface and can run an optional HTTP worker
  for PLIP or a separately licensed PyRosetta installation.

Windows PyMOL never executes a WSL Python path.  Remote calculations send PDB
text in JSON; they do not send Host/WSL file paths, shell commands, or arbitrary
RosettaScripts XML.

## Status

### 0.14.0：可编辑的筛选分组

`review.json` 可选 `filter_presets`：每组使用已有评分列定义条件和排序。**Filter** 旁下拉箭头一键切组；点 **Filter** 可选组、编辑条件/排序、**Save…** 保存同名或另存新组、**Delete** 删除。没有匹配项就显示空组，不混入其他候选。

人工修改只写 `review.override.json`，不覆盖生成的 `review.json`、CSV或结构。分组是允许重叠的查看范围，不自动标注Good/Bad。旧配置无需添加字段；带新字段的包需0.14.0及以后版本。完整生成prompt见 [walkthrough的产物整理模板](docs/structure_annotator_csv_walkthrough.md#产物整理prompt含筛选分组)。


### 0.13.0：AI连接、查看预设与路径查找

1. **Settings > Automation > AI command drafts… > AI settings…**：选择原Codex配置或 **Custom API**，填写URL、Key、模型；API格式明确选择Responses或Chat Completions，不自动换服务。Refresh读取模型列表，也可以手动输入模型ID。Key默认仅本次运行可用；勾选Remember key后使用Windows当前用户加密保存。
2. **Viewing presets…**：PPI界面、PLI口袋、oligomer亚基、membrane蛋白/脂质、DNA/RNA复合物、cartoon总览、surface，共7项。预设不需要API，先生成可编辑草稿，确认插入后再Apply。膜预设只显示现有脂质/疏水残基，不预测膜平面或重新定向结构。
3. 远端Browse支持模糊排序、方向键和Enter；成功进入上/下级目录自动清筛选，读取失败保留原位置和列表。

AI新增对象范围内透明度、半径及常见配色命令；无法完成时指出触发步骤和原因，失败重试不再清空已有预览草稿。生成与插入都不会执行命令。


### 0.12.4：减少页面文字

- 打开页只保留位置、路径和操作；帮助放在右上角 **?**。没有历史时隐藏恢复入口，没有 SSH profile 时显示 **Set up SSH…**。
- SSH 登录程序放入 **Advanced**，不再重复显示说明和程序路径。
- 审阅页移除常驻图例和分数免责声明；暂缓显示 **Later** 状态标记，筛选/抽样时才显示范围计数，分数解释按 **?** 查看。


### 0.12.3：修复桌面版 SSH 已 ready 仍停在认证

Windows 桌面 PyMOL 的宿主是 `pythonw.exe`。用它启动 Python 登录脚本时，下层 SSH 的标准输入/输出可能没有回到插件管道，表现为外部窗口已打印 `ready: true`，插件仍显示认证等待。现在仅对登录脚本改用**同一 PyMOL 目录的 `python.exe`**，仍隐藏后台窗口，保留原 SSH config、profile 和 OTP 程序，不绕过认证。

安装新版后需要完全退出并重启 PyMOL，结束旧版本拥有的 SSH 会话。定向验收 `just test-ssh-ux` / `just test-ssh-real` 默认从桌面同款 `pythonw.exe` 启动，避免只覆盖控制台 Python。

### 0.12.2：连续审阅

- **Continue last review** 恢复最近的数据源、筛选、抽样范围和候选位置；只有用户明确点击才加载，不会启动时偷偷打开远端文件。
- **Mark current for later** 使用独立的本地工作流状态，不改变 Good/Bad 等质量标签；**Next unreviewed** 会跳过暂缓项，**Next marked for later** 可集中回看。
- **Export review handoff…** 导出来源、筛选、进度、标签和待复核项的 JSON 摘要，不复制结构文件；隐藏的工作流状态不会混进质量标签导出。
- 分数卡片明确说明：相对排名只在当前审阅集内比较；绝对通过规则只有在配置或表格提供时才显示。
- 错误提示提供直接的 **Open data** 或 **Open Connections…** 恢复入口。

### 0.12.0：SSH 远端审阅提速

远端使用建议：在 **Open from** 选择现有 SSH profile，粘贴远端文件路径后打开。无需挂载、改写表格路径或更换 OTP 登录程序。

- 同一 profile 复用已认证连接；等待时显示连接/认证、队列、读取或下载阶段和已用秒数。首次 OTP 等待仍由原登录程序决定，不伪报为传输缓慢。
- 未压缩文件以轻量压缩传输，`.gz` 原样传输；接收直接流式落盘，失败或取消不覆盖已有文件。
- 非 gzip 结构直接进入既有原子缓存，避免额外的临时文件复制；已准备的结构仍沿用原来的预取/快速切换。
- 下载显示已完成大小、总大小与实际收到的压缩数据速率；它不是解压后的文件写入速率。

开发验收：`just test-ssh-ux` 运行 Windows Qt + 临时真实 OpenSSH 的定向路径。`just test-ssh-real /remote/path/scores.tsv x` 只读测量现有服务器；不要将本地回环测试当作 WAN 带宽结论。完整证据与当前状态见 `docs/validation/01_sampling_reference_2026-09-29.md` 的 SSH 章节。

### 0.11.0：AI per-step 命令草稿

1. 打开一个 candidate，在 **Settings > Automation > AI command drafts…** 输入自然语言。
2. 勾选触发位置：**When a structure opens** 是每次打开结构；**After saving label** 是保存指定标签后。它们不是候选表里的 step 列。
3. 点 **Generate draft**，查看各触发位置的多行草稿，可以直接编辑。点 **Insert into command editors…** 后明确确认覆盖；未选中的框不变。
4. 回到原编辑框继续修改，最后点 **Apply commands**。生成、插入和 Apply 本身都不会运行命令；原有打开结构/保存标签操作才触发执行。

打开结构和保存标签后的多行命令一起保存到既有状态/`review.override.json`，重开同一表会恢复；不覆盖源`review.json`。

固定使用现有 **test / gpt-6-astra**。Windows Qt 通过 `wsl.exe --distribution Debian` 启动随插件附带的短命 Python 进程（WSL Python ≥3.11），只读该默认WSL用户的 `~/.codex/config.toml`（或既有 `CODEX_HOME`）与认证。复用配置的 Responses URL，不创建服务、不改全局配置、不另存密钥。未配置、网络失败或超时会报错，不能自动换模型/provider。

请求发送自然语言、所选命令草稿和当前对象名，不上传结构文件。请不要在输入或命令草稿里放密钥。生成可取消；取消结束本地请求进程并丢弃结果，不保证供应商停止已开始的计算或计费。

AI仅支持明确的 `show / hide / color / util.cbc / zoom / orient / center / align / super` 控制台语法。每行一条命令，严格JSON契约、step映射、占位符、原生参数和当前选择都须通过校验；缺字段、额外文字、截断、未知命令等不会覆盖原框。手写命令仍沿用原有能力，不被AI白名单限制。对齐命令会移动其mobile对象，应在确认前核对方向；reference对象只在当前比较有效时存在。

验收进度和真实证据见下方同一[验收矩阵](docs/validation/01_sampling_reference_2026-09-29.md)；复现入口 `just test-ai-commands`。

### 0.10.0：先看全集，再看样本和 reference

- 候选表 **Overview / sample**：显示全集和筛选集合的分布、缺失值、组覆盖；按分组和最多五个数值分位层取稳定代表样本。每层默认 2 条，界面分别显示样本、筛选、全集计数。不是随机或无偏统计抽样。
- **Return to full filtered set** 恢复筛选集合；再清除 Filter 可回到所有候选。重新导入会退出样本模式，避免隐藏子集被误认为全集；评分百分位始终使用完整筛选集合，导出仍包含所有原始行。
- **More actions > Reference column…** 指定 reference 路径列。也可在 `review.json` 中设置 `"reference_column": "reference_file"`。用户修改存入 `review.override.json`，不覆盖生成配置或源表。
- reference 列显示文件名，悬停看完整路径。**Compare reference** 异步准备并加载真实结构，将独立 reference 对齐到 candidate；只移动 reference，不改 candidate 坐标/颜色或源文件。橙色与文字标明 reference，显示 RMSD 和匹配原子数；下一条会清除旧对照。
- 蓝色表示已保存的标注、红色表示文件错误，均保留文字；组覆盖的色块也配组名和计数。颜色不暗示评分好坏。
- 从审阅进入现有 **Batch processing** 时预填 candidate/reference 列。批处理仍读取源表，**不会自动继承审阅抽样/过滤**；选择唯一 ID 列与处理 ID，再 Preview 后运行。
- 原生命令补验修复了带逗号/括号的引号内正则、`pp_show`/撤销的表示位读取，以及报错后完成标记残留；仅两个目标命令使用专用参数入口，不修改PyMOL全局解析器。

当前证据、失败记录和未验证边界见[验收矩阵](docs/validation/01_sampling_reference_2026-09-29.md)。

Version 0.9.1 fixes real SSH imports using the existing OTP-aware login program
and default SSH configuration on PyMOL's Python 3.10. **Open data** now has one
**Open from** connection selector, one **Path** input and one **Open** button.
CSV/TSV paths are detected automatically; advanced controls are in **Import
options** and **Connections…**. Exact-case SSH aliases take priority in fuzzy
completion. [Validation](docs/validation/structure_annotator_source_ui_2026-09-17.md).

SSH reads reuse one authenticated connection for each server/program, so header,
review settings, table and structure reads do not each request another OTP.

Version 0.9.0 opens remote folders and CSV/TSV tables through existing **SSH
profiles**. Choose **SSH profile** in Open data and enter the remote path;
SSHFS and CSV path rewrites are unnecessary. Column, choice and path-history
search support fuzzy matching. **Ctrl+click** a choice saves one candidate and
advances by default, including multiple-choice fields. Ordinary multiple-choice
clicks stage choices. [Usage](docs/structure_annotator_csv_walkthrough.md) and
[validation](docs/validation/structure_annotator_ssh_fuzzy_2026-09-17.md).

Version 0.8.1 fixes Enter in filter completion: Enter, keypad Enter and Tab
accept the highlighted column while keeping the filter dialog open. Enter
applies the filter when the completion popup is closed.

Version 0.8.0 imports CSV/TSV review bundles automatically from adjacent
`review.json`, with user edits in `review.override.json`. Candidate labels,
ordered score columns and shared commands are applied on import. Bare drive
roots such as `K:` are accepted in path profiles. Folder imports prepare in the
background and can be cancelled; history suggestions no longer replace a newly
entered path before acceptance. [Usage](docs/structure_annotator_csv_walkthrough.md)
and [validation](docs/validation/structure_annotator_import_2026-09-17.md).

Version 0.7.0 embeds **Structure Annotator** and **Batch Processing** in PyMOL
by default. The top-right **Window mode** icon offers **Embedded in PyMOL** or
**Floating window**. Each tool remembers its mode, dock side/width and floating
position/size. Switching preserves drafts, selection and running work; embedded
tools share tabs instead of opening duplicate sidebars. The existing shared
style and close protections remain. [Contract and validation](docs/workspace_window_modes.md).

Version 0.6.1 aligns **Batch Processing** with the Structure Annotator workspace:
shared typography, primary actions, tables, fixed-size icon tools, progress and
error feedback. Both entry paths and normal/compact windows are checked in real
Windows PyMOL. [UI validation](docs/validation/batch_ui_consistency_2026-09-16.md).

Version 0.6.0 adds **Batch Processing** for folder/CSV structure files: all
against one reference, or A/B pairs matched by ID template, wildcard or regex.
Each pair runs an editable sequence of PyMOL commands and saves both processed
A and B by default. Preview, stop/continue-on-error, separate outputs and JSON
reports are included. Open the plugin menu or the annotator's **Data** menu,
or run `pp_batch`. [Examples and contracts](docs/batch_processing_specification.md)
and [validation evidence](docs/validation/batch_processing_2026-09-16.md).

Structure Annotator 0.5.1 restores optional one-click annotation. Enable
**Save on label click** in **Settings > Annotations** or **More actions**;
**Go to the next structure after saving** independently controls advancement.
Click-saving is off by default. **More actions > List columns** restores optional
source-row and file-status columns, and **Structure details** includes every
saved annotation, including fields no longer configured. Multiple-choice
Alt+digit shortcuts toggle draft values without saving. Evidence is in
[the feature-restoration record](docs/validation/structure_annotator_click_save_2026-09-16.md).

Structure Annotator 0.5.0 simplifies the review workspace around the current
structure and one **Save and next** action. Mouse choices remain drafts until
saved; required fields show what is missing. **Data**, **Filter**, **Settings**
and the more-actions menu open secondary controls on demand. Scores and fields
scroll while Save stays visible, including at 760 x 560. Drafts survive ordinary
navigation and opening settings/import. Screenshots and real Windows evidence
are in [the layout validation record](docs/validation/structure_annotator_layout_2026-09-16.md).

Structure Annotator 0.4.0 adds editable server path profiles and a separate
**This computer's format** selector, initially **Custom**. Profiles support
ordered prefix removal/addition, regex rewrites and Windows/POSIX root
mappings. **Edit profiles…** tests a sample without saving or probing files.
**Check file paths** opens its own result window and preserves the active
review and selected candidates. Command edits take effect only after
**Apply commands**; parser failures identify the failing line and restore the
previous structure and cursor. Validation is recorded in
[the mapping and switching record](docs/validation/structure_annotator_mapping_2026-09-16.md).

Structure Annotator 0.3.0 adds Shift/Ctrl candidate selection, required/optional
single-choice, multiple-choice, and text fields, atomic batch save/undo, and
candidate score cards. Cards follow selected CSV columns and the current row
filter; their numeric formatting is compact and full precision remains in
tooltips. Relative ranks and explicit absolute criteria are shown separately.
The **Data** menu opens directory and CSV inputs. Opening
the panel never automatically restores historical data or settings;
**Recent configurations…** provides searchable, explicit restore actions.
Windows directory imports and materialization support extended-length paths.

Structure Annotator 0.2.1 combines scores and annotation actions in one workspace.
It adds CSV-header completion, searchable column selection, typed column filters,
PyMOL-native Tab completion, and Qt shortcuts for viewport, panel, and console
focus. Import preparation and configuration writes run in the background;
already-preloaded navigation performs no file-load or filesystem-stat calls in
its click handler. Current evidence is in
[the usability validation record](docs/validation/structure_annotator_usability_2026-09-15.md).

The historical non-loopback WSL/fake-PyMOL subset passed 619/619 on both Python
3.12 and 3.13 and covers the 71 canonical commands (eight task-oriented facades
plus 63 advanced primitives) and eight declared compatibility names. Four
loopback-bound worker modules were unverified in that earlier sandbox run;
the 0.5.1 complete suite passed 716 tests. The latest reviewed
Windows Host smoke predates the
facades and passed PyMOL 3.1.3.1 with the former 63-command surface, including
`matrix_mode=0/1/2`, static CGO/map/measurement followers, group navigation,
checked object/group command failures, anchor/motif/ligand alignment,
PCA/cyclic orientation undo, style revert, and exact local interaction cleanup.
The same Host gate now also covers Structure Annotator directory and CSV
sources, row-level sidecar annotations, merged export, dataset/global state,
Windows hidden-directory behavior, cursor/config restoration, and fuzzy
history. A separate visible PyQt5 smoke verifies the real three-tab panel,
automatic popup display, mouse/keyboard candidate acceptance, targeted Scores
cell updates, same-source reload freshness, and that a CSV preview row cannot
navigate a previously loaded dataset. A real 50,000 x 8 Windows-Qt benchmark
measured 93.85% less table RSS and sub-millisecond cursor refresh. Real `K:` FUSE input
passes cold/warm Windows-local cache checks. The authenticated WSL staging
client/cache/materializer chain separately passes a real worker cold/warm E2E;
Windows-PyMOL-to-worker Structure Annotator staging remains a manual Host gate.
Windows PyMOL also reached the live WSL worker through
localhost forwarding and validated PLIP export identity.  Real mouse movement, callback-owned CGO, Qt
timeout/cancel, licensed PyRosetta, and PyMOL 2.x compatibility remain separate
gates. The updated Host harness contains task-facade and state-1 `pp_show` undo
checks, but it has not been executed in this revision and does not yet prove the
new all-state snapshot/object-identity-marker path. That facade-wide Host attempt
was blocked by WSL interop; the newer annotation workflow checks run directly in
Windows PyMOL. See
[implementation status](docs/implementation_status.md), the
[Host record](docs/validation/windows_host_smoke_2026-07-25.md), the
[annotator optimization record](docs/validation/structure_annotator_optimization_2026-07-26.md), and the
[Windows Host acceptance checklist](docs/windows_host_acceptance.md).

The following older modules are not canonical entry points:

- `workflows/dataset_navigation/file_management.py` and
  `analysis/alignment_selection/align_by_ligand.py` are compatibility facades.
- legacy style scripts delegate to the style registry.
- `workflows/dataset_navigation/screen_boost.py` and
  `file_management.py` are compatibility adapters over the same canonical
  `NavigationSession`; neither owns a second cursor or binds keys at import.
- `analysis/interactions/plip_interactions.py` keeps old command/UI names but
  its public run/clear commands delegate to the canonical WSL-backed pipeline
  and exact manifests.  New code should use `pp_interact_*` directly.
- `plugins/structure_annotator.py` retains its annotation domain and UI while
  reusing the canonical navigation cursor and explicit key leases.

The two navigation adapters are not autoloaded and their historical aliases are
not part of the declared 72+8 `pp_help` surface. Loading one manually adds only
its compatibility commands. Their console arguments use the same quote/boolean
normalization as canonical commands; input mistakes print one actionable line,
while unexpected internal failures retain a traceback. Saving `same.pdb` and
`same.cif` produces stable `same__pdb.pdb` and `same__cif.pdb` selections rather
than overwriting one file.

The detailed classification is in [the script catalog](docs/script_catalog.md).
Canonical implementation now lives under the feature-oriented
`pymolplugins/` package. Historical `analysis/`, `workflows/`, `visualization/`,
`utilities/`, and `services/` paths are import/run compatibility shims only;
optional standalone tools live under `extras/`.

## Build an installable plugin

With `just` and Python 3 installed, run from the repository:

```bash
just
# Equivalent explicit recipe:
just package
```

This packages the current working tree, including uncommitted plugin changes,
into `dist/PymolPlugins-<version>.zip`. The version comes from
`pymolplugins/__init__.py`. The ZIP includes the complete toolkit, Structure
Annotator, and their Python modules; an installed copy does not need this
repository. Optional PLIP/PyRosetta workers and ANVIL remain separate
installations. `just clean-package` removes the generated `dist/` directory.

GitHub is the source of ready-to-install ZIPs. Every push to `main` builds the
package as a workflow artifact (Actions run -> **PymolPlugins-<version>**), and
pushing a `v<version>` tag that matches `pymolplugins/__init__.py` publishes a
GitHub Release with the ZIP and `SHA256SUMS`. To cut a release:

```bash
git tag v0.14.0 && git push origin v0.14.0
```

## Install in Windows PyMOL

Use a disposable session for the first test.

1. Copy the generated ZIP to Windows, or access `dist/` through
   `\\wsl.localhost\<distro>\home\admin\gitrepo\PymolPlugins\dist`.
2. In **Plugin > Plugin Manager > Install New Plugin**, choose the ZIP and
   install it without unpacking. After replacing an older installation,
   restart PyMOL to load the updated modules.
3. In the PyMOL console, run:

   ```pymol
   pp_load
   pp_doctor
   pp_help
   ```

The Plugin menu also provides **PymolPlugins: Structure Annotator**. For source
development, run `plugins/pymol_plugins.py` from a Windows-visible checkout
instead; copying that thin entry file alone does not install its dependencies.
Windows automation must start from a normal Windows working directory rather
than an UNC current directory.

Both workspaces initially dock on the left of the PyMOL main window. Use the
top-right window icon to choose a floating window; native dock title-bar
controls also work. Mode and geometry are stored per tool in Qt user settings,
separately from dataset configuration. Closing a workspace releases its own
dock and restores the main window's original corner and viewport constraints.

`pp_load` imports only the canonical modules. `pp_doctor` reports the Host
Python/PyMOL API and optionally probes the WSL worker; `pp_help` starts with
common tasks and resolves English/Chinese task phrases, exact commands,
categories, presets, and fuzzy queries. Common phrases such as `browse groups`,
`sequence selection`, `axis align`, `ligand contact`, `ppi`, `模糊匹配`,
`contact molecular surface`, and `buried unsatisfied residues` resolve to the
relevant workflow; low-confidence arbitrary text returns `no_match` instead of
an unrelated command. A worker failure is not
fatal for local navigation, alignment, styles, geometry, or local interactions.

## Canonical quick start

The eight Tier-1 commands cover the normal workflows without exposing advanced
controller parameters:

For a disposable, one-command-at-a-time Host check, follow
[`docs/task_command_walkthrough.md`](docs/task_command_walkthrough.md).

| Goal | Copyable normal form | Preview / recovery |
| --- | --- | --- |
| Representation | `pp_show surface, assembly` | add `preview=1`; undo with returned `pp_undo OPERATION_ID` |
| Navigation | `pp_nav groups, "design_*"` | `pp_nav` reports status; `pp_nav stop` restores owned state |
| Alignment | `pp_align mobile, reference` | add `preview=1`; undo with returned `pp_undo OPERATION_ID` |
| Orientation | `pp_orient model` | add `preview=1`; undo with returned `pp_undo OPERATION_ID` |
| Contacts | `pp_contacts complex, focus=organic` | add `preview=1`; async work uses `pp_jobs`, then completion appears in `pp_undo` |
| Rosetta metrics | `pp_metrics complex, target=A, binder=B` | add `preview=1`; asynchronous by default; inspect with `pp_jobs` |
| Worker jobs | `pp_jobs` | `pp_jobs JOB_ID`; `pp_jobs cancel, JOB_ID` suppresses result handling only |
| Unified undo | `pp_undo` | `pp_undo list`; add `preview=1` to inspect the selected operation |

Action facades execute by default. `pp_show`, `pp_align`, `pp_orient`,
`pp_contacts`, and `pp_undo` accept `preview=1`; `pp_nav` with no arguments and
`pp_jobs` are status/list operations. On an input error, follow the printed
`Action:` command or run
`pp_help surface|navigate|align|orient|interactions|worker metrics`.

`pp_show` defaults to `target=all`, but that means molecule objects and recursive
group molecule leaves only; CGO, maps, measurements, and selections are not
representation targets. A commit checks every object state and, when the Host
property API is available, stores a hidden same-session object identity marker
so undo can reject a same-name replacement. `pp_orient` has no implicit target:
pass one object/group or explicit `all`. Navigation control actions such as
`next` and `stop` reject start-only options (`source`, `window`, `step`, `page`,
`wrap`, `keys`, and `replace`) instead of silently ignoring them. `pp_undo`
delegates the exact canonical reference recorded by the successful operation.

### Navigate files, objects, or root groups

```pymol
pp_nav groups, "design_*"
pp_nav next
pp_nav pin
pp_nav status
pp_nav stop
```

Use `pp_nav groups` to discover root groups, `pp_nav PATH, source=files` for a
directory, or `source=objects` for existing molecules. A group is one atomic
entry. `pp_nav` without arguments reports status and never starts implicitly.
Advanced hooks, context, and explicit cache/batch controls remain under
`pp_nav_*`.

### Resolve and iterate objects or groups

PyMOL's built-in `iterate` evaluates an expression once per atom. For
object/group-level work, use the separate target resolver:

```pymol
# Common representation task: group -> recursive molecule leaves, with undo.
pp_show surface, assembly
pp_undo

# Explicit preview never changes the scene.
pp_show sticks, "design_*", match=glob, preview=1

# Preview all matched objects; no scene mutation.
pp_target_resolve "design_*", match=glob, kinds=object

# Advanced arbitrary-template path.
pp_foreach assembly, "show surface,{target}"

# Request a non-mutating preview explicitly.
pp_foreach assembly, "disable {target}", group_mode=leaves, commit=0

# Include every recursive leaf (molecule, CGO, map, and measurement).
pp_foreach assembly, "disable {target}", group_mode=leaves

# Approximate-name matching is explicit and can be limited.
pp_target_resolve desgn_alpha, match=fuzzy, fuzzy_cutoff=0.75, limit=1
```

Matching modes are `exact`, `glob`, `contains`, `regex`, `fuzzy`, and `auto`.
An `auto` substring/fuzzy match which finds multiple names is refused before
mutation; use an exact name, explicit `match=glob|contains`, or `all_matches=1`.
Groups default to recursive `molecules`, can remain `atomic`, expand to all
recursive `leaves`, or include `both`. This setting only controls group
expansion: a directly matched CGO/map/measurement remains a valid target.
`pp_foreach` executes by default; use `commit=0` when a non-mutating preview is
desired because arbitrary PyMOL commands cannot be generically rolled back.
Templates must render as one physical parser line (semicolon-separated commands
are allowed).
Each dispatch carries an expiring in-memory same-line completion marker, so parser and
`CmdException` failures are reported in `errors` and obey
`on_error=stop|continue`; no temporary selection is created. Late acknowledgements
cannot recreate expired markers. PyMOL's console
contains the detailed parser error because its public `cmd.do` API does not
return it.

This detects parser completion, not errors swallowed inside another command,
and does not roll back partial changes or cancel PyMOL's deferred command tail.
Use one command per line in Batch Processing when failure boundaries matter.

Canonical commands normalize PyMOL console quoting at registration:
one outer single-, double-, or triple-single-quote syntax layer is removed from
explicit console arguments without decoding backslashes. Quoted selections,
regexes, command templates, and Windows/UNC paths therefore reach the Python
adapter unchanged internally.
`pp_foreach` and `pp_target_resolve` preserve their complete argument text before
splitting quoted fields, avoiding the native STRICT parser's comma/parenthesis
bug. Quoted template semicolons and following top-level commands remain supported.

### Resume Structure Annotator work and search command history

Install the ZIP and open **Plugin > PymolPlugins: Structure Annotator**. Source
developers can run `plugins/structure_annotator.py` from a complete checkout. After a
local or mapped dataset is loaded, the annotator atomically writes:

```text
<dataset>/.pymolplugins/structure_annotator.json
```

This dataset-local file records the regex, annotation fields, `On step` command,
per-bucket commands, preload options, and last file/cursor. Runtime-only PyMOL
objects, caches, navigation objects, and undo entries are not serialized.
A fresh panel opens **Open data** without restoring old settings or loading
files, even if an older saved configuration enabled automatic restoration.
Data already loaded in the current PyMOL session remains available.

Use **Data > Recent configurations…** (or **Recent…** on Open data) to search by path, date,
field or command. **Restore configuration** applies workflow settings and fills
the import inputs without loading structures or changing the active source.
**Restore and load** also opens the selected source. The selected configuration
takes priority over newer dataset-sidecar settings. Ordinary GUI imports use
the settings shown in the panel. Console `sa_load_dir` / `sa_load_csv` retain
their explicit reload/state compatibility behavior.

Finalized configurations are also stored in the Host user's global history:

```text
~/.pymolplugins/structure_annotator_history.json
```

Path and basic configuration inputs retain fuzzy-ranked global history.
CSV column fields complete against the current file's headers; noncontiguous
queries such as `acipm` match `AC.iptm`, and spaced queries match individual
name fragments. On-step and
bucket-command editors use PyMOL's native **Tab** completion; **Command history**
is a separate action. Intermediate command edits are not added to history. The history
is de-duplicated, bounded, versioned, and records use count plus recency. It is
updated when a dataset successfully loads, a configuration is explicitly
saved or used for annotation, or the panel closes—not for every intermediate
keystroke.

Useful console diagnostics are:

```pymol
sa_save_state
sa_state_status
sa_history_status
sa_history_search step_cmd, cartn
sa_history_search bucket_cmd, evrythng
```

For isolated automation, set `PYMOLPLUGINS_ANNOTATOR_HISTORY` to an alternate
history file. Dataset state is executable configuration: `On step` runs after
an object loads and a bucket command runs after annotation. Only reopen state
files from datasets you trust.

The reproducible workflow check is `tests/host/annotator_workflow_smoke.py`;
run it with Windows PyMOL's bundled Python and `--full-gui` to include viewport
and console focus. `tests/host/annotator_qt_smoke.py` retains the older CLI entry.
Commands and results are in
[the current record](docs/validation/structure_annotator_click_save_2026-09-16.md).

### Import a score CSV and annotate rows

**Multi-selection and annotation fields:** click a row to preview it, Shift-click
to select a range, and Ctrl-click to add/remove individual rows. Selection
gestures leave the current PyMOL preview unchanged. **Annotation fields** configures
required (`*`) and optional single-choice, multiple-choice, or text fields.
**Save and next** applies edited fields to the current candidate; a batch button
names the selected count. Disabling automatic advance changes its label to **Save**.
Saving applies only edited fields to the selected
rows; unchanged mixed fields are retained. Every resulting required field must
be filled before the batch is written. **Undo** restores the complete batch.
Batch annotation does not load the other selected structures or run their
per-object bucket commands. Mouse choices stage drafts by default. Enable
**Save on label click** in **Settings > Annotations** or **More actions** for
single-choice buttons and dropdowns to save the current candidate once all
required fields are complete. Enable **Go to the next structure after saving**
as well to restore the one-click save-and-next workflow. Shift+click, batch
selection and ordinary multiple-choice clicks remain drafts; failed writes preserve the
draft and cursor. Alt+digit saves a single-choice label once, or toggles a
multiple-choice value without saving.

**Ctrl+click a choice** saves the current candidate and goes to the next one,
even when ordinary automatic advancement or click-saving is off. This gesture
is available by default for single-choice and multiple-choice fields. In a
multiple-choice popup, ordinary clicks toggle staged choices; Ctrl+click a
choice or **Apply** commits all staged choices through the same save operation.
Required fields still gate saving, Shift keeps a draft, and multi-row batches
still require the explicit Save button.

**Direct SSH sources:**

In **Open data**, choose the Host alias directly in **Open from**. **Connections…**
selects an alternative **SSH config** or **SSH program**; the default config is
`~/.ssh/config`. The installed `.ssh/ssh_otp/ssh_totp.py` login program is used
when present, preserving existing verification-code authentication. Otherwise
the configured/system SSH program is used. The plugin does not force BatchMode
or copy authentication secrets. The remote host needs Python 3.
File operations share an owned SSH connection; failed reads are reported and
are not silently replayed through another client.

Paste a remote POSIX path such as
`/data/review/scores.tsv` or `~/review/structures`. **Browse…** lists the remote
filesystem through **Table file…** or **Folder…**. Click **Open**; table paths
are detected automatically. **Import options > Input type** provides an explicit
override. Relative structure paths use the remote table's folder, or the
explicit **Base folder**. Original CSV values remain unchanged. Header discovery,
table parsing, transfers and cache preparation run in the background.

SSH annotations and configuration are stored locally under
`~/.pymolplugins/ssh_sources/`, separately for each profile, config and source
path. Remote review defaults are read, and user overrides are written locally.
Remote structures and tables remain unchanged. The SSH source workspace has
its own annotation history; existing mapped-drive sidecars and inline structure
annotations are not migrated automatically. Export annotations to share them.

**Candidate score guidance:** the current structure's score summary uses up to
six selected numeric CSV columns. **More actions > Score rules…** chooses metrics,
higher/lower direction, good/bad
thresholds, or a CSV pass column. Unknown direction remains neutral. Numeric
percentiles use the current filtered candidate set; favorable relative ranks
are only colored after direction is configured. A candidate can satisfy an
absolute threshold yet rank poorly within a strong filtered set. Details also
respect the selected columns, while **Structure details** always includes all
saved annotation fields, even historical fields no longer in the current form.
Full raw values remain available on hover and in CSV exports.

Batch annotations use JSONL event schema 1.2 and require version 0.3.0 or newer
to read. Existing schema-1.0/1.1 annotations remain readable without conversion.
Exports include every saved annotation field, with JSON arrays for multi-value
fields. Implementation and acceptance details are in
[the multi-field review specification](docs/annotation_fields_and_score_guidance.md).

Paste or browse to the CSV in **Open data**. Header names load
in the background; choose the structure-path column and an optional unique ID
column using completion. **Open** opens the review workspace, where scores,
annotation buttons, and navigation share the current row. Initially only the
structure and annotation columns are shown. **Add scores / Scores…** provides search, checkboxes, **Select
all**, **Clear all**, and **Apply**; choices are saved per CSV.
**More actions > List columns > Source row / File status** shows the optional
diagnostic columns without reloading or reordering candidates. These choices
are saved with the dataset configuration and global history.

Open **Filter** and use **Text search** for substrings, or **Column conditions**, for example
`AC.iptm >= 0.8 and full_pass == True`. Column names complete while typing;
backticks quote names containing spaces or punctuation. Filters and sorting can
use hidden columns. Empty results disable navigation, and errors remain visible.
Detailed row metadata and advanced settings are collapsed by default.

**File locations > Check file paths** remains an optional complete path diagnostic. It checks
paths (including WSL staging stat) without changing the active scene, dataset,
history, annotations, or mapping store. Normal GUI loading prepares the first
readable structure and checks other paths as they are needed.

Each CSV row remains an independent entry, even when two rows reference the
same structure. The lazy review table displays selected metadata and the active label's
annotation, path/provider/cache diagnostics, substring filtering, and typed
numeric sorting. The source CSV is read-only. Annotations are append-only JSONL
events beside it:

```text
<csv-parent>/.pymolplugins/structure_annotator.<csv-id>.annotations.jsonl
```

`sa_export_csv` writes a separate, re-importable document and uses the
collision-safe column `pymol_annotation_<label>`; it refuses to overwrite the
source CSV. Per-CSV parser, mapping, staging, filter, sort, labels, commands, and
cursor settings are stored in that CSV's own version-2 state file. The GUI
restores configurations only through the explicit history actions above.

The optional `x_k` server profile maps the literal prefixes
`/xcfhome/zpzeng/mount_local`, `/xcfhome/zpzeng`, and `~/` to `K:\`. Mapped,
UNC, configured remote-drive, and `.gz` inputs are copied once into a
Windows-local persistent cache; direct `K:\...` is treated as remote by
default. Other drives can be listed in `PYMOLPLUGINS_REMOTE_DRIVES`.
For WSL paths accessible directly from Windows, select WSL as the profile's
server format and Windows as this computer's format, then provide the WSL
distribution. Drive paths convert between `C:\...` and `/mnt/c/...`; other
WSL paths use `\\wsl.localhost\<distribution>\...`. Ordinary Linux roots require
an explicit mapping. Optional worker transfer is under **File locations > File
access > Download through configured worker**. Start the worker with an explicit
`--stage-root ALIAS=PATH`, then enter that server root and alias.
The Host deduplicates multiple paths into bounded `stat-batch` requests, then
performs `stat identity -> cache lookup -> download on miss`; a one-unique-path
dataset keeps the single-stat fast path. The worker
never exposes arbitrary absolute-path, listing, write, delete, or shell APIs.

Useful diagnostics are:

```pymol
sa_mapping_list
sa_cache_status 1
sa_state_status
sa_history_status
```

See the complete [CSV/path/cache/WSL walkthrough](docs/structure_annotator_csv_walkthrough.md)
before using a production score table.

### Move a group by following one manually moved object

```pymol
pp_follow_arm driver, group=my_group
# Move "driver" with the PyMOL object-movement mouse mode.
pp_follow_apply
pp_follow_undo
pp_follow_stop
```

The follower delta is derived from object TTT matrices; PyMOL groups themselves
are not scene-graph transform parents.  Static CGO objects can follow directly.
Callback-owned overlays are skipped by default; `axes_overlay.py` registers
itself automatically.  A registered rebuild hook or explicit `cgo_policy=static`
can opt into other behavior.  Undo refuses post-apply external edits unless the
user explicitly passes `force=1`.

### Sequence selection and rigid alignment

```pymol
pp_select_sequence objects="model*", queries="GGH", occurrence=all
pp_align mobile, target, motif=GGH
pp_undo

# Explicit preview and later apply
pp_align mobile, target, ligand=organic, preview=1
pp_align apply, ALIGNMENT_ID
```

Task commands execute by default and use `preview=1` explicitly. Advanced
`pp_align_*` primitives retain their compatible preview/commit controls.
Alignment anchors and moved payloads are separate. Preview commit fingerprints both
anchors and move-object baselines, while undo refuses to overwrite newer edits
unless explicitly forced.

### Axes, orientation, and membrane slabs

```pymol
pp_orient model
pp_orient oligomer, axis=cyclic
pp_undo

# Advanced axis diagnostics and membrane drawing
pp_axis_pca selection=model, axis=long, name=model_axis
pp_axis_pca selection=model, atoms=heavy, weighting=mass, name=mass_axis
pp_axis_vector vector="0,0,1", center="0,0,0", name=normal_axis
pp_axis_two_points points="0,0,-10,0,0,10", name=two_point_axis
pp_orient_pca selection=model, move=model, target_axis=z, secondary_selection=ligand, commit=0
pp_axis_cyclic selection=oligomer, order=auto, name=symmetry_axis
pp_membrane_slab selection=oligomer, axis_source=cyclic, thickness=30
pp_membrane_slab selection=monomer, axis_source=vector, axis="0,0,1", thickness=30
```

PCA and cyclic-axis results include confidence/diagnostics.  Cn support focuses
on cyclic oligomers; higher-order dihedral/polyhedral symmetry is out of scope.
`pp_membrane_from_anvil` can parse an existing DUM-plane PDB or invoke an
explicit user-provided executable without a shell.  It validates protein
identity, maps rigidly reoriented planes back to the input frame, and rejects
non-rigid coordinate changes; no ANVIL binary is bundled.  Direct invocation
runs on the caller's OS: Windows PyMOL needs a Windows-native executable.  For
a WSL-only ANVIL build, run it in WSL and pass the output PDB back through a
Windows-visible/UNC path for parsing rather than pointing Windows at `/home/...`.

### Declarative styles

```pymol
pp_style_list
pp_style_apply publication_light, selection=all, snapshot=1, render=0
pp_style_revert
pp_style_render width=1600, height=1200, filename=figure.png
```

Applying a style does not ray trace by default.  `pp_style_render` is the only
canonical render entry point.

### Interaction presets

```pymol
pp_contacts complex, focus=organic
pp_contacts complex, focus="chain A", partner="chain B"
pp_undo

# Advanced preset discovery/export
pp_interact_list
pp_interact_export generated_group, interactions.json
pp_interact_clear generated_group
```

Canonical presets produce one versioned `InteractionResult` and delete only
names recorded in their manifest.  `polar_qc` classifies good/weak polar pairs
and donor/acceptor clashes without the fixed temporary names or global setting
mutations in the historical `show_contacts.py`.

With the WSL worker running and PLIP/OpenBabel available, PLIP presets are
asynchronous by default:

```pymol
pp_contacts complex, focus="resn LIG and chain X and resi 501", preset=plip_overview
pp_jobs
pp_jobs cancel, JOB_ID
```

The Host exports PDB text and a checked atom-identity map before starting the
thread.  HTTP is the only off-thread work; schema validation, exact-ligand
filtering, and rendering return to the Qt main thread.  A cancel request
suppresses rendering but cannot forcibly interrupt an in-progress backend.
Use `async_mode=0` only for deliberate headless/batch execution without a Qt
event loop.

The recorded validation environment completed a real RCSB 1A4W calculation
with PLIP 3.0.0, including Windows PyMOL export, WSL HTTP analysis,
exact-ligand filtering, and Host rendering.  See
[the Host evidence record](docs/validation/windows_host_smoke_2026-07-25.md);
PLIP remains optional.

## Optional WSL worker

The core worker uses only the Python standard library and can start even when
PLIP and PyRosetta are unavailable.  Optional backends must be installed in an
isolated WSL environment using their own supported and licensed distribution
channels.

```bash
python3 scripts/probe_worker_env.py --pretty
export PYMOLPLUGINS_WORKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
scripts/start_worker.sh
```

Set the same `PYMOLPLUGINS_WORKER_TOKEN` and, if needed,
`PYMOLPLUGINS_WORKER_URL` in the Windows environment inherited by PyMOL.  Then:

```pymol
pp_worker_health
pp_worker_capabilities
pp_metrics complex, target=A, binder=B, preview=1
pp_metrics complex, target=A, binder=B
pp_jobs
```

`pp_metrics` deliberately has no A/B partner defaults: target and binder must
be supplied, nonempty, present, and disjoint. The GUI default is asynchronous
and does not make a synchronous capabilities request before submission; inspect
completion and backend/readiness failures in `pp_jobs` history. Use `wait=1`
only for deliberate headless/batch execution where blocking preflight is
acceptable. `preview=1` validates the one-molecule source and request/output
plan without exporting PDB or contacting the worker. `pp_worker_health` and
`pp_worker_capabilities` remain explicit
diagnostics.

Do not put the token on a command line or commit it to the repository.  The
startup script requires a token by default and refuses non-loopback binding
without an additional explicit opt-in.  Full setup, networking, license, and
troubleshooting guidance is in [docs/wsl_worker.md](docs/wsl_worker.md).

## Development checks in WSL

The test suite does not require PyMOL:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile path/to/changed_module.py
git diff --check
```

Use the Host checklist for functionality that depends on Windows PyMOL.  Never
claim Host integration from WSL fake-command tests alone.

## Repository layout

- `pymolplugins/`: canonical feature packages and the only implementation
  namespace used by `pp_load`.
- `pymolplugins/core/`: PyMOL-independent parsing, sequence, group metadata,
  and 3D math.
- `pymolplugins/host/`: command manifest, plugin bootstrap, doctor, and help.
- `pymolplugins/worker/`: WSL HTTP application and optional backends.
- `plugins/`: thin PyMOL Plugin Manager entry files.
- `extras/`: optional standalone tools outside the canonical lifecycle.
- `analysis/`, `workflows/`, `visualization/`, `utilities/`, `services/`:
  compatibility paths retained for existing `run` and imports.
- `tests/`: feature-mirrored WSL tests plus the separate Windows Host harness.
- `docs/`: specification, status, deployment, and acceptance documentation.
- `archive/`: historical/experimental scripts, not loaded by `pp_load`.

## Licensing and third-party tools

PyRosetta, ANVIL, MSMS, PLIP, and OpenBabel are separate projects with their own
licenses and supported installation methods.  This repository does not download
or redistribute PyRosetta, ANVIL, MSMS, or credentials.  An adapter in this
repository is not permission to vendor third-party code.  The repository itself
still needs an explicit top-level license before redistribution assumptions can
be made.
