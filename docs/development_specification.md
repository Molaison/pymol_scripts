# 维护统一 PyMOL 工具箱

修改统一命令、审阅、几何、交互或 worker 时，先按本页确定 owner、输入输出、场景副作用与真实验收边界。每个改动应能通过相应任务入口复现，并记录结果和未验证项。原始设计、字段、参数和完整验收 ID 保存在 [详细规格](13_reference_development_2026-11-06.md)；其中历史计划与拟议能力不是当前实现承诺。

## 确定改动边界

1. 用 `pp_help`、manifest 和相关实现确认当前命令、签名与所属模块。Windows PyMOL/Qt 拥有界面和 `cmd.*`；WSL 负责可选 worker 和可独立运行的纯计算。
2. 为输入、计算结果和场景修改划清边界。明确 selection/anchor、需要移动的对象、输出及其 owner；不要把不同概念混成同一参数。
3. 给出可执行示例、预览或提交条件、撤销/清理方式和错误后的状态。复杂选择、多个候选或冲突名称不能静默猜测。
4. 按 [Host 验收](windows_host_acceptance.md) 选择对应边界。已往某版本通过不证明这次变更通过。

## 维护参数与共享数据

所有 canonical `cmd.extend` 回调通过共享 console adapter 和严格解析处理布尔值、数值、引号、名称、路径及列表。保持原返回 schema 和旧兼容入口，新增功能优先复用统一实现。序列用 residue records 和原 object/chain/resi/插入码/altloc 身份定位；不能假设编号连续或 CA 一定存在。

对象/group 解析应支持精确、glob、contains、regex 和有界 fuzzy；递归展开时保留成员类型和明确所有权。模糊多匹配的 mutation 需要显式选择。输出 manifest 记录本次拥有的精确名称，清理只能针对这些名称；冲突、部分失败和替换应保留可恢复状态。

## 维护导航和标注

导航只保留一个会话状态和游标；文件、对象、根 group 按条目切换，pin/window/stride/page/wrap/hook 有明确边界。高层 `pp_nav objects` / `groups` 支持 glob，默认 Left/Right 前后、Ctrl+Left/Right 翻页；空命令行接收快捷键，有文字时保留编辑；`keys=0` 禁用绑定，stop 恢复原按键和可见性。祖先需要启用时保留子项开关并遮蔽原隐藏祖先下的外部 siblings。单元测试不能代替真实 Qt 焦点与键盘验证。

目录、CSV/TSV 与 SSH 数据源复用一个审阅状态；原路径和行身份需保留，相同路径的不同行可独立标注。读取 `review.json` 后应用 `review.override.json` 和明确导入选择；编辑不覆盖生成配置。标注/状态与科学源分离，失败、取消和迟到后台结果不能替换现有场景。

Review steps 必须按顺序处理 candidate、命名对象和命令；fixed 路径复用，只有显式命令才对齐/着色。scene group 模式借用用户组，不能把借用对象认作可删除资产。目录扫描、远端读取、旧 inline 标注和 CSV 身份路径准备放后台；Qt 提交不重新探测远程文件。

## 维护刚体跟随与对齐

follow 使用 `arm → 移动 driver → apply`，传播 driver 相对基线的刚体 delta，而不是复制绝对矩阵；保留 follower 相对位置。仅接受合法刚体变换；缺失、重命名、组成员或外部状态变化要报告。默认跳过 callback-owned CGO；原子坐标编辑不是对象 TTT。原验收 `FOLLOW-01` 至 `FOLLOW-06` 覆盖非 identity、旋转平移、重复 apply/rebase、CGO、外部变更和基线撤销。

序列匹配保留链、插入码、负编号和 atom identity，支持有界 wildcard，unique 不唯一时拒绝。对齐分离 anchor 与 move payload，检查原子对应和非共线点；不静默解决对称配体歧义。预览不移动对象，提交/撤销使用记录和过期检查。原 `SEQ-*`、`ALIGN-*` 数值与身份验收见详细规格。

