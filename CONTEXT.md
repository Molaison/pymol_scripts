# 项目上下文

更新：2026-10-05

## 目标与成功标准
新手目标：没有历史配置时，靠布局、明确控件和状态反馈完成首次打开、查看和保存，而不是阅读常驻教程。每次增加文字前先判断能否用UI表达；解释按需打开，错误、进度和未保存状态不得隐藏。连续审阅目标：显式恢复、later路线、分数解释和可交接摘要，不改变质量标签含义。
当前重点：远端文件作为主要输入时的 SSH 连接、传输和连续审阅体验。减少实际传输/重复复制，等待阶段、耗时和取消可见；分别证明真实 Windows UI 行为与真实服务器耗时，不绕过原 OTP 规则。
在既有结构审阅、候选表、批处理入口上改进用户体验。增加全量概览、明确标注的分层抽样、文字配合颜色、candidate/reference 对照，以及当前provider的AI per-step命令草稿。完成标准是 Windows Host PyMOL 中真实结构、数据与窗口操作的证据，不以单测或退出码代替。

## 策略与理由
复用唯一 annotation/navigation 状态和现有导入、保存、路径映射、后台任务机制；先完整交付候选审阅增量，再按验收矩阵补缺。全量、过滤集合、样本分别计数；抽样用于覆盖审阅，不宣称无偏统计代表性。

## 关键决定
- SSH优化保留现有持久会话、profile和登录程序；轻量压缩仅减少线传字节，原子发布与源版本检查保持不变。任务继续使用原权威验收记录的“SSH体验优化（2026-09-30）”章节，不重复建档。
- AI默认Codex模式由WSL短命进程读取现有provider/model；显式Custom API由Host同安装Python运行，URL/Key/model/API格式由用户选择，不静默换服务。Key默认会话内，可选Windows DPAPI加密保存，绝不进入prompt、日志或dataset。Qt负责草稿与cmd.*；每次打开结构/保存指定标签是不同触发step。预设和AI都先预览/确认插入/Apply，生成不执行。
- Windows Host 拥有 GUI 与全部 cmd.*；WSL 仅开发及可选 worker。
- 不重做批处理已有 A/B 配对；reference 用明确配置的表列关联，不猜路径。
- 不安装管理工具代替开发。当前 PATH 和既知安装位置均没有可用 Beads，以下单一执行记录暂为权威任务状态。
- 抽样仅作显式会话审阅层，重导入回到完整筛选集合；评分百分位和导出不把样本冒充全集。批处理预填A/B列，但不自动继承审阅抽样/过滤，必须先预览确认。
- 发布走 GitHub 公开仓库 `Molaison/pymol_scripts`（origin，main）：安装包由 `.github/workflows/package.yml` 自动构建，push main 产出 Actions artifact，推 `v<version>` 标签（须等于 `pymolplugins/__init__.py` 的 `__version__`）发布 Release，含 ZIP 与 SHA256SUMS。ZIP 一律由 `scripts/package_plugin.py` 生成，不再手工打包。

## 长期发现
既有 Host 记录见 docs/implementation_status.md。历史已通过不等于本版本重新通过；新验收保留失败 JSON 和原生截图。
Windows K:映射盘可能对不同文件报告st_ino=0，并拒绝realpath；不能直接用samestat/samefile的零inode结果认定文件相同。review.override内容不变时不应重复替换，否则会使正在导入的源签名失效。
Windows PyMOL 3.1的原子表示位需通过cmd.iterate读取，不能假设get_model的Atom含reps。引号内括号/逗号需要两个目标命令的专用解析入口。执行完成标记不应创建场景选择，否则原生错误尾部的迟到执行可能留下对象；短命内存token不改变原生用户命令尾部的取消语义。
普通CSV导入明确不恢复整份历史设置，持久命令需走现有review.override映射；step_cmd和bucket_cmds现在一起保存/恢复。AI生成只覆盖用户确认的草稿，原Apply/执行边界不变。取消通过stdin结束WSL请求进程，不承诺取消供应商计算/计费。

## 约束
接管基线 149 项未提交修改；禁止 reset/clean、覆盖工作树。job-runner/pi-job-runner 暂停。不改 SSH 身份或全局环境/provider 配置。仅项目、既有 output/、Host 验收临时文件和可回滚安装可写。

