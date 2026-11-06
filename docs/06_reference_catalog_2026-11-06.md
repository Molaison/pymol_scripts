# 查找命令和脚本

> 历史详细参考：本页保留 2026-11-06 重写前的条款、示例和验收范围。旧版本措辞和测试结果不自动适用于当前版。操作请从 [现行页面](script_catalog.md) 开始。

查找统一命令、兼容入口和独立脚本时使用本索引。先运行 `pp_load`、`pp_help`；按任务操作见 [命令示例](task_command_walkthrough.md)。0.17.0 候选版的 group、interaction 和样式见 [使用指南](01_group_interaction_styles.md)，[本次验收](validation/02_toolkit_unification_2026-11-06.md) 记录实际范围；7 月记录只是历史基线。

下表保留精确路径和技术职责，便于追查命令所属实现。`Host` 表示需要 PyMOL 宿主；`WSL` 表示可在普通 Python 中使用或测试；`Both` 表示包含不依赖 PyMOL 的部分，并不意味着所有界面都能在 WSL 运行。

- **Canonical**：统一入口加载的命令或受支持服务；新工作优先使用。
- **Compatibility**：保留旧命令和导入路径的兼容入口。
- **Standalone**：独立功能，不一定纳入统一生命周期。
- **Legacy/experimental**：`pp_load` 不加载；使用前确认副作用与依赖。

历史顶层路径保留为兼容入口；统一实现位于 `pymolplugins/`。下表的英文技术字段保留原有精确信息。

