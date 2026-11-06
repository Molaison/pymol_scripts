# 用 PyMOL 命令完成常见任务

本指南让你在 PyMOL 控制台练习显示、对象和 group 导航、对齐、方向、样式和接触。先安装插件；使用一次性场景，避免示例对象名与现有对象冲突。命令中的 `walk_*` 在下方创建，`YOUR_*` 是必须替换成真实数据的占位符。

## 加载工具并创建练习对象

```pymol
pp_load
pp_doctor check_worker=0
pp_help
fab ACDEFGHIK, walk_a
create walk_b, walk_a
create walk_c, walk_a
create walk_d, walk_a
translate [12,0,0], walk_b
translate [24,0,0], walk_c
translate [36,0,0], walk_d
group walk_root_a, walk_a
group walk_root_b, walk_b
group walk_root_c, walk_c
group walk_root_d, walk_d
```

对象面板应出现四个 group 和对应结构。`pp_help` 按任务显示入口；例如 `pp_help navigate` 查看导航、`pp_help "sequence selection"` 查看序列选择。`pp_doctor` 报告命令归属和环境；可选 worker 不可用不应阻止本地工具加载。

## 改变显示并撤销

```pymol
pp_show surface, walk_root_a, preview=1
pp_show surface, walk_root_a
pp_undo
```

预览不修改场景；第二条对 group 内分子显示表面，`pp_undo` 恢复受管操作。group 中非分子成员不会被当作分子表示目标。`pp_undo list` 查看记录；对已被外部改变的状态，撤销可能因过期检查而拒绝。

## 按对象执行命令

```pymol
pp_foreach walk_b, "show spheres,{target}", commit=0
pp_foreach walk_b, "show spheres,{target}"
pp_foreach "walk_root_*", "show sticks,{target}", match=glob
```

第一条只预览；第二条实际执行一次；第三条对匹配 group 中的分子对象逐项执行，不是逐原子执行。任意命令模板没有通用撤销，先用 `commit=0` 检查，完成后自行执行适当的逆操作。模糊匹配可先用 `pp_target_resolve` 检查；不明确的多匹配不会静默批量执行。

## 浏览对象和 group

```pymol
pp_nav groups, "walk_root_*", window=2, page=2, wrap=0
pp_nav next
pp_nav next-page
pp_nav stop
```

开始后每个 group 作为一个候选，`window=2` 显示两个，`page=2` 每页移动两个，`wrap=0` 到边界停止。`pp_nav objects, "walk_*"` 可按对象浏览；名称和 glob 均可用。`window`、`page` 等是启动选项，不能附在 `next` 上。

默认 Left/Right 前后切换，Ctrl+Left/Right 翻页；点击视图区后命令行为空时仍可使用，有文字时方向键用于编辑文本。启动命令加 `keys=0` 可关闭绑定。`pp_nav stop` 恢复原快捷键和原可见性；Annotator 的 Alt+Left/Right 独立切换审阅条目。组内原本关闭的子对象保持关闭；开启隐藏祖先时不会意外显示祖先下的外部兄弟对象。

## 选择序列并对齐

```pymol
pp_select_sequence walk_a, CDE, selection_name=walk_motif, occurrence=unique
create align_mobile, walk_a
translate [8,5,2], align_mobile
pp_align align_mobile, walk_a, motif=CDE, preview=1
pp_align align_mobile, walk_a, motif=CDE
pp_undo
```

选择结果应覆盖唯一的 CDE motif。预览显示匹配和拟合信息但不移动对象；实际对齐移动完整 mobile，`pp_undo` 恢复。`motif` 和 `ligand` 不能同时指定；匹配不唯一时先明确选择。复杂链、插入码及配体对称性仍应使用自己的真实结构核对。

## 确定方向并绘制膜平面

```pymol
pp_orient walk_a, preview=1
pp_orient walk_a
pp_undo
pp_membrane_slab selection=walk_a, axis_source=pca, name=walk_membrane, thickness=30
```

PCA 预览给出轴与置信信息；实际操作将主轴朝 Z，撤销恢复变换。膜平面按轴中心、方向和厚度绘制，不代表已验证的真实膜位置。cyclic 轴需要真实对称复合物；低置信度先人工检查。ANVIL 需要用户自行提供可用程序或已有输出；Windows Host 不能把 WSL 路径当作 Windows 可执行文件。

## 跟随移动 group

```pymol
create walk_follow_driver, walk_a
create walk_follow_peer, walk_a
group walk_follow_group, walk_follow_driver walk_follow_peer
pp_follow_arm walk_follow_driver, group=walk_follow_group
```

在 PyMOL object-movement 模式移动 driver，然后执行：

```pymol
pp_follow_apply
pp_follow_status
pp_follow_undo
pp_follow_stop
```

peer 跟随 driver 的刚体变化并保持相对位置；undo 恢复 arm 时的基线，stop 释放会话。没有移动时应显示无变化。callback-owned CGO 默认跳过；原子坐标编辑不等于对象变换。

## 应用样式并查看接触

