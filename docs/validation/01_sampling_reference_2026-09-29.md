# 全量概览、抽样与 reference：执行及验收

## 当前任务
状态：**本次0.10.0增量、安装验收和命令级自动验收已完成；项目全环境/人工验证仍有下列明确待办**。负责人会话：`01a0e982-0ba7-7f20-aae6-748f04eac0cc`（gamma / Debian WSL）。
目标：保留既有工作，交付真实可用的概览、分层样本、颜色标识和结构对照，并覆盖相关异步与紧凑窗口路径。
工具限制：Beads 不可用；本文件是唯一任务状态记录。job-runner/pi-job-runner 未调用。

## 用户可见验收矩阵
| 功能 | 已有真实证据 | 本轮状态 |
|---|---|---|
| CSV/TSV、文件夹导入；取消、错误保持场景 | 2026-09-17 source/import Host 记录 | 本轮真实TSV异步导入及view取消通过；文件夹/SSH本轮未重跑 |
| 标注保存、下一项、草稿、紧凑窗口 | 2026-09-22 save visibility Host 记录 | 本轮Host窗口回归、600行样本内异步保存与760×560通过 |
| 全量分布、分层抽样、回到全集 | 本轮真实600行 | 30/600样本；筛选后10/200/600；恢复筛选集及全集通过 |
| 颜色与文字/图例 | 评分卡已有方向配置 | 分组色块/文字、reference橙色原子及RMSD、紧凑布局通过；文件错误/保存标签保留文本 |
| reference 列、真实加载与对照、保存重开 | 本轮2399原子真实结构 | candidate坐标变化0、缺失reference错误、override保存重开通过 |
| 批处理输出、取消和错误 | 本轮既有Host批处理＋真实远端配对 | 两类均通过，证据见下；修复K: inode=0身份误判 |
| 原生命令与 task facade、显示/撤销 | 原14组分项已取得通过证据；另增1组语法检查 | Windows原生执行；修复项在真实Qt主线程通过，含离散2状态30原子撤销 |
| 最终0.10.0安装包 | 临时真实Plugin Manager安装 | 11模块、72主命令+8兼容命令、6菜单及工作区通过 |
| worker/PLIP/PyRosetta、真实ANVIL、PyMOL 2.x及手动交互 | 总表有各自边界 | 本轮未验证；不宣称项目全功能全环境通过 |

## 实施边界与恢复
主线程负责现有 plugin/state/reference 集成、文档和 Host 验收。
子线程 `01a0e984-2519-7f01-9c75-202b6c84c458` 仅写 `annotation/sampling.py`、`sampling_widgets.py`；其余文件不并发写。
初始进程检查未发现明确的其他本项目测试/编辑进程；共享 daemon 的存在不代表它在写本项目。若发现冲突则暂停同文件写入并报告。
最终交付与后续边界见文末；“维护暂停交接”仅保留历史安全点，不再表示暂停。600行真实验收未重跑。

## 已运行事实
- `output/annotation-window-20260929.json`：Windows PyMOL 3.1.3.1，保存/下一项、草稿、Notes、最大化与 760×560 布局通过（既有小结构回归，不是600行实数据信据）。
- `output/01_sampling_reference_20260929.json`：真实 RNA 表 600 行、56 原列，3 个 seed × 5 层 × 2 条 = 30 样本；筛选一个 seed 后 10/200/600；恢复筛选集合、清除筛选、实际异步取消/保存、缺失 reference 错误、用户配置保存重开均通过。原始输入表和两份比较结构散列不变。
- reference 2399 原子；对齐后 RMSD 16.138 Å，2199 个匹配原子（仅描述对齐，不表示质量合格）；candidate 最大坐标变化 0。真实 QGL framebuffer 有357种采样颜色，显式 repaint 后截图，不证明无人值守自动重绘时序。
- `*-failed1.json`：验收脚本错误读取 PyMOL Atom.color；改为原生 cmd.iterate。`*-failed2.json`：验收脚本把保留原分隔符的TSV导出当作逗号CSV；改用实际分隔符。
- `*-failed3.json`：真实配置竞态。保存位置时反复原子替换内容不变的 review.override.json，导致导入签名检查失败。现在设置写入线程在内容相同时不替换；不放宽源变更检测，重开已通过。
- 一张桌面坐标截图被其他窗口遮挡，未作为证据保留；已换为已存在验收方法中的 QGL 原生 framebuffer。紧凑窗口 RMSD 改为换行而非省略。
- 既有批处理 Host session `17729` 已退出0；`output/02_batch_20260929.json` 状态 passed，输出最大坐标误差0，包含取消、错误、紧凑布局、长路径和原文件保护。

## 维护暂停交接（历史安全点；当前进展见文末）

暂停原因：用户要求父会话重启本次新建的 WSL Codex 后台，使代理环境生效。**不是项目功能失败，也不授权修改全局配置。** 等待用户明确继续，保留同一线程和全部未提交修改。

### 已交付到工作树
1. `sampling.py` / `sampling_widgets.py`：全量分布、缺失值、组覆盖、最多五个数值层的确定性代表抽样。
2. `plugin.py` / `panel_layout.py` / `review_controller.py`：复用现有游标、明确全量/筛选/样本范围、保持筛选全集评分百分位；reference列、可取消后台准备、独立reference对齐、文字配色、紧凑窗口换行。
3. `review_file.py`：reference配置/override保存；内容相同不重写override，避免位置保存干扰导入签名检查。
4. `batch_widgets.py` / `batch_sources.py`：审阅到批处理的A/B列交接及“不继承抽样/过滤”提示；修复真实K:映射盘st_ino=0导致不同文件被判同一文件。保留同路径和硬链接保护，不用不受该卷支持的Windows realpath。
5. `tests/host/01_sampling_reference_smoke.py` 与 `just test-review-overview`：真实数据端到端验收入口。`readme.md` / `docs/implementation_status.md` 已有0.10.0说明；版本已更新。

### 已验证证据
| 证据 | 实际结果 |
|---|---|
| `output/01_sampling_reference_20260929.json` / `.log` | **最终 passed**；600行56原列，导入0.232秒；30样本；筛选后10/200/600；真实异步保存/取消/恢复、reference错误与配置重开、源散列保护均通过 |
| 同前缀 `-scene.png` / `-compact.png` / `-overview.png` | 真实QGL framebuffer及Qt界面；橙色reference叠合，紧凑窗口RMSD完整换行；已实际查看 |
| 同前缀 `-batch.json` / `-batch-candidate.cif` / `-batch-reference.cif` | 从审阅交接真实K:配对，1对/2输出；实际cmd.load重载并检查原子和坐标集 |
| 同前缀 `-export.csv` / `-state.json` / `-annotations.jsonl` / `-review.override.json` | 原生导出/状态/事件/配置；导出保留原表分隔符和全部600行 |
| `output/02_batch_20260929.json` / `.log` | 既有Windows GUI批处理验收passed；最大坐标误差0；本轮K:身份修复之后的共享边界另见下一行 |
| `output/02_batch_identity_20260929.json` | 原生Windows：两个不同K:文件inode均0仍可配对；同路径及真实本地硬链接仍判为同一文件 |
| `output/annotation-window-20260929.json` / `.log` | 保存、草稿、最大化和紧凑窗口既有回归passed |

故障证据保留 `*-failed1` 至 `*-failed5` 的JSON/log；前3次详见上文。failed4确认K:不同文件inode均0而原生samefile误判；failed5为初版修复调用Windows realpath遭WinError1005，该调用已移除。最终验收 session `69715` 退出0，不留待监视任务。

### 安装包与未完成项
- 已构建 `dist/PymolPlugins-0.10.0.zip`，**448509字节**；最近构建日志 `output/04_package_20260929.log`。这是包含最终K:修复的包。尚未执行本版本的临时Plugin Manager安装验收，不能称“安装已验证”。没有改用户现有安装。
- 命令级Host补验的启动调用已退出1，日志 `output/03_host_commands_20260929.log`：**PowerShell执行策略在启动PyMOL前阻止.ps1（PSSecurityException / UnauthorizedAccess）**。这不是命令功能失败或通过。恢复时修正本次启动方式，不能改全局执行策略或绕过原生Host边界。
- SSH初始OTP耗时/中断、可选worker/PLIP/PyRosetta、真实ANVIL、PyMOL2.x和所有手动交互功能仍有既有独立未验证项。不能用本轮局部通过覆盖整个项目的证据边界。

