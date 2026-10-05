"""Validate complete templates before dispatching any PyMOL command."""

from string import Formatter


STEP_FIELDS = {"object", "selection", "file", "index", "count", "entry_id", "row_number"}
BUCKET_FIELDS = STEP_FIELDS | {"label", "value"}

VIEW_PRESETS = {
    "cartoon": "Cartoon overview",
    "ppi": "PPI · protein interface",
    "pli": "PLI · ligand pocket",
    "oligomer": "Oligomer · subunits",
    "membrane": "Membrane · protein & lipids",
    "nucleic": "DNA / RNA complex",
    "surface": "Molecular surface",
}
LIPID_SELECTION = "resn POPC+POPE+POPG+POPS+DPPC+DPPE+DOPC+DOPE+DOPS+DLPC+DMPC+CHOL+CLR"


def viewing_preset(name, object_name, api):
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
    if name in {"ppi", "oligomer", "membrane"} and not count(protein):
        raise ValueError("This preset needs a protein in the current structure")
    if name in {"ppi", "oligomer"}:
        chains = api.get_chains(protein.replace("{object}", object_name))
        if len(chains) < 2:
            raise ValueError("This preset needs at least two protein chains; load the biological assembly")
    if name == "ppi":
        # Each chain is compared with the other chains, never with itself.
        interfaces = ["byres ((%s and chain \"%s\") within 5 of (%s and not chain \"%s\"))"
                      % (protein, chain, protein, chain) for chain in chains]
        interface = "(" + " or ".join("(" + value + ")" for value in interfaces) + ")"
        if not count(interface):
            raise ValueError("No inter-chain protein contacts within 5 Å")
        commands += ["show cartoon, " + protein, "util.cbc " + protein,
                     "show sticks, " + interface, "util.cnc " + interface,
                     "zoom " + interface + ", 6"]
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


def format_commands(template, context):
    """Return numbered commands; reject incomplete or unknown placeholders."""
    values = dict(context)
    if "object" in values:
        values["selection"] = values["object"]
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


def validate_commands(step, buckets):
    format_commands(step, {name: "sa_current_structure" for name in STEP_FIELDS})
    for label, template in buckets.items():
        try:
            format_commands(template, {name: "sa_current_structure" for name in BUCKET_FIELDS})
        except ValueError as exc:
            raise ValueError("After annotation %r: %s" % (label, exc)) from exc
