# Implementation Status

Snapshot date: 2026-07-26 (Asia/Shanghai)
Specification: [development_specification.md](development_specification.md)

This file separates code existence, WSL evidence, automated Windows Host
evidence, interactive Host evidence, and optional licensed-backend evidence.

Current evidence anchors:

- WSL: 369 standard-library unit/fake-command tests pass (`21.097s` on the
  final ANNO-CSV-12 rerun).
- Windows Host: the automated PyMOL 3.1.3.1 record is
  [automatic pass / manual pending](validation/windows_host_smoke_2026-07-25.md).
- Structure Annotator: `HOST-ANNOTATION-CSV-01` and the real PyQt5 smoke pass;
  real Windows `K:` FUSE mapping/cache passes, and the PyMOL-independent
  allowlisted WSL staging client/cache/materializer chain passes a real worker
  cold/warm E2E. The batch-stat and 50,000-row evidence is recorded in
  [the 2026-07-26 optimization record](validation/structure_annotator_optimization_2026-07-26.md).
- WSL worker: a real loopback run on Python 3.12.13 returned `status=ok` with
  PLIP 3.0.0 available; Windows Host PyMOL reached that worker through localhost
  forwarding and verified all 63 canonical commands.
- Real PLIP/OpenBabel numerical validation passes both direct-backend and HTTP
  paths on RCSB 1A4W. A separate Windows PyMOL -> WSL PLIP -> exact-ligand Host
  render produced one QWE site, 14 interactions, and 12 manifest-owned outputs.
  PyRosetta remains absent and no PyRosetta numerical result is claimed.

Status vocabulary:

- **WSL verified**: covered by current pure/fake-command tests.
- **Host smoke verified (3.1.3.1)**: covered by the versioned automatic Host
  record, within that check's stated limits.
- **Implemented; interactive Host pending**: code/tests exist, but mouse,
  callback, Qt, rendering, or another interactive behavior is not recorded.
- **Blocked by optional licensed environment**: code/mock contract exists, but
  a legal installed backend is required for numerical validation.

PyMOL 2.x compatibility remains open even when the 3.1.3.1 smoke check passes.

## Acceptance matrix

### Navigation

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| NAV-01, NAV-02 | WSL verified | Empty/single/final-short batches, wrap, stride, and forward/back symmetry are covered. |
| NAV-03 | Host smoke verified (3.1.3.1) | Nested root group remained atomic with molecule, static CGO, map, measurement, and multistate leaves; an unmanaged overlay was preserved. |
| NAV-04 | Implemented; Host file-failure pending | Fake tests prove ordinary load rollback plus transactional `replace=1`: the prior controller, visible scene, adopted file objects, and key lease survive a failed first load. A real Host file-load failure remains manual. |
| NAV-05 | WSL verified | Both legacy facades share the canonical hook/pin functions and controller; dual load-order tests prove overlapping aliases resolve to the same callable, and neither adapter binds keys at import. |
| NAV-06 | WSL verified | Shared console booleans reject unknown tokens and parse false strings correctly. |

Key lease restoration, third-party rebinding preservation, structured hook
context/offset, pin clearing, and explicit session replacement are also tested.

### Structure Annotator state and history

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| ANNO-01, ANNO-02 | Host smoke verified (3.1.3.1) + WSL verified | Version-2 dataset-local state is schema-checked, size-bounded, and atomically replaced. Tests cover directory/CSV isolation, version-1 directory migration, On-step/bucket/preload/cache restoration, relative-file or stable-entry cursor recovery, missing-entry fallback, corrupt JSON repair, and exclusion of undo/object/materialized-cache state. Host restored `b.pdb`, the complete config, and commands from a quoted path containing spaces; the Windows hidden attribute was set. |
| ANNO-03 | Host Qt/storage/search + WSL verified | Global history snapshots and 12 field histories are bounded, de-duplicated, atomically written, corrupt-file quarantined, and fuzzy-ranked by textual relevance plus frequency/recency. Headless PyMOL wrote the global schema through a temporary profile path and resolved `cartn` to the complete command. A separate real Host PyQt5 smoke created the three-tab panel, observed both popups, accepted On-step by mouse and bucket command by keyboard, and verified fixed/dynamic completers plus preview-source isolation. A latest snapshot reopens its source by default; a fresh-process visual check of the opt-out remains manual. |
| ANNO-04 | Host smoke verified (3.1.3.1) | `HOST-ANNOTATION-STATE-01` is registered and passed. It uses a quoted directory containing spaces and a temporary global history path, and asserts Windows hidden state, schema/config/cursor restoration, and fuzzy command lookup without polluting the real user profile. Removing `cmd.sync()` from the parser callback eliminated the observed self-wait `wait_queue` timeout on rerun. |