### 进程和子代理安全点
- 抽样worker `01a0e984-2519-7f01-9c75-202b6c84c458` 已报告完成，写边界仅两个sampling模块；主线程调用关闭返回not_found。没有等待中的子代理工作包。
- 本线程启动的所有exec测试/打包句柄均已终态；最近核心GUI验收`69715`退出0，命令Host启动调用退出1，打包退出0。**没有活跃测试需要恢复监督，没有科学作业或job-runner提交。**
- 本轮未做reset/clean/commit、未改SSH身份或全局shell/Codex/provider配置。

### 收到“继续”后的下一步
1. 先读本节及`03_host_commands_20260929.log`，处理PowerShell进程级启动阻塞后继续命令级Host验收；保持原Windows PyMOL入口，不改全局配置，不重做已通过增量。
2. 使用既有 `tests/host/plugin_package_smoke.py` 对上述确切ZIP做可回滚临时安装验收；证据写入既有output/。
3. 在本文件合并最终状态和用户验收矩阵，明确仍需环境/人工条件的项，再向用户交付成果和安装入口。

## 维护后继续
用户已明确继续，同一线程和模型/provider不变。保持600行验收成果，不重跑该流程。
维护时初稿的0.10.0 ZIP安装验收通过，exec session `90788` 退出0；结果现保留为 `output/05_plugin_package_20260929-before-runtime-fixes.json` 和 `.log`。修复命令级运行时之后又验收了最终ZIP，见下文；用户现有插件安装不变。
首次恢复后的session `7559`在进入Windows Python前失败（UtilAcceptVsock accept4 failed 110），日志保留为 `05_plugin_package_20260929-interop-failed.log`。会话继承 `/run/WSL/193380_interop`；仅对验收子进程指定已有 `/run/WSL/2_interop` 即成功，没有改全局环境/配置或重启WSL。
命令级入口改为 `just test-host-commands`，直接使用同一Python/PyMOL并复用 `tests/host/host_smoke.py`，不调用PowerShell、不修改任何安全策略。它证明原生Windows cmd.* 边界，不替代GUI或可选worker/许可证验收。
恢复命令保存在justfile；新增 `just test-package` 是上述安装验收的可复现命令。没有新子代理或科学任务。
当前命令级运行使用 `env WSL_INTEROP=/run/WSL/2_interop just test-host-commands`；原PowerShell失败日志保留为 `output/03_host_commands_20260929-powershell-blocked.log`，新的Python直连结果写 `output/03_host_commands_20260929.json` / `.log`。

## 最终交付与真实证据

### 安装包
**`dist/PymolPlugins-0.10.0.zip`，449210字节。**

SHA-256：`a9544b693e885eb0e638876b145264f01d6be76257ceea0df10d01907da60423`

在 Windows PyMOL 中选择 **Plugin > Plugin Manager > Install New Plugin** 安装；替换旧版本后重启PyMOL。该ZIP保留新增概览/抽样、文字配色、reference列，以及本轮命令修复。

最终构建：`output/04_package_20260929-final.log`。
最终真实安装：`output/05_plugin_package_20260929.json` / `.log`，session `72087` 退出0，JSON含同一ZIP的SHA-256。PyMOL **3.1.3.1** / Windows Python **3.10.16**，安装到自动清理的临时目录，完整11模块、72主命令+8兼容命令、6菜单、两个工作区及其嵌入/浮动/标签切换通过。不是仅import成功，也没有改用户当前安装。

### 命令与共享边界
| 原始记录 | 真实结果与证据边界 |
|---|---|
| `03_host_commands_20260929.json` | 首次完整Windows无界面执行14组，12组pass；其余2组的原始失败保留，**此JSON总体仍是fail** |
| `03_host_commands_20260929-repaired.json` | 真实PyMOLQtGUI主线程：任务facade通过，含2个离散state共30原子显示/撤销；当时仍含目标命令标记残留失败，**不能把该整份记录称passed** |
| `03_host_targets_20260929-final.json` | 目标解析/foreach最终Qt验收passed，包括引号正则、模糊批量批准/拒绝、部分错误、stop/continue与无场景标记残留 |
| `03_host_syntax_20260929.json` | 新增语法Qt验收passed：关键字绑定、引号内模板分号、后续顶层命令、未闭合引号错误及迟到ack |
| `02_batch_20260929-dispatch.json` | 完成标记变更后补验共享批处理：56对/112结构，最大坐标误差0，51份源文件散列不变，431字符输出路径，取消及stop/continue通过 |

上述记录均在 `output/`，各有同名前缀log。通过证据按**分项**合并覆盖原14组及新增1组语法；不是捏造一次全部passed的运行。600行RNA审阅流程保持之前的真实通过证据，本轮按用户要求没有重跑。

### 修复与失败归因
1. 启动：直接Bundled Python避开PowerShell策略；`runpy`提供明确脚本上下文，`-k`跳过用户启动脚本而不修改它。初次 `cmd.run` 路径错误保留为 `*-bootstrap-failed.*`。原生退出0可能伴随失败JSON，just入口现在以最终JSON判定退出状态，不以进程退出码代替验收。
2. 正则：原生STRICT解析器在引号字符串内遇到括号与逗号仍错误分割。仅`pp_foreach`/`pp_target_resolve`使用原生LITERAL入口与受控字段拆分，保留反斜杠、引号、关键字和后续命令；不改全局PyMOL解析函数。
3. 显示/撤销：原生`get_model().atom`不含`reps`；改用`cmd.iterate`读取完整对象原子表，保留所有状态的身份检查。旧测试用单CA配体做默认多点对齐不成立；改用已有多残基结构，并用适合其对称性的cyclic方向检查，不放宽生产阈值。
4. 完成标记：真实Qt也重现失败尾部的选择标记迟到，不是仅无界面启动问题。现在用独立唯一token及短命Event，不再创建场景选择；检查完成状态后移除token，迟到回执不影响下一次调用。原异常和partial-error保留。**这不宣称取消了PyMOL自身延后的用户命令后缀，也不检测其他命令内部吞掉的错误**；重要多步工作使用Batch Processing每行一条命令。

辅助检查：31项任务测试、13项目标测试通过；它们不替代以上原生证据。本轮所改文件的`git diff --check`通过。新增/历史失败日志均保留，没有用mock结果替换真实功能结果。

### 复现与当前进程状态
正常WSL interop可用时，在项目根目录运行：

```bash
just test-package
just test-host-commands
```

当前持久会话继承的interop通道曾失效；本轮成功调用仅对验收子进程加 `env WSL_INTEROP=/run/WSL/2_interop` 前缀。这个路径是当前gamma已有socket，不应写入通用全局配置。定向命令验收可用just的第三参数，例如：

```bash
env WSL_INTEROP=/run/WSL/2_interop just test-host-commands \
  /mnt/d/Softwares/Pymol3.1/python.exe output/03_host_targets_20260929-final.json HOST-TARGETS-01
```

所有本线程测试/打包句柄已终态，最终安装session `72087`和批处理session `57619`均退出0。只读advisor `01a0e9ac-59f6-7322-8562-f1a98af4f26a`已完成并关闭；前一抽样worker也已完成。没有待监督作业、没有job-runner提交，也未修改模型/provider、全局shell/Codex/SSH或Windows安全策略。

### 后续边界（不是本次交付阻塞）
- 可以直接安装上述ZIP使用新增功能；当前无未解决的增量功能/安装/命令自动验收阻塞。
- 项目全覆盖仍需独立条件：真实鼠标对象拖动、callback拥有的CGO随视图变化、远端Qt请求超时/取消、初始SSH OTP等待，以及实际worker/PLIP/PyRosetta、ANVIL和PyMOL2.x。不能凭本轮自动通过称这些完成。
- 后续继续以本文件为唯一任务状态入口，优先选择上述一个尚缺真实证据的边界；不要重跑已通过600行数据以制造重复证明。

## AI per-step增量（已交付；下述实施记录保留，最终结果见文末）

同一线程 `01a0e982-0ba7-7f20-aae6-748f04eac0cc`。用户明确追加并授权实现；当前没有子代理或新科学作业。

