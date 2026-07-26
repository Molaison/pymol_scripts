# PyMOL Plugins 统一体验改造 Specification

状态：核心 implementation、CSV Structure Annotator 与真实 PLIP 链路已完成；真实 ANVIL、合法 PyRosetta、PyMOL 2.x 与剩余手工交互验收仍开放
日期：2026-07-26
目标运行面：Windows Host PyMOL 2.x（Qt）
开发与外部计算面：WSL2

当前证据与未完成边界见
[implementation_status.md](implementation_status.md)。PyMOL 3.1.3.1 的自动
Host smoke、真实 Qt widget smoke、`K:` FUSE/cache、WSL staging 与 PLIP 3.0.0
数值/渲染链路已通过，但不替代 PyMOL 2.x、真实鼠标/callback、用户提供的
ANVIL，以及合法安装的 PyRosetta 数值验收。

## 1. 目标与边界

本次改造把仓库从一组彼此独立、会互相覆盖命令和状态的脚本，升级为一套可组合、可回滚、可批处理、可从 Windows PyMOL 调用 WSL 计算服务的结构检查工具。

必须完成的用户场景：

1. 文件、对象和 PyMOL group 都能按 batch/window 导航；group 是不可拆分的导航单元。
2. visualization styles 由一个 registry 管理，应用样式与 ray/render 分离。
3. interactions 提供 preset、确定的结果 schema、受控的 PyMOL 输出树和可导出数据。
4. 支持按关键对象、motif residues、sequence motif 和 ligand 对齐，并把同一刚体变换应用到明确指定的整个 complex/group。
5. 主动 arm 一个 driver object，记录基线；用户移动 driver 后，将其增量刚体变换应用到 group 中其余对象。静态 CGO 必须支持。
6. `select_by_sequence.py` 对 insertion code、空链、多字符链、缺失 CA、非标准残基和多命中可靠。
7. 提供 PCA 轴、cyclic oligomer 对称轴、指定向量定向，以及基于这些轴绘制 membrane slab；支持解析 ANVIL 输出。
8. Windows PyMOL 通过 HTTP 请求 WSL 中的 Rosetta/PyRosetta 服务，计算 CMS、两类 buried unsat，并为后续 dSASA、dG、SC、packstat 留出稳定协议。

本次不把以下内容伪装成已经完成：

- WSL 中的纯 Python/fake-cmd 测试不能替代 Windows Host PyMOL 的矩阵、Qt、鼠标和 CGO 集成测试。
- 没有 Rosetta/PyRosetta 合法安装源和许可证时，不下载、提交或再分发 PyRosetta 二进制。
- 不在许可证审查前 vendor ANVIL、MSMS 或 Rosetta 源码/二进制。
- 第一版不处理 dihedral、局部 atom editing 或非刚体形变的 group follow。
- 第一版 cyclic symmetry 聚焦 Cn；C2 需要单独的 subunit-pair 算法，高阶 Dn/T/O/I 不在自动识别范围内。

## 2. 当前仓库诊断

### 2.1 导航存在三套不兼容状态机

- `workflows/dataset_navigation/screen_boost.py` 是当前功能最完整的 file bucket/window 浏览器，但依赖全局变量、PyMOL object 顺序和 enabled 状态反推 cursor，并会 `disable all`。
- `workflows/dataset_navigation/file_management.py` 重复注册方向键、`pin`、`register_switch_cmd`、`next/prev` 等命令；`_show_current_structure()` 还会因缺少 `global _current_index` 在首次显示时报错。
- `plugins/structure_annotator.py` 有更明确的 `files/index/managed_objects`，但把导航、标注、预加载、Qt 和快捷键绑定在一个控制器中。

`structure_annotator` 不是完整的重复功能：annotation write/undo/export 属于独有 domain。重复的是文件索引、cursor、managed-object 生命周期和切换 hook。

### 2.2 PyMOL group 不是父变换节点

PyMOL group 只组织成员，不保存一个由 child 自动继承的 parent transform。移动一个 child 不会带动 siblings，这是 PyMOL 的对象模型语义。

PyMOL 每个 leaf object 有 TTT（translation-transformation-translation）矩阵：

```text
[R00 R01 R02 post_x]
[R10 R11 R12 post_y]
[R20 R21 R22 post_z]
[pre_x pre_y pre_z  1]

x' = R @ (x + pre) + post
```

静态 molecule、CGO、map、measurement 等 leaf object 都可使用通用 object TTT。callback-owned viewport overlay（例如 `axes_overlay.py`）会在 view 改变时主动重写自身 TTT，必须跳过或使用 rebuild hook。

### 2.3 styles 没有统一契约

- `macaron_style` 和 `pbs` 各被注册两次，最终签名由加载顺序决定。
- apply style 与立即 `ray` 混在一起，无法安全用于快速导航。
- RGB 同时混用 0..255 与 0..1。
- revert 只保存部分 settings，没有恢复 atom colors、representations、background 和 selection-scoped settings。
- `preset`、`minimal`、`next` 等通用命令名污染全局命令空间。

### 2.4 interactions 的错误来自选择边界和无数据契约

`ligand_surrounding.py` 先找“包含 ligand 的 molecule object”，随后用整个 object 计算 H-bond、surface 和 distance。蛋白与 ligand 同 object 时，结果会包含蛋白内部原子或覆盖整个 complex。

现有 interaction 工具普遍只有显示副作用，没有结构化 rows、manifest、精确 clear 或 run provenance。当前 PLIP 原型已经能画多类 interaction，但仍缺稳定 schema、timeout、异步 UI、精确清理和 Windows Host 到 WSL 的正确执行边界。

### 2.5 selection/alignment/geometry 有确定性错误

- `select_by_sequence.py` 会对 `42A` 执行 `int(resi)`；把空链伪装为 A；从 FASTA header 最后一个字符猜 chain；gap 使用无界贪婪 `.*`；命中再简化为 `resi start-end`。
- `align_by_ligand.py` 对 `cmd.alignto` 使用了错误签名；独立 ligand object 被移动时不会带动对应 complex；没有定义配体原子对应关系和对称歧义。
- `measure_inertia.py` 混合 covariance PCA、物理惯性和 surface inertia，存在 tuple 解包、缺 import、固定临时文件和 ChimeraX-only import 等运行错误。
- `select_interface_res.py` 的 residue SASA 会把 B-factor 反复覆盖为最后一个 atom 的值，且污染原对象。

### 2.6 WSL/Host 是两个运行面