## 任务与证据入口
权威执行记录：[本次功能验收与状态](docs/validation/01_sampling_reference_2026-09-29.md)。
0.11.0已完成AI增量及真实临时安装；恢复读同一记录末尾“AI最终交付”。线程仍为`01a0e982-0ba7-7f20-aae6-748f04eac0cc`。`dist/PymolPlugins-0.11.0.zip`，最终证据`output/07_ai_installed_20260929.json` passed（实际provider、正确step、多行、编辑确认、2399原子真实执行、取消、错误保留、保存重开）。保留600行及0.10.0安装证据，没有重跑。所有测试句柄已终态，无子代理/科学任务；下一步用户可安装新版，项目其余人工/外部边界继续留在同一矩阵。
Host 复现入口：tests/host/ 与 justfile。历史总表：docs/implementation_status.md。
SSH 0.12.0已交付：负责人线程01a0f2f7-3536-7d30-a8a7-348c6fe15590。恢复读同一权威记录末尾“SSH最终交付”。证据08_ssh_ux、09_ssh_real、10_ssh_package_install_20260930.json均passed；实际x/OTP首连加读表3.46秒、热导入600行0.412秒，线传载荷下降但不宣称吞吐提升等于压缩比。安装包dist/PymolPlugins-0.12.0.zip；全部句柄终态，无待监督作业。下一步为用户安装并重启，当前用户安装未改动。

新手上手0.12.1已交付：同一负责人线程，权威记录末尾“新手上手最终交付”。11_onboarding_20260930.json证明临时安装包、干净设置、帮助、空SSH列表、首次CSV/结构导入和Save and next通过；760×560截图已看。ZIP为dist/PymolPlugins-0.12.1.zip；用户当前安装未改，全部句柄终态。下一步为安装/重启；未做真人上手计时，不把自动化验收当真人研究。

连续审阅0.12.2已完成：权威记录末尾“连续审阅升级”。最终包dist/PymolPlugins-0.12.2.zip，Windows临时安装证据output/12_continuous_review_20261001.json passed；包含Continue入口、later/Next unreviewed、handoff、错误恢复和760x560首次路径。21项相关单测与静态检查通过；混合90项测试的FakeCmd.keyword环境阻塞已记录。当前安装未改，所有句柄终态。


SSH桌面入口0.12.3已交付：同一权威记录末尾“SSH桌面入口修复最终交付”。Windows pythonw宿主让原OTP wrapper的子进程输出脱离插件管道，造成外部ready而authenticate不退出；此前console Python验收未覆盖此边界。现Python登录脚本使用同安装目录python.exe，保留原profile/wrapper/OTP及无窗口标志；缺少该解释器时明确报错。Host SSH验收默认改为pythonw。
最终包dist/PymolPlugins-0.12.3.zip；13_ssh_ready_gui、13_ssh_ready_real、13_ssh_ready_install_20261002.json均passed，定向回归23项通过。原x实际读取600行/56列、2399原子；单次首连含读表1.60秒、GUI热导入0.41秒，不承诺恒定耗时。98874/17744/79645全部终态，无待监督作业；当前用户安装和进程未改。下一步用户安装0.12.3并完全退出、重启PyMOL，沿用原x配置。


页面减字0.12.4已交付：用户明确要求“让UI设计自己说话”。默认页面不再放三步教程、SSH说明、颜色图例、分数免责声明；用上下文动作、按需帮助/Advanced、Later状态标记、条件范围计数表达。该原则是后续UI增量的约束，不以新手友好为由堆常驻小字；关键错误/进度/取消/未保存状态保留。
证据output/14_quiet_ui_20261002.json passed：Windows pythonw/PyMOL3.1.3.1、最终ZIP临时安装、真实首次打开/保存与设置/帮助/筛选/暂缓流程。760×560打开/审阅和576×207设置、586×430帮助截图已查看。首轮弹窗异常和无输出探针保留，不伪称其通过；详细记录见同一权威执行记录末尾。最终包dist/PymolPlugins-0.12.4.zip；全部句柄终态，当前安装与用户进程未改。下一步安装并完全退出/重启，不再重跑已验证边界。


0.13.0已交付：同一权威记录末尾“AI配置、预设、路径UX最终交付”。自定义API地址/Key/模型及模型列表、明确Responses/Chat Completions、Windows加密Key；AI失败保留草稿并反馈step/原因，支持常见对象设置和配色。7个无需API的查看预设均已在真实结构执行且坐标不变；PLI透明背景/口袋朝向经实际截图修正。远端路径fzf式排序/键盘、切目录清筛选、失败回滚已通过原生SSH。新表继承上一源reference列的真实缺陷也已修复。
证据：15_path_ux passed；15_command_ux首轮保留后段切表failed，但前面的真实Custom API/31模型/8.46秒生成/2399原子执行/Key加密/重开均完成；修复后的15_viewing_presets与最终15_pli_view passed。两种协议本地HTTP测试9项、命令6项、模糊匹配6项通过。最终包dist/PymolPlugins-0.13.0.zip，全部句柄终态，两个worker已完成，用户当前安装/进程和全局配置未改。下一步安装并完全重启；若用户补原始失败prompt再定向复验，不重复已过API。
长期边界：reference_column属于数据源，不应从前一张表的GUI配置泄漏到新源。膜预设只显示现有脂质/疏水残基，不构造膜平面；PPI预设按当前链组成生成，异构候选需检查选择表达式。后续文案仍优先替换为控件/状态设计。


