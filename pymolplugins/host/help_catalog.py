"""PyMOL-independent task, command, and preset help catalog.

The catalog intentionally contains plain data and deterministic string matching.
It can therefore be tested in WSL without importing PyMOL or any stateful Host
module.  Runtime code may add availability information, but should not rewrite
the usage contract defined here.
"""

from copy import deepcopy
from difflib import SequenceMatcher
import re
import unicodedata

from pymolplugins.host.manifest import (
    AUTOLOADED_COMPAT_COMMANDS,
    CANONICAL_COMMANDS,
    command_category,
)


CATALOG_SCHEMA = "pymolplugins.command-help-catalog/1.0"
_MIN_SEARCH_SCORE = 50.0


def _help_entry(
    kind,
    name,
    purpose,
    minimal_usage,
    example,
    mutates,
    undo,
    next_commands=(),
    keywords=(),
    category="",
    aliases=(),
):
    if str(category).endswith("_compat"):
        tier = "compat"
    elif kind == "command":
        tier = "canonical"
    else:
        tier = str(kind)
    return {
        "kind": str(kind),
        "tier": tier,
        "name": str(name),
        "purpose": str(purpose),
        "minimal_usage": str(minimal_usage),
        "example": str(example),
        "mutates": bool(mutates),
        "undo": None if undo is None else str(undo),
        "next": list(next_commands),
        "keywords": list(keywords),
        "category": str(category),
        "aliases": list(aliases),
    }


_TASKS = (
    _help_entry(
        "task",
        "diagnose",
        "Check the Host PyMOL API, command ownership, paths, and optional worker.",
        "pp_doctor check_worker=0",
        "pp_doctor check_worker=0",
        False,
        None,
        ("pp_help", "pp_capabilities check_worker=0"),
        ("doctor", "install", "load", "health", "commands"),
        "toolkit",
        ("check install", "installation check"),
    ),
    _help_entry(
        "task",
        "surface",
        "Apply one representation in every state; default all means molecule objects/group leaves only, with undo identity when supported.",
        "pp_show REPRESENTATION [, TARGET]",
        "pp_show surface, walk_root_a",
        True,
        "pp_undo [OPERATION_ID]",
        ("pp_show surface, walk_root_a, preview=1", "pp_undo", "pp_batch"),
        ("show", "hide", "representation", "group", "object", "foreach"),
        "targets",
        ("表面", "显示表面", "show representation"),
    ),
    _help_entry(
        "task",
        "navigate",
        "Navigate files, molecule objects, or whole root groups as entries.",
        "pp_nav TARGET",
        'pp_nav groups, "walk_root_*"',
        True,
        "pp_nav stop",
        ("pp_nav next", "pp_nav prev", "pp_nav pin", "pp_nav stop"),
        ("navigation", "nav", "next", "previous", "batch", "groups"),
        "navigation",
        (
            "导航",
            "浏览",
            "批量浏览",
            "group batch",
            "batch groups",
            "group batches",
            "browse files",
            "browse objects",
            "browse groups",
            "navigate files",
            "navigate objects",
            "navigate groups",
        ),
    ),
    _help_entry(
        "task",
        "follow",
        "Propagate a manually moved driver's rigid transform to its group peers.",
        "pp_follow_arm DRIVER [, group=GROUP]",
        "pp_follow_arm driver, group=my_group",
        True,
        "pp_follow_undo",
        ("Move the driver with PyMOL object-movement mode", "pp_follow_apply", "pp_follow_stop"),
        ("move group", "driver", "transform", "cgo"),
        "follow",
    ),
    _help_entry(
        "task",
        "sequence motif",
        "Select exact or bounded sequence motifs without residue-number coercion.",
        "pp_select_sequence OBJECTS, MOTIF",
        "pp_select_sequence model, GGH",
        True,
        "Delete or replace the generated selection.",
        ("pp_align_motif mobile, target, GGH",),
        ("sequence", "motif", "residues", "select"),
        "sequence_alignment",
        (
            "序列基序",
            "序列选择",
            "sequence selection",
            "select sequence",
            "motif selection",
            "select motif",
        ),
    ),
    _help_entry(
        "task",
        "align",
        "Align an explicit payload from selection, motif, or ligand anchors.",
        "pp_align MOBILE, REFERENCE",
        "pp_align mobile, target, motif=GGH",
        True,
        "pp_undo",
        (
            "pp_align mobile, target, motif=GGH, preview=1",
            "pp_align apply, ALIGNMENT_ID",
            "pp_undo",
        ),
        ("alignment", "ligand", "anchor", "rigid", "superpose"),
        "sequence_alignment",
        (
            "对齐",
            "结构对齐",
            "配体对齐",
            "基序对齐",
            "structure align",
            "structure alignment",
            "align ligand",
            "ligand align",
            "ligand alignment",
            "align motif",
            "motif align",
            "motif alignment",
        ),
    ),
    _help_entry(
        "task",
        "orient",
        "Orient one explicit structure/group to X/Y/Z from a PCA or cyclic axis.",
        "pp_orient TARGET",
        "pp_orient oligomer, axis=cyclic",
        True,
        "pp_undo",
        ("pp_orient model, preview=1", "pp_orient model", "pp_undo"),
        ("orientation", "pca", "cyclic", "axis", "rotate"),
        "geometry",
        (
            "定向",
            "取向",
            "轴对齐",
            "axis align",
            "align axis",
            "pca axis",
            "cyclic axis",
            "pca alignment",
            "cyclic alignment",
        ),
    ),
    _help_entry(
        "task",
        "membrane",
        "Draw two membrane boundary planes from PCA, cyclic, vector, or ANVIL axes.",
        "pp_membrane_slab selection=SELECTION [, axis_source=pca|cyclic|vector]",
        "pp_membrane_slab selection=model, axis_source=pca",
        True,
        "Delete the generated pp_membrane group after reviewing ownership.",
        ("pp_geometry_export pp_membrane",),
        ("slab", "plane", "bilayer", "anvil", "axis"),
        "geometry",
        ("膜平面", "膜板", "membrane plane"),
    ),
    _help_entry(
        "task",
        "style",
        "Apply a declarative scene, lighting, or palette preset with a snapshot.",
        "pp_style_apply PRESET [, selection=SELECTION]",
        "pp_style_apply publication_light, selection=all",
        True,
        "pp_style_revert",
        ("pp_style_list", "pp_style_revert", "pp_style_render"),
        ("publication", "palette", "lighting", "render", "color"),
        "styles",
        (
            "样式",
            "配色",
            "渲染风格",
            "出图",
            "渲染图片",
            "发表图片",
            "render figure",
            "render image",
            "publication figure",
            "figure style",
            "colour palette",
        ),
    ),
    _help_entry(
        "task",
        "interactions",
        "Render a manifest-owned ligand, chain, polar, or PLIP interaction result.",
        "pp_contacts SOURCE",
        "pp_contacts complex, focus=organic",
        True,
        "pp_undo [OPERATION_ID] after any local or async result completes",
        (
            "pp_contacts complex, preview=1",
            "pp_undo",
            "pp_jobs",
            "pp_interact_clear OWNER_REFERENCE",
        ),
        ("contacts", "ligand", "hydrogen bonds", "plip", "polar"),
        "interactions",
        (
            "接触",
            "配体接触",
            "相互作用",
            "ligand contact",
            "ligand contacts",
            "contact ligand",
            "contacts ligand",
            "protein ligand contacts",
            "ppi",
            "protein protein interaction",
            "protein protein interactions",
            "interface contacts",
            "interface interaction",
            "interface interactions",
            "small molecule contacts",
            "hbond",
            "h bond",
            "hydrogen bond",
            "hydrogen bonds",
            "salt bridge",
            "salt bridges",
            "pi stack",
            "pi stacking",
            "ligand environment",
            "蛋白相互作用",
            "蛋白蛋白相互作用",
            "界面接触",
            "界面相互作用",
            "氢键",
            "盐桥",
            "配体环境",
        ),
    ),
    _help_entry(
        "task",
        "worker metrics",
        "Submit Rosetta metrics asynchronously; inspect completion or failure with pp_jobs.",
        "pp_metrics SOURCE, target=CHAINS, binder=CHAINS",
        "pp_metrics complex, target=A, binder=B",
        True,
        None,
        (
            "pp_metrics complex, target=A, binder=B, preview=1",
            "pp_jobs",
            "pp_jobs cancel, JOB_ID",
            "pp_worker_health",
        ),
        ("rosetta", "cms", "bunsat", "async", "wsl"),
        "worker",
        (
            "指标",
            "打分",
            "rosetta 打分",
            "rosetta metrics",
            "contact molecular surface",
            "buried unsatisfied",
            "buried unsatisfied residue",
            "buried unsatisfied residues",
            "接触分子表面",
            "埋藏未满足残基",
            "埋藏不饱和残基",
        ),
    ),
    _help_entry(
        "task",
        "annotation",
        "Open the Structure Annotator GUI for persistent directory or CSV review.",
        "Plugin > PymolPlugins: Structure Annotator",
        "Plugin > PymolPlugins: Structure Annotator",
        True,
        "Use the annotator's row-level Undo action.",
        ("Load a directory or CSV in the Source tab",),
        ("csv", "scores", "review", "label", "history"),
        "annotation",
        (
            "标注",
            "结构标注",
            "评分表",
            "structure annotation",
            "annotate structure",
            "score csv",
        ),
    ),
)


