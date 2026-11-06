"""Argument completion shared by toolkit and annotator console commands.

PyMOL supplies the comma position and the current token, not the full parsed
call. Resolve ``name=value`` tokens here without replacing its console parser.
"""

import glob
import inspect
import os
import sys


BOOL_PARAMETERS = frozenset((
    "repair", "check_worker", "preview", "all_matches", "wrap", "keys",
    "replace", "force", "wait", "case_sensitive", "quiet", "commit", "save_b",
    "bind_keys", "keep_loaded", "allow_external_changes", "visualize", "draw",
    "snapshot", "render", "surface", "labels", "async_mode", "align",
    "delete_sources", "allow_gaps", "enabled", "keep_previous", "preload_enabled",
    "restore_saved", "raise_errors", "include_entries", "require_exists", "descending",
))
SELECTION_PARAMETERS = frozenset((
    "target", "targets", "selection", "objects", "mobile", "reference", "move",
    "focus", "partner", "partner1", "partner2", "ligand", "receptor", "binder",
    "driver", "followers", "mobile_anchor", "target_anchor", "mobile_obj",
    "target_obj", "mobile_ligand", "target_ligand", "secondary_selection",
    "protein_obj_sele", "model_selection", "entries", "subunits",
))
PATH_PARAMETERS = frozenset((
    "path", "filename", "directory", "output", "output_json", "input_or_selection",
    "executable", "source_base", "review_path", "csv_path", "state_path",
    "out_path", "stage_root", "target_prefix",
    "a", "b",
))
ENUMS = {
    "on_error": ("stop", "continue"),
    "group_mode": ("atomic", "molecules", "leaves", "both"),
    "zoom_policy": ("current", "current_and_pins", "none"),
    "weighting": ("equal", "mass"),
    "target_axis": ("x", "y", "z"), "to": ("x", "y", "z"),
    "secondary_target": ("auto", "x", "y", "z"),
    "gap_mode": ("exact", "bounded"),
    "method": ("split_firstn_sep", "split_lastn_sep"),
    "methods": ("split_firstn_sep", "split_lastn_sep"),
    "cgo_policy": ("static", "skip_callback", "rebuild"),
}
COMMAND_ENUMS = {
    ("pp_group", "action"): ("info", "enable", "disable"),
    ("show_structure_annotator_panel", "mode"): ("docked", "floating"),
    ("pp_show", "mode"): ("show", "hide"),
    ("pp_nav", "action"): ("start", "next", "prev", "next-page", "prev-page",
                            "goto", "pin", "unpin", "status", "stop",
                            "files", "groups", "objects"),
    ("pp_nav", "source"): ("auto", "files", "groups", "objects"),
    ("pp_nav_start", "source"): ("files", "groups", "objects"),
    ("pp_jobs", "action"): ("list", "status", "history", "cancel"),
    ("pp_batch", "source"): ("files", "csv"),
    ("pp_batch", "mode"): ("reference", "paired"),
    ("pp_batch", "match"): ("template", "glob", "regex"),
    ("pp_align_anchor", "match"): ("identifier", "name", "order"),
    ("pp_align_ligand", "match"): ("identifier", "name", "order"),
    ("pp_orient", "axis"): ("pca", "cyclic", "x", "y", "z"),
    ("pp_orient", "center"): ("origin", "keep"),
    ("pp_axis_pca", "axis"): ("long", "middle", "short"),
    ("pp_orient_pca", "axis"): ("long", "middle", "short"),
    ("pp_membrane_slab", "axis_source"): ("pca", "cyclic", "vector", "two_points", "anvil"),
}


def _module_values(module_name, attribute):
    module = sys.modules.get(module_name)
    return list(getattr(module, attribute, {})) if module else []


def _paths(token):
    expanded = os.path.expandvars(os.path.expanduser(token or ""))
    paths = sorted(glob.glob(expanded + "*"))
    return [path.replace("\\", "/") + ("/" if os.path.isdir(path) else "")
            for path in paths]