- 当前 WSL Python 没有 `pymol`，真实 PyMOL/Qt/CGO 测试必须在 Windows Host 进程中执行。
- Windows PyMOL 不能直接启动 `/home/.../.venv/bin/python`，也不应接收 WSL 文件路径。
- Host 读取仓库脚本应使用 `\\wsl.localhost\<distro>\home\admin\gitrepo\PymolPlugins\...` 或安装到 Windows 可见目录。
- Windows long UNC 应转换为 `\\?\UNC\server\share\...`，不能简单加 `\\?\`。

## 3. 统一设计原则

### 3.1 计算与 PyMOL 副作用分离

纯数据/算法层不得 import PyMOL；PyMOL adapter 负责：

- 读取 atom/object/session 数据；
- 调用纯函数；
- 在 commit 时修改对象；
- 创建精确命名的显示对象；
- 保存 manifest 和 undo record。

### 3.2 anchor、move、display 三个边界分离

- anchor selection 只决定计算矩阵或 interaction。
- move objects 明确决定哪些对象被刚体变换。
- display preset 只决定如何显示结果。

任何 API 不得因为“selection 命中了某 object”就把整个 object 隐式当成 ligand、move payload 或 clear 范围。

### 3.3 默认 session-safe

- 不使用无作用域的 `disable all`、`hide everything`、`color all` 或 prefix wildcard delete。
- 只修改本 session 的 `managed_names` 或 manifest 精确记录的名称。
- 临时 setting 必须在 `finally` 恢复。
- destructive operation 提供 preview/dry-run、commit 和 undo。
- batch cursor 是 controller 的权威状态；只有显式 sync 才从 scene 反推。

### 3.4 命名空间与兼容

新 canonical commands 统一使用 `pp_` 前缀。旧命令保留 wrapper，但不再包含复制实现；过于通用的 legacy aliases 只保留一个迁移周期并打印一次 deprecation。

### 3.5 可复现输出

几何矩阵、轴、interaction、Rosetta metrics 都必须能序列化为 JSON，并携带输入 identity/hash、参数、backend/version、warnings 和 confidence。

## 4. 共享基础设施

### 4.1 参数解析

提供唯一实现：

```text
as_bool(value)
as_int(value)
as_float(value)
split_names(value)
sanitize_name(value)
```

`"0"`、`"false"`、`"off"` 必须为 false；未知 boolean token 必须报错，不能用 `bool(string)`。

### 4.2 ResidueRecord 与 sequence index

```text
ResidueRecord
  object
  state
  segi
  chain
  resi                 # 原字符串，如 -1、42A
  resv                 # 可选整数部分
  insertion
  resn
  one_letter
  atom_indices[]
  atom_names{}
```

同一 atom stream 同时生成 sequence 和精确 residue mapping，禁止再把 FASTA 与另一份 CA/resi 数组按位置拼接。

### 4.3 纯 Python 3D math

第一版不得为了 3x3/4x4 运算强制 Host 安装 SciPy/scikit-learn。共享内核提供：

- 4x4 multiply/inverse；
- TTT ↔ homogeneous；
- vector/dot/cross/normalize；
- symmetric eigen decomposition；
- Horn/Kabsch rigid fit；
- rotation from vector A to B，显式处理平行/反平行；
- transform points 和 RMSD；
- rotation validation：`R.T R≈I`、`det(R)≈1`。

### 4.4 OutputManifest

```text
OutputManifest
  schema_version
  run_id
  root_group
  created_names[]
  source_objects[]
  input_hash
  parameters
  result_path?
  stale_fingerprint?
```

clear 只能删除 `created_names` 中的精确名称。

### 4.5 Object/group target resolver

PyMOL 内置 `iterate` 保持逐 atom 语义；不得用它模拟 object/group 遍历。
canonical toolkit 单独提供：

```pymol
pp_target_resolve targets, match=auto, kinds="object,group",
                  group_mode=atomic, fuzzy_cutoff=0.72, limit=0
pp_foreach targets, command, match=auto, kinds="object,group",
           group_mode=atomic, commit=0, on_error=stop
```

所有 canonical `cmd.extend` callback 必须经过统一 console boundary：PyMOL
STRICT parser 保留的最外层单引号、双引号或三单引号语法只移除一层，不执行
escape decoding、`literal_eval` 或 `shlex`，以保持 UNC/Windows path、regex、
selection 和 command template 的内部文本不变；直接 Python 调用不经过此转换。

- match 支持 `exact|glob|contains|regex|fuzzy|auto`；返回实际名称、类型、
  match method 和 score，不静默丢弃歧义。
- group mode 支持 `atomic|leaves|both|molecules`；`leaves` 使用 partial-session
  parent metadata，递归包含 molecule、CGO、map 和 measurement。
- command template 提供 `{target}`、`{source}`、`{kind}`、`{object_type}`、
  `{index}`；preview 为默认，只有 `commit=1` 才调用 `cmd.do`。模板必须渲染为
  单个 physical parser line；允许 `;` 串联，但拒绝换行和 NUL。
- PyMOL 3.1 的 public `cmd.do` 对 parser/CmdException 成功与失败都返回
  `None`，private `_cmd.do` 也不是稳定 status API。每次执行须在同一 parser
  line 末尾追加唯一、quiet 的空 selection marker；只有 parser 完整执行前序
  command 才会创建 marker。通过 public `get_names/delete` 检查并立即精确清理，
  不解析 console 文本、不依赖 private API。失败写入 `errors`，并严格遵守
  `on_error=stop|continue`；console 保留 PyMOL 自己的详细错误。
- 任意 PyMOL command 无法通用回滚，所以 resolver 结果和渲染后的命令列表
  必须在执行前完整返回。

验收：

- `TARGET-01` exact/glob/contains/regex/fuzzy 均确定性排序、去重。
- `TARGET-02` nested group 的 leaf expansion 不遗漏非 molecule object。
- `TARGET-03` preview 不调用 `cmd.do`；commit 只执行返回列表中的命令。
- `TARGET-04` unrelated object 不受 group leaf iteration 影响。
- `TARGET-05` swallowed parser/CmdException 不能记为 executed；stop/continue
  数量准确，且 scene 中不残留 completion marker。

### 4.6 Package ownership

canonical implementation 只允许存在于 `pymolplugins/` 命名空间，并按
feature 垂直组织。`analysis/workflows/visualization/utilities/services/plugins`
不得被 canonical package 反向 import；它们只保留迁移 shim 或 Plugin
Manager entry。非 canonical 工具进入 `extras/`，历史参考进入 `archive/`。
架构测试以 AST 检查该单向依赖规则。

## 5. Specification：统一导航

### 5.1 数据模型

```text
NavigationEntry
  id
  kind                  # file | object | group
  display_name
  source_paths[]
  object_names[]
  group_name?
  metadata

NavigationSession
  entries[]
  cursor
  batch_start
  batch_size
  window_size
  stride
  wrap
  pinned_entry_ids
  managed_names
  visible_entry_ids
  zoom_policy