_CATEGORY_PURPOSE = {
    "toolkit": "Load, diagnose, and discover the toolkit.",
    "tasks": "Recommended workflows with explicit preview, undo, stop, or job guidance.",
    "targets": "Resolve and apply commands once per object or group.",
    "navigation": "Navigate files, objects, or root groups.",
    "follow": "Propagate one manually moved object's transform.",
    "sequence_alignment": "Select sequence motifs and perform rigid alignment.",
    "geometry": "Compute axes, orient structures, and draw membrane planes.",
    "styles": "Apply, revert, and render declarative styles.",
    "interactions": "Compute and render local or PLIP interaction presets.",
    "worker": "Probe the WSL worker and submit Rosetta calculations.",
    "grouping_compat": "Legacy name-based grouping helpers retained for compatibility.",
    "sequence_compat": "Legacy aliases for the canonical sequence selector.",
}


# Curated command rows use:
# purpose, minimal usage, example, mutates, undo, next, keywords.
_COMMAND_ROWS = {
    "pp_show": (
        "Show/hide in all states; default all means molecule objects/group leaves only, with same-session undo identity when supported.",
        "pp_show REPRESENTATION [, TARGET]",
        "pp_show surface, walk_root_a",
        True,
        "pp_undo",
        ("pp_show surface, walk_root_a, preview=1",),
        ("surface", "display", "group", "表面"),
    ),
    "pp_nav": (
        "Start or control navigation; control actions reject start-only options.",
        "pp_nav [ACTION|TARGET]",
        'pp_nav groups, "walk_root_*"',
        True,
        "pp_nav stop",
        ("pp_nav next", "pp_nav status", "pp_nav stop"),
        ("browse", "group batch", "导航"),
    ),
    "pp_align": (
        "Align objects, anchors, one motif, or one ligand and journal the mutation.",
        "pp_align MOBILE, REFERENCE",
        "pp_align mobile, reference, motif=GGH",
        True,
        "pp_undo",
        (
            "pp_align mobile, reference, preview=1",
            "pp_align apply, ALIGNMENT_ID",
        ),
        ("superpose", "motif", "ligand", "对齐"),
    ),
    "pp_orient": (
        "Orient an explicit target from PCA, cyclic symmetry, or an explicit vector.",
        "pp_orient TARGET",
        "pp_orient oligomer, axis=cyclic",
        True,
        "pp_undo",
        ("pp_orient oligomer, preview=1", "pp_orient oligomer"),
        ("pca", "symmetry axis", "定向"),
    ),
    "pp_contacts": (
        "Render a safely inferred local contact result or one explicit preset.",
        "pp_contacts SOURCE",
        "pp_contacts complex, focus=organic",
        True,
        "pp_undo [OPERATION_ID] after any local or async result completes",
        (
            "pp_contacts complex, preview=1",
            "pp_jobs",
            "pp_undo",
        ),
        ("ligand contacts", "interactions", "配体接触"),
    ),
    "pp_metrics": (
        "Submit Rosetta metrics asynchronously by default; failures remain inspectable in pp_jobs.",
        "pp_metrics SOURCE, target=CHAINS, binder=CHAINS",
        "pp_metrics complex, target=A, binder=B",
        True,
        None,
        (
            "pp_metrics complex, target=A, binder=B, preview=1",
            "pp_jobs",
            "pp_jobs cancel, JOB_ID",
        ),
        ("rosetta", "cms", "bunsat", "指标"),
    ),
    "pp_jobs": (
        "List active/recent jobs, including backend failures; cancel suppresses Host result handling only.",
        "pp_jobs [JOB_ID] | pp_jobs cancel, JOB_ID",
        "pp_jobs",
        True,
        None,
        ("pp_jobs JOB_ID", "pp_jobs cancel, JOB_ID"),
        ("worker status", "history", "任务"),
    ),
    "pp_undo": (
        "Undo through the exact stored canonical reference for the latest or named operation.",
        "pp_undo [OPERATION_ID|list]",
        "pp_undo",
        True,
        None,
        ("pp_undo list",),
        ("revert", "restore", "撤销"),
    ),
    "pp_load": (
        "Import modules, safely restore missing callbacks, and verify command ownership.",
        "pp_load [repair=0|1]",
        "pp_load",
        True,
        None,
        ("pp_doctor check_worker=0", "pp_help"),
        ("bootstrap", "repair", "reload"),
    ),
    "pp_doctor": (
        "Report Host APIs, paths, dependencies, worker state, and command conflicts.",
        "pp_doctor [check_worker=0|1]",
        "pp_doctor check_worker=0",
        False,
        None,
        ("pp_load repair=1", "pp_worker_health"),
        ("diagnose", "conflicts", "environment"),
    ),
    "pp_capabilities": (
        "Report available command categories and optional backends.",
        "pp_capabilities [check_worker=0|1]",
        "pp_capabilities check_worker=0",
        False,
        None,
        ("pp_help",),
        ("features", "backends"),
    ),
    "pp_help": (
        "Find task-first help, exact commands/categories, presets, and fuzzy matches.",
        "pp_help [TOPIC]",
        "pp_help navigation",
        False,
        None,
        ("pp_help pp_nav_start", "pp_help surface", "pp_help publication"),
        ("usage", "examples", "search"),
    ),
    "pp_target_resolve": (
        "Preview exact, glob, contains, regex, or fuzzy object/group matching.",
        "pp_target_resolve TARGETS [, match=auto]",
        'pp_target_resolve "design_*", match=glob, kinds=object',
        False,
        None,
        ("pp_foreach",),
        (
            "fuzzy",
            "fuzzy matching",
            "glob",
            "objects",
            "groups",
            "object matching",
            "group matching",
            "模糊匹配",
            "对象匹配",
            "组匹配",
        ),
    ),
    "pp_foreach": (
        "Execute one command per target by default; groups expand to molecule leaves and commit=0 previews.",
        'pp_foreach TARGETS, "COMMAND {target}" [, commit=0]',
        'pp_foreach my_group, "show surface,{target}"',
        True,
        "No generic undo is possible for arbitrary command templates.",
        ('pp_foreach my_group, "COMMAND {target}", commit=0', "pp_target_resolve", "pp_batch"),
        (
            "surface",
            "batch action",
            "iterate",
            "iterate objects",
            "batch objects",
            "group iterate",
            "object",
            "批量对象",
            "对象级迭代",
        ),
    ),
    "pp_batch": (
        "Run ordered commands for file/CSV A-B pairs, saving both by default; template, wildcard and regex ID matching.",
        "pp_batch [A_INPUT, B_INPUT, output=DIR, mode=reference|paired, source=files|csv, preview=1]",
        'pp_batch "D:/models/A_{id}.pdb", "D:/models/B_{id}.pdb", mode=paired, output="D:/processed", preview=1',
        True,
        "Source files are retained; no generic undo for arbitrary recipe commands. GUI Stop acts between steps.",
        ("pp_batch", "pp_help pp_batch"),
        ("batch", "pair", "reference", "csv", "wildcard", "regex", "file processing", "all against ref",
         "batch processing", "paired structures", "批量处理", "配对"),
    ),
    "pp_nav_start": (
        "Start a managed file, object, or root-group navigation session.",
        "pp_nav_start [source=groups|objects|files] [, entries=NAMES] [, path=DIR]",
        'pp_nav_start source=groups, entries="group_a,group_b", window_size=1',
        True,
        "pp_nav_stop restores managed existing-object visibility or removes managed file loads.",
        ("pp_nav_next", "pp_nav_prev", "pp_nav_pin", "pp_nav_stop"),
        ("navigate", "navigation", "groups", "files", "objects", "window"),
    ),
    "pp_nav_next": (
        "Advance the current navigation window.",
        "pp_nav_next",
        "pp_nav_next",
        True,
        "pp_nav_prev",
        ("pp_nav_pin", "pp_nav_status", "pp_nav_stop"),
        ("step", "forward"),
    ),
    "pp_nav_prev": (
        "Move the current navigation window backward.",
        "pp_nav_prev",
        "pp_nav_prev",
        True,
        "pp_nav_next",
        ("pp_nav_status", "pp_nav_stop"),
        ("back", "previous"),
    ),
    "pp_nav_goto": (
        "Jump to a one-based navigation position.",
        "pp_nav_goto POSITION",
        "pp_nav_goto 3",
        True,
        "pp_nav_prev or pp_nav_goto PREVIOUS_POSITION",
        ("pp_nav_status",),
        ("jump", "position"),
    ),
    "pp_nav_pin": (
        "Keep the current or named navigation entry visible while stepping.",
        "pp_nav_pin [ENTRY]",
        "pp_nav_pin",
        True,
        "pp_nav_unpin",
        ("pp_nav_next", "pp_nav_clear_pins"),
        ("keep", "visible"),
    ),
    "pp_nav_stop": (
        "Stop navigation and release its managed scene/key state.",
        "pp_nav_stop [keep_loaded=0|1]",
        "pp_nav_stop",
        True,
        None,
        ("pp_nav_start",),
        ("finish", "restore"),
    ),
    "pp_follow_arm": (
        "Capture a driver and its follower baselines before manual movement.",
        "pp_follow_arm DRIVER [, group=GROUP|followers=NAMES]",
        "pp_follow_arm driver, group=my_group",
        True,
        "pp_follow_stop forgets the baseline; pp_follow_undo restores it after movement.",
        ("Move the driver in object-movement mode", "pp_follow_apply"),
        ("driver", "move group", "cgo"),
    ),
    "pp_follow_apply": (
        "Apply the driver's incremental transform to armed followers.",
        "pp_follow_apply",
        "pp_follow_apply",
        True,
        "pp_follow_undo",
        ("pp_follow_rebase", "pp_follow_stop"),
        ("transform", "propagate"),
    ),
    "pp_follow_undo": (
        "Restore the armed/rebased driver and follower matrices.",
        "pp_follow_undo [force=0|1]",
        "pp_follow_undo",
        True,
        None,
        ("pp_follow_stop",),
        ("restore",),
    ),
    "pp_select_sequence": (
        "Select exact or bounded sequence motif matches by atom identity.",
        "pp_select_sequence OBJECTS, QUERIES",
        "pp_select_sequence model, GGH",
        True,
        "Delete or replace the output selection.",
        ("pp_align_motif",),
        ("motif", "residue", "sequence"),
    ),
    "pp_align_anchor": (
        "Fit arbitrary mobile/target atom selections and move an explicit payload.",
        "pp_align_anchor MOBILE_SELECTION, TARGET_SELECTION [, move=PAYLOAD]",
        "pp_align_anchor mobile_anchor, target_anchor, move=mobile, commit=1",
        True,
        "pp_align_undo",
        ("pp_align_list", "pp_align_export"),
        ("selection", "rigid fit"),
    ),
    "pp_align_motif": (
        "Fit corresponding sequence-motif residues and move an explicit payload.",
        "pp_align_motif MOBILE, TARGET, MOTIF [, move=PAYLOAD]",
        "pp_align_motif mobile, target, GGH, move=mobile, commit=1",
        True,
        "pp_align_undo",
        ("pp_align_list", "pp_align_export"),
        ("sequence", "residue anchors"),
    ),
    "pp_align_ligand": (
        "Fit explicit ligand atoms and move an explicit object/group payload.",
        "pp_align_ligand MOBILE, TARGET [, mobile_ligand=SELECTION]",
        "pp_align_ligand mobile, target, mobile_ligand=organic, target_ligand=organic, commit=1",
        True,
        "pp_align_undo",
        ("pp_align_list",),
        ("small molecule", "organic"),
    ),
    "pp_align_commit": (
        "Apply the most recent or named alignment preview after freshness checks.",
        "pp_align_commit [RECORD_ID]",
        "pp_align_commit",
        True,
        "pp_align_undo",
        ("pp_align_export",),
        ("apply preview",),
    ),
    "pp_align_undo": (
        "Undo a committed alignment without overwriting newer edits by default.",
        "pp_align_undo [RECORD_ID] [, force=0|1]",
        "pp_align_undo",
        True,
        None,
        ("pp_align_list",),
        ("restore",),
    ),
    "pp_axis_pca": (
        "Compute, store, and optionally draw a PCA axis.",
        "pp_axis_pca [selection=SELECTION]",
        "pp_axis_pca selection=model, axis=long, name=model_axis",
        True,
        "Delete the generated axis CGO.",
        ("pp_orient_pca", "pp_membrane_slab"),
        ("principal component", "long axis"),
    ),
    "pp_axis_cyclic": (
        "Estimate, store, and optionally draw a cyclic oligomer axis.",
        "pp_axis_cyclic [selection=SELECTION]",
        "pp_axis_cyclic selection=oligomer, order=auto, name=symmetry_axis",
        True,
        "Delete the generated axis CGO.",
        ("pp_orient_cyclic", "pp_membrane_slab"),
        ("symmetry", "oligomer", "Cn"),
    ),
    "pp_orient_pca": (
        "Plan or apply a PCA-axis orientation to an explicit payload.",
        "pp_orient_pca selection=SELECTION, move=PAYLOAD [, commit=1]",
        "pp_orient_pca selection=model, move=model, target_axis=z, commit=1",
        True,
        "pp_orientation_undo",
        ("pp_axis_pca",),
        ("rotate", "principal axis"),
    ),
    "pp_orient_cyclic": (
        "Plan or apply a cyclic-axis orientation to an explicit payload.",
        "pp_orient_cyclic selection=SELECTION, move=PAYLOAD [, commit=1]",
        "pp_orient_cyclic selection=oligomer, move=oligomer, target_axis=z, commit=1",
        True,
        "pp_orientation_undo",
        ("pp_axis_cyclic",),
        ("symmetry", "rotate"),
    ),
    "pp_membrane_slab": (
        "Draw two membrane boundary planes around a structure-derived center.",
        "pp_membrane_slab selection=SELECTION [, axis_source=SOURCE]",
        "pp_membrane_slab selection=model, axis_source=pca, thickness=30",
        True,
        "Delete the generated membrane group after reviewing ownership.",
        ("pp_geometry_export pp_membrane",),
        ("surface", "bilayer", "planes"),
    ),
    "pp_style_list": (
        "List canonical declarative style presets.",
        "pp_style_list [CATEGORY]",
        "pp_style_list",
        False,
        None,
        ("pp_style_describe publication_light",),
        ("presets", "palette"),
    ),
    "pp_style_apply": (
        "Apply a style preset and create a revert snapshot by default.",
        "pp_style_apply PRESET [, selection=SELECTION]",
        "pp_style_apply publication_light, selection=all",
        True,
        "pp_style_revert",
        ("pp_style_render",),
        ("publication", "palette", "lighting"),
    ),
    "pp_style_revert": (
        "Restore the most recent LIFO style snapshot.",
        "pp_style_revert [SNAPSHOT_ID]",
        "pp_style_revert",
        True,
        None,
        ("pp_style_apply",),
        ("undo style",),
    ),
    "pp_style_render": (
        "Ray trace the current scene, optionally to a PNG file.",
        "pp_style_render [width=PIXELS, height=PIXELS, filename=PATH]",
        "pp_style_render width=1600, height=1200, filename=figure.png",
        True,
        None,
        (),
        ("ray", "png", "image"),
    ),
    "pp_interact_list": (
        "List local and WSL-backed interaction presets.",
        "pp_interact_list",
        "pp_interact_list",
        False,
        None,
        ("pp_interact_describe ligand_quick",),
        ("contacts", "presets"),
    ),
    "pp_interact_run": (
        "Compute and render one manifest-owned interaction result.",
        "pp_interact_run [PRESET] [, source=SELECTION]",
        "pp_interact_run",
        True,
        "pp_interact_clear GENERATED_GROUP",
        ("pp_interact_export GENERATED_GROUP, interactions.json",),
        ("contacts", "ligand", "plip", "hydrogen bonds"),
    ),
    "pp_interact_clear": (
        "Delete only names recorded in an exact interaction manifest.",
        "pp_interact_clear GENERATED_GROUP",
        "pp_interact_clear ppi_complex_ligand_quick_RUNID",
        True,
        None,
        (),
        ("cleanup",),
    ),
    "pp_worker_health": (
        "Probe the configured WSL worker health endpoint.",
        "pp_worker_health [url=URL]",
        "pp_worker_health",
        False,
        None,
        ("pp_worker_capabilities",),
        ("wsl", "server", "status"),
    ),
    "pp_rosetta_metrics": (
        "Synchronously calculate requested Rosetta metrics through the worker.",
        "pp_rosetta_metrics selection=SELECTION, target_chains=A, binder_chains=B",
        "pp_rosetta_metrics selection=complex, target_chains=A, binder_chains=B",
        False,
        None,
        ("pp_worker_health",),
        ("cms", "bunsat", "pyrosetta"),
    ),
    "pp_rosetta_metrics_async": (
        "Submit Rosetta metrics without blocking the Host Qt event loop.",
        "pp_rosetta_metrics_async selection=SELECTION, target_chains=A, binder_chains=B",
        "pp_rosetta_metrics_async selection=complex, target_chains=A, binder_chains=B",
        True,
        "pp_worker_cancel JOB_ID suppresses pending result handling.",
        ("pp_worker_jobs",),
        ("cms", "bunsat", "job", "wsl"),
    ),
    "pp_worker_jobs": (
        "List currently pending Host-side worker jobs.",
        "pp_worker_jobs",
        "pp_worker_jobs",
        False,
        None,
        ("pp_worker_cancel JOB_ID",),
        ("async", "status"),
    ),
    "group_objects_by_name": (
        "Create real PyMOL groups by splitting molecule object names.",
        "group_objects_by_name [selection=SELECTION]",
        "group_objects_by_name selection=all, method=split_lastn_sep, sep=_",
        True,
        "Ungroup or delete only the generated groups after inspection.",
        ("pp_help navigation",),
        ("legacy", "group", "names"),
    ),
    "merge_models_by_name": (
        "Create merged molecule objects from name partitions; source deletion is opt-in.",
        "merge_models_by_name [selection=SELECTION]",
        "merge_models_by_name selection=all, delete_sources=0",
        True,
        "Delete generated merged objects; sources remain unless delete_sources=1 was used.",
        ("pp_help grouping_compat",),
        ("legacy", "merge", "names"),
    ),
    "align_in_groups": (
        "Legacy helper which aligns molecule members inside selected groups.",
        "align_in_groups GROUPS [, reference=OBJECT]",
        "align_in_groups my_group",
        True,
        "No built-in undo; use pp_align for journaled alignment.",
        ("pp_help align",),
        ("legacy", "align", "group"),
    ),
    "merge_groups": (
        "Deprecated compatibility alias for non-destructive name grouping.",
        "merge_groups [model_selection=SELECTION]",
        "merge_groups model_selection=all",
        True,
        "Ungroup or delete only the generated groups after inspection.",
        ("pp_help group_objects_by_name",),
        ("legacy", "deprecated", "group"),
    ),
    "sbs_help": (
        "Print help for legacy sequence-selection aliases.",
        "sbs_help",
        "sbs_help",
        False,
        None,
        ("pp_help pp_select_sequence",),
        ("legacy", "sequence"),
    ),
    "sbs": (
        "Legacy alias for sequence-motif selection.",
        "sbs OBJECTS, QUERIES",
        "sbs model, GGH",
        True,
        "Delete or replace the generated selection.",
        ("pp_help pp_select_sequence",),
        ("legacy", "sequence", "motif"),
    ),
    "select_sequence": (
        "Legacy alias for sequence-motif selection.",
        "select_sequence OBJECTS, QUERIES",
        "select_sequence model, GGH",
        True,
        "Delete or replace the generated selection.",
        ("pp_help pp_select_sequence",),
        ("legacy", "sequence", "motif"),
    ),
    "seq_select": (
        "Legacy alias for sequence-motif selection.",
        "seq_select OBJECTS, QUERIES",
        "seq_select model, GGH",
        True,
        "Delete or replace the generated selection.",
        ("pp_help pp_select_sequence",),
        ("legacy", "sequence", "motif"),
    ),
}


