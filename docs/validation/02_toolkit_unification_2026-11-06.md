# 插件统一审查与整合

负责人：本次 Codex 主会话。状态：本地整合、真实 Host 验收与候选包交付完成（2026-11-06）；尚未公开发布或替换用户日常安装。

## 目标与验收

- 当前 0.16.0 的统一入口可发现并使用绘图、interaction 和 group 功能。
- interaction 默认零新增命名 selection，不删除或改变用户原有 selection；验证链间、跨对象同链、配体、空选择、状态与重复输出。
- canonical 命令按实际参数位置补全，并覆盖命名参数、场景对象/selection/group、合法枚举和路径。
- Structure Annotator 支持候选派生产物作为完整 group 切换，也支持将已加载 group 当作候选。固定参考复用，外部对象不成为插件可删除资产。
- 真实 Windows PyMOL 验证行为、group切换性能和绘图效果；不以 fake-cmd 单测替代。

## 基线与范围

原目录 `D:/SyncPath/GitRepo/PymolPlugins` 停留在 2025-12-30 的 `2dd4d08`，有既有未提交修改。本次保留原目录，使用独立工作树 `D:/SyncPath/GitRepo/PymolPlugins-unification`，分支 `codex/unify-tools`，基于 upstream main `a883fe9`。本机安装包为 0.16.0，真实运行时 `D:/Softwares/Pymol3.1/python.exe`，PyMOL 3.1.3.1。

现有 CONTEXT.md 已记录本机无可用 Beads，采用项目既有 `docs/validation/` 执行记录方式。本文件是本次 outcome 的唯一状态和证据入口。

## 已核实的设计依据

- 当前版有 `pp_load`、manifest 和统一帮助，旧目录“没有统一入口”的发现不适用于当前版。
- interaction `_render_partners` 默认创建 partner selections；需要在显示层改为表达式引用。
- group 基础能力仍是兼容入口；现有 canonical navigation 已能处理group，补足发现与操作接口时复用它。
- Annotator 的固定 review objects 已缓存复用；候选并非每一步都重载。反复 `_object_exists` 会重复获取全部对象列表，需实际计量。
- PyMOLish 参考源：https://github.com/adrian-jasinski/pymolish ，检查时 commit 为 `aee3666990c0937d70f0706ad04a268efc8fe84b`。参考 group 导航/发现习惯，不直接引入另一套插件或其全套依赖。
- 用户指定 SSH `debug:~/.agents/skills/04_external_links/pymol/scripts/acceptance_styles.py`，该文件最近提交 `0a68a9d`（2026-09-02）。复用样式参数，转入本项目可撤销 registry；不照搬创建 named selections 的 review recipes。

## 审查发现与修复

| 问题 | 证据与影响 | 已落实处理 |
| --- | --- | --- |
| P1：替换分组递归删除源结构 | `group_objects_by_name(...replace=1)` 原来调用 `cmd.delete(group)`；真实 PyMOL 会连成员一起删除 | 使用 `group(action="empty")` 后重组，保留源对象。真实测试保留两个结构 |
| P1：新创建的组可能装有借用对象 | 仅按新对象名差集认领，再递归删除组，仍可能误删组内原有用户对象 | 删除自有容器前清空成员；只删除明确拥有的叶对象。失败回滚及换源均检查 foreign 对象保留 |
| P2：interaction 产生多余 selection | `_render_partners` 为每个非 ligand partner 调用 select；measurement 还会自动隐藏原 selection | partner 保留为表达式，默认零新增 selection；在 renderer 边界恢复已有 selection 启用状态 |
| P2：候选派生产物没有统一生命周期 | 接触线、配体显示对象与候选单独启停；已加载组不属于文件输入 | 默认候选 bundle；新增场景 group 审阅，复用 STATE/NavigationSession/RowAnnotationStore，固定对象复用 |
| P2：跨候选重用同名产物导致错组 | `keep_previous=True` 时第二步的 `distance contacts` 修改第一候选持有的 contacts | 执行前拒绝显式跨候选产物名冲突，保留游标和场景，提示使用 `{index}` / `{object}`；唯一名称实际切换成功 |
| P2：导航重复查询全部对象 | `_ensure_candidate_loaded` / `_evict_unneeded_objects` 循环内反复 `_object_exists` | 单次事务名称快照；scene group 切换不调用 get_session，启用对象时恢复其父组 |
| P2：参数补全分散且缺失 | style、interaction、annotator 等参数未完整接入；固定位置表不认识 keyword=value | 签名驱动的统一补全，动态对象/selection/group、合法枚举、路径、标签与ID；保留原生解析器 |
| P2：绘图 preset 与帮助清单脱节 | 远端已有多个样式，当前 registry 无对应条目；手写帮助需要重复维护 | 接入6个样式并使用原快照/撤销；帮助自动纳入 registry 新增条目 |