已确认：`STATE.step_cmd`是每次打开结构的多行模板，`bucket_cmds[value]`是保存该标签后的模板。现有`format_commands`逐行替换并通过`_execute_checked`执行；`validate_commands`检查占位符，不验证命令名。将复用该解析链，增加AI专用的命令白名单和原生参数/选择校验，不收紧旧的手写命令。

契约：`{"schema":"pymolplugins.ai-commands/1","steps":[{"step_id":"on_open","commands":["show cartoon, {object}","zoom {object}"]}]}`。step_id必须与用户选中的触发框完全一致（`on_open`、`after_label/<实际标签值>`），commands为按序单行字符串数组，填框时用换行连接。禁止Markdown、额外字段/文本、重复/缺失step、未知占位符/命令、分号串联及截断结果。生成不执行，明确确认覆盖草稿后仍需原来的Apply commands。

网络路线：Windows QProcess → 已有Debian WSL Python → 只读现有Codex `test` provider配置（Responses，模型`gpt-6-astra`）。没有现成项目AI客户端；不写全局配置、不复制密钥到Windows或仓库。

| 用户可见能力 | 当前证据 |
|---|---|
| 600行概览/抽样、颜色、reference、保存/取消 | 既有真实通过，保持不重跑 |
| 0.10.0安装、命令级启动和共享批处理 | 上节真实通过，保持 |
| AI多step/多行严格解析、草稿确认和编辑 | 实施中，待真实Windows UI验收 |
| 当前provider请求到PyMOL实际执行 | 待真实请求及原子/表示/坐标检查 |
| 取消、网络失败、坏结果不污染、紧凑窗口 | 待原生交互与定向故障验收，故障注入需单独标明 |

下一步：实现契约/Qt草稿与WSL provider进程；预计开发加原生验收30–45分钟。所有既有未提交修改保留；没有发现别的会话在写本次目标文件。

### AI开发与首个完整真实结果

新增`annotation/ai_commands.py`（prompt、唯一schema、严格解析、项目模板+原生只读校验）、`ai_widgets.py`（各触发草稿、确认、取消/失败）、`ai_provider.py`（WSL只读既有provider配置、Responses请求）。旧label命令框升级为多行，存储仍是`bucket_cmds[value]`字符串，没有迁移或第二套执行器。

`output/06_ai_commands_20260929.json` **passed**（Windows PyMOL3.1.3.1，session `73669`退出0）：
- 当前provider实际请求6.471秒；返回2个step、6条命令；编辑后On open有6行、Good有2行。
- 原有导航执行：2399原子全部cyan，原生reps=32（cartoon）；保存Good后全部yellow，reps=33（cartoon+sticks）。不是仅JSON有效。
- 生成/插入不执行、拒绝覆盖保留原框、确认后仍需Apply、GUI心跳215次；多行模板实际异步保存通过。
- 11类坏文档在真实GUI解析入口注入，全部拒绝且不污染原框。它们是**定向故障注入，不是模型自然返回**。
- 实际provider请求发出后取消，WSL进程约0.0205秒退出，编辑框不变；不能承诺远端计算/计费同时停止。
- 原配置/模型不变，仅该测试子进程使用拒绝连接的本地代理，真实网络失败提示及重试按钮通过。没有修改全局代理。

同前缀`-provider.json`含原始模型JSON文字、请求ID和usage，`-request.json`含实际prompt/schema；均不含认证。`-state.json`、`.pse`、Qt草稿/错误截图保留。首个scene截图观察到旧OpenGL帧，**不把该图称为最终黄色显示证据**；原子/reps检查不受影响，安装验收将显式驱动原生重绘并补图。

两个早期失败保留：`*-fixture-failed.*`是测试review配置误用了不支持的id_column；`*-cancel-harness-failed.*`是已取消进程释放后测试又访问QProcess，主流程当时已通过。只修验收脚本，不放宽生产断言。

版本递增为0.11.0，保留0.10.0包及其证据。当前正在真实Plugin Manager临时安装后，从**Windows本地安装路径**完整执行AI功能，并验证模板保存重开；这是新路径/交付包边界，不重跑600行。监督句柄session `23241`，结果目标`output/07_ai_installed_20260929.json`。没有子代理。

### AI最终交付

**0.11.0已通过安装后的真实端到端验收。** `output/07_ai_installed_20260929.json`为最终passed，session `17709`退出0。之前session `23241`的保存重开失败已保留为`07_ai_installed_20260929-reopen-failed.*`；安装与AI执行当时已通过，但该旧JSON总体仍为failed。

最终安装包：`dist/PymolPlugins-0.11.0.zip`，**459007字节**。

SHA256：`66126cc3bae9c50c8816a25a87312c5ad6eddf2f9b6f168a98eadbb22147670a`。

使用Plugin Manager安装该ZIP，升级后重启PyMOL。本轮仅在自动清理的Windows临时目录安装；用户当前安装不变。旧0.10.0包和149项接管基线及后续修改全部保留。

| 用户可见功能 | 最终证据/状态 |
|---|---|
| 全量概览、分层抽样、颜色、reference | 复用600行原生通过证据，未重跑、不重复入口 |
| AI真实provider和Windows安装路径 | test / gpt-6-astra请求及返回model一致，Responses completed；6.743秒；模块来自C:临时安装目录，不是工作树偷换 |
| 多step/多行、占位符和实际执行 | on_open生成4行、Good生成2行；预览和原框再编辑后分别6行/2行。导航到下一真实结构后2399原子cyan/cartoon；保存Good后yellow/cartoon+sticks |
| 明确确认、不自动执行、可编辑 | 生成不改变原框/STATE/原子颜色；拒绝覆盖保留原框；确认填框仍未执行；Apply后只在原导航/保存事件执行 |
| 取消、网络错误、异常结果不污染 | 已派发请求本地取消约0.020秒退出；同provider经临时拒绝代理出现真实网络失败；11类注入坏文档全部拒绝，按钮恢复可用，原框不变 |
| 异步与紧凑界面 | 生成期间Qt心跳223次；紧凑草稿窗口按钮可见且可操作，原生Qt截图保留 |
| 保存和重开 | step_cmd、bucket_cmds在原状态文件和review.override持久化；关闭panel、重置内存、重新导入同表后两个框逐字恢复 |
| 安装包 | Windows Python3.10.16 / PyMOL3.1.3.1，11模块、72+8命令、6菜单和既有工作区检查通过；未修改用户插件目录 |

实际缺陷修复：普通CSV GUI导入使用`restore_saved=False`并以当前设置覆盖历史配置，原先review.override只包含step_cmd，导致标签命令虽存入状态却在新panel普通导入时丢失。将**bucket_cmds加入既有review键、严格类型/模板校验及同一导入覆盖链**，不改变其它历史设置的恢复策略、不建立新持久层。

完整证据位于`output/07_ai_installed_20260929*`：主JSON/log、`-provider.json`（模型原始JSON文字、ID、usage及返回model/status）、`-request.json`（实际prompt/schema）、`-state.json`、`.pse`、`-draft.png`/`-error.png`/`-scene.png`。最终scene是显式原生idle/repaint后的黄色cartoon+sticks画面，已实际查看；不宣称无人值守刷新时序。两份源结构散列保持不变。

凭据检查：现有provider token未出现在新增源码、AI产物（40个文件）和ZIP成员中；代码没有输出认证、HTTP头或服务端错误body。没有更换provider/model、复制密钥、修改全局代理/shell/Codex、SSH身份或Windows安全策略。

复现当前交付包（正常interop无需env前缀）：

```bash
env WSL_INTEROP=/run/WSL/2_interop just test-ai-commands \
  /mnt/d/Softwares/Pymol3.1/python.exe \
  output/07_ai_installed_20260929.json dist/PymolPlugins-0.11.0.zip
```

契约补充：成功step需1–40个单行命令；schema允许空数组仅用来明确表示不支持请求，UI拒绝插入并提示修改需求，不静默改成其它命令。AI支持9个明确控制台命令子集，手写能力不收紧。原生只读参数/选择校验不会证明未来每个候选都存在同样链或比较对象，真正执行错误仍由既有通道显示。

