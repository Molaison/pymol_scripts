# Structure Annotator batch/large-table validation: 2026-07-26

This record covers the optimization work layered on commits `ef64af7` and
`4f1dfc2`. The tested worktree adds bounded WSL batch stat, incremental
annotation-log state, a lazy Scores table, per-snapshot path-probe reuse, and
background Qt CSV preparation. Windows Host evidence is kept separate from
WSL-only worker evidence.

## WSL suite and real worker

The repository-wide suite, rerun after the final path-cache and asynchronous
prepare refinements, completed with:

```text
Ran 364 tests in 16.220s
OK
```

The final count includes regressions for canonical-equivalent rows sharing one
single stat, request-level and per-item batch errors, heterogeneous source
bases, immutable worker preparation, stale result suppression, and fresh
transaction rollback snapshots.

A real foreground standard-library worker bound `127.0.0.1:18765`, required a
Bearer token, and exposed root alias `repo` without exposing its server path.
One client run produced:

```json
{
  "batch": [
    {"path": "data/helical_bundle.pdb", "ok": true, "size": 62712},
    {"path": "data/does-not-exist.pdb", "ok": false,
     "error": "stage_file_not_found"}
  ],
  "max_batch_items": 256,
  "single_matches_batch": true,
  "download_matches_single": true,
  "downloaded_bytes": 62712
}
```

The worker request log was exactly:

```text
GET  /v1/capabilities       200
POST /v1/stage/stat-batch   200
POST /v1/stage/stat         200
POST /v1/stage/file         200
```

After the strict per-item code/status validation was added, a final rerun of
the current client produced `stat-batch -> stat -> file`, returned the same
62,712-byte identity, and preserved `stage_file_not_found` for the missing
item.

This proves the deployed endpoint and legacy endpoint coexistence in WSL. It
does not claim that the Windows PyMOL controller itself crossed the HTTP
boundary; that remains a separate manual gate.

## Windows PyMOL 3.1.3.1

The automated Host harness ran in Windows 10 build 19045 with PyMOL 3.1.3.1
and its Python 3.10.16. All 13 checks passed, including the real `cmd.do` CSV
load/annotate/export/restore path:

```text
automatic_status: pass
overall_status:   automatic_pass_manual_pending
started:          2026-07-26T02:55:36.664401+00:00
completed:        2026-07-26T02:55:37.650404+00:00
```

Record:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-smoke-current.json
sha256 e9ba5aef46748c8bc0d48be5892574daf8d6e3ed885be5517e2229d6e7b93790
```

## Real Windows PyQt5 behavior

The visible Qt smoke confirmed:

- the Scores main table is `QTableView`, not `QTableWidget`;
- active click resolved through the entry-ID map to `sa_goto(2)`;
- Preview, filtered, missing, and stale-generation clicks made zero `sa_goto`
  calls; the stale table refreshed before requiring a second click;
- changing one annotation emitted `dataChanged` only for cell `(0, 3)`;
- reloading the same source generation changed score `0.0 -> 9.9` and status
  `available -> missing`, rather than retaining stale model rows;
- fuzzy completion still passed for mouse and keyboard paths.

Record:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-current.json
sha256 98ab172496b16f5fcbcfa8aa08a658b1f429abc885f4467c8a106600246cfec7
```

## Async CSV prepare/commit follow-up

The follow-up expanded the same final 364-test suite with regressions for:

- exact duplicate local paths issuing one `isfile/access` pair per resolve;
- distinct mapping aliases sharing one canonical physical candidate probe;
- pure prepare making no PyMOL/scene mutation;
- pre-cancel result suppression and CSV fingerprint change rejection;
- a failed prepared commit restoring the cursor/scene captured at completion
  time, after the user navigated while preparation was running;
- deferred history restore recovering source intent without probing a remote
  source during panel construction.

