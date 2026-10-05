"""PyMOL commands for object/group resolution and object-level iteration."""

import json
import re
import threading
import traceback
import uuid
from typing import Dict, Optional, Sequence

try:
    from pymol import cmd
except ImportError:
    cmd = None  # type: ignore

from pymolplugins.core.parameters import as_bool, extend_command
from .resolver import expand_targets, resolve_targets


_PENDING_EXECUTIONS = {}


class ForeachCommandError(RuntimeError):
    """Raised when PyMOL's command parser does not finish a command line."""


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is not available in this Python process")
    return cmd


def _complete_execution(token: str) -> None:
    """A late parser acknowledgement must not create objects or revive state."""
    completed = _PENDING_EXECUTIONS.get(token)
    if completed is not None:
        completed.set()


def _validate_command_line(command: str) -> None:
    """Reject inputs which ``cmd.do`` would dispatch as independent lines."""

    if "\n" in command or "\r" in command:
        raise ValueError(
            "pp_foreach command templates must render one physical PyMOL command line"
        )
    if "\x00" in command:
        raise ValueError("pp_foreach command templates must not contain NUL bytes")


def _print_error(
    command_name: str,
    exc: Exception,
    quiet: bool,
    *,
    scene_preserved: bool,
) -> None:
    """Print expected input errors concisely and unexpected failures verbosely."""

    if quiet:
        return
    scene_status = "scene unchanged" if scene_preserved else "scene may already be changed"
    print(f"{command_name} error: {exc}; {scene_status}")
    if not isinstance(exc, ValueError):
        traceback.print_exc()


def _mutation_ambiguities(
    matches: Sequence[object], match_mode: str
) -> Dict[str, list]:
    """Return implicit/fuzzy multi-matches which require explicit consent."""

    grouped: Dict[str, list] = {}
    for value in matches:
        matched_by = str(getattr(value, "matched_by", ""))
        guarded = matched_by == "fuzzy" or (
            match_mode == "auto" and matched_by == "contains"
        )
        if not guarded:
            continue
        grouped.setdefault(str(getattr(value, "query", "")), []).append(
            str(getattr(value, "name", ""))
        )
    return {
        query: names
        for query, names in grouped.items()
        if len(names) > 1
    }


_SAFE_CONSOLE_TOKEN = re.compile(r"^[A-Za-z0-9_.*?+:/\\-]+$")


def _console_token(value: object) -> Optional[str]:
    """Return one copyable PyMOL-console token when it can be quoted safely."""

    if not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value)
    if text and _SAFE_CONSOLE_TOKEN.fullmatch(text):
        return text
    if '"' not in text:
        return '"%s"' % text
    if "'" not in text:
        return "'%s'" % text
    return None


def _commit_hint(
    targets: object,
    command: object,
    *,
    match: object,
    kinds: object,
    group_mode: object,
    fuzzy_cutoff: object,
    limit: object,
    case_sensitive: bool,
    on_error: object,
    all_matches: bool,
) -> str:
    values = {
        "targets": _console_token(targets),
        "command": _console_token(command),
        "match": _console_token(match),
        "kinds": _console_token(kinds),
        "group_mode": _console_token(group_mode),
        "fuzzy_cutoff": _console_token(fuzzy_cutoff),
        "limit": _console_token(limit),
        "on_error": _console_token(on_error),
    }
    if any(value is None for value in values.values()):
        return "Repeat the same pp_foreach command with commit=1"
    parts = [
        "pp_foreach %s" % values["targets"],
        values["command"],
        "match=%s" % values["match"],
        "kinds=%s" % values["kinds"],
        "group_mode=%s" % values["group_mode"],
        "fuzzy_cutoff=%s" % values["fuzzy_cutoff"],
        "limit=%s" % values["limit"],
        "case_sensitive=%d" % int(case_sensitive),
        "on_error=%s" % values["on_error"],
        "commit=1",
    ]
    if all_matches:
        parts.append("all_matches=1")
    return ", ".join(parts)


