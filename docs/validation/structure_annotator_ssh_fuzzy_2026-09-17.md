# Structure Annotator: SSH sources, fuzzy completion and Ctrl+click

Status: implemented, packaged and installation-checked, 2026-09-17. Existing working-tree changes
are preserved; this task does not commit or install into the user's PyMOL.

## Required behavior

- Select an existing OpenSSH Host profile and enter a remote folder or CSV/TSV
  path directly. Preserve CSV path values, use remote-relative path semantics,
  and fetch structures through the existing asynchronous preparation/cache.
- Use local, source-specific annotation/configuration storage for SSH sources.
  Keep remote input files unchanged. Surface SSH/authentication/read failures;
  failed or cancelled imports retain the previous dataset and scene.
- Rank column, choice and history completion by fuzzy matching while retaining
  exact accepted values and the existing popup Tab/Enter submission contract.
- Ordinary multiple-choice clicks stage values. Ctrl+click applies the staged
  choices, saves one candidate and advances only after a successful save. This
  gesture is enabled by default; required fields and explicit batch saves remain.

## Execution record

- Baseline: heavily dirty worktree; no managed-project schema is present.
- Existing source/header workers, immutable datasets, row annotation store,
  persistent structure cache and global history are the integration owners.
- Two read-only scouts and two bounded workers all failed upstream with HTTP
  502 before producing changes. Implementation and validation remain in the
  main thread; no delegated result is treated as evidence.
- Initial focused tests: 211 ran, with one old fixture missing the new source
  selector. Updating that fixture gives 81 focused tests passing. The 13 new
  SSH/fuzzy tests also pass independently.
- `python3 -m unittest discover -s tests -q`: 780 tests pass in 22.412 s.
  Session `71521`; log `output/annotation-regression-0.9.0.log`.
- Real SSH/Windows acceptance: session `38891`,
  `python3 tests/host/run_annotator_ssh_acceptance.py --output
  output/annotation-ssh-fuzzy-0.9.0.json`. Windows PyMOL 3.1.3.1 imports 8 CSV
  rows and 3 remote structures; 12 SSH operations run off the Qt thread.
  Import click: 2.80 ms; warm Next: 1.59 ms; Cancel: 0.72 ms. Fuzzy whole fields,
  quoted filter tokens, Enter/keypad Enter/Tab, remote history, the remote
  browser, single/multiple/secondary choice gestures, Shift, required fields,
  write failure/retry, explicit batches and connection failure preservation pass.
  Six remote source-file hashes are unchanged. Logs are the adjacent `.log`
  and `.sshd.log`; supervision uses the exec session and terminal JSON.
- The SSH server is a temporary local OpenSSH daemon with its own key/config.
  This proves real Windows OpenSSH transport and PyMOL integration, not a
  production server's credentials or long-distance transfer latency. The
  cancellation test injects a remote sleep; it is identified separately.
- Existing source-review acceptance: session `14442`, Windows PyMOL 3.1.3.1,
  `output/annotation-source-review-0.9.0.json` and `.log`: passed. Review
  overrides, pasted CSV/TSV paths, mapping, failure preservation, 12 source
  hashes and 203-file background import remain intact. The earlier session
  `76153` exited 9 without JSON and was not counted as passed. Retaining the
  QApplication and writing results before cleanup resolves that harness exit.
- The task-supervisor skill's watcher is not available here. The user's
  explicit exec/write_stdin supervision contract is used for all test sessions.
- Existing complete GUI workflow: session `20169`,
  `output/annotation-workflow-0.9.0.json` and `.log`: passed, 200 rows and 128
  columns. Import: 0.103 s; warm Next median/P95/max: 1.28/1.56/1.66 ms, with
  zero click-path load/stat calls. Conditions/empty/error filters, Enter
  completion/submission, native command completion, viewport/panel/console
  shortcuts, cancellation and close/late-result isolation all pass.
- The final `scripts/package_plugin.py` build produced
  `dist/PymolPlugins-0.9.0.zip`, 436740 bytes,
  SHA-256 `41a56c38e36d3f42d59bff98c1e85865fcd59b19e65f3313892686554f397faf`.
  ZIP CRC, all 100 packaged source files, all four SSH runtime files and the
  two existing package/bootstrap regressions pass. Receipt:
  `output/annotation-package-0.9.0.json`.
- Installer session `40751` passed all assertions and wrote JSON for 0.9.0,
  including 11 modules, 72 canonical + 8 compatibility commands, six menus,
  native workspace modes, SSH imports and the installed profile control.
  Interpreter cleanup then exited 9. The harness now calls `cmd.quit(0)` only
  after successful assertions and persisted JSON. Reruns `52044` and `3321`
  exit 0 with passing JSON; `3321` checks the final archive above. Receipt and
  log: `output/plugin-package-release-0.9.0.json` and `.log`. Installation uses
  an isolated temporary plugin directory and leaves user installation unchanged.
- Final source checks include readable-file diagnostics in both remote CSV and
  directory previews, and disabling remote-browser path editing during a pending
  response. Session `86535` passes all 781 tests in 24.092 s; log:
  `output/annotation-regression-release-0.9.0.log`.
- Final Windows/OpenSSH run `71385` passes all cases, including the browser
  input guard. Receipt: `output/annotation-ssh-release-0.9.0.json`, with `.log`
  and `.sshd.log`. Import click: 5.11 ms; warm Next: 1.93 ms; Cancel: 1.23 ms;
  12 SSH operations run off the Qt thread and six input hashes remain unchanged.
- Final ZIP CRC and all 100 source-file comparisons pass, as does
  `git diff --check`. No required implementation or Windows PyMOL 3.1.3.1
  validation work remains. PyMOL 2.x/other Qt bindings and production-server
  credentials were not exercised.
