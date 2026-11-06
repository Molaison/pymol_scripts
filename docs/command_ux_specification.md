# 维护命令帮助与任务体验

修改 PyMOL 命令、帮助、补全或错误提示时，用本页确定用户应如何发现、执行和恢复操作。先确认当前 manifest、实际签名和受影响的 Host 边界；提交前按下方流程核对可复制示例与返回结果。原始评分表、逐项 P0 回归和历史范围保留在 [详细规格](11_reference_command_ux_2026-11-06.md)。

## 修改前确定用户任务

1. 写出用户要完成的任务、实际输入、会改变的对象和最短成功路径。
2. 查 `pp_help`、命令签名、manifest 和已有 canonical owner；优先调用已有状态机，不建立第二个导航游标、缓存、变换或 interaction 模型。
3. 区分无需 mutation 的预览、真实执行、撤销/清理和不支持的操作；不把历史提案当作已交付能力。

默认帮助按任务列出不超过 12 个推荐入口，再提供类别和高级原语。原规范要求常用任务发现加执行不超过三条命令，推荐入口最多四个必需参数；具体 scientific 参数放在明确 preset 或高级帮助。

## 编写帮助和结果

`pp_help` 应能处理任务词、类别、preset、精确或模糊命令名；同一概念使用相同术语。帮助说明目的、最小语法、真实示例、场景影响、撤销和后续诊断。机器返回保持现有字典 schema，不为文案变化破坏调用者。

预览应说明没有改变，并给出实际执行方式；成功结果说明受影响目标和可用 undo/clear/stop。预期输入错误应指出错误参数、允许形式及修复方向，并保留旧状态；traceback 可供调试，不能是唯一解释。支持 `quiet=1` 的命令只抑制人类输出，不改变返回合同。

`pp_nav objects` / `groups` 默认 Left/Right 前后、Ctrl+Left/Right 翻页；空命令行接收导航，有文字时保留光标编辑；`keys=0` 禁用绑定，`pp_nav stop` 恢复原快捷键与可见性。Annotator 的 Alt+Left/Right 是独立操作。修改帮助时同时核对任务和命令条目及搜索词。

## 验证用户路径

按发现、成功步骤、参数清晰度、结果可预测性、错误恢复和科学用途各评 1–5 分；原合同要求结果可预测性和错误恢复至少 4 分，推荐任务所有维度至少 4 分。检查这些路径：安装与 Host/WSL 边界；文件/对象/group 导航；目标迭代；表格导入、标注、撤销与导出；序列/配体对齐；轴、膜、跟随；样式；相互作用；可选 worker。

用真实 PyMOL `cmd.do` 检查引号、布尔、路径、选择表达式与实际注册；Qt 和场景行为用真实 Host，普通 Python 测试不能替代。检查空查询、未知查询、中文/英文别名及合理拼写错误；无关查询应得到 `no_match`。模糊匹配不能静默执行有歧义的破坏性操作。验收具体清单见 [Windows Host](windows_host_acceptance.md)。

## 区分命令默认值

任务级动作通常直接执行，支持的非破坏预览使用 `preview=1`；高级科学原语可有各自预览/提交合同。`pp_foreach` 的预览是 `commit=0`。自动识别只能在类型或目标无歧义时进行，多匹配应要求明确选择或显式 all-matches。所有示例中的对象与路径须说明前提，不能宣称占位文件存在。

历史 P0 重现、原始命令 UX 评分、版本特定的命令数量与未通过 Host 门禁均在 [详细规格](11_reference_command_ux_2026-11-06.md) 中按当时记录保留；当前证据见 [历史索引](03_history_index.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="1-baseline-problem"></a>
- [1. Baseline problem](11_reference_command_ux_2026-11-06.md#1-baseline-problem)
<a id="2-evaluation-rubric"></a>
- [2. Evaluation rubric](11_reference_command_ux_2026-11-06.md#2-evaluation-rubric)
<a id="3-progressive-disclosure"></a>
- [3. Progressive disclosure](11_reference_command_ux_2026-11-06.md#3-progressive-disclosure)
<a id="4-common-task-acceptance"></a>
- [4. Common task acceptance](11_reference_command_ux_2026-11-06.md#4-common-task-acceptance)
<a id="5-help-and-result-contract"></a>
- [5. Help and result contract](11_reference_command_ux_2026-11-06.md#5-help-and-result-contract)
<a id="6-parameter-and-naming-contract"></a>
- [6. Parameter and naming contract](11_reference_command_ux_2026-11-06.md#6-parameter-and-naming-contract)
<a id="7-bug-and-environment-gates"></a>
- [7. Bug and environment gates](11_reference_command_ux_2026-11-06.md#7-bug-and-environment-gates)
<a id="8-iteration-policy"></a>
- [8. Iteration policy](11_reference_command_ux_2026-11-06.md#8-iteration-policy)
<a id="9-reproduced-implementation-backlog"></a>
- [9. Reproduced implementation backlog](11_reference_command_ux_2026-11-06.md#9-reproduced-implementation-backlog)
<a id="10-tier-1-facade-specification"></a>
- [10. Tier-1 facade specification](11_reference_command_ux_2026-11-06.md#10-tier-1-facade-specification)
<a id="11-current-validation-boundary"></a>
- [11. Current validation boundary](11_reference_command_ux_2026-11-06.md#11-current-validation-boundary)

</details>
