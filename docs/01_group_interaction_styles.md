# Group、interaction 与绘图命令

本页介绍 0.17.0 候选版如何整组浏览、标注、查看相互作用和应用样式；该版本尚未正式发布。安装后在 PyMOL 运行 `pp_load`；`pp_help` 查常用任务，`pp_help grouping` 查分组命令，`help 命令名` 查完整参数。

## 整组查看与标注

已有一组候选，每个候选含蛋白、配体、接触线或 CGO 时，把各候选组放在一个父组下：

```pymol
group candidates, candidate_1 candidate_2 candidate_3
pp_group info, candidates
pp_review_groups candidates, review_path=D:/reviews/scene-review.json
```

Structure Annotator 将 `candidates` 的**直接子 group**作为候选。Next/Previous 切换整组，距离对象和标签跟随组可见性。打开已关闭父组下的候选时，会恢复其祖先组可见性。`pp_review_groups` 不复制结构；标注保存在指定 JSON manifest 旁的 `.annotations.jsonl`。再次使用前先加载原 PSE，再运行相同命令；组名是匹配身份，重命名或换一套候选后使用新的 review 路径。

GUI 入口是 Annotator 的 **More → Review loaded scene groups…**。普通文件/CSV 候选默认与 review 命令新生成的配体、接触线、标签等一起放入组。固定参考对象及其派生产物仍按固定步骤复用。开关可用 **Group candidate and created objects** 或 `sa_group_candidate 0` / `sa_group_candidate 1`（打开 Annotator 后可用）。已存在的用户对象不因被引用而成为插件可删除的资产。

每候选新建对象应使用唯一名称，例如 step command 中写 `distance contacts_{index}, {object} and index 1, {object} and index 2`。保留多个候选时，直接复用另一个候选已经持有的派生产物名会被拒绝，并提示用 `{index}` / `{object}` 区分，避免接触线仍属上一组。自定义 Python 命令仍由调用者明确控制对象命名和修改范围。

只查看、不标注时，继续使用已有导航：

```pymol
pp_nav groups, "candidate_*"
pp_nav next
pp_nav prev
pp_nav stop
```

`pp_nav objects, "model_*"` 同样支持 glob 匹配。高层 `pp_nav objects` / `pp_nav groups` 默认绑定方向键：Left/Right 查看上一个/下一个，Ctrl+Left/Right 翻页；`pp_nav stop` 恢复原快捷键和导航前状态。启动时加 `keys=0` 可不绑定。底层 `pp_nav_start` 的 `bind_keys` 默认是 0。Annotator 的 Alt+Left/Right 是另一组快捷键。

点击 PyMOL 视图区后，焦点通常回到命令行：命令行为空时方向键继续导航；已有文字时方向键保留文本光标编辑。Annotator 的 Alt+Left/Right 切换审阅条目，不与裸方向键混用。

组切换保留各子对象的独立开关，并启用查看当前组所需的祖先；原本隐藏的祖先下，候选之外的兄弟对象不会因此意外显示。

常用分组操作：

```pymol
pp_group_by_name all, method=split_lastn_sep, sep=_, prefix=review_
pp_group info, review_sample
pp_group disable, review_sample
pp_group enable, review_sample
```

`pp_group enable/disable` 改变整个容器的开关，保留各子对象自己的开关。`pp_group_by_name ... replace=1` 清空旧的成员关系后重组，不删除源结构。不要用 PyMOL 的 `delete group_name` 代替取消分组，它会递归删除组内对象。

## Interaction 不留下命名 selection

推荐从 `pp_contacts` 开始；明确指定对象和角色，避免多对象场景中的歧义：

```pymol
# 蛋白链间
pp_contacts complex, focus=chain A, partner=chain B, preset=interchain_basic

# 配体口袋
pp_contacts complex, focus=resn LIG, partner=polymer, preset=ligand_quick

# 来自不同对象、即使链名相同，也可以明确指定两方
pp_interact_run polar_qc, source=(model_a or model_b), partner1=model_a, partner2=model_b
```

默认不创建 partner selections，并保留已有 selection 的名称、成员和启用状态。用于显示的 measurement、显示副本及受管 group 仍会生成；原始 partner 表达式保存在结果数据中。

需要稳定结果名称、导出或清理时：

```pymol
pp_interact_run ligand_quick, source=complex, ligand=resn LIG, receptor=polymer, group=my_contacts
pp_interact_export my_contacts, D:/reviews/contacts.json
pp_interact_clear my_contacts
```

`pp_contacts` 结果可使用输出提示中的 `pp_undo`；直接 `pp_interact_run` 结果使用 `pp_interact_clear`。空、重叠或超出 source 的 partner 会明确报错。`state=1` 是默认状态，分析另一状态请显式设置 `state=2`。本地极性接触与几何距离用于结构检查，不等于完整能量判定；PLIP 仍需原来的外部 worker。

## 绘图与撤销

新增 6 个样式，来自用户维护的 PyMOL preset，纳入原有统一 registry：

| 名称 | 用途 |
| --- | --- |
| `clean_outline` | 白底、正交投影和黑色轮廓 |
| `ao_cartoon` | 通过环境遮蔽表达深度 |
| `soft_glossy` | 柔和高光和圆润 cartoon |
| `monochrome_bw` | 黑白线稿渲染 |
| `goodsell_flat` | 平面色块与轮廓 |
| `ligand_outline` | 配体 sticks、接触线和标签尺寸 |

```pymol
pp_style_list
pp_style_describe clean_outline
pp_style_apply clean_outline, complex
pp_style_revert
```

样式应用不自动渲染，也不自动选择科学角色或改变相机朝向。灯光、背景、投影和几何默认值是全局设置，`pp_style_describe` 会列出影响范围；`pp_style_revert` 按后进先出恢复。需要 PNG 时显式运行：

```pymol
pp_style_render 1600, 1200, D:/reviews/figure.png
```

## Tab 补全

统一补全从实际函数签名获取参数名；命令的对象、selection、group、样式、interaction preset、布尔值、枚举和路径使用对应候选。新建或删除场景对象后，下次 Tab 自动更新。本轮候选版验收覆盖 75 个 canonical、8 个兼容命令和 24 个 Annotator 命令；具体范围见 [验收记录](validation/02_toolkit_unification_2026-11-06.md)。

```text
pp_style_apply clean_<Tab>
pp_style_apply preset=ligand_out<Tab>
pp_contacts complex, pres<Tab>            → preset=
pp_contacts complex, preset=ligand_q<Tab>  → preset=ligand_quick
pp_group info, cand<Tab>
sa_annotate Goo<Tab>                      → Good（默认标签）
```

PyMOL 原生命令行按逗号和当前 token 做补全。本功能不解析任意复杂选择表达式，也不会推断科学上的配体/受体角色；带空格或逗号的完整表达式仍需正确加引号。数值参数保留自由输入，并提供参数名和已有默认值。