**终态与下一步**：所有测试/打包句柄均已终态，没有子代理、后台科学作业或待恢复监督。当前AI增量无已知阻塞；用户可安装0.11.0使用。Beads仍缺失，本文件继续为唯一权威任务记录。独立未验证边界仍是前述真实鼠标拖动、callback CGO、外部worker/初始SSH OTP/ANVIL、PyMOL2.x；本次不据局部通过宣称它们完成。

## SSH体验优化（2026-09-30，进行中）
负责人线程：01a0f2f7-3536-7d30-a8a7-348c6fe15590。延续本记录；Beads已知路径仍不可用，不另建任务系统。
目标：远端文件成为主要输入时，减少传输及本地重复复制，连接/认证/读取/下载等待可见且可取消；保持用户现有SSH profile、OTP登录程序、远端只读、缓存身份与原生Windows Host边界。
已确认：持久会话已实现；下载先写TemporaryFile再复制到目标，结构还会再次复制进缓存；浏览器等待不区分认证/传输。
实施：主线程负责remote/ssh.py、ssh_worker.py及传输测试；界面worker独占annotation/plugin.py、ssh_widgets.py及必要的现有Host验收脚本。不使用job-runner，不修改SSH身份/全局配置，不覆盖未提交基线。
验收：现有传输契约测试；真实Windows Qt/OpenSSH路径、延迟下的界面心跳/取消；可用时以原profile只读实际数据测量首连、热连接、传输与缓存。控制延迟与真实WAN指标分开。
下一步：实现阶段反馈和流式落盘，再执行既有Host验收；测试/子代理句柄在本节续记。

SSH界面worker 01a0f2ff-a66f-7662-b45e-e24b520d3ec1 已完成，四文件已交付；不留并发写者。传输21项定向测试通过（output/08_ssh_transport_20260930.log）。当前Windows原生SSH验收运行中：exec session 38817；恢复用write_stdin接管此句柄，结果output/08_ssh_ux_20260930.json及同名前缀log/driver.log。尚未宣称GUI或WAN通过。

首轮原生SSH导入成功，但Host脚本把缓存失败后留下的空分片目录误作缓存产物；修正断言仅禁止残留文件。原始失败保留08_ssh_ux_20260930-assertion-failed.*；不修改生产校验。

修正后原生验收运行句柄：exec session 53518；沿用08_ssh_ux_20260930.*，旧38817已失败终态。

第二次启动因父线程断言补丁缩进错误在GUI前失败；缩进已修复，生产代码未改变。日志保留08_ssh_ux_20260930-indent-failed.*。

当前原生验收句柄：exec session 11655；53518已启动失败终态。

原生临时OpenSSH/Windows验收最终passed（08_ssh_ux_20260930.json）；11655已终态。真实x profile WAN只读验收已开始：exec session 30075，09_ssh_real_20260930.json/.log；等待首连最多120秒，仍使用原OTP登录程序与服务器路径。

### SSH真实服务器结果
原生Windows3.1.3.1 + 原x profile/ssh_totp.py，600行/56列表及2399原子实际结构，09_ssh_real_20260930.json passed，session30075终态。首连加首表下载3.455929秒；热连接导入0.411532秒，点击15.129ms；一条连接承载14个非Qt线程SSH操作，输入表/结构/配置hash不变。
表1186447字节→139975传输载荷，减少约88.2%；结构205368→66428字节，减少约67.7%。wire_bytes是应用层压缩载荷，不含SSH/TCP开销，不能推算物理链路带宽。没有做同条件旧版前后吞吐benchmark，不宣称速度提升8.48倍。
计时口径修正：原始log中的auth_seconds实际是轮询首次看到非认证状态的时间，不是精确认证耗时。脚本/JSON已改名first_response_progress_seconds并加注释，原始log保留；没有重连或重跑。首次修正文档命令发生字符串转义错误，未写入或启动安装，修正命令后继续。此次只证明首连总用时3.46秒，不保证未来OTP租期等待恒定。
原生回环验收08_ssh_ux_20260930.json passed：热Next4.859ms、导航取消2.005ms、导入取消0.648ms、目录/TSV/gzip及缓存身份保护。取消等待使用显式远端sleep故障注入，不能当作真实网络故障发生率。
安装包已生成461264字节。下一步只做该ZIP的临时Plugin Manager安装边界，不再重复SSH或全量功能测试。

最终ZIP临时安装验收运行句柄exec session 26682，输出10_ssh_package_install_20260930.json/.log；用户当前插件安装未改动。

### SSH最终交付（已完成）
版本0.12.0：dist/PymolPlugins-0.12.0.zip，461264字节。10_ssh_package_install_20260930.json最终passed；真实Windows Plugin Manager临时安装、模块/菜单/工作区入口通过，用户当前安装未改动。打包日志10_ssh_package_20260930.log；安装日志同JSON前缀。
已完成：阶段/等待/下载大小和速率反馈；既有登录会话复用；256KiB分块轻量压缩（gzip不重压）；原子流式下载；非gzip直接缓存；导航等待取消及保留当前结构；真实输入不变。21项传输测试、Windows SSH定向验收、实际x服务器600行/56列导入、最终ZIP安装均通过。
证据边界：21项单测是本地Python worker协议；08是临时真实OpenSSH+Windows Qt，等待/取消使用显式sleep注入；09是原x/OTP程序与实际远端文件，首连含读表总3.46秒，热连接导入0.412秒。没有把单次实测、压缩比或UI取消按钮耗时等同于持续WAN吞吐、精确认证时长或远端中止时长。大规模批量/长期掉线/OTP租期争用未作长期压力实验；不承诺首次连接每次都同样快。
保留原生首次断言失败、父线程断言缩进启动失败与计时标签修正说明，不把失败算作通过。全局SSH/provider/认证分配规则没有改动，远端源没有改写。
所有本轮测试句柄均终态：93126、36611传输测试passed；38817/53518为已保留的Host失败；11655为最终Host passed；30075真实服务器passed；38877打包成功；26682最终安装passed。界面worker01a0f2ff-a66f-7662-b45e-e24b520d3ec1已完成，无并发写者或待监督作业。
下一步：用户在Plugin > Plugin Manager > Install New Plugin选择0.12.0 ZIP并重启PyMOL，沿用Open from=x和原远端路径。常规复现优先just test-ssh-ux；只在需要重新测量WAN时运行just test-ssh-real /remote/path/scores.tsv x。本轮不再重复已通过的功能或测试。

## 新手上手优化（进行中）
用户追加目标：确保新用户能够很快上手。仍由线程01a0f2f7-3536-7d30-a8a7-348c6fe15590负责，沿用本执行记录。
已观察卡点：README首先讲开发架构/版本史；界面未解释SSH profile/配置属于哪台电脑；新用户没有从选文件到保存的连续提示。
当前实施：主线程在既有Open data页加入三步提示、Quick start及无服务器配置帮助，不强制弹向导、不改全局SSH配置；已有worker01a0f2ff-a66f-7662-b45e-e24b520d3ec1只编辑README/现有walkthrough/INSTALL_TEXT。双方写边界不重叠。
验收目标：使用全新隔离设置，从最终临时安装包打开Quick start、读取空SSH列表帮助、选取CSV/结构列并完成第一次保存；验证紧凑布局。新手人工可用性/独立上手时间尚未实测，不拿自动化代替真人。
下一步：完成主路径的安装包原生验收，保留既有SSH性能结果，不重连真实服务器或重跑无关批处理功能。

新手文档工作包的最终报告与实际锚点不一致：三处说明已有初稿；主线程保留并补齐0.12.1、Quick start/First SSH connection入口和统一示例。最终ZIP首次使用验收运行中：exec session 46729，output/11_onboarding_20260930.json/.log，打包日志11_onboarding_package_20260930.log。

首次新手验收已导入3个结构，但测试沿用QMainWindow的提前close顺序，真实PyMOLQtGUI关闭使JSON未落盘，不计passed；日志保留11_onboarding_20260930-early-close.log。现仅修正测试退出顺序，先落结果再cmd.quit；安装包代码不变。

新手验收恢复句柄：exec session 95938；46729已失败终态。