```

术语固定：

- batch：resident/load 范围；
- window：当前同时可见的 entry 数；
- group：PyMOL `object:group`；
- label：annotation bucket/value。

### 5.2 Commands

```pymol
pp_nav_start source=files|objects|groups, entries="", path="",
             batch_size=20, window_size=1, stride=1, wrap=1,
             bind_keys=0, zoom_policy=current
pp_nav_next
pp_nav_prev
pp_nav_next_batch
pp_nav_prev_batch
pp_nav_goto index
pp_nav_pin [entry]
pp_nav_unpin [entry]
pp_nav_status
pp_nav_sync_from_scene
pp_nav_stop
```

### 5.3 行为

- group mode 以 root group 为原子 entry；nested parent/child 不能同时成为 entry。
- group batch 第一版只控制显示，不删除用户已有成员。文件 manifest 模式才允许 unload，而且只处理 `managed_names`。
- pin 作用于完整 entry；group pin 保留整个 root group。
- navigation hooks 接收 `{entry, group, objects, index, batch, window}` 结构化 context。
- 快捷键只有 `bind_keys=1` 时注册；stop 恢复之前 binding。
- `screen_boost.py` 成为 compatibility adapter；`file_management.py` 变为 shim；annotator 复用 core，但保留 annotation controller；multi-state loader 保留独立用例。

### 5.4 验收

- `NAV-01` 空 entry、单 entry、最后不足一个 batch、wrap on/off 均不越界。
- `NAV-02` window/stride 的 forward/backward 对称；跨 batch 后得到完整最后 window。
- `NAV-03` group entry 中 molecule/CGO/map 一起显示，未纳入 session 的 overlay 不被关闭。
- `NAV-04` 新 session 不删除旧 session 或用户对象；load 失败回滚 cursor/managed state。
- `NAV-05` `screen_boost` 与 `file_management` 不再维护两份独立状态或覆盖不同实现。
- `NAV-06` PyMOL console string booleans 均按规范解析。

### 5.5 Structure Annotator 可恢复状态与全局历史

Structure Annotator 保留独立的 annotation write/undo/export domain，但配置
和 cursor 必须跨 PyMOL 会话恢复。每个成功加载的 dataset 原子写入：

```text
<directory-dataset>/.pymolplugins/structure_annotator.json
schema = pymolplugins.structure-annotator-state/2.0
```

dataset-local state 保存 source descriptor、regex pattern、label name、values、
`On step`、每个 bucket command、auto-advance/keep/prefer/skip/preload/cache
配置，以及最后一个 entry ID、相对文件名和 index。CSV source 使用同目录下按
CSV basename 隔离的 `structure_annotator.<csv-id>.json`，并额外保存 path/ID/
display columns、encoding/delimiter、mapping profile、relative base、WSL staging
root/alias、filter 和 sort。文件列表、PyMOL managed objects、materialized cache、
navigation session、annotated-index cache 和 undo stack 都是 runtime state，不得
序列化。数据集变化时先按稳定 entry ID 或相对文件名恢复；目标消失时才 clamp
到安全 index。version 1 directory state 可读取迁移，但新写入一律是 version 2。

跨 dataset 的最终配置另存为：

```text
~/.pymolplugins/structure_annotator_history.json
schema = pymolplugins.structure-annotator-history/2.0
```

全局历史同时维护完整 config snapshots 与字段级去重记录。字段包括
`dataset_dir`、`csv_path`、`source_mode`、`path_column`、`id_column`、
`display_columns`、`mapping_profile`、`pattern`、`label_name`、`values_text`、
`step_cmd` 和 `bucket_cmd`；候选综合 prefix、substring、subsequence、相似度、
使用次数和 recentness 排序。Qt 输入框在用户输入时动态显示 fuzzy candidates。
全局历史只在 source 成功 load、显式 `sa_save_state`、实际 annotation 使用配置
或关闭 panel 时记账；300 ms dataset-state debounce 不得把每个输入片段写成历史。

最新完整 snapshot 是新 PyMOL 会话寻找“上次 source”的指针。打开 panel
时先恢复 config，再在 `restore_last_dataset=true` 且 source 存在时调用相同的
`sa_load_dir` 或 `sa_load_csv` 恢复 entries 和 cursor；UI checkbox 可主动关闭
自动加载。测试或多 profile 部署可用 `PYMOLPLUGINS_ANNOTATOR_HISTORY` 覆盖
全局路径。

两类 JSON 都必须 versioned、bounded、UTF-8 和 temp-file + fsync +
`os.replace` 原子写入。Windows 除 dot-directory 外还设置 hidden attribute。
损坏的 dataset state 只警告并重建；损坏的 global history 在下次 finalized
write 前改名为 `.corrupt-*` 再重建，不能阻止 annotation。

命令面：

```pymol
sa_save_state
sa_state_status
sa_history_status
sa_history_search field, query, limit=12
```

安全边界：恢复的 `On step` 在 object load 后执行，bucket command 在 annotate
后执行，因此 dataset state 是 executable configuration，只能加载可信目录。

- `ANNO-01` 首次成功 load 创建隐藏、版本化、原子且有大小上限的 dataset state。
- `ANNO-02` 新会话恢复完整配置、相对文件 cursor；undo/runtime object state 不恢复。
- `ANNO-03` 全局 snapshots/字段历史去重、有上限；输入时 fuzzy 搜索，损坏历史可恢复。
- `ANNO-04` Windows Host 真实 `cmd.do` 可加载含空格目录，并验证 state/history JSON、hidden attribute、cursor 与 command 恢复；另用 PyMOL bundled Python/PyQt 的 visible panel 验证 popup 自动出现及鼠标/键盘接受完整候选。

### 5.6 Structure Annotator CSV、路径映射与本地缓存

CSV 模式必须把每一行建模为独立 `AnnotationEntry`，而不是按 structure path
去重。source CSV 始终只读；原始 header、row values、quoted newline、physical
row number 和 metadata 必须保留。path column 为精确 header，ID column 可选但
若指定必须非空且唯一；没有 ID 时用 CSV basename namespace、raw path 与重复
occurrence 生成稳定 entry ID。不同 basename 的 CSV 不共享 row identity；CSV
和其 `.pymolplugins` 一起移动或通过 `K:`/UNC/WSL alias 访问时 identity 保持。

控制面：

```pymol
sa_csv_preview csv_path, path_column, id_column="", display_columns="",
               encoding=utf-8-sig, delimiter=auto, mapping_profile=x_k,
               source_base="", stage_root="", stage_alias=""
sa_load_csv csv_path, path_column=structure_path, id_column="", ...
sa_filter query="", sort_column="", descending=0
sa_mapping_list
sa_mapping_add profile, source_prefix, target_prefix, provider=mapped,
               priority=0, require_exists=1, case_sensitive=1
