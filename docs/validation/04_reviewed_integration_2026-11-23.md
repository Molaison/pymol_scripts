# 未合并修改审查与整合

本次从 `origin/main` 的 `542a57a` 出发，将有独立价值的修改整合到
`codex/reviewed-integration-20261123`。审查开始于 2026-11-23，验收完成于
2026-11-24。交付为本地 Git 提交；原分支、原工作树、本地 main 和用户安装保留。

16 个有未提交内容的源工作树先保存为本地原始提交。暂存区与工作文件不同的版本
也分别保存。引用位于 `refs/codex/raw-integration/20261123/` 和
`refs/codex/source-integration/20261123/`，精确输入见本机唯一恢复记录。
依赖缓存、实验输出和旧任务文件只作本地归档，未进入产品。

## 取舍

| 来源 | 结果与理由 |
| --- | --- |
| Legacy Collections 与 round 26 | 接受采样、结构差异、并排面板、预览恢复和独立导出；纳入独有进度节流。有限坐标实验和九份临时补丁已被最终核心覆盖，去重。 |
| CSV reference / pocket / CDR | 接受统一包内的参考、口袋、CDR、评分筛选与审阅增量；修复实际坐标、对应和生命周期错误。 |
| 批量比较 | 接受单参考、逐行父本和优化路径；统一到 Annotator 的 Batch 页及 canonical `pp_compare`。 |
| Experience | 接受 preset-first 草稿、折叠编辑、映射调序、两种样式和页面自有 Undo；保留 main 的 Review 布局与独立 Color / Lighting 页。 |
| 扩展序列 | 接受 `pp_fasta` 与显式 `(RESN)` 匹配，复用唯一 residue index 和 selection 事务；不加入第二套正则或短名全局命令。 |
| macOS、旧 Screen Boost、unification、review-merge | main 或已移入的 owner 已覆盖对应能力；保留较新的布局、导航、Bundle 兼容、膜面/边及 cavity 控件。 |
| 旧 PLIP runner | canonical worker、protocol 和 renderer 已覆盖八类交互、partners、groups 与聚焦显示，旧同步 runner 归档。 |
| 规则文件与历史上下文 | 原始版本保留；不作为产品功能重新移植。 |

本次接受的独立修复包为 `d5f5384`、`0992054`、`a8dbad0`、`247886f`，
版本标注修复最终为 `46b93bd`。完整逐来源决定见
`D:/SyncPath/GitRepo/PymolPlugins/output/07_reviewed_integration/03_decisions.tsv`。

## 已修复的问题

- 已有对象 TTT 时，低 RMSD 与屏幕上约 35 Å 的错位并存。比较现在使用显示坐标，
  只写回候选第一帧，参考与后续帧保持。局部拟合仍按完整参与链建立序列对应。
- 不完整的配体 RMSD 对应曾丢弃全部 contact 差异。现在保留独有取代基的 gained /
  lost contacts，并统一 local/world 坐标。旧 `_align1` / `_align2` 选择可恢复。
- 同名新分子曾接收旧变换、颜色、可见性或异步结果。现在检查分子加载生命周期，
  旧结果失效，新的 reference / cavity 结果仍能显示。
- 结构在磁盘被替换后，旧结构的标签曾被认作新结构已审阅。质量标签现在携带已观察
  版本，历史、batch、Undo 和 compaction 保留关联；direct 对象逐出后重新准备身份。
- 标注版本与缓存绝对位置分开，CSV 原始路径和目录稳定 ID 使相对路径 Bundle 搬迁后
  可以恢复匹配版本的标签。标注时不增加源 stat、hash 或 SSH 请求。
- pLDDT 全零输入曾被错误拒绝。有限 0–100 值现在可用，包括 0；非法值仍在改颜色前拒绝。
  Appearance Undo 使用该页自己的 snapshot ID，保护其他操作的栈顶。
- Batch 入口误用 `QStackedWidget.addTab`，已改为 `addWidget`。草稿往返保留，Review
  的 Reference 按钮只在 Review 显示；忙时导航、切页及关闭遵守场景清理边界。
- 同文件及硬链接配对在共同入口拒绝，比较目录发布前再次检查输入身份；失败与取消
  清理本次产物。优化路径 anchor 由生成的每步命令显式隐藏。

旧无版本质量标签保留在原 JSONL 历史中，需要人工重新确认，不能自动认领新结构。
重开数据源后，匹配版本加载成功才恢复对应标签和已审阅状态；批量保存要求每个目标
已有确认版本。Later 等工作流状态继续跟随行 ID。源 CSV 的描述性 `label` 不改变。

## 验证

| 检查 | 实际结果 |
| --- | --- |
| 最终相关合同与 package / command ownership | 506 项：498 通过、3 跳过、5 个 main 基线失败保留。 |
| 真实 PyMOL 比较、扩展序列、pocket / CDR 算法 | 24 项通过。 |
| 版本、历史、Undo、batch、可携带身份与真实 PyMOL | 17 项通过；部分与组合合同重复，不累加为独立测试数。 |
| Collections | 38 项 sampling、4 项 package 检查通过；math / native / regression 退出 0，真实并行 PNG、锁、回滚、取消与收据通过。 |
| 实际 ANARCII | 单域与含 linker 的双域编号在独立环境通过。GUI 本轮不重复模型计算。 |
| Windows 临时安装与隐藏桌面 | 最终 8 个计划阶段通过；参考按钮定向修正后 4 个计划阶段通过。 |

