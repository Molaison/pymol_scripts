# PymolPlugins task-oriented command UX specification

Status: active specification, 2026-07-27 (Asia/Shanghai)

This document treats command efficiency as user efficiency: how quickly a new
or returning user can discover the right workflow, predict its side effects,
recover from an error, and obtain a scientifically useful result. Runtime
performance remains important but is not a substitute for this contract.

## 1. Baseline problem

Before this iteration the manifest exposed 63 canonical `pp_*` commands.
Including optional Structure Annotator console commands, the package defined 85
`pp_*`/`sa_*` functions. Of the 63 canonical callbacks, 37 had no function
docstring and 15 exposed at least eight parameters. `pp_help` returned only a
category/name inventory, without usage, examples, side effects, recovery, or a
recommended next action. Four grouping commands were loaded by `pp_load` but
were absent from the manifest and bypassed the shared console adapter.

Several safe low-level commands used action verbs while defaulting to preview.
`pp_foreach` now executes by default after the exact no-op repro; explicit
`commit=0` is its preview form. Advanced alignment and orientation primitives
retain preview/commit records. A boolean such as `committed: false` is not an
adequate human explanation: the first visible line must say that nothing was
changed and show the shortest way to apply the reviewed result.

## 2. Evaluation rubric

Every task and public entry point is scored from 1 (unusable) to 5 (excellent):

| Dimension | 1 | 3 | 5 |
| --- | --- | --- | --- |
| Discoverability | requires source/docs search | category help finds a command | task phrase finds a runnable example |
| Steps to success | more than five remembered actions | two to four documented actions | one command, or preview plus one apply |
| Parameter clarity | internal names and many required knobs | defaults work but advanced knobs are exposed | task words, inferred inputs, advanced options hidden |
| Outcome predictability | side effects are surprising or silent | structured status exists | first line states changed/unchanged targets and undo |
| Error recovery | traceback/empty result only | error identifies bad value | actionable fix, fuzzy suggestion, state preserved |
| Scientific usefulness | produces unclear/dead output | useful with manual interpretation | preset matches a real decision and explains provenance |

No workflow is accepted below 4 for outcome predictability or error recovery.
A recommended workflow must reach at least 4 in every dimension.

## 3. Progressive disclosure

The command surface has three levels:

1. **Tasks**: at most twelve recommended workflows shown by default. Each has a
   plain-language purpose and one copyable minimal example.
2. **Categories**: navigation, objects/groups, annotation, alignment, geometry,
   styles, interactions, and worker metrics. Category help shows the normal
   workflow first and optional variations second.
3. **Advanced primitives**: existing `pp_nav_*`, `pp_align_*`, `pp_axis_*`,
   `pp_interact_*`, `sa_*`, and compatibility commands. They remain callable
   for scripts but no longer dominate first-run help.

Existing command names and return schemas remain compatible unless a reproduced
correctness bug makes that impossible. New task facades delegate to canonical
implementations; they must not create a second cursor, transform store, cache,
or interaction result model.

## 4. Common task acceptance

The default guide must cover these tasks without requiring source-code reading:

1. verify installation and understand the Windows Host versus WSL boundary;
2. browse files, existing objects, or root groups and move next/previous;
3. apply a PyMOL action to one object, fuzzy-matched objects, or group members;
4. open Structure Annotator, import a score CSV, annotate, undo, and export;
5. align a payload by selections, sequence motif, or ligand;
6. orient by PCA/cyclic symmetry, draw a membrane slab, or move a group rigidly;
7. apply/revert a named visualization style;
8. inspect protein/protein or protein/ligand interactions and clear/export them;
9. diagnose and run optional WSL PLIP/Rosetta/ANVIL backends.

For the normal case, discovery plus execution is at most three commands and no
recommended command has more than four required arguments. Optional scientific
parameters live in a named preset/profile or advanced help.

## 5. Help and result contract

`pp_help` must accept a task phrase, category, preset, or exact/fuzzy command
name. Its human output includes:

- purpose and whether the command reads, previews, or mutates state;
- minimal syntax with defaults omitted;
- one realistic example and its expected visible result;
- apply/commit and undo/clear/stop instructions where relevant;
- environment requirements and the next diagnostic for optional backends;
- related task-level and advanced commands.

Machine-readable results remain returned as dictionaries. Human console output
leads with one concise outcome line. Preview results must literally say
`Preview only; nothing changed` and supply an actionable next step. Successful
mutations state the number and names of affected targets plus the available
undo/clear/stop command. Expected input errors lead with the invalid field,
accepted forms, fuzzy alternatives when possible, and confirmation that prior
state was preserved. Detailed tracebacks may follow for debugging but may not
be the only explanation. `quiet=1` suppresses human output without changing the
return schema.

## 6. Parameter and naming contract

- Task-level commands use user concepts (`target`, `group`, `file`, `motif`,
  `ligand`, `axis`, `membrane`, `style`, `contacts`) rather than controller
  internals.