def _execute_checked(command_api, command: str) -> None:
    """Execute one parser line and verify that parsing reached its end.

    PyMOL 3.1's public ``cmd.do`` catches parser/CmdException failures and
    returns ``None`` for both success and failure.  Its private ``_cmd.do`` is
    not a stable status API either. Append a same-line Python acknowledgement.
    A failed native parser can defer its remaining tail until another dispatch;
    expiring the token makes that late acknowledgement harmless, without
    replaying the tail or leaving a selection in the user's scene.
    """

    _validate_command_line(command)
    token = uuid.uuid4().hex
    completed = threading.Event()
    _PENDING_EXECUTIONS[token] = completed
    wrapped = (command.rstrip() + "; /__import__('pymolplugins.targets.commands', "
        "fromlist=['_complete_execution'])._complete_execution('" + token + "')")
    dispatched_error = None
    acknowledged = False
    try:
        command_api.do(wrapped, echo=0)
        acknowledged = completed.is_set()
    except Exception as exc:
        dispatched_error = exc
    finally:
        _PENDING_EXECUTIONS.pop(token, None)

    if dispatched_error is not None:
        raise ForeachCommandError(
            "PyMOL command dispatch raised %s: %s"
            % (type(dispatched_error).__name__, dispatched_error)
        ) from dispatched_error
    if not acknowledged:
        raise ForeachCommandError(
            "PyMOL parser did not complete the command; inspect the PyMOL "
            "console for the parser/CmdException detail"
        )


def pp_target_resolve(
    targets="*",
    match="auto",
    kinds="object,group",
    group_mode="atomic",
    fuzzy_cutoff=0.72,
    limit=0,
    case_sensitive=0,
    quiet=0,
) -> Dict[str, object]:
    """Preview object/group name resolution without mutating the scene."""

    quiet_value = False
    try:
        quiet_value = as_bool(quiet)
        matches = resolve_targets(
            _require_cmd(),
            targets,
            match=str(match),
            kinds=kinds,
            fuzzy_cutoff=fuzzy_cutoff,
            limit=limit,
            case_sensitive=case_sensitive,
        )
        expanded = expand_targets(_require_cmd(), matches, str(group_mode))
        result = {
            "schema": "pymolplugins.targets/1.0",
            "status": "resolved",
            "match": str(match),
            "group_mode": str(group_mode),
            "matches": [value.to_dict() for value in matches],
            "expanded_targets": [value.to_dict() for value in expanded],
        }
        if not quiet_value:
            print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        _print_error(
            "pp_target_resolve",
            exc,
            quiet_value,
            scene_preserved=True,
        )
        return {
            "schema": "pymolplugins.targets/1.0",
            "status": "error",
            "error": str(exc),
            "scene_preserved": True,
        }