5 个保留失败均在独立 `542a57a` 基线复现：

1. `test_case_distinct_csv_siblings_use_distinct_sidecars`
2. `test_case_distinct_csv_siblings_preserve_the_exact_legacy_owner`
3. `test_csv_schema_change_recovers_stale_display_sort_and_filter_view`
4. `test_registered_console_adapter_unquotes_ligand_selection`
5. `test_registered_clear_reports_stale_owner_without_traceback_or_delete`

没有把这些失败计为通过。新默认点击保存和版本确认规则的旧测试已按实际合同更新，
保留历史、源字节、Undo、搬迁和未确认版本的实质断言。

原生环境为 Windows PyMOL 3.1.4.1、Qt 5.15.8、Intel UHD Graphics 770 / OpenGL 4.6。
所有 GUI 使用独立 `PyMOLAcceptance_*` 桌面，输入桌面始终为 `Default`。临时安装、
设置、cache 和 fixtures 位于任务目录；自有进程已退出，用户安装及会话未修改。

最终 GUI 检查包含实际 ZIP 安装、菜单/帮助、OpenGL 变化帧、CSV、后台标签保存及 Undo、
Batch 草稿与预载往返、同名重载结果失效、生成比较目录并打开 Review，以及忙时取消关闭。
此前同一实现的 Collections parent / 主题 / 场景恢复、Color / Lighting、自有 Undo、
AI 草稿和映射调序已有完成证据；修改后只复验受影响合同。

## 证据与复现

本机证据根目录：
`D:/SyncPath/GitRepo/PymolPlugins/output/07_reviewed_integration/`。

- `02_raw_inputs.json`：输入 HEAD、原始/暂存区提交与稳定性。
- `08_collections_evidence/`：已完成的 math / native / regression 结果。
- `10_main_baseline_comparison.log`、`12_final_combined_checks.log`：基线与最终组合结果。
- `11_real_pymol_api_checks.log`、`14_annotation_version_checks.log`：实际 API 合同。
- `06_host_acceptance/01_20261124_004402/01_result.json`：8 阶段最终 GUI 验收；
  ZIP SHA256 为 `0be4afafbf8d91c6b88cc753285d55f5c0bc890307f86b41c41099c381e2ad52`。
- `06_host_acceptance/01_20261124_010737/01_result.json`：参考按钮与独立 Batch 往返复验；
  最后验收 ZIP SHA256 为 `0dd26afc05fe928c743dab2c056f443733de58f791d07e7e928705d5156ace02`。
- `06_host_acceptance/01_20261124_000115/`：Collections 实际预览与恢复。
- `06_host_acceptance/01_20261124_000002/`：Appearance、AI 和 mapping 已完成阶段；
  当时 Batch 入口失败也保留。

定向复验最初只写内存中的预载选项，被界面设置写回后未观察到预载。
脚本已改为操作真实 checkbox / spinbox，独立复验通过；没有为测试绕过产品。

```text
D:/Softwares/Pymol3/python.exe -X utf8 -m unittest tests.targets.test_02_comparison_api tests.alignment.test_extended_sequence_pymol tests.host.test_review_pocket_algorithms
D:/Softwares/Pymol3/python.exe -X utf8 -m unittest tests.annotation.test_01_annotation_versions tests.annotation.test_annotation_batches tests.annotation.test_03_annotation_portability tests.annotation.test_02_annotation_provenance_api
D:/Softwares/Pymol3/python.exe -I tests/host/24_reviewed_integration.py --source . --output output/07_reviewed_integration/06_host_acceptance --checks annotation_versions batch_draft identity batch_review lifecycle
```

最后一条通过现有 `21_hidden_desktop.py` 启动。若 runtime 缺少 pywin32，可用
`--launcher-wheel <匹配 Python 的官方 wheel>` 在任务目录解压使用，不做全局安装。
完整组合测试的模块列表记录于 `12_final_combined_checks.log`。

## 实际边界

本次新增验收为本机小型数据，没有重新证明 macOS、SSH、外部 AI 调用或大数据性能。
cavity GUI 检查验证异步生命周期与绘制回调，计算准确性使用已完成算法检查。
无既有内容摘要时，标注版本是元数据观察身份；direct 模式物化至实际解析间的并发替换
窗口沿用原加载合同。旧 PLIP 的 `plip_python` 仍为废弃参数，部署走 canonical worker。

Bundle 参考在
`K:/xcf26/zpzeng/gitrepo/agent-skills/skills/pymol/references/BUNDLE_MODE.md`
同步，按冻结文本由另一只手应用并核对 hash。未修改其安装副本、Git 分支或全局配置。
同步结果见 `18_context_bundle_applied.json`；Bundle SHA256 为
`45b325b4e6a91de444420eae97f7712cbf809b0beda4acfbf993e2fd6a49ec27`。
