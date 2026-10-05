# PymolPlugins 逐项验收 walkthrough

这份流程用于 Windows Host PyMOL 中的人工验收。仓库可以位于 WSL，但下面的
PyMOL 命令必须在 **Windows PyMOL 控制台**执行；WSL 单元测试不能替代这些
步骤。请使用一次性 PyMOL session，每完成一节再进入下一节。失败时保留完整
控制台输出，不要继续执行会掩盖现场的清理命令，先运行 `pp_doctor`。

命令中的 `walk_*` 都由第 1 节现场创建，不需要下载结构。`YOUR_*` 是明确的
外部数据占位符，必须替换成自己的真实对象、selection 或 Windows 路径，不能
原样执行。

## 0. 加载、基础帮助与常见 alias

```pymol
pp_load
pp_doctor check_worker=0
pp_help
pp_help "browse groups"
pp_help "sequence selection"
pp_help "move group"
pp_help "axis align"
pp_help "膜平面"
pp_help "ppi"
pp_help "发表图片"
pp_help "模糊匹配"
pp_help "contact molecular surface"
pp_help "buried unsatisfied residues"
pp_help jobs
pp_help aligment
pp_help 结构对其
pp_help foobar
```

通过条件：

- `pp_doctor` 中 72 个 canonical commands 均归当前插件所有；
- `browse groups`、`sequence selection`、`move group`、`axis align`、
  `膜平面`、`ppi` 和 `发表图片` 依次首先返回 `navigate`、
  `sequence motif`、`follow`、`orient`、`membrane`、`interactions` 和
  `style`；
- `模糊匹配` 返回 `pp_target_resolve`；`contact molecular surface` 与
  `buried unsatisfied residues` 都返回 `worker metrics`；
- `jobs` 首先返回 `pp_jobs`；`aligment` 与 `结构对其` 以
  `matched_by=fuzzy` 返回 `align`；
- `foobar` 返回 `no_match`，不会给出看似可信但无关的命令；
- 详情包含 `Changes scene`、`Undo` 和 `Next`。

清理：本节只读，不需要清理。

