# 残基分析：CoreToSurf 与结构理解补充

更新：2026-11-26。负责人：当前 Codex 会话。状态：完成，验收通过。
代码 owner：main，D:/SyncPath/GitRepo/PymolPlugins-reviewed-integration；集成基线 e00394e（0.22.5），交付版本 0.23.0。

## 使用

1. 在 PyMOL 加载结构，打开 **Plugin → PymolPlugins → Residue analysis…**，或输入 **pp_residues**。
2. 选择对象或填写 protein selection，点 **Analyze**。Core / Boundary / Surface 模式按整残基 SASA 分层；滑条实时更新，切换单位时分别保留各自阈值。
3. **Color by** 可切到相对暴露度、Kyte–Doolittle 疏水性、暴露疏水残基和非局部 Cα 接触数。相关参数按当前模式显示。
4. 表格可排序、横向滚动；双击残基定位该位置。**Copy layers** 复制 PyMOL 选择表达式或按对象列出的 Rosetta resnums；**Export residue CSV…** 和 **Export contact CSV…** 分别保存逐残基数据与稀疏接触对。
5. **Create selections** 才显式生成 pp_residues_core / boundary / surface / unclassified。**Restore colors** 或 **pp_residue_clear** 恢复本工具保存的原子颜色并删除它创建的选择。

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
- 缓存以对象、状态、拓扑/残基身份、坐标、occupancy 和原子半径为依据，坐标或编号变化后自动重算；Recompute SASA 可强制重算。选择蛋白的一部分时，SASA 仍保留源对象完整遮蔽环境。
- PyMOL 选择导出包含对象、segment、chain 和原始残基号。Rosetta resnums 按对象分开；对负编号、insertion code、非单 ASCII 字母 chain、同对象重复 chain/resi 跨 segment 的歧义给出遗漏提示，不把它们错误映射成另一个残基。

## 来源与许可证

- [CoreToSurf](https://github.com/ajasja/CoreToSurf)，固定提交 58dd4a5dc0d6cbe9b2efa6d811948734c2cfabae（2026-11-25），BSD-3-Clause。提取并适配 src/coretosurf/sasa.py 的纯算法；来源与完整许可证随包保留在 pymolplugins/residues/LICENSE.CoreToSurf。
- [Tien et al. 2013, PLoS ONE e80635](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0080635)：理论最大 ASA。
- [ExPASy ProtScale：Kyte–Doolittle](https://web.expasy.org/protscale/pscale/Hydropath.Doolittle.html)：疏水标尺数值。
- [PyMOL 原生命令参考](https://pymol.org/pymol-command-ref.html#get_area)：get_area 计算接口。
- [Rosetta ResidueSelectors](https://docs.rosettacommons.org/docs/latest/scripting_documentation/RosettaScripts/ResidueSelectors/ResidueSelectors)：LayerSelector 方法和使用范围对照。

## 验收、证据与恢复

全部必需验收通过，无待监督子进程。使用 V1 原生角色；没有启动 Job Runner、科学推理或云连接器。当前旧脚本 checkout 的独立 UI 改进记录与文件不属于本任务。

- 28 项纯指标、manifest/console adapter、帮助和打包测试通过，包含未知残基、CA 距离边界、同链 gap、跨对象/链/segment、随机点对 brute force 和许可证打包。
- 9 项真实 PyMOL 3.1.4.1 测试通过：原生 SASA oracle；源 B/color/坐标/全局设置；重原子遮蔽与水/氢；同 atomcount 坐标编辑与重编号缓存；多状态；byres 与环境；对象隔离；导出歧义；颜色恢复与选择冲突。
- 最终 ZIP 在 PyMOL 3.1.3.1 的独立隐藏桌面通过真实菜单、Qt/OpenGL、滑条/单位记忆、五种着色、剪贴板、真实文件保存对话框、234 行 residue CSV、724 对 contact CSV、620×500 控件与恢复颜色检查。输入桌面始终 Default。
- 最终 ZIP 另在 PyMOL 3.1.4.1 中通过控制台 coretosurf、带引号 selection、带空格 CSV 路径、保留用户 core 选择与清理命令检查。PyMOL 2.x、Linux/macOS 未在本轮执行。

证据目录 output/08_residue_analysis/：01_result.json（原生终态，含早期失败记录）、02_desktop.json、03_renderer_before.png、04_layers_panel.png、05_renderer_contacts.png、06_residues.csv、07_contacts.csv、08_compact_panel.png、09_checks.json（其余检查与包哈希）。

原生排错发现：QHeaderView 的持续 ResizeToContents 导致每个插入单元格都重算列宽，同一 234 残基场景填表约 22 秒；改为整表填完后一次 resizeColumnsToContents，最终记录为 0.000–0.016 秒。这是该隐藏桌面场景的表格填充时间，不是大型蛋白的整体耗时承诺。读取原子颜色使用 cmd.iterate，不能假设 get_model 返回的 Chempy Atom 有 color 属性。

可复现命令（仓库根目录）：

~~~powershell
C:/Python313/python.exe -m unittest tests.test_residue_metrics tests.architecture.test_package_layout tests.architecture.test_host_help_registry tests.architecture.test_plugin_package -q
D:/Softwares/Pymol3/python.exe -m unittest tests.test_residue_analysis -v
C:/Python313/python.exe scripts/package_plugin.py
D:/Softwares/Pymol3.1/python.exe -I -u tests/host/21_hidden_desktop.py --record output/08_residue_analysis/02_desktop.json --timeout 120 tests/host/25_residue_analysis.py --package dist/PymolPlugins-0.23.0.zip --output output/08_residue_analysis
~~~

安装包：dist/PymolPlugins-0.23.0.zip；SHA256：92f9f97c3a59559bf9007384942454fe2646c1489a47d353d0f98a4ed8817ba9。
在 Plugin Manager 选择此 ZIP 安装，重启 PyMOL 后从 Residue analysis… 打开。此交付为本地源码提交及安装包；正式 Release 和日常安装不在本次变更中。
