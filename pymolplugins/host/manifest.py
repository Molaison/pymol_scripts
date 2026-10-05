"""Pure-data module and command inventory for the Host plugin.

``CANONICAL_COMMANDS`` contains eight task facades plus the 64-command
advanced inventory.  The toolkit also autoloads a small compatibility surface
to ``pp_help`` and ``pp_doctor``.  Both surfaces are declared here so runtime
verification can compare callback identity instead of checking only that a name
exists.
"""

CANONICAL_MODULES = (
    "pymolplugins.targets.commands",
    "pymolplugins.navigation.commands",
    "pymolplugins.follow.commands",
    "pymolplugins.grouping.commands",
    "pymolplugins.alignment.sequence",
    "pymolplugins.alignment.rigid",
    "pymolplugins.geometry.commands",
    "pymolplugins.styles.registry",
    "pymolplugins.interactions.controller",
    "pymolplugins.remote.commands",
    "pymolplugins.tasks.commands",
)

CANONICAL_COMMANDS = {
    "toolkit": ("pp_load", "pp_doctor", "pp_capabilities", "pp_help"),
    "tasks": (
        "pp_show", "pp_nav", "pp_align", "pp_orient",
        "pp_contacts", "pp_metrics", "pp_jobs", "pp_undo",
    ),
    "targets": ("pp_target_resolve", "pp_foreach", "pp_batch"),
    "navigation": (
        "pp_nav_start", "pp_nav_next", "pp_nav_prev", "pp_nav_next_batch",
        "pp_nav_prev_batch", "pp_nav_goto", "pp_nav_pin", "pp_nav_unpin",
        "pp_nav_clear_pins", "pp_nav_register_hook", "pp_nav_unregister_hook",
        "pp_nav_clear_hooks", "pp_nav_context", "pp_nav_sync_from_scene",
        "pp_nav_status", "pp_nav_stop",
    ),
    "follow": (
        "pp_follow_arm", "pp_follow_apply", "pp_follow_rebase",
        "pp_follow_undo", "pp_follow_status", "pp_follow_stop",
    ),
    "sequence_alignment": (
        "pp_select_sequence", "pp_align_anchor", "pp_align_motif",
        "pp_align_ligand", "pp_align_commit", "pp_align_undo",
        "pp_align_export", "pp_align_list",
    ),
    "geometry": (
        "pp_axis_pca", "pp_axis_vector", "pp_axis_two_points",
        "pp_axis_cyclic", "pp_orient_pca", "pp_orient_cyclic",
        "pp_orientation_undo", "pp_membrane_slab", "pp_membrane_from_anvil",
        "pp_geometry_export",
    ),
    "styles": (
        "pp_style_list", "pp_style_describe", "pp_style_apply",
        "pp_style_revert", "pp_style_render",
    ),
    "interactions": (
        "pp_interact_list", "pp_interact_describe", "pp_interact_run",
        "pp_interact_clear", "pp_interact_export",
        "pp_interact_backend_status",
    ),
    "worker": (
        "pp_worker_health", "pp_worker_capabilities", "pp_rosetta_metrics",
        "pp_rosetta_metrics_async", "pp_worker_cancel", "pp_worker_jobs",
    ),
}


# These names are registered as a consequence of importing canonical modules.
# They remain callable for compatibility, but are not promoted into the
# task-plus-advanced canonical API and should not be used for new command design.
AUTOLOADED_COMPAT_COMMANDS = {
    "grouping_compat": (
        "group_objects_by_name",
        "merge_models_by_name",
        "align_in_groups",
        "merge_groups",
    ),
    "sequence_compat": (
        "sbs_help",
        "sbs",
        "select_sequence",
        "seq_select",
    ),
}


_CANONICAL_CATEGORY_MODULES = {
    "toolkit": "pymolplugins.host.plugin",
    "tasks": "pymolplugins.tasks.commands",
    "targets": "pymolplugins.targets.commands",
    "navigation": "pymolplugins.navigation.commands",
    "follow": "pymolplugins.follow.commands",
    "geometry": "pymolplugins.geometry.commands",
    "styles": "pymolplugins.styles.registry",
    "interactions": "pymolplugins.interactions.controller",
    "worker": "pymolplugins.remote.commands",
}


def _canonical_targets():
    result = {}
    for category, names in CANONICAL_COMMANDS.items():
        if category == "sequence_alignment":
            for name in names:
                module = (
                    "pymolplugins.alignment.sequence"
                    if name == "pp_select_sequence"
                    else "pymolplugins.alignment.rigid"
                )
                result[name] = (module, name)
            continue
        module = _CANONICAL_CATEGORY_MODULES[category]
        for name in names:
            result[name] = (module, name)
    return result


CANONICAL_COMMAND_TARGETS = _canonical_targets()

COMPAT_COMMAND_TARGETS = {
    "group_objects_by_name": (
        "pymolplugins.grouping.commands",
        "group_objects_by_name",
    ),
    "merge_models_by_name": (
        "pymolplugins.grouping.commands",
        "merge_models_by_name",
    ),
    "align_in_groups": ("pymolplugins.grouping.commands", "align_in_groups"),
    "merge_groups": ("pymolplugins.grouping.commands", "merge_groups"),
    "sbs_help": ("pymolplugins.alignment.sequence", "show_help"),
    "sbs": ("pymolplugins.alignment.sequence", "select_by_sequence"),
    "select_sequence": (
        "pymolplugins.alignment.sequence",
        "select_by_sequence",
    ),
    "seq_select": ("pymolplugins.alignment.sequence", "select_by_sequence"),
}

# Complete declared live surface, used for runtime ownership verification.
COMMAND_TARGETS = dict(CANONICAL_COMMAND_TARGETS)
COMMAND_TARGETS.update(COMPAT_COMMAND_TARGETS)

LEGACY_GLOBAL_ALIASES = (
    "loadpath", "step", "back", "pin", "unpin", "preset", "minimal",
)


def command_count() -> int:
    """Return the exact number of canonical PyMOL commands."""
    return sum(len(commands) for commands in CANONICAL_COMMANDS.values())


def compat_command_count() -> int:
    """Return the number of declared commands autoloaded for compatibility."""

    return sum(len(commands) for commands in AUTOLOADED_COMPAT_COMMANDS.values())


def declared_command_names(include_compat: bool = True):
    """Return the declared live command names in deterministic load order."""

    names = [
        name for commands in CANONICAL_COMMANDS.values() for name in commands
    ]
    if include_compat:
        names.extend(
            name
            for commands in AUTOLOADED_COMPAT_COMMANDS.values()
            for name in commands
        )
    return tuple(names)


def command_category(name: str) -> str:
    """Return the declared category for *name*, or an empty string."""

    for category, commands in CANONICAL_COMMANDS.items():
        if name in commands:
            return category
    for category, commands in AUTOLOADED_COMPAT_COMMANDS.items():
        if name in commands:
            return category
    return ""