### 新手上手最终交付（已完成，0.12.1）
结果：Open data内三步提示；不强制弹出的Quick start；无SSH服务器列表时的行动提示；Connections内First SSH connection说明；默认选项/结构路径列/本地保存解释。README、现有walkthrough和包内INSTALL.md均把首次操作放在高级说明前，示例与界面一致。
安装包：dist/PymolPlugins-0.12.1.zip，463561字节，SHA-256 8afc8d086b7a2c94608d373fd09c8e77d8e40af7dbd5c209750fa398b8080c3c。实际包内运行，不是工作树替代。
最终证据：output/11_onboarding_20260930.json passed；Windows PyMOL3.1.3.1。隔离历史、映射、缓存与工作区设置，空SSH配置不被写入；Quick start通过真实按钮打开。3行CSV/3原子结构首次导入，普通标签点击仅草稿，第一次Save and next已写入并到下一条；4份输入文件内容不变；760×560布局主按钮完整可见。
同名前缀-open.png/-help.png已实际查看：三步入口、配置提示、帮助和主按钮无裁切；-review.png保留首次保存后的界面。日志11_onboarding_20260930.log；打包日志11_onboarding_package_20260930.log。早关窗口的失败日志保留，不计为通过。没有重复跑WAN、OTP或旧批处理套件。
边界：这是干净安装/设置下的原生自动化首次使用证明，不是真实新用户上手时长或易用性研究；SSH账号、主机信任、密钥/OTP仍需用户已有环境/管理员准备。没有改用户当前安装和全局SSH设置。
状态：46729早关窗口未落JSON，已失败终态；95938最终passed。文档worker已终态，父线程已接管并完成必要补齐，无并发写者/待监督作业。
下一步：用户通过Plugin Manager安装0.12.1并重启，从三步入口或Quick start开始。复现命令just test-onboarding；没有新的SSH性能问题时不重复首连实验。若开展真人测试，应让首次使用者只看界面完成同一路径，再根据具体卡点调整，不承诺未经计时的“几分钟”。

连续审阅升级已写入：继续上次、later状态/路线、分数解释、handoff导出、错误恢复链接及保存后清除later。静态与定向单测运行中：exec session 54772；需先看结果再做Windows Host。

0.12.2连续审阅包已生成；原生Windows安装验收运行中，exec session 3790，输出12_continuous_review_20261001.json/driver.log。

首次连续审阅验收命令把output路径错传成host_python，退出127，PyMOL未启动；原始日志保留。按justfile参数顺序修正后重跑。

连续审阅修正后Windows验收句柄exec session 18061；3790参数错误已终态。

首轮连续审阅Host验收的新增断言要求默认无显示分数时出现Relative rank，fixture没有选择score列，故在功能前提不满足处失败；原始JSON保留，生产代码不因该断言失败。移除过窄断言，保留分数卡片本身的单元/既有UI边界。

连续审阅最终Host验收运行句柄exec session 83181；旧断言失败已保留。

修复了真实Host发现的owner边界：review_controller调用模块级清理later状态，之前错误调用Panel方法导致Save and next不前进。新包验收句柄exec session 71720。

连续审阅验收加入handoff导出、later状态不污染用户annotations；最终Host句柄exec session 63194。

第二次连续审阅Host验收发现handoff方法错误使用Panel不存在的_active_source_reference；应调用模块函数。失败JSON保留，生产包修正后重打。

handoff owner修正后最终Host验收句柄exec session 1869；63194已失败终态。

最终Host验收补充Continue last review按钮状态：exec session 32618。

连续审阅最终验证：21项相关单测、py_compile、diff-check及output/12 JSON状态通过；混合90项测试仍受既有FakeCmd.keyword环境问题影响，未作为本次通过依据。exec session 99649。

## 连续审阅升级（2026-10-01，已完成）
用户追加要求：把最大化提升体验的方案加入产品。此次选择不改变质量标签含义的连续审阅路线，复用唯一STATE、annotation sidecar、已有恢复位置、评分指导、SSH缓存、导出和错误通道。
已交付：
- Data > Continue last review 和Open data页按钮：显式恢复最近数据源、筛选/抽样配置及候选位置，不启动时自动连接远端。
- 独立本地工作流状态 __pymol_review_status__=later；More中可标记/清除，Next unreviewed跳过later，Next marked for later集中回看；保存正式质量标签后自动清除later。状态不进入质量字段和CSV质量标签导出。
- Export review handoff…：原子写JSON摘要，包含来源、筛选、抽样、当前位置、进度、标签和later项，不复制结构文件，显式写明边界。
- ScoreFooter解释相对排名只在当前审阅集内成立，绝对规则只有配置/CSV提供时显示；路径/SSH错误增加直接Open data / Open Connections恢复链接。
- README、现有walkthrough、包内INSTALL_TEXT及Implementation Status更新；版本0.12.2。
最终包：dist/PymolPlugins-0.12.2.zip，SHA-256 4687661b26b51f4387306766cf65c014af5d23c2fbadb8753825835ca2737530。
证据：output/12_continuous_review_20261001.json passed；Windows PyMOL3.1.3.1，临时Plugin Manager安装，隔离历史/映射/缓存/工作区设置。首次3行CSV/3原子结构导入、标签草稿、Save and next、Continue入口可用、later路线、handoff导出、隐藏状态不污染annotations、4份源文件不变、760x560布局均通过。对应driver/package日志保留。
验证：21项test_review_guidance+test_annotation_store通过；目标模块py_compile、git diff --check通过。一次混合90项测试未通过是既有FakeCmd缺少keyword属性，在导入targets.commands时失败，未进入本次功能；不把它当作本次回归，也未改测试假对象。
已保留失败：首次测试命令参数顺序错误；score hint断言在没有选择score列的fixture中不成立；handoff第一次owner边界错误；均已修正，不计通过。
边界：本轮实现了恢复、路线、解释和交接；没有把“断线后离线继续最新远端版本”、自动相机策略或真人上手时长研究伪装成完成。长期断线/OTP争用/大规模交接仍未验证。用户当前安装和远端源未改。
状态：本轮所有exec句柄终态，无子代理/后台科学任务。下一步用户安装0.12.2并重启；若继续体验优化，优先做真实用户观察后再决定离线模式或结构视角自动化。

补充实际点击Continue last review并恢复首候选：exec session 73214。

第三次连续审阅Host验收只因测试脚本在Continue断言前使用未赋值first而失败；两次真实加载已完成，生产包未失败。修正测试变量顺序后重跑。

Continue实际点击测试变量修正后句柄exec session 19381。

补充：最终Host验收随后实际点击 Continue last review，第二次加载后恢复首候选并保持3原子；output/12_continuous_review_20261001.json仍为最终passed。此前三次失败均为验收脚本自身参数/断言/变量错误，已保留并不计入功能失败。

## SSH ready后卡在认证（2026-10-02，已完成）
用户报告：外部终端/SSH窗口已出现worker ready JSON，插件仍停在authenticate。
原先验收都由python.exe启动，没有覆盖桌面pythonw.exe。Windows同环境最小继承句柄探针：外层python.exe→subprocess.call子进程输出12345，父管道收到；外层pythonw.exe→同一子进程退出0但父管道输出为空。现有运行中的PyMOL进程来自pythonw.exe；未操作/结束它。wrapper源码run_wrapper使用subprocess.call继承stdio，与复现路径一致。
已直接修复SshClient.command：Python登录脚本在pythonw宿主下使用同目录python.exe；保留原wrapper、profile、认证参数和OTP规则；CREATE_NO_WINDOW不变。缺少同目录console解释器时明确报错，不自动改用其他SSH客户端或Python环境。
扩展既有Host入口允许pythonw.exe启动GUI，临时目录探针使用同安装console Python。输出记录宿主解释器与登录命令前缀（不含远端脚本文本/认证凭据）。
下一步：定向协议测试、pythonw启动的真实OpenSSH/Qt，以及原x profile只读验收；不重跑连续审阅或其他未受影响模块。

Desktop pythonw/OpenSSH/Qt验收运行中：exec session 98874，output/13_ssh_ready_gui_20261002.json及同名前缀log。恢复用write_stdin接管，不重复提交。

Desktop pythonw回环OpenSSH/Qt最终passed（13_ssh_ready_gui_20261002.json），宿主pythonw→同目录python.exe→原wrapper，14次非Qt线程操作和取消通过。现原x只读WAN验收：exec session 17744，13_ssh_ready_real_20261002.json/.log；不修改当前用户PyMOL进程。