sa_mapping_remove profile, source_prefix=""
sa_cache_status include_entries=0
sa_cache_clear force=0
```

`sa_csv_preview` 是严格只读预检：可读取 CSV、mapping store 和远端 `stage/stat`，
但不得创建/quarantine mapping store，不得写 state/history/annotation，不得改变
active dataset、cursor 或 PyMOL scene。Preview table 有独立 source token；点击
preview row 只展示 metadata，不能导航旧数据集。Load 必须先解析、resolve、
materialize 当前 entry，随后事务式 park 旧 managed objects；新对象成功后删除
parked objects，任何失败完整恢复旧 source、cursor、enabled scene 和
`keep_previous` 状态。

路径解析优先级固定为：Windows drive/UNC direct、CSV-relative candidate、
longest-prefix POSIX mapping。候选必须存在、可读且为 regular file；missing、
unreadable、ambiguous、invalid 和 transport error 保留为不同 row status。内置
`x_k` profile 将 `/xcfhome/zpzeng/mount_local`、`/xcfhome/zpzeng`、literal `~/`
映射到 `K:\`。mapping profile 是 bounded/versioned 的全局配置；Preview 对缺失
store 只在内存中使用默认值。

默认启用 Windows-local persistent structure cache，默认上限 5 GiB/5000 项，
根目录可由 `PYMOLPLUGINS_STRUCTURE_CACHE_DIR` 覆盖。mapped/UNC、环境变量
`PYMOLPLUGINS_REMOTE_DRIVES` 指定的 drive（默认 `K`）和 gzip source 使用
cache；identity 包含 provider、规范化后的实际 resolved physical target、size、
mtime、可选 hash/version token。不同 Linux alias 映射到同一个物理 `K:` 文件
时共享 cache key，不得以 raw CSV spelling 产生重复副本。copy/decompress 只在线程
中做 I/O；任何 `cmd.*` load、pin 和 scene mutation 回到 PyMOL main thread。

WSL-only structure 使用显式 opt-in staging。worker 只允许启动参数
`--stage-root ALIAS=ABSOLUTE_ROOT` 注册的 regular file，Host 只发送 alias 与
canonical relative path。顺序固定为 `stage/stat -> cache lookup -> stage/file on
miss`；下载完成后必须使用 response 的完整 `v1-<64-hex>` metadata identity
token 建 key，不能盲用较早的 stat identity。该 token 是
device/inode/size/mtime/mode 的摘要，不是 content hash；它能识别通常的替换或
metadata 变化，但不能识别刻意保留全部这些字段的原位内容改写。每次下载使用
独立临时目录，验证 schema/root/size/identity、拒绝 redirect，并以 adjacent
`.part + fsync + os.replace` 发布；worker 不提供 list/write/delete/shell surface。

大 CSV 不得为每个 WSL row 建立一条 HTTP 往返。worker 额外提供：

```text
POST /v1/stage/stat-batch
request:  {schema_version, root_alias, relative_paths[]}
response: {schema, root_alias, results[]}
```

单个 request 最多包含 256 个非空、唯一、canonical relative paths；更大的
unique-path 集合由 Host 分成多个有界 batch。response 与 request 顺序/数量严格
一致，每项只能是 `{relative_path,source}` 或 `{relative_path,error}`，逐项保留
missing/unreadable/invalid/too-large 等确定性 path 分类，且不得暴露 server
absolute path。timeout 或总 deadline 超限是整批 `stage_batch_timeout`/504，不能
伪装成某一 row 的确定性错误。batch 仍受 Bearer、body size 和总 staging
deadline 约束。Structure Annotator 在同一 CSV 有至少两个 stage path
时优先 batch；duplicate row 先按 relative path 去重再复用结果。仅当旧 worker
明确返回 endpoint `not_found` 时回退单文件 `/v1/stage/stat`；auth、schema、
transport 或 malformed response 不得被兼容回退掩盖。
v1 per-item error code/status 必须是 worker 定义的精确配对；request-level code
出现在 item 内或 status 不匹配时，Host 必须按 malformed response 整批拒绝。

CSV annotation 是按 `(entry_id, label)` 的 append-only JSONL sidecar：

```text
<csv-parent>/.pymolplugins/
  structure_annotator.<csv-id>.annotations.jsonl
