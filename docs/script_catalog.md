# Script Catalog

This catalog distinguishes supported entry points from migration wrappers and
standalone historical tools.  Runtime surfaces are important: `Host` means the
module imports PyMOL and must run inside Windows PyMOL; `WSL` means it can run in
ordinary Python; `Both` means a PyMOL-independent core is also testable in WSL.

Status vocabulary:

- **Canonical**: loaded by `plugins/pymol_plugins.py` or a supported service
  entry point; new workflows should use these APIs.
- **Compatibility**: a thin wrapper around canonical behavior, retained for old
  command lines.
- **Standalone**: useful independent domain/UI, but not part of the unified
  canonical lifecycle.
- **Legacy/experimental**: not loaded by `pp_load`; may own unsafe global state,
  broad mutations, duplicate commands, or optional unvalidated dependencies.

All canonical implementation imports flow inward to `pymolplugins/`. Historical
top-level paths remain only as migration shims so existing PyMOL `run` commands
and Python imports continue to work.

## Canonical toolkit

| Surface | Path | Primary API / purpose |
| --- | --- | --- |
| Host | `pymolplugins/host/plugin.py` | Unified implementation for `pp_load`, full-manifest `pp_doctor`/`pp_capabilities`, and categorized `pp_help`; `plugins/pymol_plugins.py` is its thin Plugin Manager entry. |
| WSL | `pymolplugins/host/manifest.py` | Declarative, uniqueness-tested canonical module and 63-command inventory. |
| Both | `pymolplugins/core/parameters.py` | Strict PyMOL-console boundary and value parsing: one-layer paired-quote normalization for every canonical `cmd.extend` callback, plus `as_bool`, `as_int`, `as_float`, `split_names`, and `sanitize_name`. |
| WSL | `pymolplugins/core/sequence.py` | Residue-safe sequence index and exact/bounded motif matching with original chain/resi identity. |
| WSL | `pymolplugins/core/math3d.py` | Dependency-free TTT/homogeneous transforms, rigid validation, Horn/Kabsch fit, eigen/vector utilities. |
| Both | `pymolplugins/targets/resolver.py` | Deterministic exact/glob/contains/regex/fuzzy object/group resolution and recursive non-molecule-safe group expansion. |
| Host | `pymolplugins/targets/commands.py` | Preview-first object/group iteration with same-line parser completion checks: `pp_target_resolve` and `pp_foreach`. |
| WSL | `pymolplugins/navigation/model.py` | Pure `NavigationEntry` / `NavigationSession` state model. |
| Host | `pymolplugins/navigation/commands.py` | Canonical file/object/root-group navigator: `pp_nav_start`, `pp_nav_next`, `pp_nav_prev`, batch, pin, hook, status, sync, and stop commands. |
| Both | `pymolplugins/follow/commands.py` | Explicit `arm -> move driver -> apply` object-TTT propagation with rebase, stale-safe undo, and safe-default static/callback CGO policy: `pp_follow_*`. |
| Host | `pymolplugins/grouping/commands.py` | Existing name-based grouping/alignment helpers; loaded by the toolkit but command names are pre-namespace and should be used cautiously. |
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
| Standalone | Host | `pymolplugins/annotation/plugin.py` | Three-tab directory/CSV Qt annotation UI with preview, lazy model/view score table and O(1) row maps, transactional source swaps, row-level annotation/undo/export, background materialization, persistent cache, per-source restore, and global fuzzy history. |
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
  background CSV heartbeat/cancel/late-result isolation, and main-thread
  Preview/Load completion.
- `tests/host/annotator_qt_performance.py`: repeatable 50,000 x 8 bundled-Qt
  model/widget RSS comparison and 200-sample cursor-refresh performance gate.
- `docs/validation/windows_host_smoke_2026-07-25.md`: latest reviewed Host/CSV/cache
  smoke evidence summary.
- `docs/validation/structure_annotator_optimization_2026-07-26.md`: batch-stat,
  incremental JSONL, and large-table validation evidence.

When adding a module, record its status and runtime surface here.  Do not label a
module canonical merely because it imports successfully or passes syntax checks.