原x profile / pythonw真实WAN验收passed，JSON已确认；17744终态。最终0.12.3 ZIP已构建，当前仅做pythonw宿主的临时Plugin Manager安装边界：exec session 79645，13_ssh_ready_install_20261002.json/.log。


### SSH桌面入口修复最终交付（0.12.3，已完成）
根因：Windows桌面pythonw.exe启动原Python OTP wrapper时，wrapper内subprocess.call没有把SSH标准输出正确送回插件管道，外部窗口虽然出现ready，插件仍在等待。之前console Python验收遗漏了桌面宿主边界。现仅把此Python登录脚本的解释器选为同安装目录python.exe，保持原wrapper、x profile、OTP策略和CREATE_NO_WINDOW；缺少该解释器时给出明确错误，不换环境或认证客户端。

| 验证边界 | 最终结果与证据 |
| --- | --- |
| 定向协议与解释器选择回归 | 23 passed；output/13_ssh_ready_unit_20261002.log |
| pythonw真实Windows Qt + 临时OpenSSH | passed；output/13_ssh_ready_gui_20261002.json；14次非Qt线程SSH操作，目录、缓存、取消通过 |
| pythonw + 原x/OTP wrapper + 实际远端文件 | passed；output/13_ssh_ready_real_20261002.json；600行、56列、2399原子，复用1条连接 |
| 最终ZIP临时Plugin Manager安装 | passed；output/13_ssh_ready_install_20261002.json；Windows PyMOL 3.1.3.1，插件0.12.3，模块、菜单、工作区和SSH入口通过 |

单次实际服务器测量：首连加读表1.597213秒，热结构下载0.0428855秒，GUI热导入0.407005秒。不是纯认证时长，不构成旧版前后速度对照，也不保证每次OTP或网络等待恒定。
交付包：dist/PymolPlugins-0.12.3.zip，466128字节；SHA-256 3ad1ecfefbfa28d119bdae71962863f3cce60e26a108d5bb76d246322aba9792。just test-ssh-ux / test-ssh-real的默认宿主已改为pythonw，以持续覆盖桌面入口。
状态：98874、17744、79645均已完成并有passed JSON；无待监督作业或并发写者。PyMOLWin启动身份尝试未生成结果，不作为通过证据。用户当前安装、运行中的PyMOL、SSH配置、wrapper、凭据和远端源均未修改。
下一步：用户在Plugin Manager安装0.12.3 ZIP，完全退出并重启PyMOL后沿用原x和远端路径。所需边界已验证，不再重跑WAN或无关测试；如仍出现卡住，应采集新版会话状态和日志定位，不以旧进程结果判定新版。


## 页面减字（2026-10-02，已完成）
用户再次明确：让UI设计自己说话，不用常驻小字补救交互。已按原生截图确认打开页三步/SSH说明、审阅图例和分数免责声明占用空间。当前修改移除这些常驻文字；帮助入口为可键盘聚焦且有无障碍名称的?；缺SSH配置显示Set up SSH动作，登录程序折叠；Later改成状态标记，筛选计数仅有子集时出现。错误、等待进度、取消和未保存提示保留。
验证契约：最终0.12.4 ZIP临时安装，在Windows Qt从干净设置完成首次打开和保存，实际操作SSH高级展开/帮助/分数帮助/暂缓标记/筛选计数，并查看760×560原生截图。不会重连WAN或改SSH传输。沿用tests/host/plugin_package_smoke.py与just test-onboarding，不建立新任务或脚本。下一步打包并执行该边界。

最终包原生UI验收已启动：exec session 70142；结果output/14_quiet_ui_20261002.json，同名前缀.log，打包日志14_quiet_ui_package_20261002.log。恢复用write_stdin接管，禁止重复提交。

首轮70142行为验收passed，但原生截图显示SSH设置/帮助均异常扩大至3002×1401；不作为完整UI交付。打开/审阅760×560正常。正在定位弹窗几何，保留初轮结果后修正复验；不重跑WAN。

弹窗几何定位执行句柄3982；同一验收入口增加窗口状态/minimum/sizeHint输出；首轮异常截图与JSON保留为14_quiet_ui_20261002-dialog-expanded*。

弹窗定位结果：3982终态，显示前620×240，显示后3002×1401；Qt windowState=0、minimum=282×198、sizeHint=374×207，故不是长文字撑开的最小尺寸。两次独立Qt探针31362/95573退出0但没有可读结果，不用它们宣称根因。停止重复独立探针；保留已知事实，不猜测全局窗口管理原因。选择局部UI方案：设置和帮助使用内容驱动固定尺寸，Advanced展开随内容调整；不改变全局窗口策略或PyMOL主窗口。增加真实弹窗尺寸验收。

内容驱动弹窗最终验收句柄80727，同一14系列结果；恢复用write_stdin。


### 页面减字最终交付（0.12.4）
结果：移除打开页三步文字/SSH说明、审阅常驻颜色图例、分数免责声明和暂缓操作的重复成功句。打开页可见标签只有Open from/Path；帮助移至右上角?。无历史时隐藏恢复/最近入口；无SSH profile时直接给Set up SSH操作；位置选择有电脑/网络图标，路径占位随位置变化。高级登录程序折叠。Later用标题旁状态标记；无筛选时不重复统计全集，筛选/抽样时保留范围；分数绝对目标用状态色块，相对排名用中性色和正常字号，解释按?打开。真实错误、下载进度/取消和未保存提示未隐藏。

最终验收：output/14_quiet_ui_20261002.json passed，Windows pythonw/PyMOL 3.1.3.1，从最终ZIP经Plugin Manager临时安装。干净设置、无SSH列表配置入口、Advanced实际展开、Quick start和分数帮助实际点击、3行表/3原子结构首次加载、草稿/Save and next、Later/恢复/交接、Filtered 1 / 3及清除后隐藏范围均通过，4个输入文件未改。
截图：同名前缀-open.png、-review.png（760×560）、-connections.png（576×207）、-help.png（586×430）已查看；主要操作无裁切。首轮弹窗异常图保留为-dialog-expanded*，不是最终展示结果；正常字号没有通过缩小字体实现。独立探针无输出不算证据。弹窗外部扩大原因未确证，内容驱动局部尺寸方案已在目标宿主验证，不改全局窗口配置。
交付：dist/PymolPlugins-0.12.4.zip，466334字节，SHA-256 d6ffbe6b5425c95f65f636b427ff7529df372a28159bab6b7dbe829e8e22368c。复现：WSL_INTEROP=/run/WSL/2_interop just test-onboarding dist/PymolPlugins-0.12.4.zip /mnt/d/Softwares/Pymol3.1/pythonw.exe output/14_quiet_ui_20261002.json；使用现有Windows PyMOL环境，无WAN/凭据要求。GUI为真实自动化验收，不是真人上手研究。
状态：70142/3982/31362/95573/80727均终态，无并发写者或待监督任务。用户当前插件安装、现有PyMOL进程、SSH配置与远端数据未改。下一步用户安装0.12.4并完全退出/重启；后续UI增量先用层级、控件、状态表达，仅必要解释按需展现，不再追加默认小字。

## AI配置、查看预设与路径查找（2026-10-02，已完成）
用户要求：AI draft可配置API地址/API Key/模型，修复空命令泛化失败；补PPI/PLI/oligomer/membrane等常见预设；路径fzf式模糊选择，切目录后清筛选，并修同路径实际缺陷。范围不包括改用户全局provider/SSH配置或自动执行AI草稿。
已确认根因：AI UI/transport写死test/gpt-6-astra；当前白名单仅9命令，prompt明确指示不支持则返回空数组，parser又要求1–40条而只报泛化错误。当前实际Codex仍是test/gpt-6-astra/Responses。
策略：原Codex默认路线不变，增加显式Custom API选择及本地保存；新增可编辑、无需API的查看预设，仍走预览/确认/Apply边界；扩展受控常见PyMOL查看命令与准确错误，不给任意Python/shell执行开口。默认UI保持安静，设置按需打开。
分工：worker01a0fce1-aed1-7412-9796-afe9fc9b16d4独占ssh_widgets/history_store及两项路径验收；主线程AI UI/命令校验/presets/集成/交付；provider worker独占ai_provider.py与新定向provider测试。所有输出在既有output/15_*，不覆盖13/14记录。下一步实现后执行最终ZIP的Windows原生操作与真实provider验证，保留证据边界。