_READ_ONLY_COMMANDS = {
    "pp_capabilities",
    "pp_doctor",
    "pp_help",
    "pp_target_resolve",
    "pp_nav_context",
    "pp_nav_status",
    "pp_follow_status",
    "pp_align_list",
    "pp_style_list",
    "pp_style_describe",
    "pp_interact_list",
    "pp_interact_describe",
    "pp_interact_backend_status",
    "pp_worker_health",
    "pp_worker_capabilities",
    "pp_worker_jobs",
}


_FALLBACK_USAGE_EXAMPLES = {
    "pp_nav_register_hook": (
        'pp_nav_register_hook "show sticks, {object}"',
        'pp_nav_register_hook "show sticks, {object}"',
    ),
    "pp_nav_unregister_hook": (
        'pp_nav_unregister_hook "show sticks, {object}"',
        'pp_nav_unregister_hook "show sticks, {object}"',
    ),
    "pp_axis_two_points": (
        "pp_axis_two_points POINTS",
        'pp_axis_two_points "0,0,-10,0,0,10"',
    ),
    "pp_membrane_from_anvil": (
        "pp_membrane_from_anvil OUTPUT_PDB [, selection=SELECTION]",
        "pp_membrane_from_anvil anvil_output.pdb, selection=model",
    ),
    "pp_geometry_export": (
        "pp_geometry_export RESULT_NAME [, filename=PATH]",
        "pp_geometry_export model_axis, filename=model_axis.json",
    ),
    "pp_style_describe": (
        "pp_style_describe PRESET",
        "pp_style_describe publication_light",
    ),
    "pp_interact_describe": (
        "pp_interact_describe PRESET",
        "pp_interact_describe ligand_quick",
    ),
    "pp_interact_export": (
        "pp_interact_export GENERATED_GROUP, FILENAME",
        "pp_interact_export ppi_generated_group, interactions.json",
    ),
    "pp_worker_cancel": (
        "pp_worker_cancel JOB_ID",
        "pp_worker_cancel JOB_ID",
    ),
}