- The same concept uses the same parameter name across task commands.
- Auto detection may select files/objects/groups only when unambiguous; the
  result reports what was inferred. Ambiguity returns choices instead of
  guessing a destructive action.
- Fuzzy matching is convenient but bounded. A mutating task may auto-accept
  only one high-confidence match; multiple matches require preview or an
  explicit all-matches option.
- Task-level action names execute by default; supported non-mutating checks use
  `preview=1`. Advanced scientific primitives retain their preview defaults,
  while `pp_foreach` intentionally uses explicit `commit=0` after its reproduced
  default-no-op failure.
- Boolean tokens use the shared strict parser. Lists, Windows paths, UNC paths,
  selections, regexes, and templates use the shared quote normalization and
  have real PyMOL parser regressions.

## 7. Bug and environment gates

Every P0/P1 fix starts with an exact user-facing repro and a regression at the
lowest faithful boundary. Parser, Qt, object-type, matrix, or Host/WSL bugs must
also pass their real boundary gate; WSL fake-command tests are not evidence of
Windows Host behavior. The minimum final matrix is:

- complete WSL standard-library/fake-PyMOL suite;
- Windows Python bundled with the reviewed PyMOL build;
- real bundled Qt for command guide/Structure Annotator behavior;
- real Windows PyMOL smoke through `cmd.do` for parser and scene mutation;
- explicit open gate for PyMOL 2.x until that runtime is available.

## 8. Iteration policy

Work proceeds in small, reviewable batches: reproduce, specify, implement,
regress, run the relevant Host boundary, independently review, then commit.
After each batch, the same rubric is rescored and the next highest user-impact
failure is selected. Runtime micro-optimizations do not take priority over a
workflow that users cannot discover or correctly execute.

## 9. Reproduced implementation backlog

The first parallel audit produced the following historical input backlog; later
sections record the implemented command/help surface. `P0` means a
normal user path can lose data, delete/overwrite unrelated scene state, commit
a scientifically stale result, or become impossible to recover without
reloading the session.

| ID | Priority | Reproduced failure | Minimum acceptance |
| --- | --- | --- | --- |
| ANNO-U01 | P0 | CSV files differing only by basename case share row IDs/state/annotation sidecars | Distinct files never share mutable state; legacy state migration is explicit |
| ANNO-U02 | P0 | Re-imported exported annotation is treated only as metadata and the next export overwrites it blank | Export/import/export preserves reviewed values without a sidecar |
| ANNO-U03 | P0 | Directory annotation rewrites source PDB/CIF in place; failure truncates it and values containing spaces are cut | Atomic replace with validated backup/rollback; complete value round-trip |
| GROUP-U01 | P0 | `replace=1` deletes a non-group object with the requested group name | Only an owned/real group may be replaced; all other conflicts leave scene unchanged |
| GROUP-U02 | P0 | `align_in_groups valid_group` does nothing while a typo can fall back to all groups | Exact selected groups only; no-match is an error; deterministic/explicit reference |
| GEO-U01 | P0 | Axis/membrane drawing deletes an unrelated same-name object | Ownership manifest, collision preflight, transaction, owned-only clear/replace |
| GEO-U02 | P0 | Orientation undo silently overwrites a later manual transform | Committed fingerprint, operation ID, stale refusal, explicit force |
| GEO-U03 | P0 | Cyclic auto merges same chain IDs across objects and C2 trusts raw atom order | Explicit subunit identity and residue correspondence; ambiguity rejected |
| GEO-U04 | P0 | Follow undo/rebuild failure leaves only part of a group restored | All-or-nothing TTT/hook rollback with structured rollback failures |
| SEQ-U01 | P0 | Bounded wildcards enumerate hundreds of thousands of matches before `first`/`unique` returns | Streaming early stop plus bounded `all`/truncation/cancel contract |
| INT-U01 | P0 | Async PLIP result commits after the source object was replaced under the same name | Main-thread current source fingerprint gate before any output mutation |
| INT-U02 | P0 | Older async request finishing late overwrites a newer result for the same output group | Per-output latest epoch plus source fingerprint; independent groups unaffected |
| ROS-U01 | P0 | NaN/Inf, empty metrics, wrong source hash or wrong partners can pass the response path | Exact request-bound finite typed scientific result validation end to end |
| NAV-U01 | P0 | Invalid stop option/key restore failure drops controller but leaves owned file objects/keys | Validate before mutation; best-effort cleanup; retain retryable degraded ownership |
| TARGET-U01 | P0 | A semicolon template can mutate before failure while reporting zero executed targets | Report non-atomic/possibly modified targets or disallow compound task-level actions |
| CMD-U01 | P0 | Live plugin registers 71 commands while help/doctor declare 63; four grouping callbacks bypass parsing adapter | Declared canonical/legacy inventory equals live surface and callback identity is checked |

The next wave covers navigation focus/coverage and boundary hooks, regex/list
parsing, alignment preview/undo record selection, worker security/readiness/job
history, interaction preset parameters, style scope reporting, and task-level
facades. These remain important, but they do not precede the P0 integrity work
above.