def pp_foreach(
    targets,
    command,
    match="auto",
    kinds="object,group",
    group_mode="molecules",
    fuzzy_cutoff=0.72,
    limit=0,
    case_sensitive=0,
    commit=1,
    on_error="stop",
    quiet=0,
    all_matches=0,
) -> Dict[str, object]:
    """Render or execute one PyMOL command per resolved object/group target.

    Supported placeholders are ``{target}``, ``{name}``, ``{source}``,
    ``{kind}``, ``{object_type}``, and one-based ``{index}``.  Commands execute
    by default; pass ``commit=0`` for an explicit non-mutating preview. Groups
    default to recursive molecule leaves; pass ``group_mode=atomic`` when the
    PyMOL command itself accepts a group name.
    """

    quiet_value = False
    attempted = []
    executed = []
    possibly_modified = []
    try:
        quiet_value = as_bool(quiet)
        committed = as_bool(commit)
        allow_all_matches = as_bool(all_matches)
        template = str(command)
        if not template.strip():
            raise ValueError("command template must not be empty")
        policy = str(on_error).strip().lower()
        if policy not in {"stop", "continue"}:
            raise ValueError("on_error must be stop or continue")
        canonical_match = str(match).strip().lower()
        matches = resolve_targets(
            _require_cmd(),
            targets,
            match=canonical_match,
            kinds=kinds,
            fuzzy_cutoff=fuzzy_cutoff,
            limit=limit,
            case_sensitive=case_sensitive,
        )
        ambiguities = _mutation_ambiguities(matches, canonical_match)
        if committed and ambiguities and not allow_all_matches:
            ambiguity_methods = sorted(
                {
                    value.matched_by
                    for value in matches
                    if value.query in ambiguities
                }
            )
            fuzzy_only = ambiguity_methods == ["fuzzy"]
            result = {
                "schema": "pymolplugins.foreach/1.0",
                "status": "error",
                "error_code": (
                    "ambiguous_fuzzy_match"
                    if fuzzy_only
                    else "ambiguous_auto_match"
                ),
                "error": (
                    "implicit target matching produced multiple names; use an "
                    "exact target, an explicit set match, or pass all_matches=1"
                ),
                "scene_preserved": True,
                "committed": False,
                "all_matches": False,
                "match": canonical_match,
                "matches": [value.to_dict() for value in matches],
                "ambiguous_matches": ambiguities,
                "ambiguous_match_methods": ambiguity_methods,
                "attempted_targets": [],
                "executed_targets": [],
                "possibly_modified_targets": [],
                "partial_side_effect_possible": False,
                "guidance": (
                    "For intentional set operations use explicit match=glob or "
                    "match=contains; implicit/fuzzy batches require all_matches=1."
                ),
            }
            if not quiet_value:
                print(
                    "pp_foreach refused an ambiguous implicit mutation; "
                    "nothing changed. Candidates: %s"
                    % ", ".join(
                        name for names in ambiguities.values() for name in names
                    )
                )
                print(result["guidance"])
            return result
        expanded = expand_targets(_require_cmd(), matches, str(group_mode))
        rendered = []
        for index, value in enumerate(expanded, start=1):
            context = {
                "target": value.target,
                "name": value.target,
                "source": value.source,
                "kind": value.kind,
                "object_type": value.object_type,
                "index": index,
            }
            try:
                text = template.format_map(context)
            except (KeyError, ValueError) as exc:
                raise ValueError(f"invalid command template: {exc}") from exc
            _validate_command_line(text)
            rendered.append({**value.to_dict(), "index": index, "command": text})

        errors = []
        if committed:
            for item in rendered:
                attempted.append(item["target"])
                try:
                    _execute_checked(_require_cmd(), item["command"])
                    executed.append(item["target"])
                except Exception as exc:
                    possibly_modified.append(item["target"])
                    errors.append(
                        {
                            "target": item["target"],
                            "command": item["command"],
                            "error": str(exc),
                            "side_effects_possible": True,
                        }
                    )
                    if policy == "stop":
                        break
        status = "preview"
        if committed:
            status = "applied" if not errors else "partial_error"
        safe_to_commit = not ambiguities or allow_all_matches
        hint = (
            _commit_hint(
                targets,
                command,
                match=canonical_match,
                kinds=kinds,
                group_mode=group_mode,
                fuzzy_cutoff=fuzzy_cutoff,
                limit=limit,
                case_sensitive=as_bool(case_sensitive),
                on_error=policy,
                all_matches=allow_all_matches,
            )
            if safe_to_commit
            else ""
        )
        result = {
            "schema": "pymolplugins.foreach/1.0",
            "status": status,
            "committed": committed,
            "all_matches": allow_all_matches,
            "match": canonical_match,
            "group_mode": str(group_mode),
            "matches": [value.to_dict() for value in matches],
            "commands": rendered,
            "attempted_targets": attempted,
            "executed_targets": executed,
            "possibly_modified_targets": possibly_modified,
            "partial_side_effect_possible": bool(possibly_modified),
            "errors": errors,
            "commit_hint": hint,
            "safe_to_commit": safe_to_commit,
            "match_policy": {
                "fuzzy": "multiple matches require all_matches=1 when committing",
                "auto": (
                    "implicit contains/fuzzy multi-matches require all_matches=1"
                ),
                "glob_contains": (
                    "explicit match=glob or match=contains applies to every match"
                ),
            },
            "detail": {"completion_check": "same-line parser sentinel"},
        }
        if not quiet_value:
            if status == "preview":
                print(
                    "Preview only; nothing changed. Prepared %d command(s) for %d target(s)."
                    % (len(rendered), len(rendered))
                )
                if hint:
                    print("Apply: " + hint)
                else:
                    print(
                        "Implicit matches are ambiguous; use an exact target or "
                        "add all_matches=1 intentionally."
                    )
            elif errors:
                print(
                    "pp_foreach did not complete for %d target(s); these targets "
                    "may already be modified: %s"
                    % (len(errors), ", ".join(possibly_modified))
                )
            else:
                print("pp_foreach applied %d command(s)." % len(executed))
            print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        scene_preserved = not attempted
        _print_error(
            "pp_foreach",
            exc,
            quiet_value,
            scene_preserved=scene_preserved,
        )
        return {
            "schema": "pymolplugins.foreach/1.0",
            "status": "error",
            "error": str(exc),
            "scene_preserved": scene_preserved,
            "attempted_targets": attempted,
            "executed_targets": executed,
            "possibly_modified_targets": possibly_modified,
            "partial_side_effect_possible": bool(possibly_modified),
            "errors": [],
        }


