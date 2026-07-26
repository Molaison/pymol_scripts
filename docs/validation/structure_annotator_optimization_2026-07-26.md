# Structure Annotator batch/large-table validation: 2026-07-26

This record covers the optimization work layered on commit `ef64af7`. The
tested worktree adds bounded WSL batch stat, incremental annotation-log state,
and a lazy Scores table. Windows Host evidence is kept separate from WSL-only
worker evidence.

## WSL suite and real worker

The repository-wide suite completed with:

```text
Ran 352 tests in 17.538s
OK
```

The final count includes regressions for canonical-equivalent rows sharing one
single stat and for rejecting request-level or mismatched code/status errors
inside a batch item.

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