文件夹或 CSV 的批处理使用 `pp_batch`，支持固定参考、ID 模板、通配符和正则配对，
默认保存处理后的 A、B。输入示例、多步命令和结果约定见
[批处理使用说明](batch_processing_specification.md#usage)。它不要求预先加载结构，
与已加载对象的 `pp_foreach` 或多行标签批量保存是不同功能。

Host-only 边界：这里验收的是 Windows PyMOL 的 `cmd.do` 引号解析、控制台输出
和 completion 注册；直接调用 Python 函数或 WSL fake-cmd 通过不能替代本节。

## 1. 建立一次性本地夹具

```pymol
delete walk_*
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
create walk_follow_driver, walk_a
create walk_follow_peer, walk_a
translate [0,15,0], walk_follow_peer
group walk_follow_group, walk_follow_driver
group walk_follow_group, walk_follow_peer
```

通过条件：对象面板中出现四个 `walk_root_*` root groups，每组只有一个
molecule leaf；另有 `walk_follow_group`，包含 driver 和 peer 两个 molecule
leaves。`walk_a` 的序列是 `ACDEFGHIK`。

清理：这些夹具保留到最后一节；中途不要删除或更改 group membership。

Host-only 边界：后续步骤依赖真实 PyMOL object/group 类型和 object panel 状态，
因此不要用普通 Python 对象模拟本节。

## 2. 表示方式、group 展开与精确 undo

```pymol
pp_show surface, walk_root_a, preview=1
pp_show surface, walk_root_a
pp_undo preview=1
pp_undo
```

通过条件：preview 不改变画面或 journal；apply 只显示 `walk_a` 的 surface；
返回一个可复制的 `pp_undo op_...`；undo 恢复逐原子的原表示状态。

再建立一个非 molecule 对象并确认默认 `target=all` 不受它影响：

```pymol
distance walk_measure, walk_a and index 1, walk_b and index 1
pp_show cartoon, preview=1
```

通过条件：preview 成功，targets 只列 molecule objects/group molecule leaves，
不因 measurement/CGO/map 共存而整体失败。

清理：

```pymol
delete walk_measure
```

Host-only 边界：本夹具证明真实 molecule/group 类型过滤和单状态表示 round
trip。multistate 的每个 state、隐藏 object identity marker，以及删除后重建同名
对象的 undo 拒绝仍应按 Windows Host acceptance 的独立门禁执行。

## 3. object/group 级 iterate：`pp_foreach`

PyMOL 原生 `iterate` 是逐原子执行；这里的命令是一项一个 object/group target。
先对单个对象 preview，再实际执行：

```pymol
pp_foreach walk_b, "show spheres,{target}", commit=0
pp_foreach walk_b, "show spheres,{target}"
```

通过条件：第一条输出 `status=preview`、`executed_targets=[]`，画面不变；第二条
只执行一次，`executed_targets` 只有 `walk_b`。

再验证 group batch、显式 glob 和 fuzzy typo：

```pymol
pp_foreach "walk_root_*", "show sticks,{target}", match=glob
pp_target_resolve walk_rooot_a, match=fuzzy, kinds=group, limit=1
pp_foreach walk_rooot_a, "show mesh,{target}", match=fuzzy, kinds=group, limit=1
```

通过条件：glob 恰好执行四次，targets 是 `walk_a` 至 `walk_d`，而不是四个 group
名字或逐原子重复；fuzzy 首先解析到 `walk_root_a`，随后按默认
`group_mode=molecules` 只对 `walk_a` 执行一次。

最后确认隐式多匹配不会静默批量修改：

```pymol
pp_foreach walk_root, "disable {target}"
```

通过条件：返回 `ambiguous_auto_match`，明确要求 exact target、显式 set match 或
`all_matches=1`；所有对象仍保持原 enabled state。

清理：arbitrary template 没有通用 undo，必须执行已审核的逆操作：

```pymol
hide spheres, walk_b
hide sticks, walk_a or walk_b or walk_c or walk_d
hide mesh, walk_a
```

Host-only 边界：必须通过真实 STRICT parser 和 `cmd.do` same-line completion
marker 验证。WSL resolver 测试不能证明 quoted template 在 Host 中实际只执行
一次，也不能证明 PyMOL 吞掉的 parser/CmdException 被正确识别。

## 4. group 批量 navigation

```pymol
pp_nav groups, "walk_root_a,walk_root_b,walk_root_c,walk_root_d", window=2, page=2, wrap=0
pp_nav
pp_nav next
pp_nav next-page
pp_nav next-page
```

通过条件：初始显示两个完整 group entries；`next` 按一个 entry 前进，
`next-page` 按 page 前进；到非循环边界时顶层为 `unchanged`，不会重复运行
hooks。

参数防误用检查：

```pymol
pp_nav next, window=2
pp_nav stop
```

通过条件：第一条在移动前报错，明确 `window` 是 start-only，而不是静默忽略；
`stop` 恢复 navigation 接管前的可见性。

清理：正常路径已由 `pp_nav stop` 完成。只有 cleanup/visibility/key lease 失败并
确认可以放弃未恢复 ownership 后，才使用 `pp_nav stop, force=1`。

Host-only 边界：真实 enabled state、group atomicity、key lease 和 object panel
刷新都必须在 Windows Host 检查；纯 `NavigationSession` 测试不是场景证据。

## 5. sequence motif 选择与 motif alignment

先从自包含的 `walk_a` 选择唯一 `CDE` motif：

```pymol
pp_select_sequence walk_a, CDE, selection_name=walk_motif, occurrence=unique
```

通过条件：返回 `status=success`、`match_count=1`，创建 `walk_motif` selection；
匹配保留原 object/chain/resi atom identity，画面只把这三个 residues 显示为
sticks/color，而不是按连续 residue number 猜测。

用同一 motif 对齐完整 mobile object：

```pymol
create align_mobile, walk_a
translate [8,5,2], align_mobile
pp_align align_mobile, walk_a, motif=CDE, preview=1
pp_align align_mobile, walk_a, motif=CDE
pp_undo
```

通过条件：preview 返回唯一 motif anchors 和 fit diagnostics，但不移动
`align_mobile`；apply 移动整个 object，而不仅是 motif residues；统一 undo 使用
精确 alignment record 并恢复 apply 前的 object transform。

冲突输入必须在 delegate 前拒绝：

```pymol
pp_align align_mobile, walk_a, motif=CDE, ligand=LIG
```

通过条件：返回 motif/ligand mutually exclusive，scene preserved。

清理：

```pymol
delete walk_motif
delete align_mobile
```

Host-only 边界：selection atom identity、insertion/blank/multi-character chain、
altloc 和真实 object TTT 需要 Host fixture 逐项验证。本地 `fab` 只覆盖最小
unique exact motif 路径。

## 6. PCA/cyclic orientation

```pymol
pp_orient walk_a, preview=1
pp_orient walk_a
pp_undo
```

通过条件：无 target 的 `pp_orient` 会拒绝；preview 给出 axis/confidence 和
apply 提示；apply 后长轴指向 Z，undo 恢复 transform。

对真实 cyclic oligomer 才执行下面的占位命令：

```pymol
pp_orient YOUR_CYCLIC_OLIGOMER, axis=cyclic, preview=1
```

低置信度或非 cyclic 结果应默认拒绝，只有人工复核后才使用 `force=1`。

清理：最小 PCA apply 已由 `pp_undo` 恢复；不要对未 apply 的 preview 运行额外
undo。

Host-only 边界：本节需要真实 object TTT 和画面方向检查。`YOUR_CYCLIC_OLIGOMER`
必须替换成经过审阅的真实 cyclic assembly；本 walkthrough 不伪造高级对称性。

## 7. 主动 group follow：arm → 鼠标移动 → apply → undo

```pymol
pp_follow_arm walk_follow_driver, group=walk_follow_group
pp_follow_status
```

然后在 PyMOL **object-movement mouse mode** 中只旋转并平移
`walk_follow_driver`。不要移动 `walk_follow_peer`，随后运行：

```pymol
pp_follow_apply
pp_follow_status
pp_follow_undo
pp_follow_stop
```

通过条件：arm 解析出一个 follower；apply 后 peer 获得与 driver 相同的刚体
delta，二者内部坐标不变；status 的 `apply_count` 增加；undo 同时恢复 driver
和 peer 的 arm baseline；stop 只释放 session。未移动 driver 时 apply 应明确
`no_change`，不是伪造成功。

清理：正常路径已执行 `pp_follow_undo` 和 `pp_follow_stop`。若 apply 前就要退出，
直接运行 `pp_follow_stop`；它只忘记 baseline，不替用户移动对象。

Host-only 边界：真实鼠标 object movement 是本节核心，程序化
`set_object_ttt` 只能做算法测试，不能替代它。`matrix_mode=0/1/2`、static CGO、
measurement/map、callback-owned CGO 的 skip/rebuild policy 仍是单独 Host 门禁。

## 8. PCA 轴 membrane slab

```pymol
pp_membrane_slab selection=walk_a, axis_source=pca, name=walk_membrane, thickness=30
```

通过条件：返回 `status=drawn` 和 `ownership_id=geometry_membrane_...`；对象面板
出现 `walk_membrane` group 与 `walk_membrane__planes` CGO；画面显示两张平行
矩形，其间距为 30 Å，中心和 normal 来自 PCA 结果。

清理：

```pymol
delete walk_membrane__planes
delete walk_membrane
```

Host-only 边界：真实 CGO draw、透明度、group ownership marker、同名 user object
collision 和 replacement rollback 必须在 Host 验证。ANVIL 不属于自包含步骤；
只有已有真实输出时，才把下列两个占位符替换后执行：

```pymol
pp_membrane_from_anvil YOUR_ANVIL_OUTPUT_PDB, selection=YOUR_STRUCTURE_OBJECT, name=walk_anvil_membrane
```

## 9. style describe/apply/render/revert

```pymol
pp_style_describe macaron_default
pp_style_apply macaron_default, selection=walk_a
pp_style_render width=640, height=480
pp_style_revert
```

通过条件：describe 列出 global 与 target-scoped effects；apply 只应用样式并打印
精确 `Undo: pp_style_revert SNAPSHOT_ID`，不会隐式 ray；render 明确打印 viewer
render 尺寸；revert 按 LIFO 恢复 apply 前的 settings、atom colors 和
representations。

清理：正常路径已由 `pp_style_revert` 完成。不要在没有本 walkthrough snapshot
时反复运行无参数 revert，以免弹出并恢复用户更早的 snapshot。

Host-only 边界：真实颜色/settings scope、ray、PNG 和 visual quality 必须在
Windows Host 验证。若要写文件，只能把下面占位符换成已存在且可写的 Windows
目录：

```pymol
pp_style_render width=640, height=480, filename="YOUR_EXISTING_WINDOWS_OUTPUT_DIR/walk_style.png"
```

## 10. contacts、preview、replacement 与 async undo

`walk_*` 不含 ligand。请加载一份经过审阅的真实 protein–ligand complex，并把
`YOUR_COMPLEX` 和 `YOUR_EXACT_LIGAND_SELECTION` 替换后再执行：

```pymol
pp_contacts YOUR_COMPLEX, focus="YOUR_EXACT_LIGAND_SELECTION", preview=1
pp_contacts YOUR_COMPLEX, focus="YOUR_EXACT_LIGAND_SELECTION"
pp_undo
```

通过条件：preview 执行完整 partner/preset/group preflight，但不渲染、不写
manifest、不联网，并打印安全引用后的可复制 `Next`；local result 只创建
manifest-owned names，统一 undo 使用 `ppi-run:<run_id>:<group>` exact owner。

稳定 group replacement 检查：

```pymol
pp_contacts YOUR_COMPLEX, focus="YOUR_EXACT_LIGAND_SELECTION", group=ppi_review
pp_contacts YOUR_COMPLEX, focus="YOUR_EXACT_LIGAND_SELECTION", group=ppi_review
pp_undo list
pp_undo
```

通过条件：journal 中旧 run 已被 supersede，只有新 run 可撤销；旧 operation
绝不能清掉新结果；最后的 undo 清理当前 exact owner。

清理：使用本节返回的 `pp_undo OPERATION_ID`；不要用宽泛 `delete ppi*`。若一个
async result 已完成，则先 `pp_jobs JOB_ID` 找到 completion，再使用 completion
新增的统一 undo。

Host-only 边界：partner selection、PyMOL export identity、main-thread render、
replacement rollback 和 exact manifest clear 需要真实 Host complex。若使用
`preset=plip_overview`，submitted/failed/stale/cancelled job 都不得伪造 undo；
只有通过 stale/source validation 并渲染完成的 completion event 才新增 undo。

## 11. Rosetta metrics 与 WSL worker

无需 worker 的安全 preview，先把 `YOUR_COMPLEX` 换成恰好一个 molecule
object/selection，并把 A/B 换成其真实、非空且不重叠的 chain lists：

```pymol
pp_metrics YOUR_COMPLEX, target=A, binder=B, preview=1
```

通过条件：解析为恰好一个 molecule object，显示 chains/metrics/output plan，
但不导出 PDB、不访问 health/capabilities/HTTP。

准备好 WSL worker 和合法 PyRosetta 环境后：

```pymol
pp_metrics YOUR_COMPLEX, target=A, binder=B
pp_jobs
pp_jobs JOB_ID
```

通过条件：GUI 默认异步；提交路径不做同步 HTTP；完成或失败进入 bounded
history。`pp_jobs cancel, JOB_ID` 只表示抑制 Host result handling，不宣称终止
正在运行的 PyRosetta C++。

清理：metrics 默认不修改 scene。需要取消 Host result handling 时才运行
`pp_jobs cancel, JOB_ID`；不要把它当成 backend termination。

Host-only 边界：loopback Windows→WSL HTTP、Windows PyMOL structure export、
worker authentication 和 licensed PyRosetta 数值是独立门禁。preview 或 mock
结果不能证明 CMS/buried-unsat 数值正确。

## 12. Structure Annotator：directory 与 CSV

从 `Plugin > PymolPlugins: Structure Annotator` 打开面板。此节需要自己的外部
数据；把以下占位符替换为真实值：

- `YOUR_WINDOWS_DATASET_DIR`：含 PDB/CIF 的一次性 Windows 目录；
- `YOUR_SCORE_TABLE.csv`：含明确 structure-path/ID/score headers 的 CSV；
- `YOUR_SOURCE_PREFIX` 与 `YOUR_WINDOWS_MAPPED_ROOT`：仅在需要 WSL/SSHFS 路径
  映射时填写。

逐项执行：

1. Directory 模式选择 `YOUR_WINDOWS_DATASET_DIR`，设置 label、values 和
   On-step command。
2. 标注两项、undo 一次、关闭面板并重新打开。
3. 确认目录内隐藏 sidecar 恢复配置、cursor、annotations。
4. CSV 模式选择 `YOUR_SCORE_TABLE.csv`，指定 structure path、ID、display
   columns。
5. 如确有跨系统路径，为 `YOUR_SOURCE_PREFIX` 新增映射到
   `YOUR_WINDOWS_MAPPED_ROOT`；不要照抄别人的盘符或主目录。
6. preview CSV 后再 load；切换行时核对 score columns 与加载结构一致。
7. reimport/export，确认 annotation 值和稳定 row identity 未丢失。
8. 在任一输入框键入历史值的 typo，确认 fuzzy completion 可选择完整值。

通过条件：source structure/CSV bytes 不被原地改写；dataset-local state、全局
history、cursor 和 row annotations 在重开后恢复；preview 不污染旧 scene。

清理：只删除本节一次性目录中的 plugin-owned sidecar/export，并先保留验收
记录。不要删除原始 structure/CSV，也不要手工修改全局 history JSON。

Host-only 边界：当前 revision 的 history fields/static GUI bindings、Qt popup、
Windows `samefile`/盘符身份及 Windows→WSL staging 必须以 fresh Host Qt 运行
作为最终证据。

## 13. 最终清理与证据边界

```pymol
pp_nav stop, force=1
pp_follow_stop
delete walk_membrane__planes
delete walk_membrane
delete walk_motif
delete align_mobile
delete walk_*
```

`force=1` 只用于明确放弃无法恢复的 navigation ownership；正常完成第 4 节时
应显示 inactive。不存在的 `walk_*` 名称可安全忽略。style 与 contacts 应在各自
章节用精确 revert/undo 清理，不要在这里宽泛删除用户对象。

不要把本 walkthrough 的通过结果扩展为以下未运行门禁：PyMOL 2.x/PySide2、
三种真实 `matrix_mode` 鼠标 follow、multistate representation identity marker、
callback-owned CGO、licensed PyRosetta 数值、ANVIL executable、真实
Windows→WSL staging，或当前 harness 1.5.0 的完整 Windows Host smoke。