_PRESET_ROWS = (
    (
        "publication_light",
        ("publication", "pbs"),
        "Clean shadow-free publication lighting.",
        "pp_style_apply publication_light, selection=all",
        "pp_style_revert",
        ("lighting", "figure", "style"),
    ),
    (
        "minimal_light",
        ("minimal",),
        "Subtle lighting on a white background.",
        "pp_style_apply minimal_light, selection=all",
        "pp_style_revert",
        ("lighting", "style"),
    ),
    (
        "macaron_default",
        ("macaron",),
        "Pastel macaron spectrum with soft lighting.",
        "pp_style_apply macaron_default, selection=polymer",
        "pp_style_revert",
        ("palette", "style", "color"),
    ),
    (
        "macaron_warm",
        ("warm_macaron",),
        "Warm macaron spectrum.",
        "pp_style_apply macaron_warm, selection=polymer",
        "pp_style_revert",
        ("palette", "style", "color"),
    ),
    (
        "macaron_cool",
        ("cool_macaron",),
        "Cool macaron spectrum.",
        "pp_style_apply macaron_cool, selection=polymer",
        "pp_style_revert",
        ("palette", "style", "color"),
    ),
    (
        "ink_wash",
        ("watercolor", "watercolour"),
        "High-key ink-wash lighting.",
        "pp_style_apply ink_wash, selection=all",
        "pp_style_revert",
        ("style", "render"),
    ),
    (
        "high_contrast_outline",
        ("cartoon", "high_contrast"),
        "Flat high-contrast cartoon lighting.",
        "pp_style_apply high_contrast_outline, selection=all",
        "pp_style_revert",
        ("style", "outline"),
    ),
    (
        "rfd2_lighting",
        ("rfd2", "rf_diffusion_lighting"),
        "RFdiffusion2 inspection lighting.",
        "pp_style_apply rfd2_lighting, selection=all",
        "pp_style_revert",
        ("style", "lighting", "rf diffusion"),
    ),
    (
        "rfd2_paper_palette",
        ("paper_rainbow",),
        "RFdiffusion2 paper-inspired seven-color spectrum.",
        "pp_style_apply rfd2_paper_palette, selection=polymer",
        "pp_style_revert",
        ("style", "palette", "color"),
    ),
    (
        "rfd2_sam_palette",
        ("rfd2_rainbow",),
        "RFdiffusion2 Sam palette spectrum.",
        "pp_style_apply rfd2_sam_palette, selection=polymer",
        "pp_style_revert",
        ("style", "palette", "color"),
    ),
    (
        "professional_secondary_structure",
        ("protein_colors",),
        "Color helices, sheets, and loops with a compact professional palette.",
        "pp_style_apply professional_secondary_structure, selection=polymer",
        "pp_style_revert",
        ("style", "secondary structure", "color"),
    ),
    (
        "ligand_contacts",
        (),
        "Fast local heavy-atom ligand contacts with one closest residue pair.",
        "pp_interact_run ligand_contacts, source=complex",
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction", "ligand"),
    ),
    (
        "ligand_quick",
        ("ligand",),
        "Focused local ligand-site hydrogen bonds.",
        "pp_interact_run ligand_quick, source=complex",
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction"),
    ),
    (
        "ligand_site",
        ("binding_site",),
        "Ligand site with hydrogen bonds, salt bridges, and property colors.",
        "pp_interact_run ligand_site, source=complex",
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction"),
    ),
    (
        "interchain_basic",
        ("interchain",),
        "Local chain-chain hydrogen bonds and salt bridges.",
        "pp_interact_run interchain_basic, source=complex",
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction", "chains"),
    ),
    (
        "polar_qc",
        (),
        "Classify good/weak polar pairs and like-polar clashes.",
        'pp_interact_run polar_qc, source=complex, partner1="chain A", partner2="chain B"',
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction", "clash"),
    ),
    (
        "plip_overview",
        (),
        "Complete PLIP interaction categories through the WSL worker.",
        "pp_interact_run plip_overview, source=complex",
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction", "wsl"),
    ),
    (
        "plip_publication",
        (),
        "Focused publication-oriented PLIP result.",
        "pp_interact_run plip_publication, source=complex",
        "pp_interact_clear GENERATED_GROUP",
        ("contacts", "interaction", "wsl"),
    ),
)