路径worker已完成：15_path_ux_20261002.json passed（Windows3.1.3.1 + 临时真实OpenSSH，模糊排序/键盘/成功切目录清筛选/失败回滚，6份源文件不变）；6项unittest通过，pytest不在环境中未安装。provider worker01a0fce4-0e49-7072-ac11-428925c29ae9已完成：9项本地HTTP transport测试通过，含两种API/models/describe/脱敏；Windows DPAPI与真实API由父Host验收接管。无并发写者。主线程命令模板6项通过；一次prompt字符串拼接语法错误已修正。
公开RCSB条目下载83859三次连接超时已终态，不用模拟数据替代；同条目改用PDBe镜像，下载句柄83621。7项预设使用1BRS/1A4W/2RH1及既有真实RNA复合物。最终验收计划读取既有授权test/gpt-6-astra连接，在隔离临时设置里用显式Custom API原生Host路线完成真实生成，不改变Codex；Key只经进程管道和内存、不进日志。随后验证默认Codex取消/错误边界及7预设实际结构显示。此前同模型生成耗时6.74秒；本轮预计1–3分钟，单请求上限120秒。

PDBe下载83621也代理超时；检查发现代理指向172.26.128.1:7897，仅下载命令使用curl --noproxy '*'后RCSB相同3条目成功（19440终态，共1097469字节）。全局代理未改，fetch-preset-inputs记录实际可重现命令。已生成0.13.0包，下一步最终Host运行。

最终0.13.0 Host验收句柄40597；output/15_command_ux_20261002.json/.log，package日志15_command_ux_package_20261002.log。用write_stdin接管，勿重复提交。

40597已失败终态，但失败前AI合同已完成：最终包安装、Key DPAPI roundtrip/非明文保存、31项模型列表、自定义原生API原test/gpt-6-astra Responses实际生成8.456868秒、2399原子真实打开/保存触发执行、set cartoon_transparency=0.2、预览保留/手工空行注释/确认拒绝/取消/网络失败/持久重开均通过。原始15_command_ux_20261002.json保留整体failed，失败位于后续预设切表：Unknown reference column: reference。
根因是GUI新表导入传入上一数据集完整config，只清score_guidance未清reference_column；修正新源分支清reference_column，显式新表review配置及恢复历史仍优先。该实际缺陷纳入本轮；保留既有真实AI证据，脚本加--stage presets继续预设和切表边界，不重复收费API。

切表修复后仅预设阶段句柄9301，output/15_viewing_presets_20261002.json/.log。

9301预设/切表阶段passed：7项预设在原生Windows真实结构执行，1BRS=5153原子、1A4W=2439、2RH1=3804、RNA复合物=2399；所有坐标max error=0，切新表reference列已清。API配置/草稿截图已看，紧凑且无常驻教程。实际PLI截图显示蛋白cartoon挡住配体；这影响预设查看目标，已把受体cartoon设透明、区分口袋/配体stick半径并面向口袋。仅复验改动的PLI及最终ZIP，不重跑AI或其余6项预设；脚本新增--preset限定。

PLI可见性修正/最终ZIP验收句柄27682，output/15_pli_view_20261002.json/.log。


### AI配置、预设、路径UX最终交付（0.13.0）

| 交付 | 行为与证据 |
| --- | --- |
| AI自定义连接 | AI settings内可切Codex/Custom API，编辑URL、Key、模型与API格式，Refresh模型列表；真实Host读到31模型，原test/gpt-6-astra的Responses通过显式Custom API原生Windows路线生成8.456868秒，2399原子正确执行两个触发步骤 |
| 密钥保存 | 默认仅会话内；可选Windows DPAPI当前用户加密。Host证明加密Key重开恢复、设置文件无明文Key、取消Remember后移除密文。真实Key只在管道/内存，不在prompt、request产物或dataset状态 |
| AI失败修复 | 白名单9→14命令，新增对象范围set/spectrum及元素配色；无命令时反馈具体step和provider note，不再笼统要求1–40条。失败重试保留已有草稿；插入允许手写空行/注释，仍检查命令且必须确认，不生成即执行 |
| 7个查看预设 | Cartoon、PPI、PLI、oligomer、membrane、DNA/RNA、surface；不依赖API，仍为可编辑草稿。真实1BRS/1A4W/2RH1/RNA复合物全部执行，所有坐标max error=0；PLI按实际截图修复cartoon遮挡并复验 |
| 路径选择 | 紧凑/词边界模糊排序，搜索框↑↓/Enter，成功进入父/子目录清筛选；失败回滚原路径、列表和筛选并恢复按钮，不假装进入新目录 |
| 同路径实际缺陷 | 新CSV清理上一源reference列，防止Unknown reference column；显式新源review配置及历史恢复仍保留 |

证据分层：
- 6项command_templates + 9项provider本地HTTP + 6项fuzzy定向测试通过。Chat Completions为真实本地HTTP合同测试，不冒充外部模型实测；Responses外部原provider已实际成功。
- output/15_path_ux_20261002.json passed：Windows PyMOL3.1.3.1与临时真实OpenSSH，键盘/筛选清空/失败回滚和6个远端源文件不变。
- output/15_command_ux_20261002.json保留首次整跑failed：AI配置/真实生成/真实执行/取消/拒绝覆盖/重开均已完成，失败在随后新表导入遗留reference列；不涂改原始失败。修复后不重复API。
- output/15_viewing_presets_20261002.json passed：安装包、7预设及新源reference清理。1BRS 5153原子，1A4W 2439，2RH1 3804，RNA复合物2399。
- output/15_pli_view_20261002.json passed：最终ZIP安装与PLI可见性修正。原生API配置/560×520草稿、PPI/PLI/membrane实际截图已看，最终PLI配体清晰可见。其余6预设未改，不重复验证。

最终包：dist/PymolPlugins-0.13.0.zip，473115字节；SHA-256 877d7ab557db736b7c3456b0a10b1ce68a6d82adc60a96dbfc3c554d41838fba。真实结构下载来源RCSB（仅下载命令直连，原代理超时记录保留），输入文件未修改。
复现：just fetch-preset-inputs；WSL_INTEROP=/run/WSL/2_interop just test-ai-commands /mnt/d/Softwares/Pymol3.1/pythonw.exe output/15_command_ux_rerun.json dist/PymolPlugins-0.13.0.zip output all。只复验预设可将all改presets，最后可加pli限定单项；路径用just test-ssh-ux /mnt/d/Softwares/Pymol3.1/pythonw.exe output/15_path_ux_rerun.json。不会用成功预设阶段假装再次跑过实际API。
状态：40597为首轮后段失败；9301、27682为修复后定向passed；83859/83621代理下载失败、19440直连下载成功均终态。两个worker完成，无并发写者/待监督任务。没有替换用户当前插件安装、结束其PyMOL、修改全局Codex/SSH/代理配置。尚未收到用户原始失败prompt，不能宣称已逐字复验该输入；已证明对应空结果处理及扩展命令真实路径。
下一步：用户安装0.13.0并完全重启，从Settings > Automation进入AI settings或Viewing presets。按当前小缺陷发现结果交付，不以更多说明文字替代交互，不再追加无关测试。


## 筛选分组与产物prompt（2026-10-02，已完成）
用户要求生成prompt与插件同时支持由预定义筛选器快速分类，并在Filter页人工编辑，保持现有格式相容。选择可重叠命名视图，不写人工质量标签、不改结构/CSV；review.json可选filter_presets(name,expression,sort_column,sort_descending)，旧配置无此字段不变，人工编辑通过现有review.override.json原子覆盖列表。未知列/非法表达式在提交前拒绝。主线程独占本轮代码/文档，沿用当前任务记录，无新委派/科学运行。
已实现数据载入/持久化/换源清理、Filter分组下拉与Save/Delete、主页面Filter箭头一键切换；复用现有表达式引擎与唯一STATE导航/抽样。完整可复制prompt在既有walkthrough末尾，包含hardlink/rawscore/代表label/4–6指标/阈值依据/重叠与缺失及不覆盖人工override。未收到具体运行产物路径，本轮不生成或修改科学结果。
验收计划：纯filter/review合同测试，然后最终0.14.0 ZIP在Windows PyMOL从旧配置导入到生成分组、重叠/空组、编辑新增删除、人工override重开、换源隔离和源文件不变；保留实际紧凑Filter截图。预计原生阶段约1分钟，不重跑AI/WAN。

