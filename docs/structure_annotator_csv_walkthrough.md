# 用 Structure Annotator 审阅评分表和结构

打开本地或 SSH 上的 CSV/TSV 和 PDB/CIF，在 PyMOL 中查看分数、筛选、标注并继续审阅。首次安装见 [快速开始](structure_annotator_quickstart.md)。目录自动识别和新版 Filter 操作对应当前开发分支。已发布安装包见 [最新 Release](https://github.com/Molaison/pymol_scripts/releases/latest)。

## 打开评分表

1. 打开 **Plugin → PymolPlugins → Review structures…**。
2. **Open from** 选择 **This computer** 或已配置的 SSH 主机；在 **Path** 选择结构目录或 CSV/TSV，点击 **Open**。
3. 如提示 **Structure column**，选择指向 `.pdb`、`.cif` 或其 `.gz` 文件的列。相对路径默认相对于表格所在目录；需要时在 **Import options → File locations** 指定基准目录或映射。
4. 在 **Review** 查看当前结构及选定的评分列。选择标签，再点击 **Save and next**；保存成功后进入下一条。

直接打开文件夹时，**Detect from path** 优先使用同级的 `sum_scores.csv`。如果没有该文件，插件会查找带结构路径列的 CSV/TSV。只找到一张时直接打开，找不到时按目录导入结构。多张表都符合时，可直接输入所需表格的路径；需要跳过评分表时，可在 **Import options → Input type** 选择 **Folder**。

SSH 导入会在本机保留最近 5 个远程来源的表格、表头、目录清单和审阅配置。单项缓存上限 64 MiB，总计 256 MiB。打开时用 SSH 检查文件版本，命中后从本机读取。用 **Data → Remote data → Check for updates and reload** 检查并刷新当前远程来源。用 **Clear remote import cache** 清理这些本机副本。结构文件仍使用原有的结构缓存。

例子只说明表格格式；将路径和字段替换成自己的真实数据：

```csv
design_id,structure_path,score1,label
design_a,structures/model_a.pdb,0.82,典型模式
```

建议使用唯一的非空 ID。多个行可以引用同一结构路径并分别标注；重复的非空 ID 会产生行错误。支持 CSV、TSV 以及自动识别的分隔符。点击 **Preview paths…** 可在打开结构前查看路径状态和选定的评分列；GUI 打开时其他候选通常按需检查，未访问项可能显示 `unchecked`。

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

在 **Filter → Text search** 搜索名称、路径或元数据。在 **Column conditions → Add condition…** 选择字段，再选择包含、不包含、开头、结尾、为空或比较，也可以直接输入 `score1 >= 0.8 and contains(notes, "candidate")`。文本包含、开头和结尾匹配忽略大小写。选中条件后点 **Group conditions** 加括号，未选中文本时将整条表达式括起来。

筛选和排序可使用未显示的列。补全支持列名、`and`、`or`、`not`、比较符和文本匹配函数。用 Tab 或鼠标接受补全，用 Enter 提交当前输入。空结果会显示空集合，错误表达式保留原视图。

补全 `startswith`、`endswith` 或 `contains` 时，先选中字段占位符。用 Tab 或鼠标选好字段后，输入要匹配的文本，再按 Enter 应用。

**Scores** 表头按当前显示的候选行统计。类别列显示最常见的三种值、占比和其余类别的占比，缺失单独计数。数值列显示直方图和最小值、最大值。筛选或抽样后，统计随表格更新。

把鼠标放在 **Scores** 表格或表头上，按住 **Shift** 滚动滚轮可横向浏览列。

点击类别或 **Missing** 的复选框可选中或取消。点 **Other…** 展开其余类别，用复选框逐项选，或用 **Select all**、**Clear all** 批量选择，再点 **Apply**。打开明细时保留原来的勾选。

同列选项用 `or` 组合，各列之间用 `and`。直方图重新拖选会替换该列的表头范围，端点取所选柱子内的实际最小值和最大值。双击直方图清除该列的表头筛选。已筛选的列会保留满足其他条件的选项和范围，选中的类别即使当前计数为零也可以取消。

**Filter** 中的 `header_filter(…)` 显示表头管理的条件，可继续编辑。提交或保存组时会移除引号外的落单括号。打开旧表头叠加格式的筛选时，会合并重复类别，并保留每列最后一个范围。

输入字段比较或匹配函数的值时，补全会提供该列的实际取值。类别值会按输入匹配，并在表达式中自动加引号。数值列会建议实际数值。**Add condition…** 里的 Value 也使用所选列的取值。

分布图默认展开，CSV 来源行号显示在表格左侧的窄行头。在候选表旁，用 **Columns…** 选择要显示的列，用 **Table options** 切换分布图、来源行号和文件状态、查看行详情或导出带标注的 CSV。**Filter / sort…** 打开筛选和排序。打开分布图后，仍可拖选数字范围或点击分类来筛选。

在 **Filter / sort… → Top per group…** 选分组列和评分，再点 **Apply filter**。例如用 `target` 分组，评分选 `ipsae` 或 `ipsae + 0.5 * iptm`。先应用行筛选，再按原始精度保留每组分数最高的可导航行。分组或分数缺失的行跳过，同分时保留原表中先出现的行。分组列会显示在结果里。可以把两个规则分别保存为命名筛选组，也可以在同一对话框点 **Remove ranking** 清掉排名。

点击 Filter 旁箭头切换命名分组，或选 **New filter group…** 创建。编辑条件和排序后点 **Save group…**，用 **Delete** 删除所选组。CSV 源的人工修改写入 `review.override.json`，目录和场景组的修改写入各自的审阅状态。`filter_presets` 数组的每项包含唯一的 `name`、`expression` 及可选的 `sort_column`、`sort_descending`。组可重叠、可为空；override 中的 `[]` 清空全部组。需要自动整理分组时使用 [统一生成提示词](02_agent_review.md#复制提示词) 中的筛选组要求。

代表性抽样是显式审阅路线，不等同全集或无偏统计样本。检查界面中的全集、筛选集和样本数量；重新导入回到完整筛选集合。批处理不会自动继承审阅的筛选和抽样。

## 聚类并审阅代表结构

在 **Review → Cluster…** 对当前 Filter 命中的候选聚类。选择 **Sequence**（MMseqs2）、**Structure**（Foldseek）或 **Sequence and structure**（同时属于同一序列簇和同一结构簇）。多链结构填要比较的蛋白链 ID，例如 binder 的链。序列也可从 CSV 的序列列读取。覆盖率要求比对覆盖两侧各自的长度。

在 **Representative score** 选一列或填组合表达式，例如 `ipsae - 0.1 * rmsd`，再选分数越高或越低越好。列名有空格时用反引号。缺失与计算无效的分数排在末尾，同分按原始行序。改分数后点 **Show representatives** 可重选代表，不会重跑聚类。选择簇后点 **Show cluster members** 查看该簇，**Full filtered list** 返回完整筛选结果。状态栏显示跳过的行数，悬停可看原因。

SSH 数据源默认使用源服务器的结构路径执行聚类，分组结果返回本机。在 **Run on** 选执行环境。Windows 与 SSH 会检查 PATH、常见 Conda 和虚拟环境目录，并验证工具是否能运行。点 **Check dependencies** 查看版本、路径和缺失项。**Install dependencies…** 复用可用环境，并在需要时把缺失工具装到所选 SSH 账户或 Windows 用户的专用目录。

Windows 选择 **Windows (native)** 可直接运行 MMseqs2 序列聚类。要从结构提取序列，还需要 Gemmi。Foldseek 结构聚类可选 **WSL** 或源 **SSH** 服务器。 [MMseqs2](https://github.com/soedinglab/MMseqs2) · [Foldseek](https://github.com/steineggerlab/foldseek) · [Gemmi](https://gemmi.readthedocs.io/en/stable/mol.html)

## 叠加与对齐结构

在 **Review** 的表格里按 **Ctrl** 点选候选，把该结构作为彩色副本叠加到当前结构。再点一次移除。普通点选继续切换当前候选。用 **Pin current** 保留当前结构以便之后切换，用 **Frame all** 将视角缩放到所有可见的比较对象。

点 **Align…** 选择一个或全部比较副本、参考对象、对齐方式和两侧的原子选择。默认比较蛋白 Cα，也可以限定链、残基或配体。按原子顺序配对时，两侧原子数须相同且顺序对应。对齐只移动比较副本，结果里的 RMSD 保持 PyMOL 所选方法的定义。

在表格中逐项批量标注选行用 **Ctrl+Shift+点选**。用 **Shift+点选** 选择连续范围。比较列表仅保留在当前审阅会话，换数据源时清空。

## 配置标注、恢复和导出

在 **Settings → Annotations** 设置单选、多选或文本字段及必填条件。单候选的单选标签在必填项完整时默认点击即保存。按住 Shift 点选可暂留草稿，多选字段的普通点击也保留草稿。选择多行后显式保存，**More actions → Undo last save** 撤销上一次完整保存。失败时草稿和当前位置保留。评分卡的相对排名针对当前筛选集合；绝对方向和阈值需由用户配置。

点击保存、保存后前进、表格显示以及加载和预取偏好在修改时自动保存。换数据源或重开 PyMOL 时继续使用已选的偏好。

在 **More → Highlight antibody CDRs (IMGT)** 可开启抗体可变区编号。表格有 `binder_chain` 或 `binder_chains` 时按该列选链，可写 `H+L`；否则从候选蛋白链中识别抗体可变区，并跳过声明的靶链。CDR1、CDR2、CDR3 在 cartoon 中分别为蓝色、橙色和洋红色。**More → IMGT numbering…** 可查结构残基号与 IMGT 编号的对应关系。编号用本机 ANARCII，结构残基号和 sticks 颜色保留。

暂时不能判断时选择 **More → Mark current for later**；**Next marked for later** 用于回看。下次主动选择 **Open data… → Continue last review** 恢复最近的数据源、筛选和位置；打开面板不会自动连接历史 SSH 源。首次导入后的历史记录可能仍在后台写入，暂不可用时等保存完成再试。

用 **Review tools → Export review handoff…** 导出进度和标注摘要，文件不包含结构。要导出带标注的 CSV，可在 PyMOL 控制台指定新文件：

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

命令配置可执行 PyMOL 操作，只使用可信配置。编辑后先确认并 **Save commands**；自定义命令的任意副作用不保证通用撤销。Viewing presets 和 AI command drafts 均先生成可编辑草稿，确认插入并 Apply 后才执行；预设依赖实际链和配体组成，不推断科学结论。

蛋白设计与复合物可使用保持相机、界面表面、固定参考叠合及候选 Cα 对齐预设；具体行为和参考选择见 [Step commands 预设](02_agent_review.md#用-step-commands-预设调整显示)。

## 连接 SSH 数据源

先在运行 PyMOL 的电脑完成现有 SSH 设置并确认 `ssh my-lab` 可以连接；Windows 默认读取 `%USERPROFILE%/.ssh/config`。首次主机信任以及私钥或密码步骤仍由现有 SSH setup 完成。远端需要 `python3`，不需要 SSHFS 或 WSL worker。SSH profile 是 Host 别名，不是密码。

在 **Open from** 选该别名，填写远端绝对路径或 `~/` 路径，例如 `/data/review/scores.tsv`，再点 **Open**。**Connections…** 可选择已有 SSH config 和登录程序。在 **Shown in Open from** 取消勾选可隐藏别名，再勾选可恢复显示。隐藏的别名仍可手输，也可通过 **Continue last review** 恢复。表格相对路径按远端表格目录解析，可设置远端 Base folder；插件按需下载结构到本机缓存，并复用认证连接。原 SSH config、跳板或代理和 `known_hosts` 继续生效。远端配置先读、本地 override 优先，远端源文件只读。

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

打开 **Plugin → PymolPlugins → Maintenance → Updates**，点击 **Check now** 检查正式版本。发现更新后点击 **Download and install**。新版本在下次启动 PyMOL 时加载。

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