def default_tasks():
    """Return detached task-first help entries (always at most twelve)."""

    return [deepcopy(value) for value in _TASKS]


def help_topics():
    """Return deterministic exact topics suitable for PyMOL completion."""

    values = list(CANONICAL_COMMANDS)
    values.extend(AUTOLOADED_COMPAT_COMMANDS)
    values.extend(
        name for commands in CANONICAL_COMMANDS.values() for name in commands
    )
    values.extend(
        name
        for commands in AUTOLOADED_COMPAT_COMMANDS.values()
        for name in commands
    )
    values.extend(task["name"] for task in _TASKS)
    for preset in _preset_entries():
        values.append(preset["name"])
        values.extend(preset["aliases"])
    return tuple(dict.fromkeys(values))


def category_help(category):
    """Return a detached category description, or ``None``."""

    name = str(category).strip().lower()
    commands = CANONICAL_COMMANDS.get(name)
    if commands is None:
        commands = AUTOLOADED_COMPAT_COMMANDS.get(name)
    if commands is None:
        return None
    return {
        "kind": "category",
        "tier": "compat" if name.endswith("_compat") else "canonical",
        "name": name,
        "purpose": _CATEGORY_PURPOSE[name],
        "minimal_usage": "pp_help %s" % name,
        "example": "pp_help %s" % name,
        "mutates": False,
        "undo": None,
        "next": ["pp_help %s" % command for command in commands[:3]],
        "keywords": [],
        "category": name,
        "aliases": [],
        "commands": list(commands),
    }