0.14.0原生分组验收句柄85344；output/16_filter_groups_20261002.json/.log，单测日志16_filter_groups_unit_20261002.log。恢复用write_stdin接管。

85344终态，7项过滤合同单测passed；Host旧配置/新组加载成功后，下拉菜单未打开处超时。保留-menu-failed记录。测试改为等待窗口激活/布局事件后点真实箭头，并在失败时记录子控件几何；不以直接调用菜单替代。补足当前组名称/菜单勾选反馈，隐藏默认冗余Candidates标题防止窄窗截断。

分组菜单/最终包复验句柄71259；同一16系列JSON/log。

菜单事件循环修正后验收句柄94128；生产包未变，同一16系列JSON/log。


### 筛选分组与prompt最终交付（0.14.0）
结果：新增可选filter_presets数组，组名/表达式与可选排序；加载自动显示在Filter下拉箭头中，选中组名直接显示在主界面，菜单勾选活动组。Filter页面可以编辑、同名保存、新建和删除；错误条件留在编辑页而不提交。分组默认可重叠，空组显示0，不自动标注，不复制/移动结构；使用原筛选/抽样和唯一导航STATE。原review.json不改，人的修改只写review.override.json。源切换清理旧分组，旧bundle无新字段兼容；新字段需0.14.0+。

最终证据：output/16_filter_groups_20261002.json passed，真实Windows pythonw/PyMOL3.1.3.1，从最终ZIP临时Plugin Manager安装。3组导入，高组2行且排序正确、重叠1行、空组0行；编辑后1行；新增/删除、override关闭重开、非法表达式保留原视图、旧配置/换源隔离、无人工标注写入全部通过。5个生成源文件和4个旧输入文件未改。-filters.png、-group-menu.png、-group.png已查看：编辑器/分组菜单紧凑，主界面显示当前组与Filtered 1 / 3，未加常驻教程。group截图中scores仍显示短暂Updating，未拿它宣称分数异步渲染已完成；本次验收是分组与导航边界。
7项纯filter/review合同单测passed（16_filter_groups_unit_20261002.log）。prompt示例已直接交给validate_review_config/validate_review_columns/format_commands验证：字段有效、2个示例表达式合法、step_cmd解析为4行而非字面量反斜线n。完整可复制prompt位于docs/structure_annotator_csv_walkthrough.md“产物整理prompt（含筛选分组）”，README与包内安装说明更新。

最终包：dist/PymolPlugins-0.14.0.zip，475252字节，SHA-256 250fa2e16dc414fb2908a3393dc2a2e7c6fbe1f56001f00d20678a0f7051eb91。复现：WSL_INTEROP=/run/WSL/2_interop just test-onboarding dist/PymolPlugins-0.14.0.zip /mnt/d/Softwares/Pymol3.1/pythonw.exe output/16_filter_groups_rerun.json。没有重新跑AI请求、WAN或科学打分。
失败可见：85344下拉点击超时终态，保留-menu-failed；71259因测试未在同步原生菜单循环中安排选择而阻塞，精确核对命令行后只停止owned PID9672，句柄终态255；保留-menu-blocked日志。修正仅验收脚本定时真实菜单选择后94128最终passed。该停止导致的已知临时安装目录已清理。所有句柄终态，无后台任务或并发写者；没有触碰用户当前安装/运行中的PyMOL、SSH或provider配置。
下一步：安装0.14.0并完全重启；后续产物使用更新prompt。当前未提供实际运行目录/分数文件，因此只交付插件与生成契约，不假装已整理或分组某次实际运行产物。


## Step自动加载reference（2026-10-13，执行中）

用户明确要求导入candidate及切换step自动加载reference，不能只配列并要求点击Compare reference。父线程01a13357-0dcc-72b0-a4ff-b66b54c3d945在Linux节点的干净独立clone `/xcf26/zpzeng/gitrepo/pymol_scripts`、分支 `fix/automatic-step-reference` 实施；原其他机器工作树未修改。唯一执行记录继续使用本文，不创建重复任务库。

路线：已有reference_column且当前行非空时默认自动加载，复用异步路径映射/缓存/SSH准备和原橙色reference；只移动reference作显示对齐，candidate坐标不变。当前candidate手动隐藏需保持到下一candidate；缺失/失败/取消不得循环重试或错误挂接旧reference。无reference配置的源保持candidate-only。

写入归属：子代理automatic_reference_loading负责plugin.py、定向annotation回归和walkthrough；父代理负责版本、真实Linux Qt/PyMOL验收、打包及记录。当前主机已有PyMOL和PyQt5，暂无Windows Host，不能把Linux证据当Windows验收。下一步：收取局部修改、执行真实双object及切换验收，构建可安装ZIP并交付review分支。

### 自动reference最终交付（0.14.1）

已有reference_column时导入及切换step默认异步加载参考；候选提交后Qt主线程加载/对齐橙色reference。换步清理和取消旧请求，数据代次、路径、候选和任务epoch拒绝迟到结果；当前步Hide、失败或取消不循环重试，Compare可手动重试。无列/空值保持仅候选。实测退出暴露HistoryCompletionFilter在非按键销毁事件访问已删completer，已将popup访问限于KeyPress，未添加泛化异常吞噬。

最终ZIP：dist/PymolPlugins-0.14.1.zip。最终真实验收 output/17_automatic_reference_linux_20261013.json 为passed，原生进程退出0：Linux PyMOL2.6.2/PyQt5/offscreen，从ZIP解包加载实际代码；真实cal53 0015/0017候选自动对应两份原簇最佳参考，首开/隐藏保持/手动重开/下一步/空值清理通过。候选4795/4816原子、参考4803/4813原子，候选坐标最大误差0，四份源文件hash未变。没有渲染像素或Windows安装验收，不能将其当作Windows已通过。

复现：rtk proxy env QT_QPA_PLATFORM=offscreen /xcf26/zpzeng/miniconda/bin/python tests/host/02_automatic_reference_linux.py /xcf26/zpzeng/project_run_log/xyzhang_task/008_cal53_pore_blocking_binder/publish/001_cal53a19_hotspot_binder/03_physical_interface_objective/14_clustered_iptm_review/sum_scores.csv --package dist/PymolPlugins-0.14.1.zip --output output/17_automatic_reference_linux_20261013.json

新增automatic_reference回归7项通过；packaging/layout 7项通过。扩展旧core/review_import存在既有FakeQt setVisible缺失和FakeCmd未执行命令完成标记问题，未将这些失败计为通过、未扩大修复。首次和第二次真实检查功能均passed但退出SIGABRT；首次证据保存在output/17_automatic_reference_linux_teardown_failed_20261013.json。最终修复后同路径检查退出0，所有本轮句柄终态、子代理完成，无后台验收任务。实际科学PDB/CSV评分和ty/ty3优化任务未修改。

源码在独立codex/automatic-step-reference分支交付草稿PR，不合并main或发布Release。用户下一步安装ZIP并完全重启，再打开现有sum_scores.csv。尚未更新用户Windows安装。

## 自动发布与插件更新（2026-10-13，执行中）

用户授权合并PR并构建自动发布及插件自更新，随后明确选择仓库直接公开；仓库已设public。沿用本执行记录：父负责GitHub合并、main版本触发Release工作流、版本0.15.0、集成验证与发布；automatic_reference_loading负责公开Release自更新纯逻辑/Qt界面与定向测试。默认后台检查、自动安装可由用户开启；只安装校验成功的正式包，保留备份，提示重启，不热替换内存模块。现有用户配置与科学数据不改。

PR #2已squash merge至2c0379d8d5e7fb803e9615970396f4e1ce880788。GraphQL首次报权限错误，REST同一head正常合并；未改分支保护。新工作在codex/automatic-release-updates。0.15.0打包/布局/更新引擎14项通过，真实Linux Qt Updates窗口与默认设置通过。仅替换引擎的模拟IO回归不冒充实际GitHub下载；后续在main自动Release成功后用 tests/host/03_release_update_linux.py 临时安装真实0.14.1包，下载公开0.15.0并在新进程Qt确认重启生效。Windows仍未实测。