```

每个完整 event `flush + fsync`；尾部未换行的 torn event 可忽略/修复，完整坏行
必须报错。`sa_undo` 追加 undo event，不重写 source CSV。`sa_export_csv` 拒绝覆盖
source，原样保留 source rows，并写 collision-safe `pymol_annotation_<label>` 列；
export 必须可再次 import，且重复 path 的 annotation 仍保持独立。
同一 store 的未变化 read 只检查 file signature，不重复读取 JSONL；外部实例改变
signature 后必须 reload/replay。已 fsync 的本地 append 允许增量更新内存 replay
状态，不得每次重读全历史；progress rescan 对一个 label 只取一次 snapshot。

- `ANNO-CSV-01` UTF-8 BOM、auto/explicit delimiter、ragged/duplicate/empty row、typed score、quoted newline 与 duplicate path 行语义确定。
- `ANNO-CSV-02` Preview 不写、不换 scene；load/filter/sort 的成功切换与所有失败路径均事务回滚。
- `ANNO-CSV-03` per-CSV v2 state 恢复 parser/mapping/staging/view/cursor；新 CSV 不继承上一个 CSV 的 ID/display/delimiter/base/staging 配置。ID/base/staging 用显式空字符串清空；display columns 的空字符串表示 auto，显式 `[]` 才表示无 display column。
- `ANNO-CSV-04` JSONL annotation、undo、source-preserving merged export 与 re-import 通过；source CSV/PDB bytes 不变。
- `ANNO-CSV-05` 默认 `x_k` 三个 alias 与 direct `K:` 在真实 Windows FUSE drive 上 resolve；mapped aliases 共享 cold/warm Windows-local cache。
- `ANNO-CSV-06` 真实 WSL worker 的独立 client/cache/materializer E2E 只出现 `stat -> file -> stat`，第二次以完整 metadata token 命中重开的 persistent cache；controller load 复用刚完成的 resolve stat，错误 Bearer、missing 和 containment failure 被拒绝。
- `ANNO-CSV-07` Windows PyMOL `cmd.do` 含空格路径完成 load/annotate/undo/export/restore；visible Qt smoke 验证 Source/Annotate/Scores 与 Preview source isolation。
- `ANNO-CSV-08` 600 个 unique WSL paths 最多产生 3 个 batch HTTP calls，重复 row 不增加 path count；逐项错误保持 row status，旧 worker `not_found` 才逐条兼容回退。
- `ANNO-CSV-09` Scores 主表使用 lazy `QAbstractTableModel + QTableView`，不得为
  每个 cell 分配 `QTableWidgetItem`；metadata 小表继续使用 `QTableWidget`。active
  dataset 以单调 generation 标识，`entry_id -> table row` 与
  `entry_id -> navigation index` 均为 O(1)，同一路径 CSV 原地修改后 reload
  必须刷新 metadata/status，annotation/undo 只触发对应 annotation cell 的
  `dataChanged`。Windows bundled Qt 在 50,000 x 8 synthetic table 上相对旧
  `QTableWidget` 的额外 RSS 至少下降 70%；200 次 cursor refresh 的 p95 < 16 ms、
  max < 50 ms。
- `ANNO-CSV-10` 一次 CSV resolve 中的普通、Windows drive、UNC 与 mapping
  candidate 必须按 canonical Windows candidate 复用 filesystem probe；相同 raw path
  的 local-direct resolution 也必须复用。probe cache 只属于本次 immutable
  resolve snapshot，不得跨 reload 隐藏文件变化。对 `N` rows、`U` unique
  Windows candidates，exists/regular/readable probe 次数必须为 `O(U)`，不能是
  `O(N)`；lexical canonicalization 不承诺解析 symlink/junction。不同 raw rows 的
  `entry_id`、metadata 和 annotation identity 仍保持独立。
- `ANNO-CSV-11` console `sa_csv_preview`/`sa_load_csv` 保持同步返回契约，但 Qt
  Preview、Load 和 CSV restore-last 必须拆成后台 pure prepare 与主线程
  transactional commit。prepare 可以读取 persisted state、解析/resolve CSV、执行
  WSL stat 并 materialize selected structure，但不得访问 Qt widget、调用 `cmd.*`
  或修改 live `STATE`。completion 必须通过 queued Qt signal 回到 application
  thread，并在任何 scene mutation 前验证 latest request epoch、cancel/close、UI
  source/config fingerprint、submission `dataset_generation` 与进程内 annotation
  generation；CSV 在 worker prepare 前后的 device/inode/size/mtime fingerprint
  也必须一致。queued completion 不得重新访问 K:/UNC/WSL filesystem；外部进程
  并发修改 sidecar 不在支持范围内。stale/cancelled completion
  只能丢弃，不能 rollback 当前 scene；commit 开始时才获取 fresh runtime snapshot，
  因而失败会恢复等待结束前的最新 scene/cursor。第一阶段 Cancel 定义为立即恢复
  UI 并抑制 late result，不能声称强杀已经进入 FUSE/HTTP read 的线程。面板必须先
  build/show，再以 zero-delay task 启动 restore；真实 Host gate 用 blocked worker
  证明 event-loop heartbeat、cancel suppression、late-result isolation 和 main-thread
  commit。专用 pool 第一阶段最多两个 worker；若两个不可协作取消的 read 同时阻塞，
  后续请求允许排队到既有 timeout，但任何被取消结果仍不得写入 UI、`STATE` 或 scene。
- `ANNO-CSV-12` Host 的 `stage/stat-batch` chunker 必须按最终
  `json.dumps(..., separators=(",", ":"), ensure_ascii=False).encode("utf-8")`
  请求体的精确字节数执行 greedy 分批，继续同时满足 256 items 与 64 KiB 上限。
  quote、backslash、control character、CJK 与非 BMP Unicode path 的分批结果必须与
  逐候选完整序列化的 reference implementation 一致；单项超限仍返回 typed
  `request_too_large`，顺序与去重契约不变。chunk planning 只能一次序列化固定 envelope
  和每个 path token，再增量累计 comma/token bytes；对 `N` paths、总 encoded token
  长度 `B`，时间必须为 `O(N+B)`，不得为每加入一个 path 重编码整个候选 chunk。
- `ANNO-CSV-13` CSV parse phase 必须先从当前 raw-row snapshot metadata 推断
  `ColumnSpec`，再为每一行只构造一次最终 `AnnotationEntry`；不得先构造无 typed
  display values 的完整 entry，再对全部 entries 执行 `dataclasses.replace`。优化前后
  entry ID/ordinal/display name/source path/base/row number/raw metadata/raw values、row
  validation error、availability、display column order/type/value 与 `AnnotationDataset`
  schema 必须逐字段一致；directory source 与通用 model helper 的既有契约不变。
  constructor-count regression 必须证明 `N` CSV rows 在 path resolution 前恰好创建
  `N` 个 entries。50,000 x 8 synthetic CSV 的 parse/load wall time 与 tracemalloc peak
  必须相对同机旧实现均下降，且不能通过延迟生成会逃出 immutable snapshot 的 iterator
  来换取表面内存下降。

## 6. Specification：group follow 刚体变换

### 6.1 Commands

```pymol
pp_follow_arm driver, group="", followers="", cgo_policy=skip_callback,
              allow_external_changes=0
pp_follow_apply
pp_follow_rebase
pp_follow_undo
pp_follow_status
pp_follow_stop
```

默认工作流是一次性的 `arm → 手工移动 driver → apply`。live QTimer 是 Host GUI 验证后的可选扩展，不作为第一版正确性的前提。

### 6.2 算法

```text
A0 = homogeneous(driver_ttt_at_arm)
A1 = homogeneous(driver_ttt_now)
D  = A1 @ inverse(A0)

for each follower i:
    Bi1 = D @ homogeneous(follower_ttt_at_arm)
```

再把 `Bi1` 转回合法 TTT，并通过 `cmd.set_object_ttt` 设置。不得使用绝对 `matrix_copy(driver, follower)`，否则会破坏 follower 原相对位置。

### 6.3 支持范围

- driver 必须是 leaf object。
- `get_object_ttt() is None` 视为 identity。
- 只接受刚体 delta；scale/shear/reflection 拒绝。
- nested group 展开为唯一 leaf set。
- 静态 CGO、map、measurement 直接跟随 TTT。
- callback-owned/viewport CGO 默认 skip；后续可注册 `rebuild_hook(name, delta, context)`。
- atom/selection coordinate editing 不属于 object TTT motion，必须明确报告 unsupported，不能静默当作零变化。

### 6.4 验收

- `FOLLOW-01` driver/follower 初始矩阵均非 identity 时，相对构型保持不变。
- `FOLLOW-02` rotation+translation、180° rotation、多次 apply/rebase 数值正确。
- `FOLLOW-03` driver 不被双重应用 delta。
- `FOLLOW-04` 静态 CGO 无需重绘即可跟随；callback overlay 被检测并跳过/提示。
- `FOLLOW-05` driver 删除、重命名、group membership 变化或 follower 外部修改均有确定错误，不覆盖未知状态。
- `FOLLOW-06` undo 精确恢复 arm baseline。

## 7. Specification：sequence 与高级对齐

### 7.1 Sequence commands

```pymol
pp_select_sequence objects, queries, selection_name=selected_residues,
                   chain="", state=1, separator="|",
                   gap_mode=exact, max_gap=0,
                   occurrence=all, visualize=1, color=red
