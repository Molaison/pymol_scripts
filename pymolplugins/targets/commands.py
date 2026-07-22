"""PyMOL commands for object/group resolution and object-level iteration."""

import json
import traceback
import uuid
from typing import Dict

try:
    from pymol import cmd
except ImportError:
    cmd = None  # type: ignore

from pymolplugins.core.parameters import as_bool
from .resolver import expand_targets, resolve_targets


_EXECUTION_MARKER_PREFIX = "ppfe_ack_"


class ForeachCommandError(RuntimeError):
    """Raised when PyMOL's command parser does not finish a command line."""


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is not available in this Python process")
    return cmd


def _name_set(command_api) -> set:
    """Return all current PyMOL names through the public ``cmd`` API."""

    try:
        values = command_api.get_names("all")
    except TypeError:
        values = command_api.get_names()
    return {str(value) for value in (values or ())}


def _execution_marker(command_api) -> str:
    """Allocate a short scene name which cannot collide with an existing name."""

    existing = _name_set(command_api)
    while True:
        marker = _EXECUTION_MARKER_PREFIX + uuid.uuid4().hex[:12]
        if marker not in existing:
            return marker


def _validate_command_line(command: str) -> None:
    """Reject inputs which ``cmd.do`` would dispatch as independent lines."""

    if "\n" in command or "\r" in command:
        raise ValueError(
            "pp_foreach command templates must render one physical PyMOL command line"
        )
    if "\x00" in command:
        raise ValueError("pp_foreach command templates must not contain NUL bytes")


def _execute_checked(command_api, command: str) -> None:
    """Execute one parser line and verify that parsing reached its end.

    PyMOL 3.1's public ``cmd.do`` catches parser/CmdException failures and
    returns ``None`` for both success and failure.  Its private ``_cmd.do`` is
    not a stable status API either.  Append a quiet, empty named-selection
    marker to the same parser line instead: PyMOL only reaches that command if
    every preceding command completed.  The marker is deleted immediately and
    is never exposed as a target or result name.
    """

    _validate_command_line(command)
    marker = _execution_marker(command_api)
    wrapped = "%s; select %s, none, enable=0, quiet=1" % (
        command.rstrip(),
        marker,
    )
    dispatched_error = None
    try:
        command_api.do(wrapped, echo=0)
    except Exception as exc:
        dispatched_error = exc

    marker_present = marker in _name_set(command_api)
    cleanup_error = None
    if marker_present:
        try:
            command_api.delete(marker)
        except Exception as exc:
            cleanup_error = exc

    if dispatched_error is not None:
        raise ForeachCommandError(
            "PyMOL command dispatch raised %s: %s"
            % (type(dispatched_error).__name__, dispatched_error)
        ) from dispatched_error
    if not marker_present:
        raise ForeachCommandError(
            "PyMOL parser did not complete the command; inspect the PyMOL "
            "console for the parser/CmdException detail"
        )
    if cleanup_error is not None:
        raise ForeachCommandError(
            "command completed but its execution marker could not be removed: %s"
            % cleanup_error
        ) from cleanup_error


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

    try:
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
        if not as_bool(quiet):
            print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_foreach(
    targets,
    command,
    match="auto",
    kinds="object,group",
    group_mode="atomic",
    fuzzy_cutoff=0.72,
    limit=0,
    case_sensitive=0,
    commit=0,
    on_error="stop",
    quiet=0,
) -> Dict[str, object]:
    """Render or execute one PyMOL command per resolved object/group target.

    Supported placeholders are ``{target}``, ``{name}``, ``{source}``,
    ``{kind}``, ``{object_type}``, and one-based ``{index}``.  Preview is the
    default because arbitrary PyMOL commands cannot be rolled back generically.
    """

    try:
        template = str(command)
        if not template.strip():
            raise ValueError("command template must not be empty")
        policy = str(on_error).strip().lower()
        if policy not in {"stop", "continue"}:
            raise ValueError("on_error must be stop or continue")
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

        committed = as_bool(commit)
        errors = []
        executed = []
        if committed:
            for item in rendered:
                try:
                    _execute_checked(_require_cmd(), item["command"])
                    executed.append(item["target"])
                except Exception as exc:
                    errors.append(
                        {
                            "target": item["target"],
                            "command": item["command"],
                            "error": str(exc),
                        }
                    )
                    if policy == "stop":
                        break
        status = "preview"
        if committed:
            status = "applied" if not errors else "partial_error"
        result = {
            "schema": "pymolplugins.foreach/1.0",
            "status": status,
            "committed": committed,
            "match": str(match),
            "group_mode": str(group_mode),
            "matches": [value.to_dict() for value in matches],
            "commands": rendered,
            "completion_check": "same-line parser sentinel",
            "executed_targets": executed,
            "errors": errors,
        }
        if not as_bool(quiet):
            print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def register_commands(command_api=None) -> None:
    selected = command_api if command_api is not None else cmd
    if selected is None:
        return
    selected.extend("pp_target_resolve", pp_target_resolve)
    selected.extend("pp_foreach", pp_foreach)


register_commands()
