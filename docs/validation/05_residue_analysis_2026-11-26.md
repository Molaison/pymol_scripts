# View 配色中的残基分析：CoreToSurf 与结构理解补充

更新：2026-11-26。负责人：当前 Codex 会话。状态：完成，验收通过。
代码 owner：main，D:/SyncPath/GitRepo/PymolPlugins-reviewed-integration。残基算法的原始集成基线为 e00394e（0.22.5），View 嵌入基线为 b321597（0.23.0）。交付版本为 0.23.0。独立审查与 main 发布范围见文末。

## 使用

1. 在 PyMOL 加载结构，打开 **Plugin → PymolPlugins → Change scene view… → Color**。在 Review 页也可通过 **View → Color…** 带入当前候选；输入 **pp_residues** 会复用同一个 View 窗口并选择 Core / boundary / surface。
2. 使用 View 顶部的 **Object**，在 **Palette** 中选 Core / boundary / surface，点共享的 **Apply object color**。按整残基 SASA 分层；应用后滑条实时更新，切换单位时分别保留各自阈值。**Undo last style** 一次撤销该次应用及随后的阈值预览。
3. 同一 **Palette** 还可切到相对暴露度、Kyte–Doolittle 疏水性、暴露疏水残基和非局部 Cα 接触数。相关参数按当前配色显示；普通配色继续使用原来的控件。**Copy command** 复制对应命令。
4. 展开 **Residue values and export** 查看可排序、横向滚动的表格；双击残基定位该位置。**Copy layers** 复制 PyMOL 选择表达式或按对象列出的 Rosetta resnums；**Residue CSV…** 和 **Contact CSV…** 分别保存逐残基数据与稀疏接触对。查看、复制和导出数值不重新着色。
5. **Create selections** 显式生成 pp_residues_core / boundary / surface / unclassified。**pp_residue_clear** 恢复栈顶连续的命令行残基配色，并删除这些选择。残基配色、普通配色和 Lighting 共用样式撤销顺序。如果更新的操作来自 View 或其他 style，先用 **Undo last style** 或 **pp_style_revert** 撤销。命令行配色之间若有其他 style，撤销中间的 style 后再次清理。

下列 model 替换为实际已加载对象名：

~~~pymol
pp_residues
pp_core_surface model, 20, 40
pp_core_surface model, 20, 50, rel
pp_residues model, mode=hydropathy
pp_residues model, mode=exposed, exposure=25, hydrophobicity=0
pp_residues model, mode=contacts, cutoff=8, gap=2
pp_residues model, color=0, outfile="D:/results/residues.csv"
pp_residue_clear
~~~

coretosurf model, 20, 40 保留上游命令拼写，但选择名使用 pp_residues_*，保留场景中已有的 core / boundary / surface。正常 pp_residues 和 pp_core_surface 默认不创建选择。pp_help pp_residues 可查帮助；选择、mode、unit、布尔值和导出路径支持现有补全框架。

## 现有能力与本次缺口

| 能力 | main 基线的已有能力 | 本次实现 |
| --- | --- | --- |
| SASA / RSA 与 core–boundary–surface | 未发现本地逐残基分析；PyMOL 原生命令可算面积，但没有这套统一面板与缓存/导出 | 复用 CoreToSurf 的最大 ASA、残基归一化和 Rosetta 格式化，调用原生 get_area；增加对象/状态、选择与颜色生命周期 |
| 疏水性与暴露疏水残基 | interchain_basic 是氢键/盐桥；ligand_contacts 是几何接触；PLIP 有蛋白–配体疏水相互作用，但没有此本地逐残基视图 | Kyte–Doolittle 标尺上色，与 RSA 联合筛出暴露疏水残基 |
| 非局部 Cα 接触数 | 原子距离、界面接触和 PLIP 已有；未发现逐残基非局部 Cα 计数 | 空间网格计算、逐残基计数上色、接触对 CSV |
| 氢键、盐桥、界面、clash、口袋 | 已有 interactions 与既有视图/后端入口 | 继续使用原实现 |