def pp_batch(
    a="", b="", commands="align {A}, {B}", output="",
    mode="reference", source="files", ids="", a_column="structure_file",
    id_column="", mapping_profile="custom", source_base="", target_format="custom",
    wsl_distribution="", save_b=1, on_error="stop", preview=0, quiet=0,
    encoding="utf-8-sig", delimiter="auto",
    match="template", a_pattern=r"A_(?P<id>.+)\.pdb", b_pattern=r"B_(?P<id>.+)\.pdb",
):
    """Process files against a reference or by equal A/B IDs.

    With no inputs, open the batch dialog. In file paired mode, A and B are
    path templates with one {id}, or use match=glob with * capturing the ID.
    With match=regex, A/B are directories and a_pattern/b_pattern are filename
    expressions with (?P<id>...) or one capture group. In CSV mode, A is the CSV filename,
    a_column selects A paths, and B is the reference filename or B column.
    Recipes accept {A}, {B}, {id}, {index} and output/source path fields.
    File outputs use a separate directory; preview=1 never loads or writes.
    Console execution is synchronous; the GUI can stop between steps.
    """
    quiet_value, runner = False, None
    try:
        quiet_value = as_bool(quiet)
        if not a and not b:
            from .batch_widgets import show_batch_dialog
            return show_batch_dialog()
        from .batch_runner import BatchRunner
        from .batch_sources import BatchConfig, prepare_batch
        config = BatchConfig(a=str(a), b=str(b), commands=str(commands), output=str(output),
                             mode=str(mode), source=str(source), ids=str(ids),
                             a_column=str(a_column), id_column=str(id_column),
                             mapping_profile=str(mapping_profile), source_base=str(source_base),
                             target_format=str(target_format), wsl_distribution=str(wsl_distribution),
                             save_b=as_bool(save_b), on_error=str(on_error),
                             encoding=str(encoding), delimiter=str(delimiter), match=str(match),
                             a_pattern=str(a_pattern), b_pattern=str(b_pattern))
        plan = prepare_batch(config)
        if as_bool(preview):
            result = dict(schema="pymolplugins.batch/1.0", status="preview",
                          scene_preserved=True, plan=plan.to_dict())
            if not quiet_value:
                print("Preview only; nothing changed. %d pair(s) ready. Repeat with preview=0 to run." % len(plan.items))
        else:
            runner = BatchRunner(plan, _require_cmd(), _execute_checked)
            runner.start()
            while runner.advance():
                pass
            result = runner.report()
            if not quiet_value:
                print("Batch %s: %d completed, %d failed. Results: %s" %
                      (result["status"], result["completed"], result["failed"], result["report_path"]))
        return result
    except Exception as exc:
        if runner is not None:
            runner.abort(exc)
        _print_error("pp_batch", exc, quiet_value, scene_preserved=runner is None or not runner.started_at)
        return runner.report() if runner is not None else dict(
            schema="pymolplugins.batch/1.0", status="error", error=str(exc), scene_preserved=True)


def register_commands(command_api=None) -> None:
    selected = command_api if command_api is not None else cmd
    if selected is None:
        return
    extend_command(selected, "pp_target_resolve", pp_target_resolve, literal=True)
    extend_command(selected, "pp_foreach", pp_foreach, literal=True)
    extend_command(selected, "pp_batch", pp_batch)


register_commands()