| 要完成的任务 | 从这里开始 |
| --- | --- |
| 查帮助、查看环境 | `pp_load`、`pp_help`、`pp_doctor check_worker=0` |
| 浏览对象或 group | `pp_nav`；具体步骤见 [命令指南](task_command_walkthrough.md) |
| 查看相互作用、分组、样式 | [候选版指南](01_group_interaction_styles.md) |
| 导入、筛选、标注结构 | [Structure Annotator](structure_annotator_csv_walkthrough.md) |
| 批量配对和处理文件 | [Batch Processing](batch_processing_specification.md#usage) |
| 配置可选计算后端 | [WSL worker](wsl_worker.md) |

## Canonical toolkit

| Surface | Path | Primary API / purpose |
| --- | --- | --- |
| Host | `pymolplugins/host/plugin.py` | Unified implementation for `pp_load`, full-manifest `pp_doctor`/`pp_capabilities`, and categorized `pp_help`; `plugins/pymol_plugins.py` is its thin Plugin Manager entry. |
| WSL | `pymolplugins/host/manifest.py` | Declarative, uniqueness-tested inventory. The 0.17.0 candidate has 75 canonical commands (including eight task facades) and eight compatibility names. |
| Both | `pymolplugins/tasks/commands.py` | Recommended `pp_show`, `pp_nav`, `pp_align`, `pp_orient`, `pp_contacts`, `pp_metrics`, `pp_jobs`, and `pp_undo` facades; delegates to canonical domain owners. |
| Both | `pymolplugins/tasks/journal.py` | Bounded exact-owner operation references plus all-state representation snapshots and optional hidden same-session object identity markers for transactional `pp_show` undo. |
| Both | `pymolplugins/core/parameters.py` | Strict PyMOL-console boundary and value parsing: one-layer paired-quote normalization for every canonical `cmd.extend` callback, plus `as_bool`, `as_int`, `as_float`, `split_names`, and `sanitize_name`. |
| WSL | `pymolplugins/core/sequence.py` | Residue-safe sequence index and exact/bounded motif matching with original chain/resi identity. |
| WSL | `pymolplugins/core/math3d.py` | Dependency-free TTT/homogeneous transforms, rigid validation, Horn/Kabsch fit, eigen/vector utilities. |
| Both | `pymolplugins/targets/resolver.py` | Deterministic exact/glob/contains/regex/fuzzy object/group resolution and recursive non-molecule-safe group expansion. |
| Host | `pymolplugins/targets/commands.py` | Object/group iteration with default molecule-leaf execution, explicit `commit=0` preview, and same-line parser completion checks: `pp_target_resolve` and `pp_foreach`. |
| Both / Host UI | `pymolplugins/targets/batch_*.py` | `pp_batch`: folder/CSV fixed-reference or template/glob/regex ID pairs, ordered commands, main-thread execution, default A+B outputs, cancellation and result reports. |
| WSL | `pymolplugins/navigation/model.py` | Pure `NavigationEntry` / `NavigationSession` state model. |
| Host | `pymolplugins/navigation/commands.py` | Canonical file/object/root-group navigator: `pp_nav_start`, `pp_nav_next`, `pp_nav_prev`, batch, pin, hook, status, sync, and stop commands. |
| Both | `pymolplugins/follow/commands.py` | Explicit `arm -> move driver -> apply` object-TTT propagation with rebase, stale-safe undo, and safe-default static/callback CGO policy: `pp_follow_*`. |
| Host | `pymolplugins/grouping/commands.py` | `pp_group` inspects/toggles complete groups; `pp_group_by_name` creates non-destructive groups. Historical names remain compatible. |
| Host | `pymolplugins/host/completion.py` | Shared positional and keyword argument completion for toolkit and annotator commands. |
| Host | `pymolplugins/annotation/scene_groups.py` | Candidate bundles and existing-scene group review; enter with `pp_review_groups`. |
| Both | `pymolplugins/alignment/sequence.py` | Canonical `pp_select_sequence`; exact residue mapping covers blank/multi-character chains, insertion codes, negative resi, altloc, and missing CA. |
| Both | `pymolplugins/alignment/rigid.py` | Anchor/motif/ligand fit with explicit move payload, anchor/move fingerprints, stale-safe preview/commit/undo, and reproducible export: `pp_align_*`. |
| Both | `pymolplugins/geometry/commands.py` | Equal/mass-weighted PCA, vector/two-point/cyclic axes, secondary-roll orientation, membrane slabs, shell-free ANVIL runner/DUM parser/coordinate validation, and JSON export. |
| Both | `pymolplugins/styles/presets.py` | Declarative normalized preset definitions. |
| Host | `pymolplugins/styles/registry.py` | Validated style registry with snapshot/revert and render separation: `pp_style_*`; owns deterministic legacy wrappers. |
| WSL | `pymolplugins/interactions/schema.py` | Versioned public `InteractionResult` and exact `OutputManifest` schemas with source objects, input/stale fingerprints, and parameters. |
| WSL | `pymolplugins/interactions/presets.py` | Canonical local and PLIP preset definitions and aliases. |
| WSL | `pymolplugins/interactions/plip_protocol.py` | Strict PyMOL-export/PLIP atom identity map, raw validator, and canonical `InteractionResult` conversion. |
| Both | `pymolplugins/interactions/controller.py` | Mutation-bounded local/PLIP controller: preset dispatch, exact-ligand filtering, canonical rendering, transactional replacement restore, export, and manifest clear. |
| Host | `pymolplugins/remote/commands.py` | Windows PyMOL async Qt client for PLIP and Rosetta plus worker health/capabilities; worker threads perform HTTP only. |
| WSL | `pymolplugins/remote/client.py` | Bounded typed HTTP client with no redirects and request/response/schema checks. |
| Both | `pymolplugins/remote/staging.py` | Warm-cache single/batch stat lookup plus bounded binary staging with identity validation, 256-item/64-KiB batch chunking, exact old-worker fallback, and atomic `.part` replacement; called by Windows PyMOL and independently testable in WSL. |
| WSL | `pymolplugins/worker/app.py` | Loopback HTTP service: health/capabilities, inline analysis, and optional allowlisted `/v1/stage/stat`, `/v1/stage/stat-batch`, and `/v1/stage/file`. |
| WSL | `pymolplugins/worker/staging.py` | Root-alias/relative-path resolver with realpath/fd containment, regular-file, size, and deadline checks. |
| WSL | `pymolplugins/worker/backends/plip.py` | Optional PLIP backend; active only when PLIP/OpenBabel are importable. |
| WSL/native Python | `pymolplugins/interactions/plip_runner.py` | Versioned eight-category PLIP raw adapter used by the worker and native compatibility environments. |
| WSL | `pymolplugins/worker/backends/rosetta.py` | Lazy, serialized backend for an already-installed licensed PyRosetta environment; never downloads PyRosetta. |

## Compatibility adapters

| Surface | Path | Compatibility behavior |
| --- | --- | --- |
| Host | `workflows/dataset_navigation/file_management.py` | Old file/batch/pin command names delegate to `pp_nav_*`; owns no navigation state and no longer registers generic `next`/`prev`/`pin`. |
| Host | `workflows/dataset_navigation/screen_boost.py` | Old bucket/window/hook names delegate to the canonical navigator; owns no second cursor and performs no import-time key binding. |
| Host | `analysis/alignment_selection/align_by_ligand.py` | `alignto_ligand` / `super_ligand` delegate to canonical ligand-anchor rigid alignment and move whole complexes. |
| Host | `analysis/alignment_selection/select_by_sequence.py` | Old path shim; `sbs`, `select_sequence`, and `seq_select` remain aliases of `pp_select_sequence`. |
| Host | `visualization/styles/macaron_style.py` | Legacy macaron command loader; delegates to the registry. |
| Host | `visualization/styles/set_publication_style.py` | Legacy publication command loader; delegates to the registry. |
| Host | `visualization/styles/publication_presets.py` | Legacy preset command loader; delegates to the registry. |
| Host | `visualization/styles/ink_wash_style.py` | Legacy ink-wash command loader; delegates to the registry. |
| Host | `visualization/styles/rfdiffusion2_pymolsetting.py` | Legacy RFdiffusion-style command loader; delegates to the registry. |
| Host | `analysis/interactions/plip_interactions.py` | Old PLIP command/dialog names delegate run and clear to `pp_interact_*`; the preserved historical implementation is not entered by the public command. |

## Standalone and in-progress tools

| Status | Surface | Path | Purpose / caveat |
| --- | --- | --- | --- |
| Plugin | Host | `pymolplugins/annotation/plugin.py` | Structure Annotator workspace: directory/CSV/SSH import, scores, row annotations and export, background materialization, cache, explicit restore, and global fuzzy history. |
| Standalone support | Both | `pymolplugins/annotation/model.py`, `sources/` | PyMOL-independent immutable entry/dataset model and directory/CSV adapters; preserves raw rows and duplicate-path entry identity while inferring typed score columns. |
| Standalone support | Both | `pymolplugins/annotation/path_resolver.py`, `mapping_store.py` | Direct/relative/POSIX-to-Windows path resolution plus bounded global mapping profiles; built-in `x_k` maps three remote-home spellings to `K:`. |
| Standalone support | Both | `pymolplugins/annotation/cache.py`, `materializer.py` | Windows-local persistent source-version cache with atomic copy/decompress, LRU limits, pinning, concurrent-write deduplication, and metadata source-change checks. |
| Standalone support | Both | `pymolplugins/annotation/annotation_store.py` | Append-only, fsynced, bounded CSV row/label JSONL annotations with torn-tail recovery, signature-cached reads, incremental local replay, label snapshots, undo, and atomic compaction. |
| Standalone support | Both | `pymolplugins/annotation/state_store.py` | Version-2 atomic per-directory/per-CSV state under `.pymolplugins`, portable CSV sidecar IDs, Windows hidden-directory support, and bounded version-1 migration. |
| Standalone support | WSL/Host Python | `pymolplugins/annotation/history_store.py` | Version-2 bounded atomic global source/config snapshots plus recency/frequency-aware fuzzy field history; `PYMOLPLUGINS_ANNOTATOR_HISTORY` provides an isolation override. |
| Standalone | Host | `extras/navigation/load_states.py` | Loads a directory as states in one object; this is a separate movie-style use case. |
| Standalone | Host | `extras/interactions/ligand_environment.py` | Ligand-environment convenience commands; revised to keep generated output scoped, but canonical presets are preferred. |
| Legacy | Host | `extras/interactions/contacts.py` | Historical good/bad polar-contact plugin with Tk/Qt entry points. |
| Legacy | Host | `visualization/styles/high_contrast_ray_style.pml` | Historical direct PML preset; canonical automation should use `pp_style_apply` / `pp_style_render`. |
| Standalone | Host | `extras/interactions/hydrogen_bonds.py` | Direct inter-chain H-bond/salt-bridge visualization; canonical presets are preferred for structured output. |
| Standalone | Host | `extras/alignment/interface.py` | CB-distance/SASA/contact interface selector; the legacy implementation still needs deeper mutation/surface review. |
| Standalone | Host | `extras/alignment/index_selection.py` | `sis` helper for comma-separated residue indices. |
| Standalone | Host | `extras/alignment/sequence_difference.py` | Qt/console sequence-difference coloring workflow. |
| Standalone | Host | `extras/cavities/backbone_cavity.py` | Cavity finder with CGO output and optional Qt dialog. |
| Experimental | Host | `extras/cavities/backbone_cavity_experimental.py` | Alternate cavity workflow with external scientific dependencies. |
| Standalone | Host | `extras/geometry/inertia.py` | Covariance/inertia/surface utilities and optional user-provided MSMS executable. PCA/orientation work should prefer `orientation.py`. |
| Standalone | Host | `extras/geometry/ellipsoids.py` | Direct CGO ellipsoid builder. |
| Standalone | Host | `extras/geometry/center_of_mass.py` | Center-of-mass helper. |
| Standalone | Host | `pymolplugins/geometry/axes_overlay.py` | Callback-owned viewport axes that automatically register with group follow and are skipped by its safe default. |
| Data | Host | `visualization/overlays/z_axis_overlay.ini` | Static Z-axis CGO definition. |

## Examples and archive

| Status | Path | Purpose |
| --- | --- | --- |
| Example | `examples/pymol_wiki/color_by_mutation.py` | Curated PyMOL Wiki mutation-coloring example. |
| Archive | `archive/specialized/align_ligand.py` | Historical CLI ligand-transfer helper. |
| Archive | `archive/specialized/compute_radius_of_gyration.py` | Historical radius-of-gyration calculator. |
| Archive | `archive/specialized/validate_soft_fix.py` | Historical patch diagnostic. |
| Archive | `archive/experimental/`, `archive/legacy/` | Reference-only code and session artifacts; never loaded by `pp_load`. |

## Tests and operational documentation

- `tests/`: feature-mirrored standard-library pure/fake-PyMOL test suite runnable
  in WSL; `tests/architecture/` enforces one-way canonical imports and command
  manifest uniqueness.
- `docs/development_specification.md`: behavioral specification and acceptance IDs.
- `docs/implementation_status.md`: evidence-based completion matrix.
- `docs/wsl_worker.md`: worker installation, startup, networking, security, and troubleshooting.
- `docs/windows_host_acceptance.md`: required real-PyMOL integration record.
- `docs/structure_annotator_csv_walkthrough.md`: one-by-one CSV, path mapping,
  Windows-local cache, and WSL staging validation procedure.
- `tests/host/host_smoke.py` and `scripts/windows/run_host_smoke.ps1`: automated
  Host fixture/smoke gate; interactive mouse/callback/Qt items remain manual.
- `tests/host/annotator_qt_smoke.py`: visible Host-PyQt completion gate using
  the Python/Qt bundled with Windows PyMOL; types through QtTest and accepts
  multiline/bucket fuzzy candidates by mouse and keyboard; also checks lazy
  Scores-table identity, targeted annotation cells, same-source reloads,
  background CSV heartbeat/cancel/late-result isolation, immediate registry
  cleanup on Cancel/panel close, and main-thread Preview/Load completion.
- `tests/host/annotator_qt_performance.py`: repeatable 50,000 x 8 bundled-Qt
  model/widget RSS comparison and 200-sample cursor-refresh performance gate.
- `docs/validation/windows_host_smoke_2026-07-25.md`: historical July Host/CSV/cache
  smoke evidence summary.
- `docs/validation/structure_annotator_optimization_2026-07-26.md`: batch-stat,
  incremental JSONL, and large-table validation evidence.

When adding a module, record its status and runtime surface here.  Do not label a
module canonical merely because it imports successfully or passes syntax checks.