## 维护轴、膜与样式

PCA 最长轴来自最大协方差特征向量；近简并必须报告置信度并拒绝不安全自动方向，除非用户明确强制。cyclic C2 需要独立方法；不能由两个 centroid 假造唯一平面。膜 slab 使用轴中心和法向，在中心两侧各半厚度绘制；自动范围覆盖结构投影。ANVIL 使用明确 executable 或已有结果，检查 DUM 层、坐标身份与刚体变换，不能把 WSL 路径当 Windows 可执行文件。原 `GEO-*`、`MEM-*` 保留在详细规格。

样式 registry 验证名称、颜色和 setting；apply 默认不隐式渲染或移动相机，报告全局与目标影响；snapshot/revert 按栈恢复。render 显式执行 ray/PNG。新 preset 与兼容命令复用同一 registry；查看 `STYLE-*` 原始验收与当前 Host 证据。

## 维护相互作用和远端分析

interaction 从请求、backend、标准 `InteractionResult` 到 renderer 和 `OutputManifest`；保留 source/state/参数、原子与残基身份、几何和 provenance。默认 canonical 路径不新增 partner 命名 selection；不全局隐藏或删除用户对象。清理只删除受管输出。异步返回前检查源 fingerprint 与同输出最新请求，过期结果不能覆盖新场景。

PLIP 与 Rosetta 通过受限 HTTP 接口传结构文本；后端缺失明确报告，不静默切换。请求/响应执行大小、schema、数值及身份校验。Qt worker 只做网络，结果回主线程后渲染。取消抑制结果，不能保证停止 C++。CMS、bound bunsat、interface delta 的科学含义分开；记录 backend 版本、参数和输入来源。原 `INT-*`、`ROS-*` 逐项标准保留在详细规格。

## 验收与记录

对改变的行为先做对应纯逻辑检查，再在涉及 parser、Qt、对象类型、矩阵或网络时使用真实边界。检查失败和回滚、原数据及非 owned 对象、最终结果而非只看进程退出码；将日期、版本、输入、JSON/截图、失败和未执行项写入验证记录。真实 PyMOL 2.x、外部 worker 和许可依赖要分别标注，不用 fake 测试推断通过。构建和发布按现有项目流程由负责人执行。