```

旧 `sbs/select_sequence/seq_select` 调用新内核。

行为：

- 支持 overlap exact matches；query 统一 uppercase，去除显式 whitespace/`-`。
- `X` 可作为 single-residue wildcard。
- gap 模式必须有界；不允许无界贪婪 `.*`。
- `occurrence=unique` 遇到 0 或 >1 命中时报清晰错误。
- selection 由精确 atom index/residue records 合并，不使用 `resi start-end`。
- 返回结构化 matches；创建选择和着色是可选副作用。

### 7.2 Alignment commands

```pymol
pp_align_anchor mobile_anchor, target_anchor, move="",
                match=identifier|name|order, preview=1, commit=0
pp_align_motif mobile_obj, target_obj, mobile_motif,
               target_motif="", mobile_chain="", target_chain="",
               atoms="N+CA+C+O", move="", occurrence=unique,
               preview=1, commit=0
pp_align_ligand mobile_obj, target_obj, mobile_ligand=organic,
                target_ligand=organic, match=name, move="",
                preview=1, commit=0
pp_align_commit [record_id]
pp_align_undo [record_id]
pp_align_export record_id, filename
```

### 7.3 对应关系与刚体拟合

- anchor 与 move 解耦；独立 ligand/key object 可决定矩阵，matrix 应用到显式 complex/group members。
- `order` 要求相同 atom 数并保留模型顺序。
- `name` 要求双方 `(atom name, element, occurrence)` 唯一对应；歧义时报错。
- motif 按 motif position + atom name 对应，不要求 residue numbering 相同。
- P0 ligand 支持显式/唯一 atom-name 对应；对称 ligand 报 ambiguity。RDKit graph/MCS 是可选后端，不做静默 fallback。
- 少于 3 个非共线点拒绝刚体拟合。
- fit 强制 `det(R)=+1`，返回 before/after RMSD、point count、matrix、degeneracy/confidence。
- preview 创建 ghost/axis 或只打印记录，不移动原对象；commit 才应用 TTT；undo 保存 inverse/baseline。

### 7.4 验收

- `SEQ-01` 空链、多字符 chain、`42A`、负 resi、altloc、缺 CA、modified residues 不错位。
- `SEQ-02` exact overlap、multi-chain、multi-object、unique ambiguity、有界 gap 有确定结果。
- `ALIGN-01` synthetic rigid transform 可恢复，after RMSD 在浮点容差内。
- `ALIGN-02` reflection、共线、重复点、配体对称歧义被拒绝或明确标记。
- `ALIGN-03` 独立 key/ligand 对象只作为 anchor，指定 complex/group 完整移动。
- `ALIGN-04` commit/undo 保持 payload 内部距离和不同 payload 之间的相对关系。

## 8. Specification：geometry、axis 与 membrane slab

### 8.1 统一 AxisResult

```text
AxisResult
  source                # pca | cyclic | vector | two_points | anvil
  center[3]
  direction[3]
  secondary_direction?
  eigenvalues?
  confidence
  diagnostics
  input_selection
```

轴是无向量；如用户未给 sign hint，只保证与 target axis 点积为正。单一主轴不约束 roll，默认采用 minimal rotation 并报告 roll unconstrained。

### 8.2 Commands

```pymol
pp_axis_pca selection=all, atoms=ca|heavy, weighting=equal|mass,
            axis=long, name=pp_pca_axis
pp_orient_pca selection=all, move="", target_axis=z, center=1,
              secondary_selection="", secondary_target=auto, commit=0
pp_axis_cyclic selection=all, subunits="", order=auto,
               name=pp_cyclic_axis
pp_orient_cyclic selection=all, move="", target_axis=z, commit=0
pp_axis_vector vector="0,0,1", center="", name=pp_vector_axis
pp_axis_two_points points="x1,y1,z1,x2,y2,z2", name=pp_two_point_axis
pp_membrane_slab selection=all, axis_source=pca|cyclic|vector|two_points|anvil,
                 axis="", center="", thickness=30, width=auto, height=auto,
                 padding=8, name=pp_membrane, color=gray70,
                 transparency=0.65
pp_membrane_from_anvil input_or_selection, executable="",
                       arguments="{input}", timeout=120, name=pp_membrane_anvil
```

### 8.3 PCA

- 默认 CA、每 residue 等权；可选 all-heavy/mass-weighted。
- covariance 最大 eigenvector 是 longest geometric axis；物理 inertia 的最小 eigenvector不能与其混名。
- 报告 eigenvalue gap；近球形/简并时拒绝自动 orient，除非 `force=1`。
- 围绕结构 center 旋转；`center=1` 再把 center 平移到原点或显式 target center。

### 8.4 Cyclic axis

- Cn (`n>=3`)：每 subunit 等权 centroid；拟合 centroid plane；最小方差方向为候选 axis；在平面内拟合 circle center。
- 检查 radial CV、angular spacing residual、axial pitch 和 subunit size；纯坐标
  cloud 无法证明 sequence compatibility，因此 diagnostics 明确标为 unavailable，
  需要用户用显式 subunit selections/order 承担该边界。
- 明显 axial pitch 标记 helical，不当作 cyclic。
- C2 不能用两个 centroid 拟合 plane；需对 homologous subunits Kabsch，并从 rotation eigenvector 求二重轴。
- 自动模式低 confidence 时必须要求显式 subunit selections/order。

### 8.5 Membrane slab

- normal=`axis.direction`，midpoint=`axis.center`。
- 在 `center ± thickness/2 * normal` 构建两个矩形。
- 矩形平面基 `u,v` 与 normal 正交；auto width/height 来自结构投影 bbox + padding。
- CGO 使用透明 triangles 和可选 border，存于分子坐标系；不是 viewport overlay。
- 保存 `{center, normal, thickness, extents}` 供 JSON 导出、follow 或 redraw。

### 8.6 ANVIL adapter

ANVIL（DOI `10.1093/protein/gzv063`）输出可用 `DUM` HETATM 表示两层 membrane boundary。第一版只实现：

- 使用 `shell=False` 和显式 `{input}`/`{output}` argv 模板安全调用用户提供的
  executable，或解析已有 output PDB；
- executable 在调用进程所属 OS 执行：Windows PyMOL 只调用 Windows-native
  binary/wrapper；WSL-only ANVIL 先在 WSL 运行，再通过 UNC 可见 output PDB 进入
  parser，不把 `/home/...` 当 Windows executable；
- 将 DUM 分成两层，各自拟合 plane；
- 输出两 plane、midplane、normal、thickness 和 fit residual；
- 比较 protein coordinates/identity；若 ANVIL 对蛋白做了刚体重定向，将膜平面
  逆变换回 PyMOL 输入坐标系，非刚体变化则拒绝；
- 不依赖已老化的 web endpoint；不在许可证审查前 vendor CeCILL 源码。

### 8.7 验收

- `GEO-01` PCA 对合成长轴、反平行 target、轴简并结果正确。
- `GEO-02` synthetic C3/C6、现有 C6 fixture、非均匀角度和 helical pitch 可区分。
- `GEO-03` C2 走独立路径，不假装从两个 centroid 得到唯一 plane normal。
- `MEM-01` 两矩形法向正确、间距等于 thickness、auto extent 覆盖结构投影。
- `MEM-02` slab CGO 可随 group follow 变换。
- `MEM-03` ANVIL DUM 两平面 parser 对噪声、翻转 normal、缺层给出确定
  结果/错误；runner 不经 shell，并检测 exact identity、刚体重定向与非刚体篡改。

## 9. Specification：styles registry

### 9.1 Preset model

```text
StylePreset
  schema_version
  name
  aliases
  description
  category              # lighting | palette | scene | render
  colors                # 统一 0..1，名称使用 pp_*
  global_settings
  selection_settings
  color_operations
  representation_operations
  render_defaults
  compatibility
  reversible_fields