生产入口：`pp_group`、`pp_group_by_name`、`pp_review_groups`；`sa_load_groups` 和 `sa_group_candidate` 在 Annotator 加载后可用。完整操作说明见 [Group、interaction 与绘图命令](../01_group_interaction_styles.md)。新样式为 `clean_outline`、`ao_cartoon`、`soft_glossy`、`monochrome_bw`、`goodsell_flat`、`ligand_outline`。

## 验收结果与证据

实际运行时为 Windows PyMOL 3.1.3.1 / bundled Python 3.10.16；普通单测使用系统 Python 3.13。所有 Host 测试运行在独立进程，用户原有 PyMOL 会话未参与。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| interaction 相关单测 | 92项通过 | `tests/interactions/` |
| interaction 真实API | 13种场景通过；selection名称、成员、启用状态及源原子保持；clear/undo按owner精确清理 | `output/01_interactions_selection_free.json` |
| console/样式/group/帮助单测 | 70项通过 | `output/unification_unit.log` |
| 原生Tab、样式与group | 75 canonical + 8 compat命令，390参数位置；24 sa命令，50参数位置。所有补全provider执行无异常，原生Parser实际补全；样式设置/视图恢复 | `output/unification/result.json`、`native.log`及6张PNG |
| Annotator真实Qt | 两模式、固定参考、菜单按钮、父组恢复、标注保存重开、同源/换源、外部对象所有权、关闭恢复通过 | `output/02_annotator_groups.json`、`.log`、`.png`、`_menu.png` |
| 固定产物名回归 | 先真实复现错组，再验证拒绝冲突并保留场景；唯一名正常切换/淘汰 | `output/04_fixed_output_before.json`、`04_fixed_output_after.json` |
| 项目规定构建检查 | `.github/workflows/package.yml`所列6个测试模块共43项全部通过 | `output/03_package_checks.log` |
| 真实Plugin Manager安装 | 临时安装0.17.0，11模块、75 canonical/8 compat、菜单、Annotator/Batch窗口正常 | `output/05_package_install.json`、`.log` |

视觉检查：`clean_outline`、`ao_cartoon`、`soft_glossy`、`monochrome_bw` 示例无许可水印，结构可见；`goodsell_flat`、`ligand_outline` 两张图出现本机评估许可水印，**不能作为干净出版图片验收**。这两项的参数应用/撤销检查通过，渲染许可限制没有被绕过。实际 Qt 面板与完整 More 菜单截图均已查看。

相关旧测试另有一组80项检查：76通过、1跳过、3错误；这3项已用a883fe9原始代码重现（两个Qt stub缺属性、一个CSV schema变更后旧显示列恢复），不是本次全部测试通过。证据为 `output/02_annotator_unit.txt` 与 `output/02_annotator_baseline_unit.txt`。本次还修正了构建测试的Windows UTF-8/临时cwd/换行适配、命令计数，以及关闭后重开fixture的所有权清理，未放宽生产保护。

## 性能证据的范围

| 同一真实Host测量 | 原实现 | 当前实现 |
| --- | --- | --- |
| 101候选对象场景，两个候选管理函数各20轮 | 4060次get_object_list，1096.57ms | 40次get_names，37.49ms |
| 30次已加载scene group导航 | 旧版无对应输入模式 | 120次get_names、0次get_session，总28.00ms，平均0.93ms |