### Structure Annotator CSV, mapping, and cache

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| ANNO-CSV-01 | WSL verified | CSV/TSV parsing covers BOM, auto/explicit delimiters, exact headers, quoted newlines/physical row numbers, ragged rows, empty/duplicate IDs, duplicate structure paths, portable basename-namespaced entry IDs, numeric typing, and immutable row metadata. |
| ANNO-CSV-02, ANNO-CSV-03 | WSL verified + Host transaction success | Preview does not write scene/session/state/history/mapping data and performs staging `stat` when configured; preview rows cannot navigate an old source. Directory/CSV/filter switches preflight the new entry, park old managed objects, and either commit or restore the complete prior scene. Tri-state source settings restore per-CSV values, while a newly selected CSV inherits no parser/ID/display/base/staging values from the old CSV. Host `HOST-ANNOTATION-CSV-01` exercises the successful quoted-path transaction. A deliberately injected real Host load failure remains a manual gate; all rollback branches are fake-PyMOL tested. |
| ANNO-CSV-04 | Host smoke verified (3.1.3.1) + WSL verified | Append-only row/label JSONL supports fsync, torn-tail recovery, bounded validation and undo. Unchanged reads use a file-signature cache, progress uses one label snapshot, and a local fsynced append updates the in-memory replay state incrementally instead of rereading the full log. Source CSV/PDB bytes remain unchanged. Export refuses source overwrite, writes `pymol_annotation_<label>` without duplicate headers, preserves ragged source values, and is re-importable. Duplicate source paths remain independently annotated. |
| ANNO-CSV-05 | Real Windows `K:` FUSE verified | `/xcfhome/zpzeng/mount_local`, `/xcfhome/zpzeng`, and literal `~/` resolve to `K:\`; all three aliases share one physical-target cache key (`cold=false`, then two warm hits). Direct `K:\...` is recognized as a configured remote drive and shows its own cold/warm persistent-cache pair. The 75,609-byte source SHA-256/size/mtime stayed unchanged, and cache objects were on Windows-local `C:`. |
| ANNO-CSV-06 | Real WSL worker core verified | The PyMOL-independent Bearer-authenticated client/cache/materializer chain with root alias `structures` produced exactly `POST /v1/stage/stat`, `POST /v1/stage/file`, `POST /v1/stage/stat`. Cold materialization missed, the second stat hit a cache key containing the complete `v1-...` metadata token, and a reopened cache also hit; source bytes were unchanged. Controller tests verify a CSV load reuses its fresh resolution stat rather than issuing a redundant preflight stat. Unit tests additionally cover wrong bearer, missing, symlink/traversal/regular-file/size/deadline checks and response validation. |
| ANNO-CSV-07 | Host smoke + real PyQt5 verified | `HOST-ANNOTATION-CSV-01` loads a Windows-local CSV/path containing spaces through real `cmd.do`, keeps duplicate path rows independent, annotates, exports, restores state/cursor, and verifies source bytes. The Qt smoke validates Source/Annotate/Scores widgets and that clicking a preview row makes zero `sa_goto` calls. Windows-PyMOL-to-WSL controller staging and PyMOL 2.x are still manual gates. |
| ANNO-CSV-08 | Real WSL worker + WSL verified | The allowlisted worker exposes bounded `POST /v1/stage/stat-batch` with at most 256 unique canonical paths, ordered per-item source/error results, one request deadline, strict envelope validation, and no server paths. The Host client additionally chunks at 64 KiB, bounds responses at 2 MiB, and falls back only for an old worker's exact `404/not_found`. A real foreground worker returned one available and one missing result in one batch request; the old single stat/file endpoints then returned matching identity and 62,712 bytes. |
| ANNO-CSV-09 | Real Windows PyQt5 performance gate | Scores uses `QTableView + QAbstractTableModel`; the small metadata table remains `QTableWidget`. Dataset/navigation generations and entry-ID maps remove full-row signature scans, stale clicks refresh without navigating, same-source reload refreshed `0.0 -> 9.9` and `available -> missing`, and annotation emitted only cell `(0,3)`. On Windows bundled PyQt5, 50,000 x 8 used 12.3 MiB additional RSS versus 199.8 MiB for the legacy widget (93.85% reduction); 200 cursor refreshes had p95 0.258 ms and max 0.615 ms. |
| ANNO-CSV-10 | WSL performance + regression verified | One resolve snapshot caches only truly repeated `(raw path, source base)` keys (with a homogeneous-base string fast path) and separately reuses canonical Windows mapped/drive/UNC candidate probes. Duplicate local rows perform one `isfile/access` pair while row IDs/metadata remain independent; heterogeneous bases cannot leak resolutions, and the cache is rebuilt on reload. A 50,000-row audit retained duplicate-path speedups without the all-unique regression caused by populating a full resolution dictionary. |
| ANNO-CSV-11 | Real Windows PyQt5 + Host transaction + WSL verified | Console preview/load remain synchronous. Qt Preview/Load use immutable worker preparation and queued main-thread completion; live `STATE`, widgets and `cmd.*` stay out of the worker. Cancel/close/latest-epoch/source+config fingerprints/dataset+annotation generations suppress late results without rollback, worker-side CSV fingerprints reject mid-prepare changes, and commit takes its rollback snapshot at completion time. The queued slot performs no K:/UNC/WSL filesystem probe. The real PyQt5 gate observed 10 heartbeats while a worker was blocked, 0.20 ms cancel, late-result/config-change/annotation-change isolation, and both preview completion and load commit on the application thread. Restore-last recovers config/intent without probing the source before panel construction. Cancel is result suppression rather than thread termination: two non-cooperative blocked reads can occupy the dedicated two-thread pool until their timeouts, so a later request waits without mutating UI, `STATE`, or scene. |
| ANNO-CSV-12 | Windows bundled Python + WSL verified | Batch request planning serializes the fixed JSON envelope once and each path token once, then uses exact UTF-8 token/comma accounting while preserving the greedy 256-item/64-KiB chunks. Reference tests and 5,000 randomized cases cover quotes, backslashes, control characters, CJK and non-BMP Unicode, exact-limit acceptance/rejection, empty input, and partial-yield errors. A 50,000-path benchmark retained the same 196 chunks and reduced median planning time from 0.9596 s to 0.0302 s (31.8x); all 23 staging-client tests also passed under PyMOL 3.1.3.1's bundled Windows Python 3.10.16. |

### Object/group targets

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| TARGET-01..05 | Host smoke verified (3.1.3.1) + WSL verified | Exact/glob/contains/regex/fuzzy matching, deterministic ordering, preview-only default, and explicit commit are tested. Real Host `cmd.do` covers a quoted template and quoted glob target; expansion of one nested group covered molecule, static CGO, map, measurement, and multistate leaves while preserving an unrelated overlay. Same-line completion markers detect the real parser's swallowed unknown-command/CmdException failures, enforce stop/continue, and leave no scene names behind. |

### Group follow

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| FOLLOW-01..03, FOLLOW-06 | Host translation smoke + WSL rigid tests | Programmatic translation and undo pass for molecule/static-CGO/map/measurement/multistate under `matrix_mode=0/1/2`. WSL tests cover non-identity baselines, 180-degree rotation, repeated apply, and rebase. Real mouse rotation/translation remains manual. |
| FOLLOW-04 | Implemented; callback Host pending | `axes_overlay.py` automatically registers callback ownership; safe default is skip, with explicit static/rebuild policies and apply/undo hook tests. A live view-callback record remains manual. |
| FOLLOW-05 | WSL verified | External follower/group drift and stale undo are rejected before writes; `force=1` is explicit, and failed multi-follower apply rolls back prior followers. |

### Sequence and rigid alignment

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| SEQ-01, SEQ-02 | Host core smoke + WSL verified | Host creates an exact chain-specific motif selection and verifies its four source atom identities. Blank/multi-character chains, insertion/negative `resi`, altloc, missing CA, modified residues, overlap, bounded gaps, ambiguity, and multi-object identity remain covered by WSL fixtures. |
| ALIGN-01, ALIGN-02 | Host success paths + WSL failure tests | Real Host anchor, motif, and ligand fits reach zero RMSD, then commit and undo their object TTTs. WSL tests reject scale/reflection/collinearity; duplicate ligand keys produce a typed ambiguity and verified order remains explicit. |
| ALIGN-03, ALIGN-04 | Host smoke verified (3.1.3.1) + WSL verified | Host proves preview is non-mutating, commit/undo restores the payload, motif anchors move a whole object, and a detached ligand key remains fixed while a separate payload moves. Group leaves, atomic rollback, stale checks, hashes, state, parameters, and backend provenance are WSL-tested. |

### Geometry and membrane

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| GEO-01 | Host orientation/undo + WSL edge tests | Real Host smoke executes PCA and cyclic orientation preview/commit/undo in addition to mass-weighted PCA and explicit vector/two-point axes. Axis/membrane JSON records include input hashes, parameters, backend/version, warnings, and confidence; secondary roll, confidence gates, antiparallel rotation, atomic rollback, and export replacement remain WSL-tested. |
| GEO-02, GEO-03 | Host smoke verified (3.1.3.1) for C2/C3/C6 calculation | C2 uses paired rotation; C3/C6 pass Host. WSL fixtures reject/diagnose nonuniform and helical arrangements. |
| MEM-01 | Host numeric/CGO smoke + WSL geometry tests | Host creates a vector-normal membrane CGO and measures 30 A plane separation; WSL tests cover plane geometry and projected auto extent. Visual inspection remains open. |
| MEM-02 | Implemented; Host slab-follow pending | The slab is a static CGO and generic static-CGO follow passes Host; the actual slab object still needs an interactive record. |
| MEM-03 | WSL adapter verified; real ANVIL pending | DUM layers cover clean/noisy/reordered/missing-layer behavior. The shell-free executable adapter is tested with a fake executable, validates exact protein identity, rejects non-rigid coordinate changes, and maps planes back after rigid reorientation. No third-party ANVIL binary is vendored or claimed as executed. |

### Styles

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| STYLE-01..04 | Host core smoke + WSL registry tests | One composed scoped style and full snapshot revert pass on Host; registry validation, normalized colors, no implicit render, load-order determinism, and detailed atom/background/representation restoration are WSL-covered. |
| STYLE-05 | WSL verified; Host render pending | `pp_style_render` is the only canonical ray/PNG entry. A reviewed Host PNG record remains open. |

### Interactions

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| INT-01..05 | Host local core + WSL failure tests | Host covers exact ligand boundary, structured result, source exclusion, manifest output, and exact clear. WSL additionally covers renderer rollback, failed-replacement restoration, output provenance fields, and `polar_qc` without global-setting mutation. |
| INT-06 | WSL verified | Blank/insertion identity, multiple ligands, state forwarding, water/metal exclusion, long names, zero results, all eight PLIP types, and malformed data are covered. |
| INT-07 | Headless Host success verified; interactive Qt pending | PLIP PDB/TER atom mapping passes Host smoke. A synchronous headless Windows PyMOL request completed the full WSL PLIP POST, exact QWE ligand filter, and Host render. Async HTTP is Qt-worker-only; timeout/unreachable/cancel create no pre-response output, but GUI responsiveness/cancel remain manual. |

`plip_overview` and `plip_publication` now dispatch through the canonical WSL
endpoint. The worker returns `pymolplugins.interactions/1.0`; Host filtering
keeps only interactions containing the exact requested ligand identities.
Real 1A4W PLIP counts were 3 sites / 18 interactions before Host filtering;
the exact `QWE:H:373` Host selection retained 14 interactions: 6 hydrogen
bonds, 4 hydrophobic contacts, 1 pi stack, 1 salt bridge, and 2 water bridges.

### Rosetta/worker service

| IDs | Status | Evidence and remaining boundary |
| --- | --- | --- |
| ROS-01 | WSL verified and live loopback verified | Worker starts without optional packages, health stays `ok`, and capabilities expose backend state and effective transport limits. |
| ROS-02 | Blocked by optional licensed environment | Initialize-once and serialization are mock-tested; repeatability requires a user-supplied legal PyRosetta environment. |
| ROS-03 | WSL mock-contract verified | CMS, bound heavy-atom bunsat, and interface-delta bunsat remain separate typed fields with explicit provenance. Numerical semantics need a real installed build. |
| ROS-04 | WSL verified at contract boundary | Strict fields/types/options, duplicate metrics, missing/overlapping chains, auth/schema, unreachable/timeout, body/response limits, and error typing are covered. Hard C++ cancellation is explicitly not claimed. |
| ROS-05 | Host connection verified; analysis POST pending | Windows PyMOL reached WSL `/v1/health` through the shared client. Metric requests send inline PDB only; redirects are refused and paths/commands/XML are rejected. A licensed analysis POST remains unavailable. |

No PyRosetta wheel, token, ANVIL/MSMS executable, or licensed binary was
downloaded or added.

## Experience and infrastructure

| Feature | Status |
| --- | --- |
| Unified plugin bootstrap (`pp_load`) and `pp_help` | Host smoke verified (3.1.3.1), including reload and the full 63-command manifest. |
| Feature-oriented package ownership | Implemented and WSL architecture-tested: canonical code lives under `pymolplugins/`; AST tests reject imports back into historical namespaces; old paths are thin compatibility shims and standalone tools live under `extras/`. |
| Runtime doctor and `pp_capabilities` | Host smoke verified for complete commands/API/cache/UNC and live PLIP worker state; configured ANVIL state is reported. |
| Transactional interaction replacement | Implemented; a failed new render restores the prior exact result instead of leaving a blank group. |
| Windows fixture/smoke automation | Implemented with versioned JSON, PowerShell gate, local Windows cwd, configurable exe/repository/output paths, and exact cleanup. |
| WSL worker operational docs/probe/start wrapper | Implemented and live-loopback verified, including authenticated allowlisted `stat/file` staging and cleanup. |
| Structure source freshness/caching | Implemented: physical-path/version-token identities, atomic persistent cache, LRU limits/pins, mapped/remote-drive/gzip materialization, and WSL staging cold/warm lookup. |
| Analysis-result freshness/caching | Not implemented; PLIP/Rosetta result caching remains a follow-up optimization. |
| Navigation-triggered automatic analysis | Hook substrate exists; policy/caching orchestration remains follow-up work. |
| Interaction table/copy/legend UI | Not implemented; structured JSON and PyMOL output tree are available now. |
| Repository license | Missing; do not infer redistribution permission. |

## Phase status

| Phase | Code status | Open evidence boundary |
| --- | --- | --- |
| Phase 0: foundations/correctness | Implemented | Real mouse/callback follow and PyMOL 2.x. |
| Phase 1: navigation/alignment | Implemented | Host file-failure, deeper interactive selection records, and PyMOL 2.x. |
| Phase 2: styles/interactions | Implemented | Host PNG and interactive Qt timeout/cancel remain; real PLIP/OpenBabel succeeds. |
| Phase 3: geometry/membrane | Implemented code/contract | Host orientation/undo and slab pass; a real user-supplied ANVIL visual record remains. |
| Phase 4: WSL services/integration | PLIP implemented; Rosetta contract implemented | Windows health and PLIP analysis forwarding pass; a legal PyRosetta numerical environment remains. |

## Verification commands

```bash
python3 -m unittest discover -s tests -v
python3 scripts/probe_worker_env.py --pretty
scripts/start_worker.sh --help
rg --files -0 -g '*.py' -g '!output/**' -g '!tmp/**' | xargs -0 python3 -m py_compile
sh -n scripts/start_worker.sh
git diff --check
```

Use `scripts/windows/run_host_smoke.ps1` for the automated Host subset and
[windows_host_acceptance.md](windows_host_acceptance.md) for the remaining
interactive and optional-backend gates.