## 10. Tier-1 facade specification

The implemented public inventory is now 71 canonical commands: eight Tier-1
facades plus the unchanged 63 advanced primitives. Eight compatibility names
remain separately declared, for a total live declared surface of 79. Only the
eight English facade names are new; translated phrases are help aliases and do
not occupy additional PyMOL command names.

The manually loaded `screen_boost.py` and `file_management.py` adapters are
outside that declared/autoloaded 79-command surface. They add historical aliases
only when explicitly loaded, share the canonical navigation session, and remain
documented as compatibility—not Tier-1 discovery targets.

| Facade | Normal form | Safe default and forbidden inference |
| --- | --- | --- |
| `pp_show` | `pp_show surface, TARGET` | `target=all` still resolves only molecule objects/recursive group molecule leaves; commit snapshots every state and adds a hidden same-session object identity property when the Host supports it; rejects non-molecules and implicit multi-match |
| `pp_nav` | `pp_nav TARGET`; `pp_nav next` | No arguments report status; source auto-detection must resolve wholly to files, groups, or molecules; never replaces an active session implicitly; control actions reject start-only options rather than ignoring them |
| `pp_align` | `pp_align MOBILE, REFERENCE, motif=GGH` | Executes by default, `preview=1` is explicit; motif and ligand are mutually exclusive and ambiguous scientific anchors are rejected by the canonical primitive |
| `pp_orient` | `pp_orient TARGET` | Requires an explicit object/group or explicit `all`; executes PCA-to-Z by default, accepts cyclic/explicit vector, and keeps canonical confidence/stale-undo gates |
| `pp_contacts` | `pp_contacts COMPLEX, focus=organic` | Auto uses local contacts only and requires one ligand or two explicit partners; async PLIP is inspected with `pp_jobs`, then its exact completed run is added to `pp_undo` |
| `pp_metrics` | `pp_metrics COMPLEX, target=A, binder=B` | Source, target, and binder are mandatory; `preview=1` resolves the one molecule and request plan without PDB export/network; GUI default submits asynchronously without a synchronous capabilities probe, and backend/readiness failures remain in `pp_jobs` history; `wait=1` is the deliberate blocking path |
| `pp_jobs` | `pp_jobs`; `pp_jobs JOB_ID`; `pp_jobs cancel, JOB_ID` | Human output is bounded and includes recent terminal failures; cancellation is result suppression, not backend termination |
| `pp_undo` | `pp_undo`; `pp_undo OPERATION_ID`; `pp_undo list` | Resolves one journal operation, delegates its exact stored canonical reference, refuses stale state by default, and marks it undone only after verified restoration |

All facade responses use `pymolplugins.task-command/1.0` with `task`, `status`,
`delegated_command`, and `result`. A successful journaled scene mutation also
returns an `operation_id` and copyable `undo`. `pp_show`, `pp_align`,
`pp_orient`, `pp_contacts`, `pp_metrics`, and `pp_undo` expose `preview`; status/job facades do
not invent one, and no facade exposes `commit`. Their journal points to canonical alignment,
orientation, style, and interaction records; it does not duplicate those state
machines. `pp_show` alone owns a bounded all-state representation snapshot
because no canonical PyMOL show/hide undo record exists. On property-capable
Hosts it also adds a hidden object identity marker at commit; older APIs retain
the atom/source fingerprint fallback and report that weaker identity mode. An
async PLIP submission has no undo before it renders; its owner-qualified
completion event then adds exactly one journal entry. Stable-group replacement
supersedes the older entry, so an old operation can never clear a newer run.

Help search normalizes with Unicode NFKC and `casefold`, retains Unicode words,
and ranks exact aliases then full token coverage before prefix/fuzzy matches.
Search results report `matched_by`, `matched_terms`, and `unmatched_terms`.
Curated natural-language aliases route common intent (`browse groups`,
`sequence selection`, `structure align`, `axis align`, and `ligand contact`)
before nearby advanced primitives. Results below the 0.50 catalog confidence
boundary are omitted, so arbitrary text does not become confident-looking but
unrelated advice.
Only a truly empty help argument opens the default task guide; a nonempty
unrecognized Unicode or punctuation query returns `no_match`.

## 11. Current validation boundary

The facade/journal, registration, help ranking, Unicode behavior, rollback,
all-state representation, object-marker fallback, async job-history, and
stale-undo paths pass the 619-test non-loopback WSL subset on both Python 3.12
and 3.13. The four socket-bound worker modules remain a separately recorded
sandbox gate. Host
harness 1.5.0 contains a state-1 representation round trip, but does not yet
exercise the newer all-state/property-marker or asynchronous backend-failure
history gates. The 2026-07-27 task-facade Host launch is not passed: this run is
inside an isolated network/user namespace and cannot reach WSL's Windows
interop socket (`UtilConnectUnix` / `UtilBindVsockAnyPort`). Therefore none of
the target UX scores are promoted to Host-verified status yet.
