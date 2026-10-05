# Structure Annotator: real remote source and Open data follow-up

Status: completed, packaged and installation-checked as 0.9.1 on 2026-09-18.
Investigation started on 2026-09-17.

## Acceptance target

Open the user's exact source through its SSH profile:
`/xcfhome/zpzeng/project_run_log/hallucination_paper_preparation/010_rna_aptamer_target_protein/outputs/rna_evaluator_comparison/22_bivalent_40nt_review/01_scores.csv`.
Make the Open data page understandable with an explicit connection, one path,
and one Open action. Preserve advanced options and the default Ctrl+click
behavior from 0.9.0. Keep source tables, structures and existing sidecars intact.

## Evidence and decisions

- The real Windows K: view confirms that both `01_scores.csv` and
  `01_scores.tsv` exist. Each has 600 rows and 56 columns. The CSV is 1,186,447
  bytes. This mapped-drive read is source diagnosis, not SSH acceptance.
- `review.override.json` explicitly selects `relative_path`, with score columns
  `bivalent_worst_contact_pae` and `bivalent_weak_arm_plddt`. The CSV's
  `structure_path` still contains older `/mnt/sfs/...` paths.
- Native Windows OpenSSH and existing profiles are present. Source input still
  defaults to This computer and interprets bare Linux paths as Windows-local
  paths. Connection selection is split into two controls and file type must be
  selected separately before opening a pasted file.
- A read-only UI scout failed with upstream HTTP 502 before returning findings;
  investigation and integration remain in the main thread.
- Native client reproduction `58913` failed with
  `Permission denied (keyboard-interactive)`, recorded in
  `output/source-ui-ssh-diagnostic.json`. Version 0.9.0 forced BatchMode and
  bypassed the installed `.ssh/ssh_otp/ssh_totp.py` login program.
- Reproduction `64266` using that existing login program on the same `x`
  profile reads the exact remote CSV and all 56 headers successfully. Record:
  `output/source-ui-ssh-configured-client.json`. No secrets are copied into
  the plugin, logs or source data. The fix selects the configured login program
  before connecting; it does not retry failures through another transport.
- Real GUI attempt `8515` exposed a second defect before opening the table:
  Windows PyMOL's Python 3.10 raises `bad query field: ''` when the empty query
  of a default-config SSH URI is passed to `parse_qs(..., strict_parsing=True)`.
  The 0.9.0 local SSH harness used explicit config query parameters and missed
  this default-config path. Empty queries now map directly to no options.
- Follow-up `97093` passed source reads but timed out waiting for the Open
  action to finish. Logs contain no SSH failure; the harness now records live
  source/job/queued-action state and Python thread stacks at failure to locate
  the remaining UI transition. A separate real Qt focus check preserves the
  exact lowercase `x` alias across completion, focus change and disabling.
- State/stack capture in `76232` confirms the UI selected `x`, selected
  `relative_path`, and submitted the load correctly. The worker was waiting
  in Windows `subprocess._readerthread` for the login program's stderr. A
  multiplexing helper can retain inherited handles after the requested command.
  Diagnostic output now uses a temporary file, so completion follows the command
  process. A real child-process regression covers inherited stderr, and wrapper
  cancellation terminates the owned subprocess tree.
- Further capture `52141` shows the login process is still running, with no
  response bytes or stderr. The installed OTP helper reserves each TOTP counter
  once and waits for the next unused window. Opening a fresh SSH connection for
  every stat/header/review/download therefore accumulates authentication waits;
  stderr capture alone does not solve this. The transport now keeps one owned,
  authenticated SSH read channel per profile/program, with framed binary replies,
  serialized requests and explicit cancellation. OTP policy remains unchanged.
- The real SSH reproduction, implementation and original-source GUI acceptance
  are complete; the records below distinguish failed attempts from passing runs.

## Verified current result

- Session `90799` passes on real Windows PyMOL 3.1.3.1 using the exact CSV above
  and the existing `x` profile/login program. Output:
  `output/source-ui-real-csv-session.json` and `.log`.
- All 600 rows and 56 columns load, `relative_path` is selected from the review
  configuration, and the first structure contains 2399 atoms. Fourteen remote
  operations share exactly one SSH connection. The click takes 2.37 ms and
  import after the existing authenticated connection takes 0.429 s. Initial
  OTP-window waiting is not included in that import timing.
- The table SHA-256 remains
  `6012193442a7f61e3e8240e8c1471f193eaf12f7e8d088db95f9681e32052a97`;
  the first structure and remote review configuration hashes also match.
- The visible opening page has one connection, one path, one Open button and
  collapsed import options. CSV is detected from the pasted path. The exact
  lowercase `x` survives fuzzy completion. Screenshots:
  `output/source-ui-real-csv-session-open.png` and
  `output/source-ui-real-csv-session.png`.
- Session `57266`: all 788 tests pass in 23.990 s. Log:
  `output/source-ui-full-regression.log`. Cases include response framing,
  binary payloads, one connection across file errors, concurrent file identities,
  explicit program failure, wrapper cancellation and exact-case fuzzy matching.
- Existing Windows source/review checks pass in `85088`, including pasted
  CSV/TSV paths, configuration restore, mapping, 203-file background import,
  cancellation and original file hashes. Output:
  `output/source-ui-existing-workflow.json` and `.log`.
- Windows SSH/interaction regression `6798` passes direct table/folder reads,
  source history, fuzzy whole-field/quoted completion, default Ctrl+click for
  single/multiple/secondary fields, required fields, Shift, explicit batches,
  write failure/retry and cancellation of an active login process. Output:
  `output/source-ui-ssh-regression.json`, `.log` and `.sshd.log`. The six fixture
  source hashes are unchanged. This complements the real-source run above.
- `dist/PymolPlugins-0.9.1.zip`: 440021 bytes; SHA-256
  `57b1e32653f1c0ac7049b48c949ea61f0c3045c6d0ed932f150d311f28041d8b`.
  ZIP CRC and all 100 packaged source-file comparisons pass. Receipt:
  `output/source-ui-package.json`. The two package/bootstrap tests and
  `git diff --check` also pass.
- Real Plugin Manager installation `52552` exits 0 and passes on Windows
  PyMOL 3.1.3.1: 0.9.1, 11 modules, 72 canonical and eight compatibility
  commands, six menus, shared workspace modes and the installed single
  connection selector. Output: `output/plugin-package-0.9.1.json` and `.log`.
  Installation is isolated; the user's installed plugin folder is unchanged.
- No required implementation or validation work remains for this source and
  the tested Windows PyMOL target. Initial OTP-window waiting remains governed
  by the existing login program. PyMOL 2.x and other Qt bindings were not tested.