def command_help(name):
    """Return help for one canonical command, including a category fallback."""

    command = str(name).strip()
    category = command_category(command)
    if not category:
        return None
    row = _COMMAND_ROWS.get(command)
    if row is None:
        purpose = _CATEGORY_PURPOSE[category]
        usage, example = _FALLBACK_USAGE_EXAMPLES.get(
            command, (command, command)
        )
        row = (
            purpose,
            usage,
            example,
            command not in _READ_ONLY_COMMANDS,
            None,
            ("pp_help %s" % category,),
            (),
        )
    purpose, usage, example, mutates, undo, next_commands, keywords = row
    return _help_entry(
        "command",
        command,
        purpose,
        usage,
        example,
        mutates,
        undo,
        next_commands,
        keywords,
        category,
    )


def _preset_entries():
    values = []
    for name, aliases, purpose, example, undo, keywords in _PRESET_ROWS:
        command = "pp_style_apply" if example.startswith("pp_style") else "pp_interact_run"
        values.append(
            _help_entry(
                "preset",
                name,
                purpose,
                example,
                example,
                True,
                undo,
                ("pp_help %s" % command,),
                keywords,
                "styles" if command == "pp_style_apply" else "interactions",
                aliases,
            )
        )
    return values


def exact_help(query):
    """Resolve an exact task, command, or preset/alias (not a category)."""

    requested = unicodedata.normalize("NFKC", str(query)).strip().casefold()
    command = command_help(requested)
    if command is not None:
        return command
    for preset in _preset_entries():
        preset_names = (preset["name"],) + tuple(preset["aliases"])
        if requested in {
            unicodedata.normalize("NFKC", str(value)).strip().casefold()
            for value in preset_names
        }:
            return preset
    for task in _TASKS:
        names = (task["name"],) + tuple(task["aliases"])
        if requested in {
            unicodedata.normalize("NFKC", str(value)).strip().casefold()
            for value in names
        }:
            return deepcopy(task)
    return None