After the last cache-lock and stale-result refinements, the Windows PyMOL
3.1.3.1 automated gate again passed all 13 checks. Record:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-smoke-20260726-final.json
started   2026-07-26T04:06:11.290284+00:00
completed 2026-07-26T04:06:11.863503+00:00
sha256 27b6360afb0ca68a3fa031a397569d906eb3e21b7b728f8d158a8a09ac11fe8c
```

The latest bundled-PyQt5 record returned `status=pass` and measured:

| Check | Observed |
| --- | ---: |
| Panel construction | 17.70 ms |
| Background Preview submission | 0.33 ms |
| Cancel return | 0.20 ms |
| 20 ms heartbeat ticks during a 250 ms blocked worker | 10 |
| Late cancelled A after fast B | B remained displayed |
| Preview completion thread | Qt application thread |
| Load commit thread | Qt application thread |
| Annotation-config change during Load | stale plan suppressed |
| In-process annotation change during Load | stale plan suppressed |

The Load check used the real Windows worker-side CSV parse/resolve/materialize
path and replaced only the final commit callback with an observer; the separate
Host PyMOL gate exercised the real synchronous transaction and `cmd.load`.
This preserves the required boundary without pretending the standalone bundled
Python Qt smoke is a running PyMOL scene.

Cancel is deliberately result suppression, not forced thread termination. The
dedicated pool has two threads, so two non-cooperative FUSE/HTTP reads may hold
both slots until their configured timeouts and a newer request may wait behind
them. Their completions are still discarded and cannot mutate the panel,
`STATE`, or scene. `QThreadPool.tryTake` is not used because it is unavailable
before Qt 5.9 and has an upstream-documented auto-delete race; changing runnable
ownership remains a separate PyMOL 2.x/PySide2 compatibility task.

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-20260726-final.json
completed 2026-07-26T03:57:48.977629+00:00
sha256 8d40c2944ccb1e1397ad4f1ff206e20126a400ef4a3a46894b07ac61ee45c497
```

## Incremental batch-request byte accounting

The former Host chunk planner serialized the complete candidate request after
each appended path. A 50,000-path fixture containing CJK, Greek characters,
backslashes and quotes therefore spent a median 0.9596 s planning requests.
The revised planner serializes the fixed empty-list envelope once and each JSON
string token once, then adds the exact comma/token UTF-8 bytes incrementally.

Eleven post-change runs measured a median 0.0302 s, a **31.8x speedup**. Both
implementations produced the same 196 greedy chunks (256 paths in the first,
80 in the last), and the largest final request was 15,169 bytes. A deterministic
escape/Unicode regression, exact oversized-item rejection, serialization-shape
regression, exact-limit acceptance, partial-yield error, empty-input regression,
and 5,000 seeded randomized comparisons all matched the former full-JSON
reference exactly. The repository-wide suite expanded to 369 tests and
completed in 21.097 s with `OK`. The same 23 staging-client tests passed in
7.826 s under the Windows Python 3.10.16 bundled with PyMOL 3.1.3.1.

## Single-construction CSV entries

The former parser built a complete immutable `AnnotationEntry` for every raw
row, inferred column types from those entries, and then rebuilt every entry with
`dataclasses.replace` to attach typed display metadata. The revised parser
infers `ColumnSpec` directly from the current raw-row snapshot and constructs
each final entry once. A constructor-count regression observes `__post_init__`
itself, so a reintroduced `replace` would fail as `2N`; a separate full
`dataclasses.asdict` comparison matches the legacy two-pass output across
duplicate/empty IDs, ragged rows, numeric/text values, IDs and error fields.

The benchmark loaded a 50,000-row, 8-column UTF-8 CSV with seven display
columns. Five baseline runs had a median 0.5746 s; seven revised runs had a
median 0.4082 s, a **29.0% reduction**. Tracemalloc peak fell from 124,424,140
bytes (118.7 MiB) to 101,054,125 bytes (96.4 MiB), an **18.8% reduction**.
Retained snapshot memory remained effectively unchanged (77,048,258 versus
77,038,658 bytes), confirming that the reduction removes transient duplicate
entries rather than deferring data outside the immutable dataset.

