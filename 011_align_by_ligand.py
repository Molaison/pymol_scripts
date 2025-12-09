try:
    from pymol import cmd, CmdException  # PyMOL raises CmdException for selection errors
except ImportError:  # pragma: no cover - fallback for unusual setups
    from pymol import cmd  # type: ignore

    class CmdException(Exception):
        """Fallback CmdException for environments lacking the symbol."""

import traceback

_METHOD_CHOICES = {"alignto", "super"}


def _normalize_method(method):
    """Return the canonical alignment method name or raise ValueError."""
    normalized = (method or "alignto").lower()
    if normalized not in _METHOD_CHOICES:
        raise ValueError("method must be 'alignto' or 'super'")
    return normalized


def _collect_objects(selection, reference_object=None):
    """Return ordered object list with the reference, if any, placed first."""
    objects = cmd.get_object_list(f"({selection})") or []
    ordered = []
    for obj in objects:
        if obj not in ordered:
            ordered.append(obj)
    if reference_object:
        ref_objects = cmd.get_object_list(f"({reference_object})")
        if not ref_objects:
            raise ValueError(f"Reference selection '{reference_object}' has no objects")
        reference_name = ref_objects[0]
        if reference_name in ordered:
            ordered.remove(reference_name)
        ordered.insert(0, reference_name)
    if not ordered:
        raise ValueError(f"No objects found for selection '{selection}'")
    return ordered


def _sanitize_selection_fragment(fragment):
    """Strip wrapping quotes so PyMOL gets a raw selection expression."""
    text = fragment.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _prepare_ligand_object_list(ligand_objects):
    """Derive an ordered ligand-object list from a selection or comma string."""
    if not ligand_objects:
        return None
    expr = _sanitize_selection_fragment(ligand_objects)
    hits = []
    try:
        hits = cmd.get_object_list(f"({expr})")
    except CmdException:
        hits = []
    if hits:
        return hits
    names = [name.strip() for name in expr.split(",") if name.strip()]
    if names:
        return names
    raise ValueError(f"Ligand object specification '{ligand_objects}' produced no objects")


def _resolve_ligand_owner(
    object_name,
    object_index,
    ligand_object_pattern,
    ligand_object_list,
):
    """Resolve which object actually holds the ligand used for alignment."""
    if ligand_object_list:
        if object_index >= len(ligand_object_list):
            raise ValueError(
                "Ligand object list has fewer entries "
                f"({len(ligand_object_list)}) than required ({object_index + 1})"
            )
        return ligand_object_list[object_index]
    if not ligand_object_pattern:
        return object_name
    try:
        return ligand_object_pattern.format(
            object=object_name,
            obj=object_name,
            index=object_index,
        )
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(
            f"Failed to format ligand_object_pattern '{ligand_object_pattern}': {exc}"
        ) from exc


def _build_ligand_selection(
    object_name,
    object_index,
    ligand_fragment,
    ligand_object_pattern=None,
    ligand_object_list=None,
):
    """Build a ligand-only selection for a single object."""
    ligand_owner = _resolve_ligand_owner(
        object_name,
        object_index,
        ligand_object_pattern,
        ligand_object_list,
    )
    if ligand_owner is None:
        return f"({ligand_fragment})"
    owner_lower = ligand_owner.lower()
    if owner_lower in {"global", "all"}:
        return f"({ligand_fragment})"
    return f"({ligand_owner} and ({ligand_fragment}))"


def _count_atoms(selection):
    """Return atom count, guarding against CmdException."""
    try:
        return cmd.count_atoms(selection)
    except CmdException:
        return 0


def _align_single_object(
    obj,
    obj_index,
    reference_name,
    ligand_fragment,
    ref_ligand_sel,
    ligand_object_pattern,
    ligand_object_list,
    cutoff,
    cycles,
    matchmaker,
    method,
    quiet,
):
    """Run the requested alignment method for a single object and return a summary dict."""
    ligand_sel = _build_ligand_selection(
        obj,
        obj_index,
        ligand_fragment,
        ligand_object_pattern=ligand_object_pattern,
        ligand_object_list=ligand_object_list,
    )
    ligand_atoms = _count_atoms(ligand_sel)
    if ligand_atoms == 0:
        if not quiet:
            print(f"[{method}] Skipped {obj}: no atoms for '{ligand_fragment}'.")
        return {"status": "skipped", "reason": "no ligand atoms"}

    if method == "alignto":
        rms = cmd.alignto(
            ligand_sel,
            ref_ligand_sel,
            cutoff=cutoff,
            cycles=cycles,
            matchmaker=matchmaker,
            quiet=1,
        )
    else:
        rms = cmd.super(
            ligand_sel,
            ref_ligand_sel,
            cutoff=cutoff,
            cycles=cycles,
            quiet=1,
        )
    rms_value = rms[0] if isinstance(rms, (list, tuple)) else rms
    if not quiet:
        print(f"[{method}] {obj} -> {reference_name} | atoms={ligand_atoms} | RMS={rms_value:.3f}")
    return {"status": "aligned", "rms": rms_value, "atoms": ligand_atoms}