def _normalized_values(entry):
    values = [entry["name"], entry["purpose"]]
    values.extend(entry.get("aliases", ()))
    values.extend(entry.get("keywords", ()))
    if entry.get("category"):
        values.append(entry["category"])
    normalized = []
    for value in values:
        text = _normalize_search_text(value)
        if text:
            normalized.append(text)
            if text.startswith("pp "):
                normalized.append(text[3:])
    return normalized


def _normalize_search_text(value):
    """Normalize task phrases without discarding non-ASCII languages."""

    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def _score(query, entry):
    best = 0.0
    for candidate in _normalized_values(entry):
        if query == candidate:
            value = 100.0
        elif candidate.startswith(query) or query.startswith(candidate):
            value = 84.0
        elif query in candidate:
            value = 72.0
        else:
            value = 60.0 * SequenceMatcher(None, query, candidate).ratio()
        best = max(best, value)
    primary = []
    for value in (entry["name"],) + tuple(entry.get("aliases", ())):
        normalized = _normalize_search_text(value)
        if normalized:
            primary.append(normalized)
            if normalized.startswith("pp "):
                primary.append(normalized[3:])
    if query in primary:
        # An exact public name or curated alias must beat incidental complete
        # token coverage in another entry's prose (for example ``jobs`` inside
        # the worker-metrics description).
        best = max(best, 112.0)
    query_tokens = set(query.split())
    candidate_tokens = {
        token
        for candidate in _normalized_values(entry)
        for token in candidate.split()
    }
    if query_tokens:
        coverage = len(query_tokens.intersection(candidate_tokens)) / len(query_tokens)
        if coverage == 1.0:
            # A task whose distributed keywords cover the whole phrase should
            # beat a different task which happens to resemble one fragment.
            best = max(best, 94.0)
        else:
            best += 8.0 * coverage
    if entry["kind"] == "task":
        best += 8.0
    elif entry["kind"] == "preset":
        best += 3.0
    return best


