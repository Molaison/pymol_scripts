---
target: View Color embedded residue controls
total_score: 29
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 0
target_identity: "file:D:\\SyncPath\\GitRepo\\PymolPlugins-reviewed-integration\\pymolplugins\\residues\\panel.py"
target_fingerprint: "sha256:9d7b99091e88b93eeab0a510c99321069df420c4f89fbb85777eaca472213495"
target_path: "D:\\SyncPath\\GitRepo\\PymolPlugins-reviewed-integration\\pymolplugins\\residues\\panel.py"
timestamp: 2026-11-26T02-33-18Z
slug: pymolplugins-residues-panel-py
---
Method: dual-agent (A: 01a215a7-2c75-71d3-af9f-018bb74f92e6 · B: 01a215a7-5e5e-74b2-b831-b97c2bd215dc)

审查目标为既有 PyMOL View → Color 内嵌残基控件，快照 6eb55c0；模式 Operate。两位代理互不读取对方发现，设计判断完成后父才读取检测证据。

| Nielsen 启发式 | 分数 | 主要依据 |
| --- | --- | --- |
| 系统状态可见性 | 3/4 | 对象和作用范围清楚，操作反馈会覆盖结果或滞留。 |
| 与真实世界相符 | 3/4 | SASA、RSA、Cα 术语符合结构分析场景。 |
| 用户控制与自由 | 3/4 | 折叠详情与共享撤销可见。 |
| 一致性与标准 | 3/4 | 复用原生 Qt、对象选择及 Color/Lighting。 |
| 错误预防 | 3/4 | 阈值交叉约束明确，辅助技术关联需补齐。 |
| 识别而非记忆 | 3/4 | 参数、对象和结果可在同页核对。 |
| 灵活高效 | 4/4 | 五种配色、复制、选择及 CSV 导出。 |
| 美观与简约 | 3/4 | 默认态克制，展开后表格可横向滚动。 |
| 错误诊断与恢复 | 2/4 | 有错误和撤销反馈，恢复指引有限。 |
| 帮助与文档 | 2/4 | 有图例和 tooltip，参数说明并不完整。 |
| 合计 | 29/40 | 本轮修复前快照，不作为修复后评分。 |

设计判断：保留现有 PyMOL 工具的视觉语言。对象范围、阈值、计数和图例的关系清楚；折叠数值/导出符合先调色、再核查数据的使用顺序。蓝色主按钮与表格密度未被证明阻碍任务，不按偏好重新设计。

独立证据：B 运行一次 bundled detector，退出 0、stdout []。该工具仅支持 HTML/CSS，未报告 Python 文件扫描数，因此不能把零发现当作 Qt 检测通过。原生截图和实际宿主终态 JSON 是本次视觉依据；没有浏览器 overlay 或屏幕阅读器实测。

三项 UI 修复优先级均为 P2：

1. State、Core below、Surface from 等行内标签未显式绑定输入。给数值框和滑块设置可区分的可访问名称，并关联 QLabel buddy。读屏实际播报未测。
2. Copy layers、Create selections、导出成功会改写分析计数与图例。保留结果摘要，把操作反馈送到 View 已有状态区。
3. 应用一种 Palette 后选择另一种，旧的“applied”消息仍保留。选择变化应清除这条过期确认。

Persona 风险：Alex 复制分层定义后丢失结果语境；Sam 浏览行内输入时缺少显式语义关联。原生控件和完整文字按钮是两者都应保留的优点。

Questions skipped: 用户已明确要求所有审查问题修完后合入 main，修复范围和优先级已有授权，不再重复询问。