The final WSL suite passed 371/371 in 17.680 s. All 21 dataset/model tests also
passed in 0.043 s under PyMOL 3.1.3.1's bundled Windows Python 3.10.16. The real
Windows PyMOL 3.1.3.1 Host gate passed all 13 checks, including the CSV
load/annotate/export/restore transaction:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-smoke-20260726-anno13.json
started   2026-07-26T04:28:32.658424+00:00
completed 2026-07-26T04:28:33.192741+00:00
sha256 6fb8b38f4cb044bbfb4f82dc4f974757a3db767c7f5ebbab3444f7b4fc17840a
automatic_status pass
overall_status   automatic_pass_manual_pending
```

## Cancel and panel-close registry lifetime

Cancel now removes its job metadata from the module registry before setting the
worker-owned cancellation event. The `QThreadPool` still owns queued/running
runnables; the bundled PyQt5 ownership probe changed from `ispyowned=True` to
`False` immediately after `start()`. A queued completion or later worker emit
therefore finds no registry entry and returns without resolving the panel
weakref. This also removes two panel bookkeeping fields which were written but
never read.

The real Windows PyQt5 smoke kept the existing 10 heartbeat ticks and 0.19 ms
Cancel return, asserted immediate registry cleanup, then started a separate
panel with a blocked worker and closed it. The registry was empty immediately;
after releasing the worker, completion did not reach the closed panel or change
its table or the dataset generation. An independent ownership stress filled the
pool with two running and 500 queued jobs, cancelled and unregistered all of
them, then forced repeated GC: no runnable was released early, every queued job
observed its cancel event after the pool opened, no panel callback ran, and all
wrappers were reclaimed afterward. Live-job module reload also retained exactly
one completion for an uncancelled job while cancelled late emit remained a
no-op. Record:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-20260726-anno14.json
completed 2026-07-26T04:41:15.077984+00:00
sha256 a9c0ebb6e9ee103c2574c949bb37e064958ab65087ad9b0ab2155cc7fd5140b9
async_cancel_registry_released true
async_close_registry_released  true
async_close_late_suppressed    true
```

The final WSL suite passed 372/372 in 22.267 s. The Windows PyMOL 3.1.3.1 Host
gate again passed 13/13, including the real CSV transaction:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-smoke-20260726-anno14.json
started   2026-07-26T04:43:45.228533+00:00
completed 2026-07-26T04:43:46.040162+00:00
sha256 21bea7aab5f02e4cf3671907123ace5e0f092333fd1843ee3413259c1ba7fed5
automatic_status pass
overall_status   automatic_pass_manual_pending
```

This remains cooperative cancellation: two non-cooperative reads can still
hold both dedicated pool slots until timeout, and `QThreadPool.tryTake` remains
deferred because of its Qt-version and auto-delete ownership constraints.

## 50,000 x 8 Windows Qt benchmark

The repeatable benchmark used the same bundled PyQt5 interpreter. It retained
50,000 immutable row objects as a common data baseline, then measured only the
extra table/model RSS:

| Measure | Lazy model | Legacy widget |
| --- | ---: | ---: |
| Extra RSS | 12,881,920 bytes (12.3 MiB) | 209,453,056 bytes (199.8 MiB) |
| Population | 0.0416 s | 0.8932 s |

RSS reduction was **93.85%**. Across 200 cursor refreshes, median was 0.124 ms,
p95 was 0.258 ms, and maximum was 0.615 ms. This passes the specification
thresholds of at least 70% RSS reduction, p95 below 16 ms, and maximum below
50 ms.

Record:

```text
C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-performance-current.json
sha256 d22a379bfbae10cdc21d84f34a9c651aa31e98efc08bf853c75290b771d960fd
```

## Remaining environment gates

- PyMOL 2.x remains unverified; the real Host build is 3.1.3.1.
- Windows-PyMOL-to-WSL batch staging remains manual even though both sides and
  controller integration are independently tested.
- Annotation sidecar locking remains process-local; two PyMOL processes must
  not concurrently edit the same JSONL.
- Licensed PyRosetta numerics, real ANVIL, and real-mouse callback-owned CGO
  behavior are outside this optimization record.