```pymol
pp_style_describe clean_outline
pp_style_apply clean_outline, walk_a
pp_style_revert
```

`clean_outline` 是 0.17.0 候选样式。describe 显示全局与目标影响；apply 默认不渲染，revert 恢复最近的样式快照。可用 `pp_style_list` 查看已安装版本的样式。指定真实蛋白—配体对象后可预览和运行接触：

```pymol
pp_contacts YOUR_COMPLEX, focus="YOUR_LIGAND_SELECTION", preview=1
pp_contacts YOUR_COMPLEX, focus="YOUR_LIGAND_SELECTION"
pp_undo
```

预览不创建接触结果；实际运行生成受管显示对象，可按输出提示撤销。canonical interaction 默认不新增 partner 命名 selection；几何接触不等同能量结论。PLIP 和 PyRosetta 是可选后端，[worker 指南](wsl_worker.md) 说明准备和取消边界。

## 清理练习场景

确认只清理本指南创建的对象后，停止仍在运行的导航或 follow，再删除练习对象。若命名冲突或操作失败，先检查场景，不要盲目执行宽泛删除。完整的逐项输入、预期输出和 Host 检查保留在 [命令详细参考](05_reference_tasks_2026-11-06.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="0-加载基础帮助与常见-alias"></a>
- [0. 加载、基础帮助与常见 alias](05_reference_tasks_2026-11-06.md#0-%E5%8A%A0%E8%BD%BD%E5%9F%BA%E7%A1%80%E5%B8%AE%E5%8A%A9%E4%B8%8E%E5%B8%B8%E8%A7%81-alias)
<a id="1-建立一次性本地夹具"></a>
- [1. 建立一次性本地夹具](05_reference_tasks_2026-11-06.md#1-%E5%BB%BA%E7%AB%8B%E4%B8%80%E6%AC%A1%E6%80%A7%E6%9C%AC%E5%9C%B0%E5%A4%B9%E5%85%B7)
<a id="2-表示方式group-展开与精确-undo"></a>
- [2. 表示方式、group 展开与精确 undo](05_reference_tasks_2026-11-06.md#2-%E8%A1%A8%E7%A4%BA%E6%96%B9%E5%BC%8Fgroup-%E5%B1%95%E5%BC%80%E4%B8%8E%E7%B2%BE%E7%A1%AE-undo)
<a id="3-objectgroup-级-iteratepp_foreach"></a>
- [3. object/group 级 iterate：pp_foreach](05_reference_tasks_2026-11-06.md#3-objectgroup-%E7%BA%A7-iteratepp_foreach)
<a id="4-group-批量-navigation"></a>
- [4. group 批量 navigation](05_reference_tasks_2026-11-06.md#4-group-%E6%89%B9%E9%87%8F-navigation)
<a id="5-sequence-motif-选择与-motif-alignment"></a>
- [5. sequence motif 选择与 motif alignment](05_reference_tasks_2026-11-06.md#5-sequence-motif-%E9%80%89%E6%8B%A9%E4%B8%8E-motif-alignment)
<a id="6-pcacyclic-orientation"></a>
- [6. PCA/cyclic orientation](05_reference_tasks_2026-11-06.md#6-pcacyclic-orientation)
<a id="7-主动-group-followarm--鼠标移动--apply--undo"></a>
- [7. 主动 group follow：arm → 鼠标移动 → apply → undo](05_reference_tasks_2026-11-06.md#7-%E4%B8%BB%E5%8A%A8-group-followarm--%E9%BC%A0%E6%A0%87%E7%A7%BB%E5%8A%A8--apply--undo)
<a id="8-pca-轴-membrane-slab"></a>
- [8. PCA 轴 membrane slab](05_reference_tasks_2026-11-06.md#8-pca-%E8%BD%B4-membrane-slab)
<a id="9-style-describeapplyrenderrevert"></a>
- [9. style describe/apply/render/revert](05_reference_tasks_2026-11-06.md#9-style-describeapplyrenderrevert)
<a id="10-contactspreviewreplacement-与-async-undo"></a>
- [10. contacts、preview、replacement 与 async undo](05_reference_tasks_2026-11-06.md#10-contactspreviewreplacement-%E4%B8%8E-async-undo)
<a id="11-rosetta-metrics-与-wsl-worker"></a>
- [11. Rosetta metrics 与 WSL worker](05_reference_tasks_2026-11-06.md#11-rosetta-metrics-%E4%B8%8E-wsl-worker)
<a id="12-structure-annotatordirectory-与-csv"></a>
- [12. Structure Annotator：directory 与 CSV](05_reference_tasks_2026-11-06.md#12-structure-annotatordirectory-%E4%B8%8E-csv)
<a id="13-最终清理与证据边界"></a>
- [13. 最终清理与证据边界](05_reference_tasks_2026-11-06.md#13-%E6%9C%80%E7%BB%88%E6%B8%85%E7%90%86%E4%B8%8E%E8%AF%81%E6%8D%AE%E8%BE%B9%E7%95%8C)

</details>