def _dynamic_values(api, command, parameter):
    name = parameter.name
    annotation = sys.modules.get("pymolplugins.annotation.plugin")
    if command == "sa_annotate" and name == "value" and annotation:
        return annotation._split_values(annotation.STATE.values_text)
    if command == "sa_history_search" and name == "field" and annotation:
        return list(annotation.HISTORY_FIELDS)
    if name in {"path_column", "id_column", "label_column", "sort_column"} and annotation:
        dataset = annotation.STATE.dataset
        return list(dataset.columns) if dataset is not None else []
    if name == "profile" and command == "pp_metrics":
        return _module_values("pymolplugins.tasks.commands", "_METRIC_PROFILES")
    if name == "preset":
        if command.startswith("pp_style"):
            from pymolplugins.styles.presets import PRESETS
            return [value for item in PRESETS for value in (item.name,) + item.aliases]
        from pymolplugins.interactions.presets import PRESETS
        return list(PRESETS.names()) + (["auto"] if command == "pp_contacts" else [])
    if name == "category":
        if command == "pp_style_list":
            return ["lighting", "palette", "scene", "render"]
        from pymolplugins.host.help_catalog import help_topics
        return list(help_topics())
    if name in ("group", "parent"):
        return list(api.get_names("group_objects"))
    if name in ("chain", "mobile_chain", "target_chain", "target_chains", "binder_chains"):
        return list(api.get_chains("all"))
    if name == "color":
        return [item[0] for item in api.get_color_indices()]
    if name == "representation":
        from pymolplugins.styles.presets import VALID_REPRESENTATIONS
        return sorted(VALID_REPRESENTATIONS - {"everything"})
    if name == "record_id":
        return _module_values("pymolplugins.alignment.rigid", "_RECORDS")
    if name == "job_id":
        return _module_values("pymolplugins.remote.commands", "_ASYNC_JOBS")
    if name == "operation_id":
        if command == "pp_orientation_undo":
            return _module_values("pymolplugins.geometry.commands", "_ORIENTATION_RECORDS")
        from pymolplugins.tasks.journal import list_operations
        return ["list"] + [item["operation_id"] for item in list_operations()]
    if name == "snapshot_id":
        module = sys.modules.get("pymolplugins.styles.registry")
        return [str(item.snapshot_id) for item in getattr(getattr(module, "_REGISTRY", None), "snapshots", ())]
    if name in SELECTION_PARAMETERS or name == "source":
        return list(api.get_names("public")) + ["all", "polymer", "organic", "solvent", "enabled"]
    return None


def _values(api, command, parameter):
    key = (command, parameter.name)
    if key in COMMAND_ENUMS:
        return COMMAND_ENUMS[key]
    if parameter.name in BOOL_PARAMETERS or isinstance(parameter.default, bool):
        return ("0", "1")
    if parameter.name in ENUMS:
        return ENUMS[parameter.name]
    if parameter.name == "match":
        return ("auto", "exact", "glob", "contains", "regex", "fuzzy")
    if parameter.name == "occurrence":
        return ("unique", "first") if command == "pp_align_motif" else ("all", "first", "unique")
    values = _dynamic_values(api, command, parameter)
    if values is not None:
        return values
    default = parameter.default
    return [str(default)] if default not in (inspect.Parameter.empty, None, "") else []


class ArgumentCompletion:
    """A native Shortcut-compatible object, refreshed for each Tab press."""

    def __init__(self, api, command, parameters, position, fallback=None):
        self.api, self.command = api, command
        self.parameters, self.position = parameters, position
        self.fallback = fallback

    def __call__(self):
        return self

    def interpret(self, token, mode=0):
        prefix, separator, value = token.partition("=")
        parameter = self.parameters[self.position]
        if separator:
            parameter = next((item for item in self.parameters if item.name == prefix), None)
            if parameter is None:
                return None
        else:
            value = token
        quote = value[:1] if value[:1] in ("'", '"') else ""
        value = value[1:] if quote else value
        if parameter.name in PATH_PARAMETERS:
            candidates = _paths(value)
            if not separator:
                candidates += [item.name + "=" for item in self.parameters
                               if (item.name + "=").startswith(value)]
            # Expanded paths no longer share a typed ~/ or Windows separator prefix.
            result = candidates[0] if len(candidates) == 1 else candidates or None
        else:
            candidates = list(_values(self.api, self.command, parameter))
            if not separator:
                candidates += [item.name + "=" for item in self.parameters]
            result = self.api.Shortcut(sorted(set(candidates))).interpret(value, mode)
        if result is None and not separator and self.fallback:
            result = self.fallback[0]().interpret(token, mode)
        if result is None:
            return None
        lead = (prefix + "=" if separator else "") + quote
        return [lead + item for item in result] if isinstance(result, list) else lead + result


def register_completion(api, name, function):
    """Install signature-aware completion without requiring a running scene."""
    if not hasattr(api, "auto_arg") or not hasattr(api, "Shortcut"):
        return 0
    function = getattr(function, "_pymolplugins_literal_target", function)
    parameters = [item for item in inspect.signature(function).parameters.values()
                  if item.kind not in (item.VAR_POSITIONAL, item.VAR_KEYWORD)
                  and not item.name.startswith("_")]
    for position, parameter in enumerate(parameters):
        if isinstance(api.auto_arg, dict):
            api.auto_arg.setdefault(position, {})
        else:
            while len(api.auto_arg) <= position:
                api.auto_arg.append({})
        old = api.auto_arg[position].get(name)
        if old and isinstance(old[0], ArgumentCompletion):
            old = old[0].fallback
        api.auto_arg[position][name] = [
            ArgumentCompletion(api, name, parameters, position, old), parameter.name, "",
        ]
    return len(parameters)


def install_completions(api, callbacks):
    """Refresh after category imports which have legacy local completions."""
    return {name: register_completion(api, name, function)
            for name, function in callbacks.items()}