历史细节、全部命令形式、数据模型、阶段与验收原文见 [详细规格](13_reference_development_2026-11-06.md)；任务操作见 [命令指南](task_command_walkthrough.md)，历史结果见 [索引](03_history_index.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="1-目标与边界"></a>
- [1. 目标与边界](13_reference_development_2026-11-06.md#1-%E7%9B%AE%E6%A0%87%E4%B8%8E%E8%BE%B9%E7%95%8C)
<a id="2-当前仓库诊断"></a>
- [2. 当前仓库诊断](13_reference_development_2026-11-06.md#2-%E5%BD%93%E5%89%8D%E4%BB%93%E5%BA%93%E8%AF%8A%E6%96%AD)
<a id="21-导航存在三套不兼容状态机"></a>
- [2.1 导航存在三套不兼容状态机](13_reference_development_2026-11-06.md#21-%E5%AF%BC%E8%88%AA%E5%AD%98%E5%9C%A8%E4%B8%89%E5%A5%97%E4%B8%8D%E5%85%BC%E5%AE%B9%E7%8A%B6%E6%80%81%E6%9C%BA)
<a id="22-pymol-group-不是父变换节点"></a>
- [2.2 PyMOL group 不是父变换节点](13_reference_development_2026-11-06.md#22-pymol-group-%E4%B8%8D%E6%98%AF%E7%88%B6%E5%8F%98%E6%8D%A2%E8%8A%82%E7%82%B9)
<a id="23-styles-没有统一契约"></a>
- [2.3 styles 没有统一契约](13_reference_development_2026-11-06.md#23-styles-%E6%B2%A1%E6%9C%89%E7%BB%9F%E4%B8%80%E5%A5%91%E7%BA%A6)
<a id="24-interactions-的错误来自选择边界和无数据契约"></a>
- [2.4 interactions 的错误来自选择边界和无数据契约](13_reference_development_2026-11-06.md#24-interactions-%E7%9A%84%E9%94%99%E8%AF%AF%E6%9D%A5%E8%87%AA%E9%80%89%E6%8B%A9%E8%BE%B9%E7%95%8C%E5%92%8C%E6%97%A0%E6%95%B0%E6%8D%AE%E5%A5%91%E7%BA%A6)
<a id="25-selectionalignmentgeometry-有确定性错误"></a>
- [2.5 selection/alignment/geometry 有确定性错误](13_reference_development_2026-11-06.md#25-selectionalignmentgeometry-%E6%9C%89%E7%A1%AE%E5%AE%9A%E6%80%A7%E9%94%99%E8%AF%AF)
<a id="26-wslhost-是两个运行面"></a>
- [2.6 WSL/Host 是两个运行面](13_reference_development_2026-11-06.md#26-wslhost-%E6%98%AF%E4%B8%A4%E4%B8%AA%E8%BF%90%E8%A1%8C%E9%9D%A2)
<a id="3-统一设计原则"></a>
- [3. 统一设计原则](13_reference_development_2026-11-06.md#3-%E7%BB%9F%E4%B8%80%E8%AE%BE%E8%AE%A1%E5%8E%9F%E5%88%99)
<a id="31-计算与-pymol-副作用分离"></a>
- [3.1 计算与 PyMOL 副作用分离](13_reference_development_2026-11-06.md#31-%E8%AE%A1%E7%AE%97%E4%B8%8E-pymol-%E5%89%AF%E4%BD%9C%E7%94%A8%E5%88%86%E7%A6%BB)
<a id="32-anchormovedisplay-三个边界分离"></a>
- [3.2 anchor、move、display 三个边界分离](13_reference_development_2026-11-06.md#32-anchormovedisplay-%E4%B8%89%E4%B8%AA%E8%BE%B9%E7%95%8C%E5%88%86%E7%A6%BB)
<a id="33-默认-session-safe"></a>
- [3.3 默认 session-safe](13_reference_development_2026-11-06.md#33-%E9%BB%98%E8%AE%A4-session-safe)
<a id="34-命名空间与兼容"></a>
- [3.4 命名空间与兼容](13_reference_development_2026-11-06.md#34-%E5%91%BD%E5%90%8D%E7%A9%BA%E9%97%B4%E4%B8%8E%E5%85%BC%E5%AE%B9)
<a id="35-可复现输出"></a>
- [3.5 可复现输出](13_reference_development_2026-11-06.md#35-%E5%8F%AF%E5%A4%8D%E7%8E%B0%E8%BE%93%E5%87%BA)
<a id="4-共享基础设施"></a>
- [4. 共享基础设施](13_reference_development_2026-11-06.md#4-%E5%85%B1%E4%BA%AB%E5%9F%BA%E7%A1%80%E8%AE%BE%E6%96%BD)
<a id="41-参数解析"></a>
- [4.1 参数解析](13_reference_development_2026-11-06.md#41-%E5%8F%82%E6%95%B0%E8%A7%A3%E6%9E%90)
<a id="42-residuerecord-与-sequence-index"></a>
- [4.2 ResidueRecord 与 sequence index](13_reference_development_2026-11-06.md#42-residuerecord-%E4%B8%8E-sequence-index)
<a id="43-纯-python-3d-math"></a>
- [4.3 纯 Python 3D math](13_reference_development_2026-11-06.md#43-%E7%BA%AF-python-3d-math)
<a id="44-outputmanifest"></a>
- [4.4 OutputManifest](13_reference_development_2026-11-06.md#44-outputmanifest)
<a id="45-objectgroup-target-resolver"></a>
- [4.5 Object/group target resolver](13_reference_development_2026-11-06.md#45-objectgroup-target-resolver)
<a id="46-package-ownership"></a>
- [4.6 Package ownership](13_reference_development_2026-11-06.md#46-package-ownership)
<a id="5-specification统一导航"></a>
- [5. Specification：统一导航](13_reference_development_2026-11-06.md#5-specification%E7%BB%9F%E4%B8%80%E5%AF%BC%E8%88%AA)
<a id="51-数据模型"></a>
- [5.1 数据模型](13_reference_development_2026-11-06.md#51-%E6%95%B0%E6%8D%AE%E6%A8%A1%E5%9E%8B)
<a id="52-commands"></a>
- [5.2 Commands](13_reference_development_2026-11-06.md#52-commands)
<a id="53-行为"></a>
- [5.3 行为](13_reference_development_2026-11-06.md#53-%E8%A1%8C%E4%B8%BA)
<a id="54-验收"></a>
- [5.4 验收](13_reference_development_2026-11-06.md#54-%E9%AA%8C%E6%94%B6)
<a id="55-structure-annotator-可恢复状态与全局历史"></a>
- [5.5 Structure Annotator 可恢复状态与全局历史](13_reference_development_2026-11-06.md#55-structure-annotator-%E5%8F%AF%E6%81%A2%E5%A4%8D%E7%8A%B6%E6%80%81%E4%B8%8E%E5%85%A8%E5%B1%80%E5%8E%86%E5%8F%B2)
<a id="56-structure-annotator-csv路径映射与本地缓存"></a>
- [5.6 Structure Annotator CSV、路径映射与本地缓存](13_reference_development_2026-11-06.md#56-structure-annotator-csv%E8%B7%AF%E5%BE%84%E6%98%A0%E5%B0%84%E4%B8%8E%E6%9C%AC%E5%9C%B0%E7%BC%93%E5%AD%98)
<a id="57-review-bundles-and-responsive-source-import"></a>
- [5.7 Review bundles and responsive source import](13_reference_development_2026-11-06.md#57-review-bundles-and-responsive-source-import)
<a id="6-specificationgroup-follow-刚体变换"></a>
- [6. Specification：group follow 刚体变换](13_reference_development_2026-11-06.md#6-specificationgroup-follow-%E5%88%9A%E4%BD%93%E5%8F%98%E6%8D%A2)
<a id="61-commands"></a>
- [6.1 Commands](13_reference_development_2026-11-06.md#61-commands)
<a id="62-算法"></a>
- [6.2 算法](13_reference_development_2026-11-06.md#62-%E7%AE%97%E6%B3%95)
<a id="63-支持范围"></a>
- [6.3 支持范围](13_reference_development_2026-11-06.md#63-%E6%94%AF%E6%8C%81%E8%8C%83%E5%9B%B4)
<a id="64-验收"></a>
- [6.4 验收](13_reference_development_2026-11-06.md#64-%E9%AA%8C%E6%94%B6)
<a id="7-specificationsequence-与高级对齐"></a>
- [7. Specification：sequence 与高级对齐](13_reference_development_2026-11-06.md#7-specificationsequence-%E4%B8%8E%E9%AB%98%E7%BA%A7%E5%AF%B9%E9%BD%90)
<a id="71-sequence-commands"></a>
- [7.1 Sequence commands](13_reference_development_2026-11-06.md#71-sequence-commands)
<a id="72-alignment-commands"></a>
- [7.2 Alignment commands](13_reference_development_2026-11-06.md#72-alignment-commands)
<a id="73-对应关系与刚体拟合"></a>
- [7.3 对应关系与刚体拟合](13_reference_development_2026-11-06.md#73-%E5%AF%B9%E5%BA%94%E5%85%B3%E7%B3%BB%E4%B8%8E%E5%88%9A%E4%BD%93%E6%8B%9F%E5%90%88)
<a id="74-验收"></a>
- [7.4 验收](13_reference_development_2026-11-06.md#74-%E9%AA%8C%E6%94%B6)
<a id="8-specificationgeometryaxis-与-membrane-slab"></a>
- [8. Specification：geometry、axis 与 membrane slab](13_reference_development_2026-11-06.md#8-specificationgeometryaxis-%E4%B8%8E-membrane-slab)
<a id="81-统一-axisresult"></a>
- [8.1 统一 AxisResult](13_reference_development_2026-11-06.md#81-%E7%BB%9F%E4%B8%80-axisresult)
<a id="82-commands"></a>
- [8.2 Commands](13_reference_development_2026-11-06.md#82-commands)
<a id="83-pca"></a>
- [8.3 PCA](13_reference_development_2026-11-06.md#83-pca)
<a id="84-cyclic-axis"></a>
- [8.4 Cyclic axis](13_reference_development_2026-11-06.md#84-cyclic-axis)
<a id="85-membrane-slab"></a>
- [8.5 Membrane slab](13_reference_development_2026-11-06.md#85-membrane-slab)
<a id="86-anvil-adapter"></a>
- [8.6 ANVIL adapter](13_reference_development_2026-11-06.md#86-anvil-adapter)
<a id="87-验收"></a>
- [8.7 验收](13_reference_development_2026-11-06.md#87-%E9%AA%8C%E6%94%B6)
<a id="9-specificationstyles-registry"></a>
- [9. Specification：styles registry](13_reference_development_2026-11-06.md#9-specificationstyles-registry)
<a id="91-preset-model"></a>
- [9.1 Preset model](13_reference_development_2026-11-06.md#91-preset-model)
<a id="92-commands"></a>
- [9.2 Commands](13_reference_development_2026-11-06.md#92-commands)
<a id="93-行为与验收"></a>
- [9.3 行为与验收](13_reference_development_2026-11-06.md#93-%E8%A1%8C%E4%B8%BA%E4%B8%8E%E9%AA%8C%E6%94%B6)
<a id="10-specificationinteractions-presets-与输出"></a>
- [10. Specification：interactions presets 与输出](13_reference_development_2026-11-06.md#10-specificationinteractions-presets-%E4%B8%8E%E8%BE%93%E5%87%BA)
<a id="101-pipeline"></a>
- [10.1 Pipeline](13_reference_development_2026-11-06.md#101-pipeline)
<a id="102-commands"></a>
- [10.2 Commands](13_reference_development_2026-11-06.md#102-commands)
<a id="103-public-schema"></a>
- [10.3 Public schema](13_reference_development_2026-11-06.md#103-public-schema)
<a id="104-输出树"></a>
- [10.4 输出树](13_reference_development_2026-11-06.md#104-%E8%BE%93%E5%87%BA%E6%A0%91)
<a id="105-plip-hostwsl-backend"></a>
- [10.5 PLIP Host/WSL backend](13_reference_development_2026-11-06.md#105-plip-hostwsl-backend)
<a id="106-验收"></a>
- [10.6 验收](13_reference_development_2026-11-06.md#106-%E9%AA%8C%E6%94%B6)
<a id="11-specificationwsl-rosetta-metrics-service"></a>
- [11. Specification：WSL Rosetta metrics service](13_reference_development_2026-11-06.md#11-specificationwsl-rosetta-metrics-service)
<a id="111-architecture"></a>
- [11.1 Architecture](13_reference_development_2026-11-06.md#111-architecture)
<a id="112-request"></a>
- [11.2 Request](13_reference_development_2026-11-06.md#112-request)
<a id="113-metrics-semantics"></a>
- [11.3 Metrics semantics](13_reference_development_2026-11-06.md#113-metrics-semantics)
<a id="114-securityruntime"></a>
- [11.4 Security/runtime](13_reference_development_2026-11-06.md#114-securityruntime)
<a id="115-licenseinstall"></a>
- [11.5 License/install](13_reference_development_2026-11-06.md#115-licenseinstall)
<a id="116-验收"></a>
- [11.6 验收](13_reference_development_2026-11-06.md#116-%E9%AA%8C%E6%94%B6)
<a id="12-主动增加的高价值体验"></a>
- [12. 主动增加的高价值体验](13_reference_development_2026-11-06.md#12-%E4%B8%BB%E5%8A%A8%E5%A2%9E%E5%8A%A0%E7%9A%84%E9%AB%98%E4%BB%B7%E5%80%BC%E4%BD%93%E9%AA%8C)
<a id="121-plugin-bootstrap-与-doctor"></a>
- [12.1 Plugin bootstrap 与 doctor](13_reference_development_2026-11-06.md#121-plugin-bootstrap-%E4%B8%8E-doctor)
<a id="122-result-freshness-与-caching"></a>
- [12.2 Result freshness 与 caching](13_reference_development_2026-11-06.md#122-result-freshness-%E4%B8%8E-caching)
<a id="123-batch-integration"></a>
- [12.3 Batch integration](13_reference_development_2026-11-06.md#123-batch-integration)
<a id="124-ui-与导出"></a>
- [12.4 UI 与导出](13_reference_development_2026-11-06.md#124-ui-%E4%B8%8E%E5%AF%BC%E5%87%BA)
<a id="125-objectgroup-command-iteration"></a>
- [12.5 Object/group command iteration](13_reference_development_2026-11-06.md#125-objectgroup-command-iteration)
<a id="126-task-facades-与统一-undo"></a>
- [12.6 Task facades 与统一 undo](13_reference_development_2026-11-06.md#126-task-facades-%E4%B8%8E%E7%BB%9F%E4%B8%80-undo)
<a id="13-测试策略"></a>
- [13. 测试策略](13_reference_development_2026-11-06.md#13-%E6%B5%8B%E8%AF%95%E7%AD%96%E7%95%A5)
<a id="131-wsl-可执行门禁"></a>
- [13.1 WSL 可执行门禁](13_reference_development_2026-11-06.md#131-wsl-%E5%8F%AF%E6%89%A7%E8%A1%8C%E9%97%A8%E7%A6%81)
<a id="132-windows-host-pymol-integration"></a>
- [13.2 Windows Host PyMOL integration](13_reference_development_2026-11-06.md#132-windows-host-pymol-integration)
<a id="14-实施顺序与完成定义"></a>
- [14. 实施顺序与完成定义](13_reference_development_2026-11-06.md#14-%E5%AE%9E%E6%96%BD%E9%A1%BA%E5%BA%8F%E4%B8%8E%E5%AE%8C%E6%88%90%E5%AE%9A%E4%B9%89)
<a id="phase-0基础与正确性"></a>
- [Phase 0：基础与正确性](13_reference_development_2026-11-06.md#phase-0%E5%9F%BA%E7%A1%80%E4%B8%8E%E6%AD%A3%E7%A1%AE%E6%80%A7)
<a id="phase-1导航与对齐"></a>
- [Phase 1：导航与对齐](13_reference_development_2026-11-06.md#phase-1%E5%AF%BC%E8%88%AA%E4%B8%8E%E5%AF%B9%E9%BD%90)
<a id="phase-2stylesinteractions"></a>
- [Phase 2：styles/interactions](13_reference_development_2026-11-06.md#phase-2stylesinteractions)
<a id="phase-3geometrymembrane"></a>
- [Phase 3：geometry/membrane](13_reference_development_2026-11-06.md#phase-3geometrymembrane)
<a id="phase-4wsl-services-与集成"></a>
- [Phase 4：WSL services 与集成](13_reference_development_2026-11-06.md#phase-4wsl-services-%E4%B8%8E%E9%9B%86%E6%88%90)
<a id="15-外部依据"></a>
- [15. 外部依据](13_reference_development_2026-11-06.md#15-%E5%A4%96%E9%83%A8%E4%BE%9D%E6%8D%AE)

</details>
