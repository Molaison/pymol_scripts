# 用 Structure Annotator 审阅评分表和结构

打开本地或 SSH 上的 CSV/TSV 和 PDB/CIF，在 PyMOL 中查看分数、筛选、标注并继续审阅。需要已安装带 Qt 的插件和自己的结构文件；首次安装见 [快速开始](structure_annotator_quickstart.md)。本指南对应 0.18.0，正式版及其安装包见 [最新 Release](https://github.com/Molaison/pymol_scripts/releases/latest)。

## 打开评分表

1. 打开 **Plugin → PymolPlugins: Structure Annotator**。
2. **Open from** 选择 **This computer** 或已配置的 SSH 主机；在 **Path** 选择结构目录或 CSV/TSV，点击 **Open**。
3. 如提示 **Structure column**，选择指向 `.pdb`、`.cif` 或其 `.gz` 文件的列。相对路径默认相对于表格所在目录；需要时在 **Import options → File locations** 指定基准目录或映射。
4. 在 **Review** 查看当前结构及选定的评分列。选择标签，再点击 **Save and next**；保存成功后进入下一条。

例子只说明表格格式；将路径和字段替换成自己的真实数据：

```csv
design_id,structure_path,score1,label
design_a,structures/model_a.pdb,0.82,典型模式
```

建议使用唯一的非空 ID。多个行可以引用同一结构路径并分别标注；重复的非空 ID 会产生行错误。支持 CSV、TSV 以及自动识别的分隔符。点击 **Check file paths** 可检查路径；GUI 打开时其他候选通常按需检查，未访问项可能显示 `unchecked`。

## 配置 review.json

将 `review.json` 与评分表放在同一目录，打开表格时自动读取。以下分数字段是占位符，须替换为实际列名：

```json
{
  "path_column": "structure_path",
  "display_columns": ["score1", "score2"],
  "label_column": "label",
  "step_cmd": "hide everything, {object}\nshow cartoon, {object}\norient {object}\nzoom {object}"
}
```

`path_column` 指向结构路径；`display_columns` 按查看顺序列出表中已有的列；`label_column` 可省略或设空。非空 label 显示为候选名称，不是人工质量标注。`step_cmd` 是每个候选共用的命令模板，`{object}` 指当前候选。无效 JSON、未知列或无效占位符会报告错误并保留当前数据集。

读取顺序是生成的 `review.json`、同目录的 `review.override.json`，再应用明确的导入选择。用户应用的配置写入 override；只打开或预览未改变的配置不会创建 override。两份配置对同目录中的表生效，独立审阅包应使用独立目录。源表、生成配置和结构文件不会被标注流程改写。

要让 agent 从已有 raw 结果整理结构和评分，使用 [review bundle 生成提示词](02_agent_review.md)。它也覆盖固定参考、步骤和筛选组。hardlink 需要同一文件系统；失败时应报告，不能静默复制或重新计算评分。

## 筛选、排序和抽样

在 **Filter → Text search** 搜索名称、路径或元数据；在 **Column conditions** 输入基于实际列的条件，例如 `score1 >= 0.8 and score2 is not None`。示例阈值只演示语法，不代表科学通过标准。可用 `contains(notes, "candidate")`，带空格或标点的列名用反引号。建议从实际评分字段、方向和先前约定确定条件。

筛选和排序可使用未显示的列。补全弹出时 Enter、数字键盘 Enter 或 Tab 接受列名；弹窗关闭后 Enter 才提交，**Apply filter** 可显式提交。空结果会显示空集合，错误表达式保留原视图。筛选不会删除结构或修改评分。

点击 Filter 旁箭头切换命名分组；在 Filter 的 **Group** 编辑条件和排序，用 **Save…** 保存、**Delete** 删除。`filter_presets` 是可选数组，包含唯一的 `name`、`expression` 及可选的 `sort_column`、`sort_descending`。组可重叠、可为空；它们不是质量标签。人工修改写入 `review.override.json`，数组整体覆盖生成配置，`[]` 清空全部组。需要自动整理分组时使用 [统一生成提示词](02_agent_review.md#复制提示词) 中的筛选组要求；先确认数据和阈值依据。

代表性抽样是显式审阅路线，不等同全集或无偏统计样本。检查界面中的全集、筛选集和样本数量；重新导入回到完整筛选集合。批处理不会自动继承审阅的筛选和抽样。

## 配置标注、恢复和导出

在 **Settings → Annotations** 设置单选、多选或文本字段及必填条件。普通选择是草稿；**Save and next** 保存并前进。单个候选 Ctrl+click 可保存并前进；多选字段的普通点击仍是草稿。选择多行后显式保存；**More actions → Undo last save** 撤销上一次完整保存。失败时草稿和当前位置保留。评分卡的相对排名针对当前筛选集合；绝对方向和阈值需由用户配置。

暂时不能判断时选择 **More → Mark current for later**；**Next marked for later** 用于回看。下次主动选择 **Data → Continue last review** 恢复最近的数据源、筛选和位置；打开面板不会自动连接历史 SSH 源。首次导入后的历史记录可能仍在后台写入，暂不可用时等保存完成再试。

用 **Data → Export review handoff…** 导出进度和标注摘要，文件不包含结构。要导出带标注的 CSV，可在 PyMOL 控制台指定新文件：

```pymol
sa_export_csv "D:/review/scores_reviewed.csv"
```

路径是示例，须替换为可写的新路径；不能覆盖源表。CSV 模式的状态和 JSONL 标注位于表格目录下 `.pymolplugins/`；SSH 源的状态、配置和标注保存在本机。不要让两个 PyMOL 进程同时编辑同一 JSONL。删除 `.pymolplugins` 前先备份，其中不只是缓存。

## 自定义审阅步骤（0.16.0 起）

在 **More → Review steps…** 添加命名对象和命令，并按执行顺序排列。`candidate` 必须恰好出现一次，使用主结构列；其他对象通过 `path_column` 指定列。`fixed: true` 复用跨候选相同路径的对象。对象 `on_load_cmd` 在首次加载或定义更新时运行；命令项按列表顺序运行。下例的列名和路径必须存在于自己的表：

```json
{
  "path_column": "structure_path",
  "steps": [
    {"type": "object", "name": "reference", "path_column": "reference_path", "fixed": true,
     "on_load_cmd": "show cartoon, {reference}"},
    {"type": "object", "name": "candidate"},
    {"type": "commands", "commands": "align {candidate}, {reference}"},
    {"type": "commands"}
  ],
  "step_cmd": "show cartoon, {candidate}\nzoom ({candidate}) or ({reference})"
}
```

只引用执行到当前位置已经加载的对象；空辅助路径可跳过，但引用不存在对象会报错。`{object}` 和 `{selection}` 指 candidate；其他占位符包括 `{file}`、从 0 开始的 `{index}`、`{count}`、`{entry_id}`、`{row_number}`。标签动作还可用 `{label}`、`{value}`。未配置 `steps` 时使用 candidate 后执行 `step_cmd`。旧 `reference_column` 只映射为通用固定对象，不自动对齐、着色或调整视图。

命令配置可执行 PyMOL 操作，只使用可信配置。编辑后先确认并 **Apply commands**；自定义命令的任意副作用不保证通用撤销。Viewing presets 和 AI command drafts 均先生成可编辑草稿，确认插入并 Apply 后才执行；预设依赖实际链和配体组成，不推断科学结论。

蛋白设计与复合物可使用保持相机、界面表面、固定参考叠合及候选 Cα 对齐预设；具体行为和参考选择见 [Step commands 预设](02_agent_review.md#用-step-commands-预设调整显示)。

## 连接 SSH 数据源

先在运行 PyMOL 的电脑完成现有 SSH 设置并确认 `ssh my-lab` 可以连接；Windows 默认读取 `%USERPROFILE%/.ssh/config`。首次主机信任以及私钥或密码步骤仍由现有 SSH setup 完成。远端需要 `python3`，不需要 SSHFS 或 WSL worker。SSH profile 是 Host 别名，不是密码。

在 **Open from** 选该别名，填写远端绝对路径或 `~/` 路径，例如 `/data/review/scores.tsv`，再点 **Open**。**Connections…** 可选择已有 SSH config 和登录程序。表格相对路径按远端表格目录解析，可设置远端 Base folder；插件按需下载结构到本机缓存，并复用认证连接。原 SSH config、跳板或代理和 `known_hosts` 继续生效。远端配置先读、本地 override 优先，远端源文件只读。

### 为服务器启用内置 OTP

1. 打开 **Connections…**，在 **SSH server** 选择服务器，例如 `f101`。
2. 勾选 **Automatic OTP**，在 **OTP secret** 填写认证器的 Base32 设置密钥，不是当前六位验证码。
3. 展开 **Advanced**，将 **SSH program** 留空，再点 **OK**。如果以前明确保存了 `.py` wrapper 路径，先清空它。插件会使用系统 OpenSSH；不需要安装或修改 `ssh_totp.py` 或其他 wrapper。
4. 返回 **Open from**，选择同一 Host 并打开远端路径。每组 Host 和 SSH config 分别保存配置。

未勾选 **Remember encrypted key on this computer** 时，密钥只供当前 PyMOL 进程使用；在 Windows 勾选后，使用当前 Windows 用户的 DPAPI 加密保存。密钥不会写入 SSH URL、`review.json` 或评分表。取消对话框不会更改已保存值；取消 **Automatic OTP** 并点 **OK** 会删除该主机已保存的密钥。

默认识别 `Verification code`、`OTP`、`One-time password` 等提示；一般将 **Advanced → OTP prompt** 留空。服务器提示不同时，在该项填写匹配提示的正则表达式。内置 OTP 使用标准 TOTP：SHA1、六位、30 秒；它不填写普通密码或私钥口令，也不自动接受新主机。未启用内置 OTP 的已有 wrapper 方式仍可使用。

一次 SSH 连接最多提交一次验证码，服务器拒绝后不会自动重试。临近过期会等待下一窗口；同一进程内同一密钥不会复用时间窗口。不同 PyMOL 进程或外部 OTP 工具之间没有共享窗口锁，使用同一密钥时需要自行留意。

### 从 Python 配置 OTP

界面是推荐入口。维护脚本可用变量传递已取得的设置密钥，不要把真实密钥写进命令历史：

```python
from pymolplugins.remote.otp import configure_otp
from pymolplugins.remote.ssh import SshClient

configure_otp('f101', secret=otp_secret, config='', remember=False)
client = SshClient(location, otp_secret=otp_secret, otp_prompt=None)
```

`otp_secret` 和 `location` 是调用者提供的变量。`configure_otp` 设置该 Host 与 config 的配置；`remember=False` 仅保留在当前进程。`SshClient` 的 `otp_secret` 是单次客户端参数：`None` 读取该 profile 配置，空字符串显式禁用 OTP；`otp_prompt=None` 不指定自定义提示。

Windows 原生 OpenSSH 经临时本地挑战服务与 PyMOL Python worker 的验证覆盖了默认和自定义 OTP 提示、拒绝后只提交一次、普通密码拒填及约一秒取消；Qt 和 DPAPI 验证覆盖了保存重开、取消、禁用和仅会话使用。真实 `f101` 登录与 Unix 端尚未验收。

找不到主机时检查 Windows SSH config；`Permission denied` 时先修复终端 SSH 认证；找不到结构时检查远端路径和结构列。取消连接或下载会关闭插件拥有的连接；下一次操作可以重新连接。单文件下载上限 512 MiB、目录浏览上限 100,000 项；被阻塞的底层读取不能保证立即终止。

## 映射、缓存和诊断

本地/挂载盘来源可在 **File locations** 配置路径映射；`x_k` 是已有的映射 profile，只有实际挂载和路径相符时才选。WSL-only staging 是可选的独立 worker 功能，必须明确配置允许的 root/alias；CSV 本身仍需 Windows 可访问。直接 SSH 导入不需要它。[Worker 配置](wsl_worker.md) 说明启动和限制。

在 PyMOL 控制台查看状态：

```pymol
sa_state_status
sa_history_status
sa_mapping_list
sa_cache_status 1
```

`sa_cache_clear` 清理未 pin 的缓存；完成当前数据集且明确需要时才用 `sa_cache_clear 1` 清理 pin 项。后台导入准备可取消，`Committing…` 期间的主线程提交不能中途打断；失败或迟到结果不应覆盖当前场景。控制台 `sa_csv_preview` / `sa_load_csv` 保持同步。更多映射、缓存、导出与错误检查点保留在 [详细参考](04_reference_csv_2026-11-06.md)，其中旧界面名称和历史性能结果按当时版本理解。

## 更新插件

打开 **Plugin → PymolPlugins: Updates**，点击 **Check now** 检查正式版本；发现更新后点击 **Download and install**。安装完成后完全退出并重新打开 PyMOL；插件不会在当前会话热重载。

在同一窗口，**Check for updates when PyMOL starts** 控制启动时检查，默认开启；**Automatically install stable updates (restart required)** 控制自动安装稳定版本，默认关闭。两项可分别设置；自动安装后同样需要完全重启。

0.17.1 起，如果 GitHub API 返回匿名额度耗尽的限流错误，更新器会尝试从公开的 [最新 Release](https://github.com/Molaison/pymol_scripts/releases/latest) 获取稳定版本和 `SHA256SUMS`，无需用户提供 GitHub token。此回退仅针对 API 限流；公开页面或下载不可访问等其他网络错误仍需处理。

更新器继续校验 SHA256、版本和 ZIP 路径，在同一卷暂存和替换，失败时恢复原目录。设置、标注和科学源文件不参与替换。上一版保存在安装目录旁的 `.pymol_plugins-backup`；需要恢复时先关闭 PyMOL，再将备份改回 `pymol_plugins`。

如果旧版更新器因 API 限流无法检查，可在 [最新 Release](https://github.com/Molaison/pymol_scripts/releases/latest) 提供 0.17.1 或更新版本后，下载 `PymolPlugins-<version>.zip`，在 **Plugin → Plugin Manager → Install New Plugin** 选择 ZIP，完成后完全退出并重启。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="准备评分表并导入"></a>
- [准备评分表并导入](04_reference_csv_2026-11-06.md#%E5%87%86%E5%A4%87%E8%AF%84%E5%88%86%E8%A1%A8%E5%B9%B6%E5%AF%BC%E5%85%A5)
<a id="筛选保存和交接"></a>
- [筛选、保存和交接](04_reference_csv_2026-11-06.md#%E7%AD%9B%E9%80%89%E4%BF%9D%E5%AD%98%E5%92%8C%E4%BA%A4%E6%8E%A5)
<a id="找到高级选项"></a>
- [找到高级选项](04_reference_csv_2026-11-06.md#%E6%89%BE%E5%88%B0%E9%AB%98%E7%BA%A7%E9%80%89%E9%A1%B9)
<a id="插件更新0150-起"></a>
- [插件更新（0.15.0 起）](04_reference_csv_2026-11-06.md#%E6%8F%92%E4%BB%B6%E6%9B%B4%E6%96%B00150-%E8%B5%B7)
<a id="连续审阅"></a>
- [连续审阅](04_reference_csv_2026-11-06.md#%E8%BF%9E%E7%BB%AD%E5%AE%A1%E9%98%85)
<a id="新用户快速开始"></a>
- [新用户快速开始](04_reference_csv_2026-11-06.md#%E6%96%B0%E7%94%A8%E6%88%B7%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B)
<a id="1-runtime-boundary-and-preflight"></a>
- [1. Runtime boundary and preflight](04_reference_csv_2026-11-06.md#1-runtime-boundary-and-preflight)
<a id="ai命令先生成草稿再确认"></a>
- [AI命令：先生成草稿，再确认](04_reference_csv_2026-11-06.md#ai%E5%91%BD%E4%BB%A4%E5%85%88%E7%94%9F%E6%88%90%E8%8D%89%E7%A8%BF%E5%86%8D%E7%A1%AE%E8%AE%A4)
<a id="direct-ssh-sources"></a>
- [Direct SSH sources](04_reference_csv_2026-11-06.md#direct-ssh-sources)
<a id="2-prepare-a-minimal-score-csv"></a>
- [2. Prepare a minimal score CSV](04_reference_csv_2026-11-06.md#2-prepare-a-minimal-score-csv)
<a id="3-preview-without-changing-the-current-scene"></a>
- [3. Preview without changing the current scene](04_reference_csv_2026-11-06.md#3-preview-without-changing-the-current-scene)
<a id="4-load-inspect-scores-filter-and-sort"></a>
- [4. Load, inspect scores, filter, and sort](04_reference_csv_2026-11-06.md#4-load-inspect-scores-filter-and-sort)
<a id="5-annotate-duplicate-path-rows-independently"></a>
- [5. Annotate duplicate-path rows independently](04_reference_csv_2026-11-06.md#5-annotate-duplicate-path-rows-independently)
<a id="6-export-and-re-import"></a>
- [6. Export and re-import](04_reference_csv_2026-11-06.md#6-export-and-re-import)
<a id="7-verify-per-csv-state-and-global-fuzzy-history"></a>
- [7. Verify per-CSV state and global fuzzy history](04_reference_csv_2026-11-06.md#7-verify-per-csv-state-and-global-fuzzy-history)
<a id="8-configure-server-profiles-and-test-the-k-mapping"></a>
- [8. Configure server profiles and test the K: mapping](04_reference_csv_2026-11-06.md#8-configure-server-profiles-and-test-the-k-mapping)
<a id="9-test-wsl-only-structure-staging"></a>
- [9. Test WSL-only structure staging](04_reference_csv_2026-11-06.md#9-test-wsl-only-structure-staging)
<a id="10-diagnostics-and-safe-cleanup"></a>
- [10. Diagnostics and safe cleanup](04_reference_csv_2026-11-06.md#10-diagnostics-and-safe-cleanup)
<a id="11-current-limits-to-record-during-testing"></a>
- [11. Current limits to record during testing](04_reference_csv_2026-11-06.md#11-current-limits-to-record-during-testing)
<a id="筛选分组0140"></a>
- [筛选分组（0.14.0）](04_reference_csv_2026-11-06.md#%E7%AD%9B%E9%80%89%E5%88%86%E7%BB%840140)
<a id="产物整理prompt含筛选分组"></a>
- [产物整理prompt（含筛选分组）](02_agent_review.md#复制提示词)

</details>
