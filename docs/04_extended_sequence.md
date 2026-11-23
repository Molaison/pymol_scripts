# Extended FASTA 与显式残基 motif

在 PyMOL 控制台输入：

```pymol
pp_fasta model, width=60
pp_select_sequence model, A(MSE)(ATP), residue_tokens=1, visualize=0
```

也可在 PyMOL Python 中使用同一 API：

```python
from pymolplugins.alignment.sequence import pp_fasta, pp_select_sequence

text = pp_fasta("enabled", include_solvent=0, state=-1, width=80, quiet=1)
result = pp_select_sequence(
    "enabled and not solvent", "A(MSE)(ATP)",
    selection_name="component_motif", residue_tokens=1, visualize=0,
)
```

命令签名为 `pp_fasta(selection='enabled', include_solvent=0, state=-1,
width=80, quiet=0)`。导出只读，返回字符串；`quiet=0` 同时打印。
没有可导出的残基或参数无效时抛出明确错误，不创建 selection、不改颜色或视角。

每个 object/state/segi/chain 单独输出，例如：

```text
>complex|state=1|segi=|chain=A
AC(MSE)D(ATP)(ZN)
```

标准氨基酸、常见歧义代码及核酸使用一字母表示，修饰残基和其他 component
保留完整 `(RESN)` 名称。例如 MSE 是 `(MSE)`，不是 `M`；DA 是 `A`。
残基按选中原子的输入顺序排列。异构配体、离子等在一条 track 中相邻，
不表示它们构成共价序列。部分原子 selection 仅贡献实际选中的原子及其残基。

- `state=-1`：每个对象当前显示状态；`state=0`：各状态分别输出；正数：指定状态。
- `include_solvent=0`：排除 PyMOL `solvent` selection；设为 `1` 才包含。
- `width` 必须为正整数。换行不拆 `(RESN)`；单个 token 长于 width 时单独占一行。
- 空 segi/chain 在 header 中保留为空，不与名为 `_` 的 segment/chain 混淆。

`pp_select_sequence` 在原有参数末尾新增 `residue_tokens=0`。
默认模式仍使用既有蛋白一字母匹配：MSE 可匹配 `M`，`X` 为单残基通配符，
原有 bounded gap、occurrence 和数量限制保留。

设置 `residue_tokens=1` 后：

- 普通字母匹配导出的一字母代码；括号 token 按完整 resn 精确匹配。
  `(ALA)` 只匹配 ALA，`A` 可匹配 ALA 或核酸 A/DA。`X` 仍是一个残基的通配符，
  精确指定未知名称时使用 `(UNK)`。
- component 名称支持字母、数字、`_`、`.`、`+`、`-`；大小写归一化。
  token 外的空白和 `-` 忽略，括号内不能留空、嵌套或含空白。
- 仅支持 `gap_mode='exact', max_gap=0`；其他设置明确报错。
  重叠匹配、`all/first/unique`、`max_matches` 均复用 canonical matcher。
- 匹配不会跨 object、state、segi 或 chain。`state=0` 分状态匹配，最后的
  PyMOL selection 是匹配原子身份的并集，不能为同一原子存储逐状态可见性。
- token 模式搜索输入 selection 中所有 component，包括选中的 solvent。
  若直接使用默认导出得到的 motif，搜索 scope 也使用 `and not solvent`。
- 输出继续走既有 selection ownership 与事务回滚。非法 token、无匹配和
  名称冲突不会清空旧 selection；只可替换仍未被用户修改的本进程自有 selection。

`analysis/alignment_selection/sequence_utils.py` 只保留显式 `run` 桥接入口，
不维护另一份索引，也不注册 `get_fasta/find_seq/fasta_ext/seq_find` 短名。
主插件自动注册这两个 canonical 命令，`pp_help pp_fasta` 和
`pp_help pp_select_sequence` 提供统一帮助与签名补全。

验证命令（仓库根目录）：

```text
C:/Python313/python.exe -B -m unittest tests.alignment.test_sequence_selection tests.alignment.test_extended_sequence -v
D:/Softwares/Pymol3/python.exe -B -m unittest tests.alignment.test_extended_sequence_pymol -v
```