0.14.0筛选分组已交付：review.json可选filter_presets（name/expression及sort_column/sort_descending），使用现有安全表达式引擎和唯一STATE导航。Filter箭头一键切组、当前组名/菜单勾选；Filter页编辑/保存/新增/删除。可重叠、不自动标注、不移动结构，空组保持0；人的修改原子写review.override.json，不覆盖生成配置，数组整体覆盖，[]可清空。换数据源清旧分组，旧review.json不需要修改；新字段要求0.14.0+。
完整产物prompt在docs/structure_annotator_csv_walkthrough.md末尾，保留hardlink/raw打分不重算/代表性label/4–6关键指标，加入有依据的具体阈值、表达式合同、重叠/缺失/未覆盖计数和不覆盖人工override。未给实际运行路径，本轮不生成科学产物。
证据output/16_filter_groups_20261002.json passed（最终ZIP临时安装、旧配置、3分组、重叠/空组、编辑新增删除/override重开/换源/源内容不变），7项合同单测及prompt示例真实解析通过；编辑页/菜单/当前组截图已看。保留两次验收菜单点击问题的失败日志，最终修正为模态菜单循环内定时真实点击。所有执行句柄终态，停止的仅是明确owned测试PID9672，用户PyMOL未动，遗留临时安装已清理。下一步用户安装dist/PymolPlugins-0.14.0.zip并重启，使用新prompt整理后续产物。

自动参考0.14.1：reference_column现在驱动导入/切step自动加载，当前步隐藏/失败/取消不自动重试；候选不移动，仅reference对齐显示。独立Linux clone分支codex/automatic-step-reference，交付dist/PymolPlugins-0.14.1.zip；同一权威执行记录末尾“自动reference最终交付”保存复现及边界。真实Linux PyMOL2.6.2/Qt与cal53原结构通过，Windows未实测、用户安装未修改。

自动发布/自更新0.15.0：用户2026-10-13明确授权仓库直接公开、合并及发布。main新版本通过package/layout/updater测试后自动创建v版本Release、ZIP与SHA256SUMS，已有Release不覆盖；PR只构建artifact。插件公开读取正式Release，默认启动后台检查，Updates中可启用自动安装，校验包后同卷替换、保留备份，需重启。沿用同一权威记录“自动发布与插件更新”。

2026-10-13用户转移ownership至Molaison/pymol_scripts，公开仓库ID830272878不变。更新源改固定numeric repository API并验证其返回的canonical Release资产，避免再次转移时失效。0.15.0首次自动Release已成功；0.15.1交付ownership修复，不覆盖旧资产。

reference当前合同（0.15.1）：用户明确要求常驻固定对象，已替代0.14.1的删除/重载及reference跟随对齐。每源唯一参考缓存，切步enable/disable；reference原始坐标保持，candidate会话对象对齐并zoom配对，同名candidate重载会重新align。换源/关闭清理，磁盘PDB和评分不变。真实四步验证output/19_fixed_reference_linux_20261013.json通过，保留两个reference、回访同名复用、参考坐标误差0、四份源文件未改；Windows未实测。

0.15.1已正式发布：https://github.com/Molaison/pymol_scripts/releases/tag/v0.15.1；PR2/3/4均合并。25项CI及真实Linux公开Release升级/新进程启动通过，证据18_release_update_linux与19_fixed_reference_linux，同一权威记录末尾“0.15.1最终发布与验收”。首次安装修正版后可启用自动安装，后续版本合入main自动发布。Windows用户安装未改、未实测。

0.16.0当前合同：reference不再是专用自动启动/对齐功能。review.json.steps定义有序object/commands，固定对象差量复用，on_load_cmd只在加载或显式修改定义时运行，step_cmd及独立commands按配置执行；任意命名对象占位符在加载后可用。More/Automation的Review steps编辑器支持添加、移除、上下移动及fixed/路径列/命令；filter_text与sort_column/sort_descending可作review默认，Filter控制候选顺序。显式步骤的文件准备在worker进行，Qt按实际顺序提交；该模式不提前加载场景对象，文件缓存继续复用。普通数据保持原流程。原cal53完整CSV验证和实际编辑/保存/重开证据见同一权威记录新节，不以小样本替代完整导入。

0.16.0已发布（PR5合并、main自动发布run38397263655成功）：https://github.com/Molaison/pymol_scripts/releases/tag/v0.16.0。完整原CSV5065行、默认5039candidate、固定对象零重复toggle、任意名对象无强制对齐，以及GUI改顺序/命令保存重开均实测通过。权威记录末尾0.16.0最终交付；用户安装未改，Windows未实测。