```

### 9.2 Commands

```pymol
pp_style_list [category]
pp_style_describe preset
pp_style_apply preset, selection=all, snapshot=1, render=0
pp_style_revert [snapshot_id]
pp_style_render width=1600, height=1200, filename=""
```

首批 presets：

- `publication_light`
- `minimal_light`
- `macaron_default`
- `macaron_warm`
- `macaron_cool`
- `ink_wash`
- `high_contrast_outline`
- `rfd2_lighting`
- `rfd2_paper_palette`

lighting 与 palette 可组合。`setup_ligand_view` 移入 interaction scene preset。

### 9.3 行为与验收

- `STYLE-01` registry 验证 canonical/alias 唯一、RGB 范围和 setting 名。
- `STYLE-02` apply 默认不得 ray/png/zoom。
- `STYLE-03` snapshot 为栈；apply→revert 恢复被修改的 settings、background、atom colors 和 representations。
- `STYLE-04` 所有 legacy style commands 只调用 registry wrapper；任意加载顺序得到相同命令签名。
- `STYLE-05` render 是唯一执行 ray/png 的入口；`dpi` 不再作为无效参数打印。

## 10. Specification：interactions presets 与输出

### 10.1 Pipeline

```text
InteractionRequest
  -> backend adapter
  -> InteractionResult
  -> PyMOL renderer
  -> OutputManifest
```

### 10.2 Commands

```pymol
pp_interact_list
pp_interact_describe preset
pp_interact_run preset, source, partner1="", partner2="", state=1,
                group="", output_json="", replace=1
pp_interact_clear group
pp_interact_export group, filename
pp_interact_backend_status
```

首批 presets：

- `interchain_basic`：PyMOL H-bond + salt bridge。
- `polar_qc`：good/weak polar 与 donor/acceptor clashes。
- `ligand_quick`：4 Å site、界面 sticks、H-bond；无默认 surface/labels/all-distance。
- `ligand_site`：5 Å site、H-bond/salt/property color；surface/labels 显式开关。
- `plip_overview`：完整八类 PLIP 数据。
- `plip_publication`：数据完整，显示聚焦 interface，helpers 默认隐藏。

### 10.3 Public schema

```text
schema: pymolplugins.interactions/1.0
run:
  id
  backend{name, version}
  source{object, state, sha256}
  parameters
partners[]
sites[]:
  site_id
  interactions[]:
    id
    type
    subtype
    participants[]:
      role
      atom{source_index, export_serial, name, element, alt}
      residue{resn, chain, segi, resi, insertion}
    geometry{distance_angstrom, angles_degree}
summary{counts_by_type}
warnings[]
```

`resi` 永远保留字符串。backend raw data 只能进入 provenance/raw，不能成为公共 schema。

### 10.4 输出树

```text
ppi_<source>_<preset>_<shortid>
├── ...__partners
│   ├── ...__partner1
│   ├── ...__partner2
│   └── ...__interface
├── ...__site_<site>
│   ├── ...__hydrogen_bond
│   ├── ...__salt_bridge
│   ├── ...__hydrophobic
│   └── ...
└── ...__helpers          # 默认 disabled
```

### 10.5 PLIP Host/WSL backend

- canonical `plip_overview`/`plip_publication` 固定走 WSL HTTP service，避免
  Windows Host 的嵌入 Python 与 OpenBabel ABI 混装；可用性由 health/capabilities
  显式报告，不做静默 backend fallback。
- Windows Host 不执行 WSL executable path；HTTP body 直接携带 PDB text。
- WSL worker 提供 `GET /v1/health` 和 `POST /v1/interactions/plip`。
- client 使用 `urllib.request`，有 request size limit、60–120 s timeout 和 schema validation。
- Qt UI 网络请求在 worker thread；线程内不得调用 `cmd.*`，结果回到主线程再 render。
- `plip_runner.py` 是 worker 使用的唯一 canonical raw extractor；旧
  `plip_interactions.py` 的公开命令仅委托 canonical API。其不可达历史原型暂时
  保留为用户工作树迁移材料，不计入运行路径，后续经确认后归档。

### 10.6 验收

- `INT-01` 任一 preset 不全局 hide/color/delete 用户对象。
- `INT-02` ligand fixture 的 distance/H-bond/surface 一端必须属于精确 ligand selection。
- `INT-03` 失败恢复 temporary settings 并清理本次 partial outputs。
- `INT-04` JSON、summary counts 和视觉 object counts 来自同一 `InteractionResult`。
- `INT-05` clear 只删除 manifest 精确名称；原 source 永不加入输出 group。
- `INT-06` blank chain、insertion code、多 ligand、多 state、water/metal、长名、零 interaction 有测试。
- `INT-07` worker timeout/unreachable 时 Host UI 不冻结且无半成品。

## 11. Specification：WSL Rosetta metrics service

### 11.1 Architecture

```text
Windows Host PyMOL client
  -> HTTP JSON / loopback / Bearer token
  -> WSL persistent single-worker service
  -> PyRosetta initialized once
  -> structured JSON result
