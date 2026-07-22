"""Compatibility commands for ligand-driven whole-complex alignment.

The previous implementation called ``cmd.alignto`` with an invalid signature
and moved a detached ligand instead of its complex.  These wrappers now use
the canonical anchor/move-separated rigid alignment engine.
"""

import os
import sys
import traceback

from pymol import cmd

try:
    from pymolplugins.core.parameters import as_bool, as_float, as_int, split_names
    from pymolplugins.alignment.rigid import pp_align_ligand
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.parameters import as_bool, as_float, as_int, split_names  # type: ignore
    from pymolplugins.alignment.rigid import pp_align_ligand  # type: ignore


def _objects(selection, reference_object=None):
    objects = list(dict.fromkeys(cmd.get_object_list(f"({selection})") or []))
    if reference_object:
        references = list(cmd.get_object_list(f"({reference_object})") or [])
        if not references:
            raise ValueError(f"reference_object matched no molecule: {reference_object}")
        reference = references[0]
        objects = [reference] + [name for name in objects if name != reference]
    if not objects:
        raise ValueError(f"selection matched no molecule objects: {selection}")
    return objects


def _ligand_owners(objects, ligand_object_pattern, ligand_objects):
    explicit = split_names(ligand_objects)
    if explicit:
        if len(explicit) != len(objects):
            raise ValueError(
                f"ligand_objects must contain one owner per complex ({len(explicit)} != {len(objects)})"
            )
        return explicit
    if ligand_object_pattern:
        pattern = str(ligand_object_pattern)
        return [
            pattern.format(object=name, obj=name, index=index)
            for index, name in enumerate(objects)
        ]
    return list(objects)


def align_complexes_by_ligand(
    selection="all",
    ligand_selection="organic",
    reference_object=None,
    ligand_object_pattern=None,
    ligand_objects=None,
    cutoff=2.0,
    cycles=5,
    matchmaker=-1,
    method="alignto",
    quiet=0,
):
    """Align every mobile complex to a reference using ligand atom names.

    ``cutoff``, ``cycles``, ``matchmaker`` and ``method`` are accepted for old
    command lines but no longer control PyMOL protein alignment.  The new
    implementation performs one deterministic proper rigid fit and applies it
    to each complete mobile complex.
    """
    try:
        # Strictly validate legacy console strings instead of relying on truthiness.
        as_float(cutoff, minimum=0.0, name="cutoff")
        as_int(cycles, minimum=0, name="cycles")
        as_int(matchmaker, name="matchmaker")
        quiet_value = as_bool(quiet)
        method_name = str(method).strip().lower()
        if method_name not in {"alignto", "super"}:
            raise ValueError("method must be alignto or super")
        objects = _objects(selection, reference_object)
        if len(objects) < 2:
            raise ValueError("at least two complex objects are required")
        owners = _ligand_owners(objects, ligand_object_pattern, ligand_objects)
        reference_complex = objects[0]
        reference_ligand = owners[0]
        summary = {}
        for mobile_complex, mobile_ligand in zip(objects[1:], owners[1:]):
            result = pp_align_ligand(
                mobile_obj=mobile_ligand,
                target_obj=reference_ligand,
                mobile_ligand=ligand_selection,
                target_ligand=ligand_selection,
                match="name",
                move=mobile_complex,
                preview=0,
                commit=1,
            )
            summary[mobile_complex] = result
            if result.get("status") == "error":
                print(f"Ligand alignment skipped {mobile_complex}: {result['error']}")
            elif not quiet_value:
                print(
                    f"Ligand aligned {mobile_complex} -> {reference_complex}: "
                    f"RMSD={result['rmsd_after']:.4f}, points={result['point_count']}"
                )
        return summary
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def super_ligand(
    selection="all",
    ligand_selection="organic",
    reference_object=None,
    ligand_object_pattern=None,
    ligand_objects=None,
    cutoff=2.0,
    cycles=5,
    quiet=0,
):
    """Legacy alias; now uses the same ligand atom-name rigid fit."""
    return align_complexes_by_ligand(
        selection=selection,
        ligand_selection=ligand_selection,
        reference_object=reference_object,
        ligand_object_pattern=ligand_object_pattern,
        ligand_objects=ligand_objects,
        cutoff=cutoff,
        cycles=cycles,
        matchmaker=-1,
        method="super",
        quiet=quiet,
    )


cmd.extend("alignto_ligand", align_complexes_by_ligand)
cmd.extend("super_ligand", super_ligand)