本地检索范围为 main 的 pymolplugins 与 tests。另查阅 [Color_h](https://pymolwiki.org/Color_h)（Eisenberg 标尺）和 [Contact Map Explorer](https://github.com/dwhswenson/contact_map)（基于 MDTraj 的轨迹接触分析）。本次补充使用已发表的 KD 数值与明确的距离定义，采用纯标准库实现，不给普通 PyMOL 分析增加 MDTraj 等依赖。

## 计算定义

- 仅报告蛋白残基。SASA 的遮蔽环境为每个源对象的重原子：保留配体等非水异质原子，排除水与氢。不同对象独立计算；如果配体需要遮蔽蛋白，应在同一源对象内。
- 在临时对象上设置 dot_solvent=1、dot_density=3、solvent_radius=1.4 Å，以 get_area(load_b=1) 计算每个原子的面积并按残基求和。源对象 B 因子、坐标、表示和相机保持；预览仅修改选中蛋白残基的碳原子颜色。
- 每个残基保留模型、segment、chain、含 insertion code 的 resi 和 resn。可替代构象按同残基 occupancy 总和选一个非空 alt，保留公共空 alt 原子；Cα 取同一构象。
- 绝对分层默认 core <20 Å²、boundary 20–40 Å²、surface ≥40 Å²。相对分层默认 core <20%、boundary 20–50%、surface ≥50%。整残基 PyMOL SASA 与 Rosetta 默认侧链邻居法、mainchain+CB SASA 并不等价；这里的 20/40 是 CoreToSurf 默认分界。
- RSA = 100 × SASA / Tien 2013 理论最大 ASA。保留大于 100% 的实际值；未知残基不补造参考面积，相对分层归入 unclassified。常见修饰/力场残基名使用父氨基酸的参考值，见源码 aliases。
- Kyte–Doolittle 数值范围 −4.5 至 +4.5，正值偏疏水。暴露疏水默认要求 RSA ≥25% 且 hydropathy >0，可分别修改；未知标尺值保留空值。
- 接触对使用 Cα 距离 ≤8 Å，默认排除同 object/segment/chain 内模型顺序相邻 2 位。gap 按实际观察到的残基顺序，而非 PDB 编号差。仅在本次 selection 的蛋白残基间统计，不计算不同对象之间的接触。无 Cα 的残基显示不可用。
- 缓存以对象、状态、拓扑/残基身份、坐标、occupancy 和原子半径为依据，坐标或编号变化后自动重算；Recompute values 可强制重算。选择蛋白的一部分时，SASA 仍保留源对象完整遮蔽环境。
- PyMOL 选择导出包含对象、segment、chain 和原始残基号。Rosetta resnums 按对象分开；对负编号、insertion code、非单 ASCII 字母 chain、同对象重复 chain/resi 跨 segment 的歧义给出遗漏提示，不把它们错误映射成另一个残基。

## 来源与许可证

- [CoreToSurf](https://github.com/ajasja/CoreToSurf)，固定提交 58dd4a5dc0d6cbe9b2efa6d811948734c2cfabae（2026-11-25），BSD-3-Clause。提取并适配 src/coretosurf/sasa.py 的纯算法；来源与完整许可证随包保留在 pymolplugins/residues/LICENSE.CoreToSurf。
- [Tien et al. 2013, PLoS ONE e80635](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0080635)：理论最大 ASA。
- [ExPASy ProtScale：Kyte–Doolittle](https://web.expasy.org/protscale/pscale/Hydropath.Doolittle.html)：疏水标尺数值。
- [PyMOL 原生命令参考](https://pymol.org/pymol-command-ref.html#get_area)：get_area 计算接口。
- [Rosetta ResidueSelectors](https://docs.rosettacommons.org/docs/latest/scripting_documentation/RosettaScripts/ResidueSelectors/ResidueSelectors)：LayerSelector 方法和使用范围对照。

## 验收、证据与恢复

全部必需验收通过，无待监督子进程。使用 V1 原生角色；没有启动 Job Runner、科学推理或云连接器。当前旧脚本 checkout 的独立 UI 改进记录与文件不属于本任务。

- 115 项样式、纯指标、manifest/console adapter、帮助、打包、更新器、自动 reference、Review 导入和命令模板测试通过。包含未知残基、CA 距离边界、同链 gap、跨对象/链/segment、随机点对 brute force 和许可证打包。
- 10 项真实 PyMOL 3.1.4.1 测试通过：原生 SASA oracle；源 B/color/坐标/全局设置；重原子遮蔽与水/氢；同 atomcount 坐标编辑与重编号缓存；多状态；byres 与环境；对象隔离；导出歧义；颜色恢复与选择冲突；新增残基配色快照与普通 style 撤销的组合检查。UI 快照只含本次分析会修改的碳原子，不写入命令行颜色恢复状态。
- 新增 6 项真实 PyMOL 撤销回归测试通过。覆盖同名重建后的快照与 CLI clear、View 与 CLI 的两种先后顺序、被普通 style 分开的 CLI 块，以及更新的普通配色和 Lighting。
- 最终 ZIP 在 PyMOL 3.1.3.1 的独立隐藏桌面通过实际 View → Color 入口、无独立残基菜单、Qt/OpenGL、滑条/单位记忆与单次撤销、五种着色、剪贴板、真实文件保存对话框、234 行 residue CSV、724 对 contact CSV。展开数据区后的实测窗口为 620×732，计数和图例保留，操作反馈在共享状态区显示。共享对象选择、空参数命令复用 View、混合配色撤销、外部 style 保护、Lighting 应用/撤销和原始坐标/B 因子/颜色恢复均通过。输入桌面始终 Default。
- 同一个最终 ZIP 通过 Review 页的 View → Color… 真实菜单点击验收。当前候选对象、普通 pLDDT 配色、独立 Lighting 和外部 style 撤销保护均通过。
- 原始残基算法交付时的已安装包曾在 PyMOL 3.1.4.1 中通过控制台 coretosurf、带引号 selection、带空格 CSV 路径、保留用户 core 选择与清理命令检查；结果在 09_checks.json 的 baseline_results 中保留，本次未重复。PyMOL 2.x、Linux/macOS 未在本轮执行。

当前证据在 output/08_residue_analysis/12_review_confirmation/：01_result.json、02_desktop.json、原生截图、两个 CSV 和 09_checks.json。其下 10_review_view/ 保存同一最终 ZIP 的 Review 入口终态、桌面记录和 13_appearance.png。先前结果和旧包哈希仍在父目录及 10_review_view/ 保留。

原生排错发现：填表完成后一次 resizeColumnsToContents，避免每插入一个单元格就重算列宽。读取原子颜色使用 cmd.iterate，不能假设 get_model 返回的 Chempy Atom 有 color 属性。隐藏桌面的文件保存验收使用逐键输入路径并按 Enter，等待 Save 可用；为模态对话框加了超时取证，避免测试永久停在保存窗口。

可复现命令（仓库根目录）：

~~~powershell
C:/Python313/python.exe -m unittest tests.styles.test_style_registry tests.test_residue_metrics tests.architecture.test_plugin_package tests.architecture.test_package_layout tests.architecture.test_host_help_registry tests.host.test_updater tests.annotation.test_automatic_reference tests.annotation.test_review_import tests.annotation.test_command_templates -q
D:/Softwares/Pymol3/python.exe -m unittest tests.test_residue_analysis tests.test_residue_color_lifecycle -v
C:/Python313/python.exe scripts/package_plugin.py
D:/Softwares/Pymol3.1/python.exe -I -u tests/host/21_hidden_desktop.py --record output/08_residue_analysis/12_review_confirmation/02_desktop.json --timeout 90 tests/host/25_residue_analysis.py --package dist/PymolPlugins-0.23.0.zip --output output/08_residue_analysis/12_review_confirmation
D:/Softwares/Pymol3.1/python.exe -I -u tests/host/24_reviewed_integration.py --source D:/SyncPath/GitRepo/PymolPlugins-reviewed-integration --output output/08_residue_analysis/12_review_confirmation/10_review_view --package dist/PymolPlugins-0.23.0.zip --checks appearance
~~~

本地验收安装包：dist/PymolPlugins-0.23.0.zip，850392 bytes。SHA256：3436f0e3ce2bf0970cd215b5757867ab6167a284c59a45433997797f8320c6a1。
在 Plugin Manager 选择此 ZIP 安装，重启 PyMOL 后从 Change scene view… → Color 或 Review → View → Color… 打开。main 推送使用仓库现有 workflow 发布 [v0.23.0](https://github.com/Molaison/pymol_scripts/releases/tag/v0.23.0)。发布资产以 Release 随附 SHA256SUMS 为准。日常 PyMOL 安装未修改。

## 独立审查与 main 整合（2026-11-26）

用户要求独立子代理审查 UI（Impeccable）和实现模式，修完后合入 main。审查快照固定为 6eb55c0f81d434c3a06047043ccea1a1d9a4718c；本地 main 已含两个功能提交，远端 main 在开始审查时仍为 e00394e。

- Impeccable A：01a215a7-2c75-71d3-af9f-018bb74f92e6，独立设计判断与已有原生截图。
- Impeccable B：01a215a7-5e5e-74b2-b831-b97c2bd215dc，独立检测器适用性与原生 Qt 证据。
- 实现模式：01a215a7-8935-7c91-8b9d-f34f6b63540c，分析、颜色、预览和撤销的共享生命周期。

三份原始报告与复现保存在 output/08_residue_analysis/11_independent_review/。五项已证实问题均已修复：

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| 行内参数标签缺少显式输入关联 | 添加可访问名称和 QLabel buddy | 真实 Qt 检查名称与 buddy。实际读屏播报未测 |
| 复制、生成选择和导出覆盖计数与图例 | 操作反馈进入 View 已有状态区 | 原生 GUI 检查复制后摘要保留，截图显示生成选择后的摘要 |
| 切换 Palette 后保留旧应用确认 | 选择、对象和参数变化时清理过期反馈 | 原生 GUI 的 palette_message_reset 通过 |
| 删除后重建的同名对象匹配旧快照 | 复用项目 molecule_identity，恢复前只读核对对象生命期 | 两项真实 PyMOL 重建对象回归测试通过 |
| 旧 View 撤销或预览覆盖更新的 CLI 配色 | View、CLI 残基配色登记现有 style registry，统一 LIFO 与失败回滚 | 六项生命周期测试及真实 GUI 的 CLI 后预览与撤销保护通过 |

原审查代理完成一次修复确认。05_ui_fix_confirmation.md 确认原 UI 问题已解决，未见截图中明确新增问题。06_implementation_fix_confirmation.md 确认两项实现问题已解决。实现代理另用真实 PyMOL 注入 restore callback 故障，确认失败时场景恢复到撤销前、快照保留、重试成功。脚本和 JSON 为 03_revert_rollback_repro.*。

Impeccable 的修复前设计记录归档到 .impeccable/critique/2026-11-26T02-33-18Z__pymolplugins-residues-panel-py.md。29/40 是修复前评分。bundled detector 只支持 HTML/CSS，本次不把其零输出当作 Qt 扫描通过。