def _match_details(query, entry):
    candidates = _normalized_values(entry)
    query_terms = list(dict.fromkeys(query.split()))
    candidate_terms = {
        term for candidate in candidates for term in candidate.split()
    }
    matched = [term for term in query_terms if term in candidate_terms]
    unmatched = [term for term in query_terms if term not in candidate_terms]
    if query in candidates:
        matched_by = "exact_phrase"
    elif query_terms and not unmatched:
        matched_by = "token_complete"
    elif any(
        candidate.startswith(query) or query.startswith(candidate)
        for candidate in candidates
    ):
        matched_by = "phrase_prefix"
    elif any(query in candidate for candidate in candidates):
        matched_by = "phrase_contains"
    else:
        matched_by = "fuzzy"
    return matched_by, matched, unmatched


def search_help(query, limit=5):
    """Return deterministic fuzzy task/command/preset matches."""

    raw = str(query).strip()
    requested = _normalize_search_text(raw)
    if not requested:
        return default_tasks() if not raw else []
    entries = [deepcopy(value) for value in _TASKS]
    entries.extend(
        value
        for category in CANONICAL_COMMANDS.values()
        for value in (command_help(name) for name in category)
        if value is not None
    )
    entries.extend(
        value
        for category in AUTOLOADED_COMPAT_COMMANDS.values()
        for value in (command_help(name) for name in category)
        if value is not None
    )
    entries.extend(_preset_entries())
    ranked = []
    for entry in entries:
        score = _score(requested, entry)
        # Below this boundary short shared fragments routinely turn arbitrary
        # input (for example ``foobar``) into confident-looking, unrelated
        # advice.  Common task phrases and one-edit English/Chinese typos score
        # above the boundary through curated aliases and task weighting.
        if score >= _MIN_SEARCH_SCORE:
            value = deepcopy(entry)
            value["score"] = round(min(score, 100.0) / 100.0, 4)
            # Keep the uncapped value only while sorting.  Otherwise an exact
            # task alias (100 + task bonus) ties a merely token-complete task
            # (94 + task bonus) after both public scores are capped at 1.0.
            value["_rank_score"] = score
            matched_by, matched_terms, unmatched_terms = _match_details(
                requested, entry
            )
            value["matched_by"] = matched_by
            value["matched_terms"] = matched_terms
            value["unmatched_terms"] = unmatched_terms
            ranked.append(value)
    kind_order = {"task": 0, "preset": 1, "command": 2}
    ranked.sort(
        key=lambda value: (
            -value["_rank_score"],
            kind_order.get(value["kind"], 9),
            value["name"],
        )
    )
    selected = ranked[: max(1, int(limit))]
    for value in selected:
        value.pop("_rank_score", None)
    return selected