def _print_summary(reference_name, ref_atoms, summary, method):
    """Print alignment summary."""
    aligned = [k for k, v in summary.items() if v["status"] == "aligned"]
    skipped = [k for k, v in summary.items() if v["status"] == "skipped"]
    print(f"[{method}] Reference object: {reference_name} ({ref_atoms} ligand atoms)")
    if aligned:
        print(f"[{method}] Aligned objects: {', '.join(aligned)}")
    if skipped:
        print(f"[{method}] Skipped objects: {', '.join(skipped)}")


def _run_alignment(
    selection,
    ligand_selection,
    reference_object,
    ligand_object_pattern,
    ligand_objects,
    cutoff,
    cycles,
    matchmaker,
    method,
    quiet,
):
    """Return (summary, reference_name, ref_atom_count)."""
    objects = _collect_objects(selection, reference_object)
    reference_name = objects[0]
    ligand_fragment = _sanitize_selection_fragment(ligand_selection)
    ligand_object_list = _prepare_ligand_object_list(ligand_objects)
    ref_ligand_sel = _build_ligand_selection(
        reference_name,
        0,
        ligand_fragment,
        ligand_object_pattern=ligand_object_pattern,
        ligand_object_list=ligand_object_list,
    )
    ref_atoms = _count_atoms(ref_ligand_sel)
    if ref_atoms == 0:
        raise ValueError(
            f"Reference object '{reference_name}' contains no atoms for '{ligand_fragment}'"
        )

    summary = {}
    for idx, obj in enumerate(objects[1:], start=1):
        summary[obj] = _align_single_object(
            obj,
            idx,
            reference_name,
            ligand_fragment,
            ref_ligand_sel,
            ligand_object_pattern,
            ligand_object_list,
            cutoff,
            cycles,
            matchmaker,
            method,
            quiet,
        )
    return summary, reference_name, ref_atoms


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
    """
    Align complexes via ligand coordinates and expose the helper as a PyMOL command.

    Args:
        selection (str): Objects to include in the alignment (default: "all").
        ligand_selection (str): Selection snippet that describes each ligand.
        reference_object (str): Reference complex. Defaults to the first object from `selection`.
        ligand_object_pattern (str): Optional format string whose output names the ligand object
            for each complex (e.g. "lig_{object}" maps "complex1" -> "lig_complex1").
        ligand_objects (str): Optional ordered list/selection of ligand objects ("lig1,lig2" or
            "lig1 or lig2"); matched in the same order as the aligned complexes.
        cutoff (float): Distance cutoff for refinement.
        cycles (int): Refinement cycles.
        matchmaker (int): Matchmaker mode for `cmd.alignto` (-1 uses PyMOL default).
        method (str): Either "alignto" (default) or "super" for PyMOL's superposition routine.
        quiet (int): Set to 1 to suppress progress details.

    Returns:
        dict: Alignment summary keyed by object name.
    """
    method = _normalize_method(method)
    try:
        summary, reference_name, ref_atoms = _run_alignment(
            selection,
            ligand_selection,
            reference_object,
            ligand_object_pattern,
            ligand_objects,
            cutoff,
            cycles,
            matchmaker,
            method,
            quiet,
        )
        if not quiet:
            _print_summary(reference_name, ref_atoms, summary, method)
        return summary
    except Exception as exc:
        print(f"Error aligning complexes by ligand: {exc}")
        traceback.print_exc()
        return {}


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
    """Convenience wrapper that runs the workflow through `cmd.super`."""

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

print(
    "Registered commands: alignto_ligand(selection, ligand_selection, reference_object, "
    "ligand_object_pattern, ligand_objects, cutoff, cycles, matchmaker, method, quiet) and "
    "super_ligand(selection, ligand_selection, reference_object, ligand_object_pattern, "
    "ligand_objects, cutoff, cycles, quiet)"
)
