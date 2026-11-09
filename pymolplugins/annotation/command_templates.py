"""Validate complete templates before dispatching any PyMOL command."""

from string import Formatter


STEP_FIELDS = {"object", "selection", "candidate", "file", "index", "count", "entry_id", "row_number"}
BUCKET_FIELDS = STEP_FIELDS | {"label", "value"}

VIEW_PRESETS = {
    "cartoon": "Cartoon overview",
    "review_fixed_view": "Review · keep camera",
    "ppi": "PPI · protein interface",
    "ppi_surface": "PPI · interface surface",
    "reference_overlay": "Reference · overlay, keep coordinates",
    "reference_align": "Reference · align candidate Cα",
    "pli": "PLI · ligand pocket",
    "oligomer": "Oligomer · subunits",
    "membrane": "Membrane · protein & lipids",
    "nucleic": "DNA / RNA complex",
    "surface": "Molecular surface",
}
LIPID_SELECTION = "resn POPC+POPE+POPG+POPS+DPPC+DPPE+DOPC+DOPE+DOPS+DLPC+DMPC+CHOL+CLR"


def viewing_preset(name, object_name, api, *, reference_name="", reference_object=""):
    """Build an editable, candidate-scoped recipe from the loaded structure."""
    if name not in VIEW_PRESETS:
        raise ValueError("Choose a viewing preset")
    if not object_name or api.count_atoms(object_name) < 1:
        raise ValueError("Open a candidate structure first")
    target = "({object})"
    protein = target + " and polymer.protein"
    def count(selection):
        return api.count_atoms(selection.replace("{object}", object_name))
    commands = ["hide everything, " + target]
    note = ""
    if name.startswith("reference_"):
        return _reference_preset(name, object_name, api, reference_name, reference_object)
    if name in {"ppi", "ppi_surface", "oligomer", "membrane", "review_fixed_view"} and not count(protein):
        raise ValueError("This preset needs a protein in the current structure")
    if name in {"ppi", "ppi_surface", "oligomer"}:
        chains = api.get_chains(protein.replace("{object}", object_name))
        if len(chains) < 2:
            raise ValueError("This preset needs at least two protein chains; load the biological assembly")
    if name == "review_fixed_view":
        commands += ["show cartoon, " + protein, "util.cbc " + protein,
                     "set cartoon_transparency, 0, " + protein]
        note = "Camera and coordinates unchanged. Use candidates in a common coordinate frame."
    elif name in {"ppi", "ppi_surface"}:
        # Each chain is compared with the other chains, never with itself.
        interfaces = ["byres ((%s and chain \"%s\") within 5 of (%s and not chain \"%s\"))"
                      % (protein, chain, protein, chain) for chain in chains]
        interface = "(" + " or ".join("(" + value + ")" for value in interfaces) + ")"
        if not count(interface):
            raise ValueError("No inter-chain protein contacts within 5 Å")
        commands += ["show cartoon, " + protein, "util.cbc " + protein,
                     "show sticks, " + interface, "util.cnc " + interface,
                     "zoom " + interface + ", 6"]
        if name == "ppi_surface":
            commands[-1:-1] = ["show surface, " + interface,
                               "set transparency, 0.35, " + target,
                               "set cartoon_transparency, 0, " + protein]
            note = "Inter-chain residues within 5 Å; a visual neighborhood, not an interaction score."
    elif name == "pli":
        ligand = target + " and organic and not (" + LIPID_SELECTION + ")"
        if not count(ligand):
            raise ValueError("No organic ligand found in this structure")
        pocket = "byres ((%s) within 5 of (%s))" % (protein, ligand)
        focus = "((%s) or (%s))" % (ligand, pocket)
        commands += ["show cartoon, " + protein, "color gray70, " + protein,
                     "set cartoon_transparency, 0.65, " + protein,
                     "show sticks, " + focus, "color yellow, (" + ligand + ") and elem C",
                     "set stick_radius, 0.16, " + pocket, "set stick_radius, 0.25, " + ligand,
                     "util.cnc " + focus, "orient " + focus, "zoom " + focus + ", 5"]
    elif name == "membrane":
        commands += ["show cartoon, " + protein, "color gray70, " + protein,
                     "color orange, (" + protein + ") and resn ALA+VAL+ILE+LEU+MET+PHE+TRP+TYR"]
        lipids = target + " and (" + LIPID_SELECTION + ")"
        if count(lipids):
            commands += ["show lines, " + lipids, "color cyan, (" + lipids + ") and elem C",
                         "util.cnc " + lipids]
        else:
            note = "No lipids found; hydrophobic residues highlighted. Orientation unchanged."
        commands += ["zoom " + target + ", 3"]
    elif name == "nucleic":
        nucleic = target + " and polymer.nucleic"
        if not count(nucleic):
            raise ValueError("No DNA or RNA found in this structure")
        commands += ["show cartoon, " + target + " and polymer", "util.cbc " + target,
                     "show sticks, " + nucleic, "util.cnc " + nucleic, "zoom " + target + ", 3"]
    else:
        commands += ["show cartoon, " + target + " and polymer", "util.cbc " + target]
        if name in {"surface", "oligomer"}:
            commands += ["show surface, " + target,
                         "set transparency, %s, %s" % ("0.65" if name == "oligomer" else "0.25", target)]
        commands += ["orient " + target, "zoom " + target + ", 3"]
    return commands, note


def _reference_preset(name, object_name, api, reference_name, reference_object):
    if not reference_name or not reference_object or reference_object == object_name:
        raise ValueError("Choose a loaded fixed object from Review steps as the reference")
    candidate = "({object}) and polymer.protein"
    reference = "({" + reference_name + "}) and polymer.protein"
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
    if name == "reference_align":
        commands.insert(0, "align " + candidate + " and name CA, " + reference + " and name CA")
        commands.append("zoom ({object}) or ({" + reference_name + "}), 4")
        note = ("Moves the candidate onto the fixed reference using all protein Cα atoms. "
                "Check chain correspondence for complexes. PyMOL align creates/updates "
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
