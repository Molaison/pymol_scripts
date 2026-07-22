"""Declarative module and command inventory for the Host plugin."""

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
)

CANONICAL_COMMANDS = {
    "toolkit": ("pp_load", "pp_doctor", "pp_capabilities", "pp_help"),
    "targets": ("pp_target_resolve", "pp_foreach"),
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

LEGACY_GLOBAL_ALIASES = (
    "loadpath", "step", "back", "pin", "unpin", "preset", "minimal",
)


def command_count() -> int:
    """Return the exact number of canonical PyMOL commands."""
    return sum(len(commands) for commands in CANONICAL_COMMANDS.values())
