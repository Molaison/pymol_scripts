"""Draw an aligned candidate and its contact changes in the existing pocket scene."""
from .pocket_analysis import CONTACT_COLORS


def _load_fragment(api, name, model, color, radius, transparency):
    api.load_model(model, name, quiet=1, zoom=0)
    api.hide("everything", name)
    api.color(color, name)
    if model.bond:
        api.show("sticks", name)
        api.set("stick_radius", radius, name)
        api.set("stick_transparency", transparency, name)
    else:
        api.show("spheres", name)
        api.set("sphere_scale", radius, name)
        api.set("sphere_transparency", transparency, name)


def draw_candidate(api, reference, group, preview, reference_backbone):
    from .review_view_presets import _cylinders

    objects = {}
    try:
        for kind, model, color, radius, transparency in (
            ("reference_backbone", reference_backbone, "gray55", .10, .32),
            ("candidate_backbone", preview.backbone, "violet", .13, .12),
            ("candidate_ligand", preview.ligand, "magenta", .24, .03),
        ):
            if not model.atom:
                continue
            name = objects[kind] = api.get_unused_name(f"{reference}_{kind}")
            _load_fragment(api, name, model, color, radius, transparency)

        for change in ("Gained", "Lost"):
            pairs = dict.fromkeys(item.points for item in preview.changes if item.change == change)
            name = _cylinders(api, reference, change.lower(), pairs, CONTACT_COLORS[change], .11, .62, zoom=0)
            if name:
                objects[change.lower()] = name
        if objects:
            api.group(group, " ".join(objects.values()))
    except Exception:
        for name in objects.values():
            api.delete(name)
        raise
    return objects


def draw_contact_focus(api, reference, group, contact):
    from .review_view_presets import _cylinders, _dots

    color = CONTACT_COLORS[contact.change]
    objects = {}
    try:
        name = objects["residue"] = api.get_unused_name(f"{reference}_contact_residue")
        _load_fragment(api, name, contact.residue,
                       "marine" if contact.change == "Gained" else "orange", .19, .04)
        objects["line"] = _cylinders(api, reference, "selected_contact", (contact.points,),
                                     color, .15, .62, zoom=0)
        objects["protein_atoms"] = _dots(api, reference, "contact_protein_atoms",
                                          (atom.point for atom in contact.protein_atoms), color, .31, opacity=1)
        objects["ligand_atoms"] = _dots(api, reference, "contact_ligand_atoms",
                                         (atom.point for atom in contact.ligand_atoms), color, .45, opacity=1)
        api.group(group, " ".join(objects.values()))
    except Exception:
        for name in objects.values():
            if name:
                api.delete(name)
        raise
    return objects

