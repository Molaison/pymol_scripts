"""Validate complete templates before dispatching any PyMOL command."""

from string import Formatter


STEP_FIELDS = {"object", "selection", "candidate", "file", "index", "count", "entry_id", "row_number"}
BUCKET_FIELDS = STEP_FIELDS | {"label", "value"}

VIEW_PRESETS = {
    "overview": "Structure overview",
    "review_fixed_view": "Review · keep camera",
    "interface_detail": "Interface & pocket",
    "reference_overlay": "Reference · overlay, keep coordinates",
    "reference_align": "Reference · align candidate Cα",
    "reference_target_align": "Reference · align target, compare binder",
    "membrane_layer": "Membrane topology",
    "mass_residue_colors": "MASS paper · residue-number gradient",
}
CORE_VIEW_PRESETS = ("overview", "interface_detail", "membrane_layer", "mass_residue_colors")
LIPID_SELECTION = "resn POPC+POPE+POPG+POPS+DPPC+DPPE+DOPC+DOPE+DOPS+DLPC+DMPC+CHOL+CLR"


def viewing_preset(name, object_name, api, *, reference_name="", reference_object="",
                   target_chain=None, binder_chain=None, reference_chain=None,
                   interface_mode="auto", contact_radius="4", sidechain_scope="nearby",
                   surface="0", transparency="0.5", nucleic="1"):
    """Build an editable, candidate-scoped recipe from the loaded structure."""
    if name not in VIEW_PRESETS:
        raise ValueError("Choose a viewing preset")
    if not object_name or api.count_atoms(object_name) < 1:
        raise ValueError("Open a candidate structure first")
    if name in CORE_VIEW_PRESETS:
        return _review_viewing_preset(name, target_chain, binder_chain, interface_mode,
                                      contact_radius, sidechain_scope, surface, transparency, nucleic)
    if name.startswith("reference_"):
        return _reference_preset(name, object_name, api, reference_name, reference_object,
                                 target_chain, binder_chain, reference_chain)
    target = "({object})"
    protein = target + " and polymer.protein"
    if not api.count_atoms(protein.replace("{object}", object_name)):
        raise ValueError("This preset needs a protein in the current structure")
    return [
        "hide everything, " + target, "show cartoon, " + protein, "util.cbc " + protein,
        "set cartoon_transparency, 0, " + protein,
    ], "Camera and coordinates unchanged. Use candidates in a common coordinate frame."


def _review_viewing_preset(name, target_chain, binder_chain, mode, radius, scope, surface, transparency, nucleic):
    from .review_view_presets import show_preset_command

    if name == "overview":
        command = show_preset_command("overview", "{object}", surface=surface,
                                      transparency=transparency, nucleic=nucleic)
        note = "Chain-colored cartoon with ligand sticks and optional surface or nucleic-acid sticks."
    elif name == "interface_detail":
        command = show_preset_command("interface", "{object}", mode, radius, scope, target_chain, binder_chain,
                                      surface=surface, transparency=transparency)
        note = ("Teal dashes: polar contacts. Thick pink dashes: salt bridges. "
                "Blue beads: pi stacking. Cyan dots: accessible recesses. Violet dots: enclosed voids.")
    elif name == "membrane_layer":
        command = show_preset_command("membrane", "{object}")
        note = "Shows membrane source, thickness, TM regions and key sidechains from modeled lipids or local TmDet."
    else:
        command = show_preset_command("mass", "{object}", target_chain=target_chain, binder_chain=binder_chain)
        note = "MASS paper palette by residue number. Select a target to color both chains, each over its own range."
    return [command], note


def _chain_selection(placeholder, object_name, chain, api):
    if chain is None or chain not in api.get_chains("(%s) and polymer.protein" % object_name):
        raise ValueError("Choose an existing protein chain for " + placeholder)
    if any(char in chain for char in '"\\\r\n;{}'):
        raise ValueError("This chain identifier cannot be used in a command template")
    return '({' + placeholder + '}) and polymer.protein and chain "' + chain + '"'


def _candidate_chains(object_name, api, target_chain, binder_chain):
    target = _chain_selection("object", object_name, target_chain, api)
    binder = _chain_selection("object", object_name, binder_chain, api)
    if target_chain == binder_chain:
        raise ValueError("Target and binder must be different chains")
    return target, binder


def _reference_preset(name, object_name, api, reference_name, reference_object,
                      target_chain=None, binder_chain=None, reference_chain=None):
    if not reference_name or not reference_object or reference_object == object_name:
        raise ValueError("Choose a loaded fixed object from Review steps as the reference")
    candidate = "({object}) and polymer.protein"
    reference = "({" + reference_name + "}) and polymer.protein"
    binder = None
    if name == "reference_target_align":
        candidate, binder = _candidate_chains(object_name, api, target_chain, binder_chain)
        reference = _chain_selection(reference_name, reference_object, reference_chain, api)
    for selection in (candidate.replace("{object}", object_name),
                      reference.replace("{" + reference_name + "}", reference_object)):
        if api.count_atoms(selection + " and name CA") < 3:
            raise ValueError("Candidate and reference each need at least three protein Cα atoms")
    commands = [
        "hide everything, ({object})", "show cartoon, " + candidate,
        "color cyan, " + candidate, "set cartoon_transparency, 0, " + candidate,
        "show cartoon, " + reference, "color gray70, " + reference,
        "set cartoon_transparency, 0.55, " + reference,
    ]
    if binder is not None:
        commands += ["show cartoon, " + binder, "color orange, " + binder,
                     "set cartoon_transparency, 0, " + binder]
    if name in {"reference_align", "reference_target_align"}:
        commands.insert(0, "align " + candidate + " and name CA, " + reference + " and name CA")
        commands.append("zoom ({object}) or ({" + reference_name + "}), 4")
        note = (("Fits target-chain Cα atoms and moves the whole candidate; binder pose is preserved. "
                 if binder is not None else
                 "Moves the candidate using all protein Cα atoms. Check chain correspondence. ")
                + "PyMOL align creates/updates "
                "_align1 and _align2 selections.")
    else:
        note = "Camera and coordinates unchanged. Requires an existing common coordinate frame."
    return commands, note


def format_commands(template, context):
    """Return numbered commands; reject incomplete or unknown placeholders."""
    values = dict(context)
    if "object" in values:
        values["selection"] = values["object"]
        values["candidate"] = values["object"]
    lines = []
    for number, raw in enumerate(str(template or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            for _, name, spec, conversion in Formatter().parse(line):
                if name is not None:
                    if name not in values:
                        raise ValueError("unknown placeholder {%s}; use {object} for the current structure" % name)
                    if spec or conversion:
                        raise ValueError("placeholders do not accept format specifications")
                    if name in {"object", "selection"} and not values[name]:
                        raise ValueError("no current structure for {%s}" % name)
            rendered = line.format(**values)
            if any(char in rendered for char in "\x00\r\n"):
                raise ValueError("a command must remain on one line")
        except ValueError as exc:
            raise ValueError("Command line %d: %s\n%s" % (number, exc, line)) from exc
        lines.append((number, rendered))
    return lines


def validate_commands(step, buckets, object_names=()):
    format_commands(step, {name: "sa_current_structure" for name in STEP_FIELDS | set(object_names)})
    for label, template in buckets.items():
        try:
            format_commands(template, {name: "sa_current_structure" for name in BUCKET_FIELDS | set(object_names)})
        except ValueError as exc:
            raise ValueError("After annotation %r: %s" % (label, exc)) from exc
