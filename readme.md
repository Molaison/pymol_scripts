## Useful PyMOL Scripts

PyMOL helper scripts, plugins, and presets are now grouped by what they do so it is easier to find (and contribute) focused utilities.

## Repository Layout

- `analysis/`
  - `alignment_selection/`: sequence/structure alignment helpers such as `select_by_sequence.py`, `select_interface_res.py`, `select_idx_sep_comma.py`, `align_by_ligand.py`, and the Qt sequence difference highlighter.
  - `interactions/`: binding-site inspection utilities (`draw_hydrogen_bonds.py`, `ligand_surrounding.py`, `show_contacts.py`) that emphasize contacts between chains, ligands, and post-translational modifications.
  - `cavities/`: backbone cavity detection, the Gemini variant, and the accompanying `view_cavity.pml` preset.
  - `geometry/`: centroid/inertia/ellipsoid tools (e.g., `measure_inertia.py`, `com.py`, `draw_ellipsoids.py`).
- `workflows/`
  - `dataset_navigation/`: high-volume model review utilities (`screen_boost.py`, `file_management.py`, `load_into_single_object.py`) that expose bucket navigation, pinning, and save/load conveniences.
  - `grouping/`: utilities that reorganize loaded objects, currently `load_into_groups.py`.
- `visualization/`
  - `styles/`: color palettes and rendering presets (`set_publication_style.py`, `macaron_style.py`, `publication_presets.py`, `rfdiffusion2_pymolsetting.py`, `ink_wash_style.py`, `high_contrast_ray_style.py`).
  - `overlays/`: CGO overlays such as `axes_overlay.py` and `z_axis_overlay.ini`.
  - `plugins/`: Qt-enabled visualization plugins (initially `macaron_style_plugin.py`).
- `examples/pymol_wiki/`: curated references imported from the PyMOL Wiki (e.g., `color_by_mutation.py`).
- `docs/`: reference PDFs (`docs/references/…`) plus `docs/script_catalog.md` (an index of every active script and plugin).
- `archive/experimental`, `archive/specialized`, `archive/legacy`: older scripts kept for reference.
- `data/`: demo structures (currently `helical_bundle.pdb`) used by tutorials.

## Screen Boosting (`workflows/dataset_navigation/screen_boost.py`)

该脚本实现了用于加速蛋白质设计筛选的整套快捷键，现在与 `file_management.py`、`load_into_single_object.py` 一起放在 `workflows/dataset_navigation/` 目录下，方便成组维护。核心功能保持不变：

- `loadpath path, pattern=<regex>, bucket_size=<num>, objects_per_step=<num>`: 批量导入结构文件并启用 bucket 模式；`objects_per_step` 控制每次 `step` 同时显示的对象数（默认 1）。
- `setup_loader path=<path>, pattern=<regex>, group_size=<num>, objects_per_step=<num>`: 设置分组导入策略，并允许使用 `next_bucket`/`prev_bucket`（或 `CTRL-→ / CTRL-←`）切换批次。
- `step` / `back`（→ / ←）：在当前批次内部逐步查看结构；当 `objects_per_step>1` 时一次显示多个对象（从当前 index 开始的窗口）。
- `save_select`（CTRL+SHIFT+S）：将当前对象导出到 `<current_path>/selected` 子目录。
- `register_switch_cmd command, offset=<i>`：注册切换时执行的命令；当一次显示多个对象时，`offset` 用于指定第 `i` 个对象替换 `{object}` 占位符。

## Select Interface Residues (`analysis/alignment_selection/select_interface_res.py`)

脚本位置已调整为 `analysis/alignment_selection/select_interface_res.py`，其使用方式不变：

```pymol
run analysis/alignment_selection/select_interface_res.py
select_interface_enhanced your_complex_name, method, cb_cutoff, contact_cutoff
```

三种结果选择分别创建：

- `interface_by_distance_{your_complex_name}`：基于原子距离的界面残基。
- `interface_by_area_{your_complex_name}`：基于 SASA 差值的界面残基。
- `interface_{your_complex_name}`：两个标准的交集。

## Ligand Alignment (`analysis/alignment_selection/align_by_ligand.py`)

新的 `analysis/alignment_selection/align_by_ligand.py` 提供 `alignto_ligand` 与 `super_ligand` 命令，可根据配体配准多条链：

```pymol
run analysis/alignment_selection/align_by_ligand.py
alignto_ligand selection=*, ligand_selection=organic, reference_object=objectA
super_ligand selection=model*, ligand_objects="ligA,ligB"
```

可通过 `ligand_object_pattern` 或 `ligand_objects` 精确指定配体对象，命令会输出 RMS 摘要并自动着色配体原子。

## Visualization Styles & Plugins

- `visualization/styles/macaron_style.py` 与 `visualization/plugins/macaron_style_plugin.py`：提供多种粉彩/冷暖光谱，并通过 Qt 对话框快速切换色板 (`apply_custom_style`, `show_macaron_dialog`)。
- `visualization/styles/ink_wash_style.py`：水墨风格的色板与渲染设置，突出高对比的黑白效果。
- `visualization/styles/high_contrast_ray_style.py`：简洁的光追参数模板（关闭阴影、提升环境光），适合快速导出海报底图。
- 仍保留 `set_publication_style.py`, `publication_presets.py`, `rfdiffusion2_pymolsetting.py` 等经典预设，可与上方脚本组合使用。

## Overlays & Specialized Utilities

- `visualization/overlays/axes_overlay.py` 与 `visualization/overlays/z_axis_overlay.ini`：用于在视窗内绘制三轴/参考 Z 轴 CGO 对象，配合对称轴分析尤其方便。
- `archive/specialized/compute_radius_of_gyration.py`, `validate_soft_fix.py`, `align_ligand.py` 等保留脚本按照功能命名，可作为特殊流程的起点。

## Script Catalog

为了覆盖整个脚本库，`docs/script_catalog.md` 列出了主要路径、命令和用途。新增脚本时请同步更新该清单，确保命名与功能保持一致。

## Adding New Content

- **分析脚本**：根据用途放入 `analysis` 的子目录，确保使用 `snake_case` 文件名，并在 docstring 里说明 PyMOL 命令与参数。
- **批量/流程脚本**：放入 `workflows`，若脚本依赖批量导航逻辑，请复用 `dataset_navigation` 中的工具模块。
- **可视化预设**：放入 `visualization/styles`（配色/渲染）或 `visualization/overlays`（CGO 对象）。Qt 插件放入 `visualization/plugins` 并实现 `__init_plugin__`。
- **示例与文档**：放入 `examples` 或 `docs`，避免占用根目录。

遵循 `AGENTS.md` 中的 PyMOL 插件风格指南（单文件插件、`cmd.extend` 注册、traceback 打印等），可以让新脚本更快地融入当前结构。