```

Endpoints：

```text
GET  /v1/health
GET  /v1/capabilities
POST /v1/analyze
```

长任务后续扩展 `/v1/jobs`；第一版保持同步、小请求和单 worker。

### 11.2 Request

```json
{
  "schema_version": 1,
  "structure": {"format": "pdb", "text": "..."},
  "partners": {"target_chains": ["A"], "binder_chains": ["B"]},
  "metrics": ["cms", "bunsat_bound_heavy", "bunsat_interface_delta"],
  "options": {}
}
```

禁止服务端路径、shell command 和任意 Python/RosettaScripts XML 注入。未知 residue/ligand params 必须显式报错。

### 11.3 Metrics semantics

- `cms`：`ContactMolecularSurfaceFilter`，方向性 metric；不等同于 dSASA。
- `bunsat_bound_heavy`：bound pose 的 buried unsatisfied heavy-atom donor/acceptor count。
- `bunsat_interface_delta`：bound 相对 separated partners 新增的 interface unsats。
- 后续：`interface_dsasa`、`interface_dg`、`shape_complementarity`、`packstat`。

不得把两类 bunsat 混成同一字段。服务响应必须包含 PyRosetta/Rosetta version、init flags、scorefunction/weights、selectors、cutoffs 和 input SHA256。

### 11.4 Security/runtime

- 默认只绑定 `127.0.0.1`，可配置 Bearer token；禁 CORS。
- 限制 request body；结构正文不写日志。
- PyRosetta/C++ 全局状态第一版串行；hard timeout 需要隔离 process，不能依赖安全中断 C++。
- localhost forwarding 不可用时允许显式配置 WSL IP，不硬编码动态地址。
- client 使用标准库 `urllib`；GUI 异步。

### 11.5 License/install

- 提供 environment probe、requirements/lock guidance 和启动脚本，但不提交 PyRosetta wheel 或凭据。
- PyRosetta non-commercial license、ANVIL CeCILL、MSMS academic-use 条款分别记录。
- 仓库需要明确自身 LICENSE，第三方 adapter 不代表第三方代码已被授权 vendor。

### 11.6 验收

- `ROS-01` 无 PyRosetta 时 `/v1/health` 可启动并明确 `available=false`，mock contract tests 可运行。
- `ROS-02` 合法环境中服务只初始化一次，同一输入结果和 provenance 稳定。
- `ROS-03` CMS、bound bunsat、interface delta bunsat 字段与定义分离。
- `ROS-04` 非标准 residue、缺 chain、服务不可达、timeout、invalid token/schema 均有类型化错误。
- `ROS-05` Windows Host 只传结构 text，不依赖 WSL/Windows 路径转换。

## 12. 主动增加的高价值体验

### 12.1 Plugin bootstrap 与 doctor

```pymol
pp_load
pp_doctor
pp_capabilities
pp_help
```

doctor 报告：PyMOL/Python/platform、完整 canonical command manifest、可写
cache、Windows/WSL path、PLIP/Rosetta/ANVIL endpoints、optional dependencies
和 command conflicts；`pp_help` 按类别列出支持命令。

### 12.2 Result freshness 与 caching

- 已实现：interaction manifest、alignment record 和 remote result 保存 input
  fingerprint/参数/backend；alignment commit/undo 对 stale 状态拒绝覆盖。
- 已实现：Structure Annotator 的 mapped/remote-drive/gzip/WSL-stage structure
  输入使用 resolved physical identity 或完整 worker version token 的原子持久
  cache；这只缓存 loadable structure，不缓存分析结果。
- 后续：PLIP/Rosetta 按 input hash + backend version + parameters 建持久 cache，
  并对 session 重开后的 stale result 提示。

### 12.3 Batch integration

- 已实现：interaction 替换失败会重新绘制旧 result，避免留下空 group。
- 后续：navigation hook 可异步运行 interaction preset；旧结果保持到新结果
  ready 后再切换。
- 对 group 批量 interaction 时每个 child 有独立 run manifest，不共享临时 selection 名。

### 12.4 UI 与导出

- 已实现：versioned JSON、exact manifest、alignment matrix/axis export 和
  `pp_help` command inventory。
- 后续：interaction table 的 site/type filter、点击 select/zoom、一键复制
  residue list、自动 legend 和 GUI parameter autocomplete。

### 12.5 Object/group command iteration

- 已实现：object/group 类型过滤、glob/contains/regex/fuzzy matching、nested
  group leaf expansion、结构化 preview 和显式 commit。
- 后续：为常用 command template 提供可发现 preset；不改变 PyMOL 原生
  `iterate` 的逐 atom 行为。

## 13. 测试策略

### 13.1 WSL 可执行门禁

使用标准库 `unittest` 和 fake PyMOL，避免要求开发环境预装 PyMOL：

- pure math、Kabsch、TTT、sequence index、cyclic axis、membrane/ANVIL parser；
- navigation state、manifest、参数 parser；
- CSV/directory source model、path mapping、per-source state/history、JSONL row
  annotation、cache/materializer、worker staging containment 与 HTTP client；
- fake-cmd mutation boundary、preview/commit/undo；
- interaction/Rosetta JSON schema 和 HTTP mock。

所有 Python 脚本执行 `python3 -m py_compile` 或 AST parse。

### 13.2 Windows Host PyMOL integration

必要 fixture：

- molecule 与 multi-state molecule；
- nested group；
- static CGO、measurement、map；
- callback axes overlay；
- ligand complex、blank chain、insertion code；
- C2/C3/C6 synthetic oligomer。

必要场景：

- `matrix_mode` 0/1/2 capability；
- mouse object motion + follow apply；
- style apply/revert；
- local/PLIP interaction；
- Qt async timeout/cancel；
- 100/1000 follower performance。

Host 自动化从 Windows working directory 启动；不能以 UNC cwd 直接运行 `cmd.exe`。

## 14. 实施顺序与完成定义

### Phase 0：基础与正确性

- shared parameter/naming/math/residue/manifest；
- tests skeleton；
- rewrite `select_by_sequence`；
- group follow arm/apply/undo。

### Phase 1：导航与对齐

- NavigationSession + group navigation；
- file_management compatibility shim；
- anchor/motif/ligand alignment + records/undo。

### Phase 2：styles/interactions

- style registry + legacy wrappers；
- interaction schema/presets/renderer/manifest；
- 修复 ligand selection boundary；
- PLIP runner 去重并接入 HTTP contract。

### Phase 3：geometry/membrane

- PCA/Cn/C2 axis；
- orientation；
- membrane slab；
- ANVIL parser、安全 executable adapter、identity/non-rigid validation 与坐标回映射。

### Phase 4：WSL services 与集成

- shared worker HTTP foundation；
- PLIP endpoint；
- Rosetta health/capabilities/mock backend；
- 在有许可证环境启用 PyRosetta CMS/BUNS；
- Windows Host smoke suite 与文档。

一个 phase 只有在对应验收编号有自动测试或明确 Host test record 后才算完成。仅通过 syntax check 不算 Host integration 完成。

## 15. 外部依据

- PyMOL object matrix：https://pymolwiki.org/index.php/Object_Matrix
- `get_object_matrix`：https://pymolwiki.org/index.php/Get_object_matrix
- `transform_object`：https://pymolwiki.org/index.php/Transform_object
- `matrix_copy`：https://pymolwiki.org/index.php/Matrix_Copy
- ANVIL paper：https://doi.org/10.1093/protein/gzv063
- Rosetta Contact Molecular Surface 与 BuriedUnsat filters：RosettaCommons documentation；运行时仍以所安装版本的 generated schema/capabilities 为准。
