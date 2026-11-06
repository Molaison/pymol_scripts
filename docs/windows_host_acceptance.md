# 在 Windows PyMOL 验收改动

使用本页选择真实 Host 边界、准备隔离环境并记录结果。先准备已安装 PyMOL 的 Windows、对应 bundled Python/Qt、一次性 PyMOL 进程、临时设置与可丢弃输入；运行相关检查后保存 JSON、日志和截图。普通 Python 测试或进程退出码不能单独证明场景与界面行为。

## 准备和记录

在测试记录中写日期、代码版本、PyMOL/Python/Qt、操作系统、输入来源、执行命令、输出路径和每项 `PASS`、`FAIL`、`BLOCKED` 或 `NOT RUN`。`BLOCKED` 要说明具体缺失能力；未经执行不要写通过。测试使用隔离 `PYMOLPLUGINS_UI_SETTINGS`，避免修改日常设置和运行中的用户会话。

准备多状态 molecule、带非 identity TTT 的对象、嵌套 group、CGO/map/measurement、精确 protein/ligand 选择和临时 CSV/PDB。需要 sequence/geometry 时覆盖空链、插入码、缺失原子和真实或合成对称体。只清理测试拥有的名称，不清理用户场景。

在独立 PyMOL 中检查基础入口：

```pymol
pp_load
pp_doctor check_worker=0
pp_help
```

记录缺失命令和冲突；重复装载不应产生重复菜单或签名变化。可选 worker 不可用时本地功能仍应可加载。真实 PyMOL 2.x 与其他 Qt 绑定属于独立验收，不能从 3.1 结果推断。

## 验收导航和快捷键

分别测试文件、对象、group；包括 glob、多窗口、步长、分页、循环与非循环边界、pin 和 hook。检查整个 group 切换、子对象独立开关、隐藏祖先以及该祖先下候选外部 siblings 的可见性。停止后比较原对象可见性与原有按键；不能只看控制器返回值。

对 `pp_nav objects` 和 `pp_nav groups` 实际点击视图区，再在空命令行按 Left/Right、Ctrl+Left/Right；命令行输入文字后按键应编辑光标。确认 `keys=0` 不绑定、`pp_nav stop` 恢复；Annotator Alt+Left/Right 切自己的条目。不要把普通 headless 测试当成 Qt 焦点和按键证据。

已提供的本轮证据为 `output/06_arrow_navigation.json` 与同名 PNG、`output/07_navigation_groups.json`：分别记录真实 Qt 键盘与 group 容器/隐藏祖先检查；`output/06_arrow_unit.log` 记录 89 项相关单测。结果和适用版本以各文件及 [本轮记录](validation/02_toolkit_unification_2026-11-06.md) 为准。

## 验收结构导入与标注

1. 使用真实 CSV/TSV、结构和独立 sidecar，检查结构列、重复路径独立行、缺失文件、预览、筛选、排序及空结果。
2. 检查单选、多选、必填、草稿、单行/批量保存、Ctrl+click、撤销、Later、继续、导出和换源。比较原表与结构内容；导出使用新路径。
3. 验证 `review.json`、`review.override.json`、Review steps、fixed 对象与 scene groups；固定对象复用，借用对象不被清理，命令新建产物按候选归属。
4. 在目录扫描、header 和远端读取中检查 UI 心跳、取消、关闭与迟到结果。真实 SSH 检查应使用默认配置 URI、实际登录程序和真实源；明确区分注入慢 I/O 与真实远端耗时。
5. 在输入框和补全弹窗分别测试 Tab、Return、数字键盘 Enter；粘贴路径后立即导入，不得被默认历史候选覆盖。

批处理应预览准确 A/B、使用每对唯一临时名称，并在长序列验证固定 reference 只读、输出坐标、源文件 hash、失败报告与停止边界。退出码不足以证明这些检查；在 `cmd.quit()` 前写最终 JSON。

## 验收命令和科学输出

| 领域 | 核对内容 |
| --- | --- |
| 目标与显示 | exact/glob/fuzzy、多匹配保护、`pp_foreach commit=0` 预览、all-state 表示及精确 undo |
| 对齐与 follow | anchor/payload、真实对象 TTT、刚体变换、外部变化和 stale undo、callback CGO 策略 |
| 几何与膜 | PCA/对称性置信度、退化输入、膜厚和坐标系、ANVIL 输入身份 |
| 样式 | 全局/目标设置影响、snapshot/revert、显式 render；检查实际图像与许可水印 |
| interaction | 精确两方、state、已有 selection 名称/成员/开关、受管输出及 clear、失败替换与旧异步结果 |
| worker | 真实服务健康、认证、明确 backend、超时与取消边界；不把 mock 当实际 PLIP/PyRosetta |

测试对用户可见的预览、错误和下一步；检查输入对象、坐标、设置与非 owned 输出未被误改。记录每个环境未覆盖项。历史完整 ID、夹具和预期标准保留在 [详细验收参考](08_reference_windows_acceptance_2026-11-06.md)。

## 验收窗口与布局