第一行仅比较已加载对象的 `_ensure_candidate_loaded` / `_evict_unneeded_objects` 循环，不代表读取、远端下载或渲染的端到端提升。测试baseline从a883fe9原始函数取得。场景导入与标注前父级校验仍需metadata snapshot，未声称全过程零查询。

官方API核验也确认 `get_session(partial=1)` 仍序列化对象，names指定group仍递归展开成员；没有公开的只读parent-tree轻量参数。来源：[PyMOL exporting.py](https://raw.githubusercontent.com/schrodinger/pymol-open-source/master/modules/pymol/exporting.py)、[Executive.cpp](https://raw.githubusercontent.com/schrodinger/pymol-open-source/master/layer3/Executive.cpp)。既有独立 `pp_nav groups` 的每步成员变化校验尚未改成缓存，这仍是后续性能审查项。

## 交付、限制与下一步

### 内置 SSH OTP（2026-11-06，追加实现）

用户要求 F101 的自动 OTP 作为插件参数提供，不依赖额外安装/修改 SSH OTP wrapper。已在 `SourceLocationControls` 的 Connections 中加入按 Host + SSH config 保存的 Automatic OTP、掩码密钥、可选 Windows DPAPI 记住，以及 Advanced 中的 OTP prompt。默认仅在本次 PyMOL 进程内使用；取消不保存，关闭 Automatic OTP 并确认会清除该主机保存值。

`SshClient(..., otp_secret=None, otp_prompt=None)` 支持读取主机配置、直接传入密钥和空字符串显式禁用。内置 OTP 使用原生 OpenSSH，继续读取原 config/known_hosts；标准库 TOTP 经插件打包的 askpass 响应器提交。验证码密钥不进入来源 URI、命令参数、子进程环境或审阅文件。Windows 原生启动器源码和 EXE 随插件交付，用户无须编译或额外安装 OTP 工具。未启用内置 OTP 时保留旧 wrapper 兼容行为；显式选择旧 wrapper 与内置 OTP 同时启用会提示清空 SSH program。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| OTP 后端及既有 wrapper/GUI Python 接口 | 18 项通过，含 RFC6238 向量、真实 DPAPI、提示过滤、失败只应答一次、临近过期/并发等待及取消、native launcher Unicode/引用 | `output/10_otp_backend_tests.log` |
| Windows 原生 OpenSSH → 临时本地 SSH 挑战服务 → PyMOL Python worker | 默认 OTP/自定义提示成功读表；第二次读取复用连接；拒绝时只提交一次；普通密码不填写；取消约 0.95 秒 | `tests/host/10_builtin_otp.py`、`output/10_otp_native.json` |
| 真实 Qt Connections | 掩码输入、DPAPI 保存重开、取消保留原值、非法密钥不写入、禁用删除、分主机、session-only 不持久化通过；截图已查看 | `tests/host/11_otp_connections.py`、`output/11_otp_connections.json`、`.png` |

这轮没有连接真实 F101 或读取其密钥。只验收了 Windows；Unix askpass 路径未在本机运行。同密钥时间窗口去重限同一 Python 进程，不覆盖其他 PyMOL 进程或外部 OTP 工具。普通密码、私钥口令与主机信任不会自动应答。

补充回归 `test_ssh_sources` + `test_ai_provider` 共 32 项：20 通过、2 跳过、1 失败、9 错误；失败项已在 `a883fe9` 原 `remote/ssh.py` 上原样重现，不作为本次通过的测试。多数为把 Windows 盘符路径交给 POSIX 远端来源 fixture；完整记录在 `output/12_ssh_regression.log`、`output/12_ssh_regression_baseline.log`。43 项规定构建检查通过（`output/12_package_checks.log`）。

追加实现已完成。GPT-6.1 Terra 会话 `01a1b098-7028-7862-860a-1d75a352bf3a` 同步了首页、快速开始、完整 CSV/SSH 指南、可用性说明及包内 INSTALL；实际最近一条 `turn_context`（2026-11-06T14:00:25.35Z）确认 model 为 `gpt-6.1-terra`、effort 为 `xhigh`，证据 `output/12_terra_otp_model.json`。原生启动器进入 ZIP 的逐字节检查和两项打包测试通过（`output/12_package_otp_checks.log`）。最终 ZIP 已在真实 PyMOL Plugin Manager 临时安装通过（`output/12_package_install.json`），当前无待监督运行。日常安装仍未变；使用下方候选包手动安装并重启后，在 Connections 中配置目标 Host 的密钥即可。真实 F101 登录留待用户提供/配置该 Host 的密钥后验收。

### 追加需求（2026-11-06，已完成）

用户追加左右键切换对象/整组，以及由 GPT-6.1 Terra 按指定文章方法重写文档，并加入原样 agent 产物整理提示词。两项追加需求已完成，下面的 ZIP 信息已更新为最终候选包。

文档写作由本机 `codex exec` 的 GPT-6.1 Terra 会话 `01a1b098-7028-7862-860a-1d75a352bf3a` 执行；模型已从实际 `turn_context` 核实，证据 `output/08_terra_model.json`。两轮写作均已完成；第二轮重写 12 份长指南/维护者正文，并沿用第一轮已改写的首页、6 个目录 README 和 group 指南。新增 agent 协作与历史索引，安装文案和导航帮助同步更新。旧细节保存在 11 份带日期的参考页，19 份历史报告保留事实，AGENTS/CONTEXT 行为文件没有在此次文档重写中改动。逐文件结果见 `output/08_terra_documentation_coverage.json`；53 份 Markdown 的 397 条本地链接/锚点、基础提示词逐字匹配和 JSON 示例检查通过。

写作方法参考 [Full Stack Data Lab 的文章](https://fsdatalab.github.io/blog/agent-text-at-scale/)：动作和关键词前置，同类内容统一结构和术语；文档层次参考 [uv](https://github.com/astral-sh/uv) 与 [ripgrep](https://github.com/BurntSushi/ripgrep) 的首页、使用指南和参考分层。

导航的真实 Qt 检查（`output/06_arrow_navigation.json`）覆盖对象/整组左右键、Ctrl 翻页、循环、文本光标编辑、keys=0、停止恢复快捷键与对象可见性，以及 Annotator Alt+方向键。原生焦点失败保存在 `output/06_arrow_navigation_before.json`：点击视图区后焦点是 CommandLineEdit，单独 set_key 收不到箭头；窗口 dispatcher 只在导航绑定有效且命令行为空时接管。group 容器可见性、隐藏成员、祖先与外部兄弟隔离、回滚及 replacement 由 `output/07_navigation_groups.json` 验证。没有声称恢复 Qt 原生操作对 selection 启用标志的一切影响；selection 的名称和成员在键盘检查中保持。

89 项相关单测、43 项规定检查和 13 项帮助检查通过，分别见 `output/06_arrow_unit.log`、`output/09_package_checks.log`、`output/09_help_checks.log`。导航/文档候选 ZIP 在真实 Windows PyMOL Plugin Manager 中临时安装通过：`output/09_package_install.json`（0.17.0、PyMOL 3.1.3.1、11 模块、75 主命令/8 兼容命令）。内置 OTP 后的最终包使用下方 `output/12_package_install.json`；所有写作和测试进程已结束，当前无待监督任务。

交付包：`dist/PymolPlugins-0.17.0.zip`（518427 bytes）。SHA256：`5885e546313dc671a7ea43eccd0fee1f6283dcfc6e85f1e60115f55838539d90`。该包已按原生 Plugin Manager 安装到临时目录并验证，日常安装仍为 0.16.0。用户随后要求提交，本轮实现、文档、测试与本记录一起提交到 `codex/unify-tools`；尚未 push/merge 或发布正式 Release，原旧工作树的已有修改保留。此前 `output/05_package_install.json` 与 `output/09_package_install.json` 对应早先候选包；当前包以 `output/12_package_install.json` 为准。

- scene-review JSON只保存组名、配置与位置，旁边JSONL保存标注；重开前必须载入原场景，重命名/更换候选集后重新导入。
- scene模式使用现有per-step commands，不把文件对象Review steps应用到借用场景组。
- 默认无selection保证适用于canonical interaction路径；手动加载的历史独立脚本未在本轮全部改造。
- 原生Tab按当前token补全，不自动解释任意复杂选择表达式或科学角色。新样式的灯光/投影/几何默认值是明确列出的全局设置，可撤销。
- PLIP/PyRosetta远端worker及真实PyMOL 2.x未重新验收。本机评估许可影响部分PNG，见上文。

下一步是使用本地候选ZIP安装并重启PyMOL后按说明使用；公开合并/发布属于后续动作。本次无遗留运行测试或等待中的远端任务。若继续开发，从此工作树、此记录和对应Host JSON恢复，不从旧目录重做。

可复现的核心命令（在此工作树，PowerShell `-NoProfile -NonInteractive`）：

```powershell
& 'D:/Softwares/Pymol3.1/python.exe' -u tests/host/01_toolkit_unification.py output/unification
& 'D:/Softwares/Pymol3.1/python.exe' tests/interactions/01_selection_free_host.py output/01_interactions_selection_free.json
& 'D:/Softwares/Pymol3.1/python.exe' tests/host/02_annotator_groups.py --output output/02_annotator_groups.json
& 'D:/Softwares/Pymol3.1/python.exe' tests/host/02_annotator_groups.py --fixed-output-check --output output/04_fixed_output_after.json
& 'D:/Softwares/Pymol3.1/python.exe' -u tests/host/06_arrow_navigation.py output/06_arrow_navigation.json
& 'D:/Softwares/Pymol3.1/python.exe' tests/host/07_navigation_groups.py
python -m unittest tests.annotation.test_ssh_otp -v
& 'C:/Python313/python.exe' -u tests/host/10_builtin_otp.py
& 'D:/Softwares/Pymol3.1/python.exe' -I -u tests/host/11_otp_connections.py
python scripts/package_plugin.py
& 'D:/Softwares/Pymol3.1/python.exe' -I -u tests/host/plugin_package_smoke.py dist/PymolPlugins-0.17.0.zip --output output/12_package_install.json
```

## 2026-11-07 主分支合并与自动更新交付

用户随后授权合并到主分支，并要求配置自动或便捷更新。`66060ee` 已快进进入 GitHub `Molaison/pymol_scripts` 的 `main`；[构建与发布 42248314684](https://github.com/Molaison/pymol_scripts/actions/runs/42248314684) 成功，发布了 v0.17.0。原旧工作树持有的本地 main 和未提交文件保持原状；合并目标是远端主分支。

实际配置更新时，旧 0.16.0 更新器遇到 GitHub 匿名 API 403，响应明确 `X-RateLimit-Remaining=0`，公开 Release 下载仍成功。`72d9d0f` 增加仅限 API 配额耗尽时使用公开 `releases/latest`、稳定 tag 与 SHA256SUMS 的检查路径，继续验证版本、ZIP 路径和 SHA256；不要求用户添加 token。该修复已进入 main，[构建与发布 42249640595](https://github.com/Molaison/pymol_scripts/actions/runs/42249640595) 成功，[v0.17.1 已正式发布](https://github.com/Molaison/pymol_scripts/releases/tag/v0.17.1)。使用方式依据 GitHub 官方 [限流说明](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) 和 [Release 链接说明](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)。

本机实际安装 `D:/Softwares/Pymol3.1/Lib/site-packages/pmg_tk/startup/pymol_plugins` 已从 0.16.0 升到 0.17.1，旧版保存在同级 `.pymol_plugins-backup`。首次升级由修复后的更新器通过公开稳定版入口引导完成，读取正式 Release、校验 SHA256/版本并执行原子替换；没有声称旧更新器已经能够自行克服限流。新进程从实际 Plugin Manager 注册路径加载 0.17.1，启动检查返回 `Up to date: 0.17.1`，OTP 原生组件存在。

用户配置 `C:/Users/ADMIN/AppData/Roaming/PymolPlugins/Workspaces.ini` 中的 `updates/auto_check`、`updates/auto_install` 均已设为 true，插件 autoload 为 true。此为本机设置；其他用户的默认值仍是启动检查开启、自动安装关闭。入口为 **Plugin → PymolPlugins: Updates**，可手动 **Check now / Download and install**。未来稳定版在启动检查后自动安装，完整退出并重开 PyMOL 后生效。检查时没有正在运行的本机 PyMOL 进程；未关闭用户会话。

| 检查 | 实际结果与证据 |
| --- | --- |
| 旧版真实网络失败与公开下载 | API 403/remaining=0、Release 清单 HTTP 200；`output/13_update_network_probe.json`，已去除临时下载 URL 的签名参数 |
| 更新器定向与规定检查 | 12 项更新器检查、46 项规定检查通过；`output/14_updater_tests.log`、`output/14_package_checks.log`；CI 同样成功 |
| 正式版真实安装 | 0.17.1 校验后替换、备份版本 0.16.0；`output/14_install_stable.json` |
| 已安装版真实 Qt 启动检查 | 版本、注册路径、持久配置、autoload、最新状态通过；`output/15_confirm_updates.json`，对应 PNG 已查看 |
| 用户文档 | GPT-6.1 Terra 原会话同步首页、完整指南和包内 INSTALL；`output/14_terra_updates_result.md` |

当前发布与本机更新已完成，无待监督进程。以后使用 Updates 入口即可；真实 F101 OTP 登录仍需按该 Host 的密钥配置完成后验收。

## 2026-11-09 大场景切组性能

负责人：当前 Codex 会话。状态：优化和真实 Host 验收完成；改动留在 `codex/group-navigation-performance`，尚未提交、发布或替换日常安装。基线 `127c2bf`。此节为唯一任务恢复记录。

### 实现与实测结果

`pymolplugins/navigation/commands.py` 的 `_group_parent_snapshot` 改为 `get_session(..., binary=1)`；每次切换仍读取当前父子关系，不缓存分组、不仅检查名称集合。旧接口的 TypeError 兼容路径保留。实际导出设置在测试前后均为 0，未被修改。

真实 PyMOL profile 确认 `get_session` 是导航调用的主要热点。`api_probe.json` 在 169,416 原子场景中测得默认会话读取中位数 182.88 ms，binary 为 11.59 ms，父子映射一致；因此没有扩大改动到导航状态、分组事件或全局设置。官方实现依据：[CmdGetSession](https://github.com/schrodinger/pymol-open-source/blob/master/layer4/Cmd.cpp) 临时设置并恢复 binary；本机 `pymol/exporting.py` 也支持该参数。

| 同一真实 Windows Host，15 次切换中位数 | 优化前 | 优化后 | 变化 |
| --- | ---: | ---: | ---: |
| 60 组：导航调用 | 110.52 ms | 8.51 ms | 12.98× |
| 60 组：导航 + Qt repaint | 637.98 ms | 536.39 ms | 减少 15.92% |
| 180 组：导航调用 | 219.00 ms | 30.72 ms | 7.13× |
| 180 组：导航 + Qt repaint | 1958.13 ms | 1775.48 ms | 减少 9.33% |

场景分别为 301/901 对象，57,096/169,416 原子；每组含真实 936 原子蛋白、嵌套组、CGO 和 measurement。3 次预热、15 次无 profile 计时、另做 3 次 profile。两个版本串行运行且输入、视角、导航参数相同。重绘时间仍主导总体耗时；此结果不是远端读取/下载或真人键盘到屏幕延迟的提升。

### 真实验收与边界

- `output/16_group_navigation/after.json`：passed。原生右方向键、CGO 新增、接触线移出、同名称集合下结构/嵌套组跨组移动、清空并删除候选组均通过。修改涉及未显示组；变化在下一次切换检测到（约 30–33 ms），游标和场景不变；恢复后可继续。
- `output/16_group_navigation/before.json`：完整性能测量和截图有效，后续恢复测试 failed。原版已正确返回 stale；本机原生 `group …, remove` 没有移出成员，导致恢复断言失败。相同现象在插件外单独复现；改用原生 `ungroup` 后，优化版小场景和完整场景通过。不将该失败标为通过，不修改 PyMOL 本体。
- `before_edits.json`：使用 git 基线模块和修正后的原生命令，五组场景的全部改组检查 passed；同时验证了可复现的 before 模块加载入口。没有重跑已完成的大场景计时。
- `output/16_group_navigation_unit.log`：59 项导航测试通过，含 binary 读取、同名称跨组移动、旧接口兼容。
- `output/16_group_navigation_package_checks.log`：46 项项目规定检查通过。未重新构建或发布同版本 ZIP。
- `comparison.html` 为自包含对比报告，`comparison.json` 为数值；`before_180.png`、`after_180.png` 为真实 OpenGL framebuffer，均已目视检查，保留本机许可文字。

此前当前桌面初测、错误断言及两次被中止的重复运行不参与最终比较。重复执行由越界子代理引起，已停止；测试入口增加 Windows mutex，拒绝并行启动同一 benchmark。误取当前桌面区域的初始截图已删除。最终测量 JSON 保留每次样本、profile、版本与源码 SHA256。

### 隐藏桌面与恢复

用户认可独立隐藏桌面并要求成为全局短规则；已写入本机 `C:/Users/ADMIN/.codex/AGENTS.md` 和明确请求的偏好记忆。仍使用 ADMIN，未登录额外用户、未更改凭据/RDP；CreateDesktopW + CreateProcess 把 GUI 放到非输入桌面。资格验证 `isolated_qualification.json` 实测 PyMOL 3.1.3.1 / Intel UHD 770 / OpenGL 4.6，旋转和换色后 framebuffer 正确更新。最终 before/after 的 `*_measure_desktop.json` 记录输入桌面前后均 Default；after child exit 0。所有本任务进程均已终态。

复现入口（在本工作树按顺序运行；before 仅在测试进程内加载 git 基线版本的 navigation 模块，不更改工作树）：

```powershell
& 'D:/Softwares/Pymol3.1/python.exe' -I -u tests/host/16_group_navigation_perf.py --stage measure --variant before --isolated
& 'D:/Softwares/Pymol3.1/python.exe' -I -u tests/host/16_group_navigation_perf.py --stage measure --variant after --isolated
& 'D:/Softwares/Pymol3.1/python.exe' -I -u tests/host/16_group_navigation_perf.py --stage compare --variant after
```

已有数据无需重跑。后续若继续优化，下一瓶颈是 PyMOL/Qt repaint；先独立测其成本。F101 OTP 和远端 worker 不属于本轮范围。

## 2026-11-09 Step commands 预设与 review bundle prompt

负责人：当前 Codex 会话。状态：实现及完整 Host 验收完成，包含新候选切换的相机保持修复；仍在 `PymolPlugins-unification` 工作树，尚未提交、打包、发布或替换日常安装。上轮大场景性能改动保留。本节为本轮唯一执行记录，无待监督进程。

新增保持相机、界面表面、固定参考叠合、候选 Cα 对齐四个草稿预设，沿用预览/确认插入/Apply 流程。固定参考选择器只列已加载的 fixed Review steps 对象；草稿使用命名占位符，AI 草稿解析器也接受上下文中实际声明的对象名。未声明占位符仍报错。参考对齐移动 candidate，参考保持不动；复合物须检查链对应。

生成 prompt 统一到 `docs/02_agent_review.md`，旧完整指南及历史进阶锚点指向此入口。保留 hardlink、raw 分数不重算、代表性 label、缺失值/重复/遗漏核查与筛选依据，补充稳定候选粒度/ID、评分溯源、固定参考和步骤执行顺序、完整路径检查及人工 override 保护。`id_column` 不能写入 review.json，需在 UI 的 Row ID column 选择；显式 steps 的 commands 项只有省略 commands 字段时才执行 step_cmd。

### 实际验证

- `output/17_step_presets/01_result.json`：passed。Windows PyMOL 3.1.3.1 / Intel UHD 770 / OpenGL 4.6，加载本工作树代码；用 [RCSB 1BRS](https://files.rcsb.org/download/1BRS.pdb) 的 A/D 链生成临时测试候选和参考，第二候选验证实际切换。测试不是新的科学评分或实验结论。
- 四个预设均通过真实 Qt 生成、确认插入、Apply、重载和切换第二候选执行。生成/插入未改场景；保持相机与叠合预设在新候选切换时相机也不变；界面表面有真实 surface；对齐候选移动约 12 Å，Cα RMSD < 0.01 Å，固定参考最大坐标变化为 0。每个预设的 candidate_switch 为 passed。
- 手工追加 `color salmon, {candidate}` 后，第二候选使用新命令、同名参考被复用；关闭/重开保留 override 和命令。源 review.json、候选文件、参考及原始 1BRS 内容不变；无关对象和人工 selection 成员保持。
- 两个 prompt JSON 示例经过插件真实配置/列名验证，缺失值与代表结构筛选命中数正确；命名占位符覆盖 on_open 和 after_label，未知名及未选择参考均拒绝。未调用外部模型评估其生成 bundle 的成功率；本轮交付是 prompt 与合同验收。
- `02_desktop.json`：输入桌面前后均为 Default，隐藏桌面子进程 exit 0；`03_render_before.png` / `04_render_changed.png` 证实实际 OpenGL 画面更新。`05_`–`08_` 为四预设视口，`09_reference_draft.png` 为真实 Qt 参考选择/草稿界面，已目视检查。
- `10_required_checks.log`：46 项项目规定检查 passed；`git diff --check` 无空白错误。未重复大场景性能测试，未重建同版本 ZIP。

首轮 `11_initial_failure.json` 因“所有预设零新增 selection”的测试假设失败；原生 `align` 确实创建或更新 `_align1`、`_align2`，`quiet=1` 亦如此，见独立原生探针 `12_align_probe.json` 及 [上游 ExecutiveAlign](https://github.com/schrodinger/pymol-open-source/blob/master/layer3/Executive.cpp)。保留其原生行为，在预设草稿和使用说明中明确提示；不删除用户选择或声称零新增。`13_navigation_wait_failure.json` 是测试只等待普通导航而未等待 Review steps 异步任务，提前读取颜色造成的失败；修正为等待 `_csv_task_id` 结束并确认目标索引，最终完整检查通过。失败未冒充通过。

补查真实新候选时，`14_before_camera_fix.json` 复现了加载入口 `zoom=-1` 触发 PyMOL 全局 auto_zoom，使无相机命令的预设仍跳视角。候选加载现固定 `zoom=0`，视角由 step_cmd 决定；默认 `zoom {object}` 仍执行，辅助 Review steps 对象原本已使用 zoom=0，不修改全局设置。修复后四个预设的新候选检查通过。`15_stale_test_object.json` 是检查缺少参考的负例仍使用已被切换淘汰的测试对象名；改为取当前候选，最终整个脚本 exit 0、JSON passed。

复现（工作树根目录，测试默认在独立隐藏桌面；输入已保存在 output 中，无需重复下载）：

```powershell
& 'D:/Softwares/Pymol3.1/python.exe' -I -u tests/host/17_step_presets.py
```

下一步：需要交付日常安装时，与尚未提交的性能改动一起审定版本、打包并发布；本轮没有操作用户 PyMOL 会话。真实 PyMOL 2.x 未重新验收。

## 2026-11-09 0.18.0 合并发布与本机更新

负责人：当前 Codex 会话。状态：进行中。用户明确授权将预设、bundle prompt、保持相机修复与上一轮切组性能优化一起提交、合并发布，再更新本机插件；不关闭或热重载当前 PyMOL 会话。版本从 0.17.1 升为 0.18.0。此节是本次发布的唯一状态记录；证据保存在 `output/18_release/`。

远端已核实为 `Molaison/pymol_scripts`，main 与本地起点均为 `127c2bf`；旧 origin URL 可正常重定向，不另改远端配置。复用现有 CI 自动发布流程。下一步：提交发布变更、PR 检查/合并，等待正式 Release，然后通过原生 updater 校验安装，并在独立隐藏桌面验证实际安装目录。