在真实异步保存、准备和批处理期间切换嵌入/浮动，检查关闭 veto、重新打开、位置宽度及仅恢复插件拥有的约束。实际 dock owner 使用 `pymol.gui.get_qtwindow()`；浮动内容通过其 `window()` 验证激活。下拉菜单按钮需点击 menu-arrow 子控件，undo 从明确启用的基线开始。

比较主要动作与实际 Windows taskbar 矩形；auto-hide 时 `availableGeometry()` 和 `rcWork` 可能包含可见任务栏。展开/收起字段而不改变宿主尺寸；分别检查表头和数据 viewport。用 `ensureVisible` 检查完整输入 bounds，等待模态窗口和真实 completion popup。QGLWidget 对比前显式重绘，完整窗口截图用原生屏幕捕获；独立 harness 持有 QApplication 到 JSON 写完。

## 汇总证据

逐项列出通过、失败、阻塞和未运行；保留原始失败而非改写成最终通过。按需要链接 JSON、日志、截图及输入/输出哈希。`output/09_package_checks.log` 是已提供的 43 项规定检查结果；它不验证后续代码改动。最终候选 ZIP 和更新后的键盘验收由发布负责人确认。更多历史结果见 [历史索引](03_history_index.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="2026-09-29-历史交付"></a>
- [2026-09-29 历史交付](08_reference_windows_acceptance_2026-11-06.md#2026-09-29-%E5%8E%86%E5%8F%B2%E4%BA%A4%E4%BB%98)
<a id="历史检查边界"></a>
- [历史检查边界](08_reference_windows_acceptance_2026-11-06.md#%E5%8E%86%E5%8F%B2%E6%A3%80%E6%9F%A5%E8%BE%B9%E7%95%8C)
<a id="runtime-boundary"></a>
- [Runtime boundary](08_reference_windows_acceptance_2026-11-06.md#runtime-boundary)
<a id="test-record-header"></a>
- [Test record header](08_reference_windows_acceptance_2026-11-06.md#test-record-header)
<a id="1-bootstrap-and-capability-baseline"></a>
- [1. Bootstrap and capability baseline](08_reference_windows_acceptance_2026-11-06.md#1-bootstrap-and-capability-baseline)
<a id="2-required-fixtures"></a>
- [2. Required fixtures](08_reference_windows_acceptance_2026-11-06.md#2-required-fixtures)
<a id="3-navigation-nav-"></a>
- [3. Navigation (NAV-*)](08_reference_windows_acceptance_2026-11-06.md#3-navigation-nav-)
<a id="objectgroup-target-iteration-target-"></a>
- [Object/group target iteration (TARGET-*)](08_reference_windows_acceptance_2026-11-06.md#objectgroup-target-iteration-target-)
<a id="tier-1-task-facades-task-"></a>
- [Tier-1 task facades (TASK-*)](08_reference_windows_acceptance_2026-11-06.md#tier-1-task-facades-task-)
<a id="4-structure-annotator-persistence-anno-"></a>
- [4. Structure Annotator persistence (ANNO-*)](08_reference_windows_acceptance_2026-11-06.md#4-structure-annotator-persistence-anno-)
<a id="csv-score-table-path-mapping-and-cache-anno-csv-"></a>
- [CSV, score table, path mapping, and cache (ANNO-CSV-*)](08_reference_windows_acceptance_2026-11-06.md#csv-score-table-path-mapping-and-cache-anno-csv-)
<a id="5-object-matrices-and-group-follow-follow-"></a>
- [5. Object matrices and group follow (FOLLOW-*)](08_reference_windows_acceptance_2026-11-06.md#5-object-matrices-and-group-follow-follow-)
<a id="6-sequence-selection-and-rigid-alignment-seq--align-"></a>
- [6. Sequence selection and rigid alignment (SEQ-*, ALIGN-*)](08_reference_windows_acceptance_2026-11-06.md#6-sequence-selection-and-rigid-alignment-seq--align-)
<a id="7-geometry-and-membrane-geo--mem-"></a>
- [7. Geometry and membrane (GEO-*, MEM-*)](08_reference_windows_acceptance_2026-11-06.md#7-geometry-and-membrane-geo--mem-)
<a id="8-styles-style-"></a>
- [8. Styles (STYLE-*)](08_reference_windows_acceptance_2026-11-06.md#8-styles-style-)
<a id="9-interactions-and-worker-int--ros-"></a>
- [9. Interactions and worker (INT-*, ROS-*)](08_reference_windows_acceptance_2026-11-06.md#9-interactions-and-worker-int--ros-)
<a id="local-canonical-presets"></a>
- [Local canonical presets](08_reference_windows_acceptance_2026-11-06.md#local-canonical-presets)
<a id="wsl-worker"></a>
- [WSL worker](08_reference_windows_acceptance_2026-11-06.md#wsl-worker)
<a id="10-performance-and-lifecycle"></a>
- [10. Performance and lifecycle](08_reference_windows_acceptance_2026-11-06.md#10-performance-and-lifecycle)
<a id="11-result-summary"></a>
- [11. Result summary](08_reference_windows_acceptance_2026-11-06.md#11-result-summary)

</details>
